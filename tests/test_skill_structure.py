#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Structure tests for the portable skill collection (PR A).

Pure stdlib. Verifies the skills/ collection, the canonical AGENTS.md fallback, and the
portability docs exist and are well-formed — WITHOUT asserting any behavior change. Run with:

    python -m unittest discover -s tests -v
"""

import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SKILL_DIRS = [
    "exam-cram", "exam-ingest", "exam-tutor", "exam-quiz",
    "exam-review", "exam-cheatsheet", "exam-audit", "exam-help",
    "confusion-tracker",
]

# core anti-hallucination rules the compact fallback must carry (invariants, see docs/skill-architecture.md)
AGENTS_INVARIANTS = ["study_progress.md", "references/wiki/", "quiz_bank.json",
                     "惰性", "AI 生成", "skills/exam-cram/SKILL.md"]


def read(*parts):
    with open(os.path.join(ROOT, *parts), "r", encoding="utf-8") as f:
        return f.read()


def frontmatter(text):
    """Extract top-level `key: value` pairs from a leading `---` ... `---` YAML-ish block.

    Folded values (`description: >`) yield an empty same-line value but the key is still recorded,
    so callers test key PRESENCE for description and a non-empty value for name/license.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    fm, depth = {}, None
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        if ln and not ln[0].isspace() and ":" in ln:      # a top-level key
            key, _, val = ln.partition(":")
            fm[key.strip()] = val.strip()
    return fm


class TestSkillCollectionStructure(unittest.TestCase):

    def test_root_skill_md_still_exists(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "SKILL.md")),
                        "root SKILL.md must remain as the compatibility entrypoint")

    def test_all_expected_skill_files_exist(self):
        for d in SKILL_DIRS:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "skills", d, "SKILL.md")),
                            f"missing skills/{d}/SKILL.md")

    def test_each_skill_has_name_description_license(self):
        for d in SKILL_DIRS:
            fm = frontmatter(read("skills", d, "SKILL.md"))
            self.assertIsNotNone(fm, f"skills/{d}/SKILL.md must start with a --- frontmatter block")
            for key in ("name", "description", "license"):
                self.assertIn(key, fm, f"skills/{d}/SKILL.md frontmatter missing '{key}'")
            self.assertTrue(fm["name"], f"skills/{d}/SKILL.md frontmatter 'name' is empty")
            self.assertTrue(fm["license"], f"skills/{d}/SKILL.md frontmatter 'license' is empty")

    def test_skill_names_are_unique_and_nonempty(self):
        names = []
        for d in SKILL_DIRS:
            fm = frontmatter(read("skills", d, "SKILL.md"))
            name = (fm or {}).get("name", "")
            self.assertTrue(name, f"skills/{d}/SKILL.md has an empty frontmatter name")
            names.append(name)
        self.assertEqual(len(names), len(set(names)), f"duplicate skill names: {names}")

    def test_main_skill_is_exam_cram(self):
        fm = frontmatter(read("skills", "exam-cram", "SKILL.md"))
        self.assertEqual((fm or {}).get("name"), "exam-cram")

    def test_agents_md_exists_with_core_fallback_rules(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "AGENTS.md")), "AGENTS.md must exist")
        body = read("AGENTS.md")
        for needle in AGENTS_INVARIANTS:
            self.assertIn(needle, body, f"AGENTS.md missing core fallback rule mention: {needle!r}")

    def test_portability_doc_exists(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "docs", "agent-portability.md")))

    def test_skill_architecture_doc_exists(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "docs", "skill-architecture.md")))

    def test_existing_confusion_tracker_skill_preserved(self):
        # the confusion-tracker sub-skill now lives inside skills/ (moved in PR E)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "skills", "confusion-tracker", "SKILL.md")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
