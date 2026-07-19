# -*- coding: utf-8 -*-
"""Tests for the P0-V2 universal visual index (scripts/build_visual_index.py + the three official tools).

Recall-first is the whole point: a page with an embedded image but NO caption keywords must still be
flagged; detector vocabulary must be multi-domain (circuit/flowchart/waveform …), never bound to one
subject. Pure stdlib; PDF backends are faked — no real pypdf/PyMuPDF needed, no network, no LLM."""
import base64
import contextlib
import json
import hashlib
import copy
import io
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
import build_visual_index as BVI          # noqa: E402
import exam_start                         # noqa: E402
import list_image_questions as LIQ        # noqa: E402
import list_figure_pages as LFP           # noqa: E402
import show_question_assets as SQA        # noqa: E402


_RUNTIME_IDENTITY = None
_CONFIRMED_FULL_PAIRS = set()


def _confirmed_tool_run(original, argv, *args, **kwargs):
    """Run a full-workspace tool against an exact confirmed test pair."""
    global _RUNTIME_IDENTITY
    arguments = list(argv)
    try:
        workspace = os.path.abspath(arguments[arguments.index("--workspace") + 1])
    except (ValueError, IndexError):
        return original(argv, *args, **kwargs)
    if not os.path.isdir(workspace):
        return original(argv, *args, **kwargs)
    try:
        materials = os.path.abspath(arguments[arguments.index("--materials") + 1])
    except (ValueError, IndexError):
        parent = os.path.dirname(workspace)
        materials = os.path.join(parent, ".%s-materials" % os.path.basename(workspace))
        os.makedirs(materials, exist_ok=True)
    home = os.path.join(
        os.path.dirname(workspace),
        ".%s-examprep-home" % os.path.basename(workspace),
    )
    key = (os.path.normcase(workspace), os.path.normcase(materials))
    with mock.patch.dict(os.environ, {"EXAMPREP_HOME": home}):
        if key not in _CONFIRMED_FULL_PAIRS and os.path.isdir(materials):
            if _RUNTIME_IDENTITY is None:
                _RUNTIME_IDENTITY = exam_start._capture_runtime_identity()
            output = io.StringIO()
            with mock.patch.object(
                    exam_start, "_capture_runtime_identity",
                    return_value=_RUNTIME_IDENTITY), \
                    contextlib.redirect_stdout(output):
                code = exam_start.run([
                    "confirm", "--course", "visual-index-fixture",
                    "--materials", materials, "--workspace", workspace,
                    "--mode", "from_scratch", "--time-budget", "le1d",
                    "--language", "en", "--processing-mode", "full", "--json",
                ])
            if code != 0:
                raise AssertionError(output.getvalue())
            _CONFIRMED_FULL_PAIRS.add(key)
        return original(argv, *args, **kwargs)


_BVI_RUN = BVI.run
_LIQ_RUN = LIQ.run
_LFP_RUN = LFP.run
_SQA_RUN = SQA.run


def setUpModule():
    """Scope full-workspace confirmation wrappers to this test module."""

    BVI.run = lambda argv, *args, **kwargs: _confirmed_tool_run(
        _BVI_RUN, argv, *args, **kwargs
    )
    LIQ.run = lambda argv, *args, **kwargs: _confirmed_tool_run(
        _LIQ_RUN, argv, *args, **kwargs
    )
    LFP.run = lambda argv, *args, **kwargs: _confirmed_tool_run(
        _LFP_RUN, argv, *args, **kwargs
    )
    SQA.run = lambda argv, *args, **kwargs: _confirmed_tool_run(
        _SQA_RUN, argv, *args, **kwargs
    )


def tearDownModule():
    """Do not leak the full-workspace confirmation wrappers into discovery."""
    BVI.run = _BVI_RUN
    LIQ.run = _LIQ_RUN
    LFP.run = _LFP_RUN
    SQA.run = _SQA_RUN

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63f8ffff3f0005fe02fe0def46b80000000049454e44ae426082"
)  # structurally valid, decodable 1x1 RGB PNG

VALID_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkS"
    "Ew8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJ"
    "CQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
    "MjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAA"
    "AAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEG"
    "E1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RF"
    "RkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKj"
    "pKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP0"
    "9fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgEC"
    "BAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLR"
    "ChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0"
    "dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbH"
    "yMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD3+iii"
    "gD//2Q=="
)


def _png_chunk(kind, payload):
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))


