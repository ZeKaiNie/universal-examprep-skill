# -*- coding: utf-8 -*-
"""A3 tests — homework/solution ingest: file classification, Q/A pairing across separate PDFs,
inline solutions, provenance, source_type tagging, visual dependence, fail-loud orphans."""
import json
import io
import hashlib
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import build_raw_input_from_workspace as B   # noqa: E402
from material_generation import build_pending_generation  # noqa: E402

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63f8ffff3f0005fe02fe0def46b80000000049454e44ae426082"
)


class FakeBackend(object):
    name = "fake"

    def __init__(self, texts_by_name):
        self.texts = texts_by_name

    def can_text(self):
        return True

    def can_render(self):
        return True

    def page_texts(self, pdf_path):
        return self.texts[os.path.basename(pdf_path)]

    def render_page_png(self, pdf_path, page_index):
        return PNG

    def page_layout(self, pdf_path, page_index):
        """Minimal one-item geometry for strict crop-only asset fixtures."""
        text = self.page_texts(pdf_path)[page_index]
        return {
            "page_bbox": [0.0, 0.0, 612.0, 792.0],
            "text_blocks": [{
                "bbox": [36.0, 36.0, 576.0, 756.0],
                "text": text,
            }],
            "images": [],
        }

    def render_page_clip_png(self, pdf_path, page_index, bbox):
        return PNG


class LayoutBackend(FakeBackend):
    """Fixture backend that proves crop calls stay distinct from whole pages."""

    def __init__(self, fixture):
        super().__init__(fixture["texts"])
        self.layouts = fixture["layouts"]
        self.clip_calls = []
        self.page_calls = []

    def page_layout(self, pdf_path, page_index):
        layouts = self.layouts.get(os.path.basename(pdf_path))
        if layouts is None:
            return super().page_layout(pdf_path, page_index)
        return layouts[page_index]

    def render_page_clip_png(self, pdf_path, page_index, bbox):
        self.clip_calls.append((os.path.basename(pdf_path), page_index + 1, list(bbox)))
        return PNG

    def render_page_png(self, pdf_path, page_index):
        self.page_calls.append((os.path.basename(pdf_path), page_index + 1))
        return PNG


HW1 = ["Problem 1\n求栈的出栈顺序。\n\nProblem 2\n给出队列复杂度并证明。",
       "Problem 3\nShade the region shown at right in the Venn diagram."]
HW1_SOL = ["Problem 1\n答案：LIFO 顺序。\n\nProblem 2\n答案：O(1)，证明略。"]
HW2_INLINE = ["第1题\n解释二叉搜索树。\nSolution\n中序遍历有序。\n\n第2题\n无答案的题。"]
ORPHAN_SOL = ["Problem 1\n这是找不到题面的答案。"]


def _mk(tmp, names_texts):
    mat = os.path.join(tmp, "mat")
    os.makedirs(os.path.join(mat, "homework"), exist_ok=True)
    fake = {}
    for name, pages in names_texts.items():
        path = os.path.join(mat, "homework", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"%PDF-fake")
        fake[os.path.basename(name)] = pages
    return mat, FakeBackend(fake)


_RUNTIME_IDENTITY = B.exam_start._capture_runtime_identity()
_CONFIRMED_FULL_PAIRS = set()


def _confirm_full_workspace(workspace, materials):
    """Authorize the exact pair used by an in-process material-builder fixture."""
    workspace = os.path.abspath(workspace)
    materials = os.path.abspath(materials)
    home = os.path.join(os.path.dirname(workspace), ".examprep-home")
    key = (os.path.normcase(workspace), os.path.normcase(materials))
    environment = {"EXAMPREP_HOME": home}
    if key in _CONFIRMED_FULL_PAIRS:
        return environment
    output = io.StringIO()
    with mock.patch.dict(os.environ, environment), mock.patch.object(
            B.exam_start, "_capture_runtime_identity",
            return_value=_RUNTIME_IDENTITY), redirect_stdout(output):
        code = B.exam_start.run([
            "confirm", "--course", "homework-ingest-fixture",
            "--materials", materials, "--workspace", workspace,
            "--mode", "from_scratch", "--time-budget", "le1d",
            "--language", "en", "--processing-mode", "full", "--json",
        ])
    if code != 0:
        raise AssertionError(output.getvalue())
    _CONFIRMED_FULL_PAIRS.add(key)
    return environment


def _run(mat, backend, extra=None):
    extra = list(extra or [])
    if "--asset-root" in extra:
        asset_root = extra[extra.index("--asset-root") + 1]
        workspace = os.path.dirname(os.path.dirname(os.path.abspath(asset_root)))
    else:
        workspace = os.path.join(os.path.dirname(os.path.abspath(mat)), "workspace")
    out = os.path.join(workspace, ".ingest", "source_raw_input.json")
    report_path = os.path.join(workspace, ".ingest", "parse_report.json")
    environment = _confirm_full_workspace(workspace, mat)
    argv = ["--materials", mat, "--out", out,
            "--report", report_path] + extra
    args = B.build_arg_parser().parse_args(argv)
    with mock.patch.dict(os.environ, environment), mock.patch.object(
            B.exam_start, "_capture_runtime_identity",
            return_value=_RUNTIME_IDENTITY):
        code, payload, report = B.run(
            args, backend=backend, publication_workspace=workspace,
        )
    return code, payload, report


HW7_ROSTER_LAYOUT = [
    ("5.3.1", 2, [124.63200378417969, 0.0, 486.44677734375, 501.4912109375]),
    ("5.3.3", 4, [55.180328369140625, 144.0, 560.6307373046875, 402.9419860839844]),
    ("5.4.2", 5, [54.0, 108.00003051757812, 558.3735961914062, 527.638916015625]),
    ("5.5.2", 7, [89.71269989013672, 144.0, 524.2877807617188, 417.6986999511719]),
    ("5.5.5", 8, [90.0, 360.0, 522.57421875, 570.2319946289062]),
    ("5.6.2", 9, [54.0, 468.0000305175781, 553.2578125, 782.1644287109375]),
    ("5.6.6", 10, [54.0, 576.0000610351562, 557.4240112304688, 784.1573486328125]),
    ("5.7.4", 11, [55.180328369140625, 287.9999694824219, 557.0831298828125, 569.8170776367188]),
    ("5.7.12", 12, [90.0, 251.99996948242188, 520.472412109375, 645.0399780273438]),
    ("5.8.2", 14, [91.18032836914062, 10.02691650390625, 524.0361328125, 388.71630859375]),
    ("5.8.6", 16, [124.9573974609375, 10.986480712890625, 486.0, 485.6005859375]),
    ("5.8.8", 18, [55.180328369140625, 325.03778076171875, 557.951904296875, 539.445068359375]),
]


def _hw7_roster_fixture():
    """Synthetic layout facts modeled on the 12-problem HW7 regression."""
    roster = "Homework 7\n" + "\n".join(
        "%d. Problem %s" % (index, number)
        for index, (number, unused_page, unused_bbox) in enumerate(
            HW7_ROSTER_LAYOUT, 1
        )
    )
    texts = [roster] + ["student work page %d" % page for page in range(2, 20)]
    solution = "\n\n".join(
        "Problem %s\nAnswer: official solution %s" % (number, number)
        for number, unused_page, unused_bbox in HW7_ROSTER_LAYOUT
    )
    page_bbox = [0.0, 0.0, 612.0, 792.0]
    by_page = {page: (number, bbox) for number, page, bbox in HW7_ROSTER_LAYOUT}
    answer_text = {
        2: [[55.0, 510.0, 545.0, 780.0]],
        4: [[55.0, 410.0, 545.0, 780.0]],
        5: [[55.0, 535.0, 545.0, 780.0]],
        7: [[55.0, 425.0, 545.0, 780.0]],
        # These next three boxes cross the raw crop boundary by less than 8pt.
        # The inset gate admits the prompt raster without admitting handwriting.
        8: [[55.0, 567.54, 545.0, 780.0]],
        9: [[55.0, 450.0, 545.0, 474.42]],
        10: [[55.0, 550.0, 545.0, 581.0]],
        11: [[55.0, 575.0, 545.0, 780.0]],
        12: [[55.0, 650.0, 545.0, 780.0]],
        14: [[55.0, 395.0, 545.0, 780.0]],
        16: [[55.0, 492.0, 545.0, 780.0]],
        18: [[55.0, 545.0, 545.0, 780.0]],
    }
    layouts = []
    for page in range(1, 20):
        if page == 1:
            layouts.append({
                "page_bbox": list(page_bbox), "images": [],
                "text_boxes": [[40.0, 40.0, 560.0, 300.0]],
            })
            continue
        images = []
        for row in range(3):
            for column in range(3):
                images.append({
                    "image_id": "repeated-graph-paper",
                    "bbox": [
                        204.0 * column, 264.0 * row,
                        204.0 * (column + 1), 264.0 * (row + 1),
                    ],
                })
        if page in by_page:
            number, bbox = by_page[page]
            if number == "5.3.1":
                split = 320.2018127441406
                images.extend([
                    {"image_id": "prompt-%s-a" % number,
                     "bbox": [bbox[0], bbox[1], bbox[2], split]},
                    {"image_id": "prompt-%s-b" % number,
                     "bbox": [bbox[0], split, bbox[2], bbox[3]]},
                ])
            else:
                images.append({"image_id": "prompt-%s" % number, "bbox": list(bbox)})
        layouts.append({
            "page_bbox": list(page_bbox), "images": images,
            "text_boxes": list(answer_text.get(page, [[55.0, 20.0, 545.0, 780.0]])),
        })
    return {
        "texts": {"hw7.pdf": texts, "hw7_sol.pdf": [solution]},
        "layouts": {"hw7.pdf": layouts},
        "expected": {
            number: {"page": page, "bbox": list(bbox)}
            for number, page, bbox in HW7_ROSTER_LAYOUT
        },
    }


