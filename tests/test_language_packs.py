# -*- coding: utf-8 -*-
"""v4-P2 — language-pack structural alignment lint. Stdlib only.

Student-visible wording lives under locales/: locales/zh/SKILL.md and
locales/en/SKILL.md are compact compatibility indices, locales/<lang>/skills/*.md
the per-skill packs, locales/<lang>/templates/ the workspace templates, and
locales/<lang>/messages.json the msgid catalogs. This
module locks the two language trees to each other and to the engine:

  P1  pack rosters — zh and en carry the SAME per-skill pack file names, and
      neither has an exam-audit pack (read-only health check: no student copy
      by design — its Language-packs section says so).
  P2  catalog parity — locales/{zh,en}/messages.json key sets are equal and
      both match scripts/i18n.py's embedded catalog key set.
  P3  en-tree purity — every locales/en/**/*.md is pure English. The strict
      zero-CJK-outside-code-spans rule for the prose surfaces is enforced by
      tests/test_language_purity.py T1; here the whole tree (including the two
      structural exceptions) is scanned with exactly two documented structural
      strips: fenced code blocks (they show PERSISTED zh-canonical file content,
      e.g. confusion-tracker's progress-table skeleton — machine vocabulary that
      never drifts with the reply language) and the literal 《科目名称》 machine
      anchor in the templates.
  P4  structural twins — each zh pack's `## ` heading list equals its en
      twin's (fence-aware: a literal '## ' line inside a fenced example is
      content, not a heading).
  P5  template machine anchors — both language editions of the workspace
      templates keep the machine anchors byte-exact: <!-- PHASE_TABLE --> in
      study_plan_template.md, <!-- PHASE_CHECKLIST --> in
      study_progress_template.md, and the 《科目名称》 token in both.
  P6  router pin — the root SKILL.md stays a compact language-neutral router
      (<= 80 lines) that dispatches into locales/.
"""
import glob
import json
import os
import sys
import unittest

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import test_language_purity as LP   # noqa: E402  (shared purity machinery)
import i18n                          # noqa: E402  (embedded catalog = engine vocabulary)

SUBJECT_ANCHOR = u"《科目名称》"


def read_rel(rel):
    with open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8") as f:
        return f.read()


def rel_glob(pat):
    return sorted(os.path.relpath(p, ROOT).replace("\\", "/")
                  for p in glob.glob(os.path.join(ROOT, *pat.split("/")), recursive=True))


def en_tree_offenses(text):
    """CJK offenses in an en-tree file, with the two P3 structural strips:
    fenced blocks are persisted-content examples (skipped whole), and the
    《科目名称》 machine anchor is removed before the scan. Inline code spans
    stay exempt exactly as in the strict T1 detector."""
    out = []
    fence_len = 0
    for n, line in enumerate(text.splitlines(), 1):
        f = LP._FENCE_LINE_RE.match(line)
        if f:
            ticks = len(f.group(1))
            if fence_len == 0:
                fence_len = ticks
            elif ticks >= fence_len:
                fence_len = 0
            continue
        if fence_len:
            continue                                   # persisted-content example
        visible = LP.CODE_SPAN_RE.sub("", line).replace(SUBJECT_ANCHOR, "")
        hits = LP.EN_CJK_RE.findall(visible)
        if hits:
            out.append((n, "".join(hits)[:16], visible.strip()[:80]))
    return out


def md_headings(text):
    """Fence-aware list of `## ` heading lines."""
    heads = []
    fence_len = 0
    for line in text.splitlines():
        f = LP._FENCE_LINE_RE.match(line)
        if f:
            ticks = len(f.group(1))
            if fence_len == 0:
                fence_len = ticks
            elif ticks >= fence_len:
                fence_len = 0
            continue
        if fence_len == 0 and line.startswith("## "):
            heads.append(line.strip())
    return heads


class P1PackRosters(unittest.TestCase):
    def test_zh_en_pack_name_sets_equal_and_no_exam_audit(self):
        zh = {os.path.basename(p) for p in rel_glob("locales/zh/skills/*.md")}
        en = {os.path.basename(p) for p in rel_glob("locales/en/skills/*.md")}
        self.assertTrue(zh, u"locales/zh/skills/ 不应为空")
        self.assertEqual(zh, en, u"zh/en 语言包文件名集合必须一致")
        self.assertNotIn("exam-audit.md", zh,
                         u"exam-audit 是只读体检，设计上没有学生侧文案包")

    def test_compatibility_entries_exist(self):
        for rel in ("locales/zh/SKILL.md", "locales/en/SKILL.md"):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, *rel.split("/"))), rel)


