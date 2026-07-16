import tempfile
import unittest
import hashlib
from pathlib import Path

from scripts.ingestion.dedup import (
    DedupConfig,
    build_dedup_facts,
    build_duplicate_candidates,
    build_duplicate_candidates_with_stats,
    build_source_conflict_review_artifacts,
    attach_conflict_review_issue_ids,
    compile_ingestion_facts,
    load_canonical_groups,
    load_duplicate_candidates,
    load_source_conflicts,
    load_source_priorities,
    replay_conflict_review_ledger,
    similarity_ppm,
    compatibility_key,
    validate_persisted_fact_derivation,
)
from scripts.ingestion.identifiers import make_source_id
from scripts.ingestion.facts import FactEvidenceRef, SourcePriority
from scripts.ingestion.models import ContentUnit, ReviewPatch
from scripts.ingestion.facts import FactValidationError


SHA_A = "a" * 64
SHA_B = "b" * 64
SOURCE_A = {"source_id": make_source_id("materials/a.txt"), "sha256": SHA_A}
SOURCE_B = {"source_id": make_source_id("materials/b.txt"), "sha256": SHA_B}


def unit(
    text,
    ordinal,
    source=SOURCE_A,
    source_file="materials/a.txt",
    kind="text",
    asset_path=None,
    asset_role=None,
    provenance="material",
):
    return ContentUnit.create(
        source["source_id"],
        source["sha256"],
        source_file,
        kind,
        text,
        1,
        ordinal=ordinal,
        chapter_id="ch01",
        asset_path=asset_path,
        asset_role=asset_role,
        provenance=provenance,
    )


