# Inline worked-example evidence

Some lecture decks put a complete demonstration directly below an
`Example N.M` heading and never add a separate `Solution N.M` heading. A bare
one-page Example is **not** automatically an answer: layout, title, and body
text alone cannot prove that the body is a complete worked solution.

The deterministic builder therefore keeps the item teaching-only with
`answer_status: "unknown"` and emits a blocking
`inline_worked_answer_candidate`. A reviewer may promote it without rerunning
the PDF parser or mutating `.ingest/source_raw_input.json`, but only through the
normal typed review queue and append-only patch ledger.

## Required evidence

Registration fails closed unless all of the following are current and exact:

- one unpaired material question unit and one unanswered
  `teaching_role: "worked_example"`, `gradable: false` teaching row;
- no row with the same item ID in `references/quiz_bank.json`;
- one uniquely matching, same-source-revision/page, `method: "native"`,
  `kind: "text"` material unit whose text begins with the exact teaching title;
- explicit `metadata.source_language: "zh"|"en"` on that native unit;
- a live crop-receipt-schema-v2 and semantic-review-schema-v2 full-prompt crop
  for the exact item/chapter/source/page, using either
  `target_item_only` or prompt-only `target_with_required_context` isolation;
- the question unit already carries the exact compact declaration for that
  receipt in `metadata.assets`; its legacy top-level asset mirror may be absent
  or may already match that exact prompt crop; and
- a named reviewer plus a concrete note that the crop/text is one complete
  worked demonstration.

## Review-ledger migration

Use the compiler-only migration after the strict prompt crop exists:

```text
python scripts/ingest_review.py --workspace <ws> --json register-inline-worked --question-unit <question_unit_id> --material-unit <native_text_unit_id> --crop-receipt-id <crop_receipt_id> --reviewer <name> --review-note <why-complete>
python scripts/ingest_review.py --workspace <ws> --json claim <issue_id>
python scripts/ingest_review.py --workspace <ws> --json draft-inline-worked <issue_id>
python scripts/ingest_review.py --workspace <ws> --json validate-patch <absolute_patch_file_from_draft>
python scripts/ingest_review.py --workspace <ws> --json apply <absolute_patch_file_from_draft>
```

Use the absolute `patch_file` returned by `draft-inline-worked`. Patch-file
arguments are resolved by the host process, so a workspace-relative display
path must not be guessed from an unrelated current working directory.

Registration writes content-addressed review evidence and supersedes only an
unclaimed generic candidate for the same question. Drafting rehydrates every
live unit, teaching row, source revision, crop receipt, and evidence hash. It
then emits the ordinary three-operation patch:

1. `replace_unit` makes the typed question carry the original-language native
   material text and the exact full-prompt crop once in `metadata.assets`
   (without a second receipt-less top-level mirror), while preserving any
   other typed prompt components for the same item;
2. `add_unit` creates a material answer with
   `answer_origin: "inline_material"` and
   `inline_material_source_unit_id` pointing back to the immutable native text
   unit; and
3. `pair_qa` creates the reciprocal pair with an exact source-revision binding.

The migrated pair must remain on the same source revision, file, page, title,
text, and `zh|en` language. It has no answer-side visual asset. The source unit
is not replaced or deleted, so the ledger decision remains reproducible from
immutable parser facts.

## Teaching and Study Guide boundary

`inline_material` is a teaching evidence route, never an assessment shortcut.
Raw ingestion and review recompilation both reject it in the selectable quiz
bank. It remains reachable only through `references/teaching_examples.json`.

Study Guide authoring independently rechecks the reciprocal pair and its exact
native-unit back-reference. Missing/ambiguous language or any source, page,
title, text, crop, or provenance drift is an authoring blocker. A full-prompt
image replaces printed prompt text in the rendered Guide, so the original
lecture item is not pasted twice.

The isolated explanation request exposes the per-language evidence origin. In
a monolingual request, when the exact material answer is already identical to
`ANSWER.text`, `material_evidence` carries a local `text_ref` instead of a
second copy of the full passage. Bilingual or translated/teaching-copy requests
retain a distinct packet-bound material payload as the factual base. The model
transport removes only leading/trailing whitespace (for example, a parser's
page-final newline); it does not collapse or rewrite internal text, and the
request's `packet_sha256` continues to bind the unchanged author packet and
source revision.
