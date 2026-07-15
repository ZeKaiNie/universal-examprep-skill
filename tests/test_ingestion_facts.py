import unittest

from scripts.ingestion.facts import (
    CanonicalGroup,
    CompatibilityKey,
    ConflictMember,
    ConflictResolution,
    DuplicateCandidate,
    FactValidationError,
    SourceConflict,
    SourcePriority,
    UnitRevisionRef,
    validate_fact_graph,
)
from scripts.ingestion.identifiers import make_source_id
from scripts.ingestion.models import ContentUnit


SHA_A = "a" * 64
SHA_B = "b" * 64


def make_unit(text, ordinal=0, source_sha=SHA_A, source_file="materials/a.txt"):
    source_id = make_source_id(source_file)
    return ContentUnit.create(
        source_id,
        source_sha,
        source_file,
        "text",
        text,
        1,
        ordinal=ordinal,
        chapter_id="ch01",
    )


class IngestionFactsTest(unittest.TestCase):
    def test_revision_ref_binds_stable_unit_id_to_full_revision(self):
        original = make_unit("first representation")
        changed = ContentUnit.create(
            original.source_id,
            original.source_sha256,
            original.source_file,
            original.kind,
            "changed representation",
            original.page,
            ordinal=original.ordinal,
            chapter_id=original.chapter_id,
        )
        self.assertEqual(original.unit_id, changed.unit_id)
        before = UnitRevisionRef.from_unit(original)
        after = UnitRevisionRef.from_unit(changed)
        self.assertNotEqual(before.unit_sha256, after.unit_sha256)
        self.assertEqual(before, UnitRevisionRef.from_dict(before.to_dict()))

    def test_strict_candidate_id_and_schema_reject_tampering(self):
        left = make_unit("same", 1)
        right = make_unit("same", 2)
        key = CompatibilityKey("ch01", "concept", "teaching", "source_backed")
        candidate = DuplicateCandidate.create(
            "char3-dice-v1",
            "dedup-nfc-ws-v1",
            "c" * 64,
            key,
            "exact",
            1_000_000,
            920_000,
            UnitRevisionRef.from_unit(left),
            UnitRevisionRef.from_unit(right),
            "d" * 64,
            "d" * 64,
        )
        self.assertEqual(candidate, DuplicateCandidate.from_dict(candidate.to_dict()))
        tampered = candidate.to_dict()
        tampered["score_ppm"] = 999_999
        with self.assertRaises(FactValidationError):
            DuplicateCandidate.from_dict(tampered)
        unknown = candidate.to_dict()
        unknown["future"] = True
        with self.assertRaises(FactValidationError):
            DuplicateCandidate.from_dict(unknown)

    def test_exact_group_accepts_connected_evidence_not_quadratic_pairs(self):
        units = [make_unit("identical", ordinal) for ordinal in range(3)]
        refs = [UnitRevisionRef.from_unit(unit) for unit in units]
        key = CompatibilityKey("ch01", "concept", "teaching", "source_backed")
        fingerprint = "e" * 64
        candidates = [
            DuplicateCandidate.create(
                "char3-dice-v1", "dedup-nfc-ws-v1", "f" * 64, key,
                "exact", 1_000_000, 920_000, refs[0], refs[index],
                fingerprint, fingerprint,
            )
            for index in (1, 2)
        ]
        group = CanonicalGroup.create(
            "exact_auto", "dedup-nfc-ws-v1", key, refs,
            fingerprint_sha256=fingerprint,
        )
        result = validate_fact_graph(
            candidates, [group], unit_index={unit.unit_id: unit for unit in units}
        )
        self.assertEqual(3, result["grouped_unit_count"])
        with self.assertRaises(FactValidationError):
            validate_fact_graph(
                candidates[:1], [group], unit_index={unit.unit_id: unit for unit in units}
            )

    def test_priorities_and_conflicts_never_imply_a_silent_winner(self):
        left = make_unit("the answer is 10", 1)
        right = make_unit("the answer is 11", 2, SHA_B, "materials/b.txt")
        with self.assertRaises(FactValidationError):
            SourcePriority.create(left.source_id, left.source_sha256, rank=90, tier="teacher_official")
        unknown = SourcePriority.create(left.source_id, left.source_sha256)
        self.assertEqual(0, unknown.rank)
        self.assertEqual("unspecified", unknown.basis)

        key = CompatibilityKey("ch01", "concept", "teaching", "source_backed")
        candidate = DuplicateCandidate.create(
            "char3-dice-v1", "dedup-nfc-casefold-ws-v1", "c" * 64, key,
            "near", 950_000, 920_000,
            UnitRevisionRef.from_unit(left), UnitRevisionRef.from_unit(right),
            "1" * 64, "2" * 64, ("numeric_mismatch",),
        )
        members = (
            ConflictMember(UnitRevisionRef.from_unit(left), "1" * 64, None, None, 0, "unspecified"),
            ConflictMember(UnitRevisionRef.from_unit(right), "2" * 64, None, None, 0, "unspecified"),
        )
        conflict = SourceConflict.create(
            candidate.candidate_id, "numeric_mismatch", members, ("numeric_mismatch",)
        )
        self.assertEqual("unresolved", conflict.status)
        self.assertIsNone(conflict.resolution)
        reprioritized_members = (
            ConflictMember(UnitRevisionRef.from_unit(left), "1" * 64, None, None, 95, "user"),
            ConflictMember(UnitRevisionRef.from_unit(right), "2" * 64, None, None, 10, "review"),
        )
        reprioritized = SourceConflict.create(
            candidate.candidate_id,
            "numeric_mismatch",
            reprioritized_members,
            ("numeric_mismatch",),
        )
        self.assertEqual(conflict.conflict_id, reprioritized.conflict_id)
        self.assertEqual("unresolved", reprioritized.status)
        invalid = conflict.to_dict()
        invalid["status"] = "resolved_preferred"
        with self.assertRaises(FactValidationError):
            SourceConflict.from_dict(invalid)

        resolution = ConflictResolution(
            "patch_" + "3" * 64,
            "prefer_source",
            left.unit_id,
            "Reviewed against the teacher answer key.",
        )
        resolved = SourceConflict.create(
            candidate.candidate_id,
            "numeric_mismatch",
            members,
            ("numeric_mismatch",),
            status="resolved_preferred",
            resolution=resolution,
        )
        self.assertEqual(left.unit_id, resolved.resolution.preferred_unit_id)


if __name__ == "__main__":
    unittest.main()
