#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Build the installable skill bundle: dist/exam-cram-coach-flash.zip

The zip contains one folder, `exam-cram-coach/`, holding exactly what a host needs
(SKILL.md, coach.py, coach/, README*, LICENSE, CHANGELOG). Unzip it into the agent's
skills directory and the skill is installed.

    python release.py            # writes dist/exam-cram-coach-flash.zip and prints its contents
"""
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
FOLDER = "exam-cram-coach"
FILES = ["SKILL.md", "coach.py", "README.md", "README.zh.md", "LICENSE", "CHANGELOG.md"]
PACKAGE = "coach"


def build(out_path):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    names = []
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in FILES:
            zf.write(os.path.join(ROOT, name), "%s/%s" % (FOLDER, name))
            names.append(name)
        for fname in sorted(os.listdir(os.path.join(ROOT, PACKAGE))):
            if fname.endswith(".py"):
                zf.write(os.path.join(ROOT, PACKAGE, fname), "%s/%s/%s" % (FOLDER, PACKAGE, fname))
                names.append("%s/%s" % (PACKAGE, fname))
    return names


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist", "exam-cram-coach-flash.zip")
    names = build(out)
    print("%s (%d files, %.0f KB)" % (out, len(names), os.path.getsize(out) / 1024))
    for n in names:
        print("  " + n)