class HomeworkIngest(unittest.TestCase):
    def test_separate_solution_pdf_paired(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw1.pdf": HW1, "hw1_sol.pdf": HW1_SOL})
        code, payload, report = _run(mat, be)
        self.assertEqual(code, 0, report)
        bank = {q["id"]: q for q in payload["quiz_bank"]}
        q1 = bank["hw_homework_hw1_1"]
        self.assertEqual(q1["source_type"], "homework")
        self.assertIn("出栈顺序", q1["question"])
        self.assertIn("LIFO", q1["answer"])                       # 题答分离 PDF 自动配对
        self.assertEqual(q1["answer_source_file"], "homework/hw1_sol.pdf")
        self.assertEqual(q1["source_file"], "homework/hw1.pdf")
        self.assertEqual(q1["source_pages"], [1])
        self.assertEqual(report["homework_pairs"], [["homework/hw1_sol.pdf", "homework/hw1.pdf"]])
        self.assertEqual(report["homework_problems"], 3)
        self.assertEqual(report["homework_answered"], 2)

    def test_unanswered_problem_fail_loud(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw1.pdf": HW1, "hw1_sol.pdf": HW1_SOL})
        code, payload, report = _run(mat, be)
        q3 = next(q for q in payload["quiz_bank"] if q["id"].endswith("_hw1_3"))
        self.assertNotIn("answer", q3)
        self.assertEqual(q3["answer_status"], "unknown")
        self.assertTrue(any(w.startswith("hw_unanswered: hw_homework_hw1_3") for w in report["warnings"]))

    def test_inline_solution_and_cn_markers(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"作业2.pdf": HW2_INLINE})
        code, payload, report = _run(mat, be)
        self.assertEqual(code, 0, report)
        bank = {q["id"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        q1 = next(q for q in bank.values() if q["id"].endswith("_1"))
        self.assertIn("二叉搜索树", q1["question"])
        self.assertIn("中序遍历有序", q1["answer"])                # inline Solution 归属前一题
        self.assertNotIn("Solution", q1["question"].split("Solution")[0] + "")  # 题面在 Solution 前截断
        q2 = next(q for q in bank.values() if q["id"].endswith("_2"))
        self.assertEqual(q2["answer_status"], "unknown")

    def test_unpaired_solution_file_warns_and_skips(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw9_solutions.pdf": ORPHAN_SOL})
        code, payload, report = _run(mat, be)
        self.assertTrue(any(w.startswith("hw_unpaired_solution_file") for w in report["warnings"]))
        self.assertFalse([q for q in payload["quiz_bank"] if q.get("source_type") == "homework"])

    def test_visual_dependent_homework_renders_assets(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"ch01/hw1.pdf": HW1, "ch01/hw1_sol.pdf": HW1_SOL})
        asset_root = os.path.join(tmp, "ws", "references", "assets")
        code, payload, report = _run(mat, be, ["--asset-root", asset_root])
        q3 = next(q for q in payload["quiz_bank"] if q["id"].endswith("_hw1_3"))
        self.assertIs(q3["requires_assets"], True)                # Venn/shown at right → 图依赖
        self.assertTrue(q3["assets"])
        self.assertEqual(q3["assets"][0]["role"], "question_context")
        self.assertTrue(os.path.isfile(os.path.join(asset_root, os.path.basename(q3["assets"][0]["path"]))))

    def test_extract_homework_never_disables(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw1.pdf": HW1, "hw1_sol.pdf": HW1_SOL})
        code, payload, report = _run(mat, be, ["--extract-homework", "never"])
        self.assertFalse([q for q in payload["quiz_bank"] if q.get("source_type") == "homework"])

    def test_lecture_extraction_unaffected(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw1.pdf": HW1})
        with open(os.path.join(mat, "lec_ch01.pdf"), "wb") as f:   # 讲义在根目录（不在 homework/ 里）
            f.write(b"%PDF-fake")
        be.texts["lec_ch01.pdf"] = ["Example 1.1 Problem\n求和。\nExample 1.1 Solution\n答案 3。"]
        code, payload, report = _run(mat, be)
        ids = [q["id"] for q in payload["quiz_bank"]]
        self.assertTrue(any(i.startswith("lecture_example_1_1") for i in ids))   # lecture 管线原样
        self.assertIn("hw_homework_hw1_1", ids)

    def test_duplicate_problem_number_kept_first(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw3.pdf": ["Problem 1\n第一处。\n\nProblem 1\n重复标记。"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)
        self.assertIn("第一处", hw[0]["question"])
        self.assertTrue(any(w.startswith("hw_duplicate_problem") for w in report["warnings"]))

    def test_output_reaches_validator_and_unresolved_review_blocks_readiness(self):
        # e2e: builder → ingest.py → validate_workspace.py（真 CLI），homework 项带标签通过校验
        import subprocess
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {
            "hw1_ch01.pdf": HW1,
            "hw1_ch01_sol.pdf": HW1_SOL,
        })
        ws = os.path.join(tmp, "ws")
        raw = os.path.join(ws, ".ingest", "source_raw_input.json")
        asset_root = os.path.join(ws, "references", "assets")
        code, payload, report = _run(
            mat, be, ["--asset-root", asset_root]
        )
        self.assertEqual(code, 0, report)
        report_path = os.path.join(ws, ".ingest", "parse_report.json")
        pending = build_pending_generation(
            hashlib.sha256(B._publication_json_bytes(payload)).hexdigest(),
            hashlib.sha256(B._publication_json_bytes(report)).hexdigest(),
            payload, report, None,
        )
        B._publish_builder_transaction((
            (report_path, report),
            (raw, payload),
            (os.path.join(ws, ".ingest", "material_build_pending.json"), pending),
        ))
        environment = _confirm_full_workspace(ws, mat)
        r1 = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "ingest.py"),
                             "-i", raw, "-o", ws], capture_output=True,
                            text=True, encoding="utf-8",
                            env={**os.environ, **environment})
        self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
        bank = json.load(open(os.path.join(ws, "references", "quiz_bank.json"), encoding="utf-8"))
        hw = [q for q in bank if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 3)                              # source_type 穿 ingest 存活
        r2 = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "validate_workspace.py"), ws],
                            capture_output=True, text=True, encoding="utf-8",
                            env={**os.environ, **environment})
        self.assertEqual(r2.returncode, 1, r2.stdout + r2.stderr)
        self.assertIn("blocked", r2.stdout.lower())

    def test_classifier_pairs_variants(self):
        hw, pairing = B.classify_homework_files(
            ["homework/hw1.pdf", "homework/hw1_sol.pdf", "homework/HW2.pdf",
             "homework/HW2_Answers.pdf", "homework/作业3.pdf", "homework/作业3答案.pdf",
             "lectures/ch01.pdf"])
        self.assertEqual(sorted(hw), ["homework/HW2.pdf", "homework/hw1.pdf", "homework/作业3.pdf"])
        self.assertEqual(pairing["homework/hw1_sol.pdf"], "homework/hw1.pdf")
        self.assertEqual(pairing["homework/HW2_Answers.pdf"], "homework/HW2.pdf")
        self.assertEqual(pairing["homework/作业3答案.pdf"], "homework/作业3.pdf")

    # ---- regression guards for Codex round-1 (7 findings) ----

    def test_bare_answer_word_not_a_marker(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw4.pdf": ["Problem 1\nAnswer the following questions about stacks.\n更多题面内容在此。"]})
        code, payload, report = _run(mat, be)
        q = next(x for x in payload["quiz_bank"] if x.get("source_type") == "homework")
        self.assertIn("Answer the following", q["question"])      # 没被裁成 inline 答案
        self.assertEqual(q["answer_status"], "unknown")

    def test_subdir_files_get_distinct_ids(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        for sub in ("week1", "week2"):
            os.makedirs(os.path.join(mat, sub), exist_ok=True)
            with open(os.path.join(mat, sub, "hw1.pdf"), "wb") as f:
                f.write(b"%PDF-fake")
        be = FakeBackend({"hw1.pdf": ["Problem 1\n本周题面内容足够长了吧。"]})
        code, payload, report = _run(mat, be)
        ids = sorted(q["id"] for q in payload["quiz_bank"] if q.get("source_type") == "homework")
        self.assertEqual(len(ids), 2)
        self.assertNotEqual(ids[0], ids[1])                       # week1/week2 不同 id

    def test_chapter_only_when_stated(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw5.pdf": ["Problem 1\n本题考察第 3 章 的内容，请作答完整过程。",
                                        "Problem 2\n没有章节线索的题面文字内容。"]})
        code, payload, report = _run(mat, be)
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertEqual(hw[1]["chapter"], 3)                     # 题文明说 → 标 chapter
        self.assertNotIn("chapter", hw[2])                        # 不硬编（作业号 ≠ 章节号）

    def test_hw1_solution_never_pairs_hw10(self):
        hw, pairing = B.classify_homework_files(["homework/hw10.pdf", "homework/hw1_sol.pdf"])
        self.assertIsNone(pairing["homework/hw1_sol.pdf"])        # 数字边界：hw1 ≠ hw10

    def test_solution_file_prefers_answer_slice(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw6.pdf": ["Problem 1\n题面：求栈顺序的完整过程。"],
                            "hw6_sol.pdf": ["Problem 1\n题面复述而已。\nAnswer 1: 真正的答案是 LIFO。"]})
        code, payload, report = _run(mat, be)
        q = next(x for x in payload["quiz_bank"] if x.get("source_type") == "homework")
        self.assertIn("LIFO", q["answer"])                        # 答案段优先于题面复述
        self.assertFalse(q["answer"].startswith("Problem 1"))

    def test_marker_only_prompt_becomes_page_reference(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"ch01/hw7.pdf": ["Problem 1\n"]})  # 只有标题，真题面是页上的图
        asset_root = os.path.join(tmp, "ws", "references", "assets")
        code, payload, report = _run(mat, be, ["--asset-root", asset_root])
        q = next(x for x in payload["quiz_bank"] if x.get("source_type") == "homework")
        self.assertEqual(q["question_text_status"], "page_reference")
        self.assertIs(q["requires_assets"], True)
        self.assertTrue(q["assets"])                              # 原页已渲染挂上

    def test_solutions_directory_companion(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(os.path.join(mat, "homework"), exist_ok=True)
        os.makedirs(os.path.join(mat, "solutions"), exist_ok=True)
        with open(os.path.join(mat, "homework", "hw1.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        with open(os.path.join(mat, "solutions", "hw1.pdf"), "wb") as f:
            f.write(b"%PDF-fake")

        class DirBackend(FakeBackend):
            def page_texts(self, pdf_path):
                if "solutions" in pdf_path.replace("\\", "/"):
                    return ["Problem 1\nAnswer 1: 目录版答案在此。"]
                return ["Problem 1\n目录版题面内容足够长。"]
        be = DirBackend({})
        code, payload, report = _run(mat, be)
        q = next(x for x in payload["quiz_bank"] if x.get("source_type") == "homework")
        self.assertIn("目录版答案", q["answer"])                   # solutions/ 目录伴随被识别配对

    # ---- regression guards for Codex round-2 (6 findings) ----

    def test_same_basename_pairs_within_directory(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        for sub in ("week1", "week2"):
            os.makedirs(os.path.join(mat, sub), exist_ok=True)
            with open(os.path.join(mat, sub, "hw1.pdf"), "wb") as f:
                f.write(b"%PDF-fake")
            with open(os.path.join(mat, sub, "hw1_sol.pdf"), "wb") as f:
                f.write(b"%PDF-fake")

        class WeekBackend(FakeBackend):
            def page_texts(self, pdf_path):
                p = pdf_path.replace("\\", "/")
                week = "week1" if "week1" in p else "week2"
                if "sol" in p:
                    return ["Problem 1\nAnswer 1: %s 的答案。" % week]
                return ["Problem 1\n%s 的题面内容足够长。" % week]
        code, payload, report = _run(mat, WeekBackend({}))
        hw = {q["source_file"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertIn("week1 的答案", hw["week1/hw1.pdf"]["answer"])   # 同目录配对，不跨目录串
        self.assertIn("week2 的答案", hw["week2/hw1.pdf"]["answer"])

    def test_long_stem_ids_stay_unique(self):
        base = "very_long_lms_export_name_" + "x" * 50
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(os.path.join(mat, "hw"), exist_ok=True)
        names = [base + "_alpha.pdf", base + "_beta.pdf"]
        for n in names:
            with open(os.path.join(mat, "hw", n), "wb") as f:
                f.write(b"%PDF-fake")

        class LongBackend(FakeBackend):
            def page_texts(self, pdf_path):
                return ["Problem 1\n长文件名题面内容足够长。"]
        code, payload, report = _run(mat, LongBackend({}))
        ids = [q["id"] for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)                        # 截断后哈希后缀保唯一

    def test_short_but_complete_prompt_stays_full(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw8.pdf": ["Problem 1\n2+2=?\n\nProblem 2\n求导 x^2。"]})
        code, payload, report = _run(mat, be)
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertEqual(hw[1]["question_text_status"], "full")   # 短而完整 ≠ 图片题
        self.assertNotIn("requires_assets", hw[1])
        self.assertEqual(hw[2]["question_text_status"], "full")

    def test_long_question_not_silently_truncated(self):
        long_q = "Problem 1\n" + "很长的编程大作业题面。" * 300
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw9.pdf": [long_q]})
        code, payload, report = _run(mat, be)
        q = next(x for x in payload["quiz_bank"] if x.get("source_type") == "homework")
        self.assertGreater(len(q["question"]), 2000)              # 保留全文，不静默截断

    def test_separated_ps_number_classified(self):
        hw, pairing = B.classify_homework_files(["exports/ps 1.pdf", "exports/PS-2.pdf", "exports/ps_3.pdf"])
        self.assertEqual(len(hw), 3)                              # ps 1 / PS-2 / ps_3 都识别为作业

    def test_answer_blank_line_not_official_answer(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw10a.pdf": ["Problem 1\n计算 2+2。\nAnswer: ________\n\nProblem 2\n下一题题面。"]})
        code, payload, report = _run(mat, be)
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertNotIn("answer", hw[1])                         # 填空线不是官方答案
        self.assertEqual(hw[1]["answer_status"], "unknown")

    # ---- regression guards for Codex round-3 (4 findings) ----

    def test_numbered_answer_key_headings_pair(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw11.pdf": ["Problem 1\n第一题题面内容。\n\nProblem 2\n第二题题面内容。"],
                            "hw11_sol.pdf": ["1. Answer: 第一题的官方答案。\n\n2) Solution: 第二题的官方答案。"]})
        code, payload, report = _run(mat, be)
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertIn("第一题的官方答案", hw[1]["answer"])          # 「1. Answer:」编号在标记前也能配上
        self.assertIn("第二题的官方答案", hw[2]["answer"])          # 「2) Solution:」同理

    def test_zero_padded_stem_pairs(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"HW01.pdf": ["Problem 1\n零填充作业的题面内容。"],
                            "HW1_sol.pdf": ["Problem 1\nAnswer 1: 零填充也要配上的答案。"]})
        code, payload, report = _run(mat, be)
        q = next(x for x in payload["quiz_bank"] if x.get("source_type") == "homework")
        self.assertIn("零填充也要配上", q["answer"])                # HW01 ↔ HW1_sol
        hw, pairing = B.classify_homework_files(["homework/hw1.pdf", "homework/hw10.pdf",
                                                 "homework/hw1_sol.pdf"])
        self.assertTrue(pairing["homework/hw1_sol.pdf"].endswith("hw1.pdf"))   # hw1/hw10 边界不受影响

    def test_same_line_prompt_stays_full(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw12.pdf": ["Problem 1 Compute 2+2.\n\nProblem 2: 求 x^2 的导数。"]})
        code, payload, report = _run(mat, be)
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertEqual(hw[1]["question_text_status"], "full")   # 标题同行的完整题面 ≠ 图片题
        self.assertNotIn("requires_assets", hw[1])
        self.assertEqual(hw[2]["question_text_status"], "full")

    def test_decimal_problem_numbers_kept_distinct(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw13.pdf": ["Problem 1.1\n第一小题题面内容。\nAnswer 1.1: 第一小题答案。\n\n"
                                         "Problem 1.2\n第二小题题面内容。"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        nums = sorted(str(q["homework_number"]) for q in hw)
        self.assertEqual(nums, ["1.1", "1.2"])                    # 小数题号不再折叠成同一个 1
        self.assertEqual(len({q["id"] for q in hw}), 2)
        by_num = {str(q["homework_number"]): q for q in hw}
        self.assertIn("第一小题答案", by_num["1.1"]["answer"])     # 小数号 inline 答案配对
        self.assertEqual(by_num["1.2"]["answer_status"], "unknown")

    def test_download_copy_suffix_and_synonym_pair(self):
        hw, pairing = B.classify_homework_files(["hw2 (4)(1).pdf", "homework2solutions.pdf"])
        self.assertEqual(pairing["homework2solutions.pdf"], "hw2 (4)(1).pdf")   # 下载副本后缀 + homework≡hw

    def test_true_duplicate_copies_stay_failloud(self):
        hw, pairing = B.classify_homework_files(["hw2 (1).pdf", "hw2 (2).pdf", "hw2solutions.pdf"])
        self.assertIsNone(pairing["hw2solutions.pdf"])            # 真重名副本歧义时拒绝配对，不串答案

    # ---- regression guards for Codex round-4 (5 findings) ----

    def test_sibling_week_directories_pair(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        for wk in ("week1", "week2"):
            os.makedirs(os.path.join(mat, wk, "homework"), exist_ok=True)
            os.makedirs(os.path.join(mat, wk, "solutions"), exist_ok=True)
            for sub in ("homework", "solutions"):
                with open(os.path.join(mat, wk, sub, "hw1.pdf"), "wb") as f:
                    f.write(b"%PDF-fake")

        class WkBackend(FakeBackend):
            def page_texts(self, pdf_path):
                p = pdf_path.replace("\\", "/")
                wk = "week1" if "week1" in p else "week2"
                if "solutions" in p:
                    return ["Problem 1\nAnswer 1: %s 的官方答案。" % wk]
                return ["Problem 1\n%s 的题面内容。" % wk]
        code, payload, report = _run(mat, WkBackend({}))
        hw = {q["source_file"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertIn("week1 的官方答案", hw["week1/homework/hw1.pdf"]["answer"])   # 同父家族层各配各的
        self.assertIn("week2 的官方答案", hw["week2/homework/hw1.pdf"]["answer"])

    def test_toc_line_not_recorded_as_answer(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw14.pdf": ["Problem 1\n题面内容在此。"],
                            "hw14_sol.pdf": ["目录\n1. Answer ........ 5", "Problem 1\n真答案内容。"]})
        code, payload, report = _run(mat, be)
        q = next(x for x in payload["quiz_bank"] if x.get("source_type") == "homework")
        self.assertIn("真答案内容", q["answer"])                   # 目录行被过滤，答案取真实解答页
        self.assertNotIn("........", q["answer"])

    def test_problem_n_solution_heading_is_inline_answer(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw15.pdf": ["Problem 1\n题面文字内容。\n\nProblem 1 Solution\n官方解答内容。\n\n"
                                         "Problem 2\n下一题题面。"]})
        code, payload, report = _run(mat, be)
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertEqual(len(hw), 2)                              # 解答段标题不是新题也不是重复
        self.assertIn("官方解答内容", hw[1]["answer"])
        self.assertEqual(hw[2]["answer_status"], "unknown")
        self.assertFalse([w for w in report["warnings"] if "hw_duplicate_problem" in w])

    def test_answer_key_after_all_problems_pairs(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw16.pdf": ["Problem 1\n题面一内容。\n\nProblem 2\n题面二内容。\n\n"
                                         "Answer 1: 答案一内容。\nAnswer 2: 答案二内容。"]})
        code, payload, report = _run(mat, be)
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertIn("答案一内容", hw[1]["answer"])                # 题目区与答案区分离也能按号配
        self.assertIn("答案二内容", hw[2]["answer"])
        self.assertNotIn("答案二内容", hw[1]["answer"])            # 各答案切片互不越界

    def test_continued_problem_keeps_all_pages(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw17.pdf": ["Problem 1\n第一页题面内容。",
                                         "Problem 1 (Continued)\n第二页续文内容。\n\nProblem 2\n下一题题面。"]})
        code, payload, report = _run(mat, be)
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertEqual(len(hw), 2)                              # 续页标题不产生新题
        self.assertIn("第二页续文内容", hw[1]["question"])          # 续页文字并入本题
        self.assertEqual(hw[1]["source_pages"], [1, 2])            # 页码覆盖续页
        self.assertFalse([w for w in report["warnings"] if "hw_duplicate_problem" in w])

    # ---- regression guards for Codex round-5 (5 findings) ----

    def test_same_line_answer_verb_stays_problem(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw18.pdf": ["Problem 1: Answer the following questions about stacks.\n"
                                         "更多题面内容在此。"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)                              # 题面动词 Answer 不翻成解答段
        self.assertIn("Answer the following", hw[0]["question"])
        self.assertEqual(hw[0]["answer_status"], "unknown")
        self.assertFalse([w for w in report["warnings"] if "hw_no_markers" in w])

    def test_verb_answer_filenames_are_homework(self):
        hw, pairing = B.classify_homework_files(["unanswered_hw1.pdf", "answer_questions_hw2.pdf"])
        self.assertEqual(len(hw), 2)                              # 都是作业文件，不是解答
        self.assertEqual(pairing, {})
        hw2, pairing2 = B.classify_homework_files(["hw1.pdf", "hw1_answers.pdf"])
        self.assertEqual(pairing2["hw1_answers.pdf"], "hw1.pdf")  # 真解答记号（hw 之后）照常配

    def test_lettered_subparts_kept_distinct(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw19.pdf": ["Problem 1(a)\n第一小问题面。\nAnswer 1(a): 第一小问答案。\n\n"
                                         "Problem 1(b)\n第二小问题面。"]})
        code, payload, report = _run(mat, be)
        hw = {str(q["homework_number"]): q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertEqual(sorted(hw), ["1a", "1b"])                # 字母小问不折叠成同一个 1
        self.assertIn("第一小问答案", hw["1a"]["answer"])
        self.assertEqual(hw["1b"]["answer_status"], "unknown")
        self.assertEqual(len({q["id"] for q in hw.values()}), 2)

    def test_sanitize_collision_ids_stay_unique(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(os.path.join(mat, "a", "b"), exist_ok=True)
        os.makedirs(os.path.join(mat, "a_b"), exist_ok=True)
        for sub in (("a", "b"), ("a_b",)):
            with open(os.path.join(mat, *sub, "hw1.pdf"), "wb") as f:
                f.write(b"%PDF-fake")
        be = FakeBackend({"hw1.pdf": ["Problem 1\n消毒撞名的题面内容。"]})
        code, payload, report = _run(mat, be)
        ids = [q["id"] for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)                        # a/b 与 a_b 消毒同串也不撞 id

    def test_mirrored_subtrees_pair(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        for wk in ("week1", "week2"):
            os.makedirs(os.path.join(mat, "homework", wk), exist_ok=True)
            os.makedirs(os.path.join(mat, "solutions", wk), exist_ok=True)
            for top in ("homework", "solutions"):
                with open(os.path.join(mat, top, wk, "hw1.pdf"), "wb") as f:
                    f.write(b"%PDF-fake")

        class MirBackend(FakeBackend):
            def page_texts(self, pdf_path):
                p = pdf_path.replace("\\", "/")
                wk = "week1" if "week1" in p else "week2"
                if "solutions" in p:
                    return ["Problem 1\nAnswer 1: %s 的镜像答案。" % wk]
                return ["Problem 1\n%s 的镜像题面。" % wk]
        code, payload, report = _run(mat, MirBackend({}))
        hw = {q["source_file"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertIn("week1 的镜像答案", hw["homework/week1/hw1.pdf"]["answer"])   # 镜像子树各配各的
        self.assertIn("week2 的镜像答案", hw["homework/week2/hw1.pdf"]["answer"])

    # ---- regression guards for Codex round-6 (4 findings) ----

    def test_blank_plus_instructions_not_an_answer(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw20.pdf": ["Problem 1\n计算 2+2。\nAnswer: ________\nShow your work carefully.\n\n"
                                         "Problem 2\n下一题题面。"]})
        code, payload, report = _run(mat, be)
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertNotIn("answer", hw[1])                         # 填空线+指示语不是官方答案
        self.assertEqual(hw[1]["answer_status"], "unknown")

    def test_lettered_prefix_answer_keys_pair(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw21.pdf": ["Problem 1(a)\n第一小问题面。\n\nProblem 1(b)\n第二小问题面。\n\n"
                                         "1(a). Answer: 甲小问答案。\n1b. Answer: 乙小问答案。"]})
        code, payload, report = _run(mat, be)
        hw = {str(q["homework_number"]): q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertIn("甲小问答案", hw["1a"]["answer"])            # 1(a). Answer: 形式配上
        self.assertIn("乙小问答案", hw["1b"]["answer"])            # 1b. Answer: 形式配上

    def test_compact_sol_suffix_classified(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw1.pdf": ["Problem 1\n紧凑后缀的题面内容。"],
                            "hw1sol.pdf": ["Problem 1\nAnswer 1: 紧凑后缀的官方答案。"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)                              # hw1sol 是解答文件，不是第二份作业
        self.assertIn("紧凑后缀的官方答案", hw[0]["answer"])
        hw2, pairing2 = B.classify_homework_files(["hw2.pdf", "hw2ans.pdf"])
        self.assertEqual(pairing2["hw2ans.pdf"], "hw2.pdf")

    def test_ambiguous_local_match_is_terminal(self):
        hw, pairing = B.classify_homework_files(["week1/hw1a.pdf", "week1/hw1b.pdf",
                                                 "week1/hw1_sol.pdf", "week2/hw1.pdf"])
        self.assertIsNone(pairing["week1/hw1_sol.pdf"])           # 本层歧义就地放弃，绝不配到 week2

    # ---- regression guards for Codex round-7 (4 findings) ----

    def test_prefixed_blank_answer_key_stays_unknown(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw22.pdf": ["Problem 1(a)\n第一小问题面。\n\nProblem 1(b)\n第二小问题面。\n\n"
                                         "1(a). Answer: ________\n1(b). Answer: 真实答案内容。"]})
        code, payload, report = _run(mat, be)
        hw = {str(q["homework_number"]): q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertNotIn("answer", hw["1a"])                      # 带号前缀的填空线不是官方答案
        self.assertEqual(hw["1a"]["answer_status"], "unknown")
        self.assertIn("真实答案内容", hw["1b"]["answer"])

    def test_solution_prefix_filenames_are_companions(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw1.pdf": ["Problem 1\n前缀式解答的题面。"],
                            "solutions_hw1.pdf": ["Problem 1\nAnswer 1: 前缀式解答的答案。"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)                              # solutions_hw1 是伴随解答不是第二份作业
        self.assertIn("前缀式解答的答案", hw[0]["answer"])
        hw2, pairing2 = B.classify_homework_files(["作业3.pdf", "答案_作业3.pdf"])
        self.assertEqual(pairing2["答案_作业3.pdf"], "作业3.pdf")   # 中文前缀同理
        hw3, pairing3 = B.classify_homework_files(["answer_questions_hw2.pdf"])
        self.assertEqual((len(hw3), pairing3), (1, {}))            # 动词短语仍归作业（中间夹词）

    def test_repeated_headings_solutions_section_pairs(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw23.pdf": ["Problem 1\n题面一内容。\n\nProblem 2\n题面二内容。",
                                         "Solutions\nProblem 1\n解答一正文。\n\nProblem 2\n解答二正文。"]})
        code, payload, report = _run(mat, be)
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertEqual(len(hw), 2)                              # 解答区重复标题不是新题也不是垃圾重复
        self.assertIn("解答一正文", hw[1]["answer"])
        self.assertIn("解答二正文", hw[2]["answer"])
        self.assertFalse([w for w in report["warnings"] if "hw_duplicate_problem" in w])

    def test_page_header_repeat_still_deduped(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw24.pdf": ["Problem 1\n题面一。\n\nProblem 2\n题面二第一页。",
                                         "Problem 2\n题面二第二页重复页眉后的正文。"]})
        code, payload, report = _run(mat, be)
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertEqual(len(hw), 2)                              # 单号页眉重现仍按重复去重
        self.assertEqual(hw[2]["answer_status"], "unknown")       # 不会被当成解答区
        self.assertTrue([w for w in report["warnings"] if "hw_duplicate_problem" in w])

    def test_alpha_suffix_solution_not_paired_to_base(self):
        hw, pairing = B.classify_homework_files(["hw1.pdf", "hw1a_sol.pdf", "hw1_extra_sol.pdf"])
        self.assertIsNone(pairing["hw1a_sol.pdf"])                # hw1a 的答案不能安到 hw1 头上
        self.assertIsNone(pairing["hw1_extra_sol.pdf"])
        hw2, pairing2 = B.classify_homework_files(["hw1_probability_worksheet.pdf", "hw1_sol.pdf"])
        self.assertEqual(pairing2["hw1_sol.pdf"], "hw1_probability_worksheet.pdf")   # 作业名延长方向仍配

    # ---- regression guards for Codex round-8 (4 findings) ----

    def test_unnumbered_solution_block_in_paired_file(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw25.pdf": ["Problem 1\n真正的题面内容。"],
                            "hw25_sol.pdf": ["Problem 1\n题面复述而已。\nSolution\n无号真解答内容在此。"]})
        code, payload, report = _run(mat, be)
        q = next(x for x in payload["quiz_bank"] if x.get("source_type") == "homework")
        self.assertIn("无号真解答内容", q["answer"])                # 无号 Solution 继承前题号，解答段获胜
        self.assertNotIn("题面复述", q["answer"])                  # 不再把复述切片当官方答案

    def test_letter_variant_assignment_not_paired_to_base_sol(self):
        hw, pairing = B.classify_homework_files(["hw1a.pdf", "hw1_sol.pdf"])
        self.assertIsNone(pairing["hw1_sol.pdf"])                 # hw1 的答案不能安到 hw1a 头上

    def test_selfcontained_solutions_pdf_extracted(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw1_solutions.pdf": ["Problem 1\n自含册的题面。\nSolution\n自含册的解答。\n\n"
                                                  "Problem 2\n第二题题面。\nSolution\n第二题解答。"]})
        code, payload, report = _run(mat, be)
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertEqual(len(hw), 2)                              # 自含题+解答的孤儿册按作业解析
        self.assertIn("自含册的解答", hw[1]["answer"])
        self.assertIn("第二题解答", hw[2]["answer"])
        self.assertTrue(any(w.startswith("hw_selfcontained_solutions") for w in report["warnings"]))
        self.assertFalse(any(w.startswith("hw_unpaired_solution_file") for w in report["warnings"]))

    def test_chapterless_homework_gets_discovery_warning(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw26.pdf": ["Problem 1\n没有章节线索的题面。"]})
        code, payload, report = _run(mat, be)
        q = next(x for x in payload["quiz_bank"] if x.get("source_type") == "homework")
        self.assertNotIn("chapter", q)                            # 作业号≠章节号：仍绝不猜
        self.assertTrue(any(w.startswith("hw_no_chapter") for w in report["warnings"]))

    # ---- regression guards for Codex round-9 (7 findings) ----

    def test_pure_answer_key_not_promoted_to_homework(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw9_key_solutions.pdf": ["Problem 1\nAnswer 1: 42\n\nProblem 2\nAnswer 2: 17"]})
        code, payload, report = _run(mat, be)
        self.assertFalse([q for q in payload["quiz_bank"] if q.get("source_type") == "homework"])
        self.assertTrue(any(w.startswith("hw_unpaired_solution_file") for w in report["warnings"]))

    def test_paired_solution_pages_kept_out_of_wiki(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw1.pdf": ["Problem 1\n泄题检查的题面。"],
                            "hw1_sol.pdf": ["Problem 1\nAnswer 1: 泄题检查的官方答案。"]})
        code, payload, report = _run(mat, be)
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("泄题检查的官方答案", wiki_all)            # 官方答案页绝不进章节 wiki
        q = next(x for x in payload["quiz_bank"] if x.get("source_type") == "homework")
        self.assertIn("泄题检查的官方答案", q["answer"])            # 但答案出处保留

    def test_mirrored_trees_under_common_root_pair(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        for wk in ("week1", "week2"):
            os.makedirs(os.path.join(mat, "course", "homework", wk), exist_ok=True)
            os.makedirs(os.path.join(mat, "course", "solutions", wk), exist_ok=True)
            for top in ("homework", "solutions"):
                with open(os.path.join(mat, "course", top, wk, "hw1.pdf"), "wb") as f:
                    f.write(b"%PDF-fake")

        class RootBackend(FakeBackend):
            def page_texts(self, pdf_path):
                p = pdf_path.replace("\\", "/")
                wk = "week1" if "week1" in p else "week2"
                if "solutions" in p:
                    return ["Problem 1\nAnswer 1: %s 公共前缀答案。" % wk]
                return ["Problem 1\n%s 公共前缀题面。" % wk]
        code, payload, report = _run(mat, RootBackend({}))
        hw = {q["source_file"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertIn("week1 公共前缀答案", hw["course/homework/week1/hw1.pdf"]["answer"])
        self.assertIn("week2 公共前缀答案", hw["course/homework/week2/hw1.pdf"]["answer"])

    def test_multiline_blank_answer_box_unknown(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw27.pdf": ["Problem 1\n计算 2+2。\nAnswer:\n________\nShow your work.\n\n"
                                         "Problem 2\n下一题题面。"]})
        code, payload, report = _run(mat, be)
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertNotIn("answer", hw[1])                         # 多行空栏+指示语不是官方答案
        self.assertEqual(hw[1]["answer_status"], "unknown")

    def test_answer_key_suffix_filenames_pair(self):
        hw, pairing = B.classify_homework_files(["hw1.pdf", "hw1_answer_key.pdf"])
        self.assertEqual(pairing["hw1_answer_key.pdf"], "hw1.pdf")
        hw2, pairing2 = B.classify_homework_files(["hw2.pdf", "hw2_solution_key.pdf"])
        self.assertEqual(pairing2["hw2_solution_key.pdf"], "hw2.pdf")
        hw3, pairing3 = B.classify_homework_files(["keyboard_hw1.pdf"])
        self.assertEqual((len(hw3), pairing3), (1, {}))            # keyboard 不是 key 后缀

    def test_same_line_solution_content_pairs(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw28.pdf": ["Problem 1\n题面文字内容。\n\nProblem 1 Solution: A1 就是答案。\n\n"
                                         "Problem 2\n下一题题面。"]})
        code, payload, report = _run(mat, be)
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertIn("A1 就是答案", hw[1]["answer"])              # 同行带内容的解答段配上
        self.assertEqual(hw[2]["answer_status"], "unknown")
        tmp2 = tempfile.mkdtemp()
        mat2, be2 = _mk(tmp2, {"hw29.pdf": ["Problem 1: Answer the following about stacks.\n更多题面。"]})
        code2, payload2, report2 = _run(mat2, be2)
        hw2 = [q for q in payload2["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw2), 1)                             # 题面动词形式不回退

    def test_numeric_only_body_is_page_reference(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"ch01/hw30.pdf": ["Problem 1\n12\n\nProblem 2\n2+2=?"]})
        asset_root = os.path.join(tmp, "ws", "references", "assets")
        code, payload, report = _run(mat, be, ["--asset-root", asset_root])
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertEqual(hw[1]["question_text_status"], "page_reference")   # 页脚数字是抽取残渣
        self.assertIs(hw[1]["requires_assets"], True)
        self.assertEqual(hw[2]["question_text_status"], "full")   # 带运算符的数字题面仍 full

    # ---- regression guards for Codex round-10 (6 findings) ----

    def test_solution_with_connector_prefix_is_companion(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw1.pdf": ["Problem 1\n连接词配对的题面。"],
                            "solutions_for_hw1.pdf": ["Problem 1\nAnswer 1: 连接词配对的答案。"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)                              # solutions_for_hw1 是伴随解答
        self.assertIn("连接词配对的答案", hw[0]["answer"])
        hw2, pairing2 = B.classify_homework_files(["hw2.pdf", "answers_to_hw2.pdf"])
        self.assertEqual(pairing2["answers_to_hw2.pdf"], "hw2.pdf")
        hw3, pairing3 = B.classify_homework_files(["answer_questions_hw3.pdf"])
        self.assertEqual((len(hw3), pairing3), (1, {}))            # 动词短语（questions 夹词）仍归作业

    def test_selfcontained_same_line_prompt_extracted(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw2_solutions.pdf": ["Problem 1 Compute 2+2.\nSolution\n答案是 4。"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)                              # 同行题面也算真实题面
        self.assertIn("答案是 4", hw[0]["answer"])
        self.assertTrue(any(w.startswith("hw_selfcontained_solutions") for w in report["warnings"]))

    def test_homework_pages_kept_out_of_wiki(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"作业2.pdf": ["第1题" + chr(10) + "作业题面。" + chr(10) + "Solution"
                                          + chr(10) + "作业的官方解答内容。"]})
        with open(os.path.join(mat, "lecture_ch1.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        be.texts["lecture_ch1.pdf"] = ["Example 1.1 Problem" + chr(10) + "讲义正文知识点。"]
        code, payload, report = _run(mat, be)
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("作业的官方解答内容", wiki_all)            # inline 解答不泄进 wiki
        self.assertNotIn("作业题面", wiki_all)                     # 作业册整册不进 wiki（题在 quiz_bank）
        self.assertIn("讲义正文知识点", wiki_all)                  # 讲义照常进 wiki
        q = next(x for x in payload["quiz_bank"] if x.get("source_type") == "homework")
        self.assertIn("作业的官方解答内容", q["answer"])

    def test_blank_answer_sheet_companion_stays_unknown(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw3.pdf": ["Problem 1\n空白答卷的题面。"],
                            "hw3_sol.pdf": ["Problem 1\nAnswer 1: ________"]})
        code, payload, report = _run(mat, be)
        q = next(x for x in payload["quiz_bank"] if x.get("source_type") == "homework")
        self.assertNotIn("answer", q)                             # 独立空白答卷不是官方答案
        self.assertEqual(q["answer_status"], "unknown")

    def test_untitled_multi_header_repeats_stay_questions(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw4.pdf": ["Problem 1\n题面一。\n\nProblem 2\n题面二。",
                                        "Problem 1\n续页一正文。\n\nProblem 2\n续页二正文。"]})
        code, payload, report = _run(mat, be)
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertEqual(hw[1]["answer_status"], "unknown")       # 无 Solutions 节标题的多号重现 ≠ 答案
        self.assertEqual(hw[2]["answer_status"], "unknown")
        self.assertTrue([w for w in report["warnings"] if "hw_duplicate_problem" in w])

    def test_single_problem_unnumbered_solution_companion(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw5.pdf": ["Problem 1\n单题作业的题面。"],
                            "hw5_sol.pdf": ["Solution\n整册就是这道题的解答。"]})
        code, payload, report = _run(mat, be)
        q = next(x for x in payload["quiz_bank"] if x.get("source_type") == "homework")
        self.assertIn("整册就是这道题的解答", q["answer"])          # 无号单块解答按唯一题配上
        tmp2 = tempfile.mkdtemp()
        mat2, be2 = _mk(tmp2, {"hw6.pdf": ["Problem 1\n多题一。\n\nProblem 2\n多题二。"],
                               "hw6_sol.pdf": ["Solution\n只有一块答案不知道归谁。"]})
        code2, payload2, report2 = _run(mat2, be2)
        hw2 = {q["homework_number"]: q for q in payload2["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertEqual(hw2[1]["answer_status"], "unknown")       # 多题作业不猜归属
        self.assertEqual(hw2[2]["answer_status"], "unknown")

    def test_figure_dash_artifact_not_a_blank(self):
        # 真实解答首行常是图表轴线残渣（单个 '-'）——填空线须 ≥3 个连续填充符，不能误否真解答
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw7.pdf": ["Problem 1\n图表题的题面。"],
                            "hw7_sol.pdf": ["Problem 1 Solution\n-\n6\ny\nx\n真正的图表解答正文。"]})
        code, payload, report = _run(mat, be)
        q = next(x for x in payload["quiz_bank"] if x.get("source_type") == "homework")
        self.assertIn("真正的图表解答正文", q["answer"])

    # ---- regression guards for Codex round-11 (5 findings) ----

    def test_homework_files_excluded_from_lecture_extraction(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw8.pdf": ["Quiz 1.1 Problem\n作业里的讲义式标题。\nQuiz 1.1 Solution\n答案。"]})
        code, payload, report = _run(mat, be)
        lec = [q for q in payload["quiz_bank"] if q["id"].startswith("lecture_")]
        self.assertFalse(lec)                                     # 作业文件不产出 lecture_* 项
        self.assertTrue(any(w.startswith("hw_lecture_overlap") for w in report["warnings"]))

    def test_continued_solution_pages_merged(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw9.pdf": ["Problem 1\n跨页解答的题面。"],
                            "hw9_sol.pdf": ["Problem 1\nAnswer 1: 第一页解答。",
                                            "Problem 1 (Continued)\n第二页解答续文。"]})
        code, payload, report = _run(mat, be)
        q = next(x for x in payload["quiz_bank"] if x.get("source_type") == "homework")
        self.assertIn("第一页解答", q["answer"])
        self.assertIn("第二页解答续文", q["answer"])                # 续页并入官方答案，不再被裁掉

    def test_prompt_folder_named_answer_questions_is_homework(self):
        hw, pairing = B.classify_homework_files(["answer_questions/hw1.pdf"])
        self.assertEqual(hw, ["answer_questions/hw1.pdf"])        # 动词短语目录装的是题面
        self.assertEqual(pairing, {})
        hw2, pairing2 = B.classify_homework_files(["solutions/hw1.pdf", "homework/hw1.pdf"])
        self.assertEqual(pairing2["solutions/hw1.pdf"], "homework/hw1.pdf")   # 纯解答目录不受影响

    def test_solution_manual_prefix_is_companion(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw1.pdf": ["Problem 1\n手册配对的题面。"],
                            "solution_manual_hw1.pdf": ["Problem 1\nAnswer 1: 手册里的官方答案。"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)                              # solution_manual 是解答不是第二份作业
        self.assertIn("手册里的官方答案", hw[0]["answer"])

    def test_prompt_does_not_swallow_solutions_heading(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw10.pdf": ["Problem 1\n题面一内容。\n\nProblem 2\n题面二内容。\n\n"
                                         "Solutions\nProblem 1\n解答一。\n\nProblem 2\n解答二。"]})
        code, payload, report = _run(mat, be)
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertNotIn("Solutions", hw[2]["question"])          # 最后一题的题面不吞节标题
        self.assertIn("题面二内容", hw[2]["question"])
        self.assertIn("解答二", hw[2]["answer"])

    # ---- regression guards for Codex round-12 (7 findings) ----

    def test_lecture_pairing_never_pulls_homework_answers(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw11.pdf": ["Quiz 1.1 Solution\n作业文件里的解答内容。"]})
        with open(os.path.join(mat, "lec_ch01.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        be.texts["lec_ch01.pdf"] = ["Quiz 1.1 Problem\n讲义里的题面。"]
        code, payload, report = _run(mat, be)
        lec = [q for q in payload["quiz_bank"] if q["id"].startswith("lecture_")]
        self.assertTrue(lec)
        self.assertNotIn("answer", lec[0])                        # 讲义题绝不从作业文件吸答案
        self.assertNotEqual(lec[0].get("answer_source_file"), "homework/hw11.pdf")

    def test_problem_solution_blank_heading_unknown(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw12.pdf": ["Problem 1\n题面内容。\n\nProblem 1 Solution: ________\n\n"
                                         "Problem 2\n下一题题面。"]})
        code, payload, report = _run(mat, be)
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertNotIn("answer", hw[1])                         # 复合标题后的填空线不是答案
        self.assertEqual(hw[1]["answer_status"], "unknown")

    def test_restated_prompt_not_answer_when_blank_marker_exists(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw13.pdf": ["Problem 1\n计算 2+2。"],
                            "hw13_sol.pdf": ["Problem 1\nCompute 2+2 restated.\nAnswer 1: ________"]})
        code, payload, report = _run(mat, be)
        q = next(x for x in payload["quiz_bank"] if x.get("source_type") == "homework")
        self.assertNotIn("answer", q)                             # 题面复述不顶替空白答案
        self.assertEqual(q["answer_status"], "unknown")

    def test_compact_solution_manual_stems_pair(self):
        hw, pairing = B.classify_homework_files(["hw1.pdf", "hw1_solutionmanual.pdf"])
        self.assertEqual(pairing["hw1_solutionmanual.pdf"], "hw1.pdf")
        hw2, pairing2 = B.classify_homework_files(["hw2.pdf", "hw2_solmanual.pdf"])
        self.assertEqual(pairing2["hw2_solmanual.pdf"], "hw2.pdf")

    def test_symbolic_prompt_selfcontained_extracted(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw14_solutions.pdf": ["Problem 1\n2+2=?\nSolution\n4 就是答案。"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)                              # 符号题面（2+2=?）是真实题面
        self.assertIn("4 就是答案", hw[0]["answer"])

    def test_solution_to_problem_headings_pair(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw15.pdf": ["Problem 1\n题面一。\n\nProblem 2\n题面二。"],
                            "hw15_sol.pdf": ["Solution to Problem 1\n第一题官方解答。\n\n"
                                             "Answer to Question 2\n第二题官方解答。"]})
        code, payload, report = _run(mat, be)
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertIn("第一题官方解答", hw[1]["answer"])            # Solution to Problem N 形式配上
        self.assertIn("第二题官方解答", hw[2]["answer"])

    def test_pset_prefix_recognized(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"pset1.pdf": ["Problem 1\npset 命名的题面。"],
                            "pset1_sol.pdf": ["Problem 1\nAnswer 1: pset 命名的答案。"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)                              # pset1 识别为作业并配对
        self.assertIn("pset 命名的答案", hw[0]["answer"])

    # ---- regression guards for Codex round-13 (5 findings) ----

    def test_late_sol_token_after_verb_prefix(self):
        hw, pairing = B.classify_homework_files(["hw1.pdf", "answer_questions_hw1_sol.pdf"])
        self.assertIn("answer_questions_hw1_sol.pdf", pairing)    # 后置 _sol 让它是解答文件
        self.assertNotIn("answer_questions_hw1_sol.pdf", hw)      # 绝不再当第二份作业导入

    def test_plural_solution_headings_pair(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw16.pdf": ["Problem 1\n题面一。\n\nProblem 2\n题面二。"],
                            "hw16_sol.pdf": ["Solutions to Problem 1\n第一题复数解答。\n\n"
                                             "2. Answers: 第二题复数解答。"]})
        code, payload, report = _run(mat, be)
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertIn("第一题复数解答", hw[1]["answer"])            # Solutions/Answers 复数标记配上
        self.assertIn("第二题复数解答", hw[2]["answer"])

    def test_visual_answer_for_text_question_rendered(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"ch01/hw17.pdf": ["Problem 1\n纯文本题面，直接作答。"],
                            "ch01/hw17_sol.pdf": ["Problem 1\nAnswer 1: see the graph below for the shape."]})
        asset_root = os.path.join(tmp, "ws", "references", "assets")
        code, payload, report = _run(mat, be, ["--asset-root", asset_root])
        q = next(x for x in payload["quiz_bank"] if x.get("source_type") == "homework")
        self.assertNotIn("requires_assets", q)                    # 题面完整可问，不 fail-close
        roles = [a["role"] for a in q.get("assets", [])]
        self.assertIn("answer_context", roles)                    # 但答案侧原页已渲染供复盘

    def test_week_scoped_solution_directories_pair(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(os.path.join(mat, "week1_homework"), exist_ok=True)
        os.makedirs(os.path.join(mat, "week1_solutions"), exist_ok=True)
        with open(os.path.join(mat, "week1_homework", "hw1.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        with open(os.path.join(mat, "week1_solutions", "hw1.pdf"), "wb") as f:
            f.write(b"%PDF-fake")

        class WkBackend(FakeBackend):
            def page_texts(self, pdf_path):
                if "solutions" in pdf_path.replace("\\", "/"):
                    return ["Problem 1\nAnswer 1: 周目录后缀的答案。"]
                return ["Problem 1\n周目录后缀的题面。"]
        code, payload, report = _run(mat, WkBackend({}))
        q = next(x for x in payload["quiz_bank"] if x.get("source_type") == "homework")
        self.assertIn("周目录后缀的答案", q["answer"])              # week1_solutions/ 识别为解答目录

    def test_markerless_single_answer_companion(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw18.pdf": ["Problem 1\n单题裸答案册的题面。"],
                            "hw18_sol.pdf": ["4"]})
        code, payload, report = _run(mat, be)
        q = next(x for x in payload["quiz_bank"] if x.get("source_type") == "homework")
        self.assertEqual(q["answer"].strip(), "4")                 # 无标记裸答案按唯一题配上
        tmp2 = tempfile.mkdtemp()
        mat2, be2 = _mk(tmp2, {"hw19.pdf": ["Problem 1\n多题一。\n\nProblem 2\n多题二。"],
                               "hw19_sol.pdf": ["42"]})
        code2, payload2, report2 = _run(mat2, be2)
        hw2 = {q["homework_number"]: q for q in payload2["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertEqual(hw2[1]["answer_status"], "unknown")       # 多题不猜归属
        self.assertEqual(hw2[2]["answer_status"], "unknown")

    # ---- regression guards for Codex round-14 (5 findings) ----

    def test_root_week_suffix_folders_pair_per_week(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        for wk in ("week1", "week2"):
            os.makedirs(os.path.join(mat, wk + "_homework"), exist_ok=True)
            os.makedirs(os.path.join(mat, wk + "_solutions"), exist_ok=True)
            for suf in ("_homework", "_solutions"):
                with open(os.path.join(mat, wk + suf, "hw1.pdf"), "wb") as f:
                    f.write(b"%PDF-fake")

        class WkBackend(FakeBackend):
            def page_texts(self, pdf_path):
                p = pdf_path.replace("\\", "/")
                wk = "week1" if "week1" in p else "week2"
                if "solutions" in p:
                    return ["Problem 1\nAnswer 1: %s 根级后缀答案。" % wk]
                return ["Problem 1\n%s 根级后缀题面。" % wk]
        code, payload, report = _run(mat, WkBackend({}))
        hw = {q["source_file"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertIn("week1 根级后缀答案", hw["week1_homework/hw1.pdf"]["answer"])   # 镜像层按周锁定
        self.assertIn("week2 根级后缀答案", hw["week2_homework/hw1.pdf"]["answer"])

    def test_generic_solutions_dir_stays_in_lecture_pipeline(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(os.path.join(mat, "lectures"), exist_ok=True)
        os.makedirs(os.path.join(mat, "solutions"), exist_ok=True)
        with open(os.path.join(mat, "lectures", "ch01.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        with open(os.path.join(mat, "solutions", "ch01.pdf"), "wb") as f:
            f.write(b"%PDF-fake")

        class LecBackend(FakeBackend):
            def page_texts(self, pdf_path):
                if "solutions" in pdf_path.replace("\\", "/"):
                    return ["Quiz 1.1 Solution\n讲义测验的官方解答。"]
                return ["Quiz 1.1 Problem\n讲义测验的题面。"]
        code, payload, report = _run(mat, LecBackend({}))
        lec = [q for q in payload["quiz_bank"] if q["id"].startswith("lecture_")]
        self.assertTrue(lec)
        self.assertIn("讲义测验的官方解答", lec[0].get("answer", ""))   # 讲义解答配对不被作业管线劫走
        self.assertFalse(any(w.startswith("hw_unpaired_solution_file") for w in report["warnings"]))

    def test_prompt_page_with_inline_answer_not_rendered(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw20.pdf": ["Problem 1\nAnswer 1: 同页的官方答案。"]})
        asset_root = os.path.join(tmp, "ws", "references", "assets")
        code, payload, report = _run(mat, be, ["--asset-root", asset_root])
        q = next(x for x in payload["quiz_bank"] if x.get("source_type") == "homework")
        self.assertEqual(q["question_text_status"], "page_reference")
        self.assertNotIn("requires_assets", q)                    # 唯一题面页含答案：不渲染不假装可问
        self.assertFalse(q.get("assets"))
        self.assertTrue(any(w.startswith("hw_prompt_page_contains_answer") for w in report["warnings"]))

    def test_answers_heading_does_not_steal_first_item(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw21.pdf": ["Problem 1\n题面一内容。\n\nProblem 2\n题面二内容。\n\n"
                                         "Answers\n1. A1 内容\n2. A2 内容"]})
        code, payload, report = _run(mat, be)
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertNotIn("2. A2", hw[1].get("answer", ""))        # 节标题不吞第一条答案项
        self.assertNotIn("A2 内容", hw[1].get("answer", ""))

    def test_answer_verb_suffix_filename_is_homework(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw1_answer_questions.pdf": ["Problem 1\n后缀动词短语的题面。"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)                              # answer_questions 后缀是题面提示语
        self.assertFalse(any(w.startswith("hw_unpaired_solution_file") for w in report["warnings"]))

    # ---- regression guards for Codex round-15 (5 findings) ----

    def test_figure_question_with_same_page_answer_downgraded(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw22.pdf": ["Problem 1\nShade the Venn diagram shown at right.\n"
                                         "Answer 1: 同页的官方答案。"]})
        asset_root = os.path.join(tmp, "ws", "references", "assets")
        code, payload, report = _run(mat, be, ["--asset-root", asset_root])
        q = next(x for x in payload["quiz_bank"] if x.get("source_type") == "homework")
        self.assertEqual(q["question_text_status"], "page_reference")   # 图依赖+同页答案 → 降级
        self.assertNotIn("requires_assets", q)                    # 不假装可问、也不渲染泄题页
        self.assertFalse(q.get("assets"))

    def test_bare_answers_header_splits_by_number(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw23.pdf": ["Problem 1\n题面一内容。\n\nProblem 2\n题面二内容。\n\n"
                                         "Answers\n1. 答案一内容\n2. 答案二内容"]})
        code, payload, report = _run(mat, be)
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertIn("答案一内容", hw[1]["answer"])                # 按号拆分，不整块归最后一题
        self.assertNotIn("答案二内容", hw[1]["answer"])
        self.assertIn("答案二内容", hw[2]["answer"])

    def test_restatement_then_titled_solutions_prefers_real_answer(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw24.pdf": ["Problem 1\n真正的题面。"],
                            "hw24_sol.pdf": ["Problem 1\n题面复述而已。\n\nSolutions\n"
                                             "Problem 1\n真正的官方解答。"]})
        code, payload, report = _run(mat, be)
        q = next(x for x in payload["quiz_bank"] if x.get("source_type") == "homework")
        self.assertIn("真正的官方解答", q["answer"])                # 带标题解答区的真解答取代复述
        self.assertNotIn("复述而已", q["answer"])

    def test_sols_plural_abbreviation_pairs(self):
        hw, pairing = B.classify_homework_files(["hw1.pdf", "hw1_sols.pdf"])
        self.assertEqual(pairing["hw1_sols.pdf"], "hw1.pdf")      # sols 复数缩写是解答后缀

    def test_materials_root_is_homework_folder(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "homework")                       # --materials 直接指向作业文件夹
        os.makedirs(mat, exist_ok=True)
        fake = {}
        for name, pages in {"worksheet1.pdf": ["Problem 1\n通用命名的题面。"],
                            "worksheet1_solutions.pdf": ["Problem 1\nAnswer 1: 通用命名的答案。"]}.items():
            with open(os.path.join(mat, name), "wb") as f:
                f.write(b"%PDF-fake")
            fake[name] = pages
        be = FakeBackend(fake)
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)                              # 根目录名补上作业线索
        self.assertIn("通用命名的答案", hw[0]["answer"])
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("通用命名的答案", wiki_all)               # 解答册照常不进 wiki

    # ---- regression guards for Codex round-16 (5 findings) ----

    def test_root_hw_folder_keeps_solutions_out_of_wiki(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "homework")
        os.makedirs(mat, exist_ok=True)
        fake = {"worksheet1.pdf": ["Problem 1\n根级工作表题面。"],
                "solutions.pdf": ["随笔式答案页，没有题号标记，纯答案内容。"]}
        for name in fake:
            with open(os.path.join(mat, name), "wb") as f:
                f.write(b"%PDF-fake")
        be = FakeBackend(fake)
        code, payload, report = _run(mat, be)
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("纯答案内容", wiki_all)                   # 根级 solutions.pdf 不漏进 wiki
        self.assertTrue(any(w.startswith("hw_unpaired_solution_file") for w in report["warnings"]))

    def test_paired_answers_header_list_split(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw25.pdf": ["Problem 1\n题面一内容。\n\nProblem 2\n题面二内容。"],
                            "hw25_sol.pdf": ["Answers\n1. 独立册答案一\n2. 独立册答案二"]})
        code, payload, report = _run(mat, be)
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertIn("独立册答案一", hw[1]["answer"])              # 独立答案册的编号清单按号拆
        self.assertIn("独立册答案二", hw[2]["answer"])
        self.assertNotIn("独立册答案二", hw[1]["answer"])

    def test_answer_box_instructions_not_official_answer(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw26.pdf": ["Problem 1\nAnswer: Give a short proof in the box below.\n\n"
                                         "Problem 2\n正常题面内容。\nAnswer: 42"]})
        code, payload, report = _run(mat, be)
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertNotIn("answer", hw[1])                         # 答题栏指示语不是官方答案
        self.assertEqual(hw[1]["answer_status"], "unknown")
        self.assertIn("42", hw[2]["answer"])                      # 有题面内容的内联答案照常

    def test_bare_key_suffix_is_companion(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw1.pdf": ["Problem 1\n裸 key 后缀的题面。"],
                            "hw1_key.pdf": ["Problem 1\nAnswer 1: 裸 key 后缀的答案。"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)                              # hw1_key 是答案册不是第二份作业
        self.assertIn("裸 key 后缀的答案", hw[0]["answer"])
        hw2, pairing2 = B.classify_homework_files(["keyboard_hw2.pdf"])
        self.assertEqual((len(hw2), pairing2), (1, {}))            # keyboard 词内 key 不受影响

    def test_numeric_only_paired_solution_kept(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw27.pdf": ["Problem 1\n数值答案的题面。"],
                            "hw27_sol.pdf": ["Problem 1\n4"]})
        code, payload, report = _run(mat, be)
        q = next(x for x in payload["quiz_bank"] if x.get("source_type") == "homework")
        self.assertIn("4", q["answer"])                           # 纯数字/符号官方答案不被字母门槛拒掉

    # ---- regression guards for Codex round-17 (4 findings) ----

    def test_restated_then_bare_answers_list_in_paired_file(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw28.pdf": ["Problem 1\n题面一。\n\nProblem 2\n题面二。"],
                            "hw28_sol.pdf": ["Problem 1\n复述一。\n\nProblem 2\n复述二。\n\n"
                                             "Answers\n1. 手册答案一\n2. 手册答案二"]})
        code, payload, report = _run(mat, be)
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertIn("手册答案一", hw[1]["answer"])                # 节头不被继承吞掉，按号拆分先行
        self.assertIn("手册答案二", hw[2]["answer"])
        self.assertNotIn("手册答案二", hw[1]["answer"])            # 第二题答案不整块灌给第一题

    def test_colon_prefixed_solution_heading_pairs(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw29.pdf": ["Problem 1\n题面内容。\n\nProblem 1: Solution: 冒号形式的解答。\n\n"
                                         "Problem 2\n下一题。"]})
        code, payload, report = _run(mat, be)
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertIn("冒号形式的解答", hw[1]["answer"])            # Problem 1: Solution: 配上
        tmp2 = tempfile.mkdtemp()
        mat2, be2 = _mk(tmp2, {"hw30.pdf": ["Problem 1: Answer the following about heaps.\n更多题面。"]})
        code2, payload2, report2 = _run(mat2, be2)
        hw2 = [q for q in payload2["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw2), 1)                             # 题面动词短语仍不误翻

    def test_answer_box_instruction_stays_in_prompt(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw31.pdf": ["Problem 1\nAnswer: Give a short proof in the box below.\n\n"
                                         "Problem 2\n下一题题面。"]})
        code, payload, report = _run(mat, be)
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertEqual(hw[1]["question_text_status"], "full")   # 指示语并回题面，保持可问全文题
        self.assertIn("Give a short proof", hw[1]["question"])
        self.assertEqual(hw[1]["answer_status"], "unknown")

    def test_blank_with_trailing_label_stays_unknown(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw32.pdf": ["Problem 1\n计算 2+2。\nAnswer: ________ (5 pts)\n\n"
                                         "Problem 2\n下一题题面。"]})
        code, payload, report = _run(mat, be)
        hw = {q["homework_number"]: q for q in payload["quiz_bank"] if q.get("source_type") == "homework"}
        self.assertNotIn("answer", hw[1])                         # 填空线+尾随评分标注不是答案
        self.assertEqual(hw[1]["answer_status"], "unknown")

    # ---- regression guards for Codex round-19 (5 findings) ----

    def test_lettered_answer_key_entries_split(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw31.pdf": ["Problem 1a\n字母小问甲题面。\n\nProblem 1b\n字母小问乙题面。"],
                            "hw31_sol.pdf": ["Answers\n1a. 字母小问答案甲。\n1b. 字母小问答案乙。"]})
        code, payload, report = _run(mat, be)
        by_num = {q["homework_number"]: q for q in payload["quiz_bank"]
                  if q.get("source_type") == "homework"}
        self.assertIn("字母小问答案甲", by_num["1a"].get("answer", ""))   # 1a. 裸字母键控行也按号拆
        self.assertIn("字母小问答案乙", by_num["1b"].get("answer", ""))

    def test_problem_colon_answer_instruction_stays_problem(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw32.pdf": ["Problem 1: Answer: Give a short proof of the theorem."]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)                              # 指示语首现不翻转成解答
        self.assertIn("Give a short proof", hw[0].get("question") or "")
        self.assertFalse(any(w.startswith("hw_no_markers") for w in report["warnings"]))

    def test_unpaired_solutions_dir_file_kept_out_of_wiki(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(os.path.join(mat, "homework"), exist_ok=True)
        os.makedirs(os.path.join(mat, "solutions"), exist_ok=True)
        for rel in ("homework/hw1.pdf", "solutions/week1.pdf", "lec01.pdf"):
            with open(os.path.join(mat, *rel.split("/")), "wb") as f:
                f.write(b"%PDF-fake")

        class WkBackend(FakeBackend):
            def page_texts(self, pdf_path):
                rel = pdf_path.replace(chr(92), "/")
                if rel.endswith("homework/hw1.pdf"):
                    return ["Problem 1\n作业一的题面。"]
                if rel.endswith("solutions/week1.pdf"):
                    return ["整册都是官方解答文字。"]
                return ["讲义正文照常进 wiki。"]
        code, payload, report = _run(mat, WkBackend({}))
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("整册都是官方解答文字", wiki_all)         # 配不上也不准从解答目录漏进 wiki
        self.assertIn("讲义正文照常进 wiki", wiki_all)

    def test_answer_key_section_heading_detected(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw34.pdf": ["Problem 1\n键标题题面一。\n\nProblem 2\n键标题题面二。"],
                            "hw34_sol.pdf": ["Answer Key\n1. 键标题答案一。\n2. 键标题答案二。"]})
        code, payload, report = _run(mat, be)
        by_num = {q["homework_number"]: q for q in payload["quiz_bank"]
                  if q.get("source_type") == "homework"}
        self.assertIn("键标题答案一", by_num[1].get("answer", ""))  # Answer Key 节头也算解答标题
        self.assertIn("键标题答案二", by_num[2].get("answer", ""))

    def test_markerless_numbered_answer_key_splits(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw35.pdf": ["Problem 1\n纯编号题面一。\n\nProblem 2\n纯编号题面二。"],
                            "hw35_sol.pdf": ["1. 纯编号答案一。\n2. 纯编号答案二。"]})
        code, payload, report = _run(mat, be)
        by_num = {q["homework_number"]: q for q in payload["quiz_bank"]
                  if q.get("source_type") == "homework"}
        self.assertIn("纯编号答案一", by_num[1].get("answer", ""))  # 连标题都没有的答案册按号整拆
        self.assertIn("纯编号答案二", by_num[2].get("answer", ""))

    # ---- regression guards for Codex round-20 (3 new findings) ----

    def test_single_entry_answer_key_goes_to_keyed_problem(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw41.pdf": ["Problem 1\n部分键题面一。\n\nProblem 2\n部分键题面二。\n\n"
                                         "Answers\n1. 部分键答案一。"]})
        code, payload, report = _run(mat, be)
        by_num = {q["homework_number"]: q for q in payload["quiz_bank"]
                  if q.get("source_type") == "homework"}
        self.assertIn("部分键答案一", by_num[1].get("answer", ""))   # 单条键控行按号给 1，
        self.assertNotIn("部分键答案一", by_num[2].get("answer", "") or "")   # 不给节前的 2

    def test_answer_box_instruction_after_prompt_not_stored_as_answer(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw42.pdf": ["Problem 1\nProve the statement X.\n"
                                         "Answer: Give a short proof in the box below."]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)
        self.assertNotIn("box below", hw[0].get("answer", "") or "")   # 指示语不是官方答案
        self.assertIn("Prove the statement X", hw[0].get("question") or "")
        self.assertIn("box below", hw[0].get("question") or "")        # 指示语并回题面

    def test_descriptive_prefix_solution_filename_pairs(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw43.pdf": ["Problem 1\n前缀配对题面。"],
                            "answer_questions_hw43_sol.pdf": ["Problem 1 Solution\n前缀配对答案。"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)
        self.assertIn("前缀配对答案", hw[0].get("answer", ""))   # 描述性前缀不挡住后缀锚定配对
        self.assertFalse(any(w.startswith("hw_unpaired_solution_file") for w in report["warnings"]))

    # ---- regression guards for Codex round-21 (6 findings) ----

    def test_glued_answerkeys_dir_recognized(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(os.path.join(mat, "homework"), exist_ok=True)
        os.makedirs(os.path.join(mat, "answerkeys"), exist_ok=True)
        for rel in ("homework/hw51.pdf", "answerkeys/hw51.pdf"):
            with open(os.path.join(mat, *rel.split("/")), "wb") as f:
                f.write(b"%PDF-fake")

        class KeyBackend(FakeBackend):
            def page_texts(self, pdf_path):
                if "answerkeys" in pdf_path.replace(chr(92), "/"):
                    return ["Problem 1 Solution\n胶连目录的官方答案。"]
                return ["Problem 1\n胶连目录题面。"]
        code, payload, report = _run(mat, KeyBackend({}))
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)                              # 答案册没被当成第二份作业
        self.assertIn("胶连目录的官方答案", hw[0].get("answer", ""))
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("胶连目录的官方答案", wiki_all)

    def test_paired_single_keyed_entry_assigned_by_number(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw52.pdf": ["Problem 1\n配对单键题面一。\n\nProblem 2\n配对单键题面二。"],
                            "hw52_sol.pdf": ["Answers\n1. 配对单键答案一。"]})
        code, payload, report = _run(mat, be)
        by_num = {q["homework_number"]: q for q in payload["quiz_bank"]
                  if q.get("source_type") == "homework"}
        self.assertIn("配对单键答案一", by_num[1].get("answer", ""))   # 单键按号给 1
        self.assertNotIn("配对单键答案一", by_num[2].get("answer", "") or "")

    def test_bare_keyed_blank_not_stored_as_answer(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw53.pdf": ["Problem 1\n键控空栏题面。"],
                            "hw53_sol.pdf": ["Answers\n1. ________"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)
        self.assertFalse((hw[0].get("answer") or "").strip())     # 键控空栏不是官方答案

    def test_spaced_paren_lettered_keys_split(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw54.pdf": ["Problem 1 (a)\n空格括号题面甲。\n\nProblem 1 (b)\n空格括号题面乙。"],
                            "hw54_sol.pdf": ["Answers\n1 (a). 空格括号答案甲。\n1 (b). 空格括号答案乙。"]})
        code, payload, report = _run(mat, be)
        by_num = {q["homework_number"]: q for q in payload["quiz_bank"]
                  if q.get("source_type") == "homework"}
        self.assertIn("空格括号答案甲", by_num["1a"].get("answer", ""))
        self.assertIn("空格括号答案乙", by_num["1b"].get("answer", ""))

    def test_versioned_solution_filename_pairs(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw55.pdf": ["Problem 1\n版本后缀题面。"],
                            "hw55_solutions_v2.pdf": ["Problem 1 Solution\n版本后缀答案。"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)                              # 版本后缀册不再被当成第二份作业
        self.assertIn("版本后缀答案", hw[0].get("answer", ""))

    def test_chinese_decimal_problem_numbers_not_folded(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw56.pdf": ["习题1.1\n中文小数题面一。\n\n习题1.2\n中文小数题面二。"],
                            "hw56_sol.pdf": ["解答1.1\n中文小数答案一。\n\n解答1.2\n中文小数答案二。"]})
        code, payload, report = _run(mat, be)
        by_num = {q["homework_number"]: q for q in payload["quiz_bank"]
                  if q.get("source_type") == "homework"}
        self.assertIn("1.1", by_num)                              # 习题1.1 / 1.2 不折叠成同号
        self.assertIn("1.2", by_num)
        self.assertIn("中文小数答案一", by_num["1.1"].get("answer", ""))
        self.assertIn("中文小数答案二", by_num["1.2"].get("answer", ""))

    # ---- regression guards for Codex round-22 (3 findings) ----

    def test_numbered_answer_box_label_not_stored_as_answer(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw61.pdf": ["Problem 1\n1. Answer: Give a short proof in the box below."]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)
        self.assertFalse((hw[0].get("answer") or "").strip())     # 带号答题栏标签不是官方答案
        self.assertIn("box below", hw[0].get("question") or "")   # 指示语并回题面

    def test_compact_keyed_answer_rows_split(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw62.pdf": ["Problem 1\n紧凑键题面一。\n\nProblem 2\n紧凑键题面二。"],
                            "hw62_sol.pdf": ["1.紧凑键答案一。\n2)紧凑键答案二。"]})
        code, payload, report = _run(mat, be)
        by_num = {q["homework_number"]: q for q in payload["quiz_bank"]
                  if q.get("source_type") == "homework"}
        self.assertIn("紧凑键答案一", by_num[1].get("answer", ""))   # 1.A 紧凑形也按号拆
        self.assertIn("紧凑键答案二", by_num[2].get("answer", ""))

    def test_glued_prompt_letter_not_taken_as_subpart(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw63.pdf": ["Problem 2Compute 2+2 and explain."],
                            "hw63_sol.pdf": ["2. 胶连题号的答案。"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)
        self.assertEqual(hw[0]["homework_number"], 2)             # 不是 2c——胶连首字母不吞
        self.assertIn("胶连题号的答案", hw[0].get("answer", ""))   # 答案键 2. 配得上

    # ---- regression guards: post-merge follow-up (CI flake + 6 findings) ----

    def test_random_hwlike_root_name_stays_lecture(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "tmp_ps4abc")     # mkdtemp 随机名踩中 _ps4 的历史 CI flake
        os.makedirs(mat)
        with open(os.path.join(mat, "ch01.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        be = FakeBackend({"ch01.pdf": ["Quiz 1.1 Problem\n讲义题面。\nQuiz 1.1 Solution\n讲义答案。"]})
        code, payload, report = _run(mat, be)
        lec = [q for q in payload["quiz_bank"] if q["id"].startswith("lecture_")]
        self.assertTrue(lec)                                       # 讲义抽取不因根名误判被劫走
        self.assertFalse(report["homework_files"])

    def test_single_problem_numbered_steps_not_split(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw71.pdf": ["Problem 1\n单题册题面。"],
                            "hw71_sol.pdf": ["Solution\n1. 第一步先求导。\n2. 第二步解方程。"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)
        self.assertIn("第一步先求导", hw[0].get("answer", ""))      # 编号步骤是完整解答
        self.assertIn("第二步解方程", hw[0].get("answer", ""))      # 不被当成答案清单拆掉

    def test_versioned_solution_directory_pairs(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(os.path.join(mat, "homework"), exist_ok=True)
        os.makedirs(os.path.join(mat, "solutions_final"), exist_ok=True)
        for rel in ("homework/hw1.pdf", "solutions_final/hw1.pdf"):
            with open(os.path.join(mat, *rel.split("/")), "wb") as f:
                f.write(b"%PDF-fake")

        class VerBackend(FakeBackend):
            def page_texts(self, pdf_path):
                if "solutions_final" in pdf_path.replace(chr(92), "/"):
                    return ["Problem 1 Solution\n版本目录的官方答案。"]
                return ["Problem 1\n版本目录题面。"]
        code, payload, report = _run(mat, VerBackend({}))
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)                               # 不再当成第二份作业
        self.assertIn("版本目录的官方答案", hw[0].get("answer", ""))

    def test_decimal_space_answer_keys_split(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw73.pdf": ["Problem 1.1\n小数键题面一。\n\nProblem 1.2\n小数键题面二。"],
                            "hw73_sol.pdf": ["Answers\n1.1 小数键答案一。\n1.2 小数键答案二。"]})
        code, payload, report = _run(mat, be)
        by_num = {q["homework_number"]: q for q in payload["quiz_bank"]
                  if q.get("source_type") == "homework"}
        self.assertIn("小数键答案一", by_num["1.1"].get("answer", ""))
        self.assertIn("小数键答案二", by_num["1.2"].get("answer", ""))

    def test_real_solution_block_not_merged_into_prompt(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw74.pdf": ["Problem 1\nSolution\nThe answer is 42."]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)
        self.assertIn("42", hw[0].get("answer", ""))               # 真解答收为答案
        self.assertNotIn("42", hw[0].get("question") or "")        # 不并回题面泄答案

    def test_mixed_root_orphan_solutions_file_kept_out_of_wiki(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        for rel in ("hw1.pdf", "solutions.pdf", "lec01.pdf"):
            with open(os.path.join(mat, rel), "wb") as f:
                f.write(b"%PDF-fake")

        class MixBackend(FakeBackend):
            def page_texts(self, pdf_path):
                rel = pdf_path.replace(chr(92), "/")
                if rel.endswith("hw1.pdf"):
                    return ["Problem 1\n混合根题面。"]
                if rel.endswith("solutions.pdf"):
                    return ["混合根的纯答案文字。"]
                return ["混合根讲义正文。"]
        code, payload, report = _run(mat, MixBackend({}))
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("混合根的纯答案文字", wiki_all)            # 配不上也不准漏进 wiki
        self.assertIn("混合根讲义正文", wiki_all)

    def test_answer_to_number_headings_pair(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw76.pdf": ["Problem 1\n直呼号题面一。\n\nProblem 2\n直呼号题面二。"],
                            "hw76_sol.pdf": ["Answer to 1: 直呼号答案一。\nAnswer to 2: 直呼号答案二。"]})
        code, payload, report = _run(mat, be)
        by_num = {q["homework_number"]: q for q in payload["quiz_bank"]
                  if q.get("source_type") == "homework"}
        self.assertIn("直呼号答案一", by_num[1].get("answer", ""))
        self.assertIn("直呼号答案二", by_num[2].get("answer", ""))

    def test_stray_decimal_line_does_not_poison_key_split(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw81.pdf": ["Problem 1\n毒化题面一。\n\nProblem 2\n毒化题面二。"],
                            "hw81_sol.pdf": ["Answers\n1. 毒化答案一。\n2. 毒化答案二。\n3.14 is pi 的说明行。"]})
        code, payload, report = _run(mat, be)
        by_num = {q["homework_number"]: q for q in payload["quiz_bank"]
                  if q.get("source_type") == "homework"}
        self.assertIn("毒化答案一", by_num[1].get("answer", ""))    # 裸小数说明行不毒化整节拆分
        self.assertIn("毒化答案二", by_num[2].get("answer", ""))

    def test_multiline_answer_box_instruction_not_stored(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw82.pdf": ["Problem 1\nAnswer:\nWrite your answer in the box below."]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)
        self.assertFalse((hw[0].get("answer") or "").strip())     # 跨行指示语也是标签不是答案

    def test_mixed_root_qa_handout_stays_in_wiki(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        for rel in ("hw1.pdf", "questions_answers.pdf"):
            with open(os.path.join(mat, rel), "wb") as f:
                f.write(b"%PDF-fake")

        class QaBackend(FakeBackend):
            def page_texts(self, pdf_path):
                rel = pdf_path.replace(chr(92), "/")
                if rel.endswith("hw1.pdf"):
                    return ["Problem 1\n混合根题面。"]
                return ["问答讲义的正文内容。"]
        code, payload, report = _run(mat, QaBackend({}))
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertIn("问答讲义的正文内容", wiki_all)              # Q&A 讲义不因文件名被误杀

    def test_root_name_boundaries_hardened(self):
        for bad in ("mat-ps4keyboard", "tmp_hw3ansible", "非作业资料", "tmp习题abc"):
            self.assertIsNone(B._HW_ROOT_RE.search("/" + bad), bad)   # 后缀/中文都要词元边界
        for good in ("HW3", "hw2solutions", "ps4", "problem set 2", "作业3", "线代作业", "习题册",
                     "作业资料", "线代作业资料", "hw3final", "hw3v2"):
            self.assertIsNotNone(B._HW_ROOT_RE.search("/" + good), good)

    def test_bare_key_file_kept_out_of_wiki(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        for rel in ("hw1.pdf", "final_key.pdf"):
            with open(os.path.join(mat, rel), "wb") as f:
                f.write(b"%PDF-fake")

        class KeyBackend(FakeBackend):
            def page_texts(self, pdf_path):
                if pdf_path.replace(chr(92), "/").endswith("final_key.pdf"):
                    return ["裸键名答案册内容。"]
                return ["Problem 1\n裸键名题面。"]
        code, payload, report = _run(mat, KeyBackend({}))
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("裸键名答案册内容", wiki_all)              # key.pdf 类裸键名不漏 wiki

    def test_extra_numbered_note_does_not_break_key_split(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw91.pdf": ["Problem 1\n注记题面一。\n\nProblem 2\n注记题面二。"],
                            "hw91_sol.pdf": ["Answers\n1. 注记答案一。\n2. 注记答案二。\n3. Grading note only."]})
        code, payload, report = _run(mat, be)
        by_num = {q["homework_number"]: q for q in payload["quiz_bank"]
                  if q.get("source_type") == "homework"}
        self.assertIn("注记答案一", by_num[1].get("answer", ""))    # 尾随注记不打碎键拆分
        self.assertIn("注记答案二", by_num[2].get("answer", ""))
        self.assertIn("Grading note", by_num[2].get("answer", ""))   # 界外行随前一条答案保留，不截断

    def test_show_your_work_instruction_not_stored(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw92.pdf": ["Problem 1\nAnswer:\nShow your work."]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)
        self.assertFalse((hw[0].get("answer") or "").strip())     # 常见 worksheet 指令不是答案

    def test_bare_manual_handout_stays_in_wiki(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        for rel in ("hw1.pdf", "manual.pdf"):
            with open(os.path.join(mat, rel), "wb") as f:
                f.write(b"%PDF-fake")

        class ManBackend(FakeBackend):
            def page_texts(self, pdf_path):
                if pdf_path.replace(chr(92), "/").endswith("manual.pdf"):
                    return ["课程手册的正文内容。"]
                return ["Problem 1\n手册题面。"]
        code, payload, report = _run(mat, ManBackend({}))
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertIn("课程手册的正文内容", wiki_all)              # 裸 manual 是讲义不是答案册

    def test_chinese_homework_materials_root_recognized(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "作业资料")
        os.makedirs(mat)
        with open(os.path.join(mat, "1.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        be = FakeBackend({"1.pdf": ["Problem 1\n中文资料根的题面。"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)                               # 作业资料/ 根按作业上下文导入
        self.assertIn("中文资料根的题面", hw[0].get("question") or "")

    def test_decimal_space_blank_key_stays_unknown(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw93.pdf": ["Problem 1.1\n小数空栏题面。"],
                            "hw93_sol.pdf": ["Answers\n1.1 ________"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)
        self.assertFalse((hw[0].get("answer") or "").strip())     # 小数-空白空栏不是官方答案

    def test_out_of_range_step_stays_in_previous_answer(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw94.pdf": ["Problem 1\n界外题面一。\n\nProblem 2\n界外题面二。"],
                            "hw94_sol.pdf": ["Answers\n1. 界外答案一。\n2. 乙的前半段。\n3. 乙的第二步继续。"]})
        code, payload, report = _run(mat, be)
        by_num = {q["homework_number"]: q for q in payload["quiz_bank"]
                  if q.get("source_type") == "homework"}
        self.assertIn("乙的前半段", by_num[2].get("answer", ""))
        self.assertIn("乙的第二步继续", by_num[2].get("answer", ""))   # 界外编号步骤不截断答案

    def test_instruction_line_with_work_kept_as_answer(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw95.pdf": ["Problem 1\nSolution\nShow your work: 2+2=4."]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)
        self.assertIn("2+2=4", hw[0].get("answer", ""))            # 带算式的行是真解答
        self.assertNotIn("2+2=4", hw[0].get("question") or "")     # 不并回题面泄答案

    # ---- D1: 试卷管线 regression guards ----

    def test_exam_filename_recognition_boundaries(self):
        for good in ("midterm.pdf", "final_exam.pdf", "final 2019.pdf", "quiz3.pdf",
                     "past_paper.pdf", "sample exam.pdf", "practice-midterm.pdf",
                     "期末试卷A卷.pdf", "真题2014.pdf", "模拟卷.pdf", "模拟试卷（偏难).pdf",
                     "2019期中.pdf", "月考试题.pdf", "exams/2020.pdf",
                     "midtermsolutions.pdf", "examanswers.pdf", "期末时间复杂度试题.pdf",
                     "midtermsoln.pdf", "final1key.pdf", "exams/final.pdf", "pastpaperanswers.pdf"):
            self.assertTrue(B._is_exam_path(good), good)
        for bad in ("example.pdf", "os_example_question2.pdf", "final_version.pdf",
                    "final report.pdf", "模拟滤波器.pdf", "期末复习提纲.pdf",
                    "期中考试安排.pdf", "考试范围.pdf", "NetSec-期末复习提纲.pdf",
                    "final review.pdf", "lec01.pdf",
                    "midterm_notes/ch01.pdf", "期中复习/ch01.pdf", "exams/期末复习笔记.pdf",
                    "final_exam_prep/sorting.pdf", "exams/graph_theory.pdf",
                    "final_exam_format.pdf", "exam_info.pdf", "期末考试题型说明.pdf"):
            self.assertFalse(B._is_exam_path(bad), bad)

    def test_exam_paper_items_tagged_and_out_of_wiki(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        for rel in ("midterm.pdf", "lec01.pdf"):
            with open(os.path.join(mat, rel), "wb") as f:
                f.write(b"%PDF-fake")

        class ExBackend(FakeBackend):
            def page_texts(self, pdf_path):
                if pdf_path.replace(chr(92), "/").endswith("midterm.pdf"):
                    return ["Problem 1\n期中试卷的题面。"]
                return ["讲义正文内容。"]
        code, payload, report = _run(mat, ExBackend({}))
        ex = [q for q in payload["quiz_bank"] if q.get("source_type") == "exam"]
        self.assertEqual(len(ex), 1)                              # 试卷题以 source_type=exam 入库
        self.assertTrue(ex[0]["id"].startswith("exam_"))
        self.assertIn("期中试卷的题面", ex[0].get("question") or "")
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("期中试卷的题面", wiki_all)              # 试卷绝不进 wiki
        self.assertIn("midterm.pdf", report.get("exam_files", []))

    def test_exam_solutions_pair_and_never_leak(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        for rel in ("midterm.pdf", "midterm_solutions.pdf", "lec01.pdf"):
            with open(os.path.join(mat, rel), "wb") as f:
                f.write(b"%PDF-fake")

        class ExBackend(FakeBackend):
            def page_texts(self, pdf_path):
                rel = pdf_path.replace(chr(92), "/")
                if rel.endswith("midterm_solutions.pdf"):
                    return ["Problem 1 Solution\n期中卷的标准答案。"]
                if rel.endswith("midterm.pdf"):
                    return ["Problem 1\n期中卷题面。"]
                return ["讲义正文内容。"]
        code, payload, report = _run(mat, ExBackend({}))
        ex = [q for q in payload["quiz_bank"] if q.get("source_type") == "exam"]
        self.assertEqual(len(ex), 1)
        self.assertIn("期中卷的标准答案", ex[0].get("answer", ""))   # 试卷答案册照常配对
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("期中卷的标准答案", wiki_all)             # 审计 P1：答案册绝不泄 wiki

    def test_unpaired_exam_solutions_warn_and_stay_out_of_wiki(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        for rel in ("期末试卷答案.pdf", "lec01.pdf"):
            with open(os.path.join(mat, rel), "wb") as f:
                f.write(b"%PDF-fake")

        class ExBackend(FakeBackend):
            def page_texts(self, pdf_path):
                if "期末试卷答案" in pdf_path:
                    return ["一、标准答案甲。\n二、标准答案乙。"]
                return ["讲义正文内容。"]
        code, payload, report = _run(mat, ExBackend({}))
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("标准答案甲", wiki_all)                  # 除名逃逸漏洞已堵死
        self.assertTrue(any("hw_unpaired_solution_file" in w or "hw_no_markers" in w
                            or "exam_no_markers" in w for w in report["warnings"]))

    def test_exam_without_markers_hands_off_to_ai(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        for rel in ("真题2020.pdf", "lec01.pdf"):
            with open(os.path.join(mat, rel), "wb") as f:
                f.write(b"%PDF-fake")

        class ExBackend(FakeBackend):
            def page_texts(self, pdf_path):
                if "真题" in pdf_path:
                    return ["一、(10 分) 大题号排版的试卷正文。"]
                return ["讲义正文内容。"]
        code, payload, report = _run(mat, ExBackend({}))
        self.assertTrue(any(w.startswith("exam_no_markers") for w in report["warnings"]))
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("大题号排版", wiki_all)                  # 抽不出题也不准漏进 wiki

    def test_exam_root_folder_context(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "2023期末真题")
        os.makedirs(mat)
        with open(os.path.join(mat, "1.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        be = FakeBackend({"1.pdf": ["Problem 1\n试卷根目录的题面。"]})
        code, payload, report = _run(mat, be)
        ex = [q for q in payload["quiz_bank"] if q.get("source_type") == "exam"]
        self.assertEqual(len(ex), 1)                              # 根目录名的试卷上下文生效
        self.assertIn("试卷根目录的题面", ex[0].get("question") or "")

    def test_selfcontained_exam_solutions_tagged_exam(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        for rel in ("midterm_solutions.pdf", "lec01.pdf"):
            with open(os.path.join(mat, rel), "wb") as f:
                f.write(b"%PDF-fake")

        class ExBackend(FakeBackend):
            def page_texts(self, pdf_path):
                if "midterm_solutions" in pdf_path:
                    return ["Problem 1\n自含卷的题面。\nProblem 1 Solution\n自含卷的答案。"]
                return ["讲义正文内容。"]
        code, payload, report = _run(mat, ExBackend({}))
        ex = [q for q in payload["quiz_bank"] if q.get("source_type") == "exam"]
        self.assertEqual(len(ex), 1)                              # 自含题答册补进 exam_files
        self.assertTrue(ex[0]["id"].startswith("exam_"))
        self.assertIn("midterm_solutions.pdf", report.get("exam_files", []))

    def test_lecture_style_quiz_handout_reroutes_to_lecture(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        for rel in ("quiz3.pdf", "lec01.pdf"):
            with open(os.path.join(mat, rel), "wb") as f:
                f.write(b"%PDF-fake")

        class QzBackend(FakeBackend):
            def page_texts(self, pdf_path):
                if "quiz3" in pdf_path:
                    return ["Quiz 1.1 Problem\n讲义式小测题面。\nQuiz 1.1 Solution\n讲义式小测答案。"]
                return ["讲义正文内容。"]
        code, payload, report = _run(mat, QzBackend({}))
        lec = [q for q in payload["quiz_bank"] if q["id"].startswith("lecture_")]
        self.assertTrue(any("讲义式小测题面" in (q.get("question") or "") for q in lec))
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("讲义式小测答案", wiki_all)              # 归还讲义管线但仍不进 wiki
        self.assertTrue(any(w.startswith("exam_lecture_style") for w in report["warnings"]))
        self.assertFalse(any(w.startswith("exam_no_markers") for w in report["warnings"]))

    def test_exam_root_generic_solutions_never_leak(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "2023期末真题")
        os.makedirs(mat)
        for rel in ("1.pdf", "solutions.pdf"):
            with open(os.path.join(mat, rel), "wb") as f:
                f.write(b"%PDF-fake")
        be = FakeBackend({"1.pdf": ["Problem 1\n根卷题面。"],
                          "solutions.pdf": ["根卷的标准答案整册。"]})
        code, payload, report = _run(mat, be)
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("根卷的标准答案整册", wiki_all)          # 试卷根上下文的答案册不除名不泄漏

    def test_exam_key_file_is_solution_not_second_paper(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        for rel in ("midterm.pdf", "midterm_key.pdf", "lec01.pdf"):
            with open(os.path.join(mat, rel), "wb") as f:
                f.write(b"%PDF-fake")

        class KyBackend(FakeBackend):
            def page_texts(self, pdf_path):
                rel = pdf_path.replace(chr(92), "/")
                if rel.endswith("midterm_key.pdf"):
                    return ["Problem 1 Solution\n期中键答案。"]
                if rel.endswith("midterm.pdf"):
                    return ["Problem 1\n期中键题面。"]
                return ["讲义正文内容。"]
        code, payload, report = _run(mat, KyBackend({}))
        ex = [q for q in payload["quiz_bank"] if q.get("source_type") == "exam"]
        self.assertEqual(len(ex), 1)                              # key 是答案册，不是第二份卷子
        self.assertIn("期中键答案", ex[0].get("answer", ""))
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("期中键答案", wiki_all)

    def test_exam_dir_key_file_pairs_as_solution(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(os.path.join(mat, "exams"), exist_ok=True)
        for rel in ("exams/2020.pdf", "exams/2020_key.pdf"):
            with open(os.path.join(mat, *rel.split("/")), "wb") as f:
                f.write(b"%PDF-fake")
        with open(os.path.join(mat, "lec01.pdf"), "wb") as f:
            f.write(b"%PDF-fake")

        class KyBackend(FakeBackend):
            def page_texts(self, pdf_path):
                rel = pdf_path.replace(chr(92), "/")
                if rel.endswith("2020_key.pdf"):
                    return ["Problem 1 Solution\n目录卷键答案。"]
                if rel.endswith("2020.pdf"):
                    return ["Problem 1\n目录卷题面。"]
                return ["讲义正文内容。"]
        code, payload, report = _run(mat, KyBackend({}))
        ex = [q for q in payload["quiz_bank"] if q.get("source_type") == "exam"]
        self.assertEqual(len(ex), 1)                              # 目录上下文的 key 是答案册
        self.assertIn("目录卷键答案", ex[0].get("answer", ""))
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("目录卷键答案", wiki_all)

    def test_quiz_handout_with_stray_problem_line_prefers_lecture(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        for rel in ("quiz4.pdf", "lec01.pdf"):
            with open(os.path.join(mat, rel), "wb") as f:
                f.write(b"%PDF-fake")

        class QzBackend(FakeBackend):
            def page_texts(self, pdf_path):
                if "quiz4" in pdf_path:
                    return ["Quiz 1.1 Problem\n占优小测题面。\nQuiz 1.1 Solution\n占优小测答案。\n"
                            "Problem 1\n杂散的一行。"]
                return ["讲义正文内容。"]
        code, payload, report = _run(mat, QzBackend({}))
        ex = [q for q in payload["quiz_bank"] if q.get("source_type") == "exam"]
        self.assertFalse(ex)                                      # 讲义标记占优→不走作业解析器
        lec = [q for q in payload["quiz_bank"] if q["id"].startswith("lecture_")]
        self.assertTrue(any("占优小测题面" in (q.get("question") or "") for q in lec))
        self.assertFalse(any("占优小测答案" in (q.get("question") or "")
                             for q in payload["quiz_bank"]))       # 答案绝不卷进任何题面
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("占优小测答案", wiki_all)                 # 归还讲义管线但正文仍挡出 wiki

    def test_exam_root_does_not_swallow_chapterish_lectures(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "final_exam_prep")
        os.makedirs(mat)
        for rel in ("ch01.pdf", "1.pdf"):
            with open(os.path.join(mat, rel), "wb") as f:
                f.write(b"%PDF-fake")
        be = FakeBackend({"ch01.pdf": ["章节讲义的正文知识点。"],
                          "1.pdf": ["Problem 1\n备考卷题面。"]})
        code, payload, report = _run(mat, be)
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertIn("章节讲义的正文知识点", wiki_all)            # ch01 是讲义，不被根上下文吞掉
        ex = [q for q in payload["quiz_bank"] if q.get("source_type") == "exam"]
        self.assertEqual(len(ex), 1)                              # 裸编号 1.pdf 仍按卷子收
        self.assertIn("备考卷题面", ex[0].get("question") or "")

    def test_glued_exam_solutions_pair_and_never_leak(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        for rel in ("midterm.pdf", "midtermsolutions.pdf", "lec01.pdf"):
            with open(os.path.join(mat, rel), "wb") as f:
                f.write(b"%PDF-fake")

        class GlBackend(FakeBackend):
            def page_texts(self, pdf_path):
                rel = pdf_path.replace(chr(92), "/")
                if rel.endswith("midtermsolutions.pdf"):
                    return ["Problem 1 Solution\n胶连卷答案。"]
                if rel.endswith("midterm.pdf"):
                    return ["Problem 1\n胶连卷题面。"]
                return ["讲义正文内容。"]
        code, payload, report = _run(mat, GlBackend({}))
        ex = [q for q in payload["quiz_bank"] if q.get("source_type") == "exam"]
        self.assertEqual(len(ex), 1)
        self.assertIn("胶连卷答案", ex[0].get("answer", ""))      # midtermsolutions 拆胶连后配对
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("胶连卷答案", wiki_all)

    def test_mixed_prep_root_keeps_named_lectures(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "final_exam_prep")
        os.makedirs(mat)
        for rel in ("sorting.pdf", "1.pdf"):
            with open(os.path.join(mat, rel), "wb") as f:
                f.write(b"%PDF-fake")
        be = FakeBackend({"sorting.pdf": ["排序讲义的正文知识点。"],
                          "1.pdf": ["Problem 1\n备考卷一题面。"]})
        code, payload, report = _run(mat, be)
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertIn("排序讲义的正文知识点", wiki_all)            # 有名字的讲义不被备考根吞掉
        ex = [q for q in payload["quiz_bank"] if q.get("source_type") == "exam"]
        self.assertEqual(len(ex), 1)                              # 像卷子的 1.pdf 仍按卷收

    def test_exam_solutions_with_review_suffix_never_leak(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        for rel in ("midterm.pdf", "midterm_solutions_review.pdf", "lec01.pdf"):
            with open(os.path.join(mat, rel), "wb") as f:
                f.write(b"%PDF-fake")

        class RvBackend(FakeBackend):
            def page_texts(self, pdf_path):
                rel = pdf_path.replace(chr(92), "/")
                if rel.endswith("midterm_solutions_review.pdf"):
                    return ["带后缀答案册的标准答案。"]
                if rel.endswith("midterm.pdf"):
                    return ["Problem 1\n带后缀卷题面。"]
                return ["讲义正文内容。"]
        code, payload, report = _run(mat, RvBackend({}))
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("带后缀答案册的标准答案", wiki_all)      # 负例词不否决答案册的排除
        ex = [q for q in payload["quiz_bank"] if q.get("source_type") == "exam"]
        self.assertEqual(len(ex), 1)                              # round-5 起 review 尾缀可剥，
        self.assertIn("带后缀答案册的标准答案", ex[0].get("answer", ""))   # 答案册直接配回卷子

    def test_glued_compound_answer_key_pairs(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        for rel in ("midterm.pdf", "midtermanswerkey.pdf", "lec01.pdf"):
            with open(os.path.join(mat, rel), "wb") as f:
                f.write(b"%PDF-fake")

        class GkBackend(FakeBackend):
            def page_texts(self, pdf_path):
                rel = pdf_path.replace(chr(92), "/")
                if rel.endswith("midtermanswerkey.pdf"):
                    return ["Problem 1 Solution\n复合胶连键答案。"]
                if rel.endswith("midterm.pdf"):
                    return ["Problem 1\n复合胶连键题面。"]
                return ["讲义正文内容。"]
        code, payload, report = _run(mat, GkBackend({}))
        ex = [q for q in payload["quiz_bank"] if q.get("source_type") == "exam"]
        self.assertEqual(len(ex), 1)
        self.assertIn("复合胶连键答案", ex[0].get("answer", ""))   # answerkey 复合胶连也拆
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("复合胶连键答案", wiki_all)

    def test_review_suffixed_exam_key_pairs_and_never_leaks(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        for rel in ("midterm.pdf", "midterm_key_review.pdf", "lec01.pdf"):
            with open(os.path.join(mat, rel), "wb") as f:
                f.write(b"%PDF-fake")

        class KrBackend(FakeBackend):
            def page_texts(self, pdf_path):
                rel = pdf_path.replace(chr(92), "/")
                if rel.endswith("midterm_key_review.pdf"):
                    return ["Problem 1 Solution\n带审阅后缀键答案。"]
                if rel.endswith("midterm.pdf"):
                    return ["Problem 1\n带审阅后缀键题面。"]
                return ["讲义正文内容。"]
        code, payload, report = _run(mat, KrBackend({}))
        ex = [q for q in payload["quiz_bank"] if q.get("source_type") == "exam"]
        self.assertEqual(len(ex), 1)                              # review 后缀不否决答案册锚定
        self.assertIn("带审阅后缀键答案", ex[0].get("answer", ""))
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("带审阅后缀键答案", wiki_all)

    def test_exam_root_generic_solutions_reroute_with_paper(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "2020期末真题")
        os.makedirs(mat)
        for rel in ("1.pdf", "solutions.pdf"):
            with open(os.path.join(mat, rel), "wb") as f:
                f.write(b"%PDF-fake")
        be = FakeBackend({"1.pdf": ["Quiz 1.1 Problem\n随卷小测题面。"],
                          "solutions.pdf": ["Quiz 1.1 Solution\n随卷小测官方解答。"]})
        code, payload, report = _run(mat, be)
        lec = [q for q in payload["quiz_bank"] if q["id"].startswith("lecture_")]
        got = [q for q in lec if "随卷小测题面" in (q.get("question") or "")]
        self.assertTrue(got)                                      # 卷子归还讲义管线
        self.assertIn("随卷小测官方解答", got[0].get("answer", ""))   # 解答册随行配对
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("随卷小测官方解答", wiki_all)             # 正文照旧挡出 wiki

    def test_exam_dir_bare_key_excluded_and_warned(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(os.path.join(mat, "exams"), exist_ok=True)
        for rel in ("exams/2020.pdf", "exams/key.pdf"):
            with open(os.path.join(mat, *rel.split("/")), "wb") as f:
                f.write(b"%PDF-fake")
        with open(os.path.join(mat, "lec01.pdf"), "wb") as f:
            f.write(b"%PDF-fake")

        class BkBackend(FakeBackend):
            def page_texts(self, pdf_path):
                rel = pdf_path.replace(chr(92), "/")
                if rel.endswith("exams/key.pdf"):
                    return ["裸键册的标准答案。"]
                if rel.endswith("2020.pdf"):
                    return ["Problem 1\n裸键卷题面。"]
                return ["讲义正文内容。"]
        code, payload, report = _run(mat, BkBackend({}))
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("裸键册的标准答案", wiki_all)            # 试卷目录的裸 key.pdf 判解答不泄漏
        self.assertTrue(any("hw_unpaired_solution_file" in w or "hw_no_markers" in w
                            for w in report["warnings"]))

    def test_glued_soln_abbreviation_pairs(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        for rel in ("midterm.pdf", "midtermsoln.pdf", "lec01.pdf"):
            with open(os.path.join(mat, rel), "wb") as f:
                f.write(b"%PDF-fake")

        class SnBackend(FakeBackend):
            def page_texts(self, pdf_path):
                rel = pdf_path.replace(chr(92), "/")
                if rel.endswith("midtermsoln.pdf"):
                    return ["Problem 1 Solution\n缩写胶连答案。"]
                if rel.endswith("midterm.pdf"):
                    return ["Problem 1\n缩写胶连题面。"]
                return ["讲义正文内容。"]
        code, payload, report = _run(mat, SnBackend({}))
        ex = [q for q in payload["quiz_bank"] if q.get("source_type") == "exam"]
        self.assertEqual(len(ex), 1)
        self.assertIn("缩写胶连答案", ex[0].get("answer", ""))    # midtermsoln 判解答并配对

    def test_exam_format_handout_stays_in_wiki(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        for rel in ("exam_info.pdf", "lec01.pdf"):
            with open(os.path.join(mat, rel), "wb") as f:
                f.write(b"%PDF-fake")

        class FmBackend(FakeBackend):
            def page_texts(self, pdf_path):
                if "exam_info" in pdf_path:
                    return ["考试形式说明：闭卷，允许一页 A4 小抄。"]
                return ["讲义正文内容。"]
        code, payload, report = _run(mat, FmBackend({}))
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertIn("允许一页 A4 小抄", wiki_all)               # 考试说明类是复习材料，留在 wiki

    def test_exam_root_selfcontained_generic_tagged_exam(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "2020期末真题")
        os.makedirs(mat)
        with open(os.path.join(mat, "solutions.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        be = FakeBackend({"solutions.pdf": ["Problem 1\n自含根卷的题面文字。\n"
                                            "Problem 1 Solution\n自含根卷答案。"]})
        code, payload, report = _run(mat, be)
        ex = [q for q in payload["quiz_bank"] if q.get("source_type") == "exam"]
        self.assertEqual(len(ex), 1)                              # 试卷根的 generic 自含册打 exam 标签
        self.assertTrue(ex[0]["id"].startswith("exam_"))

    def test_numbered_glued_key_pairs(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        for rel in ("final1.pdf", "final1key.pdf", "lec01.pdf"):
            with open(os.path.join(mat, rel), "wb") as f:
                f.write(b"%PDF-fake")

        class NkBackend(FakeBackend):
            def page_texts(self, pdf_path):
                rel = pdf_path.replace(chr(92), "/")
                if rel.endswith("final1key.pdf"):
                    return ["Problem 1 Solution\n带号胶连键答案。"]
                if rel.endswith("final1.pdf"):
                    return ["Problem 1\n带号胶连键题面。"]
                return ["讲义正文内容。"]
        code, payload, report = _run(mat, NkBackend({}))
        ex = [q for q in payload["quiz_bank"] if q.get("source_type") == "exam"]
        self.assertEqual(len(ex), 1)
        self.assertIn("带号胶连键答案", ex[0].get("answer", ""))   # final1key 拆胶连配回 final1

    def test_bare_final_in_exam_root_is_paper(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "final_exam")
        os.makedirs(mat)
        with open(os.path.join(mat, "final.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        be = FakeBackend({"final.pdf": ["Problem 1\n裸名卷题面。"]})
        code, payload, report = _run(mat, be)
        ex = [q for q in payload["quiz_bank"] if q.get("source_type") == "exam"]
        self.assertEqual(len(ex), 1)                              # 试卷根里的裸 final.pdf 是卷子
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("裸名卷题面", wiki_all)

    def test_root_level_final_solutions_never_leak(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        for rel in ("final_solutions.pdf", "lec01.pdf"):
            with open(os.path.join(mat, rel), "wb") as f:
                f.write(b"%PDF-fake")

        class FsBackend(FakeBackend):
            def page_texts(self, pdf_path):
                if "final_solutions" in pdf_path:
                    return ["期末卷的标准答案整册。"]
                return ["讲义正文内容。"]
        code, payload, report = _run(mat, FsBackend({}))
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("期末卷的标准答案整册", wiki_all)         # 裸 final 的答案册不除名不泄漏
        self.assertTrue(any("hw_unpaired_solution_file" in w or "hw_no_markers" in w
                            for w in report["warnings"]))

    def test_glued_pastpaper_answers_pair(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        for rel in ("pastpaper.pdf", "pastpaperanswers.pdf", "lec01.pdf"):
            with open(os.path.join(mat, rel), "wb") as f:
                f.write(b"%PDF-fake")

        class PpBackend(FakeBackend):
            def page_texts(self, pdf_path):
                rel = pdf_path.replace(chr(92), "/")
                if rel.endswith("pastpaperanswers.pdf"):
                    return ["Problem 1 Solution\n历年卷胶连答案。"]
                if rel.endswith("pastpaper.pdf"):
                    return ["Problem 1\n历年卷题面。"]
                return ["讲义正文内容。"]
        code, payload, report = _run(mat, PpBackend({}))
        ex = [q for q in payload["quiz_bank"] if q.get("source_type") == "exam"]
        self.assertEqual(len(ex), 1)
        self.assertIn("历年卷胶连答案", ex[0].get("answer", ""))   # paper 族胶连拆分配对
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("历年卷胶连答案", wiki_all)

    # ---- D2: 页级零静默丢失 + AI 接管清单 regression guards ----

    def test_page_number_residue_pdf_flagged_as_scanned(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {})
        with open(os.path.join(tmp, "mat", "scan.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        with open(os.path.join(tmp, "mat", "lec01.pdf"), "wb") as f:
            f.write(b"%PDF-fake")

        class ScBackend(FakeBackend):
            def page_texts(self, pdf_path):
                if "scan" in pdf_path:
                    return ["12", "13", "Page 14 of 20"]           # 每页只剩页码残渣
                return ["讲义正文内容。"]
        code, payload, report = _run(mat, ScBackend({}))
        self.assertTrue(any(w.startswith("pdf_no_text") for w in report["warnings"]))
        self.assertTrue(any(e["kind"] == "scanned_pdf" and "多模态" in e["action"]
                            for e in report.get("ai_review", [])))  # 残渣骗不过 + 移交 AI

    def test_partial_scanned_pages_flagged_with_page_numbers(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {})
        with open(os.path.join(tmp, "mat", "mixed.pdf"), "wb") as f:
            f.write(b"%PDF-fake")

        class MxBackend(FakeBackend):
            def page_texts(self, pdf_path):
                return ["第一页正文知识点。", "37", "", "第四页正文知识点。"]
        code, payload, report = _run(mat, MxBackend({}))
        w = [x for x in report["warnings"] if x.startswith("pdf_pages_no_text")]
        self.assertTrue(w and "p2-3" in w[0])                     # 页级留痕（含页号区间）
        ent = [e for e in report.get("ai_review", []) if e["kind"] == "pages_no_text"]
        self.assertEqual(ent[0]["pages"], [2, 3])

    def test_all_residue_materials_build_blocked_review_payload(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {})
        with open(os.path.join(tmp, "mat", "scan.pdf"), "wb") as f:
            f.write(b"%PDF-fake")

        class RsBackend(FakeBackend):
            def page_texts(self, pdf_path):
                return ["12", "13"]
        code, payload, report = _run(mat, RsBackend({}))
        self.assertEqual(code, 0)
        self.assertIn("no_text_extracted", report["warnings"])
        self.assertTrue(any(
            row.get("severity") == "blocking"
            for row in payload["ingestion"]["review_candidates"]
        ))

    def test_damaged_supported_ooxml_left_a_trace(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw1.pdf": ["Problem 1\n题面。"]})
        with open(os.path.join(tmp, "mat", "slides.pptx"), "wb") as f:
            f.write(b"PK-fake")
        with open(os.path.join(tmp, "mat", "Thumbs.db"), "wb") as f:
            f.write(b"junk")
        code, payload, report = _run(mat, be)
        self.assertTrue(any(w.startswith("ooxml_extract_failed") and "slides.pptx" in w
                            for w in report["warnings"]))
        ent = [e for e in report.get("ai_review", []) if e["kind"] == "ooxml_extract_failed"]
        self.assertEqual(len(ent), 1)                             # junk（Thumbs.db）不入清单
        self.assertIn("slides.pptx", ent[0]["file"])

    def test_gbk_text_decoded_with_warning(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"lec01.pdf": ["讲义正文内容。"]})
        with open(os.path.join(tmp, "mat", "notes.txt"), "wb") as f:
            f.write("GBK 编码的讲义要点。".encode("gbk"))
        code, payload, report = _run(mat, be)
        self.assertTrue(any(w.startswith("encoding_fallback") for w in report["warnings"]))
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertIn("GBK 编码的讲义要点", wiki_all)             # 正确解码入库、不乱码

    def test_undecodable_text_skipped_with_manifest(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"lec01.pdf": ["讲义正文内容。"]})
        with open(os.path.join(tmp, "mat", "bad.txt"), "wb") as f:
            f.write(b"\xff\xfe\xff\x00garbage\xff")
        code, payload, report = _run(mat, be)
        self.assertTrue(any(w.startswith("undecodable_text") for w in report["warnings"]))
        self.assertTrue(any(e["kind"] == "undecodable_text"
                            for e in report.get("ai_review", [])))   # 解不了码 → 移交不硬灌

    def test_extract_exception_promoted_to_warning(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {})
        for rel in ("broken.pdf", "lec01.pdf"):
            with open(os.path.join(tmp, "mat", rel), "wb") as f:
                f.write(b"%PDF-fake")

        class BrBackend(FakeBackend):
            def page_texts(self, pdf_path):
                if "broken" in pdf_path:
                    raise RuntimeError("模拟损坏")
                return ["讲义正文内容。"]
        code, payload, report = _run(mat, BrBackend({}))
        self.assertTrue(any(w.startswith("pdf_extract_failed") for w in report["warnings"]))
        self.assertTrue(any(e["kind"] == "extract_failed"
                            for e in report.get("ai_review", [])))   # 异常不再只藏在 skipped

    def test_residue_pdf_pages_never_enter_wiki(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {})
        for rel in ("scan.pdf", "lec01.pdf"):
            with open(os.path.join(tmp, "mat", rel), "wb") as f:
                f.write(b"%PDF-fake")

        class ScBackend(FakeBackend):
            def page_texts(self, pdf_path):
                if "scan" in pdf_path:
                    return ["12", "13"]
                return ["讲义正文内容。"]
        code, payload, report = _run(mat, ScBackend({}))
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("scan.pdf", wiki_all)                    # 残渣页绝不写进 wiki
        self.assertTrue(any(w.startswith("pdf_no_text") for w in report["warnings"]))

    def test_pagecount_footers_are_residue(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {})
        for rel in ("deck.pdf", "lec01.pdf"):
            with open(os.path.join(tmp, "mat", rel), "wb") as f:
                f.write(b"%PDF-fake")

        class FtBackend(FakeBackend):
            def page_texts(self, pdf_path):
                if "deck" in pdf_path:
                    return ["1/20", "2 / 20", "3/20"]              # 纯页脚计数
                return ["讲义正文内容。"]
        code, payload, report = _run(mat, FtBackend({}))
        self.assertTrue(any(w.startswith("pdf_no_text") for w in report["warnings"]))
        self.assertTrue(any(e["kind"] == "scanned_pdf" for e in report.get("ai_review", [])))

    def test_big5_text_decoded_correctly(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"lec01.pdf": ["讲义正文内容。"]})
        with open(os.path.join(tmp, "mat", "notes_tw.txt"), "wb") as f:
            f.write("繁體中文講義的重點內容，考試複習必讀章節。".encode("big5"))
        code, payload, report = _run(mat, be)
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertIn("繁體中文講義", wiki_all)                   # Big5 不被 GBK 抢先解成乱码
        self.assertTrue(any(w.startswith("encoding_fallback") and "BIG5" in w
                            for w in report["warnings"]))

    def test_hangul_text_counts_as_content(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"lec01.pdf": ["讲义正文内容。"]})
        with open(os.path.join(tmp, "mat", "korea.txt"), "wb") as f:
            f.write("자료 구조 강의 노트".encode("utf-8"))
        code, payload, report = _run(mat, be)
        self.assertEqual(code, 0)
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertIn("자료 구조", wiki_all)                      # 非中英文字母也算有效内容

    def test_bare_numeric_answer_companion_still_claimed(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw71.pdf": ["Problem 1\n裸数答案题面。"],
                            "hw71_sol.pdf": ["4"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)
        self.assertEqual((hw[0].get("answer") or "").strip(), "4")   # 配对认领的裸答案册不当扫描件丢
        self.assertFalse(any(e.get("file") == "hw71_sol.pdf"
                             for e in report.get("ai_review", [])))

    def test_scanned_homework_pdf_handed_to_ai(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {})
        for rel in ("hw1.pdf", "lec01.pdf"):
            with open(os.path.join(tmp, "mat", rel), "wb") as f:
                f.write(b"%PDF-fake")

        class ShBackend(FakeBackend):
            def page_texts(self, pdf_path):
                if "hw1" in pdf_path:
                    return ["12", ""]                              # 扫描的作业题面册
                return ["讲义正文内容。"]
        code, payload, report = _run(mat, ShBackend({}))
        self.assertTrue(any(e["kind"] == "scanned_pdf" and e["file"] == "hw1.pdf"
                            for e in report.get("ai_review", [])))  # 题面册产不出题必须移交

    def test_low_confidence_gbk_still_ingested_with_review(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"lec01.pdf": ["讲义正文内容。"]})
        with open(os.path.join(tmp, "mat", "short.txt"), "wb") as f:
            f.write("概率论资料".encode("gbk"))                    # 短文本常用字占比低
        code, payload, report = _run(mat, be)
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertIn("概率论资料", wiki_all)                     # 严格解码成功的数据绝不丢
        self.assertFalse(any(w.startswith("undecodable_text") for w in report["warnings"]))

    def test_mixed_pdf_residue_pages_kept_out_of_wiki(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {})
        with open(os.path.join(tmp, "mat", "mixed2.pdf"), "wb") as f:
            f.write(b"%PDF-fake")

        class MpBackend(FakeBackend):
            def page_texts(self, pdf_path):
                return ["第一页正文知识点。", "37", "第三页正文知识点。"]
        code, payload, report = _run(mat, MpBackend({}))
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertIn("第一页正文知识点", wiki_all)
        self.assertNotIn("mixed2.pdf p.2", wiki_all)              # 残渣页不进 wiki

    def test_log_file_is_not_silently_junked(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw1.pdf": ["Problem 1\n题面。"]})
        with open(os.path.join(tmp, "mat", "server.log"), "wb") as f:
            f.write(b"real material maybe")
        code, payload, report = _run(mat, be)
        self.assertTrue(any(w.startswith("unsupported_format") and "server.log" in w
                            for w in report["warnings"]))         # 只豁免已知垃圾名，不按扩展名猜

    def test_multiline_residue_solution_not_consumed_as_answer(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw72.pdf": ["Problem 1\n多行残渣题面。"]})
        with open(os.path.join(mat, "homework", "hw72_sol.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        be.texts["hw72_sol.pdf"] = ["12", "13"]                   # 逐页页码残渣
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)
        self.assertFalse((hw[0].get("answer") or "").strip())     # 残渣绝不当官方答案
        self.assertTrue(any(e["kind"] == "scanned_pdf" and "hw72_sol" in e["file"]
                            for e in report.get("ai_review", [])))

    def test_slide_footer_deck_flagged_as_scanned(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {})
        for rel in ("deck2.pdf", "lec01.pdf"):
            with open(os.path.join(tmp, "mat", rel), "wb") as f:
                f.write(b"%PDF-fake")

        class SlBackend(FakeBackend):
            def page_texts(self, pdf_path):
                if "deck2" in pdf_path:
                    return ["Slide 1", "slide 2", "Slide 3 of 20"]
                return ["讲义正文内容。"]
        code, payload, report = _run(mat, SlBackend({}))
        self.assertTrue(any(w.startswith("pdf_no_text") for w in report["warnings"]))
        self.assertTrue(any(e["kind"] == "scanned_pdf" for e in report.get("ai_review", [])))

    def test_single_residue_line_with_multiproblem_hw_handed_off(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw73.pdf": ["Problem 1\n多题一。\n\nProblem 2\n多题二。"]})
        with open(os.path.join(mat, "homework", "hw73_sol.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        be.texts["hw73_sol.pdf"] = ["4"]                          # 单行残渣配多题作业吃不下
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 2)
        self.assertFalse(any((q.get("answer") or "").strip() for q in hw))
        self.assertTrue(any(e["kind"] == "scanned_pdf" and "hw73_sol" in e["file"]
                            for e in report.get("ai_review", [])))   # 白认领改移交

    def test_numeric_table_text_is_content(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"lec01.pdf": ["讲义正文内容。"]})
        with open(os.path.join(tmp, "mat", "data.txt"), "wb") as f:
            f.write("0.12\n0.37\n0.55".encode("utf-8"))
        code, payload, report = _run(mat, be)
        self.assertEqual(code, 0)
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertIn("0.37", wiki_all)                           # 多行数字数据表不是残渣

    def test_footer_page_not_swept_into_answer(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw74.pdf": ["Problem 1\n页脚扫尾题面。"]})
        with open(os.path.join(mat, "homework", "hw74_sol.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        be.texts["hw74_sol.pdf"] = ["Problem 1 Solution\n页脚扫尾的官方答案。", "37"]
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)
        self.assertIn("页脚扫尾的官方答案", hw[0].get("answer", ""))
        self.assertNotIn("37", hw[0].get("answer", ""))           # 残渣页不卷进答案切片
        self.assertNotIn(2, hw[0].get("answer_source_pages") or [])

    # ---- D3: 警告消费契约 + 绝不猜清理 regression guards ----

    def test_filename_year_not_taken_as_chapter(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {})
        for rel in ("march-2024.pdf", "lec_ch02.pdf"):
            with open(os.path.join(tmp, "mat", rel), "wb") as f:
                f.write(b"%PDF-fake")

        class YrBackend(FakeBackend):
            def page_texts(self, pdf_path):
                if "march" in pdf_path:
                    return ["三月讲义的正文。"]
                return ["第二章讲义的正文。"]
        code, payload, report = _run(mat, YrBackend({}))
        self.assertFalse(any(ph.get("phase_num") == 2024 or "2024" in (ph.get("phase_name") or "")
                             for ph in payload["phases"]))        # march-2024 不捏造第 2024 章
        self.assertTrue(any(w.startswith("chapter_unassigned") and "march-2024" in w
                            for w in report["warnings"]))         # 无线索并入是猜测，必须留痕
        self.assertFalse(any("lec_ch02" in w for w in report["warnings"]
                             if w.startswith("chapter_unassigned")))   # 有线索的不误报

    def test_empty_wiki_flagged_loudly(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw1.pdf": ["Problem 1\n只有作业的材料。"]})
        code, payload, report = _run(mat, be)
        self.assertTrue(any(w.startswith("wiki_empty") for w in report["warnings"]))
        self.assertTrue(any(e["kind"] == "wiki_empty" for e in report.get("ai_review", [])))

    def test_ambiguous_pairing_leaves_reasoned_trace(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw1a.pdf": ["Problem 1\n甲卷题面。"],
                            "hw1b.pdf": ["Problem 1\n乙卷题面。"],
                            "hw1_sol.pdf": ["Problem 1 Solution\n歧义答案。"]})
        code, payload, report = _run(mat, be)
        self.assertTrue(any(w.startswith("hw_pairing_ambiguous") and "hw1_sol" in w
                            for w in report["warnings"]))         # 歧义放弃必须说明原因

    def test_reassigned_generic_solution_leaves_trace(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(os.path.join(mat, "solutions"), exist_ok=True)
        os.makedirs(os.path.join(mat, "lectures"), exist_ok=True)
        for rel in ("solutions/week1.pdf", "lectures/ch01.pdf"):
            with open(os.path.join(mat, *rel.split("/")), "wb") as f:
                f.write(b"%PDF-fake")

        class RaBackend(FakeBackend):
            def page_texts(self, pdf_path):
                if "solutions" in pdf_path.replace(chr(92), "/"):
                    return ["Quiz 1.1 Solution\n讲义解答内容。"]
                return ["Quiz 1.1 Problem\n讲义题面。"]
        code, payload, report = _run(mat, RaBackend({}))
        self.assertTrue(any(w.startswith("hw_solution_reassigned_to_lecture")
                            for w in report["warnings"]))         # 改道交还讲义也要留痕

    def test_camelcase_chapter_filename_detected(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {})
        with open(os.path.join(tmp, "mat", "LectureChapter02.pdf"), "wb") as f:
            f.write(b"%PDF-fake")

        class CcBackend(FakeBackend):
            def page_texts(self, pdf_path):
                return ["驼峰命名章节的正文。"]
        code, payload, report = _run(mat, CcBackend({}))
        self.assertTrue(any(ph.get("phase_num") == 2 or "第 2 章" in (ph.get("phase_name") or "")
                            for ph in payload["phases"]))         # CamelCase Chapter02 归第 2 章
        self.assertTrue(B.re.search(r"(?<=[a-z])Ch(?:apter)?[ _-]?0*(\d+)", "LectureCh02"))
        self.assertIsNone(B.re.search(r"(?<=[a-z])Ch(?:apter)?[ _-]?0*(\d+)", "march-2024"))
        self.assertFalse(any(w.startswith("chapter_unassigned")
                             for w in report["warnings"]))

    # ---- D4: 题型识别 + 未知题型警报 regression guards ----

    def test_mcq_detected_with_options(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw96.pdf": ["Problem 1\n下列哪个说法正确？\nA. 甲选项\nB. 乙选项\n"
                                         "C. 丙选项继续\n换行的丙。\nD. 丁选项"],
                            "hw96_sol.pdf": ["1. B"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 1)
        self.assertEqual(hw[0]["type"], "choice")                  # 大写连续选项行 → 选择题
        self.assertEqual(len(hw[0]["options"]), 4)
        self.assertIn("换行的丙", hw[0]["options"][2])             # 选项续行并入
        self.assertEqual(hw[0].get("answer"), "B")               # 键前缀剥掉，validator 可过

    def test_lowercase_subparts_not_choice(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw97.pdf": ["Problem 1\n(a) 求导数。\n(b) 求积分。"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(hw[0]["type"], "subjective")              # 小写小问不是选项，绝不猜

    def test_blank_prompt_is_fill_blank(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw98.pdf": ["Problem 1\n函数 f(x) 的导数是 ______ 。"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(hw[0]["type"], "fill_blank")              # 题面填空线 → 填空题

    def test_lecture_quiz_mcq_detected(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        with open(os.path.join(mat, "lec01.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        be = FakeBackend({"lec01.pdf": ["Quiz 1.1 Problem\n选出正确项：\nA. 对的\nB. 错的\n"
                                        "Quiz 1.1 Solution\nA"]})
        code, payload, report = _run(mat, be)
        lec = [q for q in payload["quiz_bank"] if q["id"].startswith("lecture_quiz")]
        self.assertTrue(lec)
        self.assertEqual(lec[0]["type"], "choice")                 # 讲义测验同样识别
        self.assertEqual(len(lec[0]["options"]), 2)
        self.assertEqual(lec[0].get("answer"), "A")                # 解答标题剥掉归一成字母

    def test_type_heuristic_warning_and_review_entry(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw99.pdf": ["Problem 1\n普通主观题面。"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertTrue(any(w.startswith("type_heuristic") for w in report["warnings"]))
        reviews = [
            entry for entry in report.get("ai_review", [])
            if entry["kind"] == "type_defaulted"
        ]
        self.assertEqual(1, len(reviews))                          # 一题一条 review，避免跨章误关闭
        self.assertEqual([hw[0]["id"]], reviews[0]["external_ids"])
        self.assertEqual("homework/hw99.pdf", reviews[0]["file"])
        self.assertEqual([1], reviews[0]["pages"])

    def test_choice_with_unnormalizable_answer_downgrades(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw100.pdf": ["Problem 1\n选出正确项：\nA. 甲\nB. 乙"],
                            "hw100_sol.pdf": ["1. 因为甲显然不对，所以综合判断选乙（详见教材）。"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(hw[0]["type"], "subjective")              # 长解答归一不了→降级不硬标
        self.assertNotIn("options", hw[0])
        self.assertIn("选乙", hw[0].get("answer", ""))             # 答案原文保留

    def test_five_option_mcq_detected(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw101.pdf": ["Problem 1\n五选一：\nA. 甲\nB. 乙\nC. 丙\nD. 丁\nE. 戊"],
                            "hw101_sol.pdf": ["1. E"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(hw[0]["type"], "choice")
        self.assertEqual(len(hw[0]["options"]), 5)                 # E 选项不再被吞进 D
        self.assertEqual(hw[0].get("answer"), "E")

    def test_response_box_blank_stays_subjective(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw102.pdf": ["Problem 1\n求积分并写出过程。\nAnswer: ________"],
                            "hw103.pdf": ["Problem 1\n证明不等式。\nShow your work: ________"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 2)
        for q in hw:
            self.assertEqual(q["type"], "subjective")              # 答题栏空线不是题面挖空

    def test_six_option_mcq_detected(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw104.pdf": ["Problem 1\n六选一：\nA. 甲\nB. 乙\nC. 丙\nD. 丁\nE. 戊\nF. 己"],
                            "hw104_sol.pdf": ["1. F"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(hw[0]["type"], "choice")
        self.assertEqual(len(hw[0]["options"]), 6)                 # F 选项不再被吞进 E
        self.assertEqual(hw[0].get("answer"), "F")

    def test_lettered_subproblem_mcq_key_normalized(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw105.pdf": ["Problem 1a\n小问选择：\nA. 甲\nB. 乙"],
                            "hw105_sol.pdf": ["Answers\n1a. B"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(hw[0]["type"], "choice")                  # 字母小问键前缀可剥
        self.assertEqual(hw[0].get("answer"), "B")

    def test_trailing_instruction_not_folded_into_option(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw106.pdf": ["Problem 1\n选出正确项：\nA. 甲\nB. 乙\n\nExplain your choice."],
                            "hw106_sol.pdf": ["1. 乙"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(hw[0]["type"], "choice")
        self.assertEqual(hw[0]["options"][1], "B. 乙")             # 空行后说明不并入选项
        self.assertEqual(hw[0].get("answer"), "B")                 # 按选项正文归一成字母

    def test_fill_blank_answer_stripped_of_heading(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        with open(os.path.join(mat, "lec01.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        be = FakeBackend({"lec01.pdf": ["Quiz 1.1 Problem\n栈的出入顺序是 ______ 。\n"
                                        "Quiz 1.1 Solution\nLIFO"]})
        code, payload, report = _run(mat, be)
        lec = [q for q in payload["quiz_bank"] if q["id"].startswith("lecture_quiz")]
        self.assertEqual(lec[0]["type"], "fill_blank")
        self.assertEqual(lec[0].get("answer"), "LIFO")             # 判分对的是填的值，不是标题

    def test_answer_prefix_stripping_precision(self):
        self.assertEqual(B._strip_answer_prefix("Answer 1: B"), "B")          # 欠剥修复
        self.assertEqual(B._strip_answer_prefix("Answer: LIFO"), "LIFO")
        self.assertEqual(B._strip_answer_prefix("Solution set"), "Solution set")   # 过剥修复
        self.assertEqual(B._strip_answer_prefix("Quiz 1.1 Solution Solution set"), "Solution set")
        self.assertEqual(B._strip_answer_prefix("Problem 1 Solution B"), "B")
        self.assertEqual(B._strip_answer_prefix("1(a). B"), "B")
        self.assertEqual(B._strip_answer_prefix("Answer: 0.5"), "0.5")        # 小数不是键
        self.assertEqual(B._strip_answer_prefix("Quiz 1.1 Solution 0.5"), "0.5")
        self.assertEqual(B._strip_answer_prefix("Answer: 1.5 表示比例"), "1.5 表示比例")

    def test_instruction_after_options_without_blank_line(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw107.pdf": ["Problem 1\n选出正确项：\nA. alpha\nB. beta\nExplain your choice."],
                            "hw107_sol.pdf": ["1. beta"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(hw[0]["type"], "choice")
        self.assertEqual(hw[0]["options"][1], "B. beta")           # 无空行的指令也不卷进选项
        self.assertEqual(hw[0].get("answer"), "B")                 # 选项正文归一不受污染

    def test_prompt_mentioning_answer_still_fill_blank(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw108.pdf": ["Problem 1\nAnswer the following: f'(x) = ______ 。"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(hw[0]["type"], "fill_blank")              # 提到 answer 的真填空不误伤

    def test_decimal_fill_blank_answer_preserved(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw109.pdf": ["Problem 1\n概率是 ______ 。"],
                            "hw109_sol.pdf": ["Problem 1 Solution\nAnswer: 0.5"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(hw[0]["type"], "fill_blank")
        self.assertEqual(hw[0].get("answer"), "0.5")               # 金标 0.5 绝不腐蚀成 5

    def test_choice_key_outside_labels_downgrades(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw110.pdf": ["Problem 1\n二选一：\nA. 甲\nB. 乙"],
                            "hw110_sol.pdf": ["1. C"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(hw[0]["type"], "subjective")              # 键在标签外→降级不发坏 choice
        self.assertNotIn("options", hw[0])

    def test_chinese_instruction_not_folded_into_option(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw111.pdf": ["Problem 1\n选出正确项：\nA. 甲\nB. 乙\n解释你的答案"],
                            "hw111_sol.pdf": ["1. B"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(hw[0]["type"], "choice")
        self.assertEqual(hw[0]["options"][1], "B. 乙")             # 中文指令行不并入选项

    def test_bare_underline_writing_area_stays_subjective(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw112.pdf": ["Problem 1\nProve the identity.\n__________"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(hw[0]["type"], "subjective")              # 书写区不是挖空

    def test_lowercase_choice_key_accepted(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw113.pdf": ["Problem 1\n二选一：\nA. 甲\nB. 乙"],
                            "hw113_sol.pdf": ["1. b"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(hw[0]["type"], "choice")
        self.assertEqual(hw[0].get("answer"), "B")                 # 小写键归一成大写标签

    def test_fill_in_the_blank_phrase_wins(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw114.pdf": ["Problem 1\nFill in the blank: ______"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(hw[0]["type"], "fill_blank")              # 明说填空不被指示语词典抑制

    def test_inline_options_truncated_at_instruction(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        with open(os.path.join(mat, "lec01.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        be = FakeBackend({"lec01.pdf": ["Quiz 1.1 Problem\n选一个：A. foo B. bar Explain your choice.\n"
                                        "Quiz 1.1 Solution\nbar"]})
        code, payload, report = _run(mat, be)
        lec = [q for q in payload["quiz_bank"] if q["id"].startswith("lecture_quiz")]
        self.assertEqual(lec[0]["type"], "choice")
        self.assertEqual(lec[0]["options"][1], "B. bar")           # 行内末选项在指令处截尾
        self.assertEqual(lec[0].get("answer"), "B")                # 按正文 bar 归一

    def test_bare_problem_label_key_normalized(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw115.pdf": ["Problem 1\n二选一：\nA. 甲\nB. 乙"],
                            "hw115_sol.pdf": ["Question 1: B"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(hw[0]["type"], "choice")
        self.assertEqual(hw[0].get("answer"), "B")                 # 裸题号标签键可剥

    def test_eg_abbreviation_not_an_option(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw116.pdf": ["Problem 1\n四选一：\nA. 甲\nB. 乙\nC. 丙\nD. 丁\n"
                                          "E.g. circle exactly one answer"],
                            "hw116_sol.pdf": ["1. D"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(hw[0]["type"], "choice")
        self.assertEqual(len(hw[0]["options"]), 4)                 # E.g. 缩写不是第五个选项

    def test_inline_option_separators_trimmed(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        with open(os.path.join(mat, "lec01.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        be = FakeBackend({"lec01.pdf": ["Quiz 1.1 Problem\n选一个：A. foo, B. bar\n"
                                        "Quiz 1.1 Solution\nfoo"]})
        code, payload, report = _run(mat, be)
        lec = [q for q in payload["quiz_bank"] if q["id"].startswith("lecture_quiz")]
        self.assertEqual(lec[0]["type"], "choice")
        self.assertEqual(lec[0]["options"][0], "A. foo")           # 尾随逗号剥掉
        self.assertEqual(lec[0].get("answer"), "A")                # 正文比对不被分隔符卡住

    def test_note_after_options_not_folded(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw117.pdf": ["Problem 1\n下列哪个正确？\nA. 甲\nB. 丁\n"
                                          "E.g. circle exactly one answer"],
                            "hw117_sol.pdf": ["1. 丁"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(hw[0]["type"], "choice")
        self.assertEqual(hw[0]["options"][1], "B. 丁")             # E.g. 注记不卷进末选项
        self.assertEqual(hw[0].get("answer"), "B")

    def test_uppercase_subparts_without_cue_not_choice(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw118.pdf": ["Problem 1\nA. Find the derivative of f.\n"
                                          "B. Compute the integral of f."]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(hw[0]["type"], "subjective")              # 无选择线索的大写小问不猜
        self.assertNotIn("options", hw[0])

    def test_scored_writing_area_stays_subjective(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw119.pdf": ["Problem 1\nProve the identity.\n__________ (5 pts)"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(hw[0]["type"], "subjective")              # 得分标注不算挖空内容

    def test_answer_the_following_subparts_not_choice(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw120.pdf": ["Problem 1\nAnswer the following questions.\n"
                                          "A. Find the derivative.\nB. Compute the integral."]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(hw[0]["type"], "subjective")              # following 不再当选择线索

    def test_cueless_mcq_promoted_by_letter_key(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw121.pdf": ["Problem 1\nWhat is 2+2?\nA. 3\nB. 4"],
                            "hw121_sol.pdf": ["1. B"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(hw[0]["type"], "choice")                  # 裸字母键触发晋升
        self.assertEqual(len(hw[0]["options"]), 2)
        self.assertEqual(hw[0].get("answer"), "B")

    def test_choice_key_with_rationale_kept(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw122.pdf": ["Problem 1\n下列哪个正确？\nA. 甲\nB. 乙"],
                            "hw122_sol.pdf": ["1. B. Because it is even."]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(hw[0]["type"], "choice")                  # 带理由的键不降级
        self.assertEqual(hw[0].get("answer"), "B")

    def test_cn_delimiter_decimal_key_stripped(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw123.pdf": ["Problem 1\n概率是 ______ 。"],
                            "hw123_sol.pdf": ["Problem 1 Solution\n1、0.5"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(hw[0]["type"], "fill_blank")
        self.assertEqual(hw[0].get("answer"), "0.5")               # 顿号键剥掉、小数保全

    def test_score_suffixed_writing_area_stays_subjective(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw124.pdf": ["Problem 1\nProve the identity.\n__________ / 5 points"],
                            "hw125.pdf": ["Problem 1\n证明恒等式。\n__________ / 10分"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(len(hw), 2)
        for q in hw:
            self.assertEqual(q["type"], "subjective")              # 计分书写行不是挖空

    # ---- D5: wiki 配图 + 图题召回补强 regression guards ----

    def test_visual_cue_vocabulary_bidirectional(self):
        for good in ("如下图所示，求阴影面积。", "见下图的电路。", "as shown in Figure 3, compute x.",
                     "下表所示的数据分布。"):
            self.assertTrue(B.requires_assets_heuristic(good, renderable=False), good)
        for pdf_only in ("见上页图，求电流方向。", "见下页图。", "See next page for the formula.",
                         "The matrix below is singular."):
            self.assertTrue(B.requires_assets_heuristic(pdf_only, renderable=True), pdf_only)
            self.assertFalse(B.requires_assets_heuristic(pdf_only, renderable=False), pdf_only)
        for bad in ("求下列区域的定义域。", "draw a histogram of the data.",
                    "用图示法说明（自行作图）。"):
            self.assertFalse(B.requires_assets_heuristic(bad, renderable=False), bad)

    def test_lecture_footer_does_not_defeat_marker_only(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        with open(os.path.join(mat, "lec01.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        be = FakeBackend({"lec01.pdf": ["Quiz 1.1 Problem\nPage 12 of 20"]})
        code, payload, report = _run(mat, be)
        lec = [q for q in payload["quiz_bank"] if q["id"].startswith("lecture_quiz")]
        self.assertTrue(lec)
        self.assertEqual(lec[0]["question_text_status"], "page_reference")   # 页脚不算正文

    def test_hw_footer_does_not_defeat_marker_only(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"hw126.pdf": ["Problem 1\nSlide 3"]})
        code, payload, report = _run(mat, be)
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertEqual(hw[0]["question_text_status"], "page_reference")    # 同规

    def test_adjacent_page_rendered_for_next_page_figure(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"ch01/hw127.pdf": ["Problem 1\n见下页图，求最短路径。", "图在此页。"]})
        asset_root = os.path.join(tmp, "ws", "references", "assets")
        code, payload, report = _run(mat, be, ["--asset-root", asset_root])
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        paths = [a.get("path", "") for a in hw[0].get("assets", [])]
        self.assertFalse(any("p002" in pth for pth in paths), paths)
        self.assertTrue(any(
            row.get("kind") == "item_asset_crop_not_materialized"
            and row.get("pages") == [2]
            for row in report["ai_review"]
        ))  # markerless adjacent pages require an explicit target crop

    def test_builder_retires_caption_only_wiki_gallery(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        with open(os.path.join(mat, "lec01.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        be = FakeBackend({"lec01.pdf": ["Figure 1: 排序流程示例\n本章正文知识点。"]})
        asset_root = os.path.join(tmp, "ws", "references", "assets")
        code, payload, report = _run(mat, be, ["--asset-root", asset_root])
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("本章图示页", wiki_all)
        self.assertNotIn("../assets/", wiki_all)
        self.assertFalse(any(n.endswith("_fig.png") for n in os.listdir(asset_root)))

    def test_builder_leaves_visual_coverage_to_visual_index(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        with open(os.path.join(mat, "lec01.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        be = FakeBackend({"lec01.pdf": ["Figure 1: 排序流程示例\n本章正文知识点。"]})
        code, payload, report = _run(mat, be)                    # 无 asset-root
        self.assertFalse(any(w.startswith("wiki_figures_") for w in report["warnings"]))
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertIn("本章正文知识点", wiki_all)                 # 纯文字 wiki 照常完整

    def test_adjacent_answer_page_never_becomes_prompt_asset(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"ch01/hw128.pdf": ["Problem 1\n见下页图，求最短路径。",
                                          "Answer 1: 官方答案在此页。"]})
        asset_root = os.path.join(tmp, "ws", "references", "assets")
        code, payload, report = _run(mat, be, ["--asset-root", asset_root])
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        q_ctx = [a for a in hw[0].get("assets", []) if a.get("role") == "question_context"]
        self.assertFalse(any("p002" in a.get("path", "") for a in q_ctx))   # 答案页绝不进题面资产

    def test_residue_figure_page_still_renderable_as_adjacent(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"ch01/hw129.pdf": ["Problem 1\n见下页图，求电路电流。", "37"]})
        asset_root = os.path.join(tmp, "ws", "references", "assets")
        code, payload, report = _run(mat, be, ["--asset-root", asset_root])
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        paths = [a.get("path", "") for a in hw[0].get("assets", [])]
        self.assertFalse(any("p002" in pth for pth in paths), paths)
        self.assertTrue(any(
            row.get("kind") == "item_asset_crop_not_materialized"
            and row.get("pages") == [2]
            for row in report["ai_review"]
        ))  # numeric residue is not proof of target-only visual content

    def test_lecture_needs_item_uses_original_prompt_for_adjacent(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        with open(os.path.join(mat, "lec01.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        be = FakeBackend({"lec01.pdf": ["Quiz 1.1 Problem\n如图，见下页图作答。", "图在此页。"]})
        asset_root = os.path.join(tmp, "ws", "references", "assets")
        code, payload, report = _run(mat, be, ["--asset-root", asset_root])
        lec = [q for q in payload["quiz_bank"] if q["id"].startswith("lecture_quiz")]
        paths = [a.get("path", "") for a in lec[0].get("assets", [])]
        self.assertFalse(any("p002" in pth for pth in paths), paths)
        self.assertIs(lec[0].get("requires_assets"), True)
        # The cue remains a fail-closed visual dependency, but an unscoped
        # adjacent page is not published as target evidence.
        self.assertNotIn("_prompt_text", lec[0])                      # 内部字段不出库

    def test_collapsed_multi_footer_still_marker_only(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        with open(os.path.join(mat, "lec01.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        be = FakeBackend({"lec01.pdf": ["Quiz 1.1 Problem\nPage 12 of 20\nSlide 3"]})
        code, payload, report = _run(mat, be)
        lec = [q for q in payload["quiz_bank"] if q["id"].startswith("lecture_quiz")]
        self.assertEqual(lec[0]["question_text_status"], "page_reference")   # 折叠多页脚不击穿

    def test_wiki_caption_must_anchor_line_start(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        with open(os.path.join(mat, "lec01.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        be = FakeBackend({"lec01.pdf": ["正文顺带提到表 1: 的数据，但本页没有图。\n更多正文。"]})
        asset_root = os.path.join(tmp, "ws", "references", "assets")
        code, payload, report = _run(mat, be, ["--asset-root", asset_root])
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("本章图示页", wiki_all)                      # 行中提及不算图表标题

    def test_caption_gallery_assets_are_not_emitted_for_multiple_sources(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(os.path.join(mat, "a"), exist_ok=True)
        for rel in ("a/b.pdf", "a_b.pdf"):
            with open(os.path.join(mat, *rel.split("/")), "wb") as f:
                f.write(b"%PDF-fake")

        class TwoBackend(FakeBackend):
            def page_texts(self, pdf_path):
                return ["Figure 1: 示例图\n正文。"]
        asset_root = os.path.join(tmp, "ws", "references", "assets")
        code, payload, report = _run(mat, TwoBackend({}), ["--asset-root", asset_root])
        figs = [n for n in os.listdir(asset_root) if n.endswith("_fig.png")]
        self.assertEqual(figs, [])

    def test_cn_adjacent_answer_page_never_prompt_asset(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"ch01/hw130.pdf": ["Problem 1\n见下页图，求值。", "答案：42。"]})
        asset_root = os.path.join(tmp, "ws", "references", "assets")
        code, payload, report = _run(mat, be, ["--asset-root", asset_root])
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        self.assertIn("42", hw[0].get("answer", ""))
        q_ctx = [a for a in hw[0].get("assets", []) if a.get("role") == "question_context"]
        self.assertFalse(any("p002" in a.get("path", "") for a in q_ctx))   # 中文答案页双保险排除

    def test_continued_page_cross_page_cue_detected(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        with open(os.path.join(mat, "lec01.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        be = FakeBackend({"lec01.pdf": ["Quiz 1.1 Problem\n题干开头。",
                                        "Quiz 1.1 Problem (Continued)\n见下页图作答。",
                                        "图在此页。"]})
        asset_root = os.path.join(tmp, "ws", "references", "assets")
        code, payload, report = _run(mat, be, ["--asset-root", asset_root])
        lec = [q for q in payload["quiz_bank"] if q["id"].startswith("lecture_quiz")]
        paths = [a.get("path", "") for a in lec[0].get("assets", [])]
        self.assertFalse(any("p003" in pth for pth in paths), paths)
        self.assertIs(lec[0].get("requires_assets"), True)
        # Cross-page dependence remains fail-closed; markerless page 3 is not
        # silently promoted to a target-only asset.

    def test_cn_caption_without_punct_remains_text_without_gallery(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        with open(os.path.join(mat, "lec01.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        be = FakeBackend({"lec01.pdf": ["图 1 排序流程示例\n本章正文知识点。"]})
        asset_root = os.path.join(tmp, "ws", "references", "assets")
        code, payload, report = _run(mat, be, ["--asset-root", asset_root])
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("本章图示页", wiki_all)
        self.assertFalse(any(name.endswith("_fig.png") for name in os.listdir(asset_root)))

    def test_txt_and_pdf_captions_do_not_create_legacy_gallery(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        with open(os.path.join(mat, "notes.txt"), "wb") as f:
            f.write("Figure 1: 文本示意，不可渲染。\n正文。".encode("utf-8"))
        with open(os.path.join(mat, "lec01.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        be = FakeBackend({"lec01.pdf": ["Figure 1: 真图页\n正文知识点。"]})
        asset_root = os.path.join(tmp, "ws", "references", "assets")
        code, payload, report = _run(mat, be, ["--asset-root", asset_root])
        figs = [n for n in os.listdir(asset_root) if n.endswith("_fig.png")]
        self.assertEqual(figs, [])

    def test_adjacent_render_anchored_to_cue_page(self):
        tmp = tempfile.mkdtemp()
        mat, be = _mk(tmp, {"ch01/hw131.pdf": ["Problem 1\n见下页图，求值。", "题面续页，无线索。",
                                          "Problem 2\n下一题的题面。"]})
        asset_root = os.path.join(tmp, "ws", "references", "assets")
        code, payload, report = _run(mat, be, ["--asset-root", asset_root])
        hw = [q for q in payload["quiz_bank"] if q.get("source_type") == "homework"]
        q1 = next(q for q in hw if q["homework_number"] == 1)
        paths = [a.get("path", "") for a in q1.get("assets", [])]
        self.assertFalse(any("p003" in pth for pth in paths), paths)   # 无线索的续页不 +1 卷走下一题

    def test_compact_cn_caption_remains_text_without_gallery(self):
        tmp = tempfile.mkdtemp()
        mat = os.path.join(tmp, "mat")
        os.makedirs(mat, exist_ok=True)
        with open(os.path.join(mat, "lec01.pdf"), "wb") as f:
            f.write(b"%PDF-fake")
        be = FakeBackend({"lec01.pdf": ["图1排序流程示例\n本章正文知识点。"]})
        asset_root = os.path.join(tmp, "ws", "references", "assets")
        code, payload, report = _run(mat, be, ["--asset-root", asset_root])
        wiki_all = " ".join(ph.get("wiki_content", "") for ph in payload.get("phases", []))
        self.assertNotIn("本章图示页", wiki_all)
        self.assertFalse(any(name.endswith("_fig.png") for name in os.listdir(asset_root)))

    def test_roster_pdf_uses_prompt_only_crops_and_delays_answers(self):
        fixture_path = os.path.join(
            ROOT, "tests", "fixtures", "homework_roster_layout_gold.json"
        )
        with open(fixture_path, encoding="utf-8") as stream:
            fixture = json.load(stream)
        # Exercise the real mixed case: the submitted PDF contributes prompt + student-work
        # crops and misleading OCR answer text, while a separate official solution page
        # contributes the only material answer/context.
        fixture["texts"]["hw_scan.pdf"][1] = (
            "Answer 1.1.2: SUBMISSION OCR says the wrong value"
        )
        fixture["texts"]["hw_scan.pdf"][3] = (
            "Answer 1.2.1: SUBMISSION OCR also disagrees"
        )
        fixture["texts"]["hw_scan_sol.pdf"] = [
            "Problem 1.1.2\nAnswer 1.1.2: official one; see the graph below\n\n"
            "Problem 1.2.1\nAnswer 1.2.1: official two; see the graph below"
        ]
        tmp = tempfile.mkdtemp()
        mat, _unused = _mk(tmp, fixture["texts"])
        backend = LayoutBackend(fixture)
        asset_root = os.path.join(tmp, "ws", "references", "assets")

        code, payload, report = _run(mat, backend, ["--asset-root", asset_root])

        self.assertEqual(code, 0, report)
        homework = {
            str(item["homework_number"]): item for item in payload["quiz_bank"]
            if item.get("source_type") == "homework"
        }
        self.assertEqual(set(homework), set(fixture["expected"]))
        for number, expected in fixture["expected"].items():
            item = homework[number]
            self.assertEqual(item["chapter"], expected["chapter"])
            self.assertEqual(item["source_pages"], expected["source_pages"])
            self.assertIn("official", item["answer"].lower())
            self.assertNotIn("submission ocr", item["answer"].lower())
            self.assertTrue(item["answer_source_file"].endswith("hw_scan_sol.pdf"))
            self.assertNotIn(1, item["source_pages"])  # roster is not a prompt page
            self.assertNotIn("student answer", item["question"])
            question_assets = [
                asset for asset in item["assets"]
                if asset["role"] == "question_context"
            ]
            answer_assets = [
                asset for asset in item["assets"]
                if asset["role"] == "answer_context"
            ]
            attempt_assets = [
                asset for asset in item["assets"]
                if asset["role"] == "student_attempt"
            ]
            self.assertEqual(len(question_assets), 1)
            self.assertTrue(answer_assets)
            self.assertTrue(attempt_assets)
            self.assertEqual(question_assets[0]["type"], "crop_image")
            self.assertTrue(all(asset["type"] == "crop_image" for asset in attempt_assets))
            self.assertTrue(all(asset["type"] == "crop_image" for asset in answer_assets))
            self.assertTrue(all(
                asset["source_file"] == item["source_file"]
                for asset in question_assets
            ))
            self.assertTrue(all(
                asset["source_file"] == item["source_file"]
                and re.fullmatch(r"[0-9a-f]{64}", asset["source_sha256"])
                for asset in attempt_assets
            ))
            self.assertTrue(all(
                asset["source_file"] == item["answer_source_file"]
                and re.fullmatch(r"[0-9a-f]{64}", asset["source_sha256"])
                for asset in answer_assets
            ))
            self.assertIn("source_bbox_pdf_points", question_assets[0])
            self.assertFalse(
                set(asset["path"] for asset in question_assets)
                & set(asset["path"] for asset in attempt_assets)
            )
            if number == "1.1.2":
                # The continuation page is useful answer evidence, but it must
                # never expand the question provenance beyond the prompt crop.
                self.assertTrue(any("p.3" in asset["caption"] for asset in attempt_assets))
                self.assertNotIn(3, item["source_pages"])
            question_unit = next(
                row for row in payload["ingestion"]["content_units"]
                if row["kind"] == "question" and row.get("external_id") == item["id"]
            )
            persisted_attempts = [
                asset for asset in question_unit["metadata"].get("assets", [])
                if asset["role"] == "student_attempt"
            ]
            persisted_official = [
                asset for asset in question_unit["metadata"].get("assets", [])
                if asset["role"] == "answer_context"
            ]
            self.assertEqual(len(attempt_assets), len(persisted_attempts))
            self.assertEqual(len(answer_assets), len(persisted_official))
            self.assertTrue(all(
                asset["source_file"] == item["source_file"]
                and re.fullmatch(r"[0-9a-f]{64}", asset["source_sha256"])
                for asset in persisted_attempts
            ))
            answer_unit = next(
                row for row in payload["ingestion"]["content_units"]
                if row["kind"] == "answer" and row.get("external_id") == item["id"]
            )
            self.assertEqual("answer_context", answer_unit.get("asset_role"))
            self.assertIn(answer_unit.get("asset_path"), {
                asset["path"] for asset in answer_assets
            })
            self.assertEqual(len(attempt_assets), len([
                asset for asset in answer_unit["metadata"].get("assets", [])
                if asset["role"] == "student_attempt"
            ]))
            self.assertEqual(len(answer_assets), len([
                asset for asset in answer_unit["metadata"].get("assets", [])
                if asset["role"] == "answer_context"
            ]))
        self.assertEqual(report["homework_roster_files"], 1)
        self.assertEqual(report["homework_prompt_crops"], 2)
        self.assertEqual(report["homework_answer_crops"], 4)
        self.assertEqual(len(backend.clip_calls), 8)
        self.assertEqual([], backend.page_calls)
        mapping_reviews = [
            entry for entry in report["ai_review"]
            if entry.get("kind") == "homework_roster_visual_mapping_unverified"
        ]
        self.assertEqual(len(mapping_reviews), 1)
        self.assertEqual(mapping_reviews[0]["pages"], [1, 2, 4])
        self.assertEqual(
            set(mapping_reviews[0]["external_ids"]),
            {"hw_homework_hw_scan_1_1_2", "hw_homework_hw_scan_1_2_1"},
        )
        self.assertIn("Visually compare every printed prompt crop", mapping_reviews[0]["action"])

    def test_roster_submission_ocr_answer_is_not_material_without_paired_solution(self):
        fixture_path = os.path.join(
            ROOT, "tests", "fixtures", "homework_roster_layout_gold.json"
        )
        with open(fixture_path, encoding="utf-8") as stream:
            fixture = json.load(stream)
        fixture["texts"].pop("hw_scan_sol.pdf")
        fixture["texts"]["hw_scan.pdf"][1] = (
            "Answer 1.1.2: SUBMISSION OCR must not become official"
        )
        fixture["texts"]["hw_scan.pdf"][3] = (
            "Answer 1.2.1: SUBMISSION OCR must remain student work"
        )
        tmp = tempfile.mkdtemp()
        mat, _unused = _mk(tmp, fixture["texts"])
        backend = LayoutBackend(fixture)
        asset_root = os.path.join(tmp, "ws", "references", "assets")

        code, payload, report = _run(mat, backend, ["--asset-root", asset_root])

        self.assertEqual(0, code, report)
        homework = [
            item for item in payload["quiz_bank"]
            if item.get("source_type") == "homework"
        ]
        self.assertEqual(2, len(homework))
        units = payload["ingestion"]["content_units"]
        reviews = payload["ingestion"]["review_candidates"]
        for item in homework:
            self.assertNotIn("answer", item)
            self.assertEqual("unknown", item["answer_status"])
            self.assertTrue(any(
                asset["role"] == "student_attempt" for asset in item.get("assets", [])
            ))
            question = next(
                row for row in units
                if row["kind"] == "question" and row.get("external_id") == item["id"]
            )
            self.assertFalse(any(
                row["kind"] == "answer" and row.get("external_id") == item["id"]
                for row in units
            ))
            self.assertTrue(any(
                "missing_answer" in (review.get("reason_codes") or [])
                and question["unit_id"] in (review.get("target_unit_ids") or [])
                for review in reviews
            ))

    def test_hw7_roster_expanded_fallback_recovers_all_12_prompt_crops(self):
        fixture = _hw7_roster_fixture()
        tmp = tempfile.mkdtemp()
        mat, _unused = _mk(tmp, fixture["texts"])
        backend = LayoutBackend(fixture)
        asset_root = os.path.join(tmp, "ws", "references", "assets")

        code, payload, report = _run(mat, backend, ["--asset-root", asset_root])

        self.assertEqual(0, code, report)
        homework = {
            str(item["homework_number"]): item for item in payload["quiz_bank"]
            if item.get("source_type") == "homework"
        }
        self.assertEqual(set(fixture["expected"]), set(homework))
        self.assertEqual(12, report["homework_prompt_crops"])
        self.assertEqual(6, sum(
            B._prompt_image_cluster(layout) is not None
            for layout in fixture["layouts"]["hw7.pdf"][1:]
        ))
        for number, expected in fixture["expected"].items():
            item = homework[number]
            self.assertEqual([expected["page"]], item["source_pages"])
            # Geometry recovery never upgrades a roster-only label to full text.
            self.assertEqual("page_reference", item["question_text_status"])
            question_assets = [
                asset for asset in item["assets"]
                if asset.get("role") == "question_context"
            ]
            self.assertEqual(1, len(question_assets))
            self.assertEqual(
                expected["bbox"], question_assets[0]["source_bbox_pdf_points"]
            )
        lower_prompt_pages = {8, 9, 10, 11, 12, 18}
        self.assertTrue(lower_prompt_pages <= {
            item["source_pages"][0] for item in homework.values()
        })
        self.assertEqual([], backend.page_calls)

        mapping = [
            row for row in report["ai_review"]
            if row.get("kind") == "homework_roster_visual_mapping_unverified"
        ]
        self.assertEqual(1, len(mapping))
        self.assertEqual("expanded_roster_fallback", mapping[0]["geometry_route"])
        self.assertEqual(
            set(item["id"] for item in homework.values()),
            set(mapping[0]["external_ids"]),
        )
        self.assertIn("referenced prerequisite", mapping[0]["action"])
        self.assertIn("missing table", mapping[0]["action"])
        self.assertIn("missing subquestion", mapping[0]["action"])
        self.assertIn("never made complete merely by passing geometry", mapping[0]["action"])
        # The known p9/p14 semantic boundaries stay unresolved and page-referenced.
        for number in ("5.6.2", "5.8.2"):
            self.assertEqual("page_reference", homework[number]["question_text_status"])
            self.assertIn(homework[number]["id"], mapping[0]["external_ids"])

    def test_hw7_roster_extra_answer_raster_breaks_exact_cardinality(self):
        fixture = _hw7_roster_fixture()
        page_13 = fixture["layouts"]["hw7.pdf"][12]
        page_13["text_boxes"] = []
        page_13["images"].append({
            "image_id": "unique-answer-raster",
            "bbox": [80.0, 300.0, 532.0, 650.0],
        })
        tmp = tempfile.mkdtemp()
        mat, _unused = _mk(tmp, fixture["texts"])
        backend = LayoutBackend(fixture)

        code, payload, report = _run(mat, backend)

        self.assertEqual(0, code, report)
        self.assertFalse([
            item for item in payload["quiz_bank"]
            if item.get("source_type") == "homework"
        ])
        review = next(
            row for row in report["ai_review"]
            if row.get("kind") == "homework_prompt_crop_unsafe_leakage"
        )
        self.assertIn("roster has 12 problems", review["action"])
        self.assertIn("expanded roster fallback proves 13", review["action"])
        self.assertEqual([], backend.clip_calls)
        self.assertEqual([], backend.page_calls)

    def test_hw7_roster_expanded_fallback_allows_only_one_candidate_per_page(self):
        fixture = _hw7_roster_fixture()
        page_13 = fixture["layouts"]["hw7.pdf"][12]
        page_13["text_boxes"] = []
        page_13["images"].extend([
            {"image_id": "ambiguous-a", "bbox": [60.0, 40.0, 550.0, 250.0]},
            {"image_id": "ambiguous-b", "bbox": [60.0, 400.0, 550.0, 610.0]},
        ])
        tmp = tempfile.mkdtemp()
        mat, _unused = _mk(tmp, fixture["texts"])
        backend = LayoutBackend(fixture)

        code, payload, report = _run(mat, backend)

        self.assertEqual(0, code, report)
        self.assertFalse([
            item for item in payload["quiz_bank"]
            if item.get("source_type") == "homework"
        ])
        review = next(
            row for row in report["ai_review"]
            if row.get("kind") == "homework_prompt_crop_unsafe_leakage"
        )
        self.assertIn("at most one prompt image candidate per page", review["action"])
        self.assertIn("page 13=2", review["action"])
        self.assertEqual([], backend.clip_calls)
        self.assertEqual([], backend.page_calls)

    def test_hw7_roster_expanded_fallback_rejects_interior_native_text(self):
        fixture = _hw7_roster_fixture()
        fixture["layouts"]["hw7.pdf"][17]["text_boxes"].append(
            [100.0, 350.0, 500.0, 500.0]
        )
        tmp = tempfile.mkdtemp()
        mat, _unused = _mk(tmp, fixture["texts"])
        backend = LayoutBackend(fixture)

        code, payload, report = _run(mat, backend)

        self.assertEqual(0, code, report)
        self.assertFalse([
            item for item in payload["quiz_bank"]
            if item.get("source_type") == "homework"
        ])
        review = next(
            row for row in report["ai_review"]
            if row.get("kind") == "homework_prompt_crop_unsafe_leakage"
        )
        self.assertIn("expanded roster fallback proves 11", review["action"])
        self.assertIn("8-point inset native-text gate", review["action"])
        self.assertEqual([], backend.clip_calls)
        self.assertEqual([], backend.page_calls)

    def test_roster_ocr_problem_markers_do_not_leak_unlabelled_handwriting(self):
        fixture_path = os.path.join(
            ROOT, "tests", "fixtures", "homework_roster_layout_gold.json"
        )
        with open(fixture_path, encoding="utf-8") as stream:
            fixture = json.load(stream)
        # The OCR layer contains the right problem numbers, printed prompt text,
        # and an unlabeled handwritten response.  Number equality alone used to
        # select the ordinary text slicer and publish the response as the prompt.
        fixture["texts"]["hw_scan.pdf"] = [
            "Homework 1\n1. Problem 1.1.2\n2. Problem 1.2.1\n",
            "Problem 1.1.2\nPrinted prompt one.\nstudent handwriting: x = 42",
            "student handwriting continues with the final answer",
            "Problem 1.2.1\nPrinted prompt two.\nstudent handwriting: choose B",
        ]
        tmp = tempfile.mkdtemp()
        mat, _unused = _mk(tmp, fixture["texts"])
        backend = LayoutBackend(fixture)
        asset_root = os.path.join(tmp, "ws", "references", "assets")

        code, payload, report = _run(mat, backend, ["--asset-root", asset_root])

        self.assertEqual(code, 0, report)
        homework = [
            item for item in payload["quiz_bank"]
            if item.get("source_type") == "homework"
        ]
        self.assertEqual(2, len(homework))
        self.assertEqual({"1.1.2", "1.2.1"}, {
            str(item["homework_number"]) for item in homework
        })
        for item in homework:
            self.assertNotIn("student handwriting", item["question"])
            self.assertEqual(
                fixture["expected"][str(item["homework_number"])]["source_pages"],
                item["source_pages"],
            )
            question_assets = [
                asset for asset in item["assets"]
                if asset.get("role") == "question_context"
            ]
            self.assertEqual(1, len(question_assets))
            self.assertEqual("crop_image", question_assets[0]["type"])
        self.assertEqual([], backend.page_calls)
        mapping = [
            row for row in report["ai_review"]
            if row.get("kind") == "homework_roster_visual_mapping_unverified"
        ]
        self.assertEqual(1, len(mapping))
        self.assertEqual(
            {item["id"] for item in homework}, set(mapping[0]["external_ids"])
        )

    def test_roster_ocr_full_page_scan_without_safe_crops_blocks_for_review(self):
        fixture_path = os.path.join(
            ROOT, "tests", "fixtures", "homework_roster_layout_gold.json"
        )
        with open(fixture_path, encoding="utf-8") as stream:
            fixture = json.load(stream)
        fixture["texts"]["hw_scan.pdf"] = [
            "Homework 1\n1. Problem 1.1.2\n2. Problem 1.2.1\n",
            "Problem 1.1.2\nPrinted prompt one.\nhandwritten result 42",
            "handwritten continuation",
            "Problem 1.2.1\nPrinted prompt two.\nhandwritten result B",
        ]
        for layout in fixture["layouts"]["hw_scan.pdf"][1:]:
            layout["images"] = [{
                "image_id": "whole-page-scan",
                "bbox": list(layout["page_bbox"]),
            }]
        tmp = tempfile.mkdtemp()
        mat, _unused = _mk(tmp, fixture["texts"])
        backend = LayoutBackend(fixture)

        code, payload, report = _run(mat, backend)

        self.assertEqual(0, code, report)
        self.assertFalse([
            item for item in payload["quiz_bank"]
            if item.get("source_type") == "homework"
        ])
        reviews = [
            row for row in report["ai_review"]
            if row.get("kind") == "homework_prompt_crop_unsafe_leakage"
        ]
        self.assertEqual(1, len(reviews))
        self.assertEqual([1, 2, 3, 4], reviews[0]["pages"])
        self.assertIn("prompt-only image anchors", reviews[0]["action"])
        self.assertIn("whole answer-bearing page", reviews[0]["action"])
        self.assertEqual([], backend.clip_calls)
        self.assertEqual([], backend.page_calls)

    def test_roster_matching_text_without_layout_capability_fails_closed(self):
        fixture_path = os.path.join(
            ROOT, "tests", "fixtures", "homework_roster_layout_gold.json"
        )
        with open(fixture_path, encoding="utf-8") as stream:
            fixture = json.load(stream)
        fixture["texts"]["hw_scan.pdf"] = [
            "Homework 1\n1. Problem 1.1.2\n2. Problem 1.2.1\n",
            "Problem 1.1.2\nPrinted prompt or OCR handwriting.",
            "continuation without an answer label",
            "Problem 1.2.1\nPrinted prompt or OCR handwriting.",
        ]
        tmp = tempfile.mkdtemp()
        mat, backend = _mk(tmp, fixture["texts"])
        backend.page_layout = None
        backend.render_page_clip_png = None

        code, payload, report = _run(mat, backend)

        self.assertEqual(0, code, report)
        self.assertFalse([
            item for item in payload["quiz_bank"]
            if item.get("source_type") == "homework"
        ])
        reviews = [
            row for row in report["ai_review"]
            if row.get("kind") == "homework_prompt_crop_unsafe_leakage"
        ]
        self.assertEqual(1, len(reviews))
        self.assertEqual([1, 2, 3, 4], reviews[0]["pages"])
        self.assertIn("layout/crop capability is unavailable", reviews[0]["action"])

    def test_roster_crop_count_mismatch_enters_typed_review(self):
        fixture_path = os.path.join(
            ROOT, "tests", "fixtures", "homework_roster_layout_gold.json"
        )
        with open(fixture_path, encoding="utf-8") as stream:
            fixture = json.load(stream)
        # Remove the second independently separable prompt.  The builder must
        # emit no pseudo-question and must name the exact review condition.
        fixture["layouts"]["hw_scan.pdf"][3]["images"] = [
            image for image in fixture["layouts"]["hw_scan.pdf"][3]["images"]
            if image["image_id"] != "prompt-2"
        ]
        tmp = tempfile.mkdtemp()
        mat, _unused = _mk(tmp, fixture["texts"])
        backend = LayoutBackend(fixture)

        code, payload, report = _run(mat, backend)

        self.assertEqual(code, 0, report)
        self.assertFalse([
            item for item in payload["quiz_bank"]
            if item.get("source_type") == "homework"
        ])
        reviews = [
            entry for entry in report["ai_review"]
            if entry.get("kind") == "homework_prompt_crop_unsafe_leakage"
        ]
        self.assertEqual(len(reviews), 1)
        self.assertIn("roster has 2 problems", reviews[0]["action"])
        self.assertIn("whole answer-bearing page", reviews[0]["action"])

    def test_roster_crop_render_failure_never_falls_back_to_whole_page(self):
        fixture_path = os.path.join(
            ROOT, "tests", "fixtures", "homework_roster_layout_gold.json"
        )
        with open(fixture_path, encoding="utf-8") as stream:
            fixture = json.load(stream)

        class BrokenCropBackend(LayoutBackend):
            def render_page_clip_png(self, pdf_path, page_index, bbox):
                self.clip_calls.append(
                    (os.path.basename(pdf_path), page_index + 1, list(bbox))
                )
                return None

        tmp = tempfile.mkdtemp()
        mat, _unused = _mk(tmp, fixture["texts"])
        backend = BrokenCropBackend(fixture)
        asset_root = os.path.join(tmp, "ws", "references", "assets")

        code, payload, report = _run(
            mat, backend,
            ["--asset-root", asset_root, "--render-pages", "required"],
        )

        self.assertEqual(code, 3)
        self.assertIn("render-pages=required", payload["error"])
        self.assertTrue(backend.clip_calls)
        self.assertEqual(backend.page_calls, [])
        self.assertTrue(any(
            warning.startswith("likely_asset_required_but_no_image")
            for warning in report["warnings"]
        ))

    def test_dotted_homework_number_infers_chapter(self):
        tmp = tempfile.mkdtemp()
        mat, backend = _mk(tmp, {
            "hw132.pdf": ["Problem 3.2.1\nCompute the requested value and show all work."]
        })

        code, payload, report = _run(mat, backend)

        self.assertEqual(code, 0, report)
        item = next(
            row for row in payload["quiz_bank"] if row.get("source_type") == "homework"
        )
        self.assertEqual(item["chapter"], 3)

    def test_no_network_or_llm(self):

















        src = open(os.path.join(ROOT, "scripts", "build_raw_input_from_workspace.py"), encoding="utf-8").read()
        for banned in ("import requests", "urllib.request", "import anthropic", "import socket"):
            self.assertNotIn(banned, src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
