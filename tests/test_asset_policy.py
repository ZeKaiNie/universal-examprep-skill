import hashlib
import os
import tempfile
import unittest
from unittest import mock

import scripts.asset_policy as asset_policy_module
from scripts.asset_policy import (
    audit_asset_policy,
    is_student_attempt_tainted,
    physical_asset_key,
    student_attempt_tainted_keys,
    workspace_asset_is_student_attempt,
)


class AssetPolicyTest(unittest.TestCase):
    @staticmethod
    def unit(unit_id, kind, *, external_id=None, chapter_id="ch01",
             paired_unit_id=None, role=None, path=None, assets=None):
        row = {
            "unit_id": unit_id,
            "kind": kind,
            "external_id": external_id,
            "chapter_id": chapter_id,
            "paired_unit_id": paired_unit_id,
        }
        if path is not None or role is not None:
            row.update(asset_path=path, asset_role=role)
        if assets is not None:
            row["metadata"] = {"assets": assets}
        return row

    def test_physical_key_rejects_unsafe_or_noncanonical_paths(self):
        for value in (
            "../x.png", "a/../x.png", "a\\..\\x.png",
            "C:/x.png", "C:\\x.png", "//server/x.png", "\\\\server\\x.png",
            "https://example.test/x.png", "a//x.png",
            "./x.png", "/x.png", "x.png\x00tail",
            "references/assets/x.png.", "references/assets./x.png",
            "references/assets/x.png ", "references/NUL/x.png",
            "references/assets/COM1.png", "references/assets/x?.png",
        ):
            with self.subTest(value=value):
                self.assertIsNone(physical_asset_key(value))

    def test_safe_slash_and_backslash_aliases_share_taint_identity(self):
        tainted = student_attempt_tainted_keys([{"assets": [{
            "path": "references\\assets\\X.png",
            "role": "student_attempt",
        }]}])
        self.assertTrue(is_student_attempt_tainted(
            "references/assets/X.png", tainted
        ))

    def test_physical_key_is_host_case_aware(self):
        upper = physical_asset_key("references/assets/X.png")
        lower = physical_asset_key("references/assets/x.png")
        if os.name == "nt":
            self.assertEqual(upper, lower)
        else:
            self.assertNotEqual(upper, lower)

    def test_collects_attempt_taint_from_top_level_and_nested_assets(self):
        rows = [
            {
                "asset_path": "references/assets/top.png",
                "asset_role": "student_attempt",
                "metadata": {"assets": [{
                    "path": "references/assets/nested.png",
                    "role": "student_attempt",
                }]},
            },
            {"assets": [{
                "path": "references/assets/official.png",
                "role": "question_context",
            }]},
        ]
        tainted = student_attempt_tainted_keys(rows)
        self.assertTrue(is_student_attempt_tainted(
            "references/assets/top.png", tainted
        ))
        self.assertTrue(is_student_attempt_tainted(
            "references/assets/nested.png", tainted
        ))
        self.assertFalse(is_student_attempt_tainted(
            "references/assets/official.png", tainted
        ))

    def test_workspace_identity_capture_is_deduplicated_and_rechecked(self):
        asset = {
            "path": "references/assets/shared.png",
            "role": "question_context",
        }
        quiz = {"id": "q1", "chapter": 1, "assets": [dict(asset)]}
        teaching = {"id": "q1", "chapter": 1, "assets": [dict(asset)]}
        unit = self.unit(
            "unit_q", "question", external_id="q1", chapter_id="ch01",
            path=asset["path"], role=asset["role"], assets=[dict(asset)],
        )
        identity = ("file", 1, 2)
        with mock.patch.object(
                asset_policy_module, "_asset_workspace_root", return_value="root"), \
                mock.patch.object(
                    asset_policy_module, "_stable_workspace_asset_identity",
                    return_value=(identity, None),
                ) as capture:
            audit_asset_policy(
                quiz_rows=[quiz], teaching_rows=[teaching], content_units=[unit],
                workspace="workspace",
            )
        # One initial capture for the unique lexical path plus one end-of-snapshot
        # drift recheck, independent of the number of repeated declarations.
        self.assertEqual(2, capture.call_count)

    def test_workspace_audit_collapses_hardlink_aliases_before_tainting(self):
        with tempfile.TemporaryDirectory() as workspace:
            asset_dir = os.path.join(workspace, "references", "assets")
            os.makedirs(asset_dir)
            official_path = os.path.join(asset_dir, "official.png")
            attempt_path = os.path.join(asset_dir, "attempt.png")
            with open(official_path, "wb") as stream:
                stream.write(b"same physical evidence")
            try:
                os.link(official_path, attempt_path)
            except (OSError, NotImplementedError):
                self.skipTest("hard links are unavailable")

            official = self.unit(
                "unit_official", "figure", chapter_id="ch01",
                path="references/assets/official.png", role="figure",
            )
            attempt = {
                "id": "student-upload", "chapter": 2,
                "assets": [{
                    "path": "references/assets/attempt.png",
                    "role": "student_attempt",
                }],
            }
            result = audit_asset_policy(
                quiz_rows=[attempt], content_units=[official],
                workspace=workspace,
            )
            self.assertEqual([], result["invalid_declarations"])
            self.assertTrue(any(
                "student_attempt-tainted" in message
                for message in result["conflicts"]
            ))
            self.assertTrue(is_student_attempt_tainted(
                "references/assets/attempt.png", result["tainted_keys"]
            ))
            self.assertTrue(is_student_attempt_tainted(
                "references/assets/official.png", result["tainted_keys"]
            ))

    def test_workspace_audit_collapses_prompt_answer_hardlink_aliases(self):
        with tempfile.TemporaryDirectory() as workspace:
            asset_dir = os.path.join(workspace, "references", "assets")
            os.makedirs(asset_dir)
            prompt_file = os.path.join(asset_dir, "prompt.png")
            answer_file = os.path.join(asset_dir, "answer.png")
            with open(prompt_file, "wb") as stream:
                stream.write(b"same physical prompt and answer")
            try:
                os.link(prompt_file, answer_file)
            except (OSError, NotImplementedError):
                self.skipTest("hard links are unavailable")

            question = self.unit(
                "unit_q", "question", external_id="q1", chapter_id="ch01",
                paired_unit_id="unit_a", path="references/assets/prompt.png",
                role="figure",
            )
            answer = self.unit(
                "unit_a", "answer", external_id="q1", chapter_id="ch01",
                paired_unit_id="unit_q", path="references/assets/answer.png",
                role="worked_solution",
            )
            result = audit_asset_policy(
                content_units=[question, answer], workspace=workspace
            )
            self.assertEqual([], result["invalid_declarations"])
            self.assertTrue(any(
                "both prompt and official answer" in message
                for message in result["conflicts"]
            ))

    def test_live_identity_taints_an_undeclared_markdown_hardlink_alias(self):
        with tempfile.TemporaryDirectory() as workspace:
            asset_dir = os.path.join(workspace, "references", "assets")
            os.makedirs(asset_dir)
            attempt_file = os.path.join(asset_dir, "attempt.png")
            alias_file = os.path.join(asset_dir, "markdown-alias.png")
            with open(attempt_file, "wb") as stream:
                stream.write(b"student work")
            try:
                os.link(attempt_file, alias_file)
            except (OSError, NotImplementedError):
                self.skipTest("hard links are unavailable")
            result = audit_asset_policy(
                quiz_rows=[{
                    "id": "attempt", "chapter": 2, "assets": [{
                        "path": "references/assets/attempt.png",
                        "role": "student_attempt",
                    }],
                }],
                workspace=workspace,
            )
            self.assertNotIn(
                physical_asset_key("references/assets/markdown-alias.png"),
                result["tainted_keys"],
            )
            self.assertTrue(workspace_asset_is_student_attempt(
                "references/assets/markdown-alias.png", workspace, result
            ))

    def test_workspace_audit_rejects_nested_asset_revision_drift(self):
        with tempfile.TemporaryDirectory() as workspace:
            asset_dir = os.path.join(workspace, "references", "assets")
            os.makedirs(asset_dir)
            payload = b"original source-backed figure"
            asset_file = os.path.join(asset_dir, "concept.png")
            with open(asset_file, "wb") as stream:
                stream.write(payload)
            unit = self.unit("concept", "figure", chapter_id="ch01")
            unit["metadata"] = {"assets": [{
                "path": "references/assets/concept.png",
                "role": "figure",
                "sha256": hashlib.sha256(payload).hexdigest(),
            }]}
            clean = audit_asset_policy(content_units=[unit], workspace=workspace)
            self.assertEqual([], clean["invalid_declarations"])
            self.assertEqual([], clean["conflicts"])

            with open(asset_file, "wb") as stream:
                stream.write(b"replaced image bytes")
            drifted = audit_asset_policy(content_units=[unit], workspace=workspace)
            self.assertTrue(any(
                "asset revision drift" in message
                for message in drifted["conflicts"]
            ))

    def test_audit_materializes_generators_and_reports_all_malformed_assets(self):
        class AssetObject:
            path = "references/assets/object.png"
            role = "figure"

        rows = ({
            "id": "q1", "chapter": 1,
            "asset_path": "references/assets/top.png",
            # Missing top-level role plus four independently malformed nested entries.
            "assets": [
                "not-an-object",
                {"role": "figure"},
                {"path": "references/assets/missing-role.png"},
                {"path": "references/assets/unknown.png", "role": "mystery"},
                {"path": "references/assets/non-string.png", "role": 7},
                AssetObject(),
            ],
        } for _ in range(1))
        result = audit_asset_policy(quiz_rows=rows)
        messages = "\n".join(result["invalid_declarations"])
        self.assertIn("complete pair", messages)
        self.assertIn("must be an object", messages)
        self.assertIn("path is missing", messages)
        self.assertIn("role is missing", messages)
        self.assertIn("'mystery'", messages)
        self.assertIn("7", messages)
        self.assertGreaterEqual(messages.count("must be an object"), 2)

    def test_source_chapter_and_phase_contradiction_fails_closed(self):
        result = audit_asset_policy(quiz_rows=[{
            "id": "q1", "chapter": 1, "phase": 2,
        }])
        self.assertTrue(any(
            "contradictory chapter/phase" in message
            for message in result["conflicts"]
        ))

    def test_phase_id_is_not_chapter_but_must_agree_within_a_pair(self):
        standalone = self.unit("u1", "figure", chapter_id="ch05")
        standalone["phase_id"] = "phase01"
        self.assertEqual([], audit_asset_policy(content_units=[standalone])["conflicts"])

        question = self.unit(
            "q", "question", external_id="q1", chapter_id="ch05",
            paired_unit_id="a",
        )
        answer = self.unit(
            "a", "answer", external_id="q1", chapter_id="ch05",
            paired_unit_id="q",
        )
        question["phase_id"] = "phase01"
        answer["phase_id"] = "phase01"
        self.assertEqual([], audit_asset_policy(
            content_units=[question, answer]
        )["conflicts"])
        answer["phase_id"] = "phase02"
        self.assertTrue(any(
            "contradictory phase IDs" in message
            for message in audit_asset_policy(
                content_units=[question, answer]
            )["conflicts"]
        ))

    def test_source_asset_evidence_requires_stable_item_and_chapter_identity(self):
        asset = [{"path": "references/assets/prompt.png", "role": "figure"}]
        missing_chapter = audit_asset_policy(quiz_rows=[{"id": "q1", "assets": asset}])
        missing_id = audit_asset_policy(quiz_rows=[{"chapter": 1, "assets": asset}])
        self.assertTrue(any("no stable chapter" in message
                            for message in missing_chapter["conflicts"]))
        self.assertTrue(any("no stable item id" in message
                            for message in missing_id["conflicts"]))

    def test_unscoped_source_item_inherits_only_one_provable_chapter(self):
        path = "references/assets/shared.png"
        source = {"id": "q1", "assets": [{"path": path, "role": "figure"}]}
        scoped_answer = self.unit(
            "unit_a", "answer", external_id="q1", chapter_id="ch01",
            role="worked_solution", path=path,
        )
        inherited = audit_asset_policy(
            quiz_rows=[source], content_units=[scoped_answer]
        )
        self.assertTrue(any("both prompt and official answer" in message
                            for message in inherited["conflicts"]))
        self.assertFalse(any("no stable chapter" in message
                             for message in inherited["conflicts"]))

        second_chapter = self.unit(
            "unit_a2", "answer", external_id="q1", chapter_id="ch02"
        )
        ambiguous = audit_asset_policy(
            quiz_rows=[source], content_units=[scoped_answer, second_chapter]
        )
        self.assertTrue(any("ambiguous item chapters" in message
                            for message in ambiguous["conflicts"]))

    def test_source_items_reject_content_unit_only_asset_roles(self):
        result = audit_asset_policy(quiz_rows=[{
            "id": "q1", "chapter": 1,
            "assets": [
                {"path": "references/assets/page.png", "role": "source_page"},
                {"path": "references/assets/other.png", "role": "other"},
            ],
        }])
        messages = "\n".join(result["invalid_declarations"])
        self.assertIn("'source_page'", messages)
        self.assertIn("'other'", messages)

    def test_legacy_source_null_assets_is_absent_but_content_metadata_null_is_invalid(self):
        source = audit_asset_policy(quiz_rows=[{
            "id": "q1", "chapter": 1, "assets": None,
        }])
        self.assertEqual([], source["invalid_declarations"])
        unit = self.unit("unit_text", "text", chapter_id="ch01")
        unit["metadata"] = {"assets": None}
        content = audit_asset_policy(content_units=[unit])
        self.assertTrue(any("metadata.assets must be an array" in message
                            for message in content["invalid_declarations"]))

    def test_untrimmed_source_item_id_cannot_evade_cross_layer_grouping(self):
        shared = "references/assets/shared.png"
        source = {
            "id": "q1 ", "chapter": 1,
            "assets": [{"path": shared, "role": "figure"}],
        }
        answer = self.unit(
            "unit_a", "answer", external_id="q1", chapter_id="ch01",
            path=shared, role="worked_solution",
        )
        result = audit_asset_policy(quiz_rows=[source], content_units=[answer])
        self.assertTrue(any("no stable item id" in message
                            for message in result["conflicts"]))

    def test_item_identity_uses_nfc_and_rejects_canonical_source_duplicates(self):
        shared = "references/assets/shared.png"
        nfc = "q\u00e9"
        nfd = "qe\u0301"
        answer = self.unit(
            "unit_a", "answer", external_id=nfd, chapter_id="ch01",
            path=shared, role="worked_solution",
        )
        cross_layer = audit_asset_policy(
            quiz_rows=[{
                "id": nfc, "chapter": 1,
                "assets": [{"path": shared, "role": "figure"}],
            }],
            content_units=[answer],
        )
        self.assertTrue(any("both prompt and official answer" in message
                            for message in cross_layer["conflicts"]))

        duplicates = audit_asset_policy(quiz_rows=[
            {"id": nfc, "chapter": 1},
            {"id": nfd, "chapter": "ch02"},
        ])
        self.assertTrue(any("canonical duplicate" in message
                            for message in duplicates["conflicts"]))

    def test_numeric_source_id_matches_string_external_id_and_bool_is_rejected(self):
        shared = "references/assets/shared.png"
        answer = self.unit(
            "unit_a", "answer", external_id="1", chapter_id="ch01",
            path=shared, role="worked_solution",
        )
        numeric = audit_asset_policy(
            quiz_rows=[{
                "id": 1, "chapter": 1,
                "assets": [{"path": shared, "role": "figure"}],
            }],
            content_units=[answer],
        )
        self.assertTrue(any("both prompt and official answer" in message
                            for message in numeric["conflicts"]))
        boolean = audit_asset_policy(quiz_rows=[{
            "id": True, "chapter": 1,
            "assets": [{"path": shared, "role": "figure"}],
        }])
        self.assertTrue(any("no stable item id" in message
                            for message in boolean["conflicts"]))

        # Preserve ingest.py's exact str(raw_id).strip() compatibility.
        distinct_floats = audit_asset_policy(quiz_rows=[
            {"id": 1, "chapter": 1},
            {"id": 1.0, "chapter": 2},
            {"id": -0.0, "chapter": 3},
            {"id": 0.0, "chapter": 4},
        ])
        self.assertFalse(any("canonical duplicate" in message
                             for message in distinct_floats["conflicts"]))
        for invalid in (float("inf"), float("-inf"), float("nan")):
            result = audit_asset_policy(quiz_rows=[{
                "id": invalid, "chapter": 1,
                "assets": [{"path": shared, "role": "figure"}],
            }])
            self.assertTrue(any("no stable item id" in message
                                for message in result["conflicts"]))

    def test_logical_item_ids_reject_internal_control_characters(self):
        asset = [{"path": "references/assets/prompt.png", "role": "figure"}]
        for value in (
            "q\x001", "q\r1", "q\n1", "q\t1", "q\x1f1", "q\x7f1", "q\x851",
        ):
            with self.subTest(layer="source", value=repr(value)):
                result = audit_asset_policy(quiz_rows=[{
                    "id": value, "chapter": 1, "assets": asset,
                }])
                self.assertTrue(any("no stable item id" in message
                                    for message in result["conflicts"]))
            with self.subTest(layer="content", value=repr(value)):
                unit = self.unit(
                    "unit_q", "question", external_id=value, chapter_id="ch01",
                    path="references/assets/prompt.png", role="figure",
                )
                result = audit_asset_policy(content_units=[unit])
                self.assertTrue(any("invalid or untrimmed external_id" in message
                                    for message in result["conflicts"]))

    def test_chapter_locator_aliases_match_across_layers(self):
        shared = "references/assets/shared.png"
        answer = self.unit(
            "unit_a", "answer", external_id="q1", chapter_id="ch01",
            path=shared, role="worked_solution",
        )
        for alias in ("ch-01", "ch_01", "chapter-01", "chapter_01"):
            with self.subTest(alias=alias):
                result = audit_asset_policy(
                    quiz_rows=[{
                        "id": "q1", "chapter": alias,
                        "assets": [{"path": shared, "role": "figure"}],
                    }],
                    content_units=[answer],
                )
                self.assertTrue(any("both prompt and official answer" in message
                                    for message in result["conflicts"]))

    def test_pair_propagates_partial_identity_and_detects_same_item_alias(self):
        question = self.unit(
            "unit_q", "question", external_id="q1", chapter_id=None,
            paired_unit_id="unit_a", role="figure",
            path="references/assets/shared.png",
        )
        answer = self.unit(
            "unit_a", "answer", external_id=None, chapter_id="ch01",
            paired_unit_id="unit_q", role="worked_solution",
            path="references\\assets\\shared.png",
        )
        result = audit_asset_policy(content_units=[question, answer])
        self.assertTrue(any(
            "both prompt and official answer" in message
            for message in result["conflicts"]
        ))

    def test_official_qa_asset_requires_identity_but_pair_can_inherit_it(self):
        path = "references/assets/answer.png"
        orphan = self.unit(
            "unit_a", "answer", external_id=None, chapter_id="ch01",
            role="worked_solution", path=path,
        )
        rejected = audit_asset_policy(content_units=[orphan])
        self.assertTrue(any("lacks a canonical external_id" in message
                            for message in rejected["conflicts"]))

        question = self.unit(
            "unit_q", "question", external_id="q1", chapter_id="ch01",
            paired_unit_id="unit_a",
        )
        orphan["paired_unit_id"] = "unit_q"
        inherited = audit_asset_policy(content_units=[question, orphan])
        self.assertFalse(any("lacks a canonical external_id" in message
                             for message in inherited["conflicts"]))

    def test_pair_rejects_contradictory_identity_kind_and_relationship(self):
        cases = {
            "external": [
                self.unit("q", "question", external_id="q1", paired_unit_id="a"),
                self.unit("a", "answer", external_id="q2", paired_unit_id="q"),
            ],
            "chapter": [
                self.unit("q", "question", external_id="q1", chapter_id="ch01",
                          paired_unit_id="a"),
                self.unit("a", "answer", external_id="q1", chapter_id="ch02",
                          paired_unit_id="q"),
            ],
            "kind": [
                self.unit("q", "question", external_id="q1", paired_unit_id="a"),
                self.unit("a", "question", external_id="q1", paired_unit_id="q"),
            ],
            "nonreciprocal": [
                self.unit("q", "question", external_id="q1", paired_unit_id="a"),
                self.unit("a", "answer", external_id="q1", paired_unit_id=None),
            ],
            "self": [
                self.unit("q", "question", external_id="q1", paired_unit_id="q"),
            ],
            "cycle": [
                self.unit("q", "question", external_id="q1", paired_unit_id="a"),
                self.unit("a", "answer", external_id="q1", paired_unit_id="q2"),
                self.unit("q2", "question", external_id="q2", paired_unit_id="q"),
            ],
        }
        for label, rows in cases.items():
            with self.subTest(label=label):
                self.assertTrue(audit_asset_policy(content_units=rows)["conflicts"])

    def test_different_items_may_reuse_official_prompt_and_answer_path(self):
        path = "references/assets/shared.png"
        result = audit_asset_policy(quiz_rows=[
            {"id": "q1", "chapter": 1, "assets": [{
                "path": path, "role": "figure",
            }]},
            {"id": "q2", "chapter": 1, "assets": [{
                "path": path, "role": "worked_solution",
            }]},
        ])
        self.assertEqual([], result["conflicts"])
        self.assertEqual([], result["invalid_declarations"])


if __name__ == "__main__":
    unittest.main()
