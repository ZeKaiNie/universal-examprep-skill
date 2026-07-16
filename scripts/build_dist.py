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
)

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
    """Remove ordinary Python comments without touching strings or source files.

    The first-line shebang and a PEP 263 encoding cookie on line one or two are
    runtime metadata, so they remain byte-equivalent after decoding/re-encoding.
    Token coordinates are applied to decoded physical lines; line endings and all
    non-comment tokens stay otherwise unchanged.
    """
    encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    text = data.decode(encoding)
    lines = text.splitlines(True)
    comments = []
    for token in tokenize.tokenize(io.BytesIO(data).readline):
        if token.type != tokenize.COMMENT:
            continue
        row = token.start[0]
        keep = (
            (row == 1 and token.string.startswith("#!"))
            or (row <= 2 and _CODING_COOKIE.match(token.string))
        )
        if not keep:
            comments.append((row, token.start[1], token.end[1]))
    for row, start, end in reversed(comments):
        line = lines[row - 1]
        lines[row - 1] = line[:start].rstrip(" \t\f") + line[end:]
    return "".join(lines).encode(encoding)


def _runtime_bytes(rel):
    with open(os.path.join(ROOT, *rel.split("/")), "rb") as stream:
        data = stream.read()
    return _strip_python_comments(data) if rel.endswith(".py") else data


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
