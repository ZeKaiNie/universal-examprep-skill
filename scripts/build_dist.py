#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Runtime-distribution builder (v4-P6) — assemble the student-facing runtime bundle, pure stdlib.

Why: a full clone is 300+ files where >80% is dev-only weight (benchmark/tests/spike/assets);
students installing the skill need ~60 files. This tool zips EXACTLY the runtime surface by an
EXPLICIT allowlist — the manifest below is the executable definition of "runtime", and
tests/test_build_dist.py asserts it stays in sync with reality (a new script referenced by skill
texts but missing here reds the suite; a manifest entry that vanished from disk reds too).

Layout inside the zip mirrors the repo root, so the `${CLAUDE_SKILL_DIR}` resolution contract
(skills/ two levels deep, scripts/ + locales/ + docs/ + prompts/ at root) holds unchanged after
`unzip into .claude/skills/universal-exam-cram-coach/`.

    python scripts/build_dist.py                        # → dist/universal-exam-cram-coach.zip
    python scripts/build_dist.py --out <path.zip> --print-manifest
Exit: 0 ok · 1 build failure · 2 manifest drift (missing file on disk)
"""
import argparse
import ast
import io
import os
import re
import sys
import tokenize
import zipfile

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# v4.3 ships both the lightweight default and the opt-in full ingestion / strict
# Study Guide toolchain in one dependency-free archive. Keep a hard ceiling so
# accidental dev-surface leaks still fail CI, while leaving a small deterministic
# margin above the audited roughly 795 KB v4.3 release candidate.
MAX_RUNTIME_ZIP_BYTES = 850_000

# ---- the executable definition of the runtime surface ----
# Directories are included RECURSIVELY but only with the listed extensions; single files verbatim.
RUNTIME_FILES = (
    "SKILL.md",
    "AGENTS.md",
    "LICENSE",
)
RUNTIME_DIRS = (
    # (dir, allowed extensions)
    ("skills", (".md",)),
    ("locales", (".md", ".json")),
    ("scripts", (".py",)),
    ("prompts", (".md",)),
    ("docs", (".md", ".json")),
)
# Source-checkout-only build/evaluation tools.  Runtime commands and adapters
# stay included; the frozen retrieval gate lives under benchmark/ and is not a
# student execution path, so its library ships with that harness rather than
# inflating every installed skill.
SCRIPT_EXCLUDES = ("build_dist.py", "retrieval_evaluation.py")
# Maintainer-only paths are useful in a source checkout but are not part of the
# student-facing runtime contract. Prefixes use normalized repo-relative forward slashes.
PATH_EXCLUDES = (
    # Imported only by the maintainer ingestion-Gold test suite; no runtime
    # command, skill, or host adapter references this evaluator.
    "scripts/ingestion/evaluation.py",
    "docs/plans/",
    "docs/history/",
    "docs/releases/",
    # The executable benchmark harness and frozen Gold sets are source-checkout
    # maintainer tools, so do not ship a runtime document whose commands are absent.
    "docs/retrieval-evaluation.md",
    # Repository-maintenance architecture/localization notes are linked from
    # the source README, which is itself outside the student runtime.  The live
    # language and portability contracts remain shipped separately.
    "docs/skill-architecture.md",
    "docs/localization.md",
    # Maintainer-facing audit JSON reference; the shipped exam-ingest skill
    # carries the complete runtime command and fail-closed handoff contract.
    "docs/formula-audit-importer.md",
    # Build-only source for the compact student copy of docs/file-format.md.
    "docs/runtime-file-contract.md",
)

RUNTIME_SUBSTITUTES = {
    # Keep established runtime links stable while avoiding the exhaustive
    # contributor/audit schema in every student install. Exact validation lives
    # in shipped scripts; this compact reference retains the agent-facing rules.
    "docs/file-format.md": "docs/runtime-file-contract.md",
}

_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_CODING_COOKIE = re.compile(
    r"^[ \t\f]*#.*?coding[:=][ \t]*[-_.A-Za-z0-9]+"
)


def is_runtime_path(rel):
    """Whether a normalized repo-relative path belongs in the student runtime bundle."""
    norm = (rel or "").replace("\\", "/").lstrip("./")
    return not any(norm == prefix.rstrip("/") or norm.startswith(prefix)
                   for prefix in PATH_EXCLUDES)


def manifest():
    """Sorted repo-relative paths (forward slashes) of every file the bundle ships."""
    out = []
    for rel in RUNTIME_FILES:
        out.append(rel)
    for d, exts in RUNTIME_DIRS:
        base = os.path.join(ROOT, d)
        for dirpath, _dirs, files in os.walk(base):
            for fn in sorted(files):
                if not fn.lower().endswith(exts):
                    continue
                if d == "scripts" and fn in SCRIPT_EXCLUDES:
                    continue
                rel = os.path.relpath(os.path.join(dirpath, fn), ROOT).replace("\\", "/")
                if not is_runtime_path(rel):
                    continue
                out.append(rel)
    return sorted(set(out))


def _strip_python_comments(data):
    """Remove ordinary comments and shrink docstrings without touching source files.

    The first-line shebang and a PEP 263 encoding cookie on line one or two are
    runtime metadata and remain byte-equivalent.  Docstrings become empty string
    literals, which keeps future-import placement, statement structure, and line
    numbers valid without shipping their non-runtime prose.
    """
    encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    text = data.decode(encoding)
    lines = text.splitlines(True)
    offsets = []
    position = 0
    for line in lines:
        offsets.append(position)
        position += len(line)

    def absolute(row, column):
        return offsets[row - 1] + column

    replacements = []
    for token in tokenize.tokenize(io.BytesIO(data).readline):
        if token.type != tokenize.COMMENT:
            continue
        row = token.start[0]
        keep = (
            (row == 1 and token.string.startswith("#!"))
            or (row <= 2 and _CODING_COOKIE.match(token.string))
        )
        if not keep:
            prefix = lines[row - 1][:token.start[1]]
            start_column = len(prefix.rstrip(" \t\f"))
            replacements.append((
                absolute(row, start_column),
                absolute(token.end[0], token.end[1]),
                "",
            ))

    tree = ast.parse(text)
    for node in ast.walk(tree):
        if not isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        statement = body[0]
        if (not isinstance(statement, ast.Expr)
                or not isinstance(statement.value, ast.Constant)
                or not isinstance(statement.value.value, str)):
            continue
        start_line = lines[statement.lineno - 1]
        end_line = lines[statement.end_lineno - 1]
        start_column = len(
            start_line.encode("utf-8")[:statement.col_offset].decode("utf-8")
        )
        end_column = len(
            end_line.encode("utf-8")[:statement.end_col_offset].decode("utf-8")
        )
        start = absolute(statement.lineno, start_column)
        end = absolute(statement.end_lineno, end_column)
        line_breaks = "".join(
            char for char in text[start:end] if char in "\r\n"
        )
        replacement = "''"
        if statement.end_lineno > statement.lineno:
            # Keep the replacement as one logical statement that starts on the
            # original line and closes on the original final line.  Appending
            # newlines after ``''`` would move a legal closing-line suffix such
            # as ``; return value`` to the beginning of a new logical line.
            replacement = "(''" + line_breaks + ")"
        replacements.append((start, end, replacement))

    for start, end, replacement in sorted(replacements, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text.encode(encoding)


def _compact_python_layout(data):
    """Remove only tokenizer-declared non-significant physical newlines.

    ``tokenize.NL`` marks layout inside implicit continuations and otherwise
    blank physical lines; unlike ``NEWLINE`` it never terminates a Python
    statement.  Re-emitting the remaining token type/string pairs through the
    stdlib untokenizer therefore preserves the executable token stream while
    avoiding source-only indentation and wrapping in the release archive.

    The one exception is an ``NL`` immediately following a retained shebang or
    PEP 263 comment: that physical newline must remain so the comment cannot
    consume the next token.
    """
    compact = []
    previous_type = None
    for token in tokenize.tokenize(io.BytesIO(data).readline):
        if token.type == tokenize.NL and previous_type != tokenize.COMMENT:
            continue
        compact.append((token.type, token.string))
        previous_type = token.type
    rendered = tokenize.untokenize(compact)
    return rendered if isinstance(rendered, bytes) else rendered.encode("utf-8")


def _runtime_bytes(rel):
    source_rel = RUNTIME_SUBSTITUTES.get(rel, rel)
    with open(os.path.join(ROOT, *source_rel.split("/")), "rb") as stream:
        data = stream.read()
    # Git checkouts may expose text as LF, CRLF, or a mixture after a partial
    # edit.  Ship one canonical byte form so the package budget and release
    # digest do not depend on the maintainer's host or core.autocrlf setting.
    data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if rel.endswith(".py"):
        return _compact_python_layout(_strip_python_comments(data))
    return data


def _zip_info(rel):
    info = zipfile.ZipInfo(rel, date_time=_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def build(out_path):
    files = manifest()
    missing = [f for f in files if not os.path.isfile(os.path.join(ROOT, *f.split("/")))]
    if missing:
        sys.stderr.write("build_dist: 清单文件在磁盘上不存在（清单漂移，先修清单或补文件）：\n  "
                         + "\n  ".join(missing) + "\n")
        return 2
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    try:
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for f in files:
                z.writestr(
                    _zip_info(f),
                    _runtime_bytes(f),
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=9,
                )
    except (OSError, UnicodeError, SyntaxError, tokenize.TokenError) as e:
        sys.stderr.write("build_dist: 写包失败: %s\n" % e)
        return 1
    kb = os.path.getsize(out_path) / 1024.0
    print("[+] dist: %s（%d 个文件，%.0f KB）" % (out_path, len(files), kb))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Build the runtime distribution zip from the explicit manifest (stdlib only).")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist", "universal-exam-cram-coach.zip"))
    ap.add_argument("--print-manifest", action="store_true",
                    help="print the manifest one path per line and exit 0 (no zip written)")
    args = ap.parse_args(argv)
    if args.print_manifest:
        print("\n".join(manifest()))
        return 0
    return build(args.out)


if __name__ == "__main__":
    sys.exit(main())
