# -*- coding: utf-8 -*-
"""Final-boundary regressions for Study Guide target-item crop receipts."""

import contextlib
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import study_guide_content as content  # noqa: E402
import study_guide_document as document  # noqa: E402
import study_guide_render as render  # noqa: E402
from asset_crops import (  # noqa: E402
    CropReceipt,
    canonical_sha256,
    compact_asset_from_receipt,
    make_crop_spec_sha256,
)
from ingestion import make_source_id  # noqa: E402


PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63f8ffff3f0005fe02fe0def46b80000000049454e44ae426082"
)


class StudyGuideCropGateTest(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="study-guide-crop-gate-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    def _inventory(self, path, record):
        return {
            "item_assets": {"item-1": {path: [record]}},
            "unit_index": {},
        }

    @staticmethod
    def _walk(path, side):
        return [{
            "item_id": "item-1",
            "source_trace": [],
            "prompt_asset_paths": [path] if side == "prompt" else [],
            "answer_asset_paths": [path] if side == "answer" else [],
        }]

    def _install_current_receipt(self, *, side, required_context_ids=()):
        materials = os.path.join(self.ws, "materials")
        source_file = "course/ch01.pdf"
        source_path = os.path.join(materials, *source_file.split("/"))
        os.makedirs(os.path.dirname(source_path), exist_ok=True)
        source_bytes = b"exact source PDF revision"
        with open(source_path, "wb") as stream:
            stream.write(source_bytes)
        item_id = "item-1"
        role = "question_context" if side == "prompt" else "worked_solution"
        content_scope = "full_prompt" if side == "prompt" else "solution"
        isolation = (
            "target_with_required_context"
            if required_context_ids else "target_item_only"
        )
        semantic = {
            "schema_version": 2,
            "target_item_id": item_id,
            "side": side,
            "crop_sha256": hashlib.sha256(PNG).hexdigest(),
            "verdict": isolation,
            "unrelated_content_present": False,
            "student_attempt_present": False,
            "detected_item_ids": [item_id] + list(required_context_ids),
            "reviewer_kind": "model_vision",
            "reviewer": "vision-model:test",
            "reviewed_at": "2026-07-18T00:00:00Z",
            "evidence_binding_sha256": hashlib.sha256(
                b"semantic evidence").hexdigest(),
            "required_context_ids": list(required_context_ids),
        }
        spec = {
            "item_id": item_id,
            "chapter_id": "ch01",
            "side": side,
            "role": role,
            "content_scope": content_scope,
            "isolation": isolation,
            "source_id": make_source_id(source_file),
            "source_file": source_file,
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "source_page": 1,
            "page_box_pdf_points": [0, 0, 612, 792],
            "bbox_pdf_points": [20, 30, 400, 500],
            "selection_method": "model_vision",
            "selection_evidence_sha256": hashlib.sha256(
                b"selection evidence").hexdigest(),
            "renderer_id": "fixture",
            "renderer_version": "1",
            "renderer_config_sha256": canonical_sha256({
                "clip_coordinate_space": "pdf_points",
                "whole_page_fallback": False,
            }),
            "semantic_purity": semantic,
        }
        spec_sha256 = make_crop_spec_sha256(**spec)
        output_path = (
            "references/assets/item-1_crop_%s.png" % spec_sha256[:12]
        )
        receipt = CropReceipt.create(
            output_path=output_path,
            output_sha256=hashlib.sha256(PNG).hexdigest(),
            output_width=1,
            output_height=1,
            supersedes=(),
            **spec
        )
        absolute_output = os.path.join(self.ws, *output_path.split("/"))
        os.makedirs(os.path.dirname(absolute_output), exist_ok=True)
        with open(absolute_output, "wb") as stream:
            stream.write(PNG)
        rows = [receipt.to_dict()]
        ingest = os.path.join(self.ws, ".ingest")
        os.makedirs(ingest, exist_ok=True)
        with open(os.path.join(ingest, "parse_report.json"), "w",
                  encoding="utf-8") as stream:
            json.dump({
                "crop_receipts": rows,
                "crop_receipt_index_sha256": canonical_sha256(rows),
            }, stream)
        with open(os.path.join(ingest, "build_manifest.json"), "w",
                  encoding="utf-8") as stream:
            json.dump({"source_root": materials}, stream)
        return compact_asset_from_receipt(receipt)

    def test_page_shaped_prompt_cannot_use_protocol_flag_as_receipt_proof(self):
        path = "references/assets/full-page.png"
        record = {
            "path": path,
            "role": "question_context",
            "type": "page_image",
            "contains_full_prompt": True,
            "sha256": "a" * 64,
        }
        with self.assertRaisesRegex(
                content.ContentError,
                "authoring_protocol_version is not proof"):
            content._validate_v2_live_crop_receipts(
                self.ws, 1, self._walk(path, "prompt"),
                self._inventory(path, record),
            )

    def test_answer_page_crop_rejects_required_context_before_receipt_lookup(self):
        path = "references/assets/answer-crop.png"
        record = {
            "path": path,
            "role": "answer_context",
            "type": "crop_image",
            "crop_receipt_id": "crop_" + "b" * 64,
            "isolation": "target_with_required_context",
            "required_context_ids": ["context-1"],
        }
        with self.assertRaisesRegex(
                content.ContentError,
                "answer-side page crop must use isolation=target_item_only"):
            content._validate_v2_live_crop_receipts(
                self.ws, 1, self._walk(path, "answer"),
                self._inventory(path, record),
            )

    def test_prompt_context_crop_is_live_verified_through_full_receipt_api(self):
        record = self._install_current_receipt(
            side="prompt", required_context_ids=("context-1",))
        path = record["path"]
        report = content._validate_v2_live_crop_receipts(
            self.ws, 1, self._walk(path, "prompt"),
            self._inventory(path, record),
        )
        self.assertEqual("verified", report["status"])
        self.assertEqual([record["crop_receipt_id"]], report["crop_receipt_ids"])
        self.assertEqual([{
            "path": record["path"],
            "crop_receipt_id": record["crop_receipt_id"],
            "sha256": record["sha256"],
            "width": 1,
            "height": 1,
        }], report["verified_asset_bindings"])

    def test_public_import_requests_live_v2_crop_enforcement(self):
        draft = os.path.join(self.ws, "draft.json")
        with open(draft, "w", encoding="utf-8") as stream:
            json.dump({}, stream)

        def reject_at_live_gate(_ws, _chapter, _manifest, **options):
            self.assertIs(options.get("_enforce_v2_crop_receipts"), True)
            raise content.ContentError("live crop gate sentinel")

        with mock.patch.object(
                content.exam_start, "require_full_processing",
                return_value={"processing_mode": "full", "ready_to_ingest": True}), \
                mock.patch.object(
                    content, "_study_guide_mutation_lock",
                    return_value=contextlib.nullcontext()), \
                mock.patch.object(
                    content, "validate_manifest", side_effect=reject_at_live_gate), \
                self.assertRaisesRegex(content.ContentError, "live crop gate sentinel"):
            content.import_manifest(self.ws, 1, draft)

    def test_public_validate_requests_live_v2_crop_enforcement(self):
        draft = os.path.join(self.ws, "draft.json")
        with open(draft, "w", encoding="utf-8") as stream:
            json.dump({}, stream)

        def reject_at_live_gate(_ws, _chapter, _manifest, **options):
            self.assertIs(options.get("_enforce_v2_crop_receipts"), True)
            raise content.ContentError("live validate crop gate sentinel")

        with mock.patch.object(
                content.exam_start, "require_full_processing",
                return_value={"processing_mode": "full", "ready_to_ingest": True}), \
                mock.patch.object(
                    content, "validate_manifest", side_effect=reject_at_live_gate), \
                self.assertRaisesRegex(
                    content.ContentError, "live validate crop gate sentinel"):
            content.load_and_validate_manifest(self.ws, 1, draft)

    def test_typed_render_snapshot_requests_live_v2_crop_enforcement(self):
        manifest = os.path.join(self.ws, "notebook", "ch01.guide.json")
        os.makedirs(os.path.dirname(manifest), exist_ok=True)
        with open(manifest, "w", encoding="utf-8") as stream:
            json.dump({}, stream)

        def reject_at_live_gate(_ws, _chapter, _manifest, **options):
            self.assertIs(options.get("_enforce_v2_crop_receipts"), True)
            raise content.ContentError("live crop gate sentinel")

        with mock.patch.object(
                content, "validate_manifest", side_effect=reject_at_live_gate), \
                self.assertRaisesRegex(render.GuideError, "live crop gate sentinel"):
            render._load_typed_manifest_snapshot(self.ws, 1)

    def test_typed_render_rejects_self_declared_protocol_without_ingestion_v2(self):
        manifest = os.path.join(self.ws, "notebook", "ch01.guide.json")
        os.makedirs(os.path.dirname(manifest), exist_ok=True)
        with open(manifest, "w", encoding="utf-8") as stream:
            json.dump({"authoring_protocol_version": 2}, stream)
        compatibility_report = {
            "ok": True,
            "ingestion_pipeline_version": None,
            "authoring_protocol_version": 2,
        }
        with mock.patch.object(
                content, "validate_manifest",
                return_value=compatibility_report), \
                self.assertRaisesRegex(
                    render.GuideError,
                    "self-declared authoring protocol cannot replace"):
            render._load_typed_manifest_snapshot(self.ws, 1)

    def test_document_renderer_itself_requires_live_v2_crop_validation(self):
        compatibility_report = {
            "ok": True,
            "ingestion_pipeline_version": None,
        }

        def compatibility_validator(_ws, _chapter, _manifest, **options):
            self.assertIs(options.get("_enforce_v2_crop_receipts"), True)
            return compatibility_report

        with mock.patch.object(
                document.exam_start, "require_full_processing",
                return_value={"processing_mode": "full", "ready_to_ingest": True}), \
                mock.patch.object(
                    document, "validate_manifest",
                    side_effect=compatibility_validator), \
                self.assertRaisesRegex(
                    render.GuideError,
                    "document rendering requires a verified ingestion-v2"):
            document.render_manifest(self.ws, {"chapter": 1})


if __name__ == "__main__":
    unittest.main(verbosity=2)
