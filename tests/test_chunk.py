# -*- coding: utf-8 -*-
"""v4-P3 R-slice — scripts/chunk.py: cleaning is conservative, chunks never exceed HARD_MAX,
offsets locate back verbatim, gold-span-in-exactly-one-chunk acceptance on synthetic degenerate
text (the real-PSYC acceptance runs only when the local gitignored workspace exists)."""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import chunk  # noqa: E402


def degenerate_transcript(n_sent=400):
    """Single-line, no-heading lecture-ish text with scraper CSS at the head (the PSYC shape)."""
    head = "PSYC 110 p { font-size: 14px; } PSYC 110 Introduction: Lecture 3 Transcript "
    sents = []
    for i in range(n_sent):
        lead = "Okay. " if i % 17 == 0 else ""
        sents.append("%sThis is sentence %d about memory encoding and retrieval in lecture three." % (lead, i))
    return head + " ".join(sents)


class Cleaning(unittest.TestCase):
    def test_css_residue_stripped_from_head(self):
        text, _ = chunk.chunk_text(degenerate_transcript(40))
        self.assertNotIn("font-size", text, "头部 CSS 残留必须被清洗")
        self.assertIn("Lecture 3 Transcript", text, "真实内容不得被误删")

    def test_braces_in_course_prose_survive(self):
        src = "## Sets\n" + ("Define the set {x | x > 0} of positive reals. " * 40) + \
              "\n## More\n" + ("Another section about {a, b, c} tuples. " * 40)
        text, chunks = chunk.chunk_text(src)
        self.assertIn("{x | x > 0}", text, "正文里的集合记号绝不能被当 CSS 清掉")
        self.assertTrue(chunks)

    def test_html_tags_stripped(self):
        text, _ = chunk.chunk_text("<div class='x'>Hello</div> <p>world</p> real content here. " * 20)
        self.assertNotIn("<div", text)
        self.assertIn("real content", text)


class Chunking(unittest.TestCase):
    def test_headings_become_chunk_boundaries(self):
        src = "# Lec 6\n\n## Terminology\n" + ("Terms explained. " * 30) + \
              "\n## Tree Navigation\n" + ("Walking the tree. " * 30)
        _, chunks = chunk.chunk_text(src)
        titles = [c["title"] for c in chunks]
        self.assertIn("Terminology", titles)
        self.assertIn("Tree Navigation", titles)

    def test_degenerate_text_chunks_under_hard_max(self):
        _, chunks = chunk.chunk_text(degenerate_transcript())
        self.assertGreater(len(chunks), 5, "长退化文本必须被切成多块")
        for c in chunks:
            self.assertLessEqual(len(c["text"]), chunk.HARD_MAX,
                                 "任何块不得超过 HARD_MAX（计划验收）")

    def test_offsets_locate_back_verbatim(self):
        text, chunks = chunk.chunk_text(degenerate_transcript())
        for c in chunks:
            self.assertEqual(text[c["start"]:c["end"]], c["text"],
                             "偏移必须能在清洗后文本里逐字定位回块内容")

    def test_chunks_cover_text_without_overlap(self):
        text, chunks = chunk.chunk_text(degenerate_transcript())
        pos = 0
        for c in chunks:
            self.assertGreaterEqual(c["start"], pos, "块不得重叠")
            pos = c["end"]
        covered = sum(c["end"] - c["start"] for c in chunks)
        self.assertGreaterEqual(covered / len(text), 0.98, "覆盖率必须≈全文（不静默丢内容）")

    def test_gold_span_lands_in_exactly_one_chunk(self):
        # the plan's acceptance shape: a verbatim quote must be findable inside ONE chunk
        src = degenerate_transcript(200)
        gold = "This is sentence 123 about memory encoding and retrieval in lecture three."
        text, chunks = chunk.chunk_text(src)
        holders = [c for c in chunks if gold in c["text"]]
        self.assertEqual(len(holders), 1, "逐字金标 span 必须完整落在唯一块内（不跨块截断）")

    def test_no_punctuation_run_hard_splits(self):
        _, chunks = chunk.chunk_text("词" * 7000)   # zero sentence enders
        self.assertTrue(all(len(c["text"]) <= chunk.HARD_MAX for c in chunks))
        self.assertGreaterEqual(len(chunks), 3)

    def test_empty_input(self):
        text, chunks = chunk.chunk_text("")
        self.assertEqual((text, chunks), ("", []))


