# -*- coding: utf-8 -*-
"""P0-V1 visual-first asset display contract tests.

These are static, deterministic guards: no real course assets, no UI rendering, no network/LLM.
They keep the prompt/docs contract aligned with the validator's fail-closed schema checks.
"""
import os
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RUNTIME_CONTRACT_FILES = [
    "SKILL.md",
    "AGENTS.md",
    "prompts/web_prompt.md",
    "skills/exam-cram/SKILL.md",
    "skills/exam-quiz/SKILL.md",
    "skills/exam-review/SKILL.md",
    "skills/exam-tutor/SKILL.md",
]

SCAN_TEXT_DIRS = ("docs", "prompts", "skills", "tests", "benchmark")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


class VisualAssetContractTest(unittest.TestCase):
    def test_file_format_defines_visual_first_contract_once(self):
        txt = read("docs/file-format.md")
        self.assertIn("### Visual-first display contract", txt)
        section = txt.split("### Visual-first display contract", 1)[1].split(
            "### Markdown / local path display guidance", 1)[0]
        self.assertIn("For any item with `requires_assets=true` or `maybe_requires_assets=true`", section)
        self.assertIn("Before asking, explaining, hinting, or solving", section)
        self.assertIn("题面图 / question-side asset", section)
        self.assertIn("答案图 / answer-side asset", section)
        self.assertIn("Do not show answer-side assets", section)
        self.assertLess(section.index("question-side asset"), section.index("answer-side assets"))

    def test_runtime_entrypoints_reference_visual_first_gate(self):
        for rel in RUNTIME_CONTRACT_FILES:
            txt = read(rel)
            self.assertIn("requires_assets=true", txt, rel)
            self.assertIn("maybe_requires_assets=true", txt, rel)
            self.assertIn("题面图 / question-side asset", txt, rel)
            self.assertIn("answer-side", txt, rel)
            self.assertTrue(
                ("fail-closed" in txt) or ("跳过" in txt) or ("skip" in txt) or ("绝不出" in txt),
                rel,
            )

    def test_file_format_documents_question_vs_answer_side_roles(self):
        txt = read("docs/file-format.md")
        self.assertIn("question_context", txt)
        self.assertIn("figure", txt)
        self.assertIn("diagram", txt)
        self.assertIn("table", txt)
        self.assertIn("answer_context", txt)
        self.assertIn("worked_solution", txt)
        self.assertIn("只有答案侧 asset", txt)
        self.assertIn("无法在出题前展示题目", txt)

    def test_markdown_path_guidance_prefers_workspace_relative_paths(self):
        txt = read("docs/file-format.md")
        self.assertIn("Prefer the workspace-relative asset path", txt)
        self.assertIn("references/assets/ch01_p012_quiz_1_1.png", txt)
        self.assertIn("must not claim that an image was displayed", txt)
        self.assertIn("only printed a path", txt)

    def test_no_malformed_windows_drive_markdown_examples(self):
        bad = "/" + "D:/"
        hits = []
        roots = [os.path.join(ROOT, d) for d in SCAN_TEXT_DIRS]
        roots.extend(os.path.join(ROOT, f) for f in ("SKILL.md", "AGENTS.md"))
        for root in roots:
            if os.path.isfile(root):
                candidates = [root]
            else:
                candidates = []
                for dirpath, dirnames, filenames in os.walk(root):
                    dirnames[:] = [d for d in dirnames if d not in {".git", "__pycache__"}]
                    for name in filenames:
                        if name.endswith((".md", ".py", ".json", ".txt")):
                            candidates.append(os.path.join(dirpath, name))
            for path in candidates:
                try:
                    with open(path, encoding="utf-8") as f:
                        text = f.read()
                except UnicodeDecodeError:
                    continue
                if bad in text:
                    hits.append(os.path.relpath(path, ROOT))
        self.assertEqual(hits, [], "malformed Windows Markdown path literal found")

    def test_prompts_do_not_allow_explaining_visual_item_before_image(self):
        banned = [
            "只打印路径也算显示",
            "仅打印路径也算显示",
            "可以先讲解再显示图",
            "先给答案再显示题面图",
            "path is enough",
            "explain before showing the image",
        ]
        for rel in RUNTIME_CONTRACT_FILES + ["docs/file-format.md"]:
            txt = read(rel)
            for phrase in banned:
                self.assertNotIn(phrase, txt, rel)
            self.assertTrue(
                ("Before asking, explaining, hinting, or solving" in txt)
                or ("before asking, explaining, hinting, or solving" in txt)
                or ("再问题、提示、讲解或给答案" in txt)
                or ("提问、提示、讲解、解答之前" in txt)
                or ("before routing into teaching" in txt)
                or ("Before explaining, hinting" in txt)
                or ("真正渲染/显示出来" in txt),
                rel,
            )

    def test_web_prompt_gates_stub_and_page_reference_items(self):
        txt = read("prompts/web_prompt.md")
        self.assertIn('question_text_status="stub"', txt)
        self.assertIn('"page_reference"', txt)
        self.assertIn("原页上下文", txt)
        self.assertIn("题面自足的 `full` 题", txt)


if __name__ == "__main__":
    unittest.main()
