import unittest

from scripts import chunk
from scripts.ingestion.facts import CanonicalGroup, CompatibilityKey, UnitRevisionRef
from scripts.ingestion.retrieval_folding import fold_units_for_retrieval


def unit(unit_id, source_id, text):
    return {
        "schema_version": 1,
        "unit_id": unit_id,
        "source_id": source_id,
        "source_sha256": ("a" if source_id.endswith("1") else "b") * 64,
        "source_file": source_id + ".txt",
        "kind": "text",
        "text": text,
        "html": None,
        "latex": None,
        "page": 1,
        "ordinal": 1,
        "chapter_id": "ch01",
        "phase_id": "phase-01",
        "parent_unit_id": None,
        "section_path": [],
        "asset_path": None,
        "asset_role": None,
        "paired_unit_id": None,
        "metadata": {},
        "method": "native",
        "confidence": 1.0,
        "provenance": "material",
    }


class RetrievalFoldingTest(unittest.TestCase):
    def test_exact_group_folds_display_but_preserves_every_occurrence_id(self):
        left = unit("unit_" + "1" * 64, "src_" + "1" * 64, "Same fact")
        right = unit("unit_" + "2" * 64, "src_" + "2" * 64, "Same fact")
        group = CanonicalGroup.create(
            derivation="exact_auto",
            normalizer="exact-nfc-ws-v1",
            compatibility_key=CompatibilityKey(
                source_side="teaching", kind_family="concept", chapter_id="ch01",
                provenance_class="source_backed",
            ),
            member_refs=(UnitRevisionRef.from_unit(left), UnitRevisionRef.from_unit(right)),
            display_unit_id=left["unit_id"],
            fingerprint_sha256="f" * 64,
        )
        folded = fold_units_for_retrieval([left, right], [group])
        self.assertEqual(1, len(folded))
        policy = chunk._verified_asset_policy_from_layers(
            content_units=[left, right], canonical_groups=[group]
        )
        chunks = chunk.chunk_units(folded, tainted_keys=policy)
        self.assertEqual(1, len(chunks))
        self.assertEqual(
            sorted((left["unit_id"], right["unit_id"])),
            sorted(chunks[0]["unit_ids"]),
        )


if __name__ == "__main__":
    unittest.main()
