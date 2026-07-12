# -*- coding: utf-8 -*-
"""PR F — runtime skill text describes current behavior, not version eras. Stdlib only.

Runtime/operational files must not carry V2.0 / V2.1 (or relative "new version") prose; version
history lives only in CHANGELOG.md. A skill should execute the current behavior directly (knowledge
provenance, diagram protocol, six quiz types, LLM Wiki lazy loading, …) instead of reasoning about
which version introduced what.

Scanned: README.md, SKILL.md, AGENTS.md, docs/*.md, prompts/*.md, skills/**/*.md, and templates/*.md
(the last are rendered into student workspaces, so they are runtime text too).

Allowlist (the ONLY exemption): a `version:` line INSIDE a file's leading YAML frontmatter
(machine-readable metadata, e.g. root SKILL.md). A `version:` line in the body is NOT exempt.
benchmark/ and CHANGELOG.md are the historical surfaces and are not scanned.
"""
import glob
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FORBIDDEN = ["V2.0", "V2.1", "v2.0", "v2.1", "New in V2", "重大更新特性", "突破性更新特性"]


def runtime_files():
    # v4-P2: SKILL.en.md → locales/en/SKILL.md; templates/ moved under locales/.
    # The locales/**/*.md glob keeps every language pack + template covered.
    rels = ["README.md", "SKILL.md", "AGENTS.md"]
    for pat in ("docs/*.md", "prompts/*.md", "skills/**/*.md", "locales/**/*.md"):
        for p in glob.glob(os.path.join(ROOT, pat), recursive=True):
            rels.append(os.path.relpath(p, ROOT).replace("\\", "/"))
    return sorted(set(rels))


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _frontmatter_last_line(lines):
    """1-based line number of the closing '---' of a leading frontmatter block, else 0."""
    if lines and lines[0].strip() == "---":
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                return idx + 1
    return 0


class NoVersionEraRuntimeTextTest(unittest.TestCase):
    def test_runtime_files_have_no_version_era_wording(self):
        offenders = []
        for rel in runtime_files():
            lines = read(rel).splitlines()
            fm_last = _frontmatter_last_line(lines)
            for i, line in enumerate(lines, 1):
                # exempt only a `version:` line within the leading frontmatter metadata
                if i <= fm_last and line.strip().startswith("version:"):
                    continue
                for tok in FORBIDDEN:
                    if tok in line:
                        offenders.append(f"{rel}:{i} -> {tok!r}")
        self.assertEqual(offenders, [], "运行时文本仍含版本号措辞: " + "; ".join(offenders))

    def test_readme_uses_capability_not_version_sections(self):
        r = read("README.md")
        self.assertNotIn("突破性更新特性", r, "README 仍把能力框定为「突破性更新特性」版本段")
        self.assertNotIn("重大更新特性", r, "README 仍把能力框定为「重大更新特性」版本段")
        for tok in ("V2.0", "V2.1"):
            self.assertNotIn(tok, r, f"README 仍含 {tok}")
        # relative "new version" framing also counts as version-era wording
        self.assertNotIn("新版", r, "README 仍用「新版」相对版本措辞，请直接描述当前能力")

    def test_history_preserved_in_changelog(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "CHANGELOG.md")), "缺少 CHANGELOG.md")
        c = read("CHANGELOG.md")
        self.assertIn("V2.1", c, "CHANGELOG 未保留 V2.1 历史")
        self.assertIn("V2.0", c, "CHANGELOG 未保留 V2.0 历史")

    def test_skill_frontmatter_version_is_the_only_exemption(self):
        lines = read("SKILL.md").splitlines()
        fm_last = _frontmatter_last_line(lines)
        self.assertTrue(fm_last, "SKILL.md 应有 frontmatter")
        self.assertTrue(any(ln.strip().startswith("version:") for ln in lines[:fm_last]),
                        "SKILL.md 应保留机读 version 元数据")
        for i, ln in enumerate(lines, 1):
            if i <= fm_last and ln.strip().startswith("version:"):
                continue
            for tok in ("V2.0", "V2.1"):
                self.assertNotIn(tok, ln, f"SKILL.md:{i} 含 {tok}（仅 frontmatter version 行可豁免）")


if __name__ == "__main__":
    unittest.main()
