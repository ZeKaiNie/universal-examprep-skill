# -*- coding: utf-8 -*-
"""v4 P1 — i18n vocabulary module + canonical-code state migration. Stdlib only.

The single-definition-point contract: persisted enums are language-neutral codes; zh/en
display strings live in catalogs; normalizers accept THREE generations of input (v4 codes,
zh display words, legacy four modes) and converge on codes; unknown values pass through
with a warning, never silently rewritten. Migration of a legacy state backs the file up.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import i18n                    # noqa: E402
import update_progress as up   # noqa: E402


class CanonMode(unittest.TestCase):
    def test_codes_are_idempotent(self):
        for c in i18n.MODES:
            self.assertEqual(i18n.canon_mode(c), (c, None, None))

    def test_zh_display_maps_to_code(self):
        self.assertEqual(i18n.canon_mode("零基础从头讲")[0], "from_scratch")
        self.assertEqual(i18n.canon_mode("某章起步补弱")[0], "shore_up")
        self.assertEqual(i18n.canon_mode("查缺补漏")[0], "fill_gaps")

    def test_legacy_four_modes_migrate_with_tier(self):
        code, tier, warn = i18n.canon_mode("panic")
        self.assertEqual((code, tier), ("from_scratch", "le1d"))
        self.assertIn("已废弃", warn)
        code, tier, _ = i18n.canon_mode("sprint")
        self.assertEqual((code, tier), ("fill_gaps", "d1_3"))
        for legacy in ("normal", "mock"):
            code, tier, _ = i18n.canon_mode(legacy)
            self.assertEqual((code, tier), ("fill_gaps", None))

    def test_unknown_kept_with_warning(self):
        code, tier, warn = i18n.canon_mode("随便学学")
        self.assertEqual((code, tier), ("随便学学", None))
        self.assertIn("非标准", warn)


class CanonTier(unittest.TestCase):
    def test_codes_and_zh_display(self):
        for c in i18n.TIERS:
            self.assertEqual(i18n.canon_tier(c), (c, None))
        self.assertEqual(i18n.canon_tier("≤1天")[0], "le1d")
        self.assertEqual(i18n.canon_tier(">7天")[0], "gt7d")

    def test_legacy_loose_aliases_survive(self):
        # the v3 alias table moved VERBATIM — spot-check the loosest entries
        for alias, code in (("当天", "le1d"), ("明天考", "le1d"), ("几天", "d1_3"),
                            ("一周", "d3_7"), ("还早", "gt7d"), ("时间充裕", "gt7d"),
                            ("1~3天", "d1_3"), ("＞7天", "gt7d")):
            self.assertEqual(i18n.canon_tier(alias), (code, None), alias)

    def test_unknown_kept_with_warning(self):
        v, warn = i18n.canon_tier("半年")
        self.assertEqual(v, "半年")
        self.assertIn("非标准", warn)


class CanonLanguage(unittest.TestCase):
    def test_three_generations(self):
        for src, code in (("zh", "zh"), ("中文", "zh"), ("简体中文", "zh"),
                          ("en", "en"), ("English", "en"), ("英文", "en"),
                          ("bilingual", "bilingual"), ("双语", "bilingual"), ("中英", "bilingual")):
            self.assertEqual(i18n.canon_language(src)[0], code, src)

    def test_ascii_aliases_case_insensitive(self):
        self.assertEqual(i18n.canon_language("ENGLISH")[0], "en")
        self.assertEqual(i18n.canon_language("Bilingual")[0], "bilingual")

    def test_workspace_language_fallback(self):
        self.assertEqual(i18n.workspace_language(None), "zh")
        self.assertEqual(i18n.workspace_language({}), "zh")
        self.assertEqual(i18n.workspace_language({"language": "English"}), "en")
        self.assertEqual(i18n.workspace_language({"language": "火星文"}), "zh")


class CanonStatuses(unittest.TestCase):
    def test_window_statuses(self):
        for src, code in (("在窗口", "in_window"), ("窗口外", "out_window"), ("已实测", "verified"),
                          ("in_window", "in_window"), ("verified", "verified")):
            self.assertEqual(i18n.canon_window_status(src), code)
        self.assertEqual(i18n.canon_window_status("神秘状态"), "神秘状态")   # passthrough

    def test_row_statuses_and_resolved_sets(self):
        for src, code in (("待复盘", "to_review"), ("待回顾", "to_revisit"), ("已订正", "corrected"),
                          ("已复盘", "reviewed"), ("已回顾", "revisited"), ("已解决", "resolved")):
            self.assertEqual(i18n.canon_row_status(src), code)
        self.assertEqual(i18n.canon_row_status("自订状态"), "自订状态")     # free strings tolerated
        # resolved-set membership works across generations via canon
        self.assertIn(i18n.canon_row_status("已订正"), i18n.MISTAKE_RESOLVED)
        self.assertIn(i18n.canon_row_status("已回顾"), i18n.CONFUSION_RESOLVED)
        self.assertNotIn(i18n.canon_row_status("待复盘"), i18n.MISTAKE_RESOLVED)


class Display(unittest.TestCase):
    def test_zh_display_roundtrip(self):
        for kind, codes in (("mode", i18n.MODES), ("tier", i18n.TIERS),
                            ("window", i18n.WINDOW_STATUSES), ("row", i18n.ROW_STATUSES)):
            for c in codes:
                disp = i18n.display(kind, c, "zh")
                self.assertNotEqual(disp, c, "缺 zh 显示词: %s.%s" % (kind, c))

    def test_en_catalog_is_structural_twin(self):
        zh, en = i18n.catalog("zh"), i18n.catalog("en")
        self.assertEqual(set(zh.keys()), set(en.keys()),
                         "zh/en 目录的键集合必须相等（结构对齐反漂移）")

    def test_unknown_passthrough(self):
        self.assertEqual(i18n.display("mode", "自由词"), "自由词")
        self.assertIsNone(i18n.display("mode", None))


class MdRoundTrip(unittest.TestCase):
    def test_codes_render_zh_and_parse_back_to_codes(self):
        st = up.default_state()
        st.update({"current_phase": 2, "mode": "fill_gaps", "time_budget": "d1_3",
                   "language": "en", "scope": "homework-only",
                   "mistake_archive": [{"id": "q1", "chapter": "2", "note": "x", "status": "to_review"}],
                   "confusion_log": [{"id": None, "chapter": "1", "note": "y", "status": "revisited"}],
                   "knowledge_window": [{"point": "堆", "chapter": "3", "status": "out_window", "note": ""}]})
        md = up.render_md(st)
        # the generated view stays Chinese (codes never leak to the student)
        self.assertIn("查缺补漏", md)
        self.assertIn("1-3天", md)
        self.assertIn("English", md)
        self.assertIn("待复盘", md)
        self.assertIn("已回顾", md)
        self.assertIn("窗口外", md)
        for code in ("fill_gaps", "d1_3", "to_review", "out_window"):
            self.assertNotIn(code, md, "代号泄漏进学生可见视图: %s" % code)
        # and the round-trip through parse_md + canon converges back on codes
        phase, mistakes, confusions, _cl, window, prefs = up.parse_md(md)
        self.assertEqual(phase, 2)
        self.assertEqual(i18n.canon_mode(prefs["mode"])[0], "fill_gaps")
        self.assertEqual(i18n.canon_tier(prefs["time_budget"])[0], "d1_3")
        self.assertEqual(i18n.canon_language(prefs["language"])[0], "en")
        self.assertEqual(mistakes[0]["status"], "to_review")
        self.assertEqual(confusions[0]["status"], "revisited")
        self.assertEqual(window[0]["status"], "out_window")


class LegacyStateMigration(unittest.TestCase):
    def _ws(self, tmp, state):
        with open(os.path.join(tmp, "study_state.json"), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)

    def _run(self, tmp, *argv):
        return subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "update_progress.py"), "--workspace", tmp] + list(argv),
            capture_output=True, text=True, encoding="utf-8")

    def test_zh_enum_state_migrates_with_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = {"version": 1, "current_phase": 1, "scope": None,
                      "mode": "查缺补漏", "time_budget": "1-3天", "language": "中文",
                      "preferences": {},
                      "mistake_archive": [{"id": "a", "chapter": "1", "note": "n", "status": "待复盘"}],
                      "confusion_log": [{"id": None, "chapter": None, "note": "c", "status": "已回顾"}],
                      "knowledge_window": [{"point": "p", "chapter": None, "status": "在窗口", "note": ""}],
                      "phase_checklist": [], "last_updated": None}
            self._ws(tmp, legacy)
            r = self._run(tmp, "add-mistake", "--id", "b", "--chapter", "2", "--note", "new")
            self.assertEqual(r.returncode, 0, r.stderr)
            st = json.load(open(os.path.join(tmp, "study_state.json"), encoding="utf-8"))
            self.assertEqual(st["mode"], "fill_gaps")
            self.assertEqual(st["time_budget"], "d1_3")
            self.assertEqual(st["language"], "zh")
            self.assertEqual(st["mistake_archive"][0]["status"], "to_review")
            self.assertEqual(st["mistake_archive"][1]["status"], "to_review")   # the new row
            self.assertEqual(st["confusion_log"][0]["status"], "revisited")
            self.assertEqual(st["knowledge_window"][0]["status"], "in_window")
            # backup of the pre-migration file exists and still holds the zh vocabulary
            bak = os.path.join(tmp, "study_state.json.v3bak")
            self.assertTrue(os.path.isfile(bak), "迁移未备份旧 state")
            old = json.load(open(bak, encoding="utf-8"))
            self.assertEqual(old["mode"], "查缺补漏")
            self.assertIn("已归一为 v4 代号", r.stderr)

    def test_v4_state_untouched_no_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            v4 = {"version": 1, "current_phase": 1, "scope": None,
                  "mode": "fill_gaps", "time_budget": "d1_3", "language": "en",
                  "preferences": {}, "mistake_archive": [], "confusion_log": [],
                  "knowledge_window": [], "phase_checklist": [], "last_updated": None}
            self._ws(tmp, v4)
            r = self._run(tmp, "add-confusion", "--note", "why")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(os.path.exists(os.path.join(tmp, "study_state.json.v3bak")),
                             "无迁移却生成了备份")

    def test_legacy_four_mode_state_migrates(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._ws(tmp, {"version": 1, "current_phase": 1, "mode": "panic",
                           "mistake_archive": [], "confusion_log": [],
                           "knowledge_window": [], "phase_checklist": [], "preferences": {}})
            r = self._run(tmp, "add-confusion", "--note", "why")
            self.assertEqual(r.returncode, 0, r.stderr)
            st = json.load(open(os.path.join(tmp, "study_state.json"), encoding="utf-8"))
            self.assertEqual(st["mode"], "from_scratch")
            self.assertEqual(st["time_budget"], "le1d")   # panic implies the ≤1-day tier

    def test_unknown_free_values_survive_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._ws(tmp, {"version": 1, "current_phase": 1, "mode": "自由学",
                           "mistake_archive": [{"id": "a", "chapter": None, "note": "n",
                                                "status": "特殊状态"}],
                           "confusion_log": [], "knowledge_window": [],
                           "phase_checklist": [], "preferences": {}})
            r = self._run(tmp, "add-confusion", "--note", "why")
            self.assertEqual(r.returncode, 0, r.stderr)
            st = json.load(open(os.path.join(tmp, "study_state.json"), encoding="utf-8"))
            self.assertEqual(st["mode"], "自由学")                       # never silently rewritten
            self.assertEqual(st["mistake_archive"][0]["status"], "特殊状态")


if __name__ == "__main__":
    unittest.main()