class P2CatalogParity(unittest.TestCase):
    def test_message_catalogs_match_engine_embedded(self):
        emb_zh = set(i18n._EMBEDDED["zh"])
        emb_en = set(i18n._EMBEDDED["en"])
        self.assertEqual(emb_zh, emb_en, u"i18n 内嵌 zh/en 目录键集必须一致")
        for loc, emb in (("zh", emb_zh), ("en", emb_en)):
            catalog = json.loads(read_rel("locales/%s/messages.json" % loc))
            keys = set(catalog)
            self.assertEqual(keys, emb,
                             u"locales/%s/messages.json 键集必须与 scripts/i18n.py 内嵌目录一致"
                             u"（差集: +%r / -%r）" % (loc, sorted(keys - emb), sorted(emb - keys)))
            self.assertEqual(
                catalog, i18n._EMBEDDED[loc],
                u"locales/%s/messages.json 的文案值也必须与内嵌目录完全一致" % loc,
            )


class P3EnTreePurity(unittest.TestCase):
    def test_every_en_md_is_pure_english(self):
        files = rel_glob("locales/en/**/*.md")
        self.assertTrue(files, u"locales/en/ 下应有 md 文件")
        bad = []
        for rel in files:
            for n, chars, snippet in en_tree_offenses(read_rel(rel)):
                bad.append("%s L%d [%s] %s" % (rel, n, chars, snippet))
        self.assertFalse(
            bad,
            u"en 语言树残留 CJK（结构豁免仅：行内代码 span / fenced 持久化内容示例 / "
            u"《科目名称》 机器锚点）：\n" + "\n".join(bad))

    def test_machinery_flags_cjk_outside_strips(self):
        self.assertTrue(en_tree_offenses(u"Reply 「提示」 to get a hint."))
        self.assertEqual(en_tree_offenses(u"# Plan for 《科目名称》 (machine anchor)"), [])
        self.assertEqual(en_tree_offenses(u"```text\n## 💡 概念疑难点记录\n```"), [])
        self.assertEqual(en_tree_offenses(u"the `状态` values `待回顾`/`已回顾`"), [])


class P4StructuralTwins(unittest.TestCase):
    def test_zh_pack_headings_match_en_twin(self):
        for zh_rel in rel_glob("locales/zh/skills/*.md"):
            en_rel = zh_rel.replace("locales/zh/", "locales/en/")
            zh_h = md_headings(read_rel(zh_rel))
            en_h = md_headings(read_rel(en_rel))
            self.assertEqual(sorted(zh_h), sorted(en_h),
                             u"%s 与 %s 的 `## ` 标题集不一致（结构双胞胎被打破）"
                             % (zh_rel, en_rel))
            if "## Student-facing Output" in en_h:
                self.assertIn("## Student-facing Output", zh_h, zh_rel)


class P5TemplateAnchors(unittest.TestCase):
    def test_machine_anchors_survive_in_both_languages(self):
        for loc in ("zh", "en"):
            plan = read_rel("locales/%s/templates/study_plan_template.md" % loc)
            prog = read_rel("locales/%s/templates/study_progress_template.md" % loc)
            self.assertIn("<!-- PHASE_TABLE -->", plan, loc)
            self.assertIn(SUBJECT_ANCHOR, plan, loc)
            self.assertIn("<!-- PHASE_CHECKLIST -->", prog, loc)
            self.assertIn(SUBJECT_ANCHOR, prog, loc)


class P6RouterPin(unittest.TestCase):
    def test_root_skill_stays_a_compact_router(self):
        text = read_rel("SKILL.md")
        self.assertLessEqual(len(text.splitlines()), 80,
                             u"根 SKILL.md 应保持 ≤80 行的语言中性路由器，不得长回全量手册")
        self.assertIn("locales/", text, u"根路由器必须派发到 locales/ 语言包")


if __name__ == "__main__":
    unittest.main()
