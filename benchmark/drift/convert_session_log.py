# -*- coding: utf-8 -*-
"""Convert a human-editable live session Markdown log into T4 drift JSONL.

This adapter is deliberately small and stdlib-only. It does not run a model,
touch the network, read secrets, or write benchmark results by default.

Exit codes:
  0 = converted / checked successfully
  2 = malformed input or CLI usage error
"""
import argparse
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TEMPLATE = os.path.join(HERE, "templates", "live_session_template.md")

TURN_RE = re.compile(r"^##\s+Turn\s+(\d+)\s*$", re.I)
SECTION_RE = re.compile(r"^###\s+(.+?)\s*$")
FIELD_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.*?)\s*$")
FENCE_RE = re.compile(r"^(?P<fence>`{3,})[A-Za-z0-9_-]*\s*$")

TURN_FIELDS = {"kind", "phase_context", "tokens_in", "tokens_out", "cost_usd"}
INT_FIELDS = {"phase_context", "tokens_in", "tokens_out"}
FLOAT_FIELDS = {"cost_usd"}
MESSAGE_SECTIONS = {"user", "assistant"}
EVENT_SECTIONS = {"events"}
EVENT_TYPES = {"read_file", "write_file"}
TRACKED_WRITE_SNAPSHOTS = {"study_plan.md", "study_progress.md", "study_state.json"}   # A4: T4 优先读 state 快照


class SessionLogError(Exception):
    """Malformed session log; surfaced as exit code 2."""


