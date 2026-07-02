# -*- coding: utf-8 -*-
"""Tests for the P0-V2 universal visual index (scripts/build_visual_index.py + the three official tools).

Recall-first is the whole point: a page with an embedded image but NO caption keywords must still be
flagged; detector vocabulary must be multi-domain (circuit/flowchart/waveform …), never bound to one
subject. Pure stdlib; PDF backends are faked — no real pypdf/PyMuPDF needed, no network, no LLM."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
import build_visual_index as BVI          # noqa: E402
import list_image_questions as LIQ        # noqa: E402
import list_figure_pages as LFP           # noqa: E402
import show_question_assets as SQA        # noqa: E402

PNG = (b"\x89PNG\r\n\x1a\n" + b"0" * 60)  # tiny fake png bytes (content irrelevant for these tests)


class FakeBackend(object):
    """Injectable stand-in for the optional PDF backends — tests never import real PDF libs."""

    def __init__(self, texts_by_name=None, media_by_name=None, text=True, media=True, render=True,
                 render_fail_pages=()):
        self.texts = texts_by_name or {}
        self.media = media_by_name or {}
        self._text, self._media, self._render = text, media, render
        self.render_fail_pages = set(render_fail_pages)   # 0-based page indexes whose render returns None
        self.name = "fake"

    def can_text(self):
        return self._text

    def can_media(self):
        return self._media

    def can_render(self):
        return self._render

    def pages_text(self, pdf_path):
        return self.texts[os.path.basename(pdf_path)]

    def pages_media(self, pdf_path):
        v = self.media.get(os.path.basename(pdf_path))
        if isinstance(v, Exception):
            raise v
        return v

    def render_page_png(self, pdf_path, page_index):
        if not self._render or page_index in self.render_fail_pages:
            return None
        return PNG


def _mk_materials(d, names):
    os.makedirs(os.path.join(d, "lectures"), exist_ok=True)
    for n in names:
        with open(os.path.join(d, "lectures", n), "wb") as f:
            f.write(b"%PDF-fake")


def _mk_workspace(tmp):
    """Copy the known-valid P0A fixture workspace and extend its bank with test questions."""
    ws = os.path.join(tmp, "ws")
    shutil.copytree(os.path.join(ROOT, "tests", "fixtures", "valid_workspace_assets"), ws)
    bank_path = os.path.join(ws, "references", "quiz_bank.json")
    bank = json.load(open(bank_path, encoding="utf-8"))
    bank += [
        {"id": "plain_1", "chapter": 1, "type": "subjective", "question": "定义栈。",
         "answer_keywords": ["LIFO"], "source": "material", "ai_generated": False},
        {"id": "suspect_1", "chapter": 1, "type": "subjective", "question": "根据图示求输出。",
         "source": "material", "ai_generated": False,
         "source_file": "lectures/ch01.pdf", "source_pages": [2]},          # p.2 = structural-only visual
        {"id": "ansfig_1", "chapter": 2, "type": "subjective", "question": "证明该恒等式。",
         "answer": "见解答", "source": "material", "ai_generated": False,
         "source_file": "lectures/ch02.pdf", "source_pages": [1],
         "answer_source_file": "lectures/ch02.pdf", "answer_source_pages": [3]},
    ]
    json.dump(bank, open(bank_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return ws


def _default_backend():
    return FakeBackend(
        texts_by_name={
            "ch01.pdf": ["纯文字页，没有任何图。", "这一页文字也没提到图。", "Figure 2: sample path 如图所示。"],
            "ch02.pdf": ["纯文字定义页。", "纯文字。", "解答：电路原理图与 waveform 波形对比。"],
        },
        media_by_name={
            "ch01.pdf": [(0, 0), (1, 0), (0, 0)],     # p.2 embeds an image but has NO keywords → structural-only
            "ch02.pdf": [(0, 0), (0, 0), (0, 8)],     # p.3 many vector drawings
        })


def _build(tmp, apply=False, backend=None, materials=True):
    ws = _mk_workspace(tmp)
    mat = os.path.join(tmp, "mat")
    if materials:
        _mk_materials(mat, ["ch01.pdf", "ch02.pdf"])
    argv = ["--workspace", ws] + (["--materials", mat] if materials else []) + (["--apply"] if apply else [])
    rc = BVI.run(argv, backend=backend or _default_backend())
    return ws, mat, rc


def _load(ws, name):
    return json.load(open(os.path.join(ws, "references", name), encoding="utf-8"))


class ClassifyPage(unittest.TestCase):
    def test_structural_only_page_is_visual(self):
        # THE recall case: embedded image, zero caption keywords — keyword-only detection missed these
        c = BVI.classify_page("这页文字完全没提到任何图表词。", images=1, drawings=0)
        self.assertTrue(c["has_visual"])
        self.assertTrue(c["signals"]["structural"])
        self.assertEqual(c["visual_kinds"], [])        # no words → structural-only, still flagged

    def test_many_drawings_is_visual(self):
        self.assertTrue(BVI.classify_page("text", images=0, drawings=9)["has_visual"])
        self.assertFalse(BVI.classify_page("text", images=0, drawings=2)["has_visual"])  # underlines ≠ figure

    def test_multi_domain_keywords_not_subject_bound(self):
        for text, kind in [("电路如原理图所示", "circuit"), ("见流程图", "flowchart"),
                           ("波形如下", "graph"), ("scatter plot of samples", "plot"),
                           ("运行结果截图", "screenshot"), ("文氏图表示事件", "geometry")]:
            c = BVI.classify_page(text)
            self.assertTrue(c["has_visual"], text)
            self.assertIn(kind, c["visual_kinds"], text)

    def test_figref_and_axis_signals(self):
        self.assertTrue(BVI.classify_page("as shown in Figure 3")["signals"]["figref"])
        self.assertTrue(BVI.classify_page("横轴表示时间")["signals"]["axis"])

    def test_plain_text_not_visual(self):
        self.assertFalse(BVI.classify_page("纯定义叙述，无任何视觉内容。")["has_visual"])


class BuildIndices(unittest.TestCase):
    def test_end_to_end_indices_and_suspects(self):
        tmp = tempfile.mkdtemp()
        ws, _mat, rc = _build(tmp)
        self.assertEqual(rc, 0)
        fig = _load(ws, "figure_page_index.json")
        qidx = _load(ws, "image_question_index.json")
        ch01 = fig["files"]["lectures/ch01.pdf"]
        self.assertEqual(ch01["pages"], 3)
        self.assertEqual([p["page"] for p in ch01["visual_pages"]], [2, 3])   # structural-only p.2 caught
        # suspect: suspect_1's source page 2 is visual, item unlabeled, no prompt asset
        self.assertEqual([s["id"] for s in qidx["suspects"]], ["suspect_1"])
        # answer-page visual cross-check for ansfig_1 (ch02 p.3 = drawings)
        rec = {r["id"]: r for r in qidx["questions"]}
        self.assertEqual(rec["ansfig_1"]["answer_pages_visual"], [3])
        self.assertTrue(rec["ansfig_1"]["has_official_answer"])
        self.assertFalse(rec["plain_1"]["requires_assets"])
        # per-chapter rollup exists and counts the suspect in its chapter
        self.assertEqual(qidx["per_chapter"]["1"]["suspects"], 1)

    def test_apply_attaches_page_asset_and_keeps_validator_green(self):
        tmp = tempfile.mkdtemp()
        ws, _mat, rc = _build(tmp, apply=True)
        self.assertEqual(rc, 0)
        bank = json.load(open(os.path.join(ws, "references", "quiz_bank.json"), encoding="utf-8"))
        q = next(x for x in bank if x["id"] == "suspect_1")
        self.assertIs(q["maybe_requires_assets"], True)
        a = q["assets"][0]
        self.assertEqual(a["role"], "question_context")
        self.assertEqual(a["type"], "page_image")
        self.assertNotIn("\\", a["path"])                                     # POSIX relative path
        self.assertTrue(os.path.isfile(os.path.join(ws, a["path"])))          # png actually written
        self.assertTrue(os.path.isfile(os.path.join(ws, "references", "quiz_bank.json.bak")))
        self.assertEqual(_load(ws, "image_question_index.json")["suspects"], [])   # re-indexed post-apply
        # the applied workspace must still pass the fail-closed validator (real CLI run)
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "validate_workspace.py"), ws],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_apply_without_render_backend_exits_3(self):
        tmp = tempfile.mkdtemp()
        be = _default_backend()
        be._render = False
        with self.assertRaises(SystemExit) as cm:
            _build(tmp, apply=True, backend=be)
        self.assertEqual(cm.exception.code, 3)

    def test_pdfs_without_text_backend_exit_3(self):
        tmp = tempfile.mkdtemp()
        with self.assertRaises(SystemExit) as cm:
            _build(tmp, backend=FakeBackend(text=False))
        self.assertEqual(cm.exception.code, 3)

    def test_no_materials_builds_question_index_with_warning(self):
        tmp = tempfile.mkdtemp()
        ws, _mat, rc = _build(tmp, materials=False)
        self.assertEqual(rc, 0)
        qidx = _load(ws, "image_question_index.json")
        self.assertEqual(qidx["suspects"], [])                                # recall net off, honestly
        self.assertTrue(any("no_materials" in w for w in qidx["warnings"]))

    def test_no_media_backend_degrades_with_warning(self):
        tmp = tempfile.mkdtemp()
        be = _default_backend()
        be._media = False
        ws, _mat, rc = _build(tmp, backend=be)
        self.assertEqual(rc, 0)
        fig = _load(ws, "figure_page_index.json")
        self.assertFalse(fig["media_signals"])
        self.assertTrue(any("no_media_backend" in w for w in fig["warnings"]))
        pages = [p["page"] for p in fig["files"]["lectures/ch01.pdf"]["visual_pages"]]
        self.assertNotIn(2, pages)                                            # structural page honestly lost

    def test_missing_workspace_or_bad_bank_exit_2(self):
        with self.assertRaises(SystemExit) as cm:
            BVI.run(["--workspace", os.path.join(tempfile.mkdtemp(), "nope")], backend=_default_backend())
        self.assertEqual(cm.exception.code, 2)


class OfficialTools(unittest.TestCase):
    def _capture(self, fn, argv):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = fn(argv)
        return rc, buf.getvalue()

    def test_list_image_questions_cross_check(self):
        tmp = tempfile.mkdtemp()
        ws, _m, _rc = _build(tmp)
        rc, out = self._capture(LIQ.run, ["--workspace", ws, "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        c1 = data["per_chapter"]["1"]
        self.assertEqual(c1["suspects"], 1)                       # the probe: chapter × count × visual linkage
        self.assertGreaterEqual(c1["questions"], 3)
        self.assertTrue(data["index_present"])

    def test_list_image_questions_warns_without_index(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        rc, out = self._capture(LIQ.run, ["--workspace", ws])
        self.assertEqual(rc, 0)
        self.assertIn("尚未构建", out)                            # suspects=0 must be flagged untrustworthy

    def test_list_figure_pages_kind_filter(self):
        tmp = tempfile.mkdtemp()
        ws, _m, _rc = _build(tmp)
        rc, out = self._capture(LFP.run, ["--workspace", ws, "--kind", "circuit", "--json"])
        self.assertEqual(rc, 0)
        files = json.loads(out)["files"]
        self.assertIn("lectures/ch02.pdf", files)                 # 电路/原理图 page
        self.assertNotIn("lectures/ch01.pdf", files)
        with self.assertRaises(SystemExit) as cm:
            LFP.run(["--workspace", tempfile.mkdtemp()])
        self.assertEqual(cm.exception.code, 2)                    # index missing → 2

    def test_show_question_assets_prompt_first_and_fail_closed(self):
        tmp = tempfile.mkdtemp()
        ws, _m, _rc = _build(tmp, apply=True)
        rc, out = self._capture(SQA.run, ["--workspace", ws, "--id", "suspect_1"])
        self.assertEqual(rc, 0)
        self.assertIn("![题面图 / question-side asset:", out)     # canonical label (docs/file-format §4)
        self.assertIn("references/assets/", out)
        self.assertNotIn("\\", out.split("(")[1].split(")")[0])   # renderable POSIX path
        # a visual item whose asset file is deleted → fail-closed exit 1
        bank = json.load(open(os.path.join(ws, "references", "quiz_bank.json"), encoding="utf-8"))
        q = next(x for x in bank if x["id"] == "suspect_1")
        os.remove(os.path.join(ws, q["assets"][0]["path"]))
        with self.assertRaises(SystemExit) as cm:
            SQA.run(["--workspace", ws, "--id", "suspect_1"])
        self.assertEqual(cm.exception.code, 1)
        with self.assertRaises(SystemExit) as cm2:
            SQA.run(["--workspace", ws, "--id", "no_such_id"])
        self.assertEqual(cm2.exception.code, 2)

    def test_show_question_assets_answer_side_only_on_demand(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        bank = json.load(open(bank_path, encoding="utf-8"))
        os.makedirs(os.path.join(ws, "references", "assets"), exist_ok=True)
        for n in ("p.png", "s.png"):
            with open(os.path.join(ws, "references", "assets", n), "wb") as f:
                f.write(PNG)
        bank.append({"id": "both_1", "chapter": 1, "type": "subjective", "question": "看图作答。",
                     "source": "material", "ai_generated": False, "requires_assets": True,
                     "assets": [{"path": "references/assets/p.png", "role": "question_context",
                                 "type": "page_image"},
                                {"path": "references/assets/s.png", "role": "worked_solution",
                                 "type": "page_image"}]})
        json.dump(bank, open(bank_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        rc, out = self._capture(SQA.run, ["--workspace", ws, "--id", "both_1"])
        self.assertIn("p.png", out)
        self.assertNotIn("s.png", out)                            # answer image NOT shown by default
        rc, out2 = self._capture(SQA.run, ["--workspace", ws, "--id", "both_1", "--with-answer"])
        self.assertIn("s.png", out2)
        self.assertLess(out2.index("p.png"), out2.index("s.png"))  # prompt strictly before answer

    # ---- regression guards for Codex round-1 (6 findings) ----

    def test_show_fails_when_any_prompt_asset_unusable(self):
        # strict-ALL: a visual item with TWO prompt assets must fail-close if ONE is missing —
        # never show a partial prompt (figure without its table) as if complete
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        bank = json.load(open(bank_path, encoding="utf-8"))
        os.makedirs(os.path.join(ws, "references", "assets"), exist_ok=True)
        with open(os.path.join(ws, "references", "assets", "fig.png"), "wb") as f:
            f.write(PNG)
        bank.append({"id": "two_asset", "chapter": 1, "type": "subjective", "question": "看图和表作答。",
                     "source": "material", "ai_generated": False, "requires_assets": True,
                     "assets": [{"path": "references/assets/fig.png", "role": "figure", "type": "page_image"},
                                {"path": "references/assets/tbl.png", "role": "table", "type": "table_image"}]})
        json.dump(bank, open(bank_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        with self.assertRaises(SystemExit) as cm:                 # tbl.png missing → partial prompt → exit 1
            SQA.run(["--workspace", ws, "--id", "two_asset"])
        self.assertEqual(cm.exception.code, 1)

    def test_show_gates_stub_and_page_reference(self):
        # stub/page_reference items share the runtime contract: no displayable prompt asset → exit 1
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        bank = json.load(open(bank_path, encoding="utf-8"))
        bank.append({"id": "pageref_1", "chapter": 1, "type": "subjective",
                     "question": "见讲义第 2 页的图示题。", "source": "material", "ai_generated": False,
                     "question_text_status": "page_reference",
                     "source_file": "lectures/ch01.pdf", "source_pages": [2]})
        json.dump(bank, open(bank_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        with self.assertRaises(SystemExit) as cm:
            SQA.run(["--workspace", ws, "--id", "pageref_1"])
        self.assertEqual(cm.exception.code, 1)                    # fail-closed, with the page pointer on stderr

    def test_apply_attaches_every_visual_page(self):
        # a suspect spanning MULTIPLE visual pages gets ALL of them attached, not just the first
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        bank = json.load(open(bank_path, encoding="utf-8"))
        bank.append({"id": "multi_page", "chapter": 1, "type": "subjective", "question": "跨页图题。",
                     "source": "material", "ai_generated": False,
                     "source_file": "lectures/ch01.pdf", "source_pages": [2, 3]})   # both visual pages
        json.dump(bank, open(bank_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf", "ch02.pdf"])
        rc = BVI.run(["--workspace", ws, "--materials", mat, "--apply"], backend=_default_backend())
        self.assertEqual(rc, 0)
        bank2 = json.load(open(bank_path, encoding="utf-8"))
        q = next(x for x in bank2 if x["id"] == "multi_page")
        self.assertEqual(len(q["assets"]), 2)                     # p.2 AND p.3 attached
        pages = sorted(a["path"] for a in q["assets"])
        self.assertTrue(pages[0].endswith("_p2.png") and pages[1].endswith("_p3.png"))

    def test_visual_hits_exact_match_beats_duplicate_basename(self):
        fig = {"lectures/ch01.pdf": {"pages": 5, "visual": {2: {}}},
               "homework/ch01.pdf": {"pages": 5, "visual": {5: {}}}}
        # exact relative path → ONLY that file's pages considered
        self.assertEqual(BVI._visual_hits(fig, "lectures/ch01.pdf", [2, 5]), [2])
        # bare basename (ambiguous) → UNION across duplicates, recall-first
        self.assertEqual(BVI._visual_hits(fig, "ch01.pdf", [2, 5]), [2, 5])

    def test_apply_skips_ambiguous_duplicate_basename(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        bank = json.load(open(bank_path, encoding="utf-8"))
        for q in bank:                                            # make suspect_1's source ambiguous
            if q["id"] == "suspect_1":
                q["source_file"] = "ch01.pdf"
        json.dump(bank, open(bank_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf", "ch02.pdf"])
        os.makedirs(os.path.join(mat, "homework"), exist_ok=True)
        with open(os.path.join(mat, "homework", "ch01.pdf"), "wb") as f:   # duplicate basename
            f.write(b"%PDF-fake")
        be = _default_backend()
        be.texts["ch01.pdf"] = be.texts["ch01.pdf"]               # both resolve by basename in the fake
        rc = BVI.run(["--workspace", ws, "--materials", mat, "--apply"], backend=be)
        self.assertEqual(rc, 0)
        qidx = _load(ws, "image_question_index.json")
        self.assertTrue(any(w.startswith("apply_skip_ambiguous") for w in qidx["warnings"]))
        bank2 = json.load(open(bank_path, encoding="utf-8"))
        q = next(x for x in bank2 if x["id"] == "suspect_1")
        self.assertNotIn("maybe_requires_assets", q)              # NOT flagged against the wrong file

    def test_list_reports_recall_net_state(self):
        # index built WITHOUT --materials: suspects=0 must be flagged untrustworthy, not silently trusted
        tmp = tempfile.mkdtemp()
        ws, _m, _rc = _build(tmp, materials=False)
        rc, out = self._capture(LIQ.run, ["--workspace", ws, "--json"])
        data = json.loads(out)
        self.assertTrue(data["index_present"])
        self.assertFalse(data["recall_net"])
        rc, out2 = self._capture(LIQ.run, ["--workspace", ws])
        self.assertIn("疑漏口径=0 不可信", out2)
        # with materials → recall_net true
        tmp2 = tempfile.mkdtemp()
        ws2, _m2, _rc2 = _build(tmp2)
        rc, out3 = self._capture(LIQ.run, ["--workspace", ws2, "--json"])
        self.assertTrue(json.loads(out3)["recall_net"])

    # ---- regression guards for Codex round-2 (7 findings) ----

    def _ws_with(self, tmp, extra):
        ws = _mk_workspace(tmp)
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        bank = json.load(open(bank_path, encoding="utf-8"))
        bank += extra
        json.dump(bank, open(bank_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return ws, bank_path

    def test_apply_partial_render_failure_attaches_nothing(self):
        # ALL-or-nothing: one page of a multi-page suspect fails to render → NO assets attached, NO flag
        tmp = tempfile.mkdtemp()
        ws, bank_path = self._ws_with(tmp, [
            {"id": "multi_fail", "chapter": 1, "type": "subjective", "question": "跨页图题。",
             "source": "material", "ai_generated": False,
             "source_file": "lectures/ch01.pdf", "source_pages": [2, 3]}])
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf", "ch02.pdf"])
        be = _default_backend()
        be.render_fail_pages = {2}                    # page 3 (0-based idx 2) fails
        rc = BVI.run(["--workspace", ws, "--materials", mat, "--apply"], backend=be)
        self.assertEqual(rc, 0)
        q = next(x for x in json.load(open(bank_path, encoding="utf-8")) if x["id"] == "multi_fail")
        self.assertNotIn("maybe_requires_assets", q)  # not flagged with a partial prompt
        self.assertFalse(q.get("assets"))             # nothing attached
        qidx = _load(ws, "image_question_index.json")
        self.assertIn("multi_fail", [s["id"] for s in qidx["suspects"]])   # stays a visible suspect

    def test_apply_normalizes_null_assets(self):
        tmp = tempfile.mkdtemp()
        ws, bank_path = self._ws_with(tmp, [
            {"id": "null_assets", "chapter": 1, "type": "subjective", "question": "图题。",
             "source": "material", "ai_generated": False, "assets": None,
             "source_file": "lectures/ch01.pdf", "source_pages": [2]}])
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf", "ch02.pdf"])
        rc = BVI.run(["--workspace", ws, "--materials", mat, "--apply"], backend=_default_backend())
        self.assertEqual(rc, 0)                       # no AttributeError on "assets": null
        q = next(x for x in json.load(open(bank_path, encoding="utf-8")) if x["id"] == "null_assets")
        self.assertIs(q["maybe_requires_assets"], True)
        self.assertEqual(len(q["assets"]), 1)

    def test_apply_fallback_prunes_leftover_workspace(self):
        # a prior generated workspace inside --materials holds a same-basename PDF: the scan prunes it,
        # so the apply fallback must prune it too — no false apply_skip_ambiguous
        tmp = tempfile.mkdtemp()
        ws, bank_path = self._ws_with(tmp, [
            {"id": "bare_name", "chapter": 1, "type": "subjective", "question": "图题。",
             "source": "material", "ai_generated": False,
             "source_file": "ch01.pdf", "source_pages": [2]}])   # bare basename
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf", "ch02.pdf"])
        old = os.path.join(mat, "old_ws")                        # leftover workspace signature
        os.makedirs(os.path.join(old, "references", "wiki"))
        open(os.path.join(old, "references", "wiki", "ch1.md"), "w").write("x")
        with open(os.path.join(old, "ch01.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        rc = BVI.run(["--workspace", ws, "--materials", mat, "--apply"], backend=_default_backend())
        self.assertEqual(rc, 0)
        qidx = _load(ws, "image_question_index.json")
        self.assertFalse(any(w.startswith("apply_skip_ambiguous") for w in qidx["warnings"]))
        q = next(x for x in json.load(open(bank_path, encoding="utf-8")) if x["id"] == "bare_name")
        self.assertIs(q.get("maybe_requires_assets"), True)      # resolved against the real lecture PDF

    def test_media_failure_degrades_single_file_only(self):
        tmp = tempfile.mkdtemp()
        be = _default_backend()
        be.media["ch02.pdf"] = RuntimeError("fitz cannot open")
        ws, _m, rc = _build(tmp, backend=be)
        self.assertEqual(rc, 0)                                  # build survives
        fig = _load(ws, "figure_page_index.json")
        self.assertTrue(any(w.startswith("media_failed") for w in fig["warnings"]))
        ch01 = [p["page"] for p in fig["files"]["lectures/ch01.pdf"]["visual_pages"]]
        self.assertIn(2, ch01)                                   # other file's structural signal intact
        ch02 = [p["page"] for p in fig["files"]["lectures/ch02.pdf"]["visual_pages"]]
        self.assertIn(3, ch02)                                   # keyword signal still works text-only

    def test_apply_rejects_symlinked_asset_root_escaping_ws(self):
        tmp = tempfile.mkdtemp()
        ws, _bank = self._ws_with(tmp, [
            {"id": "s1", "chapter": 1, "type": "subjective", "question": "图题。",
             "source": "material", "ai_generated": False,
             "source_file": "lectures/ch01.pdf", "source_pages": [2]}])
        outside = os.path.join(tmp, "outside_assets")
        os.makedirs(outside)
        link = os.path.join(ws, "references", "assets_link")
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("no symlink privilege on this system")
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf", "ch02.pdf"])
        with self.assertRaises(SystemExit) as cm:
            BVI.run(["--workspace", ws, "--materials", mat, "--apply", "--asset-root", link],
                    backend=_default_backend())
        self.assertEqual(cm.exception.code, 2)                   # realpath containment refuses the escape

    def test_apply_rejects_unsafe_source_file(self):
        # after round-5, a QUALIFIED escaping path never even becomes a suspect (no basename stand-in) —
        # it surfaces as source_pdf_not_indexed instead. The apply-side guard stays as defense-in-depth:
        # exercise it directly with a hand-made suspect.
        tmp = tempfile.mkdtemp()
        ws, bank_path = self._ws_with(tmp, [
            {"id": "esc_1", "chapter": 1, "type": "subjective", "question": "图题。",
             "source": "material", "ai_generated": False,
             "source_file": "sub/../../outside/ch01.pdf", "source_pages": [2]}])
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf", "ch02.pdf"])
        rc = BVI.run(["--workspace", ws, "--materials", mat, "--apply"], backend=_default_backend())
        self.assertEqual(rc, 0)
        qidx = _load(ws, "image_question_index.json")
        self.assertTrue(any(w.startswith("source_pdf_not_indexed") for w in qidx["warnings"]))
        q = next(x for x in json.load(open(bank_path, encoding="utf-8")) if x["id"] == "esc_1")
        self.assertNotIn("maybe_requires_assets", q)             # never attach from outside the materials
        # defense-in-depth: the apply guard itself still refuses an unsafe source
        bank = json.load(open(bank_path, encoding="utf-8"))
        warnings = []
        applied = BVI.apply_suspects(ws, mat, bank, [{"id": "esc_1", "visual_pages": [2]}],
                                     _default_backend(), os.path.join(ws, "references", "assets"), warnings)
        self.assertEqual(applied, 0)
        self.assertTrue(any(w.startswith("apply_skip_unsafe_source") for w in warnings))

    def test_realbackend_falls_back_to_fitz_when_pypdf_fails(self):
        rb = BVI.RealBackend.__new__(BVI.RealBackend)            # build without importing real libs

        class _BadPypdf(object):
            class PdfReader(object):
                def __init__(self, path):
                    raise ValueError("pypdf cannot parse this PDF")

        class _FitzDoc(object):
            page_count = 2

            def __getitem__(self, i):
                class _P(object):
                    def get_text(self):
                        return "fitz text %d" % i
                return _P()

            def close(self):
                pass

        class _Fitz(object):
            @staticmethod
            def open(path):
                return _FitzDoc()

        rb._pypdf, rb._fitz, rb._pdfium = _BadPypdf(), _Fitz(), None
        self.assertEqual(rb.pages_text("x.pdf"), ["fitz text 0", "fitz text 1"])   # fallback, not a skip

    # ---- regression guards for Codex round-3 (7 findings) ----

    def test_keyword_matching_uses_token_boundaries(self):
        # 'paragraph' must not hit 'graph'; 'comfortable' must not hit 'table' (English = token match)
        c = BVI.classify_page("This paragraph is comfortable to read and workable in maps of meaning.")
        self.assertFalse(c["has_visual"])
        self.assertTrue(BVI.classify_page("see the graph below")["has_visual"])   # real token still hits
        self.assertTrue(BVI.classify_page("统计图见下")["has_visual"])            # CJK substring preserved

    def test_ai_generated_answer_not_official(self):
        tmp = tempfile.mkdtemp()
        ws, _bank = self._ws_with(tmp, [
            {"id": "ai_ans", "chapter": 1, "type": "subjective", "question": "AI 补题。",
             "answer": "AI 写的答案", "source": "ai_generated", "ai_generated": True}])
        BVI.run(["--workspace", ws], backend=_default_backend())
        rec = {r["id"]: r for r in _load(ws, "image_question_index.json")["questions"]}
        self.assertFalse(rec["ai_ans"]["has_official_answer"])    # AI answer ≠ official answer
        self.assertTrue(rec["ansfig_1"]["has_official_answer"])   # material answer still counts

    def test_phase_tagged_bank_grouped_by_phase(self):
        tmp = tempfile.mkdtemp()
        ws, _bank = self._ws_with(tmp, [
            {"id": "ph_only", "phase": 3, "type": "subjective", "question": "phase 题。",
             "source": "material", "ai_generated": False}])
        BVI.run(["--workspace", ws], backend=_default_backend())
        qidx = _load(ws, "image_question_index.json")
        self.assertIn("3", qidx["per_chapter"])                   # phase fallback, not lumped under '?'
        rc, out = self._capture(LIQ.run, ["--workspace", ws, "--chapter", "3", "--json"])
        self.assertEqual(json.loads(out)["per_chapter"]["3"]["questions"], 1)

    def test_applied_asset_names_do_not_collide(self):
        # ids differing only in sanitized characters must yield DISTINCT screenshot files
        tmp = tempfile.mkdtemp()
        ws, bank_path = self._ws_with(tmp, [
            {"id": "q:a", "chapter": 1, "type": "subjective", "question": "图1。", "source": "material",
             "ai_generated": False, "source_file": "lectures/ch01.pdf", "source_pages": [2]},
            {"id": "q/a", "chapter": 1, "type": "subjective", "question": "图2。", "source": "material",
             "ai_generated": False, "source_file": "lectures/ch01.pdf", "source_pages": [2]}])
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf", "ch02.pdf"])
        rc = BVI.run(["--workspace", ws, "--materials", mat, "--apply"], backend=_default_backend())
        self.assertEqual(rc, 0)
        bank = json.load(open(bank_path, encoding="utf-8"))
        paths = [q["assets"][0]["path"] for q in bank if q.get("id") in ("q:a", "q/a")]
        self.assertEqual(len(paths), 2)
        self.assertNotEqual(paths[0], paths[1])                   # sha1-suffixed names never collide

    def test_broken_prompt_asset_does_not_suppress_suspect(self):
        # a declared-but-missing prompt asset can't be displayed → item must STILL be a suspect
        tmp = tempfile.mkdtemp()
        ws, _bank = self._ws_with(tmp, [
            {"id": "stale_asset", "chapter": 1, "type": "subjective", "question": "图题。",
             "source": "material", "ai_generated": False,
             "source_file": "lectures/ch01.pdf", "source_pages": [2],
             "assets": [{"path": "references/assets/gone.png", "role": "figure", "type": "page_image"}]}])
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf", "ch02.pdf"])
        BVI.run(["--workspace", ws, "--materials", mat], backend=_default_backend())
        qidx = _load(ws, "image_question_index.json")
        self.assertIn("stale_asset", [s["id"] for s in qidx["suspects"]])

    def test_url_source_file_rejected_in_apply(self):
        # a URL provenance is qualified → no basename stand-in, surfaces as source_pdf_not_indexed and
        # is never flagged; the apply guard additionally refuses :// outright (defense-in-depth)
        tmp = tempfile.mkdtemp()
        ws, bank_path = self._ws_with(tmp, [
            {"id": "url_src", "chapter": 1, "type": "subjective", "question": "图题。",
             "source": "material", "ai_generated": False,
             "source_file": "https://example.com/ch01.pdf", "source_pages": [2]}])
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf", "ch02.pdf"])
        rc = BVI.run(["--workspace", ws, "--materials", mat, "--apply"], backend=_default_backend())
        self.assertEqual(rc, 0)
        qidx = _load(ws, "image_question_index.json")
        self.assertTrue(any(w.startswith("source_pdf_not_indexed") and "example.com" in w
                            for w in qidx["warnings"]))
        q = next(x for x in json.load(open(bank_path, encoding="utf-8")) if x["id"] == "url_src")
        self.assertNotIn("maybe_requires_assets", q)              # never lends a local screenshot to a URL
        bank = json.load(open(bank_path, encoding="utf-8"))
        warnings = []
        applied = BVI.apply_suspects(ws, mat, bank, [{"id": "url_src", "visual_pages": [2]}],
                                     _default_backend(), os.path.join(ws, "references", "assets"), warnings)
        self.assertEqual(applied, 0)
        self.assertTrue(any(w.startswith("apply_skip_unsafe_source") for w in warnings))

    def test_recall_net_degraded_by_failed_pdf_scan(self):
        tmp = tempfile.mkdtemp()
        be = _default_backend()
        del be.texts["ch02.pdf"]                                  # pages_text raises → pdf_text_failed
        ws, _m, rc = _build(tmp, backend=be)
        self.assertEqual(rc, 0)
        rcode, out = self._capture(LIQ.run, ["--workspace", ws, "--json"])
        data = json.loads(out)
        self.assertFalse(data["recall_net"])                      # a wholly-unscanned PDF → untrustworthy
        self.assertIn("ch02", data["recall_note"])

    # ---- regression guards for Codex round-4 (4 findings) ----

    def test_unindexed_source_pdf_warned_and_untrusted(self):
        # a question whose provenance PDF was never scanned is UNVERIFIABLE, not silently non-visual
        tmp = tempfile.mkdtemp()
        ws, _bank = self._ws_with(tmp, [
            {"id": "lost_src", "chapter": 9, "type": "subjective", "question": "图题。",
             "source": "material", "ai_generated": False,
             "source_file": "lectures/missing_ch09.pdf", "source_pages": [1]}])
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf", "ch02.pdf"])
        BVI.run(["--workspace", ws, "--materials", mat], backend=_default_backend())
        qidx = _load(ws, "image_question_index.json")
        self.assertTrue(any(w.startswith("source_pdf_not_indexed") and "missing_ch09" in w
                            for w in qidx["warnings"]))
        rc, out = self._capture(LIQ.run, ["--workspace", ws, "--json"])
        data = json.loads(out)
        self.assertFalse(data["recall_net"])                      # untrusted, names the missing file
        self.assertIn("missing_ch09", data["recall_note"])

    def test_apply_prunes_stale_asset_and_stays_valid(self):
        # flipping maybe=true must not turn an old stale (warning-level) asset into a validator ERROR
        tmp = tempfile.mkdtemp()
        ws, bank_path = self._ws_with(tmp, [
            {"id": "stale_flip", "chapter": 1, "type": "subjective", "question": "图题。",
             "source": "material", "ai_generated": False,
             "source_file": "lectures/ch01.pdf", "source_pages": [2],
             "assets": [{"path": "references/assets/long_gone.png", "role": "figure", "type": "page_image"}]}])
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf", "ch02.pdf"])
        rc = BVI.run(["--workspace", ws, "--materials", mat, "--apply"], backend=_default_backend())
        self.assertEqual(rc, 0)
        q = next(x for x in json.load(open(bank_path, encoding="utf-8")) if x["id"] == "stale_flip")
        self.assertIs(q["maybe_requires_assets"], True)
        paths = [a["path"] for a in q["assets"]]
        self.assertNotIn("references/assets/long_gone.png", paths)   # stale asset pruned, loudly
        self.assertTrue(any(p.endswith("_p2.png") for p in paths))
        qidx = _load(ws, "image_question_index.json")
        self.assertTrue(any(w.startswith("apply_pruned_stale_asset") for w in qidx["warnings"]))
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "validate_workspace.py"), ws],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)       # applied workspace still validates

    def test_figure_pages_surfaces_scan_failures(self):
        tmp = tempfile.mkdtemp()
        be = _default_backend()
        del be.texts["ch02.pdf"]                                  # ch02 scan fails entirely
        ws, _m, _rc = _build(tmp, backend=be)
        rc, out = self._capture(LFP.run, ["--workspace", ws])
        self.assertEqual(rc, 0)
        self.assertIn("pdf_text_failed", out)                     # the missing-file gap is visible
        rc, out2 = self._capture(LFP.run, ["--workspace", ws, "--json"])
        self.assertTrue(any(w.startswith("pdf_text_failed") for w in json.loads(out2)["warnings"]))

    def test_render_falls_back_to_pdfium_when_fitz_fails(self):
        rb = BVI.RealBackend.__new__(BVI.RealBackend)

        class _FitzBroken(object):
            @staticmethod
            def open(path):
                raise RuntimeError("fitz cannot render this PDF")

        class _PdfiumPage(object):
            def render(self, scale):
                class _Bmp(object):
                    def to_pil(self):
                        class _Img(object):
                            def save(self, buf, format):
                                buf.write(b"PNGBYTES")
                        return _Img()
                return _Bmp()

        class _Pdfium(object):
            @staticmethod
            def PdfDocument(path):
                return {0: _PdfiumPage()}

        rb._pypdf, rb._fitz, rb._pdfium = None, _FitzBroken(), _Pdfium()
        self.assertEqual(rb.render_page_png("x.pdf", 0), b"PNGBYTES")   # pdfium rescues the page

    # ---- regression guards for Codex round-5 (4 P2 + 1 P3) ----

    def test_empty_materials_scan_marked_untrusted(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        empty = os.path.join(tmp, "empty_mat")
        os.makedirs(empty)
        rc = BVI.run(["--workspace", ws, "--materials", empty], backend=_default_backend())
        self.assertEqual(rc, 0)
        qidx = _load(ws, "image_question_index.json")
        self.assertTrue(any(w.startswith("no_pdfs_found") for w in qidx["warnings"]))
        rcode, out = self._capture(LIQ.run, ["--workspace", ws, "--json"])
        self.assertFalse(json.loads(out)["recall_net"])           # empty scan ≠ all clear

    def test_qualified_source_path_gets_no_basename_fallback(self):
        # bank says lectures/ch01.pdf; materials only has homework/ch01.pdf → NOT a stand-in
        fig = {"homework/ch01.pdf": {"pages": 5, "visual": {2: {}}}}
        self.assertEqual(BVI._visual_hits(fig, "lectures/ch01.pdf", [2]), [])
        self.assertFalse(BVI._file_indexed(fig, "lectures/ch01.pdf"))   # → source_pdf_not_indexed warning
        self.assertTrue(BVI._file_indexed(fig, "ch01.pdf"))             # bare name may still match

    def test_official_answer_requires_material_or_teacher_source(self):
        tmp = tempfile.mkdtemp()
        ws, _bank = self._ws_with(tmp, [
            {"id": "mix_ans", "chapter": 1, "type": "subjective", "question": "混合来源。",
             "answer": "答案", "source": "mixed", "ai_generated": False},
            {"id": "unk_ans", "chapter": 1, "type": "subjective", "question": "无来源。", "answer": "答案"}])
        BVI.run(["--workspace", ws], backend=_default_backend())
        rec = {r["id"]: r for r in _load(ws, "image_question_index.json")["questions"]}
        self.assertFalse(rec["mix_ans"]["has_official_answer"])   # mixed ≠ 官方
        self.assertFalse(rec["unk_ans"]["has_official_answer"])   # missing source ≠ 官方
        self.assertTrue(rec["ansfig_1"]["has_official_answer"])   # material 仍算

    def test_ingest_skill_orders_recall_check_after_ingest(self):
        src = open(os.path.join(ROOT, "skills", "exam-ingest", "SKILL.md"), encoding="utf-8").read()
        self.assertIn("AFTER ingest has created the workspace", src)
        # the cross-check instruction must appear AFTER the ingest.py step, not under the builder step
        self.assertLess(src.index("scripts/ingest.py"), src.index("Recall cross-check (P0-V2)"))

    def test_figref_regex_has_token_boundary(self):
        self.assertFalse(BVI.classify_page("It is comfortable 1 and profitable 2024.")["has_visual"])
        self.assertTrue(BVI.classify_page("see Table 1 for details")["has_visual"])
        self.assertTrue(BVI.classify_page("如 表 2 所示")["has_visual"])

    def test_no_network_llm_or_dep_in_new_scripts(self):
        for name in ("build_visual_index.py", "list_image_questions.py",
                     "list_figure_pages.py", "show_question_assets.py"):
            src = open(os.path.join(SCRIPTS, name), encoding="utf-8").read()
            for banned in ("import requests", "import anthropic", "import openai", "urllib.request",
                           "http.client", "import socket", "subprocess", "claude -p"):
                self.assertNotIn(banned, src, "%s contains %s" % (name, banned))


if __name__ == "__main__":
    unittest.main(verbosity=2)
