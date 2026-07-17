import copy
import tempfile
import unittest
from pathlib import Path

from scripts import ingest_course
from scripts.ingestion import atomic_write_json
from scripts.material_generation import (
    abandon_latest_runtime_recovery,
    append_runtime_recovery,
    build_pending_generation,
    build_runtime_recovery,
    complete_generation,
    complete_latest_runtime_recovery,
    expire_latest_runtime_recovery,
    json_sha256,
    material_recovery_path,
    validate_generation,
    validate_runtime_recovery_log,
)


class MaterialGenerationTest(unittest.TestCase):
    def setUp(self):
        self.raw = {
            "quiz_bank": [],
            "teaching_examples": [],
            "ingestion": {"content_units": []},
        }
        self.report = {"asset_role_promotions": [], "warnings": []}
        self.pending = build_pending_generation(
            "1" * 64,
            "2" * 64,
            self.raw,
            self.report,
            "3" * 64,
        )

    def test_pending_round_trip_binds_generation_and_complete_keeps_identity(self):
        self.assertIs(self.pending, validate_generation(
            self.pending, expected_status="pending"
        ))
        self.assertEqual(
            json_sha256({
                "quiz_rows": [],
                "teaching_rows": [],
                "content_units": [],
            }),
            self.pending["candidate_asset_policy_sha256"],
        )
        receipt = complete_generation(self.pending)
        validate_generation(receipt, expected_status="complete")
        self.assertEqual([], receipt["completion"]["recovery_logs"])
        self.assertEqual(self.pending["generation_id"], receipt["generation_id"])
        self.assertEqual("pending", self.pending["status"])

    def test_generation_schema_and_every_binding_fail_closed(self):
        mutations = {
            "unknown_key": lambda row: row.__setitem__("extra", True),
            "boolean_schema": lambda row: row.__setitem__("schema_version", True),
            "raw_path": lambda row: row["raw_input"].__setitem__("path", "raw.json"),
            "raw_hash": lambda row: row["raw_input"].__setitem__("sha256", "0" * 64),
            "report_hash": lambda row: row["parse_report"].__setitem__("sha256", "0" * 64),
            "policy_hash": lambda row: row.__setitem__(
                "candidate_asset_policy_sha256", "0" * 64
            ),
            "promotion_hash": lambda row: row.__setitem__(
                "asset_role_promotions_sha256", "0" * 64
            ),
            "promotion_count": lambda row: row.__setitem__(
                "asset_role_promotion_count", -1
            ),
            "prior_manifest": lambda row: row.__setitem__(
                "previous_build_manifest_sha256", "0" * 64
            ),
            "generation_id": lambda row: row.__setitem__(
                "generation_id", "0" * 64
            ),
            "status": lambda row: row.__setitem__("status", "running"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                value = copy.deepcopy(self.pending)
                mutate(value)
                with self.assertRaises(ValueError):
                    validate_generation(value)

    def test_schema2_successor_binds_exact_predecessor(self):
        successor = build_pending_generation(
            "1" * 64, "2" * 64, self.raw, self.report, "3" * 64,
            supersedes_generation_id=self.pending["generation_id"],
        )
        validate_generation(successor, expected_status="pending")
        self.assertEqual(2, successor["schema_version"])
        self.assertEqual(
            self.pending["generation_id"], successor["supersedes_generation_id"]
        )
        self.assertNotEqual(self.pending["generation_id"], successor["generation_id"])

    def test_runtime_recovery_log_is_generation_bound_and_closes_exactly(self):
        previous = {
            "path": "exam_runtime_receipt.json", "state": "missing",
            "sha256": None, "runtime_digest": None,
        }
        replacement = {
            "path": "exam_runtime_receipt.json", "state": "valid",
            "sha256": "4" * 64, "runtime_digest": "5" * 64,
        }
        resume = build_runtime_recovery(
            self.pending, "6" * 64, "resume", previous, replacement,
            "2026-07-16T00:00:00Z",
        )
        log = append_runtime_recovery(None, resume)
        validate_runtime_recovery_log(
            log, pending=self.pending, pending_sha256="6" * 64
        )
        completed = complete_latest_runtime_recovery(log, "7" * 64)
        self.assertEqual("completed", completed["records"][-1]["outcome"]["status"])

        supersede = build_runtime_recovery(
            self.pending, "6" * 64, "supersede", previous, replacement,
            "2026-07-16T00:00:01Z",
        )
        supersede_log = append_runtime_recovery(None, supersede)
        successor = build_pending_generation(
            "1" * 64, "2" * 64, self.raw, self.report, "3" * 64,
            supersedes_generation_id=self.pending["generation_id"],
        )
        abandoned = abandon_latest_runtime_recovery(
            supersede_log, successor["generation_id"]
        )
        self.assertEqual("abandoned", abandoned["records"][-1]["outcome"]["status"])
        self.assertEqual(
            successor["generation_id"],
            abandoned["records"][-1]["outcome"]["replacement_generation_id"],
        )
        self.assertTrue(material_recovery_path(self.pending["generation_id"]).endswith(
            self.pending["generation_id"] + ".json"
        ))

    def test_recovery_boolean_schema_versions_fail_closed(self):
        previous = {
            "path": "exam_runtime_receipt.json", "state": "missing",
            "sha256": None, "runtime_digest": None,
        }
        replacement = {
            "path": "exam_runtime_receipt.json", "state": "valid",
            "sha256": "4" * 64, "runtime_digest": "5" * 64,
        }
        recovery = build_runtime_recovery(
            self.pending, "6" * 64, "resume", previous, replacement,
            "2026-07-16T00:00:00Z",
        )
        log = append_runtime_recovery(None, recovery)
        for target in (log, log["records"][0]["authorization"]):
            broken = copy.deepcopy(log)
            if target is log:
                broken["schema_version"] = True
            else:
                broken["records"][0]["authorization"]["schema_version"] = True
            with self.assertRaises(ValueError):
                validate_runtime_recovery_log(broken)

    def test_recovery_event_and_receipt_chain_limits_are_exact(self):
        previous = {
            "path": "exam_runtime_receipt.json", "state": "missing",
            "sha256": None, "runtime_digest": None,
        }
        replacement = {
            "path": "exam_runtime_receipt.json", "state": "valid",
            "sha256": "4" * 64, "runtime_digest": "5" * 64,
        }
        log = None
        for index in range(64):
            if log is not None:
                log = expire_latest_runtime_recovery(log)
            recovery = build_runtime_recovery(
                self.pending, "6" * 64, "resume", previous, replacement,
                "2026-07-16T00:%02d:00Z" % index,
            )
            log = append_runtime_recovery(log, recovery)
        self.assertEqual(64, len(log["records"]))
        log = expire_latest_runtime_recovery(log)
        with self.assertRaisesRegex(ValueError, "bounded event limit"):
            append_runtime_recovery(log, build_runtime_recovery(
                self.pending, "6" * 64, "resume", previous, replacement,
                "2026-07-16T01:04:00Z",
            ))

        rows = [{
            "path": material_recovery_path("%064x" % index),
            "generation_id": "%064x" % index,
            "outcome": "abandoned",
            "replacement_generation_id": "%064x" % (index + 1),
        } for index in range(1, 65)]
        rows.append({
            "path": material_recovery_path(self.pending["generation_id"]),
            "generation_id": self.pending["generation_id"],
            "outcome": "completed",
            "replacement_generation_id": None,
        })
        receipt = complete_generation(self.pending, recovery_logs=rows)
        self.assertEqual(65, len(receipt["completion"]["recovery_logs"]))
        with self.assertRaisesRegex(ValueError, "invalid schema"):
            complete_generation(self.pending, recovery_logs=rows + [{
                "path": material_recovery_path("%064x" % 65),
                "generation_id": "%064x" % 65,
                "outcome": "abandoned",
                "replacement_generation_id": "%064x" % 66,
            }])

    def test_ancestor_chain_accepts_64_direct_edges_and_rejects_65_or_shortcut(self):
        previous = {
            "path": "exam_runtime_receipt.json", "state": "missing",
            "sha256": None, "runtime_digest": None,
        }
        replacement = {
            "path": "exam_runtime_receipt.json", "state": "valid",
            "sha256": "4" * 64, "runtime_digest": "5" * 64,
        }
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            generations = [self.pending]
            for index in range(65):
                predecessor = generations[-1]
                successor = build_pending_generation(
                    "1" * 64, "2" * 64, self.raw, self.report,
                    "%064x" % (index + 10),
                    supersedes_generation_id=predecessor["generation_id"],
                )
                recovery = build_runtime_recovery(
                    predecessor, "%064x" % (index + 100), "supersede",
                    previous, replacement,
                    "2026-07-16T%02d:00:00Z" % (index % 24),
                )
                log = abandon_latest_runtime_recovery(
                    append_runtime_recovery(None, recovery),
                    successor["generation_id"],
                )
                atomic_write_json(
                    workspace.joinpath(*material_recovery_path(
                        predecessor["generation_id"]
                    ).split("/")),
                    log,
                )
                generations.append(successor)

            chain = ingest_course._recovery_ancestor_chain(
                str(workspace), generations[64]
            )
            self.assertEqual(64, len(chain))
            for child, row in zip(
                    reversed(generations[1:65]), chain):
                self.assertEqual(child["generation_id"], row["child_generation_id"])
            with self.assertRaisesRegex(ValueError, "cyclic or too deep"):
                ingest_course._recovery_ancestor_chain(
                    str(workspace), generations[65]
                )

            first_path = workspace.joinpath(*material_recovery_path(
                generations[0]["generation_id"]
            ).split("/"))
            shortcut = abandon_latest_runtime_recovery(
                append_runtime_recovery(None, build_runtime_recovery(
                    generations[0], "a" * 64, "supersede", previous,
                    replacement, "2026-07-16T23:59:00Z",
                )),
                generations[64]["generation_id"],
            )
            atomic_write_json(first_path, shortcut)
            with self.assertRaisesRegex(ValueError, "ancestor audit is invalid"):
                ingest_course._recovery_ancestor_chain(
                    str(workspace), generations[64]
                )


if __name__ == "__main__":
    unittest.main()