class StructuredChunking(unittest.TestCase):
    @staticmethod
    def unit(unit_id, kind, text, ordinal, section=("Topic",), page=1):
        return {
            "unit_id": unit_id,
            "source_id": "src_x",
            "source_sha256": "0" * 64,
            "source_file": "lecture/ch01.pdf",
            "kind": kind,
            "text": text,
            "page": page,
            "ordinal": ordinal,
            "chapter_id": "ch01",
            "phase_id": "phase01",
            "section_path": list(section),
            "parent_unit_id": "unit_parent",
        }

    @staticmethod
    def policy(units=(), quiz=(), teaching=(), canonical_groups=()):
        return chunk._verified_asset_policy_from_layers(
            quiz_rows=quiz, teaching_rows=teaching, content_units=units,
            canonical_groups=canonical_groups,
        )

    def test_tables_formulas_and_questions_are_atomic(self):
        units = [
            self.unit("unit_text", "text", "intro", 1),
            self.unit("unit_table", "table", "A\tB\n" * 2000, 2),
            self.unit("unit_formula", "formula", r"E = mc^2", 3),
            self.unit("unit_question", "question", "Compute the value.", 4),
        ]
        chunks = chunk.chunk_units(units, target=100, hard_max=200)
        table = next(row for row in chunks if row["kind"] == "table")
        self.assertEqual(["unit_table"], table["unit_ids"])
        self.assertTrue(table["oversize_atomic"])
        self.assertEqual(["formula", "question"], [
            row["kind"] for row in chunks if row["kind"] in ("formula", "question")
        ])

    def test_answer_units_never_enter_teaching_retrieval(self):
        chunks = chunk.chunk_units([
            self.unit("unit_question", "question", "Prompt", 1),
            self.unit("unit_answer", "answer", "Secret answer", 2),
        ])
        self.assertTrue(any("Prompt" in row["text"] for row in chunks))
        self.assertFalse(any("Secret answer" in row["text"] for row in chunks))

    def test_student_attempt_units_never_enter_teaching_retrieval(self):
        prompt = self.unit("unit_question", "question", "Prompt", 1)
        attempt = self.unit("unit_attempt", "figure", "Student wrote 42", 2)
        attempt["asset_role"] = "student_attempt"
        attempt["asset_path"] = "references/assets/attempt.png"
        chunks = chunk.chunk_units(
            [prompt, attempt], tainted_keys=self.policy([prompt, attempt])
        )
        self.assertTrue(any("Prompt" in row["text"] for row in chunks))
        self.assertFalse(any("Student wrote 42" in row["text"] for row in chunks))

    def test_asset_units_reject_omitted_none_and_raw_empty_policy(self):
        official = self.unit("unit_official", "figure", "Official diagram", 1)
        official.update({
            "asset_path": "references/assets/official.png",
            "asset_role": "figure",
        })
        cases = ({}, {"tainted_keys": None}, {"tainted_keys": set()})
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(ValueError, "verified complete"):
                    chunk.chunk_units([official], **kwargs)

    def test_attempt_path_taints_official_unit_globally_but_distinct_attempt_is_safe(self):
        official = self.unit("unit_official", "figure", "Official diagram", 1)
        official.update({
            "asset_path": "references/assets/shared.png",
            "asset_role": "figure",
        })
        foreign_attempt = self.unit("unit_attempt", "figure", "Student work", 2)
        foreign_attempt.update({
            "chapter_id": "ch02",
            "asset_path": "references/assets/shared.png",
            "asset_role": "student_attempt",
        })
        with self.assertRaisesRegex(ValueError, "verified complete"):
            chunk.chunk_units([official, foreign_attempt])
        with self.assertRaisesRegex(ValueError, "student_attempt-tainted"):
            self.policy([official, foreign_attempt])

        safe = self.unit("unit_safe", "question", "Safe prompt", 3)
        safe["external_id"] = "safe-q"
        safe["metadata"] = {"assets": [
            {"path": "references/assets/prompt.png", "role": "question_context"},
            {"path": "references/assets/attempt.png", "role": "student_attempt"},
        ]}
        chunks = chunk.chunk_units([safe], tainted_keys=self.policy([safe]))
        self.assertTrue(any("Safe prompt" in row["text"] for row in chunks))

    def test_precomputed_cross_layer_taint_filters_nested_official_asset(self):
        unit = self.unit("unit_question", "question", "Do not retrieve", 1)
        unit["external_id"] = "q1"
        unit["metadata"] = {"assets": [{
            "path": "references/assets/shared.png", "role": "question_context",
        }]}
        from scripts.asset_policy import student_attempt_tainted_keys
        teaching = [{"id": "attempt", "chapter": 2, "assets": [{
            "path": "references/assets/shared.png", "role": "student_attempt",
        }]}]
        tainted = student_attempt_tainted_keys(teaching)
        with self.assertRaisesRegex(ValueError, "raw tainted_keys"):
            chunk.chunk_units([unit], tainted_keys=tainted)
        # Even an internally produced token cannot be borrowed for an unbound
        # unit slice.  The complete content-unit revision set is part of the
        # capability, not merely its tainted-key set.
        verified = self.policy([], teaching=teaching)
        with self.assertRaisesRegex(ValueError, "not bound"):
            chunk.chunk_units([unit], tainted_keys=verified)

    def test_paired_answer_without_external_id_cannot_alias_prompt_asset(self):
        question = self.unit("unit_question", "question", "Prompt", 1)
        question.update({
            "external_id": "q1",
            "paired_unit_id": "unit_answer",
            "asset_path": "references/assets/shared.png",
            "asset_role": "figure",
        })
        answer = self.unit("unit_answer", "answer", "Answer", 2)
        answer.update({
            "external_id": None,
            "paired_unit_id": "unit_question",
            "asset_path": "references\\assets\\shared.png",
            "asset_role": "worked_solution",
        })
        with self.assertRaisesRegex(ValueError, "verified complete"):
            chunk.chunk_units([question, answer])
        with self.assertRaisesRegex(ValueError, "both prompt and official answer"):
            self.policy([question, answer])

    def test_public_workspace_policy_blocks_quiz_or_teaching_attempt_alias(self):
        official = self.unit("unit_official", "figure", "Official diagram", 1)
        official.update({
            "asset_path": "references/assets/shared.png",
            "asset_role": "figure",
        })
        attempt = {
            "id": "foreign-attempt", "chapter": 2,
            "assets": [{
                "path": "references/assets/shared.png",
                "role": "student_attempt",
            }],
        }
        for hostile_layer in ("quiz", "teaching"):
            with self.subTest(hostile_layer=hostile_layer):
                workspace = tempfile.mkdtemp(prefix="chunk-policy-")
                self.addCleanup(shutil.rmtree, workspace, ignore_errors=True)
                references = os.path.join(workspace, "references")
                ingest = os.path.join(workspace, ".ingest")
                os.makedirs(references)
                os.makedirs(ingest)
                quiz = [attempt] if hostile_layer == "quiz" else []
                teaching = [attempt] if hostile_layer == "teaching" else []
                with open(os.path.join(references, "quiz_bank.json"), "w",
                          encoding="utf-8") as stream:
                    json.dump(quiz, stream)
                with open(os.path.join(references, "teaching_examples.json"), "w",
                          encoding="utf-8") as stream:
                    json.dump(teaching, stream)
                with open(os.path.join(ingest, "content_units.jsonl"), "w",
                          encoding="utf-8") as stream:
                    stream.write(json.dumps(official) + "\n")

                with self.assertRaisesRegex(ValueError, "student_attempt-tainted"):
                    chunk.chunk_units([official], workspace=workspace)

    def test_workspace_retrieval_blocks_hardlink_alias_of_student_attempt(self):
        official = self.unit(
            "unit_official_hardlink", "figure", "Do not retrieve", 1
        )
        official.update({
            "asset_path": "references/assets/official.png",
            "asset_role": "figure",
        })
        attempt = {
            "id": "foreign-hardlink-attempt", "chapter": 2,
            "assets": [{
                "path": "references/assets/attempt.png",
                "role": "student_attempt",
            }],
        }
        workspace = tempfile.mkdtemp(prefix="chunk-hardlink-policy-")
        self.addCleanup(shutil.rmtree, workspace, ignore_errors=True)
        references = os.path.join(workspace, "references")
        assets = os.path.join(references, "assets")
        ingest = os.path.join(workspace, ".ingest")
        os.makedirs(assets)
        os.makedirs(ingest)
        with open(os.path.join(assets, "official.png"), "wb") as stream:
            stream.write(b"same physical image")
        try:
            os.link(
                os.path.join(assets, "official.png"),
                os.path.join(assets, "attempt.png"),
            )
        except (OSError, NotImplementedError):
            self.skipTest("hard links are unavailable")
        with open(os.path.join(references, "quiz_bank.json"), "w",
                  encoding="utf-8") as stream:
            json.dump([attempt], stream)
        with open(os.path.join(references, "teaching_examples.json"), "w",
                  encoding="utf-8") as stream:
            json.dump([], stream)
        with open(os.path.join(ingest, "content_units.jsonl"), "w",
                  encoding="utf-8") as stream:
            stream.write(json.dumps(official) + "\n")

        with self.assertRaisesRegex(ValueError, "student_attempt-tainted"):
            chunk.chunk_units([official], workspace=workspace)

    def test_workspace_bound_capability_cannot_be_borrowed_across_workspaces(self):
        official = self.unit("unit_same", "figure", "Byte-identical official", 1)
        official.update({
            "asset_path": "references/assets/shared.png",
            "asset_role": "figure",
        })
        workspaces = []
        for hostile in (False, True):
            workspace = tempfile.mkdtemp(prefix="chunk-policy-borrow-")
            workspaces.append(workspace)
            self.addCleanup(shutil.rmtree, workspace, ignore_errors=True)
            references = os.path.join(workspace, "references")
            ingest = os.path.join(workspace, ".ingest")
            os.makedirs(references)
            os.makedirs(ingest)
            teaching = [{
                "id": "foreign-attempt", "chapter": 2,
                "assets": [{
                    "path": "references/assets/shared.png",
                    "role": "student_attempt",
                }],
            }] if hostile else []
            with open(os.path.join(references, "quiz_bank.json"), "w",
                      encoding="utf-8") as stream:
                json.dump([], stream)
            with open(os.path.join(references, "teaching_examples.json"), "w",
                      encoding="utf-8") as stream:
                json.dump(teaching, stream)
            with open(os.path.join(ingest, "content_units.jsonl"), "w",
                      encoding="utf-8") as stream:
                stream.write(json.dumps(official) + "\n")

        clean_workspace, hostile_workspace = workspaces
        clean_policy = chunk.workspace_asset_policy(clean_workspace)
        with self.assertRaisesRegex(ValueError, "workspace-bound"):
            chunk.chunk_units([official], tainted_keys=clean_policy)
        with self.assertRaisesRegex(ValueError, "student_attempt-tainted"):
            chunk.chunk_units([official], workspace=hostile_workspace)
        self.assertTrue(chunk.chunk_units([official], workspace=clean_workspace))

    def test_asset_free_public_helper_keeps_legacy_compatibility(self):
        unit = self.unit("unit_text", "text", "No assets", 1)
        self.assertTrue(chunk.chunk_units([unit], tainted_keys=set()))

    def test_clean_workspace_capability_cannot_be_borrowed_for_fake_unit(self):
        live = self.unit("unit_live", "figure", "Live official diagram", 1)
        live.update({
            "asset_path": "references/assets/live.png",
            "asset_role": "figure",
        })
        workspace = tempfile.mkdtemp(prefix="chunk-clean-policy-")
        self.addCleanup(shutil.rmtree, workspace, ignore_errors=True)
        references = os.path.join(workspace, "references")
        ingest = os.path.join(workspace, ".ingest")
        os.makedirs(references)
        os.makedirs(ingest)
        for name in ("quiz_bank.json", "teaching_examples.json"):
            with open(os.path.join(references, name), "w", encoding="utf-8") as stream:
                json.dump([], stream)
        with open(os.path.join(ingest, "content_units.jsonl"), "w",
                  encoding="utf-8") as stream:
            stream.write(json.dumps(live) + "\n")

        fake_revision = dict(live)
        fake_revision["text"] = "Host-injected replacement text"
        unbound = dict(live)
        unbound["unit_id"] = "unit_unbound"
        for hostile in (fake_revision, unbound):
            with self.subTest(unit_id=hostile["unit_id"], text=hostile["text"]):
                with self.assertRaisesRegex(ValueError, "not bound"):
                    chunk.chunk_units([hostile], workspace=workspace)

        chunks = chunk.chunk_units([live], workspace=workspace)
        self.assertEqual(["unit_live"], chunks[0]["unit_ids"])

    def test_asset_free_workspace_still_binds_revision_and_alias_lineage(self):
        live = self.unit("unit_live_text", "text", "Live material text", 1)
        unrelated = self.unit(
            "unit_unrelated_text", "text", "Different live material", 2
        )
        workspace = tempfile.mkdtemp(prefix="chunk-text-policy-")
        self.addCleanup(shutil.rmtree, workspace, ignore_errors=True)
        references = os.path.join(workspace, "references")
        ingest = os.path.join(workspace, ".ingest")
        os.makedirs(references)
        os.makedirs(ingest)
        for name in ("quiz_bank.json", "teaching_examples.json"):
            with open(os.path.join(references, name), "w", encoding="utf-8") as stream:
                json.dump([], stream)
        with open(os.path.join(ingest, "content_units.jsonl"), "w",
                  encoding="utf-8") as stream:
            for row in (live, unrelated):
                stream.write(json.dumps(row) + "\n")

        changed = dict(live)
        changed["text"] = "Host-forged replacement"
        with self.assertRaisesRegex(ValueError, "not bound"):
            chunk.chunk_units([changed], workspace=workspace)

        false_lineage = dict(live)
        false_lineage["retrieval_occurrence_unit_ids"] = [
            live["unit_id"], unrelated["unit_id"],
        ]
        with self.assertRaisesRegex(ValueError, "unbound retrieval occurrence aliases"):
            chunk.chunk_units([false_lineage], workspace=workspace)
        with self.assertRaisesRegex(ValueError, "require a verified"):
            chunk.chunk_units([false_lineage])

        chunks = chunk.chunk_units([live], workspace=workspace)
        self.assertEqual([live["unit_id"]], chunks[0]["unit_ids"])

    def test_asset_fold_aliases_are_exactly_bound_to_canonical_group(self):
        from scripts.ingestion.facts import (
            CanonicalGroup, CompatibilityKey, UnitRevisionRef,
        )
        from scripts.ingestion.retrieval_folding import fold_units_for_retrieval

        units = []
        for number in (1, 2, 3):
            current = self.unit(
                "unit_" + str(number) * 64,
                "figure", "Exact official diagram", number,
            )
            current.update({
                "source_id": "src_" + str(number) * 64,
                "source_sha256": str(number) * 64,
                "asset_path": "references/assets/exact.png",
                "asset_role": "figure",
            })
            units.append(current)
        left, right, unrelated = units
        group = CanonicalGroup.create(
            derivation="exact_auto",
            normalizer="exact-nfc-ws-v1",
            compatibility_key=CompatibilityKey(
                source_side="teaching", kind_family="concept",
                chapter_id="ch01", provenance_class="source_backed",
            ),
            member_refs=(
                UnitRevisionRef.from_unit(left), UnitRevisionRef.from_unit(right),
            ),
            display_unit_id=left["unit_id"],
            fingerprint_sha256="f" * 64,
        )
        verified = self.policy(units, canonical_groups=(group,))
        folded = fold_units_for_retrieval(units, (group,))
        chunks = chunk.chunk_units(folded, tainted_keys=verified)
        display_chunk = next(
            row for row in chunks if left["unit_id"] in row["unit_ids"]
        )
        self.assertEqual(
            {left["unit_id"], right["unit_id"]}, set(display_chunk["unit_ids"])
        )

        hostile = dict(next(
            row for row in folded if row["unit_id"] == left["unit_id"]
        ))
        hostile["retrieval_occurrence_unit_ids"] = [
            left["unit_id"], right["unit_id"], unrelated["unit_id"],
        ]
        with self.assertRaisesRegex(ValueError, "unbound retrieval occurrence aliases"):
            chunk.chunk_units([hostile], tainted_keys=verified)


class RealPsycAcceptance(unittest.TestCase):
    """Plan acceptance on the real (gitignored, local-only) PSYC wiki — skips elsewhere/CI."""
    WIKI = os.path.join(ROOT, "benchmark", "skill_workspace", "psyc110_full", "references", "wiki")

    def test_all_20_chapters_slice_under_hard_max(self):
        if not os.path.isdir(self.WIKI):
            self.skipTest("本地无 psyc110_full 工作区（gitignored）——真实验收仅在持有材料的机器上跑")
        import glob
        files = sorted(glob.glob(os.path.join(self.WIKI, "ch*.md")))
        self.assertGreaterEqual(len(files), 20)
        for fp in files:
            with open(fp, encoding="utf-8", errors="replace") as f:
                text, chunks = chunk.chunk_text(f.read())
            self.assertTrue(chunks, fp)
            for c in chunks:
                self.assertLessEqual(len(c["text"]), chunk.HARD_MAX, fp)
            self.assertNotIn("font-size", text, fp)


if __name__ == "__main__":
    unittest.main(verbosity=2)
