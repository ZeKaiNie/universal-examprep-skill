# -*- coding: utf-8 -*-
"""Cross-file semantic guards for skill routing, fallbacks, and documentation."""

import glob
import os
import re
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel):
    with open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8") as handle:
        return handle.read()


class CanonicalLanguageRouting(unittest.TestCase):
    def test_root_routes_on_persisted_values_not_aliases(self):
        text = read("SKILL.md")
        for value in ("zh", "en", "bilingual"):
            self.assertIn("- `%s` (display choice" % value, text)
        for alias in ("`中文`", "`English`", "`双语`"):
            self.assertNotIn("- %s →" % alias, text)
        self.assertIn("accepted user-facing input aliases", text)

    def test_every_control_language_section_uses_canonical_values(self):
        paths = glob.glob(os.path.join(ROOT, "skills", "*", "SKILL.md"))
        checked = 0
        for path in paths:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            if "## Language packs" not in text:
                continue
            checked += 1
            section = text.split("## Language packs", 1)[1].split("\n## ", 1)[0]
            for value in ("中文", "English", "双语"):
                self.assertIn("- `%s` →" % value, section, path)
            for alias in ("zh", "en", "bilingual"):
                self.assertNotIn("- `%s` →" % alias, section, path)
            self.assertIn("normalized", section, path)
        self.assertGreaterEqual(checked, 9)


class SafetySemanticContracts(unittest.TestCase):
    def test_web_without_bank_cannot_invent_checkpoint(self):
        zh = read("prompts/web_prompt.md")
        en = read("prompts/web_prompt.en.md")
        self.assertIn("没有挂载题库", zh)
        self.assertIn("不得用 AI 生成题冒充关卡测试", zh)
        self.assertIn("no mounted bank", en)
        self.assertIn("never a checkpoint", en)
        for text in (zh, en):
            self.assertIn("covered_unverified", text)
        self.assertNotIn("只有当用户没有提供任何题库时，你才可生成练习题", zh)
        self.assertNotIn("only when the student has provided no bank", en.lower())

    def test_ingest_manual_fallback_requires_missing_interpreter(self):
        text = read("skills/exam-ingest/SKILL.md")
        for phrase in ("explicitly confirmed by the student",
                       "direct interpreter probe",
                       "business/data failure",
                       "fail-loud operation error",
                       "Missing package files are not evidence"):
            self.assertIn(phrase, text)
        self.assertNotIn("switch immediately and silently", text)

    def test_no_state_with_python_initializes_before_writes(self):
        checks = {
            "skills/exam-quiz/SKILL.md": ("`study_state.json` is absent and Python works", " init`"),
            "skills/exam-review/SKILL.md": ("`study_state.json` is absent and Python works", " init`"),
            "skills/confusion-tracker/SKILL.md": ("study_state.json` is absent and Python works", " init`"),
        }
        for rel, phrases in checks.items():
            text = read(rel)
            for phrase in phrases:
                self.assertIn(phrase, text, rel)
            self.assertIn("truly cannot run", text, rel)

    def test_one_day_template_choice_is_nonblocking(self):
        text = read("skills/exam-tutor/SKILL.md")
        self.assertIn("In the `≤1天` tier, asking is forbidden", text)
        self.assertIn("immediately use `七步精讲`", text)
        self.assertIn("persist that inferred default silently", text)

    def test_source_quotes_do_not_exempt_generated_prose(self):
        text = read("skills/exam-study-guide/SKILL.md")
        self.assertIn("Source quotations/images stay original-language evidence", text)
        self.assertIn("Do not silently machine-translate source evidence", text)
        self.assertIn("Translation fields are explicitly AI-authored/localized teaching blocks", text)
        self.assertIn("Dispatch every agent-authored heading, explanation, step, answer", text)
        self.assertIn("Bilingual content is complete blockwise zh+en", text)