class IngestionDedupTest(unittest.TestCase):
    def test_integer_similarity_is_deterministic(self):
        left = "A deterministic sentence about binary search trees and ordering."
        right = "A deterministic sentence about binary search trees and orderings."
        first = similarity_ppm(left, right)
        second = similarity_ppm(left, right)
        self.assertIs(type(first), int)
        self.assertEqual(first, second)
        self.assertGreater(first, 920_000)

    def test_exact_folds_but_near_only_becomes_a_candidate(self):
        exact_a = unit("Caf\u00e9   trees preserve order.", 1)
        exact_b = unit("Cafe\u0301 trees preserve order.", 2, SOURCE_B, "materials/b.txt")
        near = unit("Caf\u00e9 trees preserve orders.", 3)
        facts = build_dedup_facts((near, exact_b, exact_a), (SOURCE_A, SOURCE_B))
        exact = [row for row in facts["candidates"] if row.match_kind == "exact"]
        near_rows = [row for row in facts["candidates"] if row.match_kind == "near"]
        self.assertEqual(1, len(exact))
        self.assertEqual(1, len(near_rows))
        self.assertEqual(1, len(facts["canonical_groups"]))
        self.assertEqual(
            {exact_a.unit_id, exact_b.unit_id},
            {ref.unit_id for ref in facts["canonical_groups"][0].member_refs},
        )
        self.assertNotIn(near.unit_id, {ref.unit_id for ref in facts["canonical_groups"][0].member_refs})

    def test_compatibility_and_visual_conflicts_fail_closed(self):
        prompt_a = unit(
            "Use the diagram to identify the traversal shown in this question.",
            1,
            kind="question",
            asset_path="assets/a.png",
            asset_role="question_context",
        )
        prompt_b = unit(
            "Use the diagram to identify the traversal shown in this question.",
            2,
            SOURCE_B,
            "materials/b.txt",
            kind="question",
            asset_path="assets/b.png",
            asset_role="question_context",
        )
        answer_side = unit(
            prompt_a.text,
            3,
            kind="answer",
            asset_path="assets/solution.png",
            asset_role="worked_solution",
        )
        facts = build_dedup_facts((prompt_a, answer_side, prompt_b), (SOURCE_A, SOURCE_B))
        self.assertEqual(1, len(facts["candidates"]))
        candidate = facts["candidates"][0]
        self.assertEqual("near", candidate.match_kind)
        self.assertIn("visual_context_mismatch", candidate.conflict_signals)
        self.assertEqual(0, len(facts["canonical_groups"]))
        self.assertEqual(1, len(facts["conflicts"]))
        conflict = facts["conflicts"][0]
        self.assertEqual("unresolved", conflict.status)
        self.assertIsNone(conflict.resolution)

    def test_student_attempt_has_an_isolated_dedup_source_side(self):
        text = "The same pixels must not collapse student work into an official solution."
        attempt = unit(
            text, 20, kind="figure", asset_path="assets/attempt.png",
            asset_role="student_attempt",
        )
        official = unit(
            text, 21, kind="figure", asset_path="assets/official.png",
            asset_role="answer_context",
        )
        prompt = unit(
            text, 22, kind="figure", asset_path="assets/prompt.png",
            asset_role="question_context",
        )
        self.assertEqual("attempt", compatibility_key(attempt).source_side)
        self.assertEqual("answer", compatibility_key(official).source_side)
        self.assertEqual("prompt", compatibility_key(prompt).source_side)
        facts = build_dedup_facts((attempt, official, prompt), (SOURCE_A,))
        self.assertEqual((), facts["candidates"])
        self.assertEqual((), facts["canonical_groups"])

    def test_numeric_conflict_is_not_decided_by_source_order(self):
        left = unit("The final value is 10 after applying the recurrence relation.", 1)
        right = unit(
            "The final value is 11 after applying the recurrence relation.",
            2,
            SOURCE_B,
            "materials/b.txt",
        )
        facts = build_dedup_facts(
            (left, right),
            (SOURCE_A, SOURCE_B),
            config=DedupConfig(threshold_ppm=850_000),
        )
        self.assertEqual(1, len(facts["conflicts"]))
        self.assertIn("numeric_mismatch", facts["conflicts"][0].reason_codes)
        self.assertTrue(all(member.priority_rank == 0 for member in facts["conflicts"][0].members))

    def test_same_source_formula_variants_remain_candidates_not_conflicts(self):
        left = unit(
            r"P(G_2\mid G_1)=P(B_2\mid B_1)=\frac{3}{4}",
            1,
            kind="formula",
        )
        right = unit(
            r"P(B_2\mid G_1)=P(G_2\mid B_1)=\frac{1}{4}",
            2,
            kind="formula",
        )
        facts = build_dedup_facts((left, right), (SOURCE_A,))
        near = [row for row in facts["candidates"] if row.match_kind == "near"]
        self.assertEqual(1, len(near))
        self.assertIn("formula_mismatch", near[0].conflict_signals)
        self.assertIn("numeric_mismatch", near[0].conflict_signals)
        self.assertEqual(0, len(facts["conflicts"]))

    def test_cross_source_formula_variants_still_fail_closed(self):
        left = unit(
            r"P(G_2\mid G_1)=P(B_2\mid B_1)=\frac{3}{4}",
            1,
            kind="formula",
        )
        right = unit(
            r"P(B_2\mid G_1)=P(G_2\mid B_1)=\frac{1}{4}",
            2,
            SOURCE_B,
            "materials/b.txt",
            kind="formula",
        )
        facts = build_dedup_facts((left, right), (SOURCE_A, SOURCE_B))
        self.assertEqual(1, len(facts["conflicts"]))
        self.assertEqual("numeric_mismatch", facts["conflicts"][0].conflict_kind)

    def test_same_source_visual_variants_do_not_become_source_conflicts(self):
        left = unit(
            "Use the diagram to identify the traversal shown in this question.",
            1,
            kind="question",
            asset_path="assets/a.png",
            asset_role="question_context",
        )
        right = unit(
            "Use the diagram to identify the traversal shown in this question.",
            2,
            kind="question",
            asset_path="assets/b.png",
            asset_role="question_context",
        )
        facts = build_dedup_facts((left, right), (SOURCE_A,))
        near = [row for row in facts["candidates"] if row.match_kind == "near"]
        self.assertEqual(1, len(near))
        self.assertIn("visual_context_mismatch", near[0].conflict_signals)
        self.assertEqual(0, len(facts["conflicts"]))

    def test_structural_theorem_numbers_do_not_create_false_numeric_conflict(self):
        left = unit(
            "Theorem 7.8\n"
            "For discrete random variables X and Y with joint PMF PX,Y(x, y), and x\n"
            "and y such that PX(x) > 0 and PY(y) > 0,\n"
            "PX|Y (x|y) = PX,Y (x, y)\n"
            "PY (y)\n"
            ",\n"
            "PY |X(y|x) = PX,Y (x, y)\n"
            "PX(x)\n"
            ".\n",
            1,
        )
        right = unit(
            "Theorem 7.9\n"
            "For discrete random variables X and Y with joint PMF PX,Y(x, y), and x\n"
            "and y such that PX(x) > 0 and PY(y) > 0,\n"
            "PX,Y (x, y) = PY |X(y|x) PX(x) = PX|Y (x|y) PY (y) .\n",
            2,
            SOURCE_B,
            "materials/b.txt",
        )
        facts = build_dedup_facts(
            (left, right),
            (SOURCE_A, SOURCE_B),
            config=DedupConfig(),
        )
        near = [row for row in facts["candidates"] if row.match_kind == "near"]
        self.assertEqual(1, len(near))
        self.assertNotIn("numeric_mismatch", near[0].conflict_signals)
        self.assertEqual(0, len(facts["conflicts"]))

    def test_structural_locator_strip_preserves_body_numeric_conflicts(self):
        left = unit("Theorem 7.8\nThe final value is 10 after the recurrence.", 1)
        right = unit(
            "Theorem 7.9\nThe final value is 11 after the recurrence.",
            2,
            SOURCE_B,
            "materials/b.txt",
        )
        facts = build_dedup_facts(
            (left, right),
            (SOURCE_A, SOURCE_B),
            config=DedupConfig(threshold_ppm=800_000),
        )
        self.assertEqual(1, len(facts["conflicts"]))
        self.assertIn("numeric_mismatch", facts["conflicts"][0].reason_codes)

    def test_equal_question_prompts_with_conflicting_answers_never_fold(self):
        answer_a = unit(
            "The official answer is 10 after applying the recurrence relation.",
            10,
            kind="answer",
        )
        answer_b = unit(
            "The official answer is 11 after applying the recurrence relation.",
            11,
            SOURCE_B,
            "materials/b.txt",
            kind="answer",
        )
        prompt = "Apply the recurrence relation and report the final value."
        question_a = ContentUnit.create(
            SOURCE_A["source_id"], SHA_A, "materials/a.txt", "question", prompt, 1,
            ordinal=20, chapter_id="ch01", paired_unit_id=answer_a.unit_id,
        )
        question_b = ContentUnit.create(
            SOURCE_B["source_id"], SHA_B, "materials/b.txt", "question", prompt, 1,
            ordinal=21, chapter_id="ch01", paired_unit_id=answer_b.unit_id,
        )
        facts = build_dedup_facts(
            (question_a, answer_a, question_b, answer_b),
            (SOURCE_A, SOURCE_B),
            config=DedupConfig(threshold_ppm=850_000),
        )
        question_pair = {question_a.unit_id, question_b.unit_id}
        candidate = next(
            row for row in facts["candidates"]
            if {row.left.unit_id, row.right.unit_id} == question_pair
        )
        self.assertEqual("near", candidate.match_kind)
        self.assertIn("answer_mismatch", candidate.conflict_signals)
        self.assertFalse(
            any(question_pair.issubset({ref.unit_id for ref in group.member_refs})
                for group in facts["canonical_groups"])
        )
        self.assertTrue(
            any(conflict.candidate_id == candidate.candidate_id
                and conflict.conflict_kind == "answer_mismatch"
                for conflict in facts["conflicts"])
        )
        conflict = next(
            conflict for conflict in facts["conflicts"]
            if conflict.candidate_id == candidate.candidate_id
        )
        artifacts = build_source_conflict_review_artifacts(
            (conflict,), (question_a, answer_a, question_b, answer_b)
        )
        self.assertEqual(1, len(artifacts))
        issue = artifacts[0]["issue"]
        self.assertEqual("blocking", issue.severity)
        attached_candidates, attached_conflicts = attach_conflict_review_issue_ids(
            facts["candidates"],
            (conflict,),
            {conflict.conflict_id: issue},
        )
        attached = attached_conflicts[0]
        self.assertEqual(issue.issue_id, attached.review_issue_id)
        self.assertEqual(
            issue.issue_id,
            next(row for row in attached_candidates if row.candidate_id == candidate.candidate_id).review_issue_id,
        )
        unsafe_patch = ReviewPatch.create(
            issue.issue_id,
            issue.source_id,
            issue.source_sha256,
            ({"op": "mark_resolved", "reason": "Keep both answers."},),
            issue.evidence,
            created_at="2026-07-14T12:00:00Z",
            status="applied",
        )
        with self.assertRaises(FactValidationError):
            replay_conflict_review_ledger((attached,), (unsafe_patch,))
        unrecoverable_patch = ReviewPatch.create(
            issue.issue_id,
            issue.source_id,
            issue.source_sha256,
            ({"op": "mark_unrecoverable", "reason": "The source answer key is irreparably damaged."},),
            issue.evidence,
            created_at="2026-07-14T12:01:00Z",
            status="applied",
        )
        terminal = replay_conflict_review_ledger((attached,), (unrecoverable_patch,))[0]
        self.assertEqual("unrecoverable", terminal.status)
        self.assertEqual(unrecoverable_patch.patch_id, terminal.resolution.patch_id)

    def test_distinct_image_backed_homework_problem_locators_are_not_near_duplicates(self):
        answer_a = unit("Official answer for the first assigned problem.", 10, kind="answer")
        answer_b = unit(
            "Official answer for the second assigned problem.",
            11,
            SOURCE_B,
            "materials/b.txt",
            kind="answer",
        )
        common_metadata = {
            "quiz_type": "subjective",
            "source_type": "homework",
            "source": "material",
            "source_language": "en",
            "requires_assets": True,
            "question_text_status": "page_reference",
        }
        question_a = ContentUnit.create(
            SOURCE_A["source_id"], SHA_A, "materials/a.txt", "question",
            "Problem 4.2.4 - see the attached prompt-only crop from hw5 (2)(1).pdf p.6.",
            6, ordinal=20, external_id="hw_hw5__4_2_4", chapter_id="ch04",
            asset_path="assets/problem-4.2.4.png", asset_role="question_context",
            paired_unit_id=answer_a.unit_id, metadata=common_metadata,
        )
        question_b = ContentUnit.create(
            SOURCE_B["source_id"], SHA_B, "materials/b.txt", "question",
            "Problem 4.3.4 - see the attached prompt-only crop from hw5 (2)(1).pdf p.11.",
            11, ordinal=21, external_id="hw_hw5__4_3_4", chapter_id="ch04",
            asset_path="assets/problem-4.3.4.png", asset_role="question_context",
            paired_unit_id=answer_b.unit_id, metadata=common_metadata,
        )
        candidates, stats = build_duplicate_candidates_with_stats(
            (question_a, answer_a, question_b, answer_b)
        )
        question_pair = {question_a.unit_id, question_b.unit_id}
        self.assertFalse(any(
            {row.left.unit_id, row.right.unit_id} == question_pair
            for row in candidates
        ))
        self.assertEqual(1, stats["near_identity_rejected_pair_count"])

    def test_same_image_backed_homework_problem_locator_remains_comparable(self):
        answer_a = unit("Official answer version A.", 10, kind="answer")
        answer_b = unit(
            "Official answer version B.", 11, SOURCE_B, "materials/b.txt", kind="answer"
        )
        metadata = {
            "quiz_type": "subjective",
            "source_type": "homework",
            "source": "material",
            "source_language": "en",
            "requires_assets": True,
            "question_text_status": "page_reference",
        }
        prompt = "Problem 4.2.4 - see the attached prompt-only crop from the assigned homework."
        question_a = ContentUnit.create(
            SOURCE_A["source_id"], SHA_A, "materials/a.txt", "question", prompt, 6,
            ordinal=20, external_id="source_a__4_2_4", chapter_id="ch04",
            asset_path="assets/source-a.png", asset_role="question_context",
            paired_unit_id=answer_a.unit_id, metadata=metadata,
        )
        question_b = ContentUnit.create(
            SOURCE_B["source_id"], SHA_B, "materials/b.txt", "question", prompt, 7,
            ordinal=21, external_id="source_b__4_2_4", chapter_id="ch04",
            asset_path="assets/source-b.png", asset_role="question_context",
            paired_unit_id=answer_b.unit_id, metadata=metadata,
        )
        candidates, stats = build_duplicate_candidates_with_stats(
            (question_a, answer_a, question_b, answer_b)
        )
        question_pair = {question_a.unit_id, question_b.unit_id}
        candidate = next(
            row for row in candidates
            if {row.left.unit_id, row.right.unit_id} == question_pair
        )
        self.assertEqual("near", candidate.match_kind)
        self.assertIn("answer_mismatch", candidate.conflict_signals)
        self.assertEqual(0, stats["near_identity_rejected_pair_count"])

    def test_warning_conflict_mark_resolved_replays_as_keep_both(self):
        left = unit(
            "See figure.", 1, kind="question",
            asset_path="assets/a.png", asset_role="question_context",
        )
        right = unit(
            "See figure.", 2, SOURCE_B, "materials/b.txt", kind="question",
            asset_path="assets/b.png", asset_role="question_context",
        )
        facts = build_dedup_facts((left, right), (SOURCE_A, SOURCE_B))
        conflict = facts["conflicts"][0]
        artifact = build_source_conflict_review_artifacts((conflict,), (left, right))[0]
        issue = artifact["issue"]
        self.assertEqual("warning", issue.severity)
        _candidates, attached = attach_conflict_review_issue_ids(
            facts["candidates"], (conflict,), {conflict.conflict_id: issue}
        )
        patch = ReviewPatch.create(
            issue.issue_id,
            issue.source_id,
            issue.source_sha256,
            ({"op": "mark_resolved", "reason": "Both figures are intentional variants."},),
            issue.evidence,
            created_at="2026-07-14T12:02:00Z",
            status="applied",
        )
        terminal = replay_conflict_review_ledger(attached, (patch,))[0]
        self.assertEqual("resolved_keep_both", terminal.status)
        self.assertEqual("keep_both", terminal.resolution.action)

    def test_large_bucket_caps_near_work_without_losing_exact_group(self):
        exact = [
            unit("Repeated exact footer for every page in this very large lecture deck.", ordinal)
            for ordinal in range(20)
        ]
        unique = [
            unit(
                "Repeated near footer for every page in this very large lecture deck item %04d." % ordinal,
                100 + ordinal,
            )
            for ordinal in range(80)
        ]
        config = DedupConfig(threshold_ppm=850_000, max_near_comparisons=25)
        first, first_stats = build_duplicate_candidates_with_stats(exact + unique, config)
        second, second_stats = build_duplicate_candidates_with_stats(list(reversed(exact + unique)), config)
        self.assertEqual([row.to_dict() for row in first], [row.to_dict() for row in second])
        self.assertEqual(first_stats, second_stats)
        self.assertEqual(19, first_stats["exact_candidate_count"])
        self.assertEqual(25, first_stats["near_compared_pair_count"])
        self.assertGreater(first_stats["near_skipped_pair_count"], 0)
        self.assertTrue(first_stats["near_truncated"])
        facts = build_dedup_facts(exact + unique, (SOURCE_A,), config=config)
        exact_group = next(
            group for group in facts["canonical_groups"]
            if exact[0].unit_id in {ref.unit_id for ref in group.member_refs}
        )
        self.assertEqual(20, len(exact_group.member_refs))
        self.assertEqual(("near_candidate_comparison_cap_reached",), facts["warnings"])

    def test_compile_writes_and_loads_all_four_sidecars(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            rows = (unit("An exact statement with enough detail.", 1), unit("An exact statement with enough detail.", 2))
            receipt = compile_ingestion_facts(workspace, rows, (SOURCE_A,))
            self.assertEqual(1, receipt["canonical_group_count"])
            self.assertEqual(1, len(load_duplicate_candidates(workspace)))
            self.assertEqual(1, len(load_canonical_groups(workspace)))
            self.assertEqual(0, len(load_source_conflicts(workspace)))
            self.assertEqual(1, len(load_source_priorities(workspace)))
            self.assertEqual([], receipt["warnings"])

    def test_recompile_preserves_reviewed_priority_for_same_source_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            evidence_path = workspace / "priority-review.txt"
            evidence_path.write_text("Teacher confirmed this source.", encoding="utf-8")
            evidence = FactEvidenceRef(
                "priority-review.txt",
                hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
            )
            priority = SourcePriority.create(
                SOURCE_A["source_id"],
                SOURCE_A["sha256"],
                rank=90,
                tier="teacher_official",
                basis="user",
                evidence=(evidence,),
            )
            rows = (unit("One stable course fact with enough detail.", 1),)
            compile_ingestion_facts(workspace, rows, (SOURCE_A,), priorities=(priority,))
            compile_ingestion_facts(workspace, rows, (SOURCE_A,))
            loaded = load_source_priorities(workspace)
            self.assertEqual(1, len(loaded))
            self.assertEqual(90, loaded[0].rank)
            self.assertEqual("user", loaded[0].basis)

    def test_live_derivation_requires_explicit_complete_priority_sidecar_and_config(self):
        rows = (unit("One stable course fact with enough detail.", 1),)
        facts = build_dedup_facts(rows, (SOURCE_A,))
        with self.assertRaisesRegex(FactValidationError, "DedupConfig is required"):
            validate_persisted_fact_derivation(
                rows,
                (SOURCE_A,),
                None,
                facts["candidates"],
                facts["canonical_groups"],
                facts["conflicts"],
                facts["priorities"],
            )
        with self.assertRaisesRegex(FactValidationError, "source priorities"):
            validate_persisted_fact_derivation(
                rows,
                (SOURCE_A,),
                facts["config"],
                facts["candidates"],
                facts["canonical_groups"],
                facts["conflicts"],
                (),
            )


if __name__ == "__main__":
    unittest.main()
