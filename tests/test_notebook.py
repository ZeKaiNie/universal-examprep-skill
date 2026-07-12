# -*- coding: utf-8 -*-
"""v4-P4 — notebook engine (scripts/notebook.py). Stdlib only.

The frozen CLI contract under test:
  * add-entry writes/creates notebook/ch<NN>.md (NN zero-padded 2) from a STDIN body,
    appending '## [#<id>] <title>' + one meta line (type-label · timestamp) + body + '---';
  * the same --id in the same chapter REPLACES the entry in place (idempotent re-teach);
  * every add-entry deterministically REBUILDS notebook/index.md (byte-identical TOC);
  * --mistake mirrors the entry into mistakes/ch<NN>.md and rebuilds mistakes/index.md,
    joining each entry to its study_state mistake row by id for the status suffix;
  * headings/labels come from i18n msgids (--lang zh|en; default study_state language → zh);
  * chapter files parse back by their '## [#<id>]' block markers, fence-aware — a '## '
    line inside a fenced code block is content, never a block boundary;
  * atomic writes + workspace containment + symlink refusal (update_progress conventions);
  * exit 0 ok · 1 read/write failure · 2 usage (empty body included).
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
NOTEBOOK = os.path.join(SCRIPTS, "notebook.py")

TS_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")


def run_nb(ws, *argv, **kw):
    stdin = kw.pop("stdin", "")
    assert not kw, kw
    return subprocess.run(
        [sys.executable, NOTEBOOK, "--workspace", ws] + list(argv),
        input=stdin, capture_output=True, text=True, encoding="utf-8")


def add(ws, chapter, eid, body, *extra):
    return run_nb(ws, "add-entry", "--chapter", str(chapter), "--type", "walkthrough",
                  "--id", eid, *extra, stdin=body)


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return f.read()


def read_bytes(*parts):
    with open(os.path.join(*parts), "rb") as f:
        return f.read()


def write_state(ws, **fields):
    st = {"version": 1, "current_phase": 1, "scope": None, "mode": None,
          "time_budget": None, "language": None, "preferences": {},
          "mistake_archive": [], "confusion_log": [], "knowledge_window": [],
          "phase_checklist": [], "last_updated": None}
    st.update(fields)
    with open(os.path.join(ws, "study_state.json"), "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)


class AddEntryBasics(unittest.TestCase):
    def test_add_creates_chapter_meta_index_and_receipt(self):
        with tempfile.TemporaryDirectory() as ws:
            r = add(ws, 2, "q13", "第一段讲解。\n\n- 要点A\n- 要点B", "--title", "Venn 图判断")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("[+]", r.stdout)
            ch = read(ws, "notebook", "ch02.md")
            self.assertIn("## [#q13] Venn 图判断", ch)
            m = re.search(r"^> (\S+) · (\d{4}-\d{2}-\d{2} \d{2}:\d{2})$", ch, re.M)
            self.assertTrue(m, "缺 '> 类型 · 时间戳' 元行:\n" + ch)
            self.assertEqual(m.group(1), "精讲")
            self.assertIn("- 要点A", ch)
            self.assertTrue(ch.rstrip().endswith("---"), "条目块须以 --- 收尾:\n" + ch)
            idx = read(ws, "notebook", "index.md")
            self.assertTrue(idx.startswith("# 📒 学习笔记目录"), idx)
            self.assertIn("## 第 2 章", idx)
            self.assertIn("- [Venn 图判断](ch02.md#q13-venn-图判断)", idx)

    def test_zero_padded_chapter_and_default_title(self):
        with tempfile.TemporaryDirectory() as ws:
            r = add(ws, 7, "hw3_1", "正文")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(os.path.isfile(os.path.join(ws, "notebook", "ch07.md")))
            ch = read(ws, "notebook", "ch07.md")
            self.assertIn("## [#hw3_1] hw3_1", ch)      # --title 缺省 = id
            self.assertIn("(ch07.md#hw3_1-hw3_1)", read(ws, "notebook", "index.md"))

    def test_replace_keyed_on_id_and_type(self):
        # Codex r4：替换键 = (id, type)。同 id 同 type 幂等替换；同 id 异 type（先精讲后判分，
        # 契约规定的正常流）必须追加——旧的 id-only 匹配会让判分静默删掉学生的精讲。
        with tempfile.TemporaryDirectory() as ws:
            self.assertEqual(add(ws, 2, "q13", "第一版正文", "--title", "旧标题").returncode, 0)
            r = run_nb(ws, "add-entry", "--chapter", "2", "--type", "walkthrough",
                       "--id", "q13", "--title", "第二版标题", stdin="第二版正文")
            self.assertEqual(r.returncode, 0, r.stderr)
            ch = read(ws, "notebook", "ch02.md")
            self.assertEqual(ch.count("## [#q13]"), 1, "同章同 id 同 type 必须原地替换:\n" + ch)
            self.assertIn("第二版正文", ch)
            self.assertNotIn("第一版正文", ch)
            idx = read(ws, "notebook", "index.md")
            self.assertIn("第二版标题", idx)
            self.assertNotIn("旧标题", idx)
            # 同 id 异 type：追加，精讲不丢
            r2 = run_nb(ws, "add-entry", "--chapter", "2", "--type", "feedback",
                        "--id", "q13", "--title", "判分记录", stdin="答对了")
            self.assertEqual(r2.returncode, 0, r2.stderr)
            ch = read(ws, "notebook", "ch02.md")
            self.assertEqual(ch.count("## [#q13]"), 2,
                             "同 id 异 type 必须追加，判分不得覆盖精讲:\n" + ch)
            self.assertIn("第二版正文", ch, "精讲正文必须存活")
            self.assertIn("> 精讲 · ", ch)
            self.assertIn("> 判分 · ", ch)
            lst = json.loads(run_nb(ws, "list", "--json").stdout)
            self.assertEqual(len(lst["entries"]), 2)
            self.assertEqual({e["type"] for e in lst["entries"]}, {"walkthrough", "feedback"})
            # 同 (id,type) 再替换仍幂等
            r3 = run_nb(ws, "add-entry", "--chapter", "2", "--type", "feedback",
                        "--id", "q13", "--title", "判分记录", stdin="部分正确")
            self.assertEqual(r3.returncode, 0, r3.stderr)
            ch = read(ws, "notebook", "ch02.md")
            self.assertEqual(ch.count("## [#q13]"), 2)
            self.assertIn("部分正确", ch)
            self.assertNotIn("答对了", ch)

    def test_duplicate_slug_anchors_match_validator_semantics(self):
        # Codex r4：同 slug 的第二个标题 GitHub 加 -1 后缀——目录与回执必须给实际锚。
        # 口径基准 = validate_workspace._md_anchors（notebook 计算的锚必须落在该集合里）。
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import validate_workspace as V
        with tempfile.TemporaryDirectory() as ws:
            r1 = run_nb(ws, "add-entry", "--chapter", "3", "--type", "walkthrough",
                        "--id", "q1", "--title", "同名", stdin="第一条")
            r2 = run_nb(ws, "add-entry", "--chapter", "3", "--type", "feedback",
                        "--id", "q1", "--title", "同名", stdin="第二条")
            self.assertEqual((r1.returncode, r2.returncode), (0, 0), r1.stderr + r2.stderr)
            idx = read(ws, "notebook", "index.md")
            self.assertIn("(ch03.md#q1-同名)", idx)
            self.assertIn("(ch03.md#q1-同名-1)", idx, "重复 slug 的第二条必须带 -1 后缀:\n" + idx)
            # 回执打印的锚 = 实际锚（-1 后缀）
            self.assertIn("#q1-同名-1", r2.stdout, "回执必须给实际锚: " + r2.stdout)
            anchors = V._md_anchors(os.path.join(ws, "notebook", "ch03.md"))
            self.assertIn("q1-同名", anchors)
            self.assertIn("q1-同名-1", anchors, "notebook 锚必须与 validator 口径一致")

    def test_replace_in_middle_preserves_neighbors_and_order(self):
        with tempfile.TemporaryDirectory() as ws:
            for eid in ("q1", "q2", "q3"):
                self.assertEqual(add(ws, 1, eid, "正文-" + eid).returncode, 0)
            self.assertEqual(add(ws, 1, "q2", "新正文-q2").returncode, 0)
            ch = read(ws, "notebook", "ch01.md")
            self.assertIn("正文-q1", ch)
            self.assertIn("正文-q3", ch)
            self.assertIn("新正文-q2", ch)
            self.assertNotIn("\n正文-q2", ch)
            self.assertTrue(ch.index("[#q1]") < ch.index("[#q2]") < ch.index("[#q3]"),
                            "替换必须原地，不得改变条目顺序:\n" + ch)

    def test_anchor_is_github_style_slug(self):
        with tempfile.TemporaryDirectory() as ws:
            r = add(ws, 1, "q9", "正文", "--title", "Venn 图：判断 (第2章)!")
            self.assertEqual(r.returncode, 0, r.stderr)
            idx = read(ws, "notebook", "index.md")
            # 标点删除、空格转连字符、大写折小写、CJK 保留 —— GitHub 锚点口径
            self.assertIn("(ch01.md#q9-venn-图判断-第2章)", idx)

    def test_cjk_markdown_and_fenced_hash_lines_do_not_break_parsing(self):
        with tempfile.TemporaryDirectory() as ws:
            body = "\n".join([
                "讲解正文（中文 CJK）。",
                "",
                "- 列表项一",
                "- **加粗要点**",
                "",
                "```md",
                "## [#fake] 伪条目（围栏内是内容不是块边界）",
                "## 二级标题示例",
                "```",
                "",
                "收尾一句。",
            ])
            self.assertEqual(add(ws, 1, "q1", body, "--title", "含围栏的讲解").returncode, 0)
            self.assertEqual(add(ws, 1, "q2", "第二条正文").returncode, 0)
            ch = read(ws, "notebook", "ch01.md")
            self.assertIn("## [#fake] 伪条目（围栏内是内容不是块边界）", ch)
            lst = json.loads(run_nb(ws, "list", "--json").stdout)
            self.assertEqual([e["id"] for e in lst["entries"]], ["q1", "q2"],
                             "围栏内的 '## [#' 行被误当条目")
            self.assertNotIn("fake", read(ws, "notebook", "index.md"))


class MistakeMirror(unittest.TestCase):
    def test_mistake_mirrors_entry_and_rebuilds_mistake_index(self):
        with tempfile.TemporaryDirectory() as ws:
            r = add(ws, 2, "q13", "错题完整讲解", "--title", "Venn 图判断", "--mistake")
            self.assertEqual(r.returncode, 0, r.stderr)
            mi = read(ws, "mistakes", "ch02.md")
            self.assertIn("## [#q13] Venn 图判断", mi)
            self.assertIn("错题完整讲解", mi)
            midx = read(ws, "mistakes", "index.md")
            self.assertTrue(midx.startswith("# ❌ 错题本目录"), midx)
            self.assertIn("- [Venn 图判断](ch02.md#q13-venn-图判断)", midx)
            # 笔记本侧照常落盘 + 重建目录
            self.assertIn("[#q13]", read(ws, "notebook", "ch02.md"))
            self.assertIn("(ch02.md#q13-venn-图判断)", read(ws, "notebook", "index.md"))

    def test_status_join_from_study_state_row(self):
        with tempfile.TemporaryDirectory() as ws:
            # 旧词表状态（待复盘）也要经 canon 归一后按语言渲染——三代输入契约
            write_state(ws, mistake_archive=[
                {"id": "q13", "chapter": "2", "note": "看错阴影", "status": "待复盘"}])
            self.assertEqual(add(ws, 2, "q13", "讲解", "--mistake").returncode, 0)
            self.assertEqual(add(ws, 2, "q99", "另一条", "--mistake").returncode, 0)
            midx = read(ws, "mistakes", "index.md")
            joined = [ln for ln in midx.splitlines() if "#q13" in ln]
            self.assertTrue(joined and "｜ 状态：待复盘" in joined[0],
                            "错题目录未按 id 关联 study_state 行的状态:\n" + midx)
            orphan = [ln for ln in midx.splitlines() if "#q99" in ln]
            self.assertTrue(orphan and "状态" not in orphan[0],
                            "无匹配 state 行的条目不应有状态后缀:\n" + midx)

    def test_mistake_index_grouped_by_chapter(self):
        with tempfile.TemporaryDirectory() as ws:
            self.assertEqual(add(ws, 3, "b1", "第三章错题", "--mistake").returncode, 0)
            self.assertEqual(add(ws, 1, "a1", "第一章错题", "--mistake").returncode, 0)
            midx = read(ws, "mistakes", "index.md")
            self.assertIn("## 第 1 章", midx)
            self.assertIn("## 第 3 章", midx)
            self.assertTrue(midx.index("## 第 1 章") < midx.index("## 第 3 章"),
                            "错题目录必须按章号升序分组:\n" + midx)


class LanguagePacks(unittest.TestCase):
    def test_lang_en_headings_and_labels(self):
        with tempfile.TemporaryDirectory() as ws:
            r = add(ws, 2, "q13", "body text", "--title", "Venn diagrams",
                    "--mistake", "--lang", "en")
            self.assertEqual(r.returncode, 0, r.stderr)
            ch = read(ws, "notebook", "ch02.md")
            self.assertIn("> Walkthrough · ", ch)
            self.assertNotIn("精讲", ch)
            idx = read(ws, "notebook", "index.md")
            self.assertTrue(idx.startswith("# 📒 Notebook index"), idx)
            self.assertIn("## Chapter 2", idx)
            midx = read(ws, "mistakes", "index.md")
            self.assertTrue(midx.startswith("# ❌ Mistake-notebook index"), midx)

    def test_lang_defaults_from_study_state(self):
        with tempfile.TemporaryDirectory() as ws:
            write_state(ws, language="en",
                        mistake_archive=[{"id": "q1", "chapter": "1",
                                          "note": "n", "status": "to_review"}])
            self.assertEqual(add(ws, 1, "q1", "body", "--mistake").returncode, 0)
            self.assertTrue(read(ws, "notebook", "index.md").startswith("# 📒 Notebook index"))
            self.assertIn("| Status: to review", read(ws, "mistakes", "index.md"))

    def test_lang_falls_back_to_zh_without_state(self):
        with tempfile.TemporaryDirectory() as ws:
            self.assertEqual(add(ws, 1, "q1", "正文").returncode, 0)
            self.assertTrue(read(ws, "notebook", "index.md").startswith("# 📒 学习笔记目录"))


class RebuildAndList(unittest.TestCase):
    def test_index_rebuild_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as ws:
            self.assertEqual(add(ws, 1, "q1", "正文一", "--title", "条目一").returncode, 0)
            self.assertEqual(add(ws, 2, "q2", "正文二", "--mistake").returncode, 0)
            nb0 = read_bytes(ws, "notebook", "index.md")
            mi0 = read_bytes(ws, "mistakes", "index.md")
            for _ in range(2):                     # 重复 rebuild 必须字节级确定
                self.assertEqual(run_nb(ws, "rebuild").returncode, 0)
                self.assertEqual(read_bytes(ws, "notebook", "index.md"), nb0)
                self.assertEqual(read_bytes(ws, "mistakes", "index.md"), mi0)

    def test_rebuild_from_hand_edited_chapter_file(self):
        with tempfile.TemporaryDirectory() as ws:
            os.makedirs(os.path.join(ws, "notebook"))
            hand = "\n".join([
                "## [#h1] 手写条目一",
                "",
                "> 精讲 · 2026-01-01 09:00",
                "",
                "内容一",
                "",
                "---",
                "",
                "## [#h2] 手写条目二",
                "",
                "正文二（无元行也是合法条目）",
                "",
                "---",
                "",
            ])
            with open(os.path.join(ws, "notebook", "ch03.md"), "w", encoding="utf-8") as f:
                f.write(hand)
            r = run_nb(ws, "rebuild")
            self.assertEqual(r.returncode, 0, r.stderr)
            idx = read(ws, "notebook", "index.md")
            self.assertIn("## 第 3 章", idx)
            self.assertIn("- [手写条目一](ch03.md#h1-手写条目一)", idx)
            self.assertIn("- [手写条目二](ch03.md#h2-手写条目二)", idx)
            # 无错题章文件时 mistakes/index.md 仍确定性生成（仅标题）
            self.assertTrue(read(ws, "mistakes", "index.md").startswith("# ❌ 错题本目录"))
            lst = json.loads(run_nb(ws, "list", "--json").stdout)
            by_id = {e["id"]: e for e in lst["entries"]}
            self.assertEqual(by_id["h1"]["type"], "walkthrough")   # 标签反查回代号
            self.assertEqual(by_id["h1"]["time"], "2026-01-01 09:00")
            self.assertIsNone(by_id["h2"]["type"])

    def test_list_json_inventory_shape(self):
        with tempfile.TemporaryDirectory() as ws:
            self.assertEqual(add(ws, 2, "q13", "正文", "--title", "标题甲",
                                 "--mistake").returncode, 0)
            self.assertEqual(add(ws, 1, "q1", "正文").returncode, 0)
            r = run_nb(ws, "list", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            lst = json.loads(r.stdout)["entries"]
            self.assertEqual([(e["chapter"], e["id"]) for e in lst],
                             [(1, "q1"), (2, "q13")])              # 章序遍历
            e = [x for x in lst if x["id"] == "q13"][0]
            self.assertEqual(e["title"], "标题甲")
            self.assertEqual(e["type"], "walkthrough")
            self.assertTrue(TS_RE.fullmatch(e["time"]), e)
            self.assertEqual(e["file"], "notebook/ch02.md")
            self.assertEqual(e["anchor"], "q13-标题甲")
            self.assertTrue(e["mistake"])
            self.assertFalse([x for x in lst if x["id"] == "q1"][0]["mistake"])

    def test_list_plain_output_smoke(self):
        with tempfile.TemporaryDirectory() as ws:
            self.assertEqual(run_nb(ws, "list").returncode, 0)     # 空笔记本也 0
            self.assertEqual(add(ws, 1, "q1", "正文").returncode, 0)
            r = run_nb(ws, "list")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("[#q1]", r.stdout)


class UsageGuards(unittest.TestCase):
    def test_empty_body_rejected_exit2(self):
        with tempfile.TemporaryDirectory() as ws:
            r = add(ws, 2, "q13", "   \n\n")
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertFalse(os.path.exists(os.path.join(ws, "notebook")),
                             "空正文不得留下任何落盘痕迹")

    def test_bare_entry_marker_in_body_rejected_exit2(self):
        with tempfile.TemporaryDirectory() as ws:
            r = add(ws, 2, "q13", "正文\n\n## [#x] 裸露标记行\n")
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertFalse(os.path.exists(os.path.join(ws, "notebook")))

    def test_unclosed_fence_in_body_rejected_exit2(self):
        with tempfile.TemporaryDirectory() as ws:
            r = add(ws, 2, "q13", "```python\nprint(1)\n")
            self.assertEqual(r.returncode, 2, r.stderr)

    def test_bad_chapter_and_bad_id_exit2(self):
        with tempfile.TemporaryDirectory() as ws:
            self.assertEqual(add(ws, 0, "q1", "正文").returncode, 2)
            for bad in ("a b", "x]y", "有#号", ""):
                r = add(ws, 1, bad, "正文")
                self.assertEqual(r.returncode, 2, "--id %r 应拒收" % bad)

    def test_missing_workspace_exit2(self):
        r = run_nb(os.path.join(tempfile.gettempdir(), "no_such_ws_xyz"),
                   "add-entry", "--chapter", "1", "--type", "walkthrough",
                   "--id", "q1", stdin="正文")
        self.assertEqual(r.returncode, 2, r.stderr)


class ContainmentGuards(unittest.TestCase):
    def _symlink(self, src, dst):
        try:
            os.symlink(src, dst)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("no symlink privilege")

    def test_symlinked_chapter_file_refused_exit1(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = os.path.join(tmp, "ws")
            os.makedirs(os.path.join(ws, "notebook"))
            outside = os.path.join(tmp, "outside.md")
            with open(outside, "w", encoding="utf-8") as f:
                f.write("外部文件原文")
            self._symlink(outside, os.path.join(ws, "notebook", "ch02.md"))
            r = add(ws, 2, "q13", "正文")
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("符号链接", r.stderr)
            self.assertEqual(read(outside), "外部文件原文", "工作区外文件被改写")

    def test_symlinked_tmp_refused_exit1(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = os.path.join(tmp, "ws")
            os.makedirs(os.path.join(ws, "notebook"))
            outside = os.path.join(tmp, "outside.md")
            with open(outside, "w", encoding="utf-8") as f:
                f.write("外部文件原文")
            self._symlink(outside, os.path.join(ws, "notebook", "ch02.md.tmp"))
            r = add(ws, 2, "q13", "正文")
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertEqual(read(outside), "外部文件原文", "工作区外文件被改写")

    def test_broken_state_json_fails_loud_exit1(self):
        with tempfile.TemporaryDirectory() as ws:
            with open(os.path.join(ws, "study_state.json"), "w", encoding="utf-8") as f:
                f.write("{not json")
            r = add(ws, 1, "q1", "正文")
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("study_state.json", r.stderr)


if __name__ == "__main__":
    unittest.main()