BAD_ZLIB_PNG = (
    b"\x89PNG\r\n\x1a\n"
    + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    + _png_chunk(b"IDAT", b"valid framing, not a zlib stream")
    + _png_chunk(b"IEND", b"")
)
BAD_CRC_PNG = PNG[:-1] + bytes([PNG[-1] ^ 1])


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
        self.assertTrue(BVI.classify_page("captionless Venn", images=0, drawings=4)["has_visual"])
        self.assertFalse(BVI.classify_page("text", images=0, drawings=2)["has_visual"])  # underlines ≠ figure

    def test_captionless_multirow_mapping_table_is_visual(self):
        text = ("Experiment              Set Theory\n"
                "Outcome                 Element\n"
                "Sample Space            Universal Set\n"
                "Event                   Set")
        got = BVI.classify_page(text, images=0, drawings=1)
        self.assertTrue(got["has_visual"])
        self.assertIn("table", got["visual_kinds"])
        self.assertTrue(got["signals"]["layout_table"])

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
    @staticmethod
    def _workspace_snapshot(ws):
        result = {}
        for directory, _subdirs, names in os.walk(ws):
            for name in names:
                path = os.path.join(directory, name)
                with open(path, "rb") as stream:
                    result[os.path.relpath(path, ws)] = stream.read()
        return result

    def _assert_no_transaction_temps(self, ws):
        leftovers = []
        for directory, _subdirs, names in os.walk(ws):
            leftovers.extend(
                os.path.join(directory, name) for name in names
                if name.startswith(".") and name.endswith(".tmp")
            )
        self.assertEqual([], leftovers)

    def test_canonical_markdown_relpath_normalizes_lexical_aliases(self):
        tmp = tempfile.mkdtemp()
        canonical_wiki = os.path.join(tmp, "ws", "references", "wiki")
        canonical_asset = os.path.join(tmp, "ws", "references", "assets", "page.png")
        original_realpath = BVI.os.path.realpath

        def canonicalize(path):
            if path == "wiki-dir-alias":
                return canonical_wiki
            if path == "asset-alias":
                return canonical_asset
            return original_realpath(path)

        with mock.patch.object(BVI.os.path, "realpath", side_effect=canonicalize):
            self.assertEqual(
                BVI._canonical_rel_posix("wiki-dir-alias", "asset-alias"),
                "../assets/page.png",
            )

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
        # v4.1: prompt and answer coverage are separate denominators. The legacy `suspects`
        # field remains a prompt-side alias for old readers.
        self.assertEqual(qidx["prompt_suspects"], qidx["suspects"])
        self.assertEqual([s["id"] for s in qidx["answer_suspects"]], ["ansfig_1"])
        self.assertEqual(qidx["per_chapter"]["2"]["answer_suspects"], 1)
        # Both outputs bind their zero/nonzero results to the same exact mutable inputs.
        self.assertEqual(fig["integrity"], qidx["integrity"])
        self.assertEqual(fig["integrity"]["schema_version"], 2)
        self.assertTrue(fig["integrity"]["mode"]["materials_scan"])
        bank_bytes = open(os.path.join(ws, "references", "quiz_bank.json"), "rb").read()
        self.assertEqual(fig["integrity"]["inputs"]["references/quiz_bank.json"]["sha256"],
                         hashlib.sha256(bank_bytes).hexdigest())
        pdf_bytes = open(os.path.join(_mat, "lectures", "ch01.pdf"), "rb").read()
        self.assertEqual(fig["integrity"]["materials"]["lectures/ch01.pdf"]["sha256"],
                         hashlib.sha256(pdf_bytes).hexdigest())
        inventory = json.dumps(["lectures/ch01.pdf", "lectures/ch02.pdf"],
                               ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertEqual(fig["integrity"]["material_inventory_sha256"],
                         hashlib.sha256(inventory).hexdigest())
        self.assertEqual(set(fig["integrity"]["outputs"]),
                         {"figure_page_index.json", "image_question_index.json"})

    def test_index_output_hardlink_is_replaced_without_overwriting_outside_inode(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        outside = os.path.join(tmp, "outside.json")
        with open(outside, "wb") as stream:
            stream.write(b"outside-stays")
        target = os.path.join(ws, "references", "figure_page_index.json")
        if os.path.exists(target):
            os.remove(target)
        try:
            os.link(outside, target)
        except (OSError, NotImplementedError):
            self.skipTest("hard links unavailable")
        self.assertEqual(BVI.run(["--workspace", ws], backend=_default_backend()), 0)
        self.assertEqual(open(outside, "rb").read(), b"outside-stays")
        self.assertFalse(os.path.samefile(outside, target))

    def test_symlinked_quiz_bank_is_rejected_without_reading_outside(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        bank = os.path.join(ws, "references", "quiz_bank.json")
        outside = os.path.join(tmp, "outside-bank.json")
        os.replace(bank, outside)
        try:
            os.symlink(outside, bank)
        except (OSError, NotImplementedError):
            self.skipTest("no symlink privilege")
        with self.assertRaises(SystemExit) as cm:
            BVI.run(["--workspace", ws], backend=_default_backend())
        self.assertEqual(cm.exception.code, 2)

    def test_symlinked_references_parent_is_rejected_before_index_write(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        real_refs = os.path.join(tmp, "outside-references")
        os.replace(os.path.join(ws, "references"), real_refs)
        try:
            os.symlink(real_refs, os.path.join(ws, "references"), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("no symlink privilege")
        before = set(os.listdir(real_refs))
        with self.assertRaises(SystemExit) as cm:
            BVI.run(["--workspace", ws], backend=_default_backend())
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(set(os.listdir(real_refs)), before)

    def test_declared_required_asset_missing_is_always_a_prompt_suspect(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        bank = json.load(open(bank_path, encoding="utf-8"))
        bank.append({
            "id": "declared_missing", "chapter": 1, "type": "subjective",
            "question": "Use the required figure.", "answer": "x", "source": "material",
            "requires_assets": True,
            "assets": [{"path": "references/assets/does-not-exist.png", "role": "question_context"}],
        })
        json.dump(bank, open(bank_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        self.assertEqual(BVI.run(["--workspace", ws], backend=_default_backend()), 0)
        suspects = _load(ws, "image_question_index.json")["prompt_suspects"]
        hit = next(row for row in suspects if row["id"] == "declared_missing")
        self.assertIn("已声明", hit["reason"])
        self.assertEqual(BVI.run(["--workspace", ws, "--apply"], backend=_default_backend()), 0)
        post = _load(ws, "image_question_index.json")
        self.assertTrue(any(row["id"] == "declared_missing" for row in post["prompt_suspects"]))
        self.assertEqual(post["prompt_applied"], 0)

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
        # A visual answer page is repaired independently and is NEVER promoted to a prompt-side gate.
        answer_q = next(x for x in bank if x["id"] == "ansfig_1")
        answer_assets = [a for a in answer_q.get("assets", []) if a.get("role") == "answer_context"]
        self.assertEqual(len(answer_assets), 1)
        self.assertTrue(os.path.isfile(os.path.join(ws, answer_assets[0]["path"])))
        self.assertNotIn("maybe_requires_assets", answer_q)
        post = _load(ws, "image_question_index.json")
        self.assertEqual(post["answer_suspects"], [])
        self.assertEqual(post["answer_applied"], 1)
        # the applied workspace must still pass the fail-closed validator (real CLI run)
        home = os.path.join(
            os.path.dirname(ws), ".%s-examprep-home" % os.path.basename(ws)
        )
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "validate_workspace.py"), ws],
                           capture_output=True, text=True, encoding="utf-8",
                           env=dict(os.environ, EXAMPREP_HOME=home))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_apply_rejects_deterministic_target_ownership_before_any_write(self):
        cases = (
            ("answer-overwrites-current-prompt", "ansfig_1", "answer", 3,
             "answer_context", "ansfig_1", "question_context", b"PROMPT"),
            ("prompt-overwrites-current-answer", "suspect_1", "prompt", 2,
             "question_context", "suspect_1", "answer_context", b"ANSWER"),
            ("answer-overwrites-cross-item-prompt", "ansfig_1", "answer", 3,
             "answer_context", "plain_1", "question_context", b"SHARED"),
        )
        for (label, target_id, side, page, _planned_role, owner_id, owner_role,
             sentinel) in cases:
            with self.subTest(label):
                tmp = tempfile.mkdtemp()
                ws = _mk_workspace(tmp)
                mat = os.path.join(tmp, "mat")
                _mk_materials(mat, ["ch01.pdf", "ch02.pdf"])
                bank_path = os.path.join(ws, "references", "quiz_bank.json")
                bank = json.load(open(bank_path, encoding="utf-8"))
                target = next(item for item in bank if item["id"] == target_id)
                source_file = (
                    target.get("answer_source_file") or target.get("source_file")
                    if side == "answer" else target.get("source_file")
                )
                suspect = {
                    "id": target_id, "source_layer": "quiz_bank",
                    "source_file": source_file, "visual_pages": [page],
                }
                asset_root = os.path.join(ws, "references", "assets")
                plan = BVI._planned_repair_assets(
                    ws, asset_root, bank, [suspect], side)[0]
                owner = next(item for item in bank if item["id"] == owner_id)
                owner["assets"] = [{
                    "path": plan["path"], "role": owner_role,
                    "type": "page_image",
                }]
                os.makedirs(os.path.dirname(plan["full_path"]), exist_ok=True)
                with open(plan["full_path"], "wb") as stream:
                    stream.write(sentinel)
                with open(bank_path, "w", encoding="utf-8") as stream:
                    json.dump(bank, stream, ensure_ascii=False, indent=2)

                # Establish deterministic pre-existing indices, then prove the
                # unsafe --apply attempt cannot rewrite any authoritative or
                # derived artifact (nor create the bank backup).
                self.assertEqual(BVI.run(
                    ["--workspace", ws, "--materials", mat],
                    backend=_default_backend()), 0)
                watched = [
                    bank_path,
                    plan["full_path"],
                    os.path.join(ws, "references", "figure_page_index.json"),
                    os.path.join(ws, "references", "image_question_index.json"),
                ]
                before = {path: open(path, "rb").read() for path in watched}
                with self.assertRaises(SystemExit) as stopped:
                    BVI.run(
                        ["--workspace", ws, "--materials", mat, "--apply"],
                        backend=_default_backend())
                self.assertEqual(stopped.exception.code, 2)
                self.assertEqual(
                    before, {path: open(path, "rb").read() for path in watched})
                self.assertFalse(os.path.lexists(bank_path + ".bak"))

    def test_apply_rejects_unowned_existing_target_before_render_or_mutation(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf", "ch02.pdf"])
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        bank = json.load(open(bank_path, encoding="utf-8"))
        suspect = {
            "id": "suspect_1", "source_layer": "quiz_bank",
            "source_file": "lectures/ch01.pdf", "visual_pages": [2],
        }
        plan = BVI._planned_repair_assets(
            ws, os.path.join(ws, "references", "assets"), bank,
            [suspect], "prompt",
        )[0]
        sentinel = PNG + b"UNOWNED"
        with open(plan["full_path"], "wb") as stream:
            stream.write(sentinel)
        self.assertEqual(BVI.run(
            ["--workspace", ws, "--materials", mat],
            backend=_default_backend()), 0)
        wiki_path = os.path.join(ws, "references", "wiki", "ch1.md")
        watched = [
            bank_path, wiki_path, plan["full_path"],
            os.path.join(ws, "references", "figure_page_index.json"),
            os.path.join(ws, "references", "image_question_index.json"),
        ]
        before = {path: open(path, "rb").read() for path in watched}

        class MustNotRender(FakeBackend):
            def render_page_png(self, pdf_path, page_index):
                raise AssertionError("ownership preflight must precede rendering")

        base = _default_backend()
        backend = MustNotRender(base.texts, base.media)
        with self.assertRaises(SystemExit) as stopped:
            BVI.run(
                ["--workspace", ws, "--materials", mat, "--apply"],
                backend=backend,
            )
        self.assertEqual(2, stopped.exception.code)
        self.assertEqual(
            before, {path: open(path, "rb").read() for path in watched}
        )
        self.assertFalse(os.path.lexists(bank_path + ".bak"))

    def test_direct_apply_allows_proved_same_side_source_refresh(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf", "ch02.pdf"])
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        bank = json.load(open(bank_path, encoding="utf-8"))
        item = next(row for row in bank if row["id"] == "suspect_1")
        suspect = {
            "id": item["id"], "source_layer": "quiz_bank",
            "source_file": item["source_file"], "visual_pages": [2],
        }
        plan = BVI._planned_repair_assets(
            ws, os.path.join(ws, "references", "assets"), bank,
            [suspect], "prompt",
        )[0]
        old = PNG + b"OLD"
        with open(plan["full_path"], "wb") as stream:
            stream.write(old)
        item["assets"] = [{
            "path": plan["path"], "role": "question_context",
            "type": "page_image", "sha256": hashlib.sha256(old).hexdigest(),
            "generated_by": BVI._REPAIR_WRITER_ID,
            "source_layer": "quiz_bank", "source_file": item["source_file"],
            "source_page": 2, "side": "prompt",
        }]
        with open(bank_path, "w", encoding="utf-8") as stream:
            json.dump(bank, stream, ensure_ascii=False, indent=2)

        applied = BVI.apply_suspects(
            ws, mat, bank, [suspect], _default_backend(),
            os.path.join(ws, "references", "assets"), [],
            # A caller-supplied empty policy/taint cannot weaken the live policy.
            tainted_keys=set(),
            policy_rows={"quiz_rows": [], "teaching_rows": [], "content_units": []},
        )
        self.assertEqual(1, applied)
        self.assertEqual(PNG, open(plan["full_path"], "rb").read())
        refreshed = next(
            asset for asset in item["assets"]
            if BVI.physical_asset_key(asset.get("path")) == plan["key"]
        )
        self.assertEqual(hashlib.sha256(PNG).hexdigest(), refreshed["sha256"])
        self.assertEqual(BVI._REPAIR_WRITER_ID, refreshed["generated_by"])

    def test_direct_apply_empty_policy_cannot_hide_foreign_attempt_owner(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf", "ch02.pdf"])
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        bank = json.load(open(bank_path, encoding="utf-8"))
        suspect = {
            "id": "suspect_1", "source_layer": "quiz_bank",
            "source_file": "lectures/ch01.pdf", "visual_pages": [2],
        }
        plan = BVI._planned_repair_assets(
            ws, os.path.join(ws, "references", "assets"), bank,
            [suspect], "prompt",
        )[0]
        ingest = os.path.join(ws, ".ingest")
        os.makedirs(ingest, exist_ok=True)
        with open(os.path.join(ingest, "content_units.jsonl"),
                  "w", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "unit_id": "foreign-attempt", "chapter_id": "ch02",
                "kind": "figure", "asset_path": plan["path"],
                "asset_role": "student_attempt", "metadata": {},
            }) + "\n")
        before_bank = json.dumps(bank, ensure_ascii=False, sort_keys=True)

        class MustNotRender(FakeBackend):
            def render_page_png(self, pdf_path, page_index):
                raise AssertionError("live foreign-attempt policy must stop rendering")

        base = _default_backend()
        with self.assertRaises(SystemExit) as stopped:
            BVI.apply_suspects(
                ws, mat, bank, [suspect],
                MustNotRender(base.texts, base.media),
                os.path.join(ws, "references", "assets"), [],
                tainted_keys=set(),
                policy_rows={
                    "quiz_rows": [], "teaching_rows": [], "content_units": [],
                },
            )
        self.assertEqual(2, stopped.exception.code)
        self.assertEqual(before_bank,
                         json.dumps(bank, ensure_ascii=False, sort_keys=True))
        self.assertFalse(os.path.lexists(plan["full_path"]))

    def test_visual_index_rejects_win32_trailing_dot_alias_before_outputs(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        self.assertEqual(BVI.run(["--workspace", ws], backend=_default_backend()), 0)
        teaching_path = os.path.join(ws, "references", "teaching_examples.json")
        with open(teaching_path, "w", encoding="utf-8") as stream:
            json.dump([{
                "id": "attempt-alias", "chapter": 1,
                "assets": [{
                    "path": "references/assets/shared.png.",
                    "role": "student_attempt", "type": "crop_image",
                }],
            }], stream, ensure_ascii=False, indent=2)
        watched = [
            teaching_path,
            os.path.join(ws, "references", "figure_page_index.json"),
            os.path.join(ws, "references", "image_question_index.json"),
        ]
        before = {path: open(path, "rb").read() for path in watched}

        with self.assertRaises(SystemExit) as stopped:
            BVI.run(["--workspace", ws], backend=_default_backend())
        self.assertEqual(stopped.exception.code, 2)
        self.assertEqual(before, {path: open(path, "rb").read() for path in watched})

    def test_apply_repairs_overlapping_and_teaching_only_examples_in_both_layers(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        bank = json.load(open(bank_path, encoding="utf-8"))
        overlap = dict(next(q for q in bank if q["id"] == "suspect_1"))
        overlap.update({"teaching_role": "worked_example", "title": "overlap"})
        teaching_only = {
            "id": "teaching_only_visual", "chapter": 1, "teaching_role": "worked_example",
            "title": "teaching only", "type": "subjective", "question": "Study the shown case.",
            "source": "material", "source_file": "lectures/ch01.pdf", "source_pages": [2],
        }
        teaching_path = os.path.join(ws, "references", "teaching_examples.json")
        with open(teaching_path, "w", encoding="utf-8") as f:
            json.dump([overlap, teaching_only], f, ensure_ascii=False, indent=2)
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf", "ch02.pdf"])

        self.assertEqual(BVI.run(["--workspace", ws, "--materials", mat, "--apply"],
                                 backend=_default_backend()), 0)
        updated_bank = json.load(open(bank_path, encoding="utf-8"))
        updated_teaching = json.load(open(teaching_path, encoding="utf-8"))
        for item in (next(q for q in updated_bank if q["id"] == "suspect_1"),
                     next(q for q in updated_teaching if q["id"] == "suspect_1"),
                     next(q for q in updated_teaching if q["id"] == "teaching_only_visual")):
            self.assertIs(item.get("maybe_requires_assets"), True)
            self.assertTrue(any(a.get("role") == "question_context" for a in item.get("assets", [])))
        self.assertTrue(os.path.isfile(bank_path + ".bak"))
        self.assertTrue(os.path.isfile(teaching_path + ".bak"))
        qidx = _load(ws, "image_question_index.json")
        self.assertEqual(qidx["prompt_suspects"], [])
        self.assertEqual(len(qidx["teaching_questions"]), 2)
        self.assertEqual(qidx["teaching_prompt_applied"], 2)

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

    def test_wiki_visual_coverage_and_apply_wiki_are_idempotent(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        wiki = os.path.join(ws, "references", "wiki", "ch1.md")
        with open(wiki, "w", encoding="utf-8") as f:
            f.write("# ch1\n\n<!-- lectures/ch01.pdf p.1 -->\nplain\n\n"
                    "<!-- lectures/ch01.pdf p.2 -->\nvector-only visual\n")
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf"])
        be = FakeBackend(texts_by_name={"ch01.pdf": ["plain", "vector-only visual"]},
                         media_by_name={"ch01.pdf": [(0, 0), (1, 0)]})

        rc = BVI.run(["--workspace", ws, "--materials", mat], backend=be)
        self.assertEqual(rc, 0)
        cov = _load(ws, "figure_page_index.json")["wiki_visual_coverage"]
        self.assertEqual((cov["detected"], cov["embedded"], cov["missing"]), (1, 0, 1))
        self.assertEqual(cov["missing_pages"][0]["source_file"], "lectures/ch01.pdf")
        self.assertTrue(any(w.startswith("wiki_visual_missing") for w in cov["warnings"]))

        argv = ["--workspace", ws, "--materials", mat, "--apply-wiki"]
        self.assertEqual(BVI.run(argv, backend=be), 0)
        cov2 = _load(ws, "figure_page_index.json")["wiki_visual_coverage"]
        self.assertEqual((cov2["detected"], cov2["embedded"], cov2["missing"]), (1, 1, 0))
        text1 = open(wiki, encoding="utf-8").read()
        self.assertEqual(text1.count("<!-- wiki-visual-index:"), 1)
        self.assertIn("../assets/", text1)

        # Re-running the mutating command must reuse the stable page asset and not duplicate markup.
        self.assertEqual(BVI.run(argv, backend=be), 0)
        text2 = open(wiki, encoding="utf-8").read()
        self.assertEqual(text2, text1)

    def test_direct_coverage_empty_taint_cannot_count_foreign_attempt_image(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        asset_path = os.path.join(ws, "references", "assets", "attempt-direct.png")
        with open(asset_path, "wb") as stream:
            stream.write(PNG)
        wiki = os.path.join(ws, "references", "wiki", "ch1.md")
        with open(wiki, "w", encoding="utf-8") as stream:
            stream.write(
                "# ch1\n\n<!-- lectures/ch01.pdf p.2 -->\n"
                "![lectures/ch01.pdf 第 2 页图示](../assets/attempt-direct.png)\n"
            )
        ingest = os.path.join(ws, ".ingest")
        os.makedirs(ingest, exist_ok=True)
        with open(os.path.join(ingest, "content_units.jsonl"),
                  "w", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "unit_id": "foreign-attempt-direct", "chapter_id": "ch02",
                "kind": "figure",
                "asset_path": "references/assets/attempt-direct.png",
                "asset_role": "student_attempt", "metadata": {},
            }) + "\n")
        coverage = BVI.build_wiki_visual_coverage(
            ws,
            {"lectures/ch01.pdf": {"visual": {2: {"has_visual": True}}}},
            tainted_keys=set(),
        )
        self.assertEqual((coverage["embedded"], coverage["missing"]), (0, 1))
        self.assertEqual([], coverage["pages"][0]["asset_paths"])
        self.assertIn("references/assets/attempt-direct.png",
                      coverage["pages"][0]["declared_assets"])

    def test_apply_wiki_target_ownership_rejects_whole_batch_before_removal(self):
        cases = ("unowned", "prompt", "answer", "attempt")
        for owner_kind in cases:
            with self.subTest(owner_kind):
                tmp = tempfile.mkdtemp()
                ws = _mk_workspace(tmp)
                mat = os.path.join(tmp, "mat")
                _mk_materials(mat, ["ch01.pdf", "ch02.pdf"])
                wiki = os.path.join(ws, "references", "wiki", "ch1.md")
                old_answer = os.path.join(
                    ws, "references", "assets", "old-generated-answer.png"
                )
                with open(old_answer, "wb") as stream:
                    stream.write(PNG)
                with open(wiki, "w", encoding="utf-8") as stream:
                    stream.write(
                        "# ch1\n\n<!-- lectures/ch01.pdf p.2 -->\nvisual\n\n"
                        "<!-- wiki-visual-index: lectures/ch02.pdf p.3 -->\n"
                        "![原页图示 lectures/ch02.pdf p.3]"
                        "(../assets/old-generated-answer.png)\n"
                    )
                digest = hashlib.sha1(
                    b"lectures/ch01.pdf"
                ).hexdigest()[:10]
                target = os.path.join(
                    ws, "references", "assets", "wiki_%s_p0002.png" % digest
                )
                target_rel = "references/assets/" + os.path.basename(target)
                sentinel = PNG + owner_kind.encode("ascii")
                with open(target, "wb") as stream:
                    stream.write(sentinel)
                bank_path = os.path.join(ws, "references", "quiz_bank.json")
                bank = json.load(open(bank_path, encoding="utf-8"))
                if owner_kind in ("prompt", "answer"):
                    owner = next(row for row in bank if row["id"] == "plain_1")
                    owner["assets"] = [{
                        "path": target_rel,
                        "role": ("question_context" if owner_kind == "prompt"
                                 else "answer_context"),
                        "type": "page_image",
                    }]
                    with open(bank_path, "w", encoding="utf-8") as stream:
                        json.dump(bank, stream, ensure_ascii=False, indent=2)
                elif owner_kind == "attempt":
                    ingest = os.path.join(ws, ".ingest")
                    os.makedirs(ingest, exist_ok=True)
                    with open(os.path.join(ingest, "content_units.jsonl"),
                              "w", encoding="utf-8") as stream:
                        stream.write(json.dumps({
                            "unit_id": "wiki-target-attempt", "chapter_id": "ch02",
                            "kind": "figure", "asset_path": target_rel,
                            "asset_role": "student_attempt", "metadata": {},
                        }) + "\n")

                self.assertEqual(BVI.run(
                    ["--workspace", ws, "--materials", mat],
                    backend=_default_backend()), 0)
                watched = [
                    wiki, bank_path, target,
                    os.path.join(ws, "references", "figure_page_index.json"),
                    os.path.join(ws, "references", "image_question_index.json"),
                ]
                before = {path: open(path, "rb").read() for path in watched}

                class MustNotRender(FakeBackend):
                    def render_page_png(self, pdf_path, page_index):
                        raise AssertionError("wiki ownership preflight must run first")

                base = _default_backend()
                with self.assertRaises(SystemExit) as stopped:
                    BVI.run(
                        ["--workspace", ws, "--materials", mat, "--apply-wiki"],
                        backend=MustNotRender(base.texts, base.media),
                    )
                self.assertEqual(2, stopped.exception.code)
                self.assertEqual(
                    before, {path: open(path, "rb").read() for path in watched}
                )
                self.assertIn("old-generated-answer.png",
                              open(wiki, encoding="utf-8").read())

    def test_solution_page_is_deferred_from_wiki_but_applied_as_answer_context(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        item = {
            "id": "solution_guard", "chapter": 1, "type": "subjective",
            "question": "Read the prompt diagram.", "answer": "Worked solution.",
            "source": "material", "source_file": "lectures/ch01.pdf", "source_pages": [1],
            "answer_source_file": "lectures/ch01.pdf", "answer_source_pages": [2],
        }
        with open(bank_path, "w", encoding="utf-8") as f:
            json.dump([item], f, ensure_ascii=False, indent=2)
        old_answer = os.path.join(ws, "references", "assets", "old-answer.png")
        with open(old_answer, "wb") as f:
            f.write(PNG)
        wiki = os.path.join(ws, "references", "wiki", "ch1.md")
        with open(wiki, "w", encoding="utf-8") as f:
            f.write(
                "# ch1\n\n"
                "<!-- lectures/ch01.pdf p.1 -->\nprompt source\n\n"
                "<!-- lectures/ch01.pdf p.2 -->\nsolution source\n\n"
                "<!-- wiki-visual-index: lectures/ch01.pdf p.2 -->\n"
                "![原页图示 lectures/ch01.pdf p.2](../assets/old-answer.png)\n")
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf"])
        be = FakeBackend(texts_by_name={"ch01.pdf": ["prompt visual", "solution visual"]},
                         media_by_name={"ch01.pdf": [(1, 0), (1, 0)]})

        rc = BVI.run(["--workspace", ws, "--materials", mat, "--apply", "--apply-wiki"],
                     backend=be)
        self.assertEqual(rc, 0)
        wiki_text = open(wiki, encoding="utf-8").read()
        self.assertNotIn("wiki-visual-index: lectures/ch01.pdf p.2", wiki_text)
        self.assertNotIn("old-answer.png", wiki_text)
        self.assertIn("wiki-visual-index: lectures/ch01.pdf p.1", wiki_text)

        updated = json.load(open(bank_path, encoding="utf-8"))
        roles = [a.get("role") for a in updated[0].get("assets", [])]
        self.assertIn("question_context", roles)
        self.assertIn("answer_context", roles)
        self.assertIs(updated[0].get("maybe_requires_assets"), True)

        fig = _load(ws, "figure_page_index.json")
        cov = fig["wiki_visual_coverage"]
        self.assertEqual(cov["detected"], cov["embedded"] + cov["missing"])
        self.assertEqual(cov["total_visual_pages"],
                         cov["detected"] + cov["deferred_answer_count"]
                         + cov["shared_prompt_answer_count"])
        self.assertEqual((cov["detected"], cov["embedded"], cov["missing"]), (1, 1, 0))
        self.assertEqual(cov["deferred_answer_count"], 1)
        self.assertEqual([(p["source_file"], p["page"], p["status"])
                          for p in cov["deferred_answer_pages"]],
                         [("lectures/ch01.pdf", 2, "deferred_answer")])
        self.assertEqual(cov["removed_deferred_answer_blocks"], 1)
        self.assertEqual(cov["manual_answer_exposure_count"], 0)
        qidx = _load(ws, "image_question_index.json")
        self.assertEqual(qidx["answer_suspects"], [])
        self.assertEqual(qidx["wiki_deferred_answer_blocks_removed"], 1)

    def test_shared_prompt_answer_page_blocks_whole_page_prompt_and_wiki_apply(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        with open(bank_path, "w", encoding="utf-8") as f:
            json.dump([{
                "id": "shared_page_guard", "chapter": 1, "type": "subjective",
                "question": "Prompt and worked answer share this page.",
                "answer": "Worked answer.", "source": "material",
                "source_file": "lectures/ch01.pdf", "source_pages": [1],
                "answer_source_file": "lectures/ch01.pdf", "answer_source_pages": [1],
            }], f, ensure_ascii=False, indent=2)
        old_page = os.path.join(ws, "references", "assets", "old-shared-page.png")
        with open(old_page, "wb") as f:
            f.write(PNG)
        wiki = os.path.join(ws, "references", "wiki", "ch1.md")
        with open(wiki, "w", encoding="utf-8") as f:
            f.write("# ch1\n\n<!-- lectures/ch01.pdf p.1 -->\nshared source\n\n"
                    "<!-- wiki-visual-index: lectures/ch01.pdf p.1 -->\n"
                    "![原页图示 lectures/ch01.pdf p.1](../assets/old-shared-page.png)\n")
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf"])
        be = FakeBackend(texts_by_name={"ch01.pdf": ["prompt and solution diagram"]},
                         media_by_name={"ch01.pdf": [(1, 0)]})

        rc = BVI.run(["--workspace", ws, "--materials", mat, "--apply", "--apply-wiki"],
                     backend=be)
        self.assertEqual(rc, 2)
        wiki_text = open(wiki, encoding="utf-8").read()
        self.assertNotIn("wiki-visual-index: lectures/ch01.pdf p.1", wiki_text)
        self.assertNotIn("old-shared-page.png", wiki_text)

        updated = json.load(open(bank_path, encoding="utf-8"))[0]
        roles = [a.get("role") for a in updated.get("assets", [])]
        self.assertNotIn("question_context", roles)       # whole-page prompt auto-apply is blocked
        self.assertIn("answer_context", roles)            # answer-side display remains valid
        self.assertNotIn("maybe_requires_assets", updated)

        cov = _load(ws, "figure_page_index.json")["wiki_visual_coverage"]
        self.assertEqual((cov["detected"], cov["embedded"], cov["missing"]), (0, 0, 0))
        self.assertEqual(cov["total_visual_pages"],
                         cov["detected"] + cov["deferred_answer_count"]
                         + cov["shared_prompt_answer_count"])
        self.assertEqual(cov["shared_prompt_answer_count"], 1)
        self.assertEqual(cov["shared_prompt_answer_blocker_count"], 1)
        self.assertEqual(cov["shared_prompt_answer_pages"][0]["status"],
                         "shared_prompt_answer")
        qidx = _load(ws, "image_question_index.json")
        self.assertEqual(qidx["shared_prompt_answer_count"], 1)
        self.assertEqual(qidx["shared_prompt_answer_blocker_count"], 1)
        self.assertEqual([s["id"] for s in qidx["prompt_suspects"]], ["shared_page_guard"])
        self.assertEqual(qidx["answer_suspects"], [])
        self.assertTrue(any(w.startswith("shared_prompt_answer_page:")
                            for w in qidx["warnings"]))
        self.assertTrue(any(w.startswith("apply_skip_shared_prompt_answer_page:")
                            for w in qidx["warnings"]))

    def test_audited_prompt_crop_resolves_shared_page_blocker_but_wiki_stays_deferred(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        crop = os.path.join(ws, "references", "assets", "shared-prompt-crop.png")
        with open(crop, "wb") as f:
            f.write(PNG)
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        with open(bank_path, "w", encoding="utf-8") as f:
            json.dump([{
                "id": "shared_page_cropped", "chapter": 1, "type": "subjective",
                "question": "Prompt and worked answer share this page.",
                "answer": "Worked answer.", "source": "material",
                "source_file": "lectures/ch01.pdf", "source_pages": [1],
                "answer_source_file": "lectures/ch01.pdf", "answer_source_pages": [1],
                "assets": [{"path": "references/assets/shared-prompt-crop.png",
                            "role": "question_context", "type": "crop_image"}],
            }], f, ensure_ascii=False, indent=2)
        wiki = os.path.join(ws, "references", "wiki", "ch1.md")
        with open(wiki, "w", encoding="utf-8") as f:
            f.write("# ch1\n\n<!-- lectures/ch01.pdf p.1 -->\nshared source\n")
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf"])
        be = FakeBackend(texts_by_name={"ch01.pdf": ["prompt and solution diagram"]},
                         media_by_name={"ch01.pdf": [(1, 0)]})

        rc = BVI.run(["--workspace", ws, "--materials", mat, "--apply", "--apply-wiki"],
                     backend=be)
        self.assertEqual(rc, 0)
        self.assertNotIn("wiki-visual-index", open(wiki, encoding="utf-8").read())
        updated = json.load(open(bank_path, encoding="utf-8"))[0]
        self.assertTrue(any(a.get("type") == "crop_image" and a.get("role") == "question_context"
                            for a in updated.get("assets", [])))
        self.assertTrue(any(a.get("role") == "answer_context" for a in updated.get("assets", [])))
        cov = _load(ws, "figure_page_index.json")["wiki_visual_coverage"]
        self.assertEqual(cov["shared_prompt_answer_count"], 1)
        self.assertEqual(cov["shared_prompt_answer_blocker_count"], 0)
        qidx = _load(ws, "image_question_index.json")
        self.assertEqual(qidx["prompt_suspects"], [])
        self.assertEqual(qidx["shared_prompt_answer_count"], 1)
        self.assertEqual(qidx["shared_prompt_answer_blocker_count"], 0)

    def test_manual_answer_page_image_is_preserved_and_fails_closed(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        with open(bank_path, "w", encoding="utf-8") as f:
            json.dump([{
                "id": "manual_solution", "chapter": 1, "type": "subjective",
                "question": "Prompt", "answer": "Solution", "source": "material",
                "source_file": "lectures/ch01.pdf", "source_pages": [1],
                "answer_source_file": "lectures/ch01.pdf", "answer_source_pages": [2],
            }], f, ensure_ascii=False, indent=2)
        manual = os.path.join(ws, "references", "assets", "manual-answer.png")
        with open(manual, "wb") as f:
            f.write(PNG)
        wiki = os.path.join(ws, "references", "wiki", "ch1.md")
        with open(wiki, "w", encoding="utf-8") as f:
            f.write("# ch1\n\n<!-- lectures/ch01.pdf p.1 -->\nprompt\n\n"
                    "<!-- lectures/ch01.pdf p.2 -->\n"
                    "![hand-authored worked answer](../assets/manual-answer.png)\n")
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf"])
        be = FakeBackend(texts_by_name={"ch01.pdf": ["prompt", "solution"]},
                         media_by_name={"ch01.pdf": [(1, 0), (1, 0)]})

        rc = BVI.run(["--workspace", ws, "--materials", mat, "--apply-wiki"], backend=be)
        self.assertEqual(rc, 2)
        self.assertIn("manual-answer.png", open(wiki, encoding="utf-8").read())
        cov = _load(ws, "figure_page_index.json")["wiki_visual_coverage"]
        self.assertEqual(cov["detected"], cov["embedded"] + cov["missing"])
        self.assertEqual(cov["deferred_answer_count"], 1)
        self.assertEqual(cov["manual_answer_exposure_count"], 1)
        self.assertEqual(cov["manual_answer_exposure_pages"][0]["coverage_issue"],
                         "manual_answer_exposure")
        self.assertTrue(any(w.startswith("wiki_answer_manual_exposure:")
                            for w in cov["warnings"]))
        self.assertEqual(_load(ws, "image_question_index.json")["manual_answer_exposure_count"], 1)

    def test_text_only_answer_page_manual_wiki_image_is_still_detected(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        with open(bank_path, "w", encoding="utf-8") as f:
            json.dump([{
                "id": "text_answer_manual", "chapter": 1, "type": "subjective",
                "question": "Prompt", "answer": "Text-only solution", "source": "material",
                "source_file": "lectures/ch01.pdf", "source_pages": [1],
                "answer_source_file": "lectures/ch01.pdf", "answer_source_pages": [2],
            }], f, ensure_ascii=False, indent=2)
        manual = os.path.join(ws, "references", "assets", "manual-text-answer.png")
        with open(manual, "wb") as f:
            f.write(PNG)
        wiki = os.path.join(ws, "references", "wiki", "ch1.md")
        with open(wiki, "w", encoding="utf-8") as f:
            f.write("# ch1\n\n<!-- lectures/ch01.pdf p.1 -->\nprompt\n\n"
                    "<!-- lectures/ch01.pdf p.2 -->\n"
                    "![hand-authored answer page](../assets/manual-text-answer.png)\n")
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf"])
        be = FakeBackend(texts_by_name={"ch01.pdf": ["plain prompt", "plain solution"]},
                         media_by_name={"ch01.pdf": [(0, 0), (0, 0)]})

        rc = BVI.run(["--workspace", ws, "--materials", mat, "--apply-wiki"], backend=be)
        self.assertEqual(rc, 2)
        self.assertIn("manual-text-answer.png", open(wiki, encoding="utf-8").read())
        fig = _load(ws, "figure_page_index.json")
        cov = fig["wiki_visual_coverage"]
        self.assertEqual(fig["files"]["lectures/ch01.pdf"]["visual_pages"], [])
        self.assertEqual(cov["deferred_answer_count"], 1)
        self.assertEqual(cov["inventory_only_answer_page_count"], 1)
        self.assertTrue(cov["deferred_answer_pages"][0]["inventory_only"])
        self.assertEqual(cov["manual_answer_exposure_count"], 1)
        self.assertEqual(_load(ws, "image_question_index.json")["manual_answer_exposure_count"], 1)

    def test_logo_in_page_block_is_not_visual_coverage_but_provenance_gallery_is(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        wiki = os.path.join(ws, "references", "wiki", "ch1.md")
        logo = os.path.join(ws, "references", "logo.png")
        with open(logo, "wb") as f:
            f.write(PNG)
        with open(wiki, "w", encoding="utf-8") as f:
            f.write("# ch1\n\n<!-- lectures/ch01.pdf p.2 -->\n"
                    "![course logo](../logo.png)\n")
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf"])
        be = FakeBackend(texts_by_name={"ch01.pdf": ["plain", "visual"]},
                         media_by_name={"ch01.pdf": [(0, 0), (1, 0)]})
        self.assertEqual(BVI.run(["--workspace", ws, "--materials", mat], backend=be), 0)
        cov = _load(ws, "figure_page_index.json")["wiki_visual_coverage"]
        self.assertEqual((cov["embedded"], cov["missing"]), (0, 1))

        with open(wiki, "a", encoding="utf-8") as f:
            f.write("\n![lectures/ch01.pdf 第 2 页图示](../logo.png)\n")
        self.assertEqual(BVI.run(["--workspace", ws, "--materials", mat], backend=be), 0)
        cov = _load(ws, "figure_page_index.json")["wiki_visual_coverage"]
        self.assertEqual((cov["embedded"], cov["missing"]), (1, 0))

    def test_apply_wiki_rejects_symlinked_wiki_parent(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        wiki_dir = os.path.join(ws, "references", "wiki")
        outside = os.path.join(tmp, "outside-wiki")
        shutil.rmtree(wiki_dir)
        os.makedirs(outside)
        with open(os.path.join(outside, "ch1.md"), "w", encoding="utf-8") as f:
            f.write("# outside\n<!-- lectures/ch01.pdf p.1 -->\n")
        try:
            os.symlink(outside, wiki_dir, target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("no symlink privilege")
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf"])
        be = FakeBackend(texts_by_name={"ch01.pdf": ["visual"]},
                         media_by_name={"ch01.pdf": [(1, 0)]})
        with self.assertRaises(SystemExit) as caught:
            BVI.run(["--workspace", ws, "--materials", mat, "--apply-wiki"], backend=be)
        self.assertEqual(caught.exception.code, 2)
        self.assertNotIn("wiki-visual-index", open(os.path.join(outside, "ch1.md"),
                                                    encoding="utf-8").read())

    def test_image_only_page_missing_exact_anchor_stays_in_denominator_and_is_repaired(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        wiki = os.path.join(ws, "references", "wiki", "ch1.md")
        with open(wiki, "w", encoding="utf-8") as f:
            f.write("# ch1\n\n<!-- lectures/ch01.pdf p.1 -->\nplain\n")
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf"])
        be = FakeBackend(texts_by_name={"ch01.pdf": ["plain", ""]},
                         media_by_name={"ch01.pdf": [(0, 0), (1, 0)]})

        self.assertEqual(BVI.run(["--workspace", ws, "--materials", mat], backend=be), 0)
        cov = _load(ws, "figure_page_index.json")["wiki_visual_coverage"]
        self.assertEqual((cov["detected"], cov["missing"]), (1, 1))
        self.assertTrue(cov["missing_pages"][0]["anchor_inferred"])
        self.assertEqual(cov["missing_pages"][0]["reason"], "source_page_anchor_missing")

        self.assertEqual(BVI.run(["--workspace", ws, "--materials", mat, "--apply-wiki"],
                                 backend=be), 0)
        repaired = _load(ws, "figure_page_index.json")["wiki_visual_coverage"]
        self.assertEqual((repaired["detected"], repaired["embedded"], repaired["missing"]),
                         (1, 1, 0))
        text0 = open(wiki, encoding="utf-8").read()
        self.assertIn("<!-- lectures/ch01.pdf p.2 -->", text0)
        self.assertEqual(text0.count("<!-- wiki-visual-index:"), 1)

    def test_apply_wiki_enforces_per_chapter_cap_and_lists_every_missing_page(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        # Keep this cap-focused fixture free of answer-only pages. Answer-page
        # deferral is covered by dedicated tests below.
        with open(os.path.join(ws, "references", "quiz_bank.json"), "w", encoding="utf-8") as f:
            json.dump([], f)
        wiki = os.path.join(ws, "references", "wiki", "ch1.md")
        with open(wiki, "w", encoding="utf-8") as f:
            f.write("# ch1\n\n" + "\n\n".join(
                "<!-- lectures/ch01.pdf p.%d -->\np%d" % (n, n) for n in range(1, 32)))
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf"])
        be = FakeBackend(
            texts_by_name={"ch01.pdf": ["plain"] * 31},
            media_by_name={"ch01.pdf": [(1, 0)] * 31})
        rc = BVI.run(["--workspace", ws, "--materials", mat, "--apply-wiki"], backend=be)
        self.assertEqual(rc, 0)
        cov = _load(ws, "figure_page_index.json")["wiki_visual_coverage"]
        self.assertEqual((cov["detected"], cov["embedded"], cov["missing"]), (31, 30, 1))
        self.assertEqual(len(cov["missing_pages"]), 1)             # complete, uncapped manifest
        self.assertEqual(cov["missing_pages"][0]["reason"], "chapter_cap")
        self.assertTrue(any(w.startswith("wiki_visual_cap") for w in cov["warnings"]))

    def test_nul_text_is_reported_with_source_page(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf"])
        be = FakeBackend(texts_by_name={"ch01.pdf": ["diagram\x00labels"]},
                         media_by_name={"ch01.pdf": [(1, 0)]})
        self.assertEqual(BVI.run(["--workspace", ws, "--materials", mat], backend=be), 0)
        warnings = _load(ws, "figure_page_index.json")["warnings"]
        self.assertTrue(any(w.startswith("nul_text: lectures/ch01.pdf p.1") for w in warnings))

    def test_prompt_and_answer_batches_reject_later_malformed_png_without_any_mutation(self):
        for side, bad_name, bad_payload in (
                ("prompt", "bad_crc", BAD_CRC_PNG),
                ("prompt", "bad_zlib", BAD_ZLIB_PNG),
                ("answer", "bad_crc", BAD_CRC_PNG),
                ("answer", "bad_zlib", BAD_ZLIB_PNG),
        ):
            with self.subTest(side=side, malformed=bad_name):
                tmp = tempfile.mkdtemp()
                ws = _mk_workspace(tmp)
                mat = os.path.join(tmp, "mat")
                _mk_materials(mat, ["ch01.pdf", "ch02.pdf"])
                bank_path = os.path.join(ws, "references", "quiz_bank.json")
                bank = json.load(open(bank_path, encoding="utf-8"))
                template_id = "suspect_1" if side == "prompt" else "ansfig_1"
                first = next(row for row in bank if row["id"] == template_id)
                second = copy.deepcopy(first)
                second["id"] = template_id + "_later"
                bank.append(second)
                page = 2 if side == "prompt" else 3
                source = (
                    first.get("source_file") if side == "prompt"
                    else first.get("answer_source_file") or first.get("source_file")
                )
                suspects = [{
                    "id": row["id"], "source_layer": "quiz_bank",
                    "source_file": source, "visual_pages": [page],
                } for row in (first, second)]
                plans = BVI._planned_repair_assets(
                    ws, os.path.join(ws, "references", "assets"),
                    bank, suspects, side,
                )
                old = PNG + b"PREEXISTING"
                with open(plans[0]["full_path"], "wb") as stream:
                    stream.write(old)
                first["assets"] = [{
                    "path": plans[0]["path"],
                    "role": ("question_context" if side == "prompt" else "answer_context"),
                    "type": "page_image", "sha256": hashlib.sha256(old).hexdigest(),
                    "generated_by": BVI._REPAIR_WRITER_ID,
                    "source_layer": "quiz_bank", "source_file": source,
                    "source_page": page, "side": side,
                }]
                with open(bank_path, "w", encoding="utf-8") as stream:
                    json.dump(bank, stream, ensure_ascii=False, indent=2)
                before_bank = copy.deepcopy(bank)
                before_item = copy.deepcopy(first)

                class BadLater(FakeBackend):
                    calls = 0

                    def render_page_png(self, pdf_path, page_index):
                        self.calls += 1
                        return PNG if self.calls == 1 else bad_payload

                base = _default_backend()
                backend = BadLater(base.texts, base.media)
                writer = BVI.apply_suspects if side == "prompt" else BVI.apply_answer_suspects
                with self.assertRaises(SystemExit):
                    writer(
                        ws, mat, bank, suspects, backend,
                        os.path.join(ws, "references", "assets"), [],
                    )
                self.assertEqual(before_bank, bank)
                self.assertEqual(before_item, first)
                self.assertEqual(old, open(plans[0]["full_path"], "rb").read())
                self.assertFalse(os.path.lexists(plans[1]["full_path"]))
                self._assert_no_transaction_temps(ws)

    def test_later_asset_replace_failure_restores_assets_and_in_memory_bank(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf", "ch02.pdf"])
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        bank = json.load(open(bank_path, encoding="utf-8"))
        first = next(row for row in bank if row["id"] == "suspect_1")
        second = copy.deepcopy(first)
        second["id"] = "suspect_replace_later"
        bank.append(second)
        suspects = [{
            "id": row["id"], "source_layer": "quiz_bank",
            "source_file": row["source_file"], "visual_pages": [2],
        } for row in (first, second)]
        plans = BVI._planned_repair_assets(
            ws, os.path.join(ws, "references", "assets"), bank, suspects, "prompt"
        )
        with open(bank_path, "w", encoding="utf-8") as stream:
            json.dump(bank, stream, ensure_ascii=False, indent=2)
        before = copy.deepcopy(bank)
        original_replace = os.replace
        failed = {"done": False}

        def fail_second(source, destination):
            if destination == plans[1]["full_path"] and not failed["done"]:
                failed["done"] = True
                raise OSError("later asset replacement failed")
            return original_replace(source, destination)

        with mock.patch.object(BVI.os, "replace", side_effect=fail_second), \
                self.assertRaises(SystemExit):
            BVI.apply_suspects(
                ws, mat, bank, suspects, _default_backend(),
                os.path.join(ws, "references", "assets"), [],
            )
        self.assertEqual(before, bank)
        self.assertFalse(os.path.lexists(plans[0]["full_path"]))
        self.assertFalse(os.path.lexists(plans[1]["full_path"]))
        self._assert_no_transaction_temps(ws)

    def test_bank_save_failure_rolls_back_run_assets_bank_and_backup(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf", "ch02.pdf"])
        self.assertEqual(BVI.run(
            ["--workspace", ws, "--materials", mat], backend=_default_backend()), 0)
        before = self._workspace_snapshot(ws)
        with mock.patch.object(
                BVI, "_write_json_with_backup", side_effect=OSError("bank save failed")):
            with self.assertRaises(OSError):
                BVI.run(
                    ["--workspace", ws, "--materials", mat, "--apply"],
                    backend=_default_backend(),
                )
        self.assertEqual(before, self._workspace_snapshot(ws))
        self._assert_no_transaction_temps(ws)

    def test_apply_wiki_rejects_later_non_png_before_any_publication(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        with open(os.path.join(ws, "references", "quiz_bank.json"),
                  "w", encoding="utf-8") as stream:
            json.dump([], stream)
        wiki = os.path.join(ws, "references", "wiki", "ch1.md")
        with open(wiki, "w", encoding="utf-8") as stream:
            stream.write(
                "# ch1\n\n<!-- lectures/ch01.pdf p.1 -->\none\n\n"
                "<!-- lectures/ch01.pdf p.2 -->\ntwo\n"
            )
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf"])
        base = FakeBackend(
            texts_by_name={"ch01.pdf": ["one", "two"]},
            media_by_name={"ch01.pdf": [(1, 0), (1, 0)]},
        )
        self.assertEqual(BVI.run(
            ["--workspace", ws, "--materials", mat], backend=base), 0)
        before = self._workspace_snapshot(ws)

        class BadLater(FakeBackend):
            calls = 0

            def render_page_png(self, pdf_path, page_index):
                self.calls += 1
                return PNG if self.calls == 1 else b"later-not-png"

        bad = BadLater(base.texts, base.media)
        with self.assertRaises(SystemExit):
            BVI.run(
                ["--workspace", ws, "--materials", mat, "--apply-wiki"],
                backend=bad,
            )
        self.assertEqual(before, self._workspace_snapshot(ws))
        self._assert_no_transaction_temps(ws)

    def test_apply_wiki_repairs_owned_stale_link_to_canonical_target(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        with open(os.path.join(ws, "references", "quiz_bank.json"),
                  "w", encoding="utf-8") as stream:
            json.dump([], stream)
        source = "lectures/ch01.pdf"
        digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:10]
        target = os.path.join(
            ws, "references", "assets", "wiki_%s_p0001.png" % digest
        )
        with open(target, "wb") as stream:
            stream.write(PNG + b"STALE")
        wrong = os.path.join(ws, "references", "assets", "wrong-existing.png")
        with open(wrong, "wb") as stream:
            stream.write(PNG)
        wiki = os.path.join(ws, "references", "wiki", "ch1.md")
        with open(wiki, "w", encoding="utf-8") as stream:
            stream.write(
                "# ch1\n\n<!-- lectures/ch01.pdf p.1 -->\nvisual\n\n"
                "<!-- wiki-visual-index: lectures/ch01.pdf p.1 -->\n"
                "![old](../assets/wrong-existing.png)\n"
            )
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf"])
        backend = FakeBackend(
            texts_by_name={"ch01.pdf": ["visual"]},
            media_by_name={"ch01.pdf": [(1, 0)]},
        )
        self.assertEqual(BVI.run(
            ["--workspace", ws, "--materials", mat], backend=backend,
        ), 0)
        before = _load(ws, "figure_page_index.json")["wiki_visual_coverage"]
        record = next(row for row in before["pages"] if row["page"] == 1)
        self.assertEqual("missing", record["status"])
        self.assertIn("references/assets/wrong-existing.png", record["declared_assets"])
        self.assertEqual(BVI.run(
            ["--workspace", ws, "--materials", mat, "--apply-wiki"],
            backend=backend,
        ), 0)
        text = open(wiki, encoding="utf-8").read()
        self.assertNotIn("wrong-existing.png", text)
        self.assertIn("../assets/%s" % os.path.basename(target), text)
        self.assertEqual(PNG, open(target, "rb").read())

    def test_malformed_canonical_wiki_png_is_missing_then_refreshed(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        with open(os.path.join(ws, "references", "quiz_bank.json"),
                  "w", encoding="utf-8") as stream:
            json.dump([], stream)
        source = "lectures/ch01.pdf"
        target_rel = BVI._wiki_generated_asset_rel(source, 1)
        target = os.path.join(ws, *target_rel.split("/"))
        with open(target, "wb") as stream:
            stream.write(BAD_ZLIB_PNG)
        wiki = os.path.join(ws, "references", "wiki", "ch1.md")
        with open(wiki, "w", encoding="utf-8") as stream:
            stream.write(
                "# ch1\n\n<!-- lectures/ch01.pdf p.1 -->\nvisual\n\n"
                "<!-- wiki-visual-index: lectures/ch01.pdf p.1 -->\n"
                "![page](../assets/%s)\n" % os.path.basename(target)
            )
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf"])
        backend = FakeBackend(
            texts_by_name={"ch01.pdf": ["visual"]},
            media_by_name={"ch01.pdf": [(1, 0)]},
        )
        self.assertEqual(BVI.run(
            ["--workspace", ws, "--materials", mat], backend=backend,
        ), 0)
        coverage = _load(ws, "figure_page_index.json")["wiki_visual_coverage"]
        self.assertEqual("missing", coverage["pages"][0]["status"])
        self.assertEqual(BVI.run(
            ["--workspace", ws, "--materials", mat, "--apply-wiki"],
            backend=backend,
        ), 0)
        self.assertEqual(PNG, open(target, "rb").read())
        coverage = _load(ws, "figure_page_index.json")["wiki_visual_coverage"]
        self.assertEqual("embedded", coverage["pages"][0]["status"])

    def test_apply_wiki_marker_only_fails_before_render_or_publication(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        with open(os.path.join(ws, "references", "quiz_bank.json"),
                  "w", encoding="utf-8") as stream:
            json.dump([], stream)
        wiki = os.path.join(ws, "references", "wiki", "ch1.md")
        with open(wiki, "w", encoding="utf-8") as stream:
            stream.write(
                "# ch1\n\n<!-- lectures/ch01.pdf p.1 -->\nvisual\n\n"
                "<!-- wiki-visual-index: lectures/ch01.pdf p.1 -->\n"
            )
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf"])
        base = FakeBackend(
            texts_by_name={"ch01.pdf": ["visual"]},
            media_by_name={"ch01.pdf": [(1, 0)]},
        )
        self.assertEqual(BVI.run(
            ["--workspace", ws, "--materials", mat], backend=base), 0)
        before = self._workspace_snapshot(ws)
        base.render_page_png = mock.Mock(
            side_effect=AssertionError("marker preflight must precede rendering")
        )
        with self.assertRaises(SystemExit):
            BVI.run(
                ["--workspace", ws, "--materials", mat, "--apply-wiki"],
                backend=base,
            )
        base.render_page_png.assert_not_called()
        self.assertEqual(before, self._workspace_snapshot(ws))
        self._assert_no_transaction_temps(ws)

    def test_apply_wiki_rejects_marker_only_even_when_page_is_cap_excluded(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        with open(os.path.join(ws, "references", "quiz_bank.json"),
                  "w", encoding="utf-8") as stream:
            json.dump([], stream)
        wiki = os.path.join(ws, "references", "wiki", "ch1.md")
        with open(wiki, "w", encoding="utf-8") as stream:
            stream.write(
                "# ch1\n\n<!-- lectures/ch01.pdf p.1 -->\none\n\n"
                "<!-- lectures/ch01.pdf p.2 -->\ntwo\n\n"
                "<!-- wiki-visual-index: lectures/ch01.pdf p.2 -->\n"
            )
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf"])
        backend = FakeBackend(
            texts_by_name={"ch01.pdf": ["one", "two"]},
            media_by_name={"ch01.pdf": [(1, 0), (1, 0)]},
        )
        self.assertEqual(BVI.run(
            ["--workspace", ws, "--materials", mat], backend=backend,
        ), 0)
        before = self._workspace_snapshot(ws)
        backend.render_page_png = mock.Mock(
            side_effect=AssertionError("all marker contracts precede rendering")
        )
        with self.assertRaises(SystemExit):
            BVI.run(
                ["--workspace", ws, "--materials", mat, "--apply-wiki",
                 "--wiki-page-cap", "1"],
                backend=backend,
            )
        backend.render_page_png.assert_not_called()
        self.assertEqual(before, self._workspace_snapshot(ws))
        self._assert_no_transaction_temps(ws)

    def test_apply_wiki_later_wiki_replace_failure_rolls_back_assets_and_markdown(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        with open(os.path.join(ws, "references", "quiz_bank.json"),
                  "w", encoding="utf-8") as stream:
            json.dump([], stream)
        wiki = os.path.join(ws, "references", "wiki", "ch1.md")
        with open(wiki, "w", encoding="utf-8") as stream:
            stream.write(
                "# ch1\n\n<!-- lectures/ch01.pdf p.1 -->\none\n\n"
                "<!-- lectures/ch01.pdf p.2 -->\ntwo\n"
            )
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf"])
        backend = FakeBackend(
            texts_by_name={"ch01.pdf": ["one", "two"]},
            media_by_name={"ch01.pdf": [(1, 0), (1, 0)]},
        )
        self.assertEqual(BVI.run(
            ["--workspace", ws, "--materials", mat], backend=backend), 0)
        before = self._workspace_snapshot(ws)
        original_replace = os.replace
        failed = {"done": False}

        def fail_wiki(source, destination):
            if destination == wiki and not failed["done"]:
                failed["done"] = True
                raise OSError("wiki replacement failed")
            return original_replace(source, destination)

        with mock.patch.object(BVI.os, "replace", side_effect=fail_wiki), \
                self.assertRaises(SystemExit):
            BVI.run(
                ["--workspace", ws, "--materials", mat, "--apply-wiki"],
                backend=backend,
            )
        self.assertEqual(before, self._workspace_snapshot(ws))
        self._assert_no_transaction_temps(ws)

    def test_out_dir_cannot_redirect_indices_over_asset_or_control_directories(self):
        for relative in ("references/assets", "notebook"):
            with self.subTest(relative=relative):
                tmp = tempfile.mkdtemp()
                ws = _mk_workspace(tmp)
                target_dir = os.path.join(ws, *relative.split("/"))
                os.makedirs(target_dir, exist_ok=True)
                sentinel = os.path.join(target_dir, "figure_page_index.json")
                with open(sentinel, "wb") as stream:
                    stream.write(b"DO-NOT-OVERWRITE")
                with self.assertRaises(SystemExit):
                    BVI.run(
                        ["--workspace", ws, "--out-dir", target_dir],
                        backend=_default_backend(),
                    )
                self.assertEqual(b"DO-NOT-OVERWRITE", open(sentinel, "rb").read())
                self._assert_no_transaction_temps(ws)

    @unittest.skipUnless(os.name == "nt", "Windows junction/reparse regression")
    def test_junction_backed_asset_and_index_parents_are_rejected(self):
        def junction(link, target):
            result = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", link, target],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True,
            )
            if result.returncode != 0 or not BVI.is_link_or_reparse(link):
                self.skipTest("junction creation unavailable: %s" % (
                    result.stderr.strip() or result.stdout.strip()
                ))

        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        mat = os.path.join(tmp, "mat")
        _mk_materials(mat, ["ch01.pdf", "ch02.pdf"])
        asset_root = os.path.join(ws, "references", "assets")
        original_assets = os.path.join(tmp, "original-assets")
        outside_assets = os.path.join(tmp, "outside-assets")
        os.replace(asset_root, original_assets)
        os.makedirs(outside_assets)
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        before_bank = open(bank_path, "rb").read()
        try:
            junction(asset_root, outside_assets)
            with self.assertRaises(SystemExit) as caught:
                BVI.run(
                    ["--workspace", ws, "--materials", mat, "--apply"],
                    backend=_default_backend(),
                )
            self.assertEqual(2, caught.exception.code)
            self.assertEqual([], os.listdir(outside_assets))
            self.assertEqual(before_bank, open(bank_path, "rb").read())
        finally:
            if os.path.lexists(asset_root):
                os.rmdir(asset_root)
            os.replace(original_assets, asset_root)

        tmp2 = tempfile.mkdtemp()
        ws2 = _mk_workspace(tmp2)
        references = os.path.join(ws2, "references")
        outside_references = os.path.join(tmp2, "outside-references")
        os.replace(references, outside_references)
        outside_bank = os.path.join(outside_references, "quiz_bank.json")
        before_bank = open(outside_bank, "rb").read()
        try:
            junction(references, outside_references)
            with self.assertRaises(SystemExit) as caught:
                BVI.run(["--workspace", ws2], backend=_default_backend())
            self.assertEqual(2, caught.exception.code)
            self.assertEqual(before_bank, open(outside_bank, "rb").read())
            self.assertFalse(os.path.lexists(os.path.join(
                outside_references, "figure_page_index.json"
            )))
            self.assertFalse(os.path.lexists(os.path.join(
                outside_references, "image_question_index.json"
            )))
        finally:
            if os.path.lexists(references):
                os.rmdir(references)
            os.replace(outside_references, references)

    def test_second_index_replacement_failure_restores_both_previous_indices(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        self.assertEqual(BVI.run(["--workspace", ws], backend=_default_backend()), 0)
        figure = os.path.join(ws, "references", "figure_page_index.json")
        questions = os.path.join(ws, "references", "image_question_index.json")
        before = {path: open(path, "rb").read() for path in (figure, questions)}
        original_replace = os.replace
        failed = {"done": False}

        def fail_second(source, destination):
            if destination == questions and not failed["done"]:
                failed["done"] = True
                raise OSError("second index replacement failed")
            return original_replace(source, destination)

        with mock.patch.object(BVI.os, "replace", side_effect=fail_second), \
                self.assertRaises(SystemExit):
            BVI.run(["--workspace", ws], backend=_default_backend())
        self.assertEqual(before, {
            path: open(path, "rb").read() for path in (figure, questions)
        })
        self._assert_no_transaction_temps(ws)

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
        self.assertIn("![题面图:", out)                          # default zh label (docs/file-format §4)
        self.assertNotIn("question-side asset", out)             # 默认 zh 模式=纯 题面图，非复合形
        self.assertIn("references/assets/", out)
        self.assertNotIn("\\", out.split("(")[1].split(")")[0])   # renderable POSIX path
        with self.assertRaises(SystemExit) as cm2:
            SQA.run(["--workspace", ws, "--id", "no_such_id"])
        self.assertEqual(cm2.exception.code, 2)
        # a visual item whose asset file is deleted → fail-closed exit 1
        bank = json.load(open(os.path.join(ws, "references", "quiz_bank.json"), encoding="utf-8"))
        q = next(x for x in bank if x["id"] == "suspect_1")
        os.remove(os.path.join(ws, q["assets"][0]["path"]))
        with self.assertRaises(SystemExit) as cm:
            SQA.run(["--workspace", ws, "--id", "suspect_1"])
        self.assertEqual(cm.exception.code, 1)

    def test_show_question_assets_rejects_signature_prefixed_corrupt_png(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        assets = os.path.join(ws, "references", "assets")
        os.makedirs(assets, exist_ok=True)
        relative = "references/assets/corrupt.png"
        with open(os.path.join(ws, *relative.split("/")), "wb") as stream:
            stream.write(BAD_ZLIB_PNG)
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        bank = json.load(open(bank_path, encoding="utf-8"))
        bank.append({
            "id": "corrupt-visual", "chapter": 1, "type": "diagram",
            "question": "Use the image.", "answer": "A",
            "requires_assets": True,
            "assets": [{
                "path": relative, "role": "figure", "type": "crop_image",
            }],
        })
        with open(bank_path, "w", encoding="utf-8") as stream:
            json.dump(bank, stream, ensure_ascii=False, indent=2)

        with self.assertRaises(SystemExit) as caught:
            SQA.run(["--workspace", ws, "--id", "corrupt-visual"])
        self.assertEqual(1, caught.exception.code)

    def test_show_question_assets_accepts_valid_jpeg(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        relative = "references/assets/prompt.jpg"
        full = os.path.join(ws, *relative.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as stream:
            stream.write(VALID_JPEG)
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        bank = json.load(open(bank_path, encoding="utf-8"))
        bank.append({
            "id": "valid-jpeg", "chapter": 1, "type": "diagram",
            "question": "Use the image.", "answer": "A", "requires_assets": True,
            "assets": [{
                "path": relative, "role": "figure", "type": "crop_image",
            }],
        })
        with open(bank_path, "w", encoding="utf-8") as stream:
            json.dump(bank, stream, ensure_ascii=False, indent=2)

        rc, output = self._capture(
            SQA.run, ["--workspace", ws, "--id", "valid-jpeg"]
        )
        self.assertEqual(0, rc)
        self.assertIn(relative, output)

    def test_show_question_assets_rejects_corrupt_jpeg(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        relative = "references/assets/corrupt.jpg"
        full = os.path.join(ws, *relative.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as stream:
            stream.write(b"\xff\xd8\xff\xd9")
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        bank = json.load(open(bank_path, encoding="utf-8"))
        bank.append({
            "id": "corrupt-jpeg", "chapter": 1, "type": "diagram",
            "question": "Use the image.", "answer": "A", "requires_assets": True,
            "assets": [{
                "path": relative, "role": "figure", "type": "crop_image",
            }],
        })
        with open(bank_path, "w", encoding="utf-8") as stream:
            json.dump(bank, stream, ensure_ascii=False, indent=2)

        with self.assertRaises(SystemExit) as caught:
            SQA.run(["--workspace", ws, "--id", "corrupt-jpeg"])
        self.assertEqual(1, caught.exception.code)

    def test_visual_index_foreign_attempt_conflict_fails_before_any_publish(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        bank = json.load(open(bank_path, encoding="utf-8"))
        bank[0].setdefault("assets", []).append({
            "path": "references/assets/a.png", "role": "question_context",
            "type": "crop_image",
        })
        with open(bank_path, "w", encoding="utf-8") as stream:
            json.dump(bank, stream, ensure_ascii=False, indent=2)
        ingest = os.path.join(ws, ".ingest")
        os.makedirs(ingest, exist_ok=True)
        with open(os.path.join(ingest, "content_units.jsonl"), "w", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "unit_id": "foreign-attempt", "chapter_id": "ch02", "kind": "figure",
                "asset_path": "references\\assets\\a.png",
                "asset_role": "student_attempt", "metadata": {},
            }) + "\n")
        sentinels = {}
        for name in ("image_question_index.json", "figure_page_index.json"):
            path = os.path.join(ws, "references", name)
            with open(path, "wb") as stream:
                stream.write(("sentinel-" + name).encode("ascii"))
            sentinels[path] = open(path, "rb").read()
        sentinels[bank_path] = open(bank_path, "rb").read()
        wiki_path = os.path.join(ws, "references", "wiki", "ch1.md")
        sentinels[wiki_path] = open(wiki_path, "rb").read()
        with self.assertRaises(SystemExit) as caught:
            BVI.run(["--workspace", ws], backend=_default_backend())
        self.assertEqual(2, caught.exception.code)
        for path, before in sentinels.items():
            self.assertEqual(before, open(path, "rb").read(), path)

    def test_visual_index_does_not_count_attempt_tainted_manual_wiki_image(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        bank = json.load(open(bank_path, encoding="utf-8"))
        bank.append({
            "id": "attempt-audit", "chapter": 2, "type": "subjective",
            "question": "Audit only", "answer": "", "source": "material",
            "assets": [{"path": "references/assets/a.png", "role": "student_attempt"}],
        })
        with open(bank_path, "w", encoding="utf-8") as stream:
            json.dump(bank, stream, ensure_ascii=False, indent=2)
        wiki_path = os.path.join(ws, "references", "wiki", "ch1.md")
        with open(wiki_path, "w", encoding="utf-8") as stream:
            stream.write(
                "# Chapter 1\n\n<!-- lectures/ch01.pdf p.2 -->\n"
                "![lectures/ch01.pdf 第 2 页图示](../assets/a.png)\n"
            )
        materials = os.path.join(tmp, "materials")
        _mk_materials(materials, ["ch01.pdf", "ch02.pdf"])
        self.assertEqual(
            0, BVI.run(["--workspace", ws, "--materials", materials],
                       backend=_default_backend())
        )
        coverage = _load(ws, "figure_page_index.json")["wiki_visual_coverage"]
        page = next(row for row in coverage["pages"]
                    if row.get("source_file") == "lectures/ch01.pdf" and row.get("page") == 2)
        self.assertEqual("missing", page["status"])
        self.assertEqual([], page["asset_paths"])
        self.assertIn("references/assets/a.png", page["declared_assets"])

    def test_visual_index_does_not_count_undeclared_hardlink_alias_of_attempt(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        assets = os.path.join(ws, "references", "assets")
        os.makedirs(assets, exist_ok=True)
        attempt = os.path.join(assets, "attempt.png")
        alias = os.path.join(assets, "official-alias.png")
        with open(attempt, "wb") as stream:
            stream.write(PNG)
        try:
            os.link(attempt, alias)
        except (OSError, NotImplementedError):
            self.skipTest("hard links unavailable")
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        bank = json.load(open(bank_path, encoding="utf-8"))
        bank.append({
            "id": "attempt-audit", "chapter": 2, "type": "subjective",
            "question": "Audit only", "answer": "", "source": "material",
            "assets": [{"path": "references/assets/attempt.png",
                        "role": "student_attempt"}],
        })
        with open(bank_path, "w", encoding="utf-8") as stream:
            json.dump(bank, stream, ensure_ascii=False, indent=2)
        wiki_path = os.path.join(ws, "references", "wiki", "ch1.md")
        with open(wiki_path, "w", encoding="utf-8") as stream:
            stream.write(
                "# Chapter 1\n\n<!-- lectures/ch01.pdf p.2 -->\n"
                "![lectures/ch01.pdf 第 2 页图示](../assets/official-alias.png)\n"
            )
        materials = os.path.join(tmp, "materials")
        _mk_materials(materials, ["ch01.pdf", "ch02.pdf"])
        self.assertEqual(
            0, BVI.run(["--workspace", ws, "--materials", materials],
                       backend=_default_backend())
        )
        coverage = _load(ws, "figure_page_index.json")["wiki_visual_coverage"]
        page = next(row for row in coverage["pages"]
                    if row.get("source_file") == "lectures/ch01.pdf"
                    and row.get("page") == 2)
        self.assertEqual("missing", page["status"])
        self.assertEqual([], page["asset_paths"])
        self.assertIn("references/assets/official-alias.png", page["declared_assets"])

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

    def test_show_question_assets_global_attempt_taint_is_order_independent(self):
        for reverse in (False, True):
            tmp = tempfile.mkdtemp()
            ws = _mk_workspace(tmp)
            path = os.path.join(ws, "references", "assets", "shared.png")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as stream:
                stream.write(PNG)
            bank_path = os.path.join(ws, "references", "quiz_bank.json")
            bank = json.load(open(bank_path, encoding="utf-8"))
            rows = [
                {"id": "official", "chapter": 1, "type": "subjective",
                 "question": "Use the figure.", "answer": "A", "source": "material",
                 "requires_assets": True,
                 "assets": [{"path": "references/assets/shared.png",
                              "role": "question_context", "type": "crop_image"}]},
                {"id": "attempt", "chapter": 1, "type": "subjective",
                 "question": "Audit row", "answer": "", "source": "material",
                 "assets": [{"path": "references\\assets\\shared.png",
                              "role": "student_attempt", "type": "crop_image"}]},
            ]
            if reverse:
                rows.reverse()
            json.dump(bank + rows, open(bank_path, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
            with self.assertRaises(SystemExit) as caught:
                SQA.run(["--workspace", ws, "--id", "official"])
            self.assertEqual(1, caught.exception.code)

    def test_show_question_assets_never_prints_attempt_only_asset(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        path = os.path.join(ws, "references", "assets", "attempt.png")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as stream:
            stream.write(PNG)
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        bank = json.load(open(bank_path, encoding="utf-8"))
        bank.append({
            "id": "attempt_only", "chapter": 1, "type": "subjective",
            "question": "Self-contained text prompt.", "answer": "A", "source": "material",
            "assets": [{"path": "references/assets/attempt.png",
                        "role": "student_attempt", "type": "crop_image"}],
        })
        json.dump(bank, open(bank_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        rc, out = self._capture(
            SQA.run, ["--workspace", ws, "--id", "attempt_only", "--with-answer"])
        self.assertEqual(0, rc)
        self.assertNotIn("attempt.png", out)

    def test_show_target_quiz_is_tainted_by_foreign_chapter_content_unit(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        asset_path = os.path.join(ws, "references", "assets", "foreign-shared.png")
        os.makedirs(os.path.dirname(asset_path), exist_ok=True)
        with open(asset_path, "wb") as stream:
            stream.write(PNG)
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        bank = json.load(open(bank_path, encoding="utf-8"))
        bank.append({
            "id": "target_q", "chapter": 1, "type": "subjective",
            "question": "Use the target figure.", "answer": "A", "source": "material",
            "requires_assets": True,
            "assets": [{"path": "references/assets/foreign-shared.png",
                        "role": "question_context", "type": "crop_image"}],
        })
        json.dump(bank, open(bank_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        ingest = os.path.join(ws, ".ingest")
        os.makedirs(ingest, exist_ok=True)
        with open(os.path.join(ingest, "content_units.jsonl"), "w", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "unit_id": "unit_foreign_attempt", "chapter_id": "ch02",
                "kind": "figure", "asset_path": "references\\assets\\foreign-shared.png",
                "asset_role": "student_attempt", "metadata": {},
            }) + "\n")
        with self.assertRaises(SystemExit) as caught:
            SQA.run(["--workspace", ws, "--id", "target_q"])
        self.assertEqual(1, caught.exception.code)

    @unittest.skipUnless(os.name == "nt", "Windows physical identity folds path case")
    def test_show_rejects_same_item_windows_case_alias_across_prompt_answer(self):
        tmp = tempfile.mkdtemp()
        ws = _mk_workspace(tmp)
        path = os.path.join(ws, "references", "assets", "Case.png")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as stream:
            stream.write(PNG)
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        bank = json.load(open(bank_path, encoding="utf-8"))
        bank.append({
            "id": "case_alias", "chapter": 1, "type": "subjective",
            "question": "Use the figure.", "answer": "A", "source": "material",
            "assets": [
                {"path": "references/assets/Case.png", "role": "question_context",
                 "type": "crop_image"},
                {"path": "references/assets/case.png", "role": "worked_solution",
                 "type": "crop_image"},
            ],
        })
        json.dump(bank, open(bank_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        with self.assertRaises(SystemExit) as caught:
            SQA.run(["--workspace", ws, "--id", "case_alias"])
        self.assertEqual(1, caught.exception.code)

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

    def test_apply_writers_reject_nonstandard_workspace_roots_before_mutation(self):
        for action, relative_root in (("--apply", "notebook"),
                                      ("--apply-wiki", ".ingest")):
            with self.subTest(action=action, relative_root=relative_root):
                tmp = tempfile.mkdtemp()
                ws, _bank = self._ws_with(tmp, [
                    {"id": "root_guard", "chapter": 1, "type": "subjective",
                     "question": "See the figure.", "source": "material",
                     "ai_generated": False, "source_file": "lectures/ch01.pdf",
                     "source_pages": [2]},
                ])
                mat = os.path.join(tmp, "mat")
                _mk_materials(mat, ["ch01.pdf", "ch02.pdf"])
                self.assertEqual(
                    0,
                    BVI.run(["--workspace", ws, "--materials", mat],
                            backend=_default_backend(), _state_locked=True),
                )
                unsafe_root = os.path.join(ws, relative_root)
                os.makedirs(unsafe_root, exist_ok=True)
                with open(os.path.join(unsafe_root, "sentinel.bin"), "wb") as stream:
                    stream.write(b"DO-NOT-TOUCH")

                def snapshot():
                    result = {}
                    for parent, _dirs, files in os.walk(ws):
                        for filename in files:
                            path = os.path.join(parent, filename)
                            with open(path, "rb") as stream:
                                result[os.path.relpath(path, ws)] = stream.read()
                    return result

                before = snapshot()
                backend = _default_backend()
                backend.render_page_png = mock.Mock(
                    side_effect=AssertionError("render must not run for an unsafe asset root")
                )
                with self.assertRaises(SystemExit) as cm:
                    BVI.run([
                        "--workspace", ws, "--materials", mat, action,
                        "--asset-root", unsafe_root,
                    ], backend=backend, _state_locked=True)
                self.assertEqual(2, cm.exception.code)
                backend.render_page_png.assert_not_called()
                self.assertEqual(before, snapshot())

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
        home = os.path.join(
            os.path.dirname(ws), ".%s-examprep-home" % os.path.basename(ws)
        )
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "validate_workspace.py"), ws],
                           capture_output=True, text=True, encoding="utf-8",
                           env=dict(os.environ, EXAMPREP_HOME=home))
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
        self.assertLess(src.index("scripts/ingest.py"), src.index("Three-sided visual cross-check"))

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
