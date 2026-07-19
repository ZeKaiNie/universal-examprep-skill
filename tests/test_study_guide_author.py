import base64
import contextlib
import copy
import hashlib
import io
import json
import os
import shutil
import sys
import unittest
import uuid
from unittest import mock


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import study_guide_author as author
from asset_crops import (
    CropReceipt,
    canonical_sha256 as crop_canonical_sha256,
    compact_asset_from_receipt,
    make_crop_spec_sha256,
)
from ingestion import ContentUnit, SourceRecord, atomic_write_jsonl, file_sha256
from ingestion.claims import (
    ClaimRecord,
    ClaimSubject,
    compile_claim_proposals,
)


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")


def write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            json.dump(row, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")


class AuthoringWorkspace(unittest.TestCase):
    def _make_crop_asset(self, item_id, side, role, content_scope, page, label):
        output_sha256 = hashlib.sha256(PNG_1X1).hexdigest()
        semantic_purity = {
            "schema_version": 2,
            "target_item_id": item_id,
            "side": side,
            "crop_sha256": output_sha256,
            "verdict": "target_item_only",
            "unrelated_content_present": False,
            "student_attempt_present": False,
            "detected_item_ids": [item_id],
            "reviewer_kind": "human",
            "reviewer": "test-fixture",
            "reviewed_at": "2026-07-18T00:00:00Z",
            "evidence_binding_sha256": hashlib.sha256(
                ("semantic:%s:%s" % (item_id, side)).encode("utf-8")
            ).hexdigest(),
            "required_context_ids": [],
        }
        spec = {
            "item_id": item_id,
            "chapter_id": "ch01",
            "side": side,
            "role": role,
            "content_scope": content_scope,
            "isolation": "target_item_only",
            "source_id": self.source.source_id,
            "source_file": self.source.path,
            "source_sha256": self.source.sha256,
            "source_page": page,
            "page_box_pdf_points": [0, 0, 612, 792],
            "bbox_pdf_points": [20, 20, 590, 300],
            "selection_method": "human",
            "selection_evidence_sha256": hashlib.sha256(
                ("selection:%s:%s" % (item_id, side)).encode("utf-8")
            ).hexdigest(),
            "renderer_id": "fixture",
            "renderer_version": "1",
            "renderer_config_sha256": hashlib.sha256(
                b"fixture renderer config"
            ).hexdigest(),
            "semantic_purity": semantic_purity,
        }
        spec_sha256 = make_crop_spec_sha256(**spec)
        relative = "references/assets/%s_crop_%s.png" % (
            label, spec_sha256[:12]
        )
        absolute = os.path.join(self.workspace, *relative.split("/"))
        with open(absolute, "wb") as stream:
            stream.write(PNG_1X1)
        receipt = CropReceipt.create(
            output_path=relative,
            output_sha256=output_sha256,
            output_width=1,
            output_height=1,
            **spec
        )
        self.crop_receipts.append(receipt)
        return compact_asset_from_receipt(receipt)

    def _write_crop_report(self):
        rows = [
            receipt.to_dict()
            for receipt in sorted(
                self.crop_receipts, key=lambda value: value.crop_receipt_id
            )
        ]
        write_json(
            os.path.join(self.workspace, ".ingest", "parse_report.json"),
            {
                "crop_receipts": rows,
                "crop_receipt_index_sha256": crop_canonical_sha256(rows),
            },
        )

    def setUp(self):
        self.full_processing_patch = mock.patch.object(
            author.exam_start,
            "require_full_processing",
            return_value={"processing_mode": "full", "ready_to_ingest": True},
        )
        self.full_processing_patch.start()
        self.addCleanup(self.full_processing_patch.stop)
        self.ingestion_v2_patch = mock.patch.object(
            author,
            "require_current_ingestion_v2",
            return_value="ingestion-v2",
        )
        self.ingestion_v2_patch.start()
        self.addCleanup(self.ingestion_v2_patch.stop)
        # Use the platform's ordinary directory permissions.  Python 3.14's
        # tempfile 0o700 creation can produce a non-traversable directory in
        # some restricted Windows runners.
        self.workspace = os.path.join(ROOT, "author-test-" + uuid.uuid4().hex)
        os.makedirs(self.workspace)
        for relative in ("materials", "references/assets", "references", ".ingest", "notebook"):
            os.makedirs(os.path.join(self.workspace, relative), exist_ok=True)

        source_path = os.path.join(self.workspace, "materials", "ch1.pdf")
        with open(source_path, "wb") as stream:
            stream.write(b"%PDF-1.4\n% disposable test source\n")
        self.attempt_path = "references/assets/attempt.png"
        with open(
            os.path.join(self.workspace, *self.attempt_path.split("/")), "wb"
        ) as stream:
            stream.write(PNG_1X1)
        self.source = SourceRecord.from_file(
            self.workspace, "materials/ch1.pdf", "application/pdf", status="parsed"
        )
        self.crop_receipts = []
        self.prompt_asset = self._make_crop_asset(
            "q1", "prompt", "question_context", "full_prompt", 2, "q1_prompt"
        )
        self.answer_asset = self._make_crop_asset(
            "q1", "answer", "answer_context", "full_answer", 3, "q1_answer"
        )
        self.prompt_path = self.prompt_asset["path"]
        self.answer_path = self.answer_asset["path"]
        attempt_sha = file_sha256(
            os.path.join(self.workspace, *self.attempt_path.split("/"))
        )

        concept = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "text",
            "Velocity is distance divided by time.",
            1,
            ordinal=0,
            chapter_id="ch01",
            metadata={"source_language": "en"},
        )
        formula = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "formula",
            "",
            1,
            ordinal=1,
            chapter_id="ch01",
            latex=r"v=\frac{d}{t}",
            metadata={"source_language": "zxx"},
        )
        question = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "question",
            "A car travels 100 m in 20 s. Find its speed.",
            2,
            ordinal=0,
            external_id="q1",
            chapter_id="ch01",
            metadata={
                "source_type": "homework",
                "source": "material",
                "source_language": "en",
                "question_text_status": "page_reference",
                "requires_assets": True,
                "assets": [copy.deepcopy(self.prompt_asset)],
            },
        )
        answer = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "answer",
            "5 m/s",
            3,
            ordinal=0,
            external_id="q1",
            chapter_id="ch01",
            metadata={
                "source_type": "homework",
                "source": "material",
                "source_language": "en",
                "assets": [copy.deepcopy(self.answer_asset)],
            },
        )
        question = question.with_pair(answer.unit_id)
        answer = answer.with_pair(question.unit_id)
        attempt = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "figure",
            "",
            4,
            ordinal=0,
            external_id="q1",
            chapter_id="ch01",
            asset_path=self.attempt_path,
            asset_role="student_attempt",
            metadata={"asset_sha256": attempt_sha},
        )
        self.units = [concept, formula, question, answer, attempt]
        self.concept_id = concept.unit_id
        self.formula_id = formula.unit_id
        self.question_id = question.unit_id
        self.answer_id = answer.unit_id

        write_json(
            os.path.join(self.workspace, "study_state.json"),
            {"schema_version": 1, "language": "bilingual"},
        )
        self.teaching = [
            {
                "id": "q1",
                "chapter": 1,
                "source_type": "homework",
                "source_file": "materials/ch1.pdf",
                "source_pages": [2],
                "answer_source_file": "materials/ch1.pdf",
                "answer_source_pages": [3],
                "question_text_status": "page_reference",
                "requires_assets": True,
                "assets": [
                    copy.deepcopy(self.prompt_asset),
                    copy.deepcopy(self.answer_asset),
                    {
                        "path": self.attempt_path,
                        "role": "student_attempt",
                        "type": "crop_image",
                        "sha256": attempt_sha,
                    },
                ],
            }
        ]
        write_json(
            os.path.join(self.workspace, "references", "teaching_examples.json"),
            self.teaching,
        )
        write_json(os.path.join(self.workspace, "references", "quiz_bank.json"), [])
        write_json(
            os.path.join(self.workspace, ".ingest", "source_manifest.json"),
            {"schema_version": 1, "sources": [self.source.to_dict()]},
        )
        self._write_units()
        write_json(
            os.path.join(self.workspace, ".ingest", "build_manifest.json"),
            {
                "schema_version": 1,
                "pipeline_version": "ingestion-v1",
                "source_root": self.workspace,
            },
        )
        self._write_crop_report()
        write_jsonl(os.path.join(self.workspace, ".ingest", "review_queue.jsonl"), [])
        write_jsonl(os.path.join(self.workspace, ".ingest", "review_patches.jsonl"), [])
        write_jsonl(os.path.join(self.workspace, ".ingest", "source_conflicts.jsonl"), [])

    def tearDown(self):
        shutil.rmtree(self.workspace, ignore_errors=True)

    def _write_units(self):
        write_jsonl(
            os.path.join(self.workspace, ".ingest", "content_units.jsonl"),
            [unit.to_dict() if hasattr(unit, "to_dict") else unit for unit in self.units],
        )

    def _add_second_item(self):
        q1_question = self.units[2]
        q1_answer = self.units[3]
        prompt_asset = self._make_crop_asset(
            "q2", "prompt", "question_context", "full_prompt", 5, "q2_prompt"
        )
        answer_asset = self._make_crop_asset(
            "q2", "answer", "answer_context", "full_answer", 6, "q2_answer"
        )
        question_metadata = copy.deepcopy(q1_question.metadata)
        question_metadata["assets"] = [copy.deepcopy(prompt_asset)]
        answer_metadata = copy.deepcopy(q1_answer.metadata)
        answer_metadata["assets"] = [copy.deepcopy(answer_asset)]
        question = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "question",
            "A second car travels 100 m in 20 s. Find its speed.",
            5,
            ordinal=0,
            external_id="q2",
            chapter_id="ch01",
            metadata=question_metadata,
        )
        answer = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "answer",
            "5 m/s",
            6,
            ordinal=0,
            external_id="q2",
            chapter_id="ch01",
            metadata=answer_metadata,
        )
        question = question.with_pair(answer.unit_id)
        answer = answer.with_pair(question.unit_id)
        self.units.extend((question, answer))
        self._write_units()
        second = copy.deepcopy(self.teaching[0])
        second["id"] = "q2"
        second["source_pages"] = [5]
        second["answer_source_pages"] = [6]
        second["assets"] = [
            copy.deepcopy(prompt_asset), copy.deepcopy(answer_asset)
        ]
        self.teaching.append(second)
        write_json(
            os.path.join(self.workspace, "references", "teaching_examples.json"),
            self.teaching,
        )
        self._write_crop_report()

    def _packet(self):
        packet = author.prepare_packet(self.workspace, 1)
        self.assertEqual("ready", packet["status"], packet["blockers"])
        path = os.path.join(self.workspace, "notebook", "ch01.authoring-packet.json")
        write_json(path, packet)
        return packet, path

    def _annotations(self, packet):
        formula_group_id = packet["formula_groups"][0]["formula_group_id"]
        annotations = {
            "schema_version": 1,
            "chapter": 1,
            "packet_sha256": packet["packet_sha256"],
            "language": "bilingual",
            "answer_explanation_mode": packet["answer_explanation_mode"],
            "knowledge_points": [
                {
                    "id": "kp_speed",
                    "title": {"zh": "速度", "en": "Speed"},
                    "explanation": {
                        "zh": "速度表示单位时间内通过的距离。",
                        "en": "Speed is distance divided by time.",
                    },
                    "explanation_provenance": {
                        "zh": "ai_translation",
                        "en": "material",
                    },
                    "semantic_unit_ids": [self.concept_id, self.formula_id],
                    "example_ids": ["q1"],
                    "formula_group_ids": [formula_group_id],
                    "material_source_units": {"en": self.concept_id},
                }
            ],
            "formulas": [
                {
                    "formula_group_id": formula_group_id,
                    "explanation": {
                        "zh": "速度等于距离除以时间。",
                        "en": "Speed equals distance divided by time.",
                    },
                    "variables": [
                        {"symbol": "v", "meaning": {"zh": "速度", "en": "speed"}},
                        {"symbol": "d", "meaning": {"zh": "距离", "en": "distance"}},
                        {"symbol": "t", "meaning": {"zh": "时间", "en": "time"}},
                    ],
                    "applicability": {
                        "zh": "已知距离和时间并要求速度时使用。",
                        "en": "Use when distance and time are known and speed is requested.",
                    },
                }
            ],
            "walkthroughs": [
                {
                    "item_id": "q1",
                    "title": {"zh": "汽车速度例题", "en": "Car speed example"},
                    "translation": {"zh": "一辆汽车在20秒内行驶100米，求速度。"},
                    "what_asked": {"zh": "求汽车速度。", "en": "Find the car's speed."},
                    "known_quantities": [
                        {
                            "label": {"zh": "距离", "en": "distance"},
                            "symbol": "d",
                            "value": "100",
                            "unit": "m",
                        },
                        {
                            "label": {"zh": "时间", "en": "time"},
                            "symbol": "t",
                            "value": "20",
                            "unit": "s",
                        },
                    ],
                    "unknown_quantities": [
                        {"label": {"zh": "速度", "en": "speed"}, "symbol": "v"}
                    ],
                    "solution_kind": "formula",
                    "knowledge_point_ids": ["kp_speed"],
                    "knowledge_point_uses": {
                        "kp_speed": {
                            "zh": "把距离和时间代入速度公式。",
                            "en": "Substitute distance and time into the speed formula.",
                        }
                    },
                    "formula_uses": [
                        {
                            "formula_group_id": formula_group_id,
                            "why_applicable": {
                                "zh": "距离和时间均已知。",
                                "en": "Both distance and time are known.",
                            },
                            "variable_mapping": [
                                {"symbol": "v", "maps_to": {"zh": "所求速度", "en": "requested speed"}},
                                {"symbol": "d", "maps_to": {"zh": "100米", "en": "100 m"}},
                                {"symbol": "t", "maps_to": {"zh": "20秒", "en": "20 s"}},
                            ],
                            "substitution": "v=100/20",
                        }
                    ],
                    "steps": [
                        {"zh": "写出速度公式。", "en": "Write the speed formula."},
                        {"zh": "代入并计算得到5。", "en": "Substitute and calculate 5."},
                    ],
                    "answer": {"zh": "5 米/秒", "en": "5 m/s"},
                    "answer_provenance": {"zh": "ai_supplemented", "en": "material"},
                    "answer_explanation": {
                        "zh": "先确认题目给出的路程是 100 米、时间是 20 秒，要求的是速度。速度的定义是单位时间内通过的路程，所以这里适用 $v=d/t$。把数值代入可得 $v=100/20=5$ 米/秒。最后的 5 米/秒不是另一个路程，而是说明汽车平均每一秒前进 5 米，因此它正好回答了题目所问的速度。",
                        "en": "First identify the given distance of 100 m, the elapsed time of 20 s, and the requested speed. Speed means distance travelled per unit time, so the rule $v=d/t$ applies. Substituting the values gives $v=100/20=5$ m/s. The result is not another distance: it means the car travels an average of 5 metres during each second, which directly answers the question.",
                    },
                }
            ],
        }
        ai = {"zh": "ai_supplement", "en": "ai_supplement"}
        for formula in annotations["formulas"]:
            formula["explanation_provenance"] = dict(ai)
            formula["applicability_provenance"] = dict(ai)
            for variable in formula["variables"]:
                variable["meaning_provenance"] = dict(ai)
        for walkthrough in annotations["walkthroughs"]:
            walkthrough["translation_provenance"] = {
                code: "ai_translation" for code in walkthrough["translation"]
            }
            walkthrough["what_asked_provenance"] = dict(ai)
            for quantity in (
                walkthrough["known_quantities"] + walkthrough["unknown_quantities"]
            ):
                quantity["provenance"] = dict(ai)
            walkthrough["knowledge_point_uses_provenance"] = {
                kp_id: dict(ai) for kp_id in walkthrough["knowledge_point_ids"]
            }
            for formula_use in walkthrough["formula_uses"]:
                formula_use["why_applicable_provenance"] = dict(ai)
                formula_use["substitution_provenance"] = "ai_supplement"
                for mapping in formula_use["variable_mapping"]:
                    mapping["maps_to_provenance"] = dict(ai)
            walkthrough["steps_provenance"] = [
                dict(ai) for unused_step in walkthrough["steps"]
            ]
            walkthrough["answer_explanation_provenance"] = dict(ai)
            if "no_formula_reason" in walkthrough:
                walkthrough["no_formula_reason_provenance"] = dict(ai)
        return annotations

    def _annotations_for_language(self, packet, language):
        annotations = self._annotations(packet)
        annotations["language"] = language
        if language == "bilingual":
            return annotations
        target = {language}

        def project(value):
            if isinstance(value, dict):
                if value and set(value).issubset({"zh", "en"}):
                    return {
                        key: project(child) for key, child in value.items()
                        if key in target
                    }
                return {key: project(child) for key, child in value.items()}
            if isinstance(value, list):
                return [project(child) for child in value]
            return value

        annotations = project(annotations)
        annotations["language"] = language
        if language == "zh":
            annotations["knowledge_points"][0]["explanation_provenance"] = {
                "zh": "ai_supplement"
            }
        return annotations

    def _persist_and_compile(self):
        packet, packet_path = self._packet()
        annotations = self._annotations(packet)
        annotations_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-annotations.json"
        )
        write_json(annotations_path, annotations)
        bindings = author.persist_notebooks(
            self.workspace, 1, packet_path, annotations_path
        )
        bindings_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-bindings.json"
        )
        write_json(bindings_path, bindings)
        manifest, proposals, report = author.compile_manifest(
            self.workspace, 1, packet_path, annotations_path, bindings_path
        )
        manifest_path = os.path.join(
            self.workspace, "notebook", "ch01.guide.claim-draft.json"
        )
        write_json(manifest_path, manifest)
        return packet, annotations, bindings, manifest, proposals, report, manifest_path

    def test_prepare_blocks_unverified_page_reference_and_detects_drift(self):
        packet, packet_path = self._packet()
        write_json(os.path.join(self.workspace, "references", "quiz_bank.json"), [
            {"id": "foreign", "chapter": 2, "source_type": "quiz"}
        ])
        with self.assertRaisesRegex(author.AuthoringError, "drifted"):
            author._load_packet(self.workspace, packet_path, 1)

        write_json(os.path.join(self.workspace, "references", "quiz_bank.json"), [])
        teaching = copy.deepcopy(self.teaching)
        teaching[0]["assets"] = [
            asset for asset in teaching[0]["assets"] if asset["role"] != "question_context"
        ]
        write_json(
            os.path.join(self.workspace, "references", "teaching_examples.json"), teaching
        )
        unit_rows = [unit.to_dict() for unit in self.units]
        for row in unit_rows:
            if row["unit_id"] == self.question_id:
                row["metadata"]["assets"] = []
        write_jsonl(
            os.path.join(self.workspace, ".ingest", "content_units.jsonl"), unit_rows
        )
        blocked = author.prepare_packet(self.workspace, 1)
        self.assertEqual("blocked", blocked["status"])
        self.assertIn(
            "unverified_page_reference_asset",
            {row["code"] for row in blocked["blockers"]},
        )

    def test_prepare_excludes_student_attempt_and_classifies_full_prompt(self):
        packet, unused = self._packet()
        item = packet["items"][0]
        self.assertEqual("full_prompt", item["prompt_asset_mode"])
        official_paths = {
            row["path"] for row in item["prompt_assets"] + item["answer_assets"]
        }
        self.assertNotIn(self.attempt_path, official_paths)
        self.assertEqual(
            [self.prompt_path],
            [row["path"] for row in item["prompt_assets"]],
        )

    def test_page_reference_rejects_figure_only_crop(self):
        teaching = copy.deepcopy(self.teaching)
        prompt = next(
            asset for asset in teaching[0]["assets"]
            if asset["role"] == "question_context"
        )
        prompt["type"] = "crop_image"
        prompt["contains_full_prompt"] = False
        write_json(
            os.path.join(self.workspace, "references", "teaching_examples.json"), teaching
        )
        unit_rows = [unit.to_dict() for unit in self.units]
        for row in unit_rows:
            if row["unit_id"] != self.question_id:
                continue
            row_prompt = row["metadata"]["assets"][0]
            row_prompt["type"] = "crop_image"
            row_prompt["contains_full_prompt"] = False
        write_jsonl(
            os.path.join(self.workspace, ".ingest", "content_units.jsonl"), unit_rows
        )
        packet = author.prepare_packet(self.workspace, 1)
        self.assertEqual("blocked", packet["status"])
        blocker = next(
            row for row in packet["blockers"]
            if row["code"] == "unverified_page_reference_asset"
            and "question_text_statuses" in row
        )
        self.assertEqual("none", blocker["prompt_asset_mode"])

    def test_raw_source_drift_blocks_notebook_persistence(self):
        packet, packet_path = self._packet()
        annotations_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-annotations.json"
        )
        write_json(annotations_path, self._annotations(packet))
        with open(os.path.join(self.workspace, "materials", "ch1.pdf"), "ab") as stream:
            stream.write(b"drift")
        with self.assertRaisesRegex(author.AuthoringError, "source revision verification failed"):
            author.persist_notebooks(self.workspace, 1, packet_path, annotations_path)
        self.assertFalse(os.path.exists(os.path.join(self.workspace, "notebook", "ch01.md")))

    def test_raw_source_drift_blocks_compile_after_notebook_persistence(self):
        packet, packet_path = self._packet()
        annotations = self._annotations(packet)
        annotations_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-annotations.json"
        )
        write_json(annotations_path, annotations)
        bindings = author.persist_notebooks(
            self.workspace, 1, packet_path, annotations_path
        )
        bindings_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-bindings.json"
        )
        write_json(bindings_path, bindings)
        with open(os.path.join(self.workspace, "materials", "ch1.pdf"), "ab") as stream:
            stream.write(b"drift")
        with self.assertRaisesRegex(author.AuthoringError, "source revision verification failed"):
            author.compile_manifest(
                self.workspace, 1, packet_path, annotations_path, bindings_path
            )

    def test_external_build_source_root_is_the_verified_revision(self):
        external = self.workspace + "-external materials"
        try:
            os.makedirs(os.path.join(external, "materials"))
            shutil.copyfile(
                os.path.join(self.workspace, "materials", "ch1.pdf"),
                os.path.join(external, "materials", "ch1.pdf"),
            )
            write_json(
                os.path.join(self.workspace, ".ingest", "build_manifest.json"),
                {
                    "schema_version": 1,
                    "pipeline_version": "ingestion-v1",
                    "source_root": external,
                },
            )
            packet, packet_path = self._packet()
            self.assertEqual(
                os.path.abspath(external), packet["source_revisions"]["source_root"]
            )
            annotations_path = os.path.join(
                self.workspace, "notebook", "ch01.authoring-annotations.json"
            )
            write_json(annotations_path, self._annotations(packet))
            author.persist_notebooks(
                self.workspace, 1, packet_path, annotations_path
            )
            with open(
                os.path.join(self.workspace, "notebook", "ch01.md"),
                "r", encoding="utf-8",
            ) as stream:
                notebook_text = stream.read()
            expected_link = os.path.abspath(
                os.path.join(external, "materials", "ch1.pdf")
            ).replace("\\", "/").replace(" ", "%20") + "#page=2"
            self.assertIn(expected_link, notebook_text)
            with open(os.path.join(external, "materials", "ch1.pdf"), "ab") as stream:
                stream.write(b"external drift")
            with self.assertRaisesRegex(
                author.AuthoringError, "source revision verification failed"
            ):
                author.persist_notebooks(
                    self.workspace, 1, packet_path, annotations_path
                )
        finally:
            shutil.rmtree(external, ignore_errors=True)

    def test_end_to_end_full_prompt_notebooks_coverage_and_proposals(self):
        packet, annotations, bindings, manifest, proposals, report, unused = (
            self._persist_and_compile()
        )
        self.assertEqual({"q1"}, set(bindings["notebook_anchors"]))
        self.assertTrue(bindings["notebook_anchors"]["q1"])
        self.assertRegex(
            bindings["notebook_block_sha256"]["q1"], r"^[0-9a-f]{64}$"
        )
        walk = manifest["walkthroughs"][0]
        self.assertEqual(
            bindings["notebook_block_sha256"]["q1"],
            walk["notebook_block_sha256"],
        )
        self.assertNotIn("prompt_text", walk)
        self.assertEqual({"zh"}, set(walk["translation"]))
        self.assertEqual([], manifest["omissions"])
        self.assertEqual([], manifest["semantic_exclusions"])
        self.assertEqual(
            set(packet["semantic_unit_ids"]),
            set(manifest["knowledge_points"][0]["source_unit_ids"]),
        )
        self.assertEqual([r"v=\frac{d}{t}"], [
            formula["latex"] for formula in manifest["knowledge_points"][0]["formulas"]
        ])
        self.assertEqual(3, len(proposals["proposals"]))
        self.assertEqual(
            {"answer", "explanation", "latex"},
            {row["subject"]["field"] for row in proposals["proposals"]},
        )
        self.assertEqual(2, report["semantic_unit_counts"]["expected"])
        with open(
            os.path.join(self.workspace, "notebook", "ch01.md"),
            "r",
            encoding="utf-8",
        ) as stream:
            notebook_text = stream.read()
        self.assertIn("🟡 AI 补充，可能与你老师讲的不完全一致", notebook_text)
        self.assertIn("🟢 From your materials", notebook_text)

    def test_bilingual_official_walkthrough_uses_block_mirrors(self):
        self._persist_and_compile()
        with open(
            os.path.join(self.workspace, "notebook", "ch01.md"),
            "r", encoding="utf-8",
        ) as stream:
            notebook = stream.read()

        self.assertIn("## [#q1] 汽车速度例题", notebook)
        self.assertIn("> EN: **Car speed example**", notebook)
        self.assertIn("### ① 题面图\n> EN: **① Question figure**", notebook)
        self.assertIn("### ② 问什么\n> EN: **② What is being asked**", notebook)
        self.assertIn("> EN: **Question:** Find the car's speed.", notebook)
        self.assertIn(
            "> EN: **Knowledge-point use kp_speed:** "
            "Substitute distance and time into the speed formula. 🟡",
            notebook,
        )
        self.assertIn("### ⑤ 逐步演算\n> EN: **⑤ Step-by-step work**", notebook)
        self.assertIn("> EN: **Step 1:** Write the speed formula.", notebook)
        self.assertIn("> EN: **Step 2:** Substitute and calculate 5. 🟡", notebook)
        self.assertIn("> EN: **Answer:** 5 m/s 🟢", notebook)
        self.assertIn("### ⑦ 来源溯源\n> EN: **⑦ Source trace**", notebook)
        self.assertIn("> EN: [materials/ch1.pdf · PDF page 2]", notebook)
        self.assertIn("汽车速度例题 / Car speed example", notebook)
        self.assertNotIn("① 题面图 / ① Question figure", notebook)
        self.assertNotIn("答案（中文）", notebook)
        self.assertNotIn("Answer (English)", notebook)

    def test_compile_rejects_a_live_walkthrough_block_changed_after_binding(self):
        packet, packet_path = self._packet()
        annotations = self._annotations(packet)
        annotations_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-annotations.json"
        )
        write_json(annotations_path, annotations)
        bindings = author.persist_notebooks(
            self.workspace, 1, packet_path, annotations_path
        )
        bindings_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-bindings.json"
        )
        write_json(bindings_path, bindings)
        notebook_path = os.path.join(self.workspace, "notebook", "ch01.md")
        with open(notebook_path, "r", encoding="utf-8") as stream:
            notebook_text = stream.read()
        self.assertIn("Write the speed formula.", notebook_text)
        with open(notebook_path, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(
                notebook_text.replace(
                    "Write the speed formula.", "Use an unrelated method.", 1
                )
            )
        with self.assertRaisesRegex(
            author.AuthoringError, "live official walkthrough blocks"
        ):
            author.compile_manifest(
                self.workspace, 1, packet_path, annotations_path, bindings_path
            )

    def test_manifest_block_hash_detects_later_drift_but_absent_hash_is_legacy(self):
        unused_packet, unused_annotations, unused_bindings, manifest, unused_proposals, \
            unused_report, unused_paths = self._persist_and_compile()
        author.guide_content.validate_manifest(self.workspace, 1, manifest)
        notebook_path = os.path.join(self.workspace, "notebook", "ch01.md")
        with open(notebook_path, "r", encoding="utf-8") as stream:
            notebook_text = stream.read()
        self.assertIn("Write the speed formula.", notebook_text)
        with open(notebook_path, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(
                notebook_text.replace(
                    "Write the speed formula.", "Use an unrelated method.", 1
                )
            )
        with self.assertRaisesRegex(
            author.guide_content.ContentError, "notebook_block_sha256"
        ):
            author.guide_content.validate_manifest(self.workspace, 1, manifest)

        legacy = copy.deepcopy(manifest)
        for walk in legacy["walkthroughs"]:
            walk.pop("notebook_block_sha256", None)
        author.guide_content.validate_manifest(self.workspace, 1, legacy)

    def test_zh_notebook_and_compiled_markers_are_chinese_only(self):
        write_json(
            os.path.join(self.workspace, "study_state.json"),
            {"schema_version": 1, "language": "zh"},
        )
        packet, packet_path = self._packet()
        annotations = self._annotations_for_language(packet, "zh")
        annotations_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-annotations.json"
        )
        write_json(annotations_path, annotations)
        bindings = author.persist_notebooks(
            self.workspace, 1, packet_path, annotations_path
        )
        bindings_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-bindings.json"
        )
        write_json(bindings_path, bindings)
        manifest, unused_proposals, unused_report = author.compile_manifest(
            self.workspace, 1, packet_path, annotations_path, bindings_path
        )
        with open(
            os.path.join(self.workspace, "notebook", "ch01.md"),
            "r", encoding="utf-8",
        ) as stream:
            notebook_text = stream.read()
        self.assertIn("### ① 题面图", notebook_text)
        self.assertIn("题面翻译： 一辆汽车在20秒内行驶100米，求速度。 🌐", notebook_text)
        self.assertIn("🌐 AI 翻译（原文来自资料）", notebook_text)
        self.assertIn("PDF 第 2 页", notebook_text)
        self.assertNotIn("Question figure", notebook_text)
        self.assertNotIn("AI-supplemented", notebook_text)
        walk = manifest["walkthroughs"][0]
        self.assertFalse(walk["translation"]["zh"].startswith("[🟡"))
        self.assertEqual(
            {"zh": "ai_translation"}, walk["translation_provenance"])
        self.assertNotIn(r"\text{", walk["formula_uses"][0]["substitution"])
        self.assertEqual(
            "ai_supplement",
            walk["formula_uses"][0]["substitution_provenance"],
        )
        self.assertNotIn("AI-supplemented", walk["formula_uses"][0]["substitution"])

    def test_en_notebook_and_compiled_markers_are_english_only(self):
        question = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "question",
            "一辆汽车在20秒内行驶100米，求速度。",
            2,
            ordinal=0,
            external_id="q1",
            chapter_id="ch01",
            metadata={
                **copy.deepcopy(self.units[2].metadata),
                "source_language": "zh",
            },
        ).with_pair(self.answer_id)
        self.assertEqual(self.question_id, question.unit_id)
        self.units[2] = question
        self._write_units()
        write_json(
            os.path.join(self.workspace, "study_state.json"),
            {"schema_version": 1, "language": "en"},
        )
        packet, packet_path = self._packet()
        annotations = self._annotations_for_language(packet, "en")
        annotations["walkthroughs"][0]["translation"] = {
            "en": "A car travels 100 metres in 20 seconds. Find its speed."
        }
        annotations["walkthroughs"][0]["translation_provenance"] = {
            "en": "ai_translation"
        }
        annotations_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-annotations.json"
        )
        write_json(annotations_path, annotations)
        bindings = author.persist_notebooks(
            self.workspace, 1, packet_path, annotations_path
        )
        bindings_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-bindings.json"
        )
        write_json(bindings_path, bindings)
        manifest, unused_proposals, unused_report = author.compile_manifest(
            self.workspace, 1, packet_path, annotations_path, bindings_path
        )
        with open(
            os.path.join(self.workspace, "notebook", "ch01.md"),
            "r", encoding="utf-8",
        ) as stream:
            notebook_text = stream.read()
        self.assertIn("### ① Question figure", notebook_text)
        self.assertIn(
            "Prompt translation: A car travels 100 metres in 20 seconds. "
            "Find its speed. 🌐",
            notebook_text,
        )
        self.assertIn("🌐 AI translation of material evidence", notebook_text)
        self.assertIn("PDF page 2", notebook_text)
        self.assertNotIn("题面图", notebook_text)
        self.assertNotIn("AI补充", notebook_text)
        walk = manifest["walkthroughs"][0]
        self.assertFalse(walk["translation"]["en"].startswith("[🟡"))
        self.assertEqual(
            {"en": "ai_translation"}, walk["translation_provenance"])
        self.assertNotIn(r"\text{", walk["formula_uses"][0]["substitution"])
        self.assertEqual(
            "ai_supplement",
            walk["formula_uses"][0]["substitution_provenance"],
        )
        self.assertNotIn("AI补充", walk["formula_uses"][0]["substitution"])

    def test_source_trace_uses_media_specific_location_and_pdf_fragment_only(self):
        cases = (
            (
                "materials/ch1.pdf", "application/pdf",
                "PDF 第 3 页", "PDF page 3", True,
            ),
            (
                "materials/ch1.pptx",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "PPTX 第 3 张幻灯片", "PPTX slide 3", False,
            ),
            (
                "materials/ch1.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "XLSX 第 3 个工作表", "XLSX worksheet 3", False,
            ),
            (
                "materials/ch1.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "DOCX 逻辑段 3", "DOCX logical segment 3", False,
            ),
        )
        revisions = {
            "schema_version": 1,
            "source_root": self.workspace,
            "sources": [
                {
                    "path": path,
                    "media_type": media_type,
                }
                for path, media_type, unused_zh, unused_en, unused_pdf in cases
            ],
        }
        for path, unused_media_type, zh_label, en_label, is_pdf in cases:
            if path != "materials/ch1.pdf":
                with open(
                    os.path.join(self.workspace, *path.split("/")), "wb"
                ) as stream:
                    stream.write(b"test")
            ref = {"source_file": path, "pages": [3]}
            href = author._source_trace_href(revisions, ref)
            self.assertEqual(zh_label, author._source_trace_location(revisions, ref, "zh"))
            self.assertEqual(en_label, author._source_trace_location(revisions, ref, "en"))
            self.assertEqual(is_pdf, href.endswith("#page=3"))
            if not is_pdf:
                self.assertNotIn("#page=", href)

    def test_knowledge_point_may_have_no_examples_but_items_still_need_mapping(self):
        second_concept = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "text",
            "Acceleration is the rate of change of velocity.",
            7,
            ordinal=0,
            chapter_id="ch01",
            metadata={"source_language": "en"},
        )
        self.units.append(second_concept)
        self._write_units()
        packet, packet_path = self._packet()
        annotations = self._annotations(packet)
        annotations["knowledge_points"].append({
            "id": "kp_acceleration",
            "title": {"zh": "加速度", "en": "Acceleration"},
            "explanation": {
                "zh": "加速度表示速度随时间的变化率。",
                "en": "Acceleration is the rate of change of velocity.",
            },
            "explanation_provenance": {
                "zh": "ai_translation", "en": "material",
            },
            "semantic_unit_ids": [second_concept.unit_id],
            "example_ids": [],
            "formula_group_ids": [],
            "material_source_units": {"en": second_concept.unit_id},
        })
        annotations_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-annotations.json"
        )
        write_json(annotations_path, annotations)
        bindings = author.persist_notebooks(
            self.workspace, 1, packet_path, annotations_path
        )
        bindings_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-bindings.json"
        )
        write_json(bindings_path, bindings)
        manifest, unused_proposals, unused_report = author.compile_manifest(
            self.workspace, 1, packet_path, annotations_path, bindings_path
        )
        empty_kp = next(
            row for row in manifest["knowledge_points"]
            if row["id"] == "kp_acceleration"
        )
        self.assertEqual([], empty_kp["example_ids"])
        self.assertEqual(
            {
                "zh": "材料未提供对应例题。",
                "en": "The materials do not provide a corresponding example.",
            },
            empty_kp["example_note"],
        )
        readable = author.guide_content.render_notebook_block(manifest)
        self.assertIn("材料未提供对应例题。", readable)
        self.assertIn("The materials do not provide a corresponding example.", readable)

        annotations["knowledge_points"][0]["example_ids"] = []
        write_json(annotations_path, annotations)
        with self.assertRaisesRegex(author.AuthoringError, "map every item"):
            author.persist_notebooks(
                self.workspace, 1, packet_path, annotations_path
            )

    def test_cli_prepare_persist_compile_fixture(self):
        packet_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-packet.json"
        )
        prepare_stdout = io.StringIO()
        with contextlib.redirect_stdout(prepare_stdout):
            status = author.run([
                "--workspace", self.workspace,
                "prepare", "--chapter", "1", "--output", packet_path, "--json",
            ])
        self.assertEqual(0, status)
        prepare_result = json.loads(prepare_stdout.getvalue())
        with open(packet_path, "r", encoding="utf-8") as stream:
            packet = json.load(stream)
        packet_before_template = copy.deepcopy(packet)
        author.build_annotations_template(packet)
        self.assertEqual(packet_before_template, packet)
        self.assertEqual(packet["packet_sha256"], author._packet_hash(packet))
        self.assertNotIn("annotations_template", packet)
        template_path = os.path.join(
            self.workspace, "notebook",
            "ch01.authoring-annotations.template.json",
        )
        self.assertEqual(template_path, prepare_result["annotations_template_output"])
        with open(template_path, "r", encoding="utf-8") as stream:
            template = json.load(stream)
        self.assertEqual(
            author._sha256_json(template),
            prepare_result["annotations_template_sha256"],
        )
        self.assertEqual("incomplete", template["template_status"])
        self.assertFalse(template["valid_annotations"])
        self.assertEqual(
            {row["formula_group_id"] for row in packet["formula_groups"]},
            {
                row["formula_group_id"]
                for row in template["annotations"]["formulas"]
            },
        )
        self.assertEqual(
            packet["item_ids"],
            [row["item_id"] for row in template["annotations"]["walkthroughs"]],
        )
        self.assertEqual(
            ["__ASSIGN_PACKET_SEMANTIC_UNIT_IDS__"],
            template["knowledge_point_schema_placeholder"]["semantic_unit_ids"],
        )
        with self.assertRaisesRegex(author.AuthoringError, "schema mismatch"):
            author._validate_annotations(packet, template)
        with self.assertRaises(author.AuthoringError):
            author._validate_annotations(packet, template["annotations"])
        annotations_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-annotations.json"
        )
        write_json(annotations_path, self._annotations(packet))
        bindings_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-bindings.json"
        )
        with contextlib.redirect_stdout(io.StringIO()):
            status = author.run([
                "--workspace", self.workspace,
                "persist-notebooks", "--chapter", "1",
                "--packet", packet_path,
                "--annotations", annotations_path,
                "--output", bindings_path,
                "--json",
            ])
        self.assertEqual(0, status)
        manifest_path = os.path.join(
            self.workspace, "notebook", "ch01.guide.claim-draft.json"
        )
        proposals_path = os.path.join(
            self.workspace, "notebook", "ch01.claim-proposals.json"
        )
        with contextlib.redirect_stdout(io.StringIO()):
            status = author.run([
                "--workspace", self.workspace,
                "compile", "--chapter", "1",
                "--packet", packet_path,
                "--annotations", annotations_path,
                "--notebook-bindings", bindings_path,
                "--manifest-output", manifest_path,
                "--proposals-output", proposals_path,
                "--json",
            ])
        self.assertEqual(0, status)
        with open(manifest_path, "r", encoding="utf-8") as stream:
            manifest = json.load(stream)
        self.assertEqual(["q1"], [row["item_id"] for row in manifest["walkthroughs"]])
        self.assertTrue(os.path.isfile(proposals_path))

    def test_multi_output_publication_rolls_back_second_replace_failure(self):
        first_relative = "notebook/ch01.guide.claim-draft.json"
        second_relative = "notebook/ch01.claim-proposals.json"
        first = os.path.join(self.workspace, *first_relative.split("/"))
        second = os.path.join(self.workspace, *second_relative.split("/"))
        write_json(first, {"old": True})
        calls = {"count": 0}

        def fail_second(source, destination):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("injected second replace failure")
            os.replace(source, destination)

        with mock.patch.object(author, "_replace_path", side_effect=fail_second):
            with self.assertRaisesRegex(author.AuthoringError, "cannot publish"):
                author._publish_json(
                    self.workspace,
                    [
                        (first, {"new": 1}, "first output", first_relative),
                        (second, {"new": 2}, "second output", second_relative),
                    ],
                )
        with open(first, "r", encoding="utf-8") as stream:
            self.assertEqual({"old": True}, json.load(stream))
        self.assertFalse(os.path.exists(second))

    def test_output_guard_rejects_ads_devices_and_win32_aliases(self):
        for relative in (
            "notebook/name:stream.json",
            "notebook/NUL.json",
            "notebook/bad./result.json",
        ):
            with self.subTest(relative=relative):
                with self.assertRaises(author.AuthoringError):
                    author._safe_workspace_path(
                        self.workspace, relative, "test output", output=True
                    )

    def test_cli_output_cannot_overwrite_official_notebook_files(self):
        chapter_path = os.path.join(self.workspace, "notebook", "ch01.md")
        index_path = os.path.join(self.workspace, "notebook", "index.md")
        with open(chapter_path, "w", encoding="utf-8") as stream:
            stream.write("user chapter\n")
        with open(index_path, "w", encoding="utf-8") as stream:
            stream.write("user index\n")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            status = author.run([
                "--workspace", self.workspace,
                "prepare", "--chapter", "1", "--output", chapter_path, "--json",
            ])
        self.assertEqual(1, status)
        with open(chapter_path, "r", encoding="utf-8") as stream:
            self.assertEqual("user chapter\n", stream.read())
        with open(index_path, "r", encoding="utf-8") as stream:
            self.assertEqual("user index\n", stream.read())

    def test_binding_publication_failure_rolls_back_whole_notebook_command(self):
        packet, packet_path = self._packet()
        annotations_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-annotations.json"
        )
        write_json(annotations_path, self._annotations(packet))
        binding_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-bindings.json"
        )
        with mock.patch.object(
            author, "_replace_path", side_effect=OSError("injected binding publication failure")
        ):
            with self.assertRaisesRegex(author.AuthoringError, "cannot publish"):
                author.persist_notebooks(
                    self.workspace, 1, packet_path, annotations_path,
                    binding_output=binding_path,
                )
        self.assertFalse(os.path.exists(binding_path))
        self.assertFalse(os.path.exists(os.path.join(self.workspace, "notebook", "ch01.md")))
        self.assertFalse(os.path.exists(os.path.join(self.workspace, "notebook", "index.md")))

    def test_full_prompt_requires_one_exact_supporting_asset_record(self):
        teaching = copy.deepcopy(self.teaching)
        prompt = next(
            row for row in teaching[0]["assets"]
            if row["role"] == "question_context"
        )
        prompt["type"] = "crop_image"
        prompt["contains_full_prompt"] = False
        write_json(
            os.path.join(self.workspace, "references", "teaching_examples.json"), teaching
        )
        rows = [unit.to_dict() for unit in self.units]
        for row in rows:
            if row["unit_id"] != self.question_id:
                continue
            split = row["metadata"]["assets"][0]
            split["role"] = "figure"
            split["type"] = "page_image"
            split["contains_full_prompt"] = False
        write_jsonl(os.path.join(self.workspace, ".ingest", "content_units.jsonl"), rows)
        packet = author.prepare_packet(self.workspace, 1)
        self.assertEqual("blocked", packet["status"])
        self.assertIn(
            "unverified_page_reference_asset",
            {row["code"] for row in packet["blockers"]},
        )

    def test_asset_drift_during_persist_rolls_back_notebooks(self):
        packet, packet_path = self._packet()
        annotations_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-annotations.json"
        )
        write_json(annotations_path, self._annotations(packet))
        real_add = author._official_notebook_add_entry

        def mutate_after_add(*args, **kwargs):
            result = real_add(*args, **kwargs)
            with open(
                os.path.join(self.workspace, *self.prompt_path.split("/")), "ab"
            ) as stream:
                stream.write(b"asset drift")
            return result

        with mock.patch.object(
            author, "_official_notebook_add_entry", side_effect=mutate_after_add
        ):
            with self.assertRaisesRegex(author.AuthoringError, "asset bytes drifted"):
                author.persist_notebooks(
                    self.workspace, 1, packet_path, annotations_path
                )
        self.assertFalse(os.path.exists(os.path.join(self.workspace, "notebook", "ch01.md")))
        self.assertFalse(os.path.exists(os.path.join(self.workspace, "notebook", "index.md")))

    def test_publication_asset_end_gate_restores_prior_output(self):
        packet, unused_packet_path = self._packet()
        output_relative = "notebook/ch01.guide.claim-draft.json"
        output_path = os.path.join(self.workspace, *output_relative.split("/"))
        write_json(output_path, {"old": True})

        def replace_then_drift(source, destination):
            os.replace(source, destination)
            with open(
                os.path.join(self.workspace, *self.prompt_path.split("/")), "ab"
            ) as stream:
                stream.write(b"publication drift")

        with mock.patch.object(author, "_replace_path", side_effect=replace_then_drift):
            with self.assertRaisesRegex(author.AuthoringError, "asset bytes drifted"):
                author._publish_json(
                    self.workspace,
                    [(output_path, {"new": True}, "manifest output", output_relative)],
                    expected_asset_revisions=author._packet_asset_revisions(packet),
                )
        with open(output_path, "r", encoding="utf-8") as stream:
            self.assertEqual({"old": True}, json.load(stream))

    def test_publication_source_end_gate_restores_prior_output(self):
        packet, unused_packet_path = self._packet()
        output_relative = "notebook/ch01.guide.claim-draft.json"
        output_path = os.path.join(self.workspace, *output_relative.split("/"))
        write_json(output_path, {"old": True})

        def replace_then_drift(source, destination):
            os.replace(source, destination)
            with open(os.path.join(self.workspace, "materials", "ch1.pdf"), "ab") as stream:
                stream.write(b"source publication drift")

        with mock.patch.object(author, "_replace_path", side_effect=replace_then_drift):
            with self.assertRaisesRegex(author.AuthoringError, "source bytes drifted after"):
                author._publish_json(
                    self.workspace,
                    [(output_path, {"new": True}, "manifest output", output_relative)],
                    expected_snapshot_sha256=packet["source_snapshot_sha256"],
                    expected_source_revisions_sha256=packet["source_revisions_sha256"],
                )
        with open(output_path, "r", encoding="utf-8") as stream:
            self.assertEqual({"old": True}, json.load(stream))

    def test_manifest_asset_revisions_include_semantic_source_refs(self):
        unused_packet, unused_annotations, unused_bindings, manifest, unused_proposals, unused_report, unused_path = (
            self._persist_and_compile()
        )
        manifest["knowledge_points"][0]["formulas"][0]["source_refs"][0][
            "asset_path"
        ] = self.prompt_path
        revisions = author._manifest_asset_revisions(self.workspace, manifest)
        self.assertIn(
            self.prompt_path,
            {row["path"] for row in revisions},
        )

    def test_notebook_batch_holds_one_mutation_lock_for_all_items(self):
        self._add_second_item()
        packet, packet_path = self._packet()
        annotations = self._annotations(packet)
        annotations["knowledge_points"][0]["example_ids"] = ["q1", "q2"]
        second = copy.deepcopy(annotations["walkthroughs"][0])
        second["item_id"] = "q2"
        annotations["walkthroughs"].append(second)
        annotations_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-annotations.json"
        )
        write_json(annotations_path, annotations)
        real_lock = author.workspace_publication_lock
        real_add = author._official_notebook_add_entry
        state = {"active": False, "entries": 0}

        @contextlib.contextmanager
        def tracking_lock(workspace):
            self.assertFalse(state["active"])
            with real_lock(workspace):
                state["active"] = True
                try:
                    yield
                finally:
                    state["active"] = False

        def guarded_add(*args, **kwargs):
            self.assertTrue(state["active"])
            state["entries"] += 1
            return real_add(*args, **kwargs)

        with mock.patch.object(author, "workspace_publication_lock", tracking_lock), mock.patch.object(
            author, "_official_notebook_add_entry", side_effect=guarded_add
        ):
            author.persist_notebooks(
                self.workspace, 1, packet_path, annotations_path
            )
        self.assertEqual(2, state["entries"])
        self.assertFalse(state["active"])

    def test_compile_rechecks_asset_bytes_at_end(self):
        packet, packet_path = self._packet()
        annotations = self._annotations(packet)
        annotations_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-annotations.json"
        )
        write_json(annotations_path, annotations)
        bindings = author.persist_notebooks(
            self.workspace, 1, packet_path, annotations_path
        )
        bindings_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-bindings.json"
        )
        write_json(bindings_path, bindings)
        real_validate = author.guide_content.validate_manifest

        def validate_then_drift(*args, **kwargs):
            result = real_validate(*args, **kwargs)
            with open(
                os.path.join(self.workspace, *self.answer_path.split("/")), "ab"
            ) as stream:
                stream.write(b"compile drift")
            return result

        with mock.patch.object(
            author.guide_content, "validate_manifest", side_effect=validate_then_drift
        ):
            with self.assertRaisesRegex(author.AuthoringError, "asset bytes drifted"):
                author.compile_manifest(
                    self.workspace, 1, packet_path, annotations_path, bindings_path
                )

    def test_reasoning_provenance_is_required_and_ai_is_visibly_labeled(self):
        packet, packet_path = self._packet()
        annotations = self._annotations(packet)
        del annotations["formulas"][0]["explanation_provenance"]
        annotations_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-annotations.json"
        )
        write_json(annotations_path, annotations)
        with self.assertRaisesRegex(author.AuthoringError, "explanation_provenance"):
            author.persist_notebooks(
                self.workspace, 1, packet_path, annotations_path
            )

        annotations = self._annotations(packet)
        annotations["walkthroughs"][0]["steps"][0]["en"] = "5 m/s"
        annotations["walkthroughs"][0]["steps_provenance"][0] = {
            "zh": "ai_translation", "en": "material",
        }
        write_json(annotations_path, annotations)
        with self.assertRaisesRegex(author.AuthoringError, "no exact claim route"):
            author.persist_notebooks(
                self.workspace, 1, packet_path, annotations_path
            )

        annotations = self._annotations(packet)
        write_json(annotations_path, annotations)
        bindings = author.persist_notebooks(
            self.workspace, 1, packet_path, annotations_path
        )
        bindings_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-bindings.json"
        )
        write_json(bindings_path, bindings)
        manifest, unused_proposals, unused_report = author.compile_manifest(
            self.workspace, 1, packet_path, annotations_path, bindings_path
        )
        formula = manifest["knowledge_points"][0]["formulas"][0]
        self.assertFalse(formula["explanation"]["en"].startswith("[🟡"))
        self.assertEqual(
            annotations["formulas"][0]["explanation_provenance"],
            formula["explanation_provenance"],
        )
        self.assertEqual(
            annotations["formulas"][0]["applicability_provenance"],
            formula["applicability_provenance"],
        )
        self.assertEqual(
            annotations["formulas"][0]["variables"][0]["meaning_provenance"],
            formula["variables"][0]["meaning_provenance"],
        )
        walk = manifest["walkthroughs"][0]
        self.assertFalse(walk["translation"]["zh"].startswith("[🟡"))
        self.assertFalse(walk["what_asked"]["en"].startswith("[🟡"))
        self.assertFalse(walk["known_quantities"][0]["label"]["en"].startswith("[🟡"))
        self.assertEqual(
            annotations["walkthroughs"][0]["translation_provenance"],
            walk["translation_provenance"],
        )
        self.assertEqual(
            annotations["walkthroughs"][0]["what_asked_provenance"],
            walk["what_asked_provenance"],
        )
        self.assertEqual(
            annotations["walkthroughs"][0]["knowledge_point_uses_provenance"],
            walk["knowledge_point_uses_provenance"],
        )
        self.assertEqual(
            annotations["walkthroughs"][0]["known_quantities"][0]["provenance"],
            walk["known_quantities"][0]["provenance"],
        )
        formula_use = walk["formula_uses"][0]
        self.assertEqual(
            annotations["walkthroughs"][0]["formula_uses"][0][
                "why_applicable_provenance"],
            formula_use["why_applicable_provenance"],
        )
        self.assertEqual(
            annotations["walkthroughs"][0]["formula_uses"][0][
                "variable_mapping"][0]["maps_to_provenance"],
            formula_use["variable_mapping"][0]["maps_to_provenance"],
        )
        self.assertEqual("ai_supplement", formula_use["substitution_provenance"])
        self.assertNotIn(r"\text{", formula_use["substitution"])
        self.assertEqual(
            annotations["walkthroughs"][0]["steps_provenance"],
            walk["steps_provenance"],
        )
        self.assertNotIn("self_check", walk)
        self.assertNotIn("self_check_provenance", walk)
        serialized = json.dumps(manifest, ensure_ascii=False)
        self.assertNotIn("[🟡", serialized)
        self.assertNotIn(r"\text{AI", serialized)
        claim_specs = author._claim_specs(manifest)
        claimed_text = {row["claim_text"] for row in claim_specs}
        self.assertNotIn(formula["explanation"]["en"], claimed_text)
        self.assertNotIn(walk["what_asked"]["en"], claimed_text)
        self.assertIn(walk["answer"]["en"], claimed_text)
        with open(
            os.path.join(self.workspace, "notebook", "ch01.md"),
            "r", encoding="utf-8",
        ) as stream:
            notebook_text = stream.read()
        self.assertIn("$$v=\\frac{d}{t}$$", notebook_text)
        self.assertIn("AI supplement — may differ", notebook_text)
        self.assertNotIn("Substitution: `v=100/20`", notebook_text)

    def test_author_rejects_bare_tex_in_localized_prose_without_rewriting(self):
        packet, unused_packet_path = self._packet()
        cases = (
            (
                "formula variable meaning",
                lambda annotations, text: annotations["formulas"][0]["variables"][0]
                ["meaning"].__setitem__("en", text),
                "annotations.formulas[0].variables[0].meaning.en",
            ),
            (
                "formula-use mapping prose",
                lambda annotations, text: annotations["walkthroughs"][0]
                ["formula_uses"][0]["variable_mapping"][0]["maps_to"].__setitem__(
                    "en", text
                ),
                "annotations.walkthroughs[0].formula_uses[0].variable_mapping[0].maps_to.en",
            ),
        )
        for label, mutate, expected_path in cases:
            with self.subTest(field=label, mode="bare"):
                annotations = self._annotations(packet)
                mutate(annotations, r"Use \alpha as the event label.")
                with self.assertRaises(author.AuthoringError) as stopped:
                    author._validate_annotations(packet, annotations)
                message = str(stopped.exception)
                self.assertIn(expected_path, message)
                self.assertIn(r"\alpha", message)
                self.assertIn("outside standard $...$ or $$...$$ delimiters", message)
                self.assertIn("automatic rewriting is disabled", message)

            with self.subTest(field=label, mode="delimited"):
                annotations = self._annotations(packet)
                mutate(annotations, r"Use $\alpha$ as the event label.")
                author._validate_annotations(packet, annotations)

    def test_author_rejects_used_formula_without_explicit_variable_mapping(self):
        packet, unused_packet_path = self._packet()
        annotations = self._annotations(packet)
        annotations["formulas"][0]["variables"] = []
        annotations["walkthroughs"][0]["formula_uses"][0]["variable_mapping"] = []
        with self.assertRaisesRegex(author.AuthoringError, "defines no variables"):
            author._validate_annotations(packet, annotations)

    def test_author_rejects_raw_superscript_in_localized_prose(self):
        packet, unused_packet_path = self._packet()
        annotations = self._annotations(packet)
        annotations["walkthroughs"][0]["answer_explanation"]["en"] = (
            "Verify (1-alpha)^3."
        )
        with self.assertRaisesRegex(author.AuthoringError, "unrendered math notation"):
            author._validate_annotations(packet, annotations)

    def test_material_reasoning_requires_exact_evidence_and_becomes_claim(self):
        packet, packet_path = self._packet()
        annotations = self._annotations(packet)
        walk = annotations["walkthroughs"][0]
        exact_prompt = packet["items"][0]["question_evidence"][0]["payloads"][0]["value"]
        walk["what_asked"]["en"] = exact_prompt
        walk["what_asked_provenance"] = {
            "zh": "ai_translation", "en": "material",
        }
        annotations_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-annotations.json"
        )
        write_json(annotations_path, annotations)
        bindings = author.persist_notebooks(
            self.workspace, 1, packet_path, annotations_path
        )
        bindings_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-bindings.json"
        )
        write_json(bindings_path, bindings)
        manifest, proposals, unused_report = author.compile_manifest(
            self.workspace, 1, packet_path, annotations_path, bindings_path
        )
        self.assertIn(
            "what_asked", {row["subject"]["field"] for row in proposals["proposals"]}
        )
        self.assertEqual(exact_prompt, manifest["walkthroughs"][0]["what_asked"]["en"])
        self.assertFalse(manifest["walkthroughs"][0]["what_asked"]["zh"].startswith("[🟡"))
        self.assertEqual(
            {"zh": "ai_translation", "en": "material"},
            manifest["walkthroughs"][0]["what_asked_provenance"],
        )
        manifest_path = os.path.join(
            self.workspace, "notebook", "ch01.guide.claim-draft.json"
        )
        write_json(manifest_path, manifest)
        self._claim_records(proposals)
        unused_attached, report = author.attach_claims(
            self.workspace, 1, manifest_path
        )
        self.assertEqual(4, report["bound_claim_count"])

    def test_one_formula_location_can_bind_latex_and_material_explanation_claims(self):
        formula = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "formula",
            "Speed equals distance divided by time.",
            1,
            ordinal=1,
            chapter_id="ch01",
            latex=r"v=\frac{d}{t}",
            metadata={"source_language": "en"},
        )
        self.assertEqual(self.formula_id, formula.unit_id)
        self.units[1] = formula
        self._write_units()
        packet, packet_path = self._packet()
        annotations = self._annotations(packet)
        annotations["formulas"][0]["explanation_provenance"] = {
            "zh": "ai_translation", "en": "material",
        }
        annotations_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-annotations.json"
        )
        write_json(annotations_path, annotations)
        bindings = author.persist_notebooks(
            self.workspace, 1, packet_path, annotations_path
        )
        bindings_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-bindings.json"
        )
        write_json(bindings_path, bindings)
        manifest, proposals, unused_report = author.compile_manifest(
            self.workspace, 1, packet_path, annotations_path, bindings_path
        )
        manifest_path = os.path.join(
            self.workspace, "notebook", "ch01.guide.claim-draft.json"
        )
        write_json(manifest_path, manifest)
        self._claim_records(proposals)
        attached, report = author.attach_claims(self.workspace, 1, manifest_path)
        formula_refs = attached["knowledge_points"][0]["formulas"][0]["source_refs"]
        self.assertEqual(2, len(formula_refs))
        self.assertEqual(2, len({row["claim_id"] for row in formula_refs}))
        self.assertEqual(4, report["bound_claim_count"])

    def test_same_text_and_latex_payload_preserves_field_identity_and_compiles(self):
        formula_value = r"(T\cup M\cup O)^c"
        formula = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "formula",
            formula_value,
            1,
            ordinal=1,
            chapter_id="ch01",
            latex=formula_value,
            metadata={"source_language": "zxx"},
        )
        self.assertEqual(self.formula_id, formula.unit_id)
        self.units[1] = formula
        self._write_units()

        packet, unused_annotations, unused_bindings, unused_manifest, proposals, \
            unused_report, unused_manifest_path = self._persist_and_compile()
        semantic = next(
            row for row in packet["semantic_units"]
            if row["source_unit_id"] == self.formula_id
        )
        self.assertEqual(
            [
                {"payload_field": "text", "value": formula_value},
                {"payload_field": "latex", "value": formula_value},
            ],
            semantic["payloads"],
        )
        formula_proposal = next(
            row for row in proposals["proposals"]
            if row["subject"]["entity_type"] == "formula"
            and row["subject"]["field"] == "latex"
        )
        self.assertEqual("latex", formula_proposal["payload_field"])
        self.assertEqual(formula_value, formula_proposal["claim_text"])

    def test_missing_walkthrough_annotation_prevents_notebook_writes(self):
        packet, packet_path = self._packet()
        annotations = self._annotations(packet)
        annotations["walkthroughs"] = []
        annotations_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-annotations.json"
        )
        write_json(annotations_path, annotations)
        with self.assertRaisesRegex(author.AuthoringError, "exactly cover"):
            author.persist_notebooks(self.workspace, 1, packet_path, annotations_path)
        self.assertFalse(os.path.exists(os.path.join(self.workspace, "notebook", "ch01.md")))

    def test_multi_item_notebook_failure_restores_first_write(self):
        self._add_second_item()
        packet, packet_path = self._packet()
        annotations = self._annotations(packet)
        annotations["knowledge_points"][0]["example_ids"] = ["q1", "q2"]
        second = copy.deepcopy(annotations["walkthroughs"][0])
        second["item_id"] = "q2"
        second["title"] = {"zh": "第二道速度例题", "en": "Second speed example"}
        annotations["walkthroughs"].append(second)
        annotations_path = os.path.join(
            self.workspace, "notebook", "ch01.authoring-annotations.json"
        )
        write_json(annotations_path, annotations)
        real_add = author._official_notebook_add_entry
        calls = {"count": 0}

        def fail_second(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise author.AuthoringError("injected notebook failure")
            return real_add(*args, **kwargs)

        with mock.patch.object(
            author, "_official_notebook_add_entry", side_effect=fail_second
        ):
            with self.assertRaisesRegex(author.AuthoringError, "injected notebook failure"):
                author.persist_notebooks(
                    self.workspace, 1, packet_path, annotations_path
                )
        self.assertEqual(2, calls["count"])
        self.assertFalse(os.path.exists(os.path.join(self.workspace, "notebook", "ch01.md")))
        self.assertFalse(os.path.exists(os.path.join(self.workspace, "notebook", "index.md")))

    def _claim_records(self, proposals):
        records = compile_claim_proposals(
            proposals["proposals"],
            self.units,
            [self.source],
            workspace=self.workspace,
        )
        atomic_write_jsonl(
            os.path.join(self.workspace, ".ingest", "claim_records.jsonl"),
            [record.to_dict() for record in records],
        )
        return list(records)

    def test_attach_claims_succeeds_then_rejects_alias_ambiguity(self):
        unused_packet, unused_annotations, unused_bindings, manifest, proposals, unused_report, manifest_path = (
            self._persist_and_compile()
        )
        records = self._claim_records(proposals)
        attached, claim_report = author.attach_claims(self.workspace, 1, manifest_path)
        self.assertEqual(3, claim_report["bound_claim_count"])
        self.assertEqual(3, sum(
            1 for ref in author._all_claim_refs(attached) if "claim_id" in ref
        ))

        answer = next(record for record in records if record.subject.field == "answer")
        alias = ClaimRecord.create(
            ClaimSubject(
                answer.subject.chapter_id,
                "teaching_item",
                answer.subject.entity_id,
                answer.subject.field,
                answer.subject.language,
                answer.subject.claim_index,
            ),
            answer.claim_text,
            answer.source,
            answer.quote,
        )
        records.append(alias)
        atomic_write_jsonl(
            os.path.join(self.workspace, ".ingest", "claim_records.jsonl"),
            [record.to_dict() for record in records],
        )
        with self.assertRaisesRegex(author.AuthoringError, "ambiguous"):
            author.attach_claims(self.workspace, 1, manifest_path)


if __name__ == "__main__":
    unittest.main()
