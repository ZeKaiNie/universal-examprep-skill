import copy
import hashlib
import os
import tempfile
import unittest
from unittest import mock

from benchmark import run_retrieval_gate as gate
from scripts import retrieval_evaluation as evaluation
from scripts.ingestion.models import ContentUnit, SourceRecord
from scripts.ingestion.storage import atomic_write_json, atomic_write_jsonl


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class MultiCourseRunnerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspaces = {}
        self.indexes = {}
        self.bundle = []
        self.queries = []
        for number, course_id in enumerate(("course-a", "course-b", "course-c"), 1):
            self._make_course(course_id, number)
        self.gold = {
            "schema_version": 1,
            "gold_id": "real-shards",
            "split": "test",
            "index_bundle": sorted(self.bundle, key=lambda row: row["course_id"]),
            "queries": sorted(self.queries, key=lambda row: row["query_id"]),
        }
        self.workspace_map = {
            "schema_version": 1,
            "courses": [{"course_id": course_id, "workspace": workspace}
                        for course_id, workspace in sorted(self.workspaces.items())],
        }

    def _make_course(self, course_id, number):
        workspace = os.path.join(self.temp.name, course_id)
        ingest = os.path.join(workspace, ".ingest")
        references = os.path.join(workspace, "references")
        os.makedirs(ingest)
        os.makedirs(references)
        source_sha = ("%02x" % number) * 32
        source = SourceRecord.create(
            "materials/%s.pdf" % course_id, source_sha, number,
            "application/pdf", status="parsed")
        relevant = ContentUnit.create(
            source.source_id, source.sha256, source.path, "text",
            "%s relevant" % course_id, 1)
        hard = ContentUnit.create(
            source.source_id, source.sha256, source.path, "text",
            "%s hard negative" % course_id, 2)
        manifest_path = os.path.join(ingest, "source_manifest.json")
        units_path = os.path.join(ingest, "content_units.jsonl")
        index_path = os.path.join(references, "retrieval_index.json")
        atomic_write_json(manifest_path, {
            "schema_version": 1, "sources": [source.to_dict()],
        })
        atomic_write_jsonl(units_path, [relevant.to_dict(), hard.to_dict()])
        index = {"version": 3, "n_docs": 2, "docs": [
            {"id": "%s-relevant" % course_id, "unit_ids": [relevant.unit_id],
             "kind": "text"},
            {"id": "%s-hard" % course_id, "unit_ids": [hard.unit_id],
             "kind": "text"},
        ]}
        atomic_write_json(index_path, index)
        self.workspaces[course_id] = workspace
        self.indexes[workspace] = index
        self.bundle.append({
            "course_id": course_id,
            "index_sha256": file_hash(index_path),
            "content_units_sha256": file_hash(units_path),
            "source_manifest_sha256": file_hash(manifest_path),
        })
        self.queries.append({
            "query_id": "q-%s" % course_id,
            "course_id": course_id,
            "query": "find %s" % course_id,
            "language": "en",
            "answerable": True,
            "relevant_unit_ids": [relevant.unit_id],
            "hard_negative_unit_ids": [hard.unit_id],
            "tags": ["paraphrase"],
            "evidence": [{
                "unit_id": relevant.unit_id,
                "source_id": source.source_id,
                "source_sha256": source.sha256,
                "page": relevant.page,
            }],
        })

    def test_workspace_map_is_strict_exact_and_cannot_reuse_one_workspace(self):
        normalized = gate.validate_workspace_map(self.workspace_map, self.gold)
        self.assertEqual(set(self.workspaces), set(normalized))
        bad = copy.deepcopy(self.workspace_map)
        bad["courses"][1]["workspace"] = bad["courses"][0]["workspace"]
        with self.assertRaisesRegex(
                evaluation.RetrievalEvaluationError, "workspace.*multiple courses"):
            gate.validate_workspace_map(bad, self.gold)

    def test_shard_loader_verifies_every_evidence_field_against_ingest_truth(self):
        loader = lambda workspace: self.indexes[workspace]
        shards = gate.load_course_shards(
            self.gold, self.workspace_map, index_loader=loader)
        self.assertEqual(set(self.workspaces), set(shards))
        bad = copy.deepcopy(self.gold)
        bad["queries"][0]["evidence"][0]["page"] = 99
        with self.assertRaisesRegex(
                evaluation.RetrievalEvaluationError, "evidence.*page"):
            gate.load_course_shards(bad, self.workspace_map, index_loader=loader)

    def test_each_query_runs_only_against_its_course_shard_and_receipt_binds_bundle(self):
        calls = []

        def search(workspace, index, query, top_k, min_score):
            course_id = os.path.basename(workspace)
            calls.append((course_id, query))
            expected = next(row for row in self.gold["queries"]
                            if row["course_id"] == course_id)
            return ([{
                "id": "%s-relevant" % course_id,
                "unit_ids": expected["relevant_unit_ids"],
                "score": 1.0,
            }], [])

        with mock.patch.object(gate.retrieve, "load_index",
                               side_effect=lambda workspace: self.indexes[workspace]), \
                mock.patch.object(gate.retrieve, "search", side_effect=search):
            receipt = gate.run_bm25_bundle(
                self.gold, self.workspace_map, repeats=2)
        self.assertEqual(6, len(calls))
        self.assertTrue(all(course_id in query for course_id, query in calls))
        self.assertEqual(
            evaluation.canonical_sha256(self.gold["index_bundle"]),
            receipt["index_bundle_sha256"])
        evaluation.evaluate(self.gold, receipt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