def read_utf8(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return f.read()
    except UnicodeDecodeError as e:
        raise SessionLogError("cannot read %s as UTF-8: %s" % (path, e))
    except OSError as e:
        raise SessionLogError("cannot read %s: %s" % (path, e))


def write_utf8(path, text):
    try:
        parent = os.path.dirname(os.path.abspath(path))
        if parent and not os.path.isdir(parent):
            raise SessionLogError("output directory does not exist: %s" % parent)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    except OSError as e:
        raise SessionLogError("cannot write %s: %s" % (path, e))


def write_stdout_utf8(text):
    """Print template text without losing emoji on legacy Windows consoles."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode("utf-8"))


def clean_block(lines):
    """Trim only surrounding blank lines; preserve internal text and UTF-8 content."""
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return "\n".join(lines[start:end])


def adapter_section(line):
    """Return the reserved adapter section name, or None for ordinary Markdown headings."""
    m = SECTION_RE.match(line)
    if not m:
        return None
    heading = m.group(1).strip()
    lower = heading.lower()
    if lower in MESSAGE_SECTIONS or lower in EVENT_SECTIONS:
        return lower
    if re.match(r"files\s+after\s*:\s*.+$", heading, re.I):
        return "files_after"
    return None


def parse_scalar(key, value, turn):
    if key in INT_FIELDS:
        if not re.fullmatch(r"\d+", value.strip()):
            raise SessionLogError("turn %d field %s must be an integer" % (turn, key))
        return int(value.strip())
    if key in FLOAT_FIELDS:
        try:
            parsed = float(value.strip())
        except ValueError:
            raise SessionLogError("turn %d field %s must be numeric" % (turn, key))
        if not math.isfinite(parsed):
            raise SessionLogError("turn %d field %s must be finite" % (turn, key))
        if parsed < 0:
            raise SessionLogError("turn %d field %s cannot be negative" % (turn, key))
        return parsed
    if key == "kind":
        value = value.strip()
        if not value:
            raise SessionLogError("turn %d field kind cannot be empty" % turn)
        return value
    raise SessionLogError("turn %d has unknown field %r" % (turn, key))


def parse_events(lines, turn):
    events = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^[-*]\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+?)\s*$", line)
        if not m:
            raise SessionLogError("turn %d event must look like '- read_file: path'" % turn)
        event_type = m.group(1)
        if event_type not in EVENT_TYPES:
            raise SessionLogError(
                "turn %d event type %r is not supported; use one of %s"
                % (turn, event_type, ", ".join(sorted(EVENT_TYPES)))
            )
        path = m.group(2).strip()
        if not path:
            raise SessionLogError("turn %d event path cannot be empty" % turn)
        events.append({"type": event_type, "path": path})
    return events


def parse_files_after(lines, start, turn, path):
    i = start
    while i < len(lines) and not lines[i].strip():
        i += 1
    m = FENCE_RE.match(lines[i].strip()) if i < len(lines) else None
    if not m:
        raise SessionLogError("turn %d Files After %s must use a fenced code block" % (turn, path))
    fence_len = len(m.group("fence"))
    close_re = re.compile(r"^`{%d,}\s*$" % fence_len)
    i += 1
    block_start = i
    while i < len(lines) and not close_re.match(lines[i].strip()):
        i += 1
    if i >= len(lines):
        raise SessionLogError("turn %d Files After %s fence is not closed" % (turn, path))
    content = clean_block(lines[block_start:i])
    return content, i + 1


def tracked_snapshot_name(path):
    norm = str(path or "").replace("\\", "/").lstrip("./")
    base = norm.rsplit("/", 1)[-1]
    return base if base in TRACKED_WRITE_SNAPSHOTS else None


def validate_tracked_writes(turn, events, files_after):
    for ev in events or []:
        if ev.get("type") != "write_file":
            continue
        required = tracked_snapshot_name(ev.get("path", ""))
        if required and required not in files_after:
            raise SessionLogError(
                "turn %d write_file for %s requires matching ### Files After: %s"
                % (turn, required, required)
            )


def parse_turn_body(turn, body):
    row = {"turn": turn}
    files_after = {}
    events = None
    seen_fields = set()
    seen = set()
    i = 0

    while i < len(body):
        line = body[i]
        if not line.strip():
            i += 1
            continue
        if adapter_section(line):
            break
        m = FIELD_RE.match(line)
        if not m:
            raise SessionLogError("turn %d has text before first section: %r" % (turn, line.strip()))
        key, value = m.group(1), m.group(2)
        if key not in TURN_FIELDS:
            raise SessionLogError("turn %d has unknown field %r" % (turn, key))
        if key in seen_fields:
            raise SessionLogError("turn %d repeats field %s" % (turn, key))
        seen_fields.add(key)
        row[key] = parse_scalar(key, value, turn)
        i += 1

    while i < len(body):
        line = body[i]
        if not line.strip():
            i += 1
            continue
        section = adapter_section(line)
        if not section:
            raise SessionLogError("turn %d has content outside a section: %r" % (turn, line.strip()))
        m = SECTION_RE.match(line)
        heading = m.group(1).strip()
        lower = heading.lower()
        i += 1

        if lower in MESSAGE_SECTIONS or lower in EVENT_SECTIONS:
            if lower in seen:
                raise SessionLogError("turn %d repeats section %s" % (turn, heading))
            seen.add(lower)
            start = i
            if lower in MESSAGE_SECTIONS:
                i = next_adapter_section(body, i, turn, heading)
            else:
                while i < len(body) and not adapter_section(body[i]):
                    i += 1
            block = body[start:i]
            if lower in EVENT_SECTIONS:
                events = parse_events(block, turn)
            else:
                value = clean_block(block)
                if not value:
                    raise SessionLogError("turn %d section %s cannot be empty" % (turn, heading))
                row[lower] = value
            continue

        fm = re.match(r"files\s+after\s*:\s*(.+)$", heading, re.I)
        if fm:
            path = fm.group(1).strip()
            if not path:
                raise SessionLogError("turn %d Files After path cannot be empty" % turn)
            snapshot_key = tracked_snapshot_name(path) or path
            if snapshot_key in files_after:
                raise SessionLogError("turn %d repeats Files After for %s" % (turn, path))
            content, i = parse_files_after(body, i, turn, path)
            files_after[snapshot_key] = content
            continue

        raise SessionLogError("turn %d has unknown section %r" % (turn, heading))

    for required in ("user", "assistant"):
        if required not in row:
            raise SessionLogError("turn %d missing ### %s section" % (turn, required.title()))
    if events is not None:
        row["events"] = events
    if files_after:
        row["files_after"] = files_after
    validate_tracked_writes(turn, events, files_after)
    return row


def parse_session_log(text):
    lines = text.splitlines()
    starts = []
    fence_len = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if fence_len is not None:
            if re.match(r"^`{%d,}\s*$" % fence_len, stripped):
                fence_len = None
            continue
        fm = FENCE_RE.match(stripped)
        if fm:
            fence_len = len(fm.group("fence"))
            continue
        m = TURN_RE.match(line)
        if m and is_turn_start(lines, i):
            starts.append((i, int(m.group(1))))
    if not starts:
        raise SessionLogError("session log has no '## Turn N' sections")

    rows = []
    seen_turns = set()
    for idx, (start, turn) in enumerate(starts):
        if turn in seen_turns:
            raise SessionLogError("duplicate turn number: %d" % turn)
        seen_turns.add(turn)
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        rows.append(parse_turn_body(turn, lines[start + 1:end]))
    return rows


def is_turn_start(lines, index):
    """A turn header is top-level only; ignore same-looking text captured inside snapshots/messages."""
    i = index + 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return False
    section = adapter_section(lines[i])
    if section in MESSAGE_SECTIONS or section in EVENT_SECTIONS:
        return True
    return bool(FIELD_RE.match(lines[i]))


def next_adapter_section(lines, start, turn, section):
    fence_len = None
    i = start
    while i < len(lines):
        stripped = lines[i].strip()
        if fence_len is not None:
            if re.match(r"^`{%d,}\s*$" % fence_len, stripped):
                fence_len = None
            i += 1
            continue
        fm = FENCE_RE.match(stripped)
        if fm:
            fence_len = len(fm.group("fence"))
            i += 1
            continue
        if adapter_section(lines[i]):
            break
        i += 1
    if fence_len is not None:
        raise SessionLogError("turn %d section %s fence is not closed" % (turn, section))
    return i


def rows_to_jsonl(rows):
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def convert_file(in_path, out_path=None, check=False):
    rows = parse_session_log(read_utf8(in_path))
    if check:
        return rows
    if not out_path:
        raise SessionLogError("--out is required unless --check is used")
    write_utf8(out_path, rows_to_jsonl(rows))
    return rows


def build_arg_parser():
    ap = argparse.ArgumentParser(
        description="Convert a UTF-8 Markdown live session log to T4-compatible JSONL."
    )
    ap.add_argument("--in", dest="in_path", help="input Markdown session log")
    ap.add_argument("--out", dest="out_path", help="output JSONL path (explicit; no default results dir)")
    ap.add_argument("--check", action="store_true", help="validate and parse the session log without writing")
    ap.add_argument(
        "--template",
        nargs="?",
        const=DEFAULT_TEMPLATE,
        help="print a starter template to stdout (defaults to the bundled template)",
    )
    return ap


def main(argv=None):
    ap = build_arg_parser()
    args = ap.parse_args(argv)

    try:
        if args.template:
            write_stdout_utf8(read_utf8(args.template))
            return 0
        if not args.in_path:
            ap.error("--in is required unless --template is used")
        if args.check and args.out_path:
            raise SessionLogError("--check does not write output; remove --out")
        rows = convert_file(args.in_path, args.out_path, args.check)
        if args.check:
            write_stdout_utf8("OK: %d turns parsed from %s\n" % (len(rows), args.in_path))
        return 0
    except SessionLogError as e:
        print("convert_session_log: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
