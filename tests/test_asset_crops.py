"""Contract tests for deterministic, target-item PDF crop receipts."""

import copy
import hashlib
import os
import sys
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from asset_crops import (  # noqa: E402
    CropAnnotation,
    CropContractError,
    CropReceipt,
    annotation_bbox_pdf_points,
    canonical_sha256,
    compact_asset_from_receipt,
    make_crop_spec_sha256,
    render_crop_png,
    validate_crop_asset_binding,
    validate_crop_asset_declaration,
)
from ingestion import make_source_id  # noqa: E402


PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63f8ffff3f0005fe02fe0def46b80000000049454e44ae426082"
)


class CropContractsTest(unittest.TestCase):
    def annotation(self):
        return {
            "schema_version": 1,
            "item_id": "quiz-17",
            "side": "prompt",
            "role": "question_context",
            "content_scope": "full_prompt",
            "source_id": make_source_id("quiz.pdf"),
            "source_file": "quiz.pdf",
            "source_sha256": hashlib.sha256(b"source revision").hexdigest(),
            "source_page": 12,
            "preview_sha256": hashlib.sha256(b"preview revision").hexdigest(),
            "preview_width": 1000,
            "preview_height": 2000,
            "bbox_preview_pixels": [100, 400, 900, 1200],
            "selection_method": "model_vision",
            "reviewer": "vision-model:test",
        }

    def spec(self):
        return {
            "item_id": "quiz-17",
            "chapter_id": "ch01",
            "side": "prompt",
            "role": "question_context",
            "content_scope": "full_prompt",
            "isolation": "target_item_only",
            "source_id": make_source_id("quiz.pdf"),
            "source_file": "quiz.pdf",
            "source_sha256": hashlib.sha256(b"source revision").hexdigest(),
            "source_page": 12,
            "page_box_pdf_points": [0, 0, 612, 792],
            "bbox_pdf_points": [40, 130, 570, 410],
            "selection_method": "model_vision",
            "selection_evidence_sha256": hashlib.sha256(
                b"annotation revision"
            ).hexdigest(),
            "renderer_id": "pymupdf",
            "renderer_version": "1.0-test",
            "renderer_config_sha256": canonical_sha256({
                "clip_coordinate_space": "pdf_points",
                "scale": 2.0,
                "whole_page_fallback": False,
            }),
            "semantic_purity": {
                "schema_version": 2,
                "target_item_id": "quiz-17",
                "side": "prompt",
                "crop_sha256": hashlib.sha256(PNG).hexdigest(),
                "verdict": "target_item_only",
                "unrelated_content_present": False,
                "student_attempt_present": False,
                "detected_item_ids": ["quiz-17"],
                "reviewer_kind": "model_vision",
                "reviewer": "vision-model:test",
                "reviewed_at": "2026-07-18T00:00:00Z",
                "evidence_binding_sha256": hashlib.sha256(
                    b"semantic review evidence"
                ).hexdigest(),
                "required_context_ids": [],
            },
        }

    def receipt(self):
        spec = self.spec()
        spec_sha256 = make_crop_spec_sha256(**spec)
        return CropReceipt.create(
            output_path=(
                "references/assets/quiz_p012_quiz-17_crop_%s.png"
                % spec_sha256[:12]
            ),
            output_sha256=hashlib.sha256(PNG).hexdigest(),
            output_width=1,
            output_height=1,
            supersedes=("references/assets/quiz_p012_quiz-17.png",),
            **spec
        )

    def test_annotation_is_exact_preview_bound_and_hash_stable(self):
        annotation = CropAnnotation.from_dict(self.annotation())
        self.assertEqual(annotation, CropAnnotation.from_dict(annotation.to_dict()))
        self.assertEqual(annotation.annotation_sha256, canonical_sha256(annotation.to_dict()))
        mapped = annotation_bbox_pdf_points(annotation, [0, 0, 500, 1000])
        self.assertEqual([50.0, 200.0, 450.0, 600.0], mapped)

        changed = self.annotation()
        changed["bbox_preview_pixels"] = [100, 400, 901, 1200]
        self.assertNotEqual(
            annotation.annotation_sha256,
            CropAnnotation.from_dict(changed).annotation_sha256,
        )

    def test_annotation_rejects_source_drift_role_mismatch_and_bad_bbox(self):
        cases = []
        wrong_source = self.annotation()
        wrong_source["source_id"] = make_source_id("other.pdf")
        cases.append((wrong_source, "source_id"))
        wrong_role = self.annotation()
        wrong_role["role"] = "worked_solution"
        cases.append((wrong_role, "side=prompt"))
        outside = self.annotation()
        outside["bbox_preview_pixels"] = [0, 0, 1001, 2000]
        cases.append((outside, "within"))
        for payload, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                CropContractError, message
            ):
                CropAnnotation.from_dict(payload)

    def test_receipt_hashes_spec_output_and_dimensions(self):
        receipt = self.receipt()
        restored = CropReceipt.from_dict(receipt.to_dict())
        self.assertEqual(receipt, restored)
        self.assertTrue(receipt.crop_receipt_id.startswith("crop_"))
        self.assertIn("crop_%s" % receipt.crop_spec_sha256[:12], receipt.output_path)

        for field, value, message in (
            ("output_width", 2, "receipt contents"),
            ("source_page", 13, "immutable spec"),
            ("isolation", "whole_page", "one of"),
        ):
            with self.subTest(field=field):
                damaged = receipt.to_dict()
                damaged[field] = value
                with self.assertRaisesRegex(CropContractError, message):
                    CropReceipt.from_dict(damaged)

    def test_compact_binding_is_strict_but_legacy_crop_remains_readable(self):
        receipt = self.receipt()
        asset = compact_asset_from_receipt(receipt)
        asset["contains_full_prompt"] = True
        self.assertTrue(validate_crop_asset_declaration(asset))
        self.assertTrue(validate_crop_asset_binding(asset, receipt))

        damaged = copy.deepcopy(asset)
        damaged["source_page"] = receipt.source_page + 1
        with self.assertRaisesRegex(CropContractError, "source_page"):
            validate_crop_asset_binding(damaged, receipt)

        self.assertFalse(validate_crop_asset_declaration({
            "path": "references/assets/legacy.png",
            "role": "question_context",
            "type": "crop_image",
        }))
        incomplete = dict(asset)
        incomplete.pop("source_sha256")
        with self.assertRaisesRegex(CropContractError, "missing fields"):
            validate_crop_asset_declaration(incomplete)

    def test_render_crop_never_calls_whole_page_fallback(self):
        class Renderer:
            def __init__(self, payload):
                self.payload = payload
                self.clip_calls = 0
                self.full_calls = 0

            def render_page_clip_png(self, unused_path, unused_page, unused_bbox):
                self.clip_calls += 1
                return self.payload

            def render_page_png(self, unused_path, unused_page):
                self.full_calls += 1
                return PNG

        success = Renderer(PNG)
        payload, width, height = render_crop_png(
            success, "quiz.pdf", 11, [0, 0, 10, 10]
        )
        self.assertEqual(PNG, payload)
        self.assertEqual((1, 1), (width, height))
        self.assertEqual(1, success.clip_calls)
        self.assertEqual(0, success.full_calls)

        failed = Renderer(None)
        with self.assertRaisesRegex(CropContractError, "fallback is forbidden"):
            render_crop_png(failed, "quiz.pdf", 11, [0, 0, 10, 10])
        self.assertEqual(1, failed.clip_calls)
        self.assertEqual(0, failed.full_calls)


if __name__ == "__main__":
    unittest.main()
