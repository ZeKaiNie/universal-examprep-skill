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
    "exam-cram", "exam-ingest", "exam-tutor", "exam-study-guide", "exam-quiz",
    "exam-review", "exam-cheatsheet", "exam-audit", "exam-help",
    "confusion-tracker",
]

# core anti-hallucination rules the compact fallback must carry (invariants, see docs/skill-architecture.md)
AGENTS_INVARIANTS = ["study_progress.md", "references/wiki/", "quiz_bank.json",
                     "Lazy-load", "AI-generated", "skills/exam-cram/SKILL.md"]


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


def nested_frontmatter_value(text, section, key):
    """Read one scalar from a single-indented frontmatter mapping.

    The repository deliberately keeps its structure checks dependency-free, so
    this small reader covers the root skill's ``metadata.version`` without
    pulling in a YAML package.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    in_section = False
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line and not line[0].isspace():
            in_section = line.strip() == f"{section}:"
            continue
        if in_section and line.startswith("  ") and ":" in line:
            child_key, _, value = line.strip().partition(":")
            if child_key == key:
                return value.strip().strip('"').strip("'")
    return None


class TestSkillCollectionStructure(unittest.TestCase):

    def test_root_skill_md_still_exists(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "SKILL.md")),
                        "root SKILL.md must remain as the compatibility entrypoint")

    def test_root_skill_metadata_matches_current_release(self):
        version = nested_frontmatter_value(read("SKILL.md"), "metadata", "version")
        self.assertEqual(
            version,
            "4.2",
            "root SKILL.md metadata must be bumped with the current release; stale metadata "
            "makes a current install look like an older skill",
        )

    def test_release_tag_matches_root_skill_metadata_when_provided(self):
        tag = os.environ.get("EXPECTED_RELEASE_TAG")
        if not tag:
            self.skipTest("release-only tag/metadata gate")
        version = nested_frontmatter_value(read("SKILL.md"), "metadata", "version")
        self.assertEqual(
            tag,
            "v" + str(version),
            "release tag and root SKILL.md metadata.version must identify the same version",
        )
        changelog_headings = [
            line.strip() for line in read("CHANGELOG.md").splitlines()
            if line.startswith("## ")
        ]
        self.assertTrue(
            any(heading == "## V" + str(version)
                or heading.startswith("## V" + str(version) + " ")
                for heading in changelog_headings),
            "CHANGELOG.md must contain a section for the release version",
        )

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
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "docs", "pdf-capability-adapters.md")))

    def test_skill_architecture_doc_exists(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "docs", "skill-architecture.md")))

    def test_existing_confusion_tracker_skill_preserved(self):
        # the confusion-tracker sub-skill now lives inside skills/ (moved in PR E)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "skills", "confusion-tracker", "SKILL.md")))

    def test_tutor_reaches_teaching_examples_through_chapter_slice(self):
        body = read("skills", "exam-tutor", "SKILL.md")
        self.assertIn("list_teaching_examples.py", body)
        self.assertIn("--chapter <N>", body)
        self.assertIn("nonzero exit", body)

    def test_human_reading_view_is_routed_without_silent_skill_download(self):
        tutor = read("skills", "exam-tutor", "SKILL.md")
        agents = read("AGENTS.md")
        self.assertIn("study_guide_render.py", tutor)
        self.assertIn("pdf-capability-adapters.md", tutor)
        self.assertIn("never silently download", tutor.lower())
        self.assertIn("pdf-capability-adapters.json", agents)

    def test_artifact_mode_is_explicit_economical_and_one_shot_safe(self):
        cram = read("skills", "exam-cram", "SKILL.md")
        cheatsheet = read("skills", "exam-cheatsheet", "SKILL.md")
        help_en = read("locales", "en", "skills", "exam-help.md")
        help_zh = read("locales", "zh", "skills", "exam-help.md")

        for body in (cram, cheatsheet, help_en, help_zh):
            self.assertIn("artifact_mode", body)
            self.assertIn("chat", body)
            self.assertIn("visual", body)
        self.assertIn("never a fourth required", cram.lower())
        self.assertIn("subscription tier", cram.lower())
        self.assertIn("one-shot", cram.lower())
        self.assertIn("does not modify the persisted value", cheatsheet)
        self.assertIn("不自动生成章节 HTML/PDF", help_zh)
        self.assertIn("do not automatically build chapter HTML/PDF", help_en)


if __name__ == "__main__":
    unittest.main(verbosity=2)
