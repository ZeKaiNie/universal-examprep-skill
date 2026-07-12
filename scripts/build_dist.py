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
import sys
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
    ("docs", (".md",)),
)
# dev-only scripts that live in scripts/ but must NOT ship (nothing today; listed for the test seam)
SCRIPT_EXCLUDES = ("build_dist.py",)   # the builder itself is a dev tool


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
                out.append(rel)
    return sorted(set(out))


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
                z.write(os.path.join(ROOT, *f.split("/")), f)
    except OSError as e:
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