class DocumentationConsistency(unittest.TestCase):
    @staticmethod
    def _without_code(text):
        text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
        return re.sub(r"`[^`\n]*`", "", text)

    def test_static_markdown_relative_links_resolve(self):
        rels = ["SKILL.md", "README.md", "README.zh.md",
                "CONTRIBUTING.md", "CONTRIBUTING.zh.md", "CHANGELOG.md",
                "CHANGELOG.en.md", "docs/language-policy.md",
                "docs/language-policy.zh.md", "docs/localization.md",
                "docs/skill-architecture.md", "docs/skill-architecture.en.md",
                "docs/agent-portability.md", "docs/agent-portability.zh.md",
                "docs/file-format.md", "docs/file-format.en.md",
                "docs/pdf-capability-adapters.md",
                "docs/pdf-capability-adapters.en.md",
                "docs/openai-study-guide-adapter.md",
                "docs/openai-study-guide-adapter.zh.md",
                "docs/exam-audit.zh.md", "benchmark/docs/coverage-matrix.md",
                "benchmark/docs/coverage-matrix.en.md"]
        rels += [os.path.relpath(path, ROOT).replace("\\", "/")
                 for pattern in ("skills/*/SKILL.md", "locales/*/SKILL.md",
                                 "locales/*/skills/*.md")
                 for path in glob.glob(os.path.join(ROOT, *pattern.split("/")))]
        broken = []
        link_re = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
        for rel in sorted(set(rels)):
            text = self._without_code(read(rel))
            for raw in link_re.findall(text):
                target = raw.strip().split()[0].strip("<>")
                if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                    continue
                target = target.split("#", 1)[0].split("?", 1)[0]
                if not target:
                    continue
                resolved = os.path.normpath(os.path.join(ROOT, os.path.dirname(rel), target))
                if not os.path.exists(resolved):
                    broken.append("%s -> %s" % (rel, target))
        self.assertFalse(broken, "Broken Markdown links:\n" + "\n".join(broken))

    def test_readme_reader_links_follow_selected_language(self):
        en = read("README.md")
        zh = read("README.zh.md")

        en_targets = (
            "docs/file-format.en.md",
            "docs/skill-architecture.en.md",
            "docs/pdf-capability-adapters.en.md",
            "CHANGELOG.en.md",
            "benchmark/docs/coverage-matrix.en.md",
            "locales/en/skills/confusion-tracker.md",
            "CONTRIBUTING.md",
        )
        zh_targets = (
            "docs/openai-study-guide-adapter.zh.md",
            "docs/agent-portability.zh.md",
            "docs/language-policy.zh.md",
            "docs/exam-audit.zh.md",
            "locales/zh/skills/confusion-tracker.md",
            "CONTRIBUTING.zh.md",
        )
        for target in en_targets:
            self.assertIn(target, en, "English README misses English reader target %s" % target)
            self.assertNotIn(target, zh, "Chinese README points to English reader target %s" % target)
        for target in zh_targets:
            self.assertIn(target, zh, "Chinese README misses Chinese reader target %s" % target)
            self.assertNotIn(target, en, "English README points to Chinese reader target %s" % target)

        self.assertIn("English · [中文](README.zh.md)", en)
        self.assertIn("中文 · [English](README.md)", zh)
        self.assertIn("English · [中文](CONTRIBUTING.zh.md)", read("CONTRIBUTING.md"))
        self.assertIn("中文 · [English](CONTRIBUTING.md)", read("CONTRIBUTING.zh.md"))

    def test_compatibility_entries_stay_compact_and_point_to_control(self):
        for rel in ("locales/zh/SKILL.md", "locales/en/SKILL.md"):
            path = os.path.join(ROOT, *rel.split("/"))
            self.assertLess(os.path.getsize(path), 12000, rel)
            text = read(rel)
            self.assertIn("skills/exam-cram/SKILL.md", text)
            marker = "compatibility entry" if rel.startswith("locales/en") else "兼容入口"
            self.assertIn(marker, text)

    def test_templates_have_no_sample_date_phase_or_fixed_hours(self):
        banned = (re.compile(r"20\d\d-\d\d-\d\d"),
                  re.compile(r"第六阶段"), re.compile(r"Phase 6"),
                  re.compile(r"6 小时"), re.compile(r"6 hours", re.IGNORECASE))
        failures = []
        for path in glob.glob(os.path.join(ROOT, "locales", "*", "templates", "*.md")):
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            for pattern in banned:
                if pattern.search(text):
                    failures.append("%s: %s" % (os.path.relpath(path, ROOT), pattern.pattern))
        self.assertFalse(failures, "Template hard-codes:\n" + "\n".join(failures))

    def test_readmes_match_one_day_and_no_questions_semantics(self):
        en = read("README.md")
        zh = read("README.zh.md")
        self.assertIn("standard-bank drills/checkpoints may still verify mastery", en)
        self.assertIn("仍可用标准题库练习或阶段测验验证掌握", zh)
        for text in (en, zh):
            self.assertIn("no_questions", text)
            self.assertIn("covered_unverified", text)
        self.assertNotIn("绝不向你提问", zh)

    def test_web_copy_is_bounded_portable_fallback(self):
        zh = read("prompts/web_prompt.md")
        en = read("prompts/web_prompt.en.md")
        self.assertIn("便携降级版", zh)
        self.assertIn("portable fallback", en)
        self.assertNotIn("100% 模拟本地完整体验", zh)
        self.assertNotIn("100% simulation of the full local experience", en)


if __name__ == "__main__":
    unittest.main()
