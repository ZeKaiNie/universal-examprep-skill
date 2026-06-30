# -*- coding: utf-8 -*-
"""PR E — confusion-tracker moved into skills/ so the collection is self-contained. Stdlib only.

Locks: the skill lives at skills/confusion-tracker/SKILL.md with valid frontmatter; no functional
SKILL.md remains at repo root; docs/README no longer treat confusion-tracker as an external
(outside-skills/) dependency.
"""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def frontmatter(text):
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end != -1 else ""


class ConfusionTrackerMoveTest(unittest.TestCase):
    def test_skill_now_in_collection(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "skills", "confusion-tracker", "SKILL.md")),
                        "skills/confusion-tracker/SKILL.md 不存在")

    def test_no_root_confusion_tracker_folder(self):
        # the legacy root confusion-tracker/ folder is removed entirely; confusion tracking lives in skills/
        self.assertFalse(os.path.isdir(os.path.join(ROOT, "confusion-tracker")),
                         "根目录 confusion-tracker/ 文件夹应已删除（疑难追踪现只在 skills/confusion-tracker/）")
        self.assertFalse(os.path.isfile(os.path.join(ROOT, "confusion-tracker", "SKILL.md")),
                         "根目录不应再有 confusion-tracker/SKILL.md")

    def test_frontmatter_valid_and_name_preserved(self):
        fm = frontmatter(read("skills", "confusion-tracker", "SKILL.md"))
        self.assertIn("name:", fm, "缺少 name")
        self.assertIn("description:", fm, "缺少 description")
        # as a skills/ collection member it must carry license, like the other sub-skills
        self.assertIn("license:", fm, "缺少 license（应与技能集合其他子技能一致）")
        self.assertIn("confusion-tracker", fm, "skill name 未保留为 confusion-tracker")

    def test_portability_doc_references_skills_path_only(self):
        c = read("docs", "agent-portability.md")
        self.assertIn("skills/confusion-tracker/SKILL.md", c, "未指向 skills/confusion-tracker/SKILL.md")
        # every confusion-tracker mention must be the skills/-prefixed path (no bare root reference)
        self.assertEqual(c.count("confusion-tracker"), c.count("skills/confusion-tracker"),
                         "agent-portability 仍有未带 skills/ 前缀的 confusion-tracker 引用")

    def test_architecture_lists_confusion_tracker_under_skills(self):
        self.assertIn("skills/confusion-tracker", read("docs", "skill-architecture.md"),
                      "skill-architecture 未把 confusion-tracker 列在 skills/ 下")

    def test_readme_lists_under_skills_not_as_root_skill(self):
        r = read("README.md")
        self.assertIn("skills/confusion-tracker", r, "README 未把 confusion-tracker 归入 skills/")
        self.assertNotIn("**`confusion-tracker/`**", r,
                         "README 仍把 confusion-tracker/ 当作根目录独立技能列出")


if __name__ == "__main__":
    unittest.main()
