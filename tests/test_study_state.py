# -*- coding: utf-8 -*-
"""A4 tests — structured progress state: migration, mutations, generated md, fail-loud IO,
validator schema, T4 JSON snapshots, entry-point contract."""
import json
import hashlib
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
import i18n
import notebook as notebook_engine
import study_guide_qa as artifact_qa
from ingestion import workspace_publication_lock


def _valid_png(width, height):
    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))

    row = b"\x00" + (b"\x00" * ((width + 7) // 8))
    return (
        artifact_qa.PNG_SIGNATURE
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 1, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )

LEGACY_MD = ("# 🎯 复习进度\n\n## ⏱️ 当前复习断点\n* **当前进行阶段**：阶段 3：树\n\n"
             "## ❌ 错题档案记录\n| 错题ID | 关联章节 | 题目内容简述 | 错误原因分析 | 状态 |\n"
             "| :--- | :--- | :--- | :--- | :--- |\n| [#q1] | 第1章 | 栈顺序 | 混淆LIFO | 未复习 |\n\n"
             "## 💡 概念疑难点记录\n- 循环队列取模没搞懂\n")


def _mk_ws(tmp, md=LEGACY_MD):
    ws = os.path.join(tmp, "ws")
    os.makedirs(ws)
    if md is not None:
        with open(os.path.join(ws, "study_progress.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(md)
    return ws


def _up(ws, args):
    env = None
    home_marker = os.path.join(ws, ".phase-test-examprep-home")
    if os.path.isfile(home_marker):
        with open(home_marker, "r", encoding="utf-8") as stream:
            fixture_home = stream.read().strip()
        env = dict(os.environ)
        env["EXAMPREP_HOME"] = fixture_home
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, "update_progress.py"),
                           "--workspace", ws] + args, capture_output=True, text=True,
                          encoding="utf-8", env=env)


def _state(ws):
    return json.load(open(os.path.join(ws, "study_state.json"), encoding="utf-8"))


def _refresh_visual_integrity(ws):
    rels = ["references/quiz_bank.json", "references/teaching_examples.json"]
    if os.path.lexists(os.path.join(ws, ".ingest")):
        rels.append(".ingest/content_units.jsonl")
    for optional in ("references/teaching_baseline.json", "ingest_report.json"):
        if os.path.isfile(os.path.join(ws, *optional.split("/"))):
            rels.append(optional)
    wiki_dir = os.path.join(ws, "references", "wiki")
    rels.extend("references/wiki/" + name for name in sorted(os.listdir(wiki_dir))
                if name.endswith(".md"))
    for manifest in ("references/quiz_bank.json", "references/teaching_examples.json"):
        for item in json.load(open(os.path.join(ws, *manifest.split("/")), encoding="utf-8")):
            for asset in item.get("assets") or []:
                if isinstance(asset, dict) and isinstance(asset.get("path"), str):
                    rels.append(asset["path"].replace("\\", "/"))
    inputs = {}
    for rel in rels:
        payload = open(os.path.join(ws, *rel.split("/")), "rb").read()
        inputs[rel] = {"sha256": hashlib.sha256(payload).hexdigest()}
    materials_root = os.path.join(os.path.dirname(ws), "materials")
    os.makedirs(materials_root, exist_ok=True)
    source_pdf = os.path.join(materials_root, "course.pdf")
    if not os.path.exists(source_pdf):
        with open(source_pdf, "wb") as stream:
            stream.write(b"%PDF-phase-evidence-source")
    material_digest = hashlib.sha256(open(source_pdf, "rb").read()).hexdigest()
    material_inventory = hashlib.sha256(
        json.dumps(["course.pdf"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    integrity = {
        "schema_version": 2,
        "generated_at": "2026-07-13T00:00:00Z",
        "mode": {"materials_scan": True, "apply_questions": False,
                 "apply_wiki": False, "backend": "fake",
                 "materials_root": os.path.abspath(materials_root)},
        "inputs": inputs,
        "materials": {"course.pdf": {"sha256": material_digest}},
        "material_inventory_sha256": material_inventory,
    }
    manifests = {}
    for name in ("figure_page_index.json", "image_question_index.json"):
        path = os.path.join(ws, "references", name)
        value = json.load(open(path, encoding="utf-8"))
        value.pop("integrity", None)
        manifests[name] = value
    integrity["outputs"] = {}
    for name, value in manifests.items():
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
        integrity["outputs"][name] = {"sha256": hashlib.sha256(raw).hexdigest()}
    for name, value in manifests.items():
        path = os.path.join(ws, "references", name)
        value["integrity"] = integrity
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(value, stream)


def _phase_evidence_ws():
    """Minimal new-manifest workspace for the v4.1 phase-evidence gate."""
    md = ("# 复习进度\n\n## ⏱️ 当前复习断点\n* **当前进行阶段**：阶段 1\n\n"
          "## 📊 知识点打卡状态\n- [ ] **阶段 1**：基础\n")
    ws = _mk_ws(tempfile.mkdtemp(), md=md)
    fixture_home = os.path.join(os.path.dirname(ws), "examprep-home")
    with open(os.path.join(ws, ".phase-test-examprep-home"), "w",
              encoding="utf-8") as stream:
        stream.write(fixture_home)
    os.makedirs(os.path.join(ws, "references", "wiki"))
    os.makedirs(os.path.join(ws, "notebook"))
    with open(os.path.join(ws, "study_plan.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("## 阶段 1：基础 `references/wiki/ch1.md`\n"
                "## 阶段 2：进阶 `references/wiki/ch2.md`\n## 阶段 3：综合\n")
    with open(os.path.join(ws, "references", "wiki", "ch1.md"), "w", encoding="utf-8") as f:
        f.write("# 第一章\n")
    with open(os.path.join(ws, "references", "wiki", "ch2.md"), "w", encoding="utf-8") as f:
        f.write("# 第二章\n")
    with open(os.path.join(ws, "references", "figure_page_index.json"), "w", encoding="utf-8") as f:
        json.dump({"wiki_visual_coverage": {
            "detected": 1, "embedded": 1, "missing": 0,
            "deferred_answer_count": 0, "deferred_answer_pages": [],
            "manual_answer_exposure_count": 0, "manual_answer_exposure_pages": [],
            "shared_prompt_answer_count": 0, "shared_prompt_answer_pages": [],
            "shared_prompt_answer_blocker_count": 0,
            "shared_prompt_answer_blocker_pages": [],
            "per_chapter": {"ch1.md": {"detected": 1, "embedded": 1, "missing": 0}},
            "pages": [{"wiki_file": "ch1.md", "status": "embedded"}]}}, f)
    with open(os.path.join(ws, "references", "image_question_index.json"), "w", encoding="utf-8") as f:
        json.dump({"prompt_suspects": [], "answer_suspects": []}, f)
    with open(os.path.join(ws, "references", "teaching_examples.json"), "w", encoding="utf-8") as f:
        json.dump([{"id": "ex1", "chapter": 1}, {"id": "ex2", "chapter": 2}], f)
    with open(os.path.join(ws, "references", "quiz_bank.json"), "w", encoding="utf-8") as f:
        json.dump([{"id": "q1", "chapter": 1, "type": "choice", "question": "1+1?",
                    "options": ["A. 2", "B. 3"], "answer": "A", "source": "teacher"},
                   {"id": "q2", "chapter": 1, "type": "true_false", "question": "2>1?",
                    "answer": True, "source": "teacher"},
                   {"id": "q3", "chapter": 2, "type": "true_false", "question": "3>2?",
                    "answer": True, "source": "teacher"},
                    {"id": "q4", "chapter": 2, "type": "true_false", "question": "4>3?",
                     "answer": True, "source": "teacher"}], f)
    with open(os.path.join(ws, "ingest_report.json"), "w", encoding="utf-8") as f:
        json.dump({"teaching_example_ids": ["ex1", "ex2"],
                   "teaching_examples_by_chapter": {"1": 1, "2": 1},
                   "teaching_example_ids_by_chapter": {"1": ["ex1"], "2": ["ex2"]}}, f)
    with open(os.path.join(ws, "references", "teaching_baseline.json"),
              "w", encoding="utf-8") as f:
        json.dump({"schema_version": 1, "policy": "append_only",
                   "teaching_example_ids": ["ex1", "ex2"],
                   "teaching_example_ids_by_chapter": {
                       "1": ["ex1"], "2": ["ex2"]}}, f)
    with open(os.path.join(ws, "notebook", "ch01.md"), "w", encoding="utf-8") as f:
        f.write("# 第一章笔记\n\n## [#ex1] 例题一\n\n完整讲解。\n")
    with open(os.path.join(ws, "notebook", "ch02.md"), "w", encoding="utf-8") as f:
        f.write("# 第二章笔记\n\n## [#ex2] 例题二\n\n完整讲解。\n")
    _refresh_visual_integrity(ws)
    r = _up(ws, ["init"])
    if r.returncode != 0:
        raise AssertionError(r.stderr)
    r = _up(ws, ["set", "--processing-mode", "full"])
    if r.returncode != 0:
        raise AssertionError(r.stderr)
    state = _state(ws)
    env = dict(os.environ)
    env["EXAMPREP_HOME"] = fixture_home
    confirmed = subprocess.run([
        sys.executable, os.path.join(SCRIPTS, "exam_start.py"), "confirm",
        "--course", "phase-fixture", "--workspace", ws,
        "--materials", os.path.join(os.path.dirname(ws), "materials"),
        "--mode", state.get("mode") or "from_scratch",
        "--time-budget", state.get("time_budget") or "le1d",
        "--language", state.get("language") or "en",
        "--artifact-mode", state.get("artifact_mode") or "chat",
        "--processing-mode", "full", "--json",
    ], capture_output=True, text=True, encoding="utf-8", env=env)
    if confirmed.returncode != 0:
        raise AssertionError(confirmed.stdout + confirmed.stderr)
    return ws


def _enable_phase_structured(ws):
    """Give phase-gate tests a minimal but genuine current-chapter ingestion IR."""
    ingest_dir = os.path.join(ws, ".ingest")
    os.makedirs(ingest_dir, exist_ok=True)
    with open(os.path.join(ingest_dir, "build_manifest.json"), "w",
              encoding="utf-8", newline="\n") as stream:
        json.dump({"schema_version": 1, "pipeline_version": "ingestion-v1"}, stream)
        stream.write("\n")
    rows = [{
        "unit_id": "phase-sem-1", "source_file": "course.pdf", "page": 1,
        "kind": "text", "chapter_id": "ch01", "provenance": "material",
        "text": "Addition combines two quantities.",
    }]
    for index, item_id in enumerate(("ex1", "q1", "q2"), 1):
        rows.extend(({
            "unit_id": "phase-q-" + item_id, "source_file": "course.pdf",
            "page": index * 2, "kind": "question", "chapter_id": "ch01",
            "provenance": "material", "external_id": item_id,
            "text": "Question " + item_id, "metadata": {"source_language": "en"},
        }, {
            "unit_id": "phase-a-" + item_id, "source_file": "course.pdf",
            "page": index * 2 + 1, "kind": "answer", "chapter_id": "ch01",
            "provenance": "material", "external_id": item_id,
            "text": "Answer " + item_id, "metadata": {"source_language": "en"},
        }))
    with open(os.path.join(ingest_dir, "content_units.jsonl"), "w",
              encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")

    titles = {"ex1": "例题一", "q1": "Example q1", "q2": "Example q2"}
    lines = ["# 第一章笔记", ""]
    for minute, item_id in enumerate(("ex1", "q1", "q2")):
        lines.extend(notebook_engine.make_entry_lines(
            item_id, titles[item_id], "Walkthrough",
            "2026-07-14 10:%02d" % minute,
            "Seven-step walkthrough for %s." % item_id,
        ))
        lines.append("")
    with open(os.path.join(ws, "notebook", "ch01.md"), "w",
              encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines).rstrip() + "\n")
    # Structured completion now requires the visual receipt to bind the exact
    # content-unit generation used by the asset-policy snapshot.
    _refresh_visual_integrity(ws)
    return {
        item_id: notebook_engine.entry_anchor(item_id, titles[item_id])
        for item_id in titles
    }


def _write_phase_guide(ws, profile="full", language="en"):
    """Write a real schema-valid ch01 typed guide for update_progress gate tests."""
    anchors = _enable_phase_structured(ws)
    state_path = os.path.join(ws, "study_state.json")
    state = json.load(open(state_path, encoding="utf-8"))
    state["language"] = language
    with open(state_path, "w", encoding="utf-8") as stream:
        json.dump(state, stream, ensure_ascii=False)

    expected = ["ex1", "q1", "q2"]
    def localized(text):
        if language == "bilingual":
            return {"zh": text, "en": text}
        return {language: text}

    source = {
        "source_file": "course.pdf", "pages": [1],
        "source_unit_id": "phase-sem-1", "role": "concept",
    }
    walkthroughs = []
    for index, item_id in enumerate(expected, 1):
        original_language = "en"
        answer_provenance = (
            {"zh": "ai_supplemented", "en": "material"}
            if language == "bilingual" else {language: "material"}
        )
        walkthroughs.append({
            "item_id": item_id, "source_type": "other",
            "answer_provenance": answer_provenance,
            "knowledge_point_ids": ["kp1"], "title": localized("Example " + item_id),
            "knowledge_point_uses": {"kp1": localized("Apply the addition concept.")},
            "notebook_anchor": anchors[item_id],
            "original_language": original_language, "prompt_asset_mode": "none",
            "prompt_asset_paths": [], "answer_asset_paths": [], "translation": {},
            "prompt_text": "Question " + item_id, "what_asked": localized("Solve it."),
            "known_quantities": [], "unknown_quantities": [], "formula_uses": [],
            "solution_kind": "concept",
            "no_formula_reason": localized("This conceptual item needs no formula."),
            "steps": [localized("Work the problem.")],
            "answer": localized("Answer " + item_id),
            "source_trace": [{
                "source_file": "course.pdf", "pages": [index * 2],
                "source_unit_id": "phase-q-" + item_id, "role": "question",
            }, {
                "source_file": "course.pdf", "pages": [index * 2 + 1],
                "source_unit_id": "phase-a-" + item_id, "role": "answer",
            }],
        })
    omissions = []
    if profile == "abridged":
        omitted = walkthroughs.pop()
        omissions.append({
            "item_id": omitted["item_id"], "knowledge_point_ids": ["kp1"],
            "reason": localized("Shortened by explicit request."),
            "source_refs": [omitted["source_trace"][0]],
        })
    manifest = {
        "schema_version": 1, "chapter": 1, "language": language, "profile": profile,
        "knowledge_points": [{
            "id": "kp1", "title": localized("Concept"),
            "explanation": localized("A source-backed concept."), "formulas": [],
            "source_refs": [source], "source_unit_ids": ["phase-sem-1"],
            "example_ids": expected,
        }],
        "walkthroughs": walkthroughs, "omissions": omissions,
        "semantic_exclusions": [],
    }
    with open(os.path.join(ws, "notebook", "ch01.guide.json"),
              "w", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False)


def _write_visual_receipt(ws):
    state = _state(ws)
    with open(os.path.join(ws, ".phase-test-examprep-home"), "r",
              encoding="utf-8") as stream:
        fixture_home = stream.read().strip()
    materials = os.path.join(os.path.dirname(ws), "materials")
    env = dict(os.environ)
    env["EXAMPREP_HOME"] = fixture_home
    confirmed = subprocess.run([
        sys.executable, os.path.join(SCRIPTS, "exam_start.py"), "confirm",
        "--course", "phase-fixture", "--workspace", ws, "--materials", materials,
        "--mode", state.get("mode") or "from_scratch",
        "--time-budget", state.get("time_budget") or "le1d",
        "--language", state.get("language") or "en",
        "--artifact-mode", state.get("artifact_mode") or "chat",
        "--processing-mode", "full", "--json",
    ], capture_output=True, text=True, encoding="utf-8", env=env)
    if confirmed.returncode != 0:
        raise AssertionError(confirmed.stdout + confirmed.stderr)

    guide_dir = os.path.join(ws, "study_guide")
    os.makedirs(guide_dir, exist_ok=True)
    paths = {
        "html": os.path.join(guide_dir, "ch01.html"),
        "pdf": os.path.join(guide_dir, "ch01.pdf"),
    }
    payloads = {
        "html": b"<!doctype html><html><body>Readable fixture guide.</body></html>",
        "pdf": b"%PDF-1.4\nfixture guide",
    }
    digests = {}
    for kind, path in paths.items():
        with open(path, "wb") as stream:
            stream.write(payloads[kind])
        digests[kind] = hashlib.sha256(payloads[kind]).hexdigest()
    old_home = os.environ.get("EXAMPREP_HOME")
    os.environ["EXAMPREP_HOME"] = fixture_home
    try:
        gate = artifact_qa.exam_start.check_registered_workspace_gate(ws)
        start_gate = artifact_qa._start_gate_snapshot(gate)
        manifest_path = os.path.join(ws, "notebook", "ch01.guide.json")
        input_hash = artifact_qa._conversion_input_hash(paths["html"])
        converter = os.path.abspath("C:/browser/msedge.exe")
        started = "2026-07-14T10:00:00Z"
        completed = "2026-07-14T10:00:01Z"
        conversion_gate_hash = artifact_qa._canonical_hash(start_gate)
        manifest_rel = "notebook/ch01.guide.json"
        html_rel = "study_guide/ch01.html"
        pdf_rel = "study_guide/ch01.pdf"
        manifest_hash = artifact_qa._sha256_file(manifest_path)
        receipt = {
            "schema_version": 3, "artifact_type": "study_guide", "chapter": 1,
            "profile": "full", "language": state.get("language") or "en",
            "content_manifest": manifest_rel,
            "content_manifest_sha256": manifest_hash,
            "expected_item_ids": ["ex1", "q1", "q2"],
            "rendered_item_ids": ["ex1", "q1", "q2"], "omitted_item_ids": [],
            "html_file": html_rel, "html_sha256": digests["html"],
            "pdf_file": pdf_rel, "pdf_sha256": digests["pdf"],
            "pdf_backend": "browser", "converter": converter,
            "native_adapter_id": None, "native_adapter_version": None,
            "conversion_input_html_sha256": input_hash,
            "conversion_started_at": started, "conversion_completed_at": completed,
            "conversion_start_gate_sha256": conversion_gate_hash,
            "conversion_run_sha256": artifact_qa._conversion_run_hash(
                1, "full", state.get("language") or "en",
                manifest_rel, manifest_hash, html_rel, digests["html"],
                pdf_rel, digests["pdf"], "browser", input_hash, converter,
                None, None, started, completed, conversion_gate_hash, start_gate),
            "preflight": {"status": "passed", "pdf_backend": "browser"},
            "start_gate": start_gate, "generated_at": "2026-07-14T10:00:02Z",
            "status": "qa_pending",
            "visual_qa": {"schema_version": 1, "status": "pending"},
        }
        with open(os.path.join(guide_dir, "ch01.receipt.json"),
                  "w", encoding="utf-8") as stream:
            json.dump(receipt, stream, ensure_ascii=False)

        class Backend(object):
            name = "phase-fixture-renderer"

            def render_pages(self, unused_path):
                png = _valid_png(1200, 1800)
                return [{
                    "png": png,
                    "text": "Readable chapter study guide content for phase evidence.\nPage 1 of 1",
                    "width": 1200, "height": 1800, "white_ratio": 0.93,
                }]

        code, summary = artifact_qa.render(
            ws, 1, backend=Backend(), now="2026-07-14T10:01:00Z")
        if code != 0:
            raise AssertionError(summary)
        code, summary = artifact_qa.accept(
            ws, 1, "all", "phase-fixture", page_verdicts=["1=pass:visually checked"],
            now="2026-07-14T10:02:00Z")
        if code != 0:
            raise AssertionError(summary)
    finally:
        if old_home is None:
            os.environ.pop("EXAMPREP_HOME", None)
        else:
            os.environ["EXAMPREP_HOME"] = old_home


class Migration(unittest.TestCase):
    # ---- regression guards for Codex round-18 ----

    def test_init_skips_legacy_empty_labels(self):
        md = LEGACY_MD.replace("| [#q1] | 第1章 | 栈顺序 | 混淆LIFO | 未复习 |",
                               "| 暂无错题 | - | - | - |").replace("- 循环队列取模没搞懂", "- 暂无")
        ws = _mk_ws(tempfile.mkdtemp(), md=md)
        r = _up(ws, ["init"])
        self.assertEqual(r.returncode, 0, r.stderr)
        st = _state(ws)
        self.assertEqual(st["mistake_archive"], [])               # 「暂无错题」是空档占位不是条目
        self.assertEqual(st["confusion_log"], [])                 # 裸「暂无」bullet 同理

    def test_init_keeps_real_note_containing_zanwu(self):
        md = LEGACY_MD.replace("- 循环队列取模没搞懂", "- 暂无法确定循环不变式怎么选")
        ws = _mk_ws(tempfile.mkdtemp(), md=md)
        _up(ws, ["init"])
        st = _state(ws)
        self.assertEqual(len(st["confusion_log"]), 1)             # 只是包含占位字样的真实笔记不能丢
        self.assertIn("循环不变式", st["confusion_log"][0]["note"])

    def test_english_phase_plan_guards_set_phase(self):
        ws = _mk_ws(tempfile.mkdtemp(), md=None)
        with open(os.path.join(ws, "study_plan.md"), "w", encoding="utf-8", newline=chr(10)) as f:
            f.write("# Plan" + chr(10) + "## Phase 1: Stack" + chr(10) + "## Phase 2: Queue" + chr(10))
        r = _up(ws, ["init"])
        self.assertEqual(r.returncode, 0, r.stderr)
        r99 = _up(ws, ["set", "--phase", "99"])
        self.assertNotEqual(r99.returncode, 0)                    # 英文 Phase N 计划的阶段守卫同样生效
        self.assertEqual(_state(ws)["current_phase"], 1)
        r2 = _up(ws, ["set", "--phase", "2"])
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertEqual(_state(ws)["current_phase"], 2)

    def test_short_confusion_table_migrates_note(self):
        md = LEGACY_MD.replace(
            "- 循环队列取模没搞懂",
            "| 序号 | 疑难点 | 状态 |" + chr(10) + "| :- | :- | :- |" + chr(10)
            + "| 1 | 短表疑难点内容 | 待回顾 |")
        ws = _mk_ws(tempfile.mkdtemp(), md=md)
        r = _up(ws, ["init"])
        self.assertEqual(r.returncode, 0, r.stderr)
        row = _state(ws)["confusion_log"][0]
        self.assertIn("短表疑难点内容", row["note"])              # 无章节列时疑难点不再被当成章节
        self.assertEqual(row["status"], "to_revisit")             # v4：state 存代号（视图仍渲染 待回顾）
        self.assertIsNone(row["chapter"])

    def test_prose_phase_mention_not_a_plan_entry(self):
        ws = _mk_ws(tempfile.mkdtemp(), md=None)
        with open(os.path.join(ws, "study_plan.md"), "w", encoding="utf-8", newline=chr(10)) as f:
            f.write("# Plan" + chr(10) + "## 阶段1：栈" + chr(10) + chr(10)
                    + "注意：不要提前进入阶段99（超纲内容）。" + chr(10))
        r = _up(ws, ["init"])
        self.assertEqual(r.returncode, 0, r.stderr)
        r99 = _up(ws, ["set", "--phase", "99"])
        self.assertNotEqual(r99.returncode, 0)                    # 散文里的阶段号不是计划条目
        self.assertEqual(_state(ws)["current_phase"], 1)

    def test_blank_init_skips_phase_zero(self):
        ws = _mk_ws(tempfile.mkdtemp(), md=None)
        with open(os.path.join(ws, "study_plan.md"), "w", encoding="utf-8", newline=chr(10)) as f:
            f.write("# Plan" + chr(10) + "## 阶段0：绪论" + chr(10) + "## 阶段1：栈" + chr(10))
        r = _up(ws, ["init"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_state(ws)["current_phase"], 1)          # 不种下全工具链拒收的 0 号断点
        r2 = _up(ws, ["add-mistake", "--chapter", "1", "--note", "阶段0计划下照常可用"])
        self.assertEqual(r2.returncode, 0, r2.stderr)

    def test_ordered_list_plan_phases_recognized(self):
        ws = _mk_ws(tempfile.mkdtemp(), md=None)
        with open(os.path.join(ws, "study_plan.md"), "w", encoding="utf-8", newline=chr(10)) as f:
            f.write("# Plan" + chr(10) + "1. 阶段 1：栈" + chr(10) + "2. 阶段 2：队列" + chr(10))
        r = _up(ws, ["init"])
        self.assertEqual(r.returncode, 0, r.stderr)
        r99 = _up(ws, ["set", "--phase", "99"])
        self.assertNotEqual(r99.returncode, 0)                    # 有序列表计划的守卫同样生效
        r2 = _up(ws, ["set", "--phase", "2"])
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertEqual(_state(ws)["current_phase"], 2)

    def test_bold_heading_ends_section(self):
        md = LEGACY_MD + "**下一步**" + chr(10) + "- 这行是待办不是疑难记录" + chr(10)
        ws = _mk_ws(tempfile.mkdtemp(), md=md)
        _up(ws, ["init"])
        st = _state(ws)
        self.assertEqual(len(st["confusion_log"]), 1)             # 加粗标题终结上一节
        self.assertNotIn("待办", st["confusion_log"][0]["note"])

    def test_symlinked_plan_rejected_by_phase_guard(self):
        ws = _mk_ws(tempfile.mkdtemp(), md=None)
        outside = os.path.join(os.path.dirname(ws), "outside_plan.md")
        with open(outside, "w", encoding="utf-8", newline=chr(10)) as f:
            f.write("## 阶段99：外部计划" + chr(10))
        try:
            os.symlink(outside, os.path.join(ws, "study_plan.md"))
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("无符号链接权限")
        r = _up(ws, ["init"])
        self.assertNotEqual(r.returncode, 0)                      # 外部计划不被信任
        self.assertIn("符号链接", r.stderr)

    def test_newline_preference_cannot_inject_rows(self):
        ws = _mk_ws(tempfile.mkdtemp())
        r = _up(ws, ["init"])
        self.assertEqual(r.returncode, 0, r.stderr)
        st = _state(ws)
        st["preferences"]["风格"] = "简洁" + chr(10) + "## ❌ 错题档案记录" + chr(10) + "- [#fake] 注入的假行"
        json.dump(st, open(os.path.join(ws, "study_state.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
        r = _up(ws, ["render"])
        self.assertEqual(r.returncode, 0, r.stderr)
        r = _up(ws, ["init", "--force"])
        self.assertEqual(r.returncode, 0, r.stderr)
        st2 = _state(ws)
        self.assertFalse(any((row.get("id") or "") == "fake"
                             for row in st2["mistake_archive"]))   # 带换行的偏好值注不进档案

    def test_smoke_state_check_rejects_checkpoint_regression(self):
        sys.path.insert(0, os.path.join(ROOT, "benchmark", "behavior_smoke"))
        import run_behavior_smoke as S
        d = tempfile.mkdtemp()
        fx = os.path.join(d, "fx")
        os.makedirs(fx)
        json.dump({"current_phase": 2, "mistake_archive": []},
                  open(os.path.join(fx, "study_state.json"), "w", encoding="utf-8"))
        bad = os.path.join(d, "bad.json")
        json.dump({"current_phase": 1,
                   "mistake_archive": [{"id": "q", "note": "x"}]},
                  open(bad, "w", encoding="utf-8"))
        sc = {"state_after": bad}
        self.assertFalse(S._state_row_written(fx, sc, "state_after", "mistake_archive", "x"))

    def test_init_adopts_legacy_md(self):
        ws = _mk_ws(tempfile.mkdtemp())
        r = _up(ws, ["init"])
        self.assertEqual(r.returncode, 0, r.stderr)
        st = _state(ws)
        self.assertEqual(st["current_phase"], 3)                  # 模板断点行被解析
        self.assertEqual(st["mistake_archive"][0]["id"], "q1")    # 表格行迁移
        self.assertIn("循环队列", st["confusion_log"][0]["note"])  # bullet 行迁移
        md = open(os.path.join(ws, "study_progress.md"), encoding="utf-8").read()
        self.assertIn("当前进行阶段**：阶段 3", md)                # md 重渲染保持可解析形态
        self.assertIn("自动生成", md)

    def test_migrated_note_excludes_status_cell(self):
        ws = _mk_ws(tempfile.mkdtemp())
        _up(ws, ["init"])
        row = _state(ws)["mistake_archive"][0]
        self.assertEqual(row["status"], "未复习")
        self.assertNotIn("未复习", row["note"])                    # 状态不再在 note 里重复一份
        self.assertIn("混淆LIFO", row["note"])

    def test_migrated_three_col_row_keeps_note(self):
        md = LEGACY_MD.replace("| [#q1] | 第1章 | 栈顺序 | 混淆LIFO | 未复习 |",
                               "| [#q1] | 第1章 | 只有笔记没有状态列 |")
        ws = _mk_ws(tempfile.mkdtemp(), md=md)
        _up(ws, ["init"])
        row = _state(ws)["mistake_archive"][0]
        self.assertEqual(row["note"], "只有笔记没有状态列")         # 无状态列时整个尾部是 note
        self.assertEqual(row["status"], "to_review")              # v4：state 存代号（视图仍渲染 待复盘）

    def test_migration_preserves_phase_checklist(self):
        md = LEGACY_MD + ("\n## 📊 知识点打卡状态\n- [x] **阶段 1**：栈与队列 (关联 `references/wiki/ch1.md`)\n"
                          "- [ ] **阶段 2**：树 (关联 `references/wiki/ch2.md`)\n")
        ws = _mk_ws(tempfile.mkdtemp(), md=md)
        r = _up(ws, ["init"])
        self.assertEqual(r.returncode, 0, r.stderr)
        st = _state(ws)
        self.assertEqual(len(st["phase_checklist"]), 2)           # 打卡状态随迁移进 state，不丢
        self.assertTrue(st["phase_checklist"][0]["done"])
        self.assertFalse(st["phase_checklist"][1]["done"])
        out = open(os.path.join(ws, "study_progress.md"), encoding="utf-8").read()
        self.assertIn("知识点打卡状态", out)                       # 生成视图渲染回打卡区
        self.assertIn("- [x] **阶段 1**", out)
        self.assertIn("- [ ] **阶段 2**", out)

    def test_set_check_official_path(self):
        md = LEGACY_MD + "\n## 📊 知识点打卡状态\n- [ ] **阶段 1**：栈与队列\n- [ ] **模拟测试**：综合自测\n"
        ws = _mk_ws(tempfile.mkdtemp(), md=md)
        self.assertEqual(_up(ws, ["init"]).returncode, 0)
        self.assertEqual(_up(ws, ["set", "--phase", "1",
                                  "--processing-mode", "full"]).returncode, 0)
        r = _up(ws, ["set-check", "--match", "阶段 1"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(_state(ws)["phase_checklist"][0]["done"])  # 勾选走官方路径
        self.assertIn("- [x] **阶段 1**", open(os.path.join(ws, "study_progress.md"), encoding="utf-8").read())
        r = _up(ws, ["set-check", "--index", "1", "--undone"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(_state(ws)["phase_checklist"][0]["done"])

    def test_legacy_set_check_warns_but_stays_compatible(self):
        md = LEGACY_MD + "\n## 📊 知识点打卡状态\n- [ ] **阶段 1**：栈与队列\n"
        ws = _mk_ws(tempfile.mkdtemp(), md=md)
        self.assertEqual(_up(ws, ["init"]).returncode, 0)
        self.assertEqual(_up(ws, ["set", "--phase", "1",
                                  "--processing-mode", "full"]).returncode, 0)
        r = _up(ws, ["set-check", "--match", "阶段 1"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("旧工作区", r.stderr)
        self.assertTrue(_state(ws)["phase_checklist"][0]["done"])

    def test_v40_visual_indexes_alone_do_not_enable_v41_hard_gate(self):
        md = LEGACY_MD + "\n## 📊 知识点打卡状态\n- [ ] **阶段 1**：栈与队列\n"
        ws = _mk_ws(tempfile.mkdtemp(), md=md)
        os.makedirs(os.path.join(ws, "references"))
        json.dump({"files": {}}, open(os.path.join(ws, "references", "figure_page_index.json"),
                                     "w", encoding="utf-8"))
        json.dump({"suspects": []}, open(os.path.join(ws, "references", "image_question_index.json"),
                                         "w", encoding="utf-8"))
        self.assertEqual(_up(ws, ["init"]).returncode, 0)
        self.assertEqual(_up(ws, ["set", "--phase", "1",
                                  "--processing-mode", "full"]).returncode, 0)
        r = _up(ws, ["set-check", "--match", "阶段 1"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("旧工作区", r.stderr)

    def test_partial_or_broken_v41_manifest_never_fails_open_as_legacy(self):
        for teaching_text in ("[]", "{broken"):
            md = LEGACY_MD + "\n## 📊 知识点打卡状态\n- [ ] **阶段 1**：栈与队列\n"
            ws = _mk_ws(tempfile.mkdtemp(), md=md)
            os.makedirs(os.path.join(ws, "references"))
            with open(os.path.join(ws, "references", "teaching_examples.json"),
                      "w", encoding="utf-8") as f:
                f.write(teaching_text)
            self.assertEqual(_up(ws, ["init"]).returncode, 0)
            self.assertEqual(_up(ws, ["set", "--phase", "1",
                                      "--processing-mode", "full"]).returncode, 0)
            before = open(os.path.join(ws, "study_state.json"), "rb").read()
            r = _up(ws, ["set-check", "--match", "阶段 1"])
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("partial/broken", r.stderr)
            self.assertEqual(open(os.path.join(ws, "study_state.json"), "rb").read(), before)

    def test_init_idempotent_without_force(self):
        ws = _mk_ws(tempfile.mkdtemp())
        _up(ws, ["init"])
        r = _up(ws, ["init"])
        self.assertEqual(r.returncode, 2)                         # 幂等保护
        self.assertEqual(_up(ws, ["init", "--force"]).returncode, 0)

    def test_init_refuses_non_utf8_md(self):
        ws = _mk_ws(tempfile.mkdtemp(), md=None)
        with open(os.path.join(ws, "study_progress.md"), "wb") as f:
            f.write("当前阶段：2".encode("gbk"))                   # 真实乱码场景
        r = _up(ws, ["init"])
        self.assertEqual(r.returncode, 1)                         # fail-loud，不猜编码静默迁移
        self.assertIn("UTF-8", r.stderr)
        self.assertFalse(os.path.isfile(os.path.join(ws, "study_state.json")))


class PhaseEvidence(unittest.TestCase):
    CORE = (("wiki", "references/wiki/ch1.md"),
            ("visual", "references/figure_page_index.json"),
            ("teaching-example", "ex1"),
            ("notebook", "notebook/ch01.md#ex1-例题一"))

    def _record_core(self, ws):
        for kind, ref in self.CORE:
            r = _up(ws, ["record-phase-evidence", "--phase", "1", "--kind", kind, "--ref", ref])
            self.assertEqual(r.returncode, 0, "%s: %s" % (kind, r.stderr))

    def test_new_manifest_set_check_without_evidence_fails_without_pollution(self):
        ws = _phase_evidence_ws()
        before = open(os.path.join(ws, "study_state.json"), "rb").read()
        r = _up(ws, ["set-check", "--match", "阶段 1"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("phase_evidence", r.stderr)
        self.assertEqual(open(os.path.join(ws, "study_state.json"), "rb").read(), before)
        self.assertFalse(_state(ws)["phase_checklist"][0]["done"])

    def test_student_attempt_visual_asset_cannot_be_recorded_as_completion_evidence(self):
        ws = _phase_evidence_ws()
        asset_rel = "references/assets/attempt.png"
        asset_path = os.path.join(ws, *asset_rel.split("/"))
        os.makedirs(os.path.dirname(asset_path), exist_ok=True)
        with open(asset_path, "wb") as stream:
            stream.write(b"attempt-audit")
        teaching_path = os.path.join(ws, "references", "teaching_examples.json")
        teaching = json.load(open(teaching_path, encoding="utf-8"))
        teaching[0]["assets"] = [{"path": asset_rel, "role": "student_attempt"}]
        with open(teaching_path, "w", encoding="utf-8") as stream:
            json.dump(teaching, stream)
        _refresh_visual_integrity(ws)
        before = open(os.path.join(ws, "study_state.json"), "rb").read()
        result = _up(ws, [
            "record-phase-evidence", "--phase", "1", "--kind", "visual",
            "--ref", asset_rel,
        ])
        self.assertNotEqual(0, result.returncode)
        self.assertIn("student_attempt", result.stderr)
        self.assertEqual(before, open(os.path.join(ws, "study_state.json"), "rb").read())

    def test_visual_checkpoint_with_attempt_only_prompt_cannot_count(self):
        ws = _phase_evidence_ws()
        asset_rel = "references/assets/attempt.png"
        asset_path = os.path.join(ws, *asset_rel.split("/"))
        os.makedirs(os.path.dirname(asset_path), exist_ok=True)
        with open(asset_path, "wb") as stream:
            stream.write(b"attempt-audit")
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        bank = json.load(open(bank_path, encoding="utf-8"))
        bank[0]["requires_assets"] = True
        bank[0]["assets"] = [{"path": asset_rel, "role": "student_attempt"}]
        with open(bank_path, "w", encoding="utf-8") as stream:
            json.dump(bank, stream)
        _refresh_visual_integrity(ws)
        result = _up(ws, [
            "record-phase-evidence", "--phase", "1", "--kind", "checkpoint",
            "--ref", "q1", "--outcome", "passed",
        ])
        self.assertNotEqual(0, result.returncode)
        self.assertIn("prompt_asset_missing", result.stderr)

    def test_new_global_attempt_conflict_invalidates_existing_completion_evidence(self):
        ws = _phase_evidence_ws()
        self._record_core(ws)
        asset_rel = "references/assets/shared.png"
        asset_path = os.path.join(ws, *asset_rel.split("/"))
        os.makedirs(os.path.dirname(asset_path), exist_ok=True)
        with open(asset_path, "wb") as stream:
            stream.write(b"shared-audit")
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        bank = json.load(open(bank_path, encoding="utf-8"))
        bank[0]["assets"] = [{"path": asset_rel, "role": "question_context"}]
        with open(bank_path, "w", encoding="utf-8") as stream:
            json.dump(bank, stream)
        teaching_path = os.path.join(ws, "references", "teaching_examples.json")
        teaching = json.load(open(teaching_path, encoding="utf-8"))
        teaching[0]["assets"] = [{"path": "references\\assets\\shared.png",
                                  "role": "student_attempt"}]
        with open(teaching_path, "w", encoding="utf-8") as stream:
            json.dump(teaching, stream)
        _refresh_visual_integrity(ws)
        before = open(os.path.join(ws, "study_state.json"), "rb").read()
        result = _up(ws, [
            "complete-phase", "--phase", "1", "--status", "covered_unverified",
        ])
        self.assertNotEqual(0, result.returncode)
        self.assertIn("student-attempt", result.stderr)
        self.assertEqual(before, open(os.path.join(ws, "study_state.json"), "rb").read())

    def test_new_content_unit_attempt_stales_visual_completion_evidence(self):
        ws = _phase_evidence_ws()
        _write_phase_guide(ws)
        asset_rel = "references/assets/wiki-counted.png"
        asset_path = os.path.join(ws, *asset_rel.split("/"))
        os.makedirs(os.path.dirname(asset_path), exist_ok=True)
        with open(asset_path, "wb") as stream:
            stream.write(b"wiki-counted-before-attempt-classification")
        wiki_path = os.path.join(ws, "references", "wiki", "ch1.md")
        with open(wiki_path, "a", encoding="utf-8", newline="\n") as stream:
            stream.write("\n![counted visual](../assets/wiki-counted.png)\n")
        # This represents the last trusted visual build.  Its integrity block
        # binds the benign structured IR as well as the wiki and source layers.
        _refresh_visual_integrity(ws)
        self._record_core(ws)

        units_path = os.path.join(ws, ".ingest", "content_units.jsonl")
        with open(units_path, "a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps({
                "unit_id": "foreign-attempt-visual", "source_file": "course.pdf",
                "page": 99, "kind": "figure", "chapter_id": "ch02",
                "provenance": "material", "text": "",
                "asset_path": asset_rel, "asset_role": "student_attempt",
                "metadata": {},
            }, ensure_ascii=False) + "\n")

        before = open(os.path.join(ws, "study_state.json"), "rb").read()
        result = _up(ws, [
            "complete-phase", "--phase", "1", "--status", "covered_unverified",
        ])
        self.assertNotEqual(0, result.returncode)
        self.assertIn("content_units.jsonl", result.stderr)
        self.assertEqual(before, open(os.path.join(ws, "study_state.json"), "rb").read())

    def test_le1d_can_complete_covered_unverified_with_core_evidence(self):
        ws = _phase_evidence_ws()
        self.assertEqual(_up(ws, ["set", "--time-budget", "le1d"]).returncode, 0)
        self._record_core(ws)
        r = _up(ws, ["complete-phase", "--phase", "1", "--status", "covered_unverified"])
        self.assertEqual(r.returncode, 0, r.stderr)
        st = _state(ws)
        self.assertTrue(st["phase_checklist"][0]["done"])
        self.assertEqual(st["phase_evidence"]["1"]["status"], "covered_unverified")
        self.assertEqual(st["phase_evidence"]["1"].get("checkpoint", []), [])
        rendered = open(os.path.join(ws, "study_progress.md"), encoding="utf-8").read()
        self.assertIn("已覆盖但未验证", rendered)

    def test_structured_phase_completion_requires_valid_full_typed_guide_for_both_statuses(self):
        for status in ("covered_unverified", "verified"):
            with self.subTest(status=status):
                ws = _phase_evidence_ws()
                _enable_phase_structured(ws)
                self.assertEqual(_up(ws, ["set", "--language", "en"]).returncode, 0)
                self._record_core(ws)
                if status == "verified":
                    for qid, outcome in (("q1", "passed"), ("q2", "wrong")):
                        result = _up(ws, ["record-phase-evidence", "--kind", "checkpoint",
                                          "--ref", qid, "--outcome", outcome])
                        self.assertEqual(result.returncode, 0, result.stderr)
                before = open(os.path.join(ws, "study_state.json"), "rb").read()
                result = _up(ws, ["complete-phase", "--status", status])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("typed Study Guide manifest", result.stderr)
                self.assertIn("ch01", result.stderr)
                self.assertEqual(open(os.path.join(ws, "study_state.json"), "rb").read(), before)

    def test_structured_phase_rejects_valid_abridged_guide_but_chat_accepts_full_without_pdf(self):
        ws = _phase_evidence_ws()
        _enable_phase_structured(ws)
        _write_phase_guide(ws, profile="abridged")
        self._record_core(ws)
        result = _up(ws, ["complete-phase", "--status", "covered_unverified"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("profile=full", result.stderr)

        _write_phase_guide(ws, profile="full")
        result = _up(ws, ["complete-phase", "--status", "covered_unverified"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.exists(os.path.join(ws, "study_guide", "ch01.pdf")))

    def test_structured_visual_phase_requires_artifact_ready_and_v1_is_read_only(self):
        ws = _phase_evidence_ws()
        _enable_phase_structured(ws)
        _write_phase_guide(ws, profile="full")
        self.assertEqual(_up(ws, ["set", "--artifact-mode", "visual"]).returncode, 0)
        self._record_core(ws)
        before = open(os.path.join(ws, "study_state.json"), "rb").read()
        result = _up(ws, ["complete-phase", "--status", "covered_unverified"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("artifact_ready", result.stderr)
        self.assertIn("chapter_artifact_receipt_missing", result.stderr)
        self.assertEqual(open(os.path.join(ws, "study_state.json"), "rb").read(), before)

        with self.assertRaisesRegex(
                artifact_qa.QAError, "ingestion-v1 Study Guide compatibility is read-only"):
            _write_visual_receipt(ws)
        self.assertEqual(open(os.path.join(ws, "study_state.json"), "rb").read(), before)

    def test_language_change_makes_old_typed_guide_invalid_until_relocalized(self):
        ws = _phase_evidence_ws()
        _enable_phase_structured(ws)
        _write_phase_guide(ws, profile="full", language="en")
        self.assertEqual(_up(ws, ["set", "--language", "zh"]).returncode, 0)
        self._record_core(ws)
        result = _up(ws, ["complete-phase", "--status", "covered_unverified"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match study_state.json.language=zh", result.stderr)

    def test_phase_completion_rejects_stale_bank_teaching_and_wiki_digests(self):
        for rel in ("references/quiz_bank.json", "references/teaching_examples.json",
                    "references/wiki/ch1.md"):
            ws = _phase_evidence_ws()
            self._record_core(ws)
            path = os.path.join(ws, *rel.split("/"))
            with open(path, "a", encoding="utf-8") as stream:
                stream.write("\n")
            result = _up(ws, ["complete-phase", "--status", "covered_unverified"])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("stale", result.stderr)
            self.assertIn(rel, result.stderr)

    def test_phase_completion_rejects_tampered_derived_visual_result(self):
        ws = _phase_evidence_ws()
        self._record_core(ws)
        image_path = os.path.join(ws, "references", "image_question_index.json")
        image = json.load(open(image_path, encoding="utf-8"))
        image["prompt_suspects"] = [{"id": "q1", "chapter": 1}]
        with open(image_path, "w", encoding="utf-8") as stream:
            json.dump(image, stream)
        result = _up(ws, ["complete-phase", "--status", "covered_unverified"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("派生结果", result.stderr)
        self.assertIn("image_question_index.json", result.stderr)

    def test_phase_completion_rejects_source_pdf_changed_after_index(self):
        ws = _phase_evidence_ws()
        self._record_core(ws)
        source_pdf = os.path.join(os.path.dirname(ws), "materials", "course.pdf")
        with open(source_pdf, "ab") as stream:
            stream.write(b"changed")
        result = _up(ws, ["complete-phase", "--status", "covered_unverified"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("原始 PDF", result.stderr)
        self.assertIn("stale", result.stderr)

    def test_phase_completion_rejects_source_pdf_added_after_index(self):
        ws = _phase_evidence_ws()
        self._record_core(ws)
        with open(os.path.join(os.path.dirname(ws), "materials", "new-chapter.pdf"), "wb") as stream:
            stream.write(b"%PDF-new")
        result = _up(ws, ["complete-phase", "--status", "covered_unverified"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PDF 路径清单", result.stderr)
        self.assertIn("stale", result.stderr)

    def test_phase_completion_rechecks_declared_prompt_asset_after_index(self):
        ws = _phase_evidence_ws()
        asset_rel = "references/assets/phase-figure.png"
        asset = os.path.join(ws, *asset_rel.split("/"))
        os.makedirs(os.path.dirname(asset), exist_ok=True)
        with open(asset, "wb") as stream:
            stream.write(b"png")
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        bank = json.load(open(bank_path, encoding="utf-8"))
        bank.append({"id": "q_visual", "chapter": 1, "requires_assets": True,
                     "assets": [{"path": asset_rel, "role": "question_context"}]})
        with open(bank_path, "w", encoding="utf-8") as stream:
            json.dump(bank, stream)
        _refresh_visual_integrity(ws)
        self._record_core(ws)
        os.remove(asset)
        result = _up(ws, ["complete-phase", "--status", "covered_unverified"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("不可读题面 asset", result.stderr)
        self.assertIn("phase-figure.png", result.stderr)

    def test_verified_requires_checkpoint_then_succeeds(self):
        ws = _phase_evidence_ws()
        self._record_core(ws)
        before = open(os.path.join(ws, "study_state.json"), "rb").read()
        r = _up(ws, ["complete-phase", "--phase", "1", "--status", "verified"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("checkpoint", r.stderr)
        self.assertEqual(open(os.path.join(ws, "study_state.json"), "rb").read(), before)
        r = _up(ws, ["record-phase-evidence", "--phase", "1", "--kind", "checkpoint",
                     "--ref", "q1", "--outcome", "passed"])
        self.assertEqual(r.returncode, 0, r.stderr)
        r = _up(ws, ["complete-phase", "--phase", "1", "--status", "verified"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("2 个不同题目", r.stderr)
        r = _up(ws, ["record-phase-evidence", "--phase", "1", "--kind", "checkpoint",
                     "--ref", "q2", "--outcome", "wrong"])
        self.assertEqual(r.returncode, 0, r.stderr)
        r = _up(ws, ["complete-phase", "--phase", "1", "--status", "verified"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_state(ws)["phase_evidence"]["1"]["status"], "verified")

    def test_checkpoint_id_without_outcome_is_rejected(self):
        ws = _phase_evidence_ws()
        before = open(os.path.join(ws, "study_state.json"), "rb").read()
        r = _up(ws, ["record-phase-evidence", "--kind", "checkpoint", "--ref", "q1"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--outcome", r.stderr)
        self.assertEqual(open(os.path.join(ws, "study_state.json"), "rb").read(), before)

    def test_verified_rejects_two_checkpoints_when_none_passed(self):
        ws = _phase_evidence_ws()
        self._record_core(ws)
        for qid, outcome in (("q1", "wrong"), ("q2", "skipped")):
            r = _up(ws, ["record-phase-evidence", "--phase", "1", "--kind", "checkpoint",
                         "--ref", qid, "--outcome", outcome])
            self.assertEqual(r.returncode, 0, r.stderr)
        r = _up(ws, ["complete-phase", "--phase", "1", "--status", "verified"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("passed", r.stderr)

    def test_manifest_content_gaps_block_completion(self):
        ws = _phase_evidence_ws()
        self._record_core(ws)
        figure_path = os.path.join(ws, "references", "figure_page_index.json")
        figure = json.load(open(figure_path, encoding="utf-8"))
        figure["wiki_visual_coverage"]["per_chapter"]["ch1.md"]["missing"] = 1
        figure["wiki_visual_coverage"]["pages"] = [{"wiki_file": "ch1.md", "status": "missing"}]
        json.dump(figure, open(figure_path, "w", encoding="utf-8"), ensure_ascii=False)
        r = _up(ws, ["complete-phase", "--status", "covered_unverified"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("missing", r.stderr)

        # Repair wiki coverage, but leave an answer-side suspect in this chapter: still blocked.
        figure["wiki_visual_coverage"]["per_chapter"]["ch1.md"]["missing"] = 0
        figure["wiki_visual_coverage"]["pages"] = [{"wiki_file": "ch1.md", "status": "embedded"}]
        json.dump(figure, open(figure_path, "w", encoding="utf-8"), ensure_ascii=False)
        image_path = os.path.join(ws, "references", "image_question_index.json")
        json.dump({"prompt_suspects": [], "answer_suspects": [{"id": "q1", "chapter": 1}]},
                  open(image_path, "w", encoding="utf-8"), ensure_ascii=False)
        r = _up(ws, ["complete-phase", "--status", "covered_unverified"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("answer_suspects", r.stderr)

    def test_visual_recall_disabled_warning_blocks_false_zero_completion(self):
        ws = _phase_evidence_ws()
        self._record_core(ws)
        figure_path = os.path.join(ws, "references", "figure_page_index.json")
        figure = json.load(open(figure_path, encoding="utf-8"))
        figure["wiki_visual_coverage"] = {
            "detected": 0, "embedded": 0, "missing": 0,
            "per_chapter": {}, "pages": []}
        figure["warnings"] = ["no_materials: recall net disabled"]
        json.dump(figure, open(figure_path, "w", encoding="utf-8"), ensure_ascii=False)
        before = open(os.path.join(ws, "study_state.json"), "rb").read()
        r = _up(ws, ["complete-phase", "--status", "covered_unverified"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("视觉召回网", r.stderr)
        self.assertEqual(open(os.path.join(ws, "study_state.json"), "rb").read(), before)

    def test_nested_metadata_ids_cannot_be_used_as_checkpoint_evidence(self):
        ws = _phase_evidence_ws()
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        bank = json.load(open(bank_path, encoding="utf-8"))
        bank[0]["metadata"] = {"decoys": [{"id": "fake1", "chapter": 1},
                                            {"id": "fake2", "chapter": 1}]}
        json.dump(bank, open(bank_path, "w", encoding="utf-8"), ensure_ascii=False)
        before = open(os.path.join(ws, "study_state.json"), "rb").read()
        r = _up(ws, ["record-phase-evidence", "--kind", "checkpoint", "--ref", "fake1",
                     "--outcome", "passed"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("absent from the runtime-eligible bank", r.stderr)
        self.assertEqual(open(os.path.join(ws, "study_state.json"), "rb").read(), before)

    def test_scope_normalization_and_unscoped_suspects_are_fail_closed(self):
        ws = _phase_evidence_ws()
        self._record_core(ws)
        image_path = os.path.join(ws, "references", "image_question_index.json")
        for suspect in ({"id": "q1", "chapter": "01"}, {"id": "unknown"}):
            json.dump({"prompt_suspects": [suspect], "answer_suspects": []},
                      open(image_path, "w", encoding="utf-8"), ensure_ascii=False)
            r = _up(ws, ["complete-phase", "--status", "covered_unverified"])
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("prompt_suspects", r.stderr)

        teaching_path = os.path.join(ws, "references", "teaching_examples.json")
        json.dump([{"id": "ex1", "chapter": 1}, {"id": "ex2", "chapter": "01"}],
                  open(teaching_path, "w", encoding="utf-8"), ensure_ascii=False)
        json.dump({"prompt_suspects": [], "answer_suspects": []},
                  open(image_path, "w", encoding="utf-8"), ensure_ascii=False)
        r = _up(ws, ["complete-phase", "--status", "covered_unverified"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ex2", r.stderr)

    def test_teaching_manifest_requires_full_phase_coverage_but_empty_phase_is_na(self):
        ws = _phase_evidence_ws()
        self._record_core(ws)
        teaching_path = os.path.join(ws, "references", "teaching_examples.json")
        json.dump([{"id": "ex1", "chapter": 1}, {"id": "ex2", "chapter": 1}],
                  open(teaching_path, "w", encoding="utf-8"), ensure_ascii=False)
        r = _up(ws, ["complete-phase", "--status", "covered_unverified"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ex2", r.stderr)

        # A course/phase with no teaching examples is explicit N/A, not an impossible gate.
        ws2 = _phase_evidence_ws()
        json.dump([], open(os.path.join(ws2, "references", "teaching_examples.json"),
                           "w", encoding="utf-8"))
        json.dump({"teaching_example_ids": [], "teaching_examples_by_chapter": {},
                   "teaching_example_ids_by_chapter": {}},
                  open(os.path.join(ws2, "ingest_report.json"), "w", encoding="utf-8"))
        json.dump({"schema_version": 1, "policy": "append_only",
                   "teaching_example_ids": [], "teaching_example_ids_by_chapter": {}},
                  open(os.path.join(ws2, "references", "teaching_baseline.json"),
                       "w", encoding="utf-8"))
        _refresh_visual_integrity(ws2)  # represents rebuilding both indices after an intentional edit
        for kind, ref in (self.CORE[0], self.CORE[1], self.CORE[3]):
            rr = _up(ws2, ["record-phase-evidence", "--kind", kind, "--ref", ref])
            self.assertEqual(rr.returncode, 0, rr.stderr)
        r = _up(ws2, ["complete-phase", "--status", "covered_unverified"])
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_deleted_ingest_baseline_example_cannot_turn_phase_into_teaching_na(self):
        ws = _phase_evidence_ws()
        teaching_path = os.path.join(ws, "references", "teaching_examples.json")
        teaching = json.load(open(teaching_path, encoding="utf-8"))
        with open(teaching_path, "w", encoding="utf-8") as stream:
            json.dump([item for item in teaching if item["id"] != "ex1"], stream)
        _refresh_visual_integrity(ws)
        # Recreate the concrete fail-open bundle: wiki + visual + notebook, but no teaching evidence.
        for kind, ref in (self.CORE[0], self.CORE[1], self.CORE[3]):
            result = _up(ws, ["record-phase-evidence", "--phase", "1",
                              "--kind", kind, "--ref", ref])
            self.assertEqual(result.returncode, 0, result.stderr)
        before = open(os.path.join(ws, "study_state.json"), "rb").read()
        result = _up(ws, ["complete-phase", "--phase", "1",
                          "--status", "covered_unverified"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("保留基线", result.stderr)
        self.assertIn("ex1", result.stderr)
        self.assertEqual(open(os.path.join(ws, "study_state.json"), "rb").read(), before)

    def test_legacy_flat_baseline_missing_id_fails_closed_for_every_phase(self):
        ws = _phase_evidence_ws()
        os.remove(os.path.join(ws, "references", "teaching_baseline.json"))
        with open(os.path.join(ws, "ingest_report.json"), "w", encoding="utf-8") as stream:
            json.dump({"teaching_example_ids": ["lost-legacy-id"]}, stream)
        _refresh_visual_integrity(ws)
        for kind, ref in (self.CORE[0], self.CORE[1], self.CORE[3]):
            self.assertEqual(_up(ws, ["record-phase-evidence", "--kind", kind,
                                      "--ref", ref]).returncode, 0)
        result = _up(ws, ["complete-phase", "--status", "covered_unverified"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("旧报告没有逐章映射", result.stderr)

    def test_rewritten_ingest_report_cannot_shrink_append_only_teaching_baseline(self):
        ws = _phase_evidence_ws()
        teaching_path = os.path.join(ws, "references", "teaching_examples.json")
        with open(teaching_path, "w", encoding="utf-8") as stream:
            json.dump([{"id": "ex2", "chapter": 2}], stream)
        # Reproduce a later, smaller ingest report.  The independent append-only manifest must
        # retain ex1/ch1 even after both visual indices are deliberately rebuilt.
        with open(os.path.join(ws, "ingest_report.json"), "w", encoding="utf-8") as stream:
            json.dump({"teaching_example_ids": ["ex2"],
                       "teaching_example_ids_by_chapter": {"2": ["ex2"]}}, stream)
        _refresh_visual_integrity(ws)
        for kind, ref in (self.CORE[0], self.CORE[1], self.CORE[3]):
            result = _up(ws, ["record-phase-evidence", "--phase", "1",
                              "--kind", kind, "--ref", ref])
            self.assertEqual(result.returncode, 0, result.stderr)
        before = open(os.path.join(ws, "study_state.json"), "rb").read()
        result = _up(ws, ["complete-phase", "--phase", "1",
                          "--status", "covered_unverified"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ex1", result.stderr)
        self.assertEqual(open(os.path.join(ws, "study_state.json"), "rb").read(), before)

    def test_manual_answer_only_wiki_exposure_blocks_phase_completion(self):
        ws = _phase_evidence_ws()
        figure_path = os.path.join(ws, "references", "figure_page_index.json")
        with open(figure_path, encoding="utf-8") as stream:
            figure = json.load(stream)
        row = {"wiki_file": "ch1.md", "source_file": "course.pdf", "page": 9,
               "status": "deferred_answer", "coverage_issue": "manual_answer_exposure",
               "manual_assets": ["../assets/manual-answer.png"]}
        coverage = figure["wiki_visual_coverage"]
        coverage["deferred_answer_count"] = 1
        coverage["deferred_answer_pages"] = [row]
        coverage["manual_answer_exposure_count"] = 1
        coverage["manual_answer_exposure_pages"] = [row]
        coverage["pages"].append(row)
        with open(figure_path, "w", encoding="utf-8") as stream:
            json.dump(figure, stream)
        _refresh_visual_integrity(ws)
        self._record_core(ws)
        before = open(os.path.join(ws, "study_state.json"), "rb").read()
        result = _up(ws, ["complete-phase", "--status", "covered_unverified"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("提前暴露", result.stderr)
        self.assertEqual(open(os.path.join(ws, "study_state.json"), "rb").read(), before)

    def test_shared_prompt_answer_page_without_audited_crop_blocks_phase_completion(self):
        ws = _phase_evidence_ws()
        figure_path = os.path.join(ws, "references", "figure_page_index.json")
        with open(figure_path, encoding="utf-8") as stream:
            figure = json.load(stream)
        row = {"wiki_file": "ch1.md", "source_file": "course.pdf", "page": 6,
               "status": "shared_prompt_answer",
               "blocker": "audited_question_side_crop_required"}
        coverage = figure["wiki_visual_coverage"]
        coverage["shared_prompt_answer_count"] = 1
        coverage["shared_prompt_answer_pages"] = [row]
        coverage["shared_prompt_answer_blocker_count"] = 1
        coverage["shared_prompt_answer_blocker_pages"] = [row]
        coverage["pages"].append(row)
        with open(figure_path, "w", encoding="utf-8") as stream:
            json.dump(figure, stream)
        _refresh_visual_integrity(ws)
        self._record_core(ws)
        before = open(os.path.join(ws, "study_state.json"), "rb").read()
        result = _up(ws, ["complete-phase", "--status", "covered_unverified"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("共享整页", result.stderr)
        self.assertEqual(open(os.path.join(ws, "study_state.json"), "rb").read(), before)

    def test_complete_phase_only_current_and_next_phase_must_be_immediate(self):
        ws = _phase_evidence_ws()
        r = _up(ws, ["complete-phase", "--phase", "2", "--status", "covered_unverified"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("current_phase", r.stderr)
        self._record_core(ws)
        before = open(os.path.join(ws, "study_state.json"), "rb").read()
        r = _up(ws, ["complete-phase", "--phase", "1", "--status", "covered_unverified",
                     "--next-phase", "3"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("紧接", r.stderr)
        self.assertEqual(open(os.path.join(ws, "study_state.json"), "rb").read(), before)
        r = _up(ws, ["complete-phase", "--phase", "1", "--status", "covered_unverified",
                     "--next-phase", "2"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_state(ws)["current_phase"], 2)

    def test_phase_one_cannot_complete_with_phase_two_evidence_bundle(self):
        ws = _phase_evidence_ws()
        st_path = os.path.join(ws, "study_state.json")
        st = _state(ws)
        st["phase_evidence"] = {"1": {
            "wiki": ["references/wiki/ch2.md"],
            "visual": ["references/figure_page_index.json"],
            "teaching_examples": ["ex2"],
            "notebook": ["notebook/ch02.md#ex2-例题二"],
            "checkpoint": [{"id": "q3", "outcome": "passed"},
                           {"id": "q4", "outcome": "wrong"}]}}
        with open(st_path, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        before = open(st_path, "rb").read()
        r = _up(ws, ["complete-phase", "--phase", "1", "--status", "verified"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("study_plan.md", r.stderr)
        self.assertEqual(open(st_path, "rb").read(), before)

    def test_explicit_no_questions_preference_caps_phase_at_covered_unverified(self):
        ws = _phase_evidence_ws()
        self.assertEqual(_up(ws, ["set", "--pref", "no_questions=true"]).returncode, 0)
        self._record_core(ws)
        for qid, outcome in (("q1", "passed"), ("q2", "wrong")):
            r = _up(ws, ["record-phase-evidence", "--phase", "1", "--kind", "checkpoint",
                         "--ref", qid, "--outcome", outcome])
            self.assertEqual(r.returncode, 0, r.stderr)
        before = open(os.path.join(ws, "study_state.json"), "rb").read()
        r = _up(ws, ["complete-phase", "--phase", "1", "--status", "verified"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no_questions", r.stderr)
        self.assertEqual(open(os.path.join(ws, "study_state.json"), "rb").read(), before)
        r = _up(ws, ["complete-phase", "--phase", "1", "--status", "covered_unverified"])
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_record_evidence_rejects_workspace_escape_without_write(self):
        ws = _phase_evidence_ws()
        outside = os.path.join(os.path.dirname(ws), "outside.md")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("outside")
        before = open(os.path.join(ws, "study_state.json"), "rb").read()
        r = _up(ws, ["record-phase-evidence", "--phase", "1", "--kind", "wiki",
                     "--ref", "../outside.md"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("路径", r.stderr)
        self.assertEqual(open(os.path.join(ws, "study_state.json"), "rb").read(), before)

    def test_old_state_without_phase_evidence_remains_readable(self):
        ws = _mk_ws(tempfile.mkdtemp())
        self.assertEqual(_up(ws, ["init"]).returncode, 0)
        st = _state(ws)
        st.pop("phase_evidence", None)
        json.dump(st, open(os.path.join(ws, "study_state.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
        r = _up(ws, ["show"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["phase_evidence"], {})


class Mutations(unittest.TestCase):
    def _ready(self):
        ws = _mk_ws(tempfile.mkdtemp())
        _up(ws, ["init"])
        return ws

    def test_mutation_on_malformed_state_fails_loud(self):
        ws = self._ready()
        st = _state(ws)
        st["mistake_archive"] = 1                                 # 手改/半写坏形态
        json.dump(st, open(os.path.join(ws, "study_state.json"), "w", encoding="utf-8"))
        r = _up(ws, ["add-mistake", "--note", "x"])
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)                   # fail-loud _die，不是 Python 崩栈
        self.assertIn("损坏", r.stderr)

    # ---- A6：3 学习模式 × 4 时间宽裕度 + 知识点窗口 ----
    def test_a6_old_mode_migration_on_set(self):
        ws = self._ready()
        r = _up(ws, ["set", "--mode", "panic"])                    # 旧模式 → 迁移 + 警告
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("已废弃", r.stderr)                          # fail-loud 警告，不静默改写
        st = _state(ws)
        self.assertEqual(st["mode"], "from_scratch")              # v4：state 存代号
        self.assertEqual(st["time_budget"], "le1d")               # panic 迁移带出当天档
        # sprint → 查缺补漏 + 1-3天：换旧模式必须把上一次迁移带出的 ≤1天 刷成 1-3天（Codex R1-XN），
        # 否则节奏判定会卡在错误的紧迫档
        _up(ws, ["set", "--mode", "sprint"])
        st2 = _state(ws)
        self.assertEqual(st2["mode"], "fill_gaps")
        self.assertEqual(st2["time_budget"], "d1_3")

    def test_a6_migration_does_not_override_explicit_time_budget(self):
        ws = self._ready()
        r = _up(ws, ["set", "--mode", "panic", "--time-budget", "3-7天"])
        self.assertEqual(r.returncode, 0, r.stderr)
        st = _state(ws)
        self.assertEqual(st["mode"], "from_scratch")
        self.assertEqual(st["time_budget"], "d3_7")               # 显式 --time-budget 不被迁移带出值覆盖

    def test_a6_time_budget_alias_normalized(self):
        ws = self._ready()
        _up(ws, ["set", "--mode", "查缺补漏", "--time-budget", "一周内"])
        self.assertEqual(_state(ws)["time_budget"], "d3_7")       # 宽松别名归一到 canonical 代号档

    def test_a6_unknown_mode_kept_with_warning(self):
        ws = self._ready()
        r = _up(ws, ["set", "--mode", "随便讲讲"])                 # 非标准值：保留原值 + 警告，绝不静默改写
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("非标准", r.stderr)
        self.assertEqual(_state(ws)["mode"], "随便讲讲")

    # ---- output-resource mode: v3-compatible chat default vs opt-in visual artifacts ----
    def test_artifact_mode_defaults_to_chat_for_new_and_legacy_state(self):
        ws = self._ready()
        st = _state(ws)
        self.assertEqual(st["artifact_mode"], "chat")
        self.assertEqual(i18n.workspace_artifact_mode(st), "chat")
        md = open(os.path.join(ws, "study_progress.md"), encoding="utf-8").read()
        self.assertIn("**输出资源模式**：对话省额", md)

        st.pop("artifact_mode")                         # simulate a v3/early-v4 state
        json.dump(st, open(os.path.join(ws, "study_state.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
        shown = _up(ws, ["show"])
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(json.loads(shown.stdout)["artifact_mode"], "chat")

    def test_artifact_mode_aliases_normalize_without_subscription_inference(self):
        ws = self._ready()
        for alias, canon in (("视觉教材", "visual"), ("PDF", "visual"),
                             ("visual study guide", "visual"), ("不在乎 token", "visual"),
                             ("token-insensitive", "visual"), ("对话省额", "chat"),
                             ("CHAT-ONLY", "chat"), ("省token", "chat")):
            r = _up(ws, ["set", "--artifact-mode", alias])
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(_state(ws)["artifact_mode"], canon, alias)
        # The state model has no subscription field and never derives the choice from one.
        self.assertNotIn("subscription", _state(ws))

    def test_artifact_mode_unknown_is_preserved_but_effective_mode_fails_safe(self):
        ws = self._ready()
        r = _up(ws, ["set", "--artifact-mode", "ultra-render"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("非标准输出资源模式", r.stderr)
        st = _state(ws)
        self.assertEqual(st["artifact_mode"], "ultra-render")
        self.assertEqual(i18n.workspace_artifact_mode(st), "chat")
        self.assertEqual(i18n.workspace_artifact_mode(None), "chat")

    def test_artifact_mode_visual_roundtrips_through_generated_md(self):
        ws = self._ready()
        r = _up(ws, ["set", "--artifact-mode", "visual"])
        self.assertEqual(r.returncode, 0, r.stderr)
        md = open(os.path.join(ws, "study_progress.md"), encoding="utf-8").read()
        self.assertIn("**输出资源模式**：视觉教材", md)
        rebuilt = _up(ws, ["init", "--force"])
        self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
        self.assertEqual(_state(ws)["artifact_mode"], "visual")

    def test_artifact_mode_legacy_display_value_migrates_on_official_save(self):
        ws = self._ready()
        st = _state(ws)
        st["artifact_mode"] = "视觉教材"                # pre-canonical/manual early state
        json.dump(st, open(os.path.join(ws, "study_state.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
        r = _up(ws, ["render"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_state(ws)["artifact_mode"], "visual")
        self.assertEqual(i18n.display("artifact", "chat", "en"), "chat-only")
        self.assertEqual(i18n.display("artifact", "visual", "en"), "visual study guide")

    def test_artifact_mode_empty_explicitly_restores_safe_chat_default(self):
        ws = self._ready()
        self.assertEqual(_up(ws, ["set", "--artifact-mode", "visual"]).returncode, 0)
        r = _up(ws, ["set", "--artifact-mode", ""])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_state(ws)["artifact_mode"], "chat")

    def test_artifact_mode_bad_state_type_fails_loud(self):
        ws = self._ready()
        st = _state(ws)
        st["artifact_mode"] = ["visual"]
        json.dump(st, open(os.path.join(ws, "study_state.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
        r = _up(ws, ["render"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("artifact_mode 必须是字符串", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_a6_knowledge_window_add_and_status(self):
        ws = self._ready()
        _up(ws, ["set", "--mode", "查缺补漏", "--time-budget", "3-7天"])
        self.assertEqual(_up(ws, ["window-add", "--point", "栈的LIFO", "--chapter", "1"]).returncode, 0)
        self.assertEqual(_up(ws, ["window-add", "--point", "队列FIFO", "--chapter", "1",
                                  "--status", "窗口外"]).returncode, 0)
        # 同名点再 add 是状态迁移、不重复加行
        _up(ws, ["window-add", "--point", "栈的LIFO", "--status", "已实测"])
        win = _state(ws)["knowledge_window"]
        self.assertEqual(len(win), 2)
        by = {w["point"]: w["status"] for w in win}
        self.assertEqual(by["栈的LIFO"], "verified")              # v4：state 存代号（视图渲染 已实测）
        self.assertEqual(by["队列FIFO"], "out_window")
        # set-status 按名定位
        _up(ws, ["window-set-status", "--point", "队列FIFO", "--status", "在窗口"])
        self.assertEqual({w["point"]: w["status"] for w in _state(ws)["knowledge_window"]}["队列FIFO"], "in_window")
        # 进度面板渲染出窗口区
        md = open(os.path.join(ws, "study_progress.md"), encoding="utf-8").read()
        self.assertIn("知识点窗口", md)
        self.assertIn("栈的LIFO", md)

    def test_a6_window_bad_status_and_missing_point_fail_loud(self):
        ws = self._ready()
        r = _up(ws, ["window-add", "--point", "x", "--status", "乱写"])
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)
        r2 = _up(ws, ["window-set-status", "--point", "不存在", "--status", "在窗口"])
        self.assertNotEqual(r2.returncode, 0)
        self.assertNotIn("Traceback", r2.stderr)

    def test_a6_window_add_ambiguous_multichapter_fail_loud(self):
        # window-add 同名点分布多章、不带 --chapter 也会静默只改第一条 → 与 set-status 同守卫 fail-loud（Codex R2-IAQ）
        ws = self._ready()
        _up(ws, ["window-add", "--point", "模板", "--chapter", "2"])
        _up(ws, ["window-add", "--point", "模板", "--chapter", "5"])
        r = _up(ws, ["window-add", "--point", "模板", "--status", "已实测"])   # 无 --chapter
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("多个章节", r.stderr)
        # 状态没被偷偷改
        self.assertTrue(all(w["status"] == "in_window" for w in _state(ws)["knowledge_window"] if w["point"] == "模板"))
        # 带 --chapter 只改该章
        r2 = _up(ws, ["window-add", "--point", "模板", "--chapter", "2", "--status", "已实测"])
        self.assertEqual(r2.returncode, 0, r2.stderr)
        by = {str(w["chapter"]): w["status"] for w in _state(ws)["knowledge_window"] if w["point"] == "模板"}
        self.assertEqual((by["2"], by["5"]), ("verified", "in_window"))

    def test_a6_window_set_status_ambiguous_multichapter_fail_loud(self):
        # 同名点分布在多章：不带 --chapter 会一次改错所有章 → 必须 fail-loud 要求精确定位（Codex R1-XU）
        ws = self._ready()
        _up(ws, ["window-add", "--point", "模板", "--chapter", "2"])
        _up(ws, ["window-add", "--point", "模板", "--chapter", "5"])
        r = _up(ws, ["window-set-status", "--point", "模板", "--status", "已实测"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("多个章节", r.stderr)
        # 带 --chapter 只改该章那条
        r2 = _up(ws, ["window-set-status", "--point", "模板", "--chapter", "2", "--status", "已实测"])
        self.assertEqual(r2.returncode, 0, r2.stderr)
        by = {str(w["chapter"]): w["status"] for w in _state(ws)["knowledge_window"] if w["point"] == "模板"}
        self.assertEqual(by["2"], "verified")
        self.assertEqual(by["5"], "in_window")                     # 第5章的同名点不受影响

    def test_a6_window_survives_init_force_roundtrip(self):
        # init --force 从 md 重新迁移时，知识点窗口必须无损带回——否则窗口/已实测追踪被静默丢
        ws = self._ready()
        _up(ws, ["set", "--mode", "查缺补漏", "--time-budget", "3-7天"])
        _up(ws, ["window-add", "--point", "栈的LIFO", "--chapter", "1"])
        _up(ws, ["window-add", "--point", "队列FIFO", "--chapter", "1", "--status", "窗口外"])
        _up(ws, ["window-set-status", "--point", "栈的LIFO", "--status", "已实测"])
        before = [(w["point"], str(w.get("chapter")), w["status"]) for w in _state(ws)["knowledge_window"]]
        r = _up(ws, ["init", "--force"])
        self.assertEqual(r.returncode, 0, r.stderr)
        after = [(w["point"], str(w.get("chapter")), w["status"]) for w in _state(ws)["knowledge_window"]]
        self.assertEqual(before, after, "init --force 丢了知识点窗口行")

    def test_a6_window_add_idempotent_backfill(self):
        # 先松登记（无章节）再补章节 → 回填到同一条，不产生 null-章节孤儿重复行
        ws = self._ready()
        _up(ws, ["window-add", "--point", "红黑树"])
        _up(ws, ["window-add", "--point", "红黑树", "--chapter", "7"])
        rows = [w for w in _state(ws)["knowledge_window"] if w["point"] == "红黑树"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(str(rows[0]["chapter"]), "7")
        # 同名但不同章 = 真正不同的点，保留两行
        _up(ws, ["window-add", "--point", "模板", "--chapter", "2"])
        _up(ws, ["window-add", "--point", "模板", "--chapter", "5"])
        self.assertEqual(len([w for w in _state(ws)["knowledge_window"] if w["point"] == "模板"]), 2)

    def test_a6_init_surfaces_normalization_warning(self):
        # 迁移进来的非标准模式：值保留 + 走 stderr 警告（与 cmd_set 一致，不静默）
        md = ("# 进度\n\n## ⏱️ 当前复习断点\n* **当前进行阶段**：阶段 1\n"
              "* **范围/模式**：混合题池 ｜ panic ｜ 时间预算 未设定\n\n## ❌ 错题档案记录\n（暂无）\n")
        ws = _mk_ws(tempfile.mkdtemp(), md=md)
        r = _up(ws, ["init"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("已废弃", r.stderr)                          # panic 迁移警告在 init 也冒出来
        st = _state(ws)
        self.assertEqual(st["mode"], "from_scratch")
        self.assertEqual(st["time_budget"], "le1d")

    def test_missing_optional_fields_tolerated(self):
        ws = self._ready()
        st = _state(ws)
        for f in ("phase_checklist", "confusion_log"):            # 旧 schema 缺字段 → 按空列表补齐
            st.pop(f, None)
        json.dump(st, open(os.path.join(ws, "study_state.json"), "w", encoding="utf-8"))
        r = _up(ws, ["add-confusion", "--note", "取模没搞懂"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(_state(ws)["confusion_log"]), 1)

    # ---- regression guards for Codex round-4 (9 findings) ----

    def test_add_confusion_uses_review_status(self):
        ws = self._ready()
        _up(ws, ["add-confusion", "--note", "取模没搞懂"])
        _up(ws, ["add-mistake", "--note", "Venn 判断错"])
        st = _state(ws)
        self.assertEqual(st["confusion_log"][-1]["status"], "to_revisit")   # 疑难走 待回顾→已回顾 契约（存代号）
        self.assertEqual(st["mistake_archive"][-1]["status"], "to_review")

    def test_migrated_confusion_bullet_gets_review_status(self):
        ws = _mk_ws(tempfile.mkdtemp())
        _up(ws, ["init"])
        self.assertEqual(_state(ws)["confusion_log"][0]["status"], "to_revisit")

    def test_render_rejects_non_string_note(self):
        ws = self._ready()
        st = _state(ws)
        st["mistake_archive"] = [{"id": "q1", "note": 5, "status": "待复盘"}]
        json.dump(st, open(os.path.join(ws, "study_state.json"), "w", encoding="utf-8"))
        r = _up(ws, ["render"])
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)                   # fail-loud，不是渲染中途崩栈
        self.assertIn("损坏", r.stderr)

    # ---- regression guards for Codex round-5 (5 findings) ----

    def test_multiline_note_stays_single_table_row(self):
        ws = self._ready()
        r = _up(ws, ["add-mistake", "--id", "q9", "--note", "第一行原因\n第二行补充"])
        self.assertEqual(r.returncode, 0, r.stderr)
        md = open(os.path.join(ws, "study_progress.md"), encoding="utf-8").read()
        row_lines = [ln for ln in md.splitlines() if "q9" in ln]
        self.assertEqual(len(row_lines), 1)                       # 换行归一成空格，行结构不被拆散
        self.assertIn("第一行原因 第二行补充", row_lines[0])
        self.assertNotIn("\n第二行补充", md)

    def test_init_rejects_invalid_phase_zero(self):
        ws = _mk_ws(tempfile.mkdtemp(), md="当前阶段：0\n## 错题本\n（暂无）\n")
        r = _up(ws, ["init"])
        self.assertEqual(r.returncode, 1)                         # 迁移绝不产出损坏 state
        self.assertIn("非法", r.stderr)
        self.assertFalse(os.path.isfile(os.path.join(ws, "study_state.json")))

    # ---- regression guards for Codex round-6 (5 findings) ----

    def test_symlinked_tmp_rejected_before_write(self):
        ws = self._ready()
        outside = os.path.join(tempfile.mkdtemp(), "victim.txt")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("外部文件不许被截断")
        try:
            os.symlink(outside, os.path.join(ws, "study_state.json.tmp"))
        except (OSError, NotImplementedError):
            self.skipTest("no symlink privilege")
        r = _up(ws, ["set", "--phase", "1", "--mode", "x"])
        self.assertEqual(r.returncode, 1)
        self.assertIn("符号链接", r.stderr)                        # 拒绝写入，不跟随链接
        self.assertEqual(open(outside, encoding="utf-8").read(), "外部文件不许被截断")

    def test_stale_plain_tmp_cleaned_and_overwritten(self):
        ws = self._ready()
        with open(os.path.join(ws, "study_state.json.tmp"), "w", encoding="utf-8") as f:
            f.write("上次崩溃的残留")
        r = _up(ws, ["set", "--phase", "2"])
        self.assertEqual(r.returncode, 0, r.stderr)               # 普通残留 tmp 清掉重建，不误伤
        self.assertEqual(_state(ws)["current_phase"], 2)
        self.assertFalse([f for f in os.listdir(ws) if f.endswith(".tmp")])

    def test_migration_preserves_scope_and_mode(self):
        md = ("# 🎯 复习进度\n\n## ⏱️ 当前复习断点\n* **当前进行阶段**：阶段 2\n"
              "* **范围/模式**：homework-only ｜ 查缺补漏 ｜ 时间预算 3天\n\n"
              "## ❌ 错题档案记录\n（暂无）\n")
        ws = _mk_ws(tempfile.mkdtemp(), md=md)
        r = _up(ws, ["init"])
        self.assertEqual(r.returncode, 0, r.stderr)
        st = _state(ws)
        self.assertEqual(st["scope"], "homework-only")            # A2 范围偏好不因迁移被静默放宽
        self.assertEqual(st["mode"], "fill_gaps")                 # v4：state 存代号
        self.assertEqual(st["time_budget"], "3天")                # 非标准串原样保留（透传语义）
        ws2 = _mk_ws(tempfile.mkdtemp())                          # 默认「混合题池｜未设定」→ 保持 None
        _up(ws2, ["init"])
        self.assertIsNone(_state(ws2).get("scope"))

    def test_real_row_containing_placeholder_text_kept(self):
        md = LEGACY_MD.replace("| [#q1] | 第1章 | 栈顺序 | 混淆LIFO | 未复习 |",
                               "| [#q1] | 第1章 | 空集（暂无）元素时的处理 | 混淆边界 | 未复习 |")
        ws = _mk_ws(tempfile.mkdtemp(), md=md)
        _up(ws, ["init"])
        st = _state(ws)
        self.assertEqual(len(st["mistake_archive"]), 1)           # 含（暂无）字样的真实行不被当占位符丢
        self.assertIn("空集（暂无）元素", st["mistake_archive"][0]["note"])
        md2 = LEGACY_MD + "\n| （暂无） | - | - | - |\n"
        ws2 = _mk_ws(tempfile.mkdtemp(), md=md2)
        _up(ws2, ["init"])
        self.assertEqual(len(_state(ws2)["mistake_archive"]), 1)  # 纯占位行仍被跳过


    # ---- regression guards for Codex round-7 (6 findings) ----

    def test_set_phase_must_be_in_plan(self):
        ws = self._ready()
        with open(os.path.join(ws, "study_plan.md"), "w", encoding="utf-8") as f:
            f.write("# 计划\n## 阶段1：栈\n## 第2阶段：树\n")
        r = _up(ws, ["set", "--phase", "99"])
        self.assertNotEqual(r.returncode, 0)                      # 官方路径写之前就拒绝
        self.assertIn("不在 study_plan.md", r.stderr)
        self.assertNotEqual(_state(ws)["current_phase"], 99)      # 事实源未被污染
        self.assertEqual(_up(ws, ["set", "--phase", "2"]).returncode, 0)   # 第N阶段写法也认

    def test_symlinked_state_rejected_by_updater(self):
        ws = _mk_ws(tempfile.mkdtemp(), md=None)
        outside = os.path.join(tempfile.mkdtemp(), "evil_state.json")
        json.dump({"version": 1, "current_phase": 1, "mistake_archive": [], "confusion_log": []},
                  open(outside, "w", encoding="utf-8"))
        try:
            os.symlink(outside, os.path.join(ws, "study_state.json"))
        except (OSError, NotImplementedError):
            self.skipTest("no symlink privilege")
        r = _up(ws, ["show"])
        self.assertEqual(r.returncode, 1)
        self.assertIn("符号链接", r.stderr)                        # 读取前 fail-loud，不采纳外部事实源


    # ---- regression guards for Codex round-8 (8 findings) ----

    def test_init_rejects_phase_outside_plan(self):
        ws = _mk_ws(tempfile.mkdtemp(), md="当前阶段：99\n## 错题本\n（暂无）\n")
        with open(os.path.join(ws, "study_plan.md"), "w", encoding="utf-8") as f:
            f.write("# 计划\n## 阶段1：栈\n")
        r = _up(ws, ["init"])
        self.assertEqual(r.returncode, 1)                         # 迁移不产出恢复不进去的断点
        self.assertIn("不在 study_plan.md", r.stderr)
        self.assertFalse(os.path.isfile(os.path.join(ws, "study_state.json")))

    def test_skills_route_reads_and_archives_through_state(self):
        # 读侧/归档侧指令也要对齐事实源——不能只有 Boundaries 一句
        review = open(os.path.join(ROOT, "skills", "exam-review", "SKILL.md"), encoding="utf-8").read()
        self.assertIn("`study_state.json`'s `mistake_archive`", review)
        quiz = open(os.path.join(ROOT, "skills", "exam-quiz", "SKILL.md"), encoding="utf-8").read()
        self.assertIn("add-mistake --id", quiz)
        tutor = open(os.path.join(ROOT, "skills", "exam-tutor", "SKILL.md"), encoding="utf-8").read()
        self.assertIn("set --phase <N>", tutor)
        tracker = open(os.path.join(ROOT, "skills", "confusion-tracker", "SKILL.md"), encoding="utf-8").read()
        self.assertIn("through `update_progress.py add-confusion`", tracker)
        self.assertIn("initialize state first when Python works", tracker)


    # ---- regression guards for Codex round-9 (5 findings) ----

    def test_init_preserves_preferences_section(self):
        ws = self._ready()
        _up(ws, ["set", "--pref", "讲解风格=七步模板", "--pref", "口吻=简洁"])
        r = _up(ws, ["init", "--force"])                          # 官方推荐的恢复路径
        self.assertEqual(r.returncode, 0, r.stderr)
        st = _state(ws)
        self.assertEqual(st["preferences"].get("讲解风格"), "七步模板")   # 偏好不因重建被静默丢
        self.assertEqual(st["preferences"].get("口吻"), "简洁")

    def test_state_dir_rejected_before_any_write(self):
        ws = self._ready()
        before_md = open(os.path.join(ws, "study_progress.md"), encoding="utf-8").read()
        os.remove(os.path.join(ws, "study_state.json"))
        os.makedirs(os.path.join(ws, "study_state.json"))         # state 路径变目录
        r = _up(ws, ["init", "--force"])
        self.assertEqual(r.returncode, 1)
        self.assertIn("拒绝写入", r.stderr)
        self.assertEqual(open(os.path.join(ws, "study_progress.md"), encoding="utf-8").read(),
                         before_md)                               # 生成视图未被先行打掉

    def test_stale_phase_blocks_mutations_but_set_repairs(self):
        ws = self._ready()
        with open(os.path.join(ws, "study_plan.md"), "w", encoding="utf-8") as f:
            f.write("# 计划\n## 阶段1：栈\n## 阶段2：树\n")
        st = _state(ws)
        st["current_phase"] = 9                                   # 手改/计划回滚后的陈旧断点
        json.dump(st, open(os.path.join(ws, "study_state.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
        r = _up(ws, ["add-mistake", "--note", "x"])
        self.assertEqual(r.returncode, 1)                         # 其他变更拒绝再保存坏断点
        self.assertIn("已不在 study_plan.md", r.stderr)
        r2 = _up(ws, ["set", "--phase", "2"])                     # 修复路径豁免并自校验新值
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertEqual(_up(ws, ["add-mistake", "--note", "x"]).returncode, 0)


    # ---- regression guards for Codex round-10 (5 findings) ----

    def test_language_aliases_normalize(self):
        # A8b：--language 别名归一到 canonical 代号（zh/en/bilingual）；ASCII 不区分大小写
        ws = self._ready()
        for alias, canon in (("zh", "zh"), ("EN", "en"), ("english", "en"),
                             ("bilingual", "bilingual"), ("中英", "bilingual"), ("简体中文", "zh")):
            r = _up(ws, ["set", "--language", alias])
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(_state(ws)["language"], canon, alias)

    def test_language_unknown_preserved_with_warning(self):
        # 未知值原样保留 + stderr 告警（绝不静默改写）
        ws = self._ready()
        r = _up(ws, ["set", "--language", "Klingon"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_state(ws)["language"], "Klingon")
        self.assertIn("非标准语言偏好", r.stderr)

    def test_language_clear_and_one_call_set(self):
        # 一次 set 同时立三样（A6+A8b 合并首问的持久化形态）；--language "" 清除
        ws = self._ready()
        r = _up(ws, ["set", "--mode", "查缺补漏", "--time-budget", "1-3天", "--language", "English"])
        self.assertEqual(r.returncode, 0, r.stderr)
        st = _state(ws)
        self.assertEqual((st["mode"], st["time_budget"], st["language"]),
                         ("fill_gaps", "d1_3", "en"))             # v4：state 存代号（视图仍渲染显示词）
        md = open(os.path.join(ws, "study_progress.md"), encoding="utf-8").read()
        self.assertIn("语言偏好", md)
        self.assertIn("English", md)
        r2 = _up(ws, ["set", "--language", ""])
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIsNone(_state(ws)["language"])

    def test_language_alias_in_md_normalized_on_init(self):
        # 手写 md 里的别名语言值在 init --force 迁移时归一
        ws = self._ready()
        _up(ws, ["set", "--language", "English"])
        mdp = os.path.join(ws, "study_progress.md")
        md = open(mdp, encoding="utf-8").read()
        self.assertIn("English", md)
        with open(mdp, "w", encoding="utf-8") as f:
            f.write(md.replace("**语言偏好**：English", "**语言偏好**：zh-CN"))
        r = _up(ws, ["init", "--force"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_state(ws)["language"], "zh")            # zh-CN → canonical 代号 zh

    def test_language_survives_forced_rebuild(self):
        ws = self._ready()
        _up(ws, ["set", "--language", "English"])
        r = _up(ws, ["init", "--force"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_state(ws)["language"], "en")            # 语言偏好经生成视图迁回（存代号）

    def test_forced_rebuild_keeps_idless_rows_idless(self):
        ws = self._ready()
        _up(ws, ["add-confusion", "--note", "无 id 的疑难甲"])
        _up(ws, ["add-confusion", "--note", "无 id 的疑难乙"])
        r = _up(ws, ["init", "--force"])
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = [x for x in _state(ws)["confusion_log"] if "无 id" in x["note"]]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(x["id"] is None for x in rows))       # 渲染的 '-' 占位不回灌成 id


    # ---- regression guards for Codex round-11 (4 findings) ----

    def test_init_rejects_symlinked_md(self):
        ws = _mk_ws(tempfile.mkdtemp(), md=None)
        outside = os.path.join(tempfile.mkdtemp(), "evil.md")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("当前阶段：1\n")
        try:
            os.symlink(outside, os.path.join(ws, "study_progress.md"))
        except (OSError, NotImplementedError):
            self.skipTest("no symlink privilege")
        r = _up(ws, ["init"])
        self.assertEqual(r.returncode, 1)
        self.assertIn("符号链接", r.stderr)                        # 外部文件不迁进事实源
        self.assertFalse(os.path.isfile(os.path.join(ws, "study_state.json")))


    # ---- regression guards for Codex round-12 (4 findings) ----

    def test_blank_init_seeds_phase_from_plan(self):
        ws = _mk_ws(tempfile.mkdtemp(), md=None)
        with open(os.path.join(ws, "study_plan.md"), "w", encoding="utf-8") as f:
            f.write("# 计划\n## 阶段2：树\n## 阶段3：图\n")      # 计划不含阶段1
        r = _up(ws, ["init"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_state(ws)["current_phase"], 2)          # 空白初始化落在计划内（min）
        self.assertEqual(_up(ws, ["add-mistake", "--note", "x"]).returncode, 0)   # 后续更新不被卡死

    def test_unreadable_plan_fails_loud(self):
        ws = self._ready()
        with open(os.path.join(ws, "study_plan.md"), "wb") as f:
            f.write("阶段：乱码".encode("gbk"))                   # 计划存在但非 UTF-8
        r = _up(ws, ["set", "--phase", "2"])
        self.assertEqual(r.returncode, 1)
        self.assertIn("无法读取", r.stderr)                        # 不静默禁用阶段守卫


    def test_set_updates_state_and_md(self):
        ws = self._ready()
        r = _up(ws, ["set", "--phase", "5", "--scope", "homework-only", "--mode", "查缺补漏",
                     "--pref", "讲解风格=七步模板"])
        self.assertEqual(r.returncode, 0, r.stderr)
        st = _state(ws)
        self.assertEqual(st["current_phase"], 5)
        self.assertEqual(st["scope"], "homework-only")
        self.assertEqual(st["preferences"]["讲解风格"], "七步模板")
        md = open(os.path.join(ws, "study_progress.md"), encoding="utf-8").read()
        self.assertIn("阶段 5", md)
        self.assertIn("homework-only", md)                        # 进度面板可见 scope/mode

    def test_a5_teaching_template_pref_roundtrip(self):
        # A5 文档口径：讲解模板变体作为偏好存 preferences（与 --mode 分离），进度面板 ⚙️ 偏好区可见
        ws = self._ready()
        r = _up(ws, ["set", "--pref", "讲解模板=七步精讲"])
        self.assertEqual(r.returncode, 0, r.stderr)
        st = _state(ws)
        self.assertEqual(st["preferences"]["讲解模板"], "七步精讲")
        self.assertNotEqual(st.get("mode"), "七步精讲")            # 偏好不污染模式
        md = open(os.path.join(ws, "study_progress.md"), encoding="utf-8").read()
        self.assertIn("偏好", md)
        self.assertIn("讲解模板", md)
        self.assertIn("七步精讲", md)                              # 面板显示当前讲解模板偏好
        r2 = _up(ws, ["set", "--pref", "讲解模板=文科变体"])       # 随时可改
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertEqual(_state(ws)["preferences"]["讲解模板"], "文科变体")

    def test_add_rows_persist_and_render(self):
        ws = self._ready()
        _up(ws, ["add-mistake", "--id", "hw_hw1_3", "--chapter", "2", "--note", "Venn 阴影判断错"])
        _up(ws, ["add-confusion", "--chapter", "1", "--note", "取模边界"])
        st = _state(ws)
        self.assertEqual(len(st["mistake_archive"]), 2)           # 迁移 1 条 + 新增 1 条
        self.assertEqual(len(st["confusion_log"]), 2)
        md = open(os.path.join(ws, "study_progress.md"), encoding="utf-8").read()
        self.assertIn("[#hw_hw1_3]", md)
        self.assertIn("取模边界", md)

    def test_set_without_state_fails(self):
        ws = _mk_ws(tempfile.mkdtemp())
        r = _up(ws, ["set", "--phase", "2"])
        self.assertEqual(r.returncode, 2)
        self.assertIn("init", r.stderr)

    def test_render_repairs_hand_edited_md(self):
        ws = self._ready()
        with open(os.path.join(ws, "study_progress.md"), "w", encoding="utf-8") as f:
            f.write("被手改坏的文件")
        r = _up(ws, ["render"])
        self.assertEqual(r.returncode, 0)
        md = open(os.path.join(ws, "study_progress.md"), encoding="utf-8").read()
        self.assertIn("当前进行阶段", md)                          # 从 state 重建

    def test_empty_note_rejected(self):
        ws = self._ready()
        r = _up(ws, ["add-mistake", "--note", "   "])
        self.assertEqual(r.returncode, 2)


    # ---- regression guards for Codex round-1 (P1 + 3 P2) ----

    def test_set_status_by_id_and_index(self):
        ws = self._ready()
        _up(ws, ["add-mistake", "--id", "q9", "--note", "第一条"])
        r = _up(ws, ["set-mistake-status", "--id", "q9", "--status", "已复盘"])
        self.assertEqual(r.returncode, 0, r.stderr)
        st = _state(ws)
        row = next(x for x in st["mistake_archive"] if x.get("id") == "q9")
        self.assertEqual(row["status"], "reviewed")                # P1：官方状态更新路径（state 存代号）
        md = open(os.path.join(ws, "study_progress.md"), encoding="utf-8").read()
        self.assertIn("已复盘", md)                                # 生成视图仍渲染中文显示词
        r2 = _up(ws, ["set-confusion-status", "--index", "1", "--status", "已解决"])
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertEqual(_state(ws)["confusion_log"][0]["status"], "resolved")

    def test_set_status_missing_target_fails(self):
        ws = self._ready()
        self.assertEqual(_up(ws, ["set-mistake-status", "--id", "nope", "--status", "x"]).returncode, 2)
        self.assertEqual(_up(ws, ["set-mistake-status", "--status", "x"]).returncode, 2)
        self.assertEqual(_up(ws, ["set-mistake-status", "--index", "99", "--status", "x"]).returncode, 2)

    def test_failed_write_leaves_no_tmp_and_truth_intact(self):
        ws = self._ready()
        before = _state(ws)
        md_dir = os.path.join(ws, "study_progress.md")
        os.remove(md_dir)
        os.makedirs(md_dir)                                       # md 路径变目录 → 写入必失败
        r = _up(ws, ["set", "--phase", "9"])
        self.assertEqual(r.returncode, 1)
        self.assertIn("拒绝写入", r.stderr)                        # round-10 起：暂存任何 tmp 前就拦截
        self.assertEqual(_state(ws)["current_phase"], before["current_phase"])   # 事实源未超前
        self.assertFalse([f for f in os.listdir(ws) if f.endswith(".tmp")])      # 无 tmp 残留
        self.assertTrue(os.path.isdir(os.path.join(ws, "study_progress.md")))    # 生成视图目录未被打掉

class ValidatorSchema(unittest.TestCase):
    def _full_ws(self, state_patch=None):
        tmp = tempfile.mkdtemp()
        ws = os.path.join(tmp, "ws")
        os.makedirs(os.path.join(ws, "references", "wiki"))
        open(os.path.join(ws, "references", "wiki", "ch1.md"), "w", encoding="utf-8").write("# ch1\n内容")
        open(os.path.join(ws, "study_plan.md"), "w", encoding="utf-8").write(
            "# 计划\n## 阶段1：栈（references/wiki/ch1.md）\n")
        open(os.path.join(ws, "study_progress.md"), "w", encoding="utf-8").write(
            "当前阶段：1\n## 错题本\n（暂无）\n## 疑难点\n（暂无）\n")
        json.dump([{"id": "q1", "chapter": 1, "type": "subjective", "question": "x?", "answer": "y",
                    "source": "material", "ai_generated": False}],
                  open(os.path.join(ws, "references", "quiz_bank.json"), "w", encoding="utf-8"))
        if state_patch is not None:
            st = {"version": 1, "current_phase": 1, "mistake_archive": [], "confusion_log": [],
                  "knowledge_window": [], "preferences": {}}
            st.update(state_patch)
            json.dump(st, open(os.path.join(ws, "study_state.json"), "w", encoding="utf-8"),
                      ensure_ascii=False)
        return ws

    def _validate(self, ws):
        return subprocess.run([sys.executable, os.path.join(SCRIPTS, "validate_workspace.py"), ws],
                              capture_output=True, text=True, encoding="utf-8")

    def test_state_phase_outside_english_plan_errors(self):
        ws = self._full_ws({"current_phase": 99})
        with open(os.path.join(ws, "study_plan.md"), "w", encoding="utf-8", newline=chr(10)) as f:
            f.write("# Plan" + chr(10) + "## Phase 1: Stack（references/wiki/ch1.md）" + chr(10)
                    + "## Phase 2: Queue" + chr(10))
        r = self._validate(ws)
        self.assertEqual(r.returncode, 1)                         # 英文计划也要挡住 99 号断点
        self.assertIn("不在 study_plan.md", r.stdout)

    @unittest.skipUnless(os.name == "posix" and getattr(os, "geteuid", lambda: 0)() != 0,
                         "chmod 0o000 只对非 root 的 POSIX 进程生效（root/容器 uid0 仍可读）")
    def test_unreadable_state_reports_not_crashes(self):
        ws = self._full_ws({"current_phase": 1})
        os.chmod(os.path.join(ws, "study_state.json"), 0o000)
        try:
            r = self._validate(ws)
        finally:
            os.chmod(os.path.join(ws, "study_state.json"), 0o644)
        self.assertEqual(r.returncode, 1)                         # 结构化报错而不是崩栈
        self.assertIn("无法读取", r.stdout)
        self.assertNotIn("Traceback", r.stderr)

    def test_zero_padded_plan_phase_accepted(self):
        ws = self._full_ws({"current_phase": 1})
        with open(os.path.join(ws, "study_plan.md"), "w", encoding="utf-8", newline=chr(10)) as f:
            f.write("# 计划" + chr(10) + "## 阶段01：栈（references/wiki/ch1.md）" + chr(10))
        r = self._validate(ws)
        self.assertNotIn("不在 study_plan.md", r.stdout)          # 「阶段01」规范化后配上 cp=1

    def test_knowledge_window_rows_without_note_accepted(self):
        ws = self._full_ws({"knowledge_window": [{"chapter": 1, "opened": True}]})
        r = self._validate(ws)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)    # 与更新器同 schema：不强求 note

    def test_good_state_passes(self):
        ws = self._full_ws({"mistake_archive": [{"id": "q1", "chapter": "1", "note": "x", "status": "待复盘"}]})
        r = self._validate(ws)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_no_state_still_valid(self):
        ws = self._full_ws(None)                                  # 无 Python 降级路径保持有效
        self.assertEqual(self._validate(ws).returncode, 0)

    def test_bad_state_types_fail(self):
        for patch in ({"current_phase": 0}, {"current_phase": "3"},
                      {"mistake_archive": ["字符串行"]},
                      {"confusion_log": [{"id": "x"}]},           # 缺 note
                      {"preferences": []}):
            ws = self._full_ws(patch)
            r = self._validate(ws)
            self.assertEqual(r.returncode, 1, "patch=%r 应报错\n%s" % (patch, r.stdout))

    def test_scalar_array_reports_without_crash(self):
        ws = self._full_ws({"mistake_archive": 1})
        r = self._validate(ws)
        self.assertEqual(r.returncode, 1)                         # 结构化报错，不是 TypeError 崩栈
        self.assertNotIn("Traceback", r.stderr)

    def test_symlinked_state_rejected(self):
        ws = self._full_ws(None)
        outside = os.path.join(tempfile.mkdtemp(), "evil.json")
        json.dump({"version": 1, "current_phase": 1}, open(outside, "w", encoding="utf-8"))
        link = os.path.join(ws, "study_state.json")
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            self.skipTest("no symlink privilege")
        r = self._validate(ws)
        self.assertEqual(r.returncode, 1)
        self.assertIn("符号链接", r.stdout)

    def test_phase_checklist_schema_validated(self):
        r = self._validate(self._full_ws({"phase_checklist": 1}))
        self.assertEqual(r.returncode, 1)                         # 标量不是打卡数组
        r = self._validate(self._full_ws({"phase_checklist": [{"text": "", "done": True}]}))
        self.assertEqual(r.returncode, 1)                         # 空 text 拒绝
        r = self._validate(self._full_ws({"phase_checklist": [{"text": "阶段 1：栈", "done": "yes"}]}))
        self.assertEqual(r.returncode, 1)                         # done 必须布尔
        r = self._validate(self._full_ws({"phase_checklist": [{"text": "阶段 1：栈", "done": False}]}))
        self.assertEqual(r.returncode, 0, r.stdout)               # 合法形态通过

    def test_state_phase_check_matches_both_plan_wordings(self):
        ws = self._full_ws({"current_phase": 99})
        with open(os.path.join(ws, "study_plan.md"), "w", encoding="utf-8") as f:
            f.write("# 计划\n## 第1阶段：栈（references/wiki/ch1.md）\n")
        r = self._validate(ws)
        self.assertEqual(r.returncode, 1)                         # 「第N阶段」写法也参与校验，99 照样拦
        self.assertIn("不在 study_plan.md", r.stdout)
        ws2 = self._full_ws({"current_phase": 2})
        with open(os.path.join(ws2, "study_plan.md"), "w", encoding="utf-8") as f:
            f.write("# 计划\n## 第1阶段：栈（references/wiki/ch1.md）\n## 第2阶段：树（references/wiki/ch1.md）\n")
        with open(os.path.join(ws2, "study_progress.md"), "w", encoding="utf-8") as f:
            f.write("当前阶段：2\n## 错题本\n（暂无）\n## 疑难点\n（暂无）\n")
        self.assertEqual(self._validate(ws2).returncode, 0)       # 合法「第N阶段」计划不误伤

    def test_state_row_id_status_types_validated(self):
        r = self._validate(self._full_ws({"mistake_archive": [
            {"id": ["q1"], "note": "x", "status": "待复盘"}]}))
        self.assertEqual(r.returncode, 1)                         # id 非字符串 → err
        r = self._validate(self._full_ws({"confusion_log": [
            {"id": "c1", "note": "x", "status": {"s": 1}}]}))
        self.assertEqual(r.returncode, 1)                         # status 非字符串 → err

    def test_dangling_state_symlink_flagged(self):
        ws = self._full_ws(None)
        try:
            os.symlink(os.path.join(ws, "no_such_target.json"), os.path.join(ws, "study_state.json"))
        except (OSError, NotImplementedError):
            self.skipTest("no symlink privilege")
        r = self._validate(ws)
        self.assertEqual(r.returncode, 1)                         # 悬空链接不能整段跳过校验
        self.assertIn("符号链接", r.stdout)

    # ---- regression guards for Codex round-15 (5 findings) ----

    def test_validator_rejects_state_directory(self):
        ws = self._full_ws(None)
        os.makedirs(os.path.join(ws, "study_state.json"))         # state 路径是目录
        r = self._validate(ws)
        self.assertEqual(r.returncode, 1)
        self.assertIn("不是常规文件", r.stdout)                    # Tier-1 不再放行更新器写不进的工作区


    def test_md_phase_mismatch_warns(self):
        ws = self._full_ws({"current_phase": 2})                  # md 说 1，state 说 2
        with open(os.path.join(ws, "study_plan.md"), "w", encoding="utf-8") as f:
            f.write("# 计划\n## 阶段1：栈（references/wiki/ch1.md)\n## 阶段2：树（references/wiki/ch1.md）\n")
        r = self._validate(ws)
        self.assertEqual(r.returncode, 0)                         # 仅告警（md 是生成视图）
        self.assertIn("不一致", r.stdout)

    def test_state_phase_outside_plan_errors(self):
        ws = self._full_ws({"current_phase": 99})                 # 计划只有阶段1
        r = self._validate(ws)
        self.assertEqual(r.returncode, 1)                         # 事实源指向不存在的阶段 → 报错不是警告
        self.assertIn("不在 study_plan.md", r.stdout)


class DriftJsonSnapshots(unittest.TestCase):
    def test_t4_reads_state_json_snapshot(self):
        sys.path.insert(0, os.path.join(ROOT, "benchmark", "drift"))
        import run_drift as D
        sc = D.load_scenario(os.path.join(ROOT, "benchmark", "drift", "scenarios", "long_session_basic.json"))
        d = tempfile.mkdtemp()
        t = os.path.join(d, "t.jsonl")
        st2 = json.dumps({"version": 1, "current_phase": 2,
                          "mistake_archive": [{"id": "stack_lifo_1", "note": "误答 FIFO"}],
                          "confusion_log": []}, ensure_ascii=False)
        turns = [
            {"turn": 1, "assistant": "进入阶段2。", "phase_context": 2,
             "files_after": {"study_state.json": st2}},
            {"turn": 2, "user": "我回来了，继续复习", "kind": "resume",
             "assistant": "欢迎回来！我们接着阶段2继续复习。"},
        ]
        with open(t, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(x, ensure_ascii=False) for x in turns))
        m = D.evaluate(sc, t)["metrics"]
        self.assertEqual(m["reset_detected"], 0)                  # checkpoint 从 JSON 读到阶段 2
        self.assertEqual(m["mistake_rows_added"], 1)              # 行持久性也从 JSON 统计

    def test_t4_malformed_state_json_exits_2(self):
        sys.path.insert(0, os.path.join(ROOT, "benchmark", "drift"))
        import run_drift as D
        sc = D.load_scenario(os.path.join(ROOT, "benchmark", "drift", "scenarios", "long_session_basic.json"))
        d = tempfile.mkdtemp()
        t = os.path.join(d, "t.jsonl")
        with open(t, "w", encoding="utf-8") as f:
            f.write(json.dumps({"turn": 1, "assistant": "x",
                                "files_after": {"study_state.json": "{broken"}}) + "\n")
        with self.assertRaises(D.DriftError):
            D.evaluate(sc, t)


    def test_t4_object_state_snapshot_raises_drifterror(self):
        sys.path.insert(0, os.path.join(ROOT, "benchmark", "drift"))
        import run_drift as D
        sc = D.load_scenario(os.path.join(ROOT, "benchmark", "drift", "scenarios", "long_session_basic.json"))
        d = tempfile.mkdtemp()
        t = os.path.join(d, "t.jsonl")
        with open(t, "w", encoding="utf-8") as f:
            f.write(json.dumps({"turn": 1, "assistant": "x",
                                "files_after": {"study_state.json": {"current_phase": 2}}}) + chr(10))
        with self.assertRaises(D.DriftError):                     # 对象快照按畸形输入报 DriftError，
            D.evaluate(sc, t)                                     # 不是 TypeError 未处理堆栈

    def test_t4_object_plan_snapshot_raises_drifterror(self):
        sys.path.insert(0, os.path.join(ROOT, "benchmark", "drift"))
        import run_drift as D
        sc = D.load_scenario(os.path.join(ROOT, "benchmark", "drift", "scenarios", "long_session_basic.json"))
        d = tempfile.mkdtemp()
        t = os.path.join(d, "t.jsonl")
        with open(t, "w", encoding="utf-8") as f:
            f.write(json.dumps({"turn": 1, "assistant": "x",
                                "files_after": {"study_plan.md": ["## 阶段1"]}}) + chr(10))
        with self.assertRaises(D.DriftError):
            D.evaluate(sc, t)

    def test_t4_rejects_md_only_snapshot_after_state(self):
        sys.path.insert(0, os.path.join(ROOT, "benchmark", "drift"))
        import run_drift as D
        sc = D.load_scenario(os.path.join(ROOT, "benchmark", "drift", "scenarios", "long_session_basic.json"))
        d = tempfile.mkdtemp()
        t = os.path.join(d, "t.jsonl")
        st2 = json.dumps({"version": 1, "current_phase": 2,
                          "mistake_archive": [{"id": "stack_lifo_1", "note": "误答 FIFO"}],
                          "confusion_log": []}, ensure_ascii=False)
        stale_md = ("当前阶段：9\n## ❌ 错题档案记录\n| 错题ID | 章节 | 原因 | 状态 |\n| :- | :- | :- | :- |\n"
                    "| [#stack_lifo_1] | 1 | 误答 FIFO | 待复盘 |\n| [#fake_row_2] | 1 | 手改加行 | 待复盘 |\n")
        turns = [
            {"turn": 1, "assistant": "进入阶段2。", "phase_context": 2,
             "files_after": {"study_state.json": st2}},
            {"turn": 2, "assistant": "偷偷手改 md。",                # state 已确立后的 md-only 手改
             "files_after": {"study_progress.md": stale_md}},
            {"turn": 3, "user": "我回来了，继续复习", "kind": "resume",
             "assistant": "欢迎回来！我们接着阶段2继续复习。"},
        ]
        with open(t, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(x, ensure_ascii=False) for x in turns))
        m = D.evaluate(sc, t)["metrics"]
        self.assertEqual(m["mistake_rows_added"], 1)              # 手改 md 的加行不算数（state 才是事实源）
        self.assertEqual(m["reset_detected"], 0)                  # 断点仍按 state 的阶段 2，不被 md 的 9 带跑
        self.assertEqual(m["md_write_after_state"], 1)            # 且违规被计数曝光（阈值 0 会让场景 FAIL）

    def test_t4_seeds_from_fixture_state_json(self):
        # fixture 自带 study_state.json（阶段2 + 一条已有错题行）而生成视图 md 过期（阶段1、无行）——
        # 指标种子必须来自 JSON 事实源：已有行不算新增、断点按阶段2
        import shutil
        sys.path.insert(0, os.path.join(ROOT, "benchmark", "drift"))
        import run_drift as D
        sc = D.load_scenario(os.path.join(ROOT, "benchmark", "drift", "scenarios", "long_session_basic.json"))
        fx = os.path.join(tempfile.mkdtemp(), "fx")
        shutil.copytree(os.path.join(ROOT, sc["fixture"]), fx)
        json.dump({"version": 1, "current_phase": 2,
                   "mistake_archive": [{"id": "stack_lifo_1", "note": "误答 FIFO"}],
                   "confusion_log": []},
                  open(os.path.join(fx, "study_state.json"), "w", encoding="utf-8"), ensure_ascii=False)
        sc2 = dict(sc, fixture=fx)
        d = tempfile.mkdtemp()
        t = os.path.join(d, "t.jsonl")
        st_same = json.dumps({"version": 1, "current_phase": 2,
                              "mistake_archive": [{"id": "stack_lifo_1", "note": "误答 FIFO"}],
                              "confusion_log": []}, ensure_ascii=False)
        turns = [
            {"turn": 1, "user": "我回来了，继续复习", "kind": "resume",
             "assistant": "欢迎回来！我们接着阶段2继续复习。",
             "files_after": {"study_state.json": st_same}},
        ]
        with open(t, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(x, ensure_ascii=False) for x in turns))
        m = D.evaluate(sc2, t)["metrics"]
        self.assertEqual(m["mistake_rows_added"], 0)              # 行在 fixture state 里就有，不算会话新增
        self.assertEqual(m["reset_detected"], 0)                  # 断点种子=阶段2，不被过期 md 的阶段1 带偏

    def test_t4_placeholder_table_row_not_a_data_row(self):
        sys.path.insert(0, os.path.join(ROOT, "benchmark", "drift"))
        import run_drift as D
        md = ("当前阶段：1\n## ❌ 错题档案记录\n"
              "| 错题ID | 关联章节 | 错误原因分析 | 状态 |\n| :--- | :--- | :--- | :--- |\n"
              "| （暂无） | - | - | - |\n")
        p = D.parse_progress(md)
        self.assertEqual(p["mistake_rows"], [])                   # 生成视图的占位行不是幻影数据行

    def test_t4_rejects_invalid_state_phase(self):
        sys.path.insert(0, os.path.join(ROOT, "benchmark", "drift"))
        import run_drift as D
        sc = D.load_scenario(os.path.join(ROOT, "benchmark", "drift", "scenarios", "long_session_basic.json"))
        for bad_cp in ('"2"', "0"):                               # 字符串/0 都是坏输入，不能静默放行
            d = tempfile.mkdtemp()
            t = os.path.join(d, "t.jsonl")
            bad = '{"version": 1, "current_phase": %s, "mistake_archive": [], "confusion_log": []}' % bad_cp
            with open(t, "w", encoding="utf-8") as f:
                f.write(json.dumps({"turn": 1, "assistant": "x",
                                    "files_after": {"study_state.json": bad}}) + "\n")
            with self.assertRaises(D.DriftError):
                D.evaluate(sc, t)

    def test_t4_rejects_malformed_state_rows(self):
        sys.path.insert(0, os.path.join(ROOT, "benchmark", "drift"))
        import run_drift as D
        sc = D.load_scenario(os.path.join(ROOT, "benchmark", "drift", "scenarios", "long_session_basic.json"))
        for bad_rows in ('["[#q1] 字符串行"]', '[{"id": "q1"}]'):   # 非对象行 / 缺非空 note
            d = tempfile.mkdtemp()
            t = os.path.join(d, "t.jsonl")
            bad = '{"version": 1, "current_phase": 2, "mistake_archive": %s, "confusion_log": []}' % bad_rows
            with open(t, "w", encoding="utf-8") as f:
                f.write(json.dumps({"turn": 1, "assistant": "x",
                                    "files_after": {"study_state.json": bad}}) + "\n")
            with self.assertRaises(D.DriftError):                 # 坏行 fail-loud，不再以 0 行静默通过
                D.evaluate(sc, t)

    def test_md_write_after_state_is_gated_metric(self):
        # 场景阈值 md_write_after_state_max=0 存在，且指标真会计数——A4 违规不只是被忽略
        sc_json = json.load(open(os.path.join(ROOT, "benchmark", "drift", "scenarios",
                                              "long_session_basic.json"), encoding="utf-8"))
        self.assertEqual(sc_json["thresholds"].get("md_write_after_state_max"), 0)
        sc_live = json.load(open(os.path.join(ROOT, "benchmark", "drift", "scenarios",
                                              "live_smoke_basic.json"), encoding="utf-8"))
        self.assertEqual(sc_live["thresholds"].get("md_write_after_state_max"), 0)

    def test_t4_rejects_non_string_row_id(self):
        sys.path.insert(0, os.path.join(ROOT, "benchmark", "drift"))
        import run_drift as D
        sc = D.load_scenario(os.path.join(ROOT, "benchmark", "drift", "scenarios", "long_session_basic.json"))
        d = tempfile.mkdtemp()
        t = os.path.join(d, "t.jsonl")
        bad = json.dumps({"version": 1, "current_phase": 2, "confusion_log": [],
                          "mistake_archive": [{"id": ["q1"], "note": "x"}]}, ensure_ascii=False)
        with open(t, "w", encoding="utf-8") as f:
            f.write(json.dumps({"turn": 1, "assistant": "x",
                                "files_after": {"study_state.json": bad}}) + chr(10))
        with self.assertRaises(D.DriftError):                     # 伪键 id 不做 str() 硬转
            D.evaluate(sc, t)

    def test_t4_keeps_real_rows_containing_placeholder_text(self):
        sys.path.insert(0, os.path.join(ROOT, "benchmark", "drift"))
        import run_drift as D
        md = ("当前阶段：1" + chr(10) + "## ❌ 错题档案记录" + chr(10)
              + "| 错题ID | 关联章节 | 原因 | 状态 |" + chr(10) + "| :- | :- | :- | :- |" + chr(10)
              + "| [#q1] | 1 | 空集（暂无）元素处理错 | 待复盘 |" + chr(10)
              + "| （暂无） | - | - | - |" + chr(10))
        p = D.parse_progress(md)
        self.assertEqual(len(p["mistake_rows"]), 1)               # 真行保留、纯占位行剔除
        self.assertIn("空集（暂无）", p["mistake_rows"][0])

    def test_t4_idless_state_rows_distinct_by_chapter(self):
        sys.path.insert(0, os.path.join(ROOT, "benchmark", "drift"))
        import run_drift as D
        snap = D.parse_state_json(json.dumps({
            "version": 1, "current_phase": 1, "mistake_archive": [],
            "confusion_log": [{"chapter": "1", "note": "取模没搞懂"},
                                 {"chapter": "2", "note": "取模没搞懂"}]}, ensure_ascii=False))
        self.assertEqual(len(set(snap["confusion_rows"])), 2)     # 同 note 不同章不折叠

    def test_t4_counts_md_write_event_after_state(self):
        sys.path.insert(0, os.path.join(ROOT, "benchmark", "drift"))
        import run_drift as D
        sc = D.load_scenario(os.path.join(ROOT, "benchmark", "drift", "scenarios", "long_session_basic.json"))
        d = tempfile.mkdtemp()
        t = os.path.join(d, "t.jsonl")
        st2 = json.dumps({"version": 1, "current_phase": 2, "mistake_archive": [],
                          "confusion_log": []}, ensure_ascii=False)
        turns = [
            {"turn": 1, "assistant": "进入阶段2。", "files_after": {"study_state.json": st2}},
            {"turn": 2, "assistant": "只写 md 事件、不带快照。",
             "events": [{"type": "write_file", "path": "study_progress.md"}]},
        ]
        with open(t, "w", encoding="utf-8") as f:
            f.write(chr(10).join(json.dumps(x, ensure_ascii=False) for x in turns))
        m = D.evaluate(sc, t)["metrics"]
        self.assertEqual(m["md_write_after_state"], 1)            # 纯事件形态的手改也计数

    def test_review_trigger_and_cheatsheet_read_state(self):
        cram = open(os.path.join(ROOT, "skills", "exam-cram", "SKILL.md"), encoding="utf-8").read()
        self.assertIn("judged from `study_state.json`", cram)     # 终局复盘触发看事实源
        sheet = open(os.path.join(ROOT, "skills", "exam-cheatsheet", "SKILL.md"), encoding="utf-8").read()
        self.assertIn("Weak-spot source: `study_state.json`", sheet)   # 小抄弱点清单读事实源

    def test_t4_state_event_without_snapshot_establishes_state(self):
        sys.path.insert(0, os.path.join(ROOT, "benchmark", "drift"))
        import run_drift as D
        sc = D.load_scenario(os.path.join(ROOT, "benchmark", "drift", "scenarios", "long_session_basic.json"))
        d = tempfile.mkdtemp()
        t = os.path.join(d, "t.jsonl")
        turns = [
            {"turn": 1, "assistant": "官方工具写了 state（裸事件）。",
             "events": [{"type": "write_file", "path": "study_state.json"}]},
            {"turn": 2, "assistant": "手改 md。",
             "files_after": {"study_progress.md": "当前阶段：9"}},
        ]
        with open(t, "w", encoding="utf-8") as f:
            f.write(chr(10).join(json.dumps(x, ensure_ascii=False) for x in turns))
        m = D.evaluate(sc, t)["metrics"]
        self.assertEqual(m["md_write_after_state"], 1)            # 裸事件也确立事实源，md-only 被记违规

    def test_t4_rejects_state_phase_outside_plan(self):
        sys.path.insert(0, os.path.join(ROOT, "benchmark", "drift"))
        import run_drift as D
        sc = D.load_scenario(os.path.join(ROOT, "benchmark", "drift", "scenarios", "long_session_basic.json"))
        d = tempfile.mkdtemp()
        t = os.path.join(d, "t.jsonl")
        bad = json.dumps({"version": 1, "current_phase": 99,
                          "mistake_archive": [], "confusion_log": []})
        with open(t, "w", encoding="utf-8") as f:
            f.write(json.dumps({"turn": 1, "assistant": "x",
                                "files_after": {"study_state.json": bad}}) + chr(10))
        with self.assertRaises(D.DriftError):                     # 计划外断点是坏输入，不进指标
            D.evaluate(sc, t)

    def test_t4_state_only_fixture_without_initial_md(self):
        import shutil
        sys.path.insert(0, os.path.join(ROOT, "benchmark", "drift"))
        import run_drift as D
        sc = D.load_scenario(os.path.join(ROOT, "benchmark", "drift", "scenarios", "long_session_basic.json"))
        fx = os.path.join(tempfile.mkdtemp(), "fx")
        shutil.copytree(os.path.join(ROOT, sc["fixture"]), fx)
        os.remove(os.path.join(fx, "study_progress.initial.md"))   # 纯 A4 fixture：只有 state
        json.dump({"version": 1, "current_phase": 2,
                   "mistake_archive": [], "confusion_log": []},
                  open(os.path.join(fx, "study_state.json"), "w", encoding="utf-8"))
        sc2 = dict(sc, fixture=fx)
        d = tempfile.mkdtemp()
        t = os.path.join(d, "t.jsonl")
        with open(t, "w", encoding="utf-8") as f:
            f.write(json.dumps({"turn": 1, "user": "我回来了，继续复习", "kind": "resume",
                                "assistant": "欢迎回来！我们接着阶段2继续复习。"},
                               ensure_ascii=False) + chr(10))
        m = D.evaluate(sc2, t)["metrics"]                        # 不再因缺 initial md 报 malformed
        self.assertEqual(m["reset_detected"], 0)

    def test_converter_docs_cover_state_snapshot(self):
        doc = open(os.path.join(ROOT, "benchmark", "drift", "docs", "live_agent_pilot.md"),
                   encoding="utf-8").read()
        self.assertIn("write_file: study_state.json", doc)        # 运行手册与转换器契约一致
        tpl = open(os.path.join(ROOT, "benchmark", "drift", "templates", "live_session_template.md"),
                   encoding="utf-8").read()
        self.assertIn("study_state.json", tpl)

    def test_t4_state_phase_valid_against_updated_plan(self):
        sys.path.insert(0, os.path.join(ROOT, "benchmark", "drift"))
        import run_drift as D
        sc = D.load_scenario(os.path.join(ROOT, "benchmark", "drift", "scenarios", "long_session_basic.json"))
        d = tempfile.mkdtemp()
        t = os.path.join(d, "t.jsonl")
        fx_plan = open(os.path.join(ROOT, sc["fixture"], "study_plan.md"), encoding="utf-8").read()
        new_plan = fx_plan + chr(10) + "## 阶段9：附加冲刺（references/wiki/ch1_stack_queue.md）" + chr(10)
        st9 = json.dumps({"version": 1, "current_phase": 9,
                          "mistake_archive": [], "confusion_log": []}, ensure_ascii=False)
        turns = [
            {"turn": 1, "user": "我们改计划，加一个冲刺阶段9", "assistant": "好的，已按你的要求调整计划。",
             "files_after": {"study_plan.md": new_plan}},
            {"turn": 2, "assistant": "进入阶段9。", "phase_context": 9,
             "files_after": {"study_state.json": st9}},
        ]
        with open(t, "w", encoding="utf-8") as f:
            f.write(chr(10).join(json.dumps(x, ensure_ascii=False) for x in turns))
        m = D.evaluate(sc, t)["metrics"]                        # 授权改计划后的新阶段不再被判坏输入
        self.assertGreaterEqual(m["turns"], 2)

    def test_smoke_state_row_matches_by_id(self):
        sys.path.insert(0, os.path.join(ROOT, "benchmark", "behavior_smoke"))
        import run_behavior_smoke as S
        d = tempfile.mkdtemp()
        fx = os.path.join(d, "fx")
        os.makedirs(fx)
        json.dump({"version": 1, "current_phase": 1}, open(os.path.join(fx, "study_state.json"),
                                                             "w", encoding="utf-8"))
        snap = os.path.join(d, "snap.json")
        json.dump({"current_phase": 1,
                   "mistake_archive": [{"id": "mc_q2", "note": "只有错因没有题号"}]},
                  open(snap, "w", encoding="utf-8"), ensure_ascii=False)
        import unittest.mock as mock
        with mock.patch.object(S, "_p", lambda rel: rel):
            ok = S._state_row_written(fx, {"sa": snap}, "sa", "mistake_archive", "mc_q2")
        self.assertTrue(ok)                                       # 官方 add-mistake 的 id 字段也算命中

    def test_t4_scalar_state_field_exits_2_not_traceback(self):
        sys.path.insert(0, os.path.join(ROOT, "benchmark", "drift"))
        import run_drift as D
        sc = D.load_scenario(os.path.join(ROOT, "benchmark", "drift", "scenarios", "long_session_basic.json"))
        d = tempfile.mkdtemp()
        t = os.path.join(d, "t.jsonl")
        bad = json.dumps({"version": 1, "current_phase": 2, "mistake_archive": 1})
        with open(t, "w", encoding="utf-8") as f:
            f.write(json.dumps({"turn": 1, "assistant": "x",
                                "files_after": {"study_state.json": bad}}) + "\n")
        with self.assertRaises(D.DriftError):                     # 畸形快照统一走 DriftError，不 TypeError 崩
            D.evaluate(sc, t)

    def test_converter_tracks_state_json_snapshot(self):
        sys.path.insert(0, os.path.join(ROOT, "benchmark", "drift"))
        import convert_session_log as C
        base = ["# Live Session", "", "## Turn 1", "", "### User", "u", "", "### Assistant", "a", "",
                "### Events", "- write_file: study_state.json", ""]
        # 有 write_file 无匹配快照 → 必须报错（否则 T4 拿旧状态继续算，漏掉重置/丢行）
        with self.assertRaises(C.SessionLogError):
            C.parse_session_log("\n".join(base))
        good = base + ["### Files After: study_state.json", "```json",
                       json.dumps({"version": 1, "current_phase": 1,
                                   "mistake_archive": [], "confusion_log": []}),
                       "```", ""]
        rows = C.parse_session_log("\n".join(good))
        self.assertIn("study_state.json", rows[0]["files_after"])  # 快照被跟踪进 files_after


class Contract(unittest.TestCase):
    # v4-P2: the root SKILL.md is a language-neutral router (still carries the
    # state-contract essentials); the zh/en full-entry manuals live under locales/.
    ENTRY_POINTS = ["SKILL.md", "locales/zh/SKILL.md", "locales/en/SKILL.md", "AGENTS.md",
                    "prompts/web_prompt.md",
                    "prompts/web_prompt.en.md", "skills/exam-cram/SKILL.md",
                    "skills/exam-quiz/SKILL.md", "skills/exam-tutor/SKILL.md", "skills/exam-review/SKILL.md",
                    "skills/confusion-tracker/SKILL.md"]

    def test_all_entry_points_carry_state_contract(self):
        for p in self.ENTRY_POINTS:
            txt = open(os.path.join(ROOT, p), encoding="utf-8").read()
            self.assertIn("study_state.json", txt, p)
            self.assertIn("update_progress.py", txt, p)

    def test_root_skill_lock_prefers_state(self):
        # v4-P2: the zh workflow wording lives in the zh full-entry pack
        txt = open(os.path.join(ROOT, "locales", "zh", "SKILL.md"), encoding="utf-8").read()
        self.assertIn("断点状态锁定 (`study_state.json`", txt)     # zh 全量入口的状态锁对齐事实源
        self.assertIn("set-check", txt)

    def test_le1d_question_semantics_and_unverified_cap_are_explicit(self):
        cadence_phrase = {
            "AGENTS.md": "题库练习/阶段测验",
            "skills/exam-cram/SKILL.md": "bank-backed drills or checkpoints",
            "skills/exam-tutor/SKILL.md": "bank-backed drills or checkpoints",
        }
        for rel, phrase in cadence_phrase.items():
            txt = open(os.path.join(ROOT, rel), encoding="utf-8").read()
            self.assertIn(phrase, txt, rel)
            self.assertIn("no_questions=true", txt, rel)
            self.assertIn("covered_unverified", txt, rel)

    def test_web_prompt_never_claims_local_writes(self):
        txt = open(os.path.join(ROOT, "prompts", "web_prompt.md"), encoding="utf-8").read()
        self.assertIn("网页端口径", txt)                          # A4 条款按网页端能力改写
        self.assertIn("绝不要声称你已写入", txt)                   # 不许谎称本地写入
        self.assertIn("只读事实源", txt)                          # 粘贴的 state 只读恢复

    def test_root_skill_final_review_reads_state(self):
        entry = open(os.path.join(ROOT, "locales", "zh", "SKILL.md"), encoding="utf-8").read()
        review = open(os.path.join(ROOT, "skills", "exam-review", "SKILL.md"), encoding="utf-8").read()
        self.assertIn("exam-review", entry)                       # 兼容入口只负责路由
        self.assertIn("mistake_archive", review)                  # 具体读取规则留在控制层

    def test_state_scenario_exercises_md_gate(self):
        # 新 state-backed 场景真正武装 md_write_after_state 阈值（basic 场景对该阈值先天空转）
        sc = json.load(open(os.path.join(ROOT, "benchmark", "drift", "scenarios",
                                         "long_session_state.json"), encoding="utf-8"))
        self.assertEqual(sc["thresholds"].get("md_write_after_state_max"), 0)
        sys.path.insert(0, os.path.join(ROOT, "benchmark", "drift"))
        import run_drift as D
        r = D.evaluate(sc, os.path.join(ROOT, sc["transcript"]))
        self.assertEqual(r["metrics"]["md_write_after_state"], 0)   # 官方双写不违规
        self.assertGreaterEqual(r["metrics"]["mistake_rows_added"], 2)   # state 快照驱动行指标
        import json as _j, tempfile as _t
        rows = [_j.loads(x) for x in open(os.path.join(ROOT, sc["transcript"]), encoding="utf-8")
                if x.strip()]
        rows.append({"turn": 99, "assistant": "手改 md。",
                     "events": [{"type": "write_file", "path": "study_progress.md"}]})
        bad = os.path.join(_t.mkdtemp(), "bad.jsonl")
        with open(bad, "w", encoding="utf-8") as f:
            f.write(chr(10).join(_j.dumps(x, ensure_ascii=False) for x in rows))
        r2 = D.evaluate(sc, bad)
        self.assertEqual(r2["metrics"]["md_write_after_state"], 1)   # 违规会被该场景真实拦截

    def test_updater_paths_resolve_from_package(self):
        for rel in (("skills", "exam-cram", "SKILL.md"), ("skills", "exam-quiz", "SKILL.md"),
                    ("skills", "exam-tutor", "SKILL.md"), ("AGENTS.md",)):
            txt = open(os.path.join(ROOT, *rel), encoding="utf-8").read()
            self.assertIn('CLAUDE_SKILL_DIR' + chr(125) + '/scripts/update_progress.py', txt, rel)

    def test_agents_md_bootstraps_state(self):
        txt = open(os.path.join(ROOT, "AGENTS.md"), encoding="utf-8").read()
        self.assertIn('init' + chr(96) + ' to establish the source of truth', txt)   # 通用代理契约也先建 state

    def test_root_skill_bootstraps_state_when_python_available(self):
        txt = open(os.path.join(ROOT, "locales", "zh", "SKILL.md"), encoding="utf-8").read()
        self.assertIn("先跑 `python " + chr(34) + chr(36) + "{CLAUDE_SKILL_DIR}/scripts/update_progress.py" + chr(34), txt)

    def test_agents_md_prefers_state(self):
        txt = open(os.path.join(ROOT, "AGENTS.md"), encoding="utf-8").read()
        self.assertIn("restore from `study_state.json` when it exists", txt)   # 先读进度条目对齐事实源
        self.assertIn("add-mistake/add-confusion", txt)             # 记录条目走官方路径

    def test_cram_restore_prefers_state(self):
        # 恢复断点必须先读 study_state.json（事实源）——生成视图 md 过期/被手改时不能拿它当起点
        txt = open(os.path.join(ROOT, "skills", "exam-cram", "SKILL.md"), encoding="utf-8").read()
        self.assertIn("from `study_state.json` when it exists", txt)

    def test_review_output_contract_routes_state(self):
        txt = open(os.path.join(ROOT, "skills", "exam-review", "SKILL.md"), encoding="utf-8").read()
        self.assertIn("via `update_progress.py set-mistake-status`", txt)   # 输出契约也走官方路径

    def test_cheatsheet_mastered_chapters_read_state(self):
        txt = open(os.path.join(ROOT, "skills", "exam-cheatsheet", "SKILL.md"), encoding="utf-8").read()
        self.assertIn("`current_phase`/`phase_checklist` when it exists", txt)

    def test_behavior_smoke_asserts_state_writes(self):
        spec = json.load(open(os.path.join(ROOT, "benchmark", "behavior_smoke", "scenarios.json"),
                              encoding="utf-8"))
        by = {sc["name"]: sc for sc in spec["scenarios"]}
        self.assertIn("state_after", by["hint_skip_mistake_archive"])   # 冒烟断言 state 写入
        self.assertIn("state_after", by["confusion_tracking"])
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(ROOT, "benchmark", "behavior_smoke",
                                                          "run_behavior_smoke.py"), "--mock"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("state_row=True", r.stdout)

    def test_review_skill_documents_status_commands(self):
        # replay 流要把行标成 已订正/已回顾 —— A4 边界必须给出官方状态命令，否则 agent 无合法持久化路径
        txt = open(os.path.join(ROOT, "skills", "exam-review", "SKILL.md"), encoding="utf-8").read()
        self.assertIn("set-mistake-status", txt)
        self.assertIn("set-confusion-status", txt)

    def test_no_python_fallback_fixture_is_stateless(self):
        # no_python_fallback 冒烟声称验证「无 state 的手写 md 工作区」——fixture 里绝不能有 study_state.json
        spec = json.load(open(os.path.join(ROOT, "benchmark", "behavior_smoke", "scenarios.json"),
                              encoding="utf-8"))
        sc = next(x for x in spec["scenarios"] if x["name"] == "no_python_fallback")
        fx = os.path.join(ROOT, "benchmark", "behavior_smoke", sc["fallback_workspace"])
        self.assertTrue(os.path.isdir(fx), fx)
        self.assertFalse(os.path.isfile(os.path.join(fx, "study_state.json")))

    def test_no_network_or_llm(self):
        src = open(os.path.join(SCRIPTS, "update_progress.py"), encoding="utf-8").read()
        for banned in ("import requests", "urllib.request", "import anthropic", "import socket"):
            self.assertNotIn(banned, src)


class WorkspaceRegistry(unittest.TestCase):
    """v4-P4 §2.5：全局工作区注册表（workspace-register / workspace-list）——
    冻结位置 EXAMPREP_HOME|~/.exam-cram 下 workspaces.json；测试一律经 EXAMPREP_HOME 隔离，
    绝不碰真实主目录。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "examprep_home")   # 不预建：save 应自建目录

    def _run(self, args, home=None):
        env = dict(os.environ)
        env["EXAMPREP_HOME"] = home or self.home
        return subprocess.run([sys.executable, os.path.join(SCRIPTS, "update_progress.py")] + args,
                              capture_output=True, text=True, encoding="utf-8", env=env)

    def _registry_file(self, home=None):
        return os.path.join(home or self.home, "workspaces.json")

    def _mk_dir(self, name):
        d = os.path.join(self.tmp, name)
        os.makedirs(d)
        return d

    def test_register_list_roundtrip(self):
        ws = self._mk_dir("ws_ds")
        mats = self._mk_dir("materials_ds")
        r = self._run(["workspace-register", "--course", "数据结构", "--path", ws,
                       "--materials", mats])
        self.assertEqual(r.returncode, 0, r.stderr)              # 注册表全局：不需要 --workspace
        reg = json.load(open(self._registry_file(), encoding="utf-8"))
        self.assertEqual(reg["version"], 1)                      # 冻结 schema：version + workspaces
        self.assertEqual(len(reg["workspaces"]), 1)
        row = reg["workspaces"][0]
        self.assertEqual(row["course"], "数据结构")
        self.assertEqual(row["path"], os.path.abspath(ws))       # 存绝对归一化路径
        self.assertEqual(row["materials"], os.path.abspath(mats))
        self.assertRegex(row["last_used"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        rj = self._run(["workspace-list", "--json"])
        self.assertEqual(rj.returncode, 0, rj.stderr)
        listed = json.loads(rj.stdout)                           # 机器口径 {"workspaces":[...]}
        self.assertEqual(listed["workspaces"], [row])
        rh = self._run(["workspace-list"])
        self.assertEqual(rh.returncode, 0, rh.stderr)
        self.assertIn("数据结构", rh.stdout)                     # 人读口径带课程与路径
        self.assertIn(os.path.abspath(ws), rh.stdout)

    def test_reregister_updates_in_place_no_duplicate(self):
        ws1, ws2 = self._mk_dir("ws_v1"), self._mk_dir("ws_v2")
        mats = self._mk_dir("mats_v1")
        self._run(["workspace-register", "--course", "EEC160", "--path", ws1,
                   "--materials", mats])
        r = self._run(["workspace-register", "--course", "EEC160", "--path", ws2])
        self.assertEqual(r.returncode, 0, r.stderr)
        reg = json.load(open(self._registry_file(), encoding="utf-8"))
        self.assertEqual(len(reg["workspaces"]), 1)              # 同课程重复登记不追加重复行
        row = reg["workspaces"][0]
        self.assertEqual(row["path"], os.path.abspath(ws2))      # 路径已更新
        self.assertEqual(row["materials"], os.path.abspath(mats))  # 未显式给 --materials 时保留旧值
        # 两门课都在，且 workspace-list 最近使用在前（同分钟并列时后登记的在前）
        wsb = self._mk_dir("ws_b")
        self._run(["workspace-register", "--course", "线性代数", "--path", wsb])
        rj = self._run(["workspace-list", "--json"])
        courses = [w["course"] for w in json.loads(rj.stdout)["workspaces"]]
        self.assertEqual(sorted(courses), ["EEC160", "线性代数"])
        self.assertEqual(courses[0], "线性代数")

    def test_register_conflicts_with_workspace_publication_and_writes_nothing(self):
        ws = self._mk_dir("ws_locked")
        with workspace_publication_lock(ws):
            result = self._run([
                "workspace-register", "--course", "Locked", "--path", ws,
            ])
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(os.path.exists(self._registry_file()))

    def test_course_move_locks_the_previous_workspace(self):
        old_ws = self._mk_dir("ws_old_locked")
        new_ws = self._mk_dir("ws_new_target")
        first = self._run([
            "workspace-register", "--course", "MoveMe", "--path", old_ws,
        ])
        self.assertEqual(first.returncode, 0, first.stderr)
        before = open(self._registry_file(), "rb").read()
        with workspace_publication_lock(old_ws):
            moved = self._run([
                "workspace-register", "--course", "MoveMe", "--path", new_ws,
            ])
        self.assertNotEqual(moved.returncode, 0)
        self.assertEqual(open(self._registry_file(), "rb").read(), before)

    def test_list_orders_newest_first_by_last_used(self):
        os.makedirs(self.home)
        with open(self._registry_file(), "w", encoding="utf-8", newline=chr(10)) as f:
            json.dump({"version": 1, "workspaces": [
                {"course": "旧课", "path": self.tmp, "materials": None,
                 "last_used": "2026-01-01 08:00"},
                {"course": "新课", "path": self.tmp, "materials": None,
                 "last_used": "2026-07-01 09:30"},
            ]}, f, ensure_ascii=False)
        rj = self._run(["workspace-list", "--json"])
        self.assertEqual(rj.returncode, 0, rj.stderr)
        courses = [w["course"] for w in json.loads(rj.stdout)["workspaces"]]
        self.assertEqual(courses, ["新课", "旧课"])              # 最近使用在前

    def test_register_missing_path_dies(self):
        missing = os.path.join(self.tmp, "no_such_dir")
        r = self._run(["workspace-register", "--course", "幽灵课", "--path", missing])
        self.assertNotEqual(r.returncode, 0)                     # 不存在的路径必须拒绝
        self.assertIn("不存在", r.stderr)
        self.assertFalse(os.path.exists(self._registry_file()))  # 拒绝时不落任何注册表
        ws = self._mk_dir("ws_ok")
        r2 = self._run(["workspace-register", "--course", "幽灵课", "--path", ws,
                        "--materials", missing])
        self.assertNotEqual(r2.returncode, 0)                    # --materials 同样必须真实存在
        self.assertFalse(os.path.exists(self._registry_file()))

    def test_corrupt_registry_fails_loud_never_recreated(self):
        os.makedirs(self.home)
        with open(self._registry_file(), "w", encoding="utf-8") as f:
            f.write("{corrupt json!!")
        ws = self._mk_dir("ws_c")
        for cmd in (["workspace-list"], ["workspace-list", "--json"],
                    ["workspace-register", "--course", "任意课", "--path", ws]):
            r = self._run(cmd)
            self.assertNotEqual(r.returncode, 0, "corrupt registry must fail: %s" % cmd)
            self.assertIn("workspaces.json", r.stderr)           # zh 报错点名注册表文件
            self.assertIn("不会静默重建", r.stderr)
        raw = open(self._registry_file(), encoding="utf-8").read()
        self.assertEqual(raw, "{corrupt json!!")                 # 损坏文件原样保留，绝不静默重建

    def test_examprep_home_isolation(self):
        home2 = os.path.join(self.tmp, "examprep_home_2")
        ws = self._mk_dir("ws_iso")
        r = self._run(["workspace-register", "--course", "心理学导论", "--path", ws])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isfile(self._registry_file()))   # 落在 EXAMPREP_HOME 冻结位置
        self.assertFalse(os.path.exists(self._registry_file(home2)))
        r2 = self._run(["workspace-list"], home=home2)
        self.assertEqual(r2.returncode, 0, r2.stderr)            # 空注册表是正常态：exit 0
        self.assertIn("注册表为空", r2.stdout)                   # 且给中文友好提示
        self.assertNotIn("心理学导论", r2.stdout)                # 两个 HOME 互不可见
        rj = self._run(["workspace-list", "--json"], home=home2)
        self.assertEqual(json.loads(rj.stdout), {"workspaces": []})

    def test_workspace_flag_still_required_elsewhere(self):
        # 放宽 --workspace 只豁免注册表子命令——其余子命令保持 argparse required 契约（exit 2）
        r = self._run(["show"])
        self.assertEqual(r.returncode, 2)
        self.assertIn("--workspace", r.stderr)


class EnTemplateMigration(unittest.TestCase):
    """Codex 评审回归：`ingest --lang en` 建出的英文进度模板跑 `init` 迁移时，
    英文表头行（"| Mistake ID | Chapter | … |"）绝不能被当成真实错题/疑难行迁进 state，
    英文打卡区（check-in）与断点（Phase 1）也要照常解析。"""

    RAW_EN = {
        "course_name": "Data Structures",
        "phases": [
            {"phase_num": 1, "phase_name": "Linear lists", "wiki_filename": "ch1_linear.md",
             "wiki_content": "# Linear lists\n\n## Linked list\nAccess cost is O(n)."},
            {"phase_num": 2, "phase_name": "Sorting", "wiki_filename": "ch2_sort.md",
             "wiki_content": "# Sorting\n\n## Merge sort\nStable, O(n log n)."},
        ],
        "quiz_bank": [
            {"id": "q1", "phase": 1, "type": "choice", "question": "Linked-list access cost?",
             "options": ["O(1)", "O(n)"], "answer": "O(n)", "source": "teacher"},
        ],
    }

    def _build_en_ws(self):
        tmp = tempfile.mkdtemp(prefix="enws_")
        self.addCleanup(__import__("shutil").rmtree, tmp, ignore_errors=True)
        raw_path = os.path.join(tmp, "raw_input.json")
        with open(raw_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(self.RAW_EN, f, ensure_ascii=False)
        ws = os.path.join(tmp, "ws")
        materials = os.path.join(tmp, "materials")
        fixture_home = os.path.join(tmp, "examprep-home")
        os.makedirs(materials)
        env = dict(os.environ)
        env["EXAMPREP_HOME"] = fixture_home
        confirmed = subprocess.run([
            sys.executable, os.path.join(SCRIPTS, "exam_start.py"), "confirm",
            "--course", "english-template-fixture",
            "--workspace", ws, "--materials", materials,
            "--mode", "from_scratch", "--time-budget", "le1d",
            "--language", "en", "--artifact-mode", "chat",
            "--processing-mode", "full", "--json",
        ], capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(confirmed.returncode, 0,
                         confirmed.stdout + confirmed.stderr)
        with open(os.path.join(ws, ".phase-test-examprep-home"), "w",
                  encoding="utf-8") as stream:
            stream.write(fixture_home)
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "ingest.py"),
                            "--input", raw_path, "--output-dir", ws, "--lang", "en",
                            "--force"],
                           capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # ``exam_start confirm`` seeds canonical state before the compiler runs.
        # These tests intentionally exercise migration from the compiler's English
        # Markdown template, so remove only that seed while retaining the exact-pair
        # registry and runtime receipt required by the compiler.
        os.remove(os.path.join(ws, "study_state.json"))
        return ws

    def test_en_init_does_not_ingest_header_rows(self):
        ws = self._build_en_ws()
        r = _up(ws, ["init"])
        self.assertEqual(r.returncode, 0, r.stderr)
        st = _state(ws)
        # 英文表头行/占位不是条目——迁移后档案必须是空的（回归：{id:"Mistake ID"} 假行）
        self.assertEqual(st["mistake_archive"], [], st["mistake_archive"])
        self.assertEqual(st["confusion_log"], [], st["confusion_log"])
        # 断点解析：模板断点行 "Current phase: Phase 1: …" → current_phase=1
        self.assertEqual(st["current_phase"], 1)
        # 打卡区（"Knowledge-point check-in status"）被识别：2 阶段 + Mock test + Pitfall sweep
        texts = [row["text"] for row in st["phase_checklist"]]
        self.assertEqual(len(texts), 4, texts)
        self.assertTrue(any("Phase 1" in t for t in texts), texts)
        self.assertTrue(all(not row["done"] for row in st["phase_checklist"]))
        # 打卡区没被表头行污染
        self.assertFalse(any("Mistake ID" in t or "Trouble spot" in t for t in texts), texts)

    def test_en_workspace_add_mistake_render_roundtrip_stays_clean(self):
        ws = self._build_en_ws()
        self.assertEqual(_up(ws, ["init"]).returncode, 0)
        r = _up(ws, ["add-mistake", "--id", "q1", "--chapter", "1", "--note", "picked O(1)"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_up(ws, ["render"]).returncode, 0)
        r2 = _up(ws, ["init", "--force"])                          # 生成视图迁回 state 的恢复路径
        self.assertEqual(r2.returncode, 0, r2.stderr)
        st = _state(ws)
        self.assertEqual(len(st["mistake_archive"]), 1, st["mistake_archive"])
        self.assertEqual(st["mistake_archive"][0]["id"], "q1")
        self.assertEqual(st["confusion_log"], [])                  # 往返不长出幽灵行
        self.assertEqual(len(st["phase_checklist"]), 4)

    def test_en_confusion_and_window_headings_recognized(self):
        # 手写英文进度文件：trouble-spot 表行归疑难档、Knowledge window 区行归窗口档
        md = ("# Progress\n\n"
              "* Current phase: Phase 2\n\n"
              "## Mistake archive\n"
              "| Mistake ID | Chapter | Question summary | Error analysis | Status |\n"
              "| :--- | :--- | :--- | :--- | :--- |\n"
              "| [#q7] | 2 | quicksort stability | mixed up stable sorts | to review |\n\n"
              "## Concept trouble-spot log\n"
              "| No. | Chapter | Trouble spot | Answer key points | Status |\n"
              "| :--- | :--- | :--- | :--- | :--- |\n"
              "| 1 | 1 | modulo boundary | wrap-around rule | to revisit |\n\n"
              "## Knowledge window (recent-mastery tracking)\n"
              "| Knowledge point | Chapter | Status | Note |\n"
              "| --- | --- | --- | --- |\n"
              "| LIFO order | 1 | verified by quiz | quizzed twice |\n")
        ws = _mk_ws(tempfile.mkdtemp(), md=md)
        with open(os.path.join(ws, "study_plan.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write("# Plan\n## Phase 1: Stack\n## Phase 2: Queue\n")
        r = _up(ws, ["init"])
        self.assertEqual(r.returncode, 0, r.stderr)
        st = _state(ws)
        self.assertEqual(st["current_phase"], 2)
        self.assertEqual(len(st["mistake_archive"]), 1, st["mistake_archive"])
        row = st["mistake_archive"][0]
        self.assertEqual(row["id"], "q7")
        self.assertEqual(row["chapter"], "2")                     # en 表头列角色映射（chapter 列）
        self.assertEqual(row["status"], "to_review")              # en 显示词状态归代号
        self.assertNotIn("to review", row["note"])                # 状态不再混进 note
        self.assertEqual(len(st["confusion_log"]), 1, st["confusion_log"])
        self.assertIn("modulo boundary", st["confusion_log"][0]["note"])
        self.assertEqual(st["confusion_log"][0]["status"], "to_revisit")
        win = st["knowledge_window"]
        self.assertEqual(len(win), 1, win)                        # 窗口表头行不被当数据行
        self.assertEqual(win[0]["point"], "LIFO order")
        self.assertEqual(win[0]["status"], "verified")            # en 窗口状态词归代号


if __name__ == "__main__":
    unittest.main(verbosity=2)
