# -*- coding: utf-8 -*-
"""Cleanup PR — runtime skill text names current behavior, not abstract "协议 / protocol" eras. Stdlib only.

The de-versioning pass removed V2.x labels, but some runtime text still used abstract protocol-style
names. Runtime files should describe the behavior directly (来源标注 / 画图先跑算法 / quiz_bank 抽题 …)
rather than make the agent reason about named protocols.

We ban only the specific abstract PHRASES below — NOT the single character `协议`, because license /
history wording legitimately uses it (e.g. `## 📝 开源协议`, `MIT License`, CHANGELOG entries).
Scanned: README.md, SKILL.md, AGENTS.md, docs/*.md, prompts/*.md, skills/**/*.md, templates/*.md.
Allowlist: CHANGELOG.md (historical terms) and benchmark/ (not scanned).
"""
import glob
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BANNED = [
    "V2.1 协议", "V2.0 协议", "协议落点",
    "防幻觉协议", "知识来源透明化协议", "画图题确定性处理协议",
    "V2.1 行为", "V2.0 行为",
]


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


class RuntimeWordingTest(unittest.TestCase):
    def test_no_abstract_protocol_phrases_in_runtime_files(self):
        offenders = []
        for rel in runtime_files():
            text = read(rel)
            for phrase in BANNED:
                if phrase in text:
                    offenders.append(f"{rel} -> {phrase!r}")
        self.assertEqual(offenders, [], "运行时文本仍含抽象「协议」措辞: " + "; ".join(offenders))

    def test_changelog_may_keep_historical_terms(self):
        # the history file is allowed to keep the old protocol names; this test documents that exemption
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "CHANGELOG.md")))

    def test_bare_xieyi_is_not_globally_banned(self):
        # a bare `协议` (开源协议 / MIT License) is fine — only the specific abstract phrases are banned
        self.assertNotIn("协议", BANNED, "不应全局禁用单字「协议」（license / 历史措辞合法使用它）")
        # README.md is English-canonical now; the Chinese license heading 「开源协议」 lives in README.zh.md
        self.assertIn("开源协议", read("README.zh.md"), "license 段「开源协议」不应被误删")


if __name__ == "__main__":
    unittest.main()
