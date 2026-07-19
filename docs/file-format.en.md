# Workspace File Format

English · [中文](file-format.md)

The exam-preparation workspace built by this skill follows a fixed structure and question-bank schema. This file is the **normative specification** and is also the basis for validation by
[`scripts/validate_workspace.py`](../scripts/validate_workspace.py).

## 1. Workspace structure

```text
<workspace>/
  study_plan.md; study_state.json; study_progress.md
  exam_runtime_receipt.json; ingest_report.json
  .lightweight/session.json         # lightweight on-demand page-batch ledger (not mixed with .ingest)
  .lightweight/assets/              # page/contact/prompt/answer images dedicated to lightweight mode
  .ingest/                          # build/review facts; never edit by hand
    source_raw_input.json; parse_report.json; source_manifest.json
    material_build_pending.json; material_build_receipt.json
    material_build_recovery/<generation_id>.json
    pending_ingest.json             # exists only during a compiler rollback transaction
    parser_receipts.json            # v2: one local-only receipt per source
    base_content_units.jsonl; content_units.jsonl
    base_chapter_phase_mappings.jsonl; chapter_phase_mappings.jsonl
    duplicate_candidates.jsonl; canonical_groups.jsonl
    source_conflicts.jsonl; source_priorities.jsonl
    claim_records.jsonl; claim_verification_receipts/chNN.json
    review_queue.jsonl; review_patches.jsonl; pending_patch.json
    unbound_review.json; ai_review_manifest.json; evidence/
    build_manifest.json; mutation.lock
  references/
    wiki/chN_*.md; quiz_bank.json; teaching_examples.json
    teaching_baseline.json; retrieval_index.json; terms.json
    figure_page_index.json; image_question_index.json; assets/
  notebook/; notebook/chNN.guide.json
  study_guide/                      # HTML/PDF/receipt; qa/ contains page-by-page evidence
```

- `study_state.json` is the source of truth for progress; `study_progress.md` is a generated view. `references/wiki/` accepts only safe relative names matching `^[\w.\-]+\.md$`, and each chapter must agree with `study_plan.md` and the current phase. The confusion tracker writes conceptual confusions to state and projects them into the view.
- `notebook.py add-entry/rebuild --lang` accepts canonical `zh|en|bilingual`; when omitted, it inherits `study_state.json.language`. A bilingual entry still writes only one auditable body, but its metadata label, chapter heading, and derived index heading display both languages. A bilingual body must not be disguised as `lang=zh`. Legacy zh/en labels remain reversibly parseable, and an entry with the same chapter, ID, and type still follows the existing idempotent replacement rule.
- `confirm` and `recover-material-build` in `exam_start.py` are the only supported writers of `exam_runtime_receipt.json`: `confirm` is only for an ordinary initial/no-pending confirmation, while `recover-material-build` is only for explicit recovery of an already-confirmed pair with an exact pending generation. The receipt binds the absolute package root, root `SKILL.md` version, runtime-surface SHA-256, Git identity (or the reason it is unavailable), Python, and UTC; the workspace must be outside the package. Every ingestion recomputes identity. Missing, malformed, link-backed, or drifted identity fails closed, and the installed package is never modified to force a match.
- The manifests, units, review ledger, and build manifest under `.ingest/` must belong to the same generation; material or derived-hash drift rejects old artifacts. v2 additionally requires parser receipts and all four deduplication/conflict sidecars. Missing files or inconsistencies in schema, revision, page graph, or policy fail closed. v1 remains readable but must not claim the v2 gate.
- `pending_patch.json` may exist only during a transaction; residue blocks operation and requires recovery or rebuilding. `mutation.lock` provides mutual exclusion only and is not a content fact.
- `material_build_pending.json` is the fail-closed generation latch between builder and compiler. Only a successful builder publishes a new generation: pending must be published before this run changes assets, raw input, or parse report, and it binds the old build manifest, new raw/report, the candidate three-layer asset policy, and migration receipts. When the builder returns nonzero, it does not replace canonical raw/report or public assets; diagnostics appear only in that command result. If publication fails and cannot fully roll back, the blocker remains. While pending exists, all ordinary mutation/publication (including review, claims, and Guides) and validation are rejected; only an explicitly generation-aware builder/compiler path may bypass this check. The only permitted correction of a legacy role is a receipt-bijective `answer_context → student_attempt` migration.
- The material compiler creates a bounded transaction across structured facts, the build manifest, wiki, question bank/teaching examples, retrieval index, reports/plans, and the material pending/receipt transition. Before changing any target, it writes `pending_ingest.json` and backups. An explicit failure rolls back immediately; an interrupted process leaves validation blocked, and the next lock-holding mutation first restores every registered target. Candidate assets/raw/report already published by the builder are outside this compiler rollback set, so material pending continues to block and permits retrying the same generation.
- Successful finalization writes `material_build_receipt.json`, sets the build manifest to schema `2`, binds the generation through a strict `material_build` object and exact hashes of the raw/report/receipt triad under `artifacts`, rechecks live bytes, and deletes material pending last. A current-protocol artifact must not be refreshed or rewritten as schema `1`; a missing or drifted receipt, `material_build` contract, or triad artifact binding blocks validation. Legacy schema `1` remains readable but does not claim this generation gate. `ingest_course.py` initializes `study_state.json` and writes the artifact preference only after the compiler subprocess succeeds; those learner-state operations are not part of the compiler transaction.
- If `exam_runtime_receipt.json` is missing or drifted while pending remains, ordinary `exam_start.py confirm` must not rewrite provenance; explicitly run `exam_start.py recover-material-build --action resume|supersede`. `resume` accepts only the same generation: when source files are intact, it directly consumes the original bytes; when a blocker-first interruption left sources missing, the builder may rebuild, but a different rebuilt generation fails with zero publication and requires `supersede`. `supersede` creates a schema `2` successor whose `supersedes_generation_id` points only to its direct predecessor.
- `.ingest/material_build_recovery/<generation_id>.json` is a strict generation-addressed recovery log. A single log holds at most 64 authorization events, and the ancestor chain holds at most 64 direct-child edges. Every abandoned outcome must point to its direct child; a receipt may record at most 64 ancestor abandonments plus one current resume completion (65 rows total). The compiler rollback transaction covers pending, receipt, the current log, and every ancestor log. The final manifest's reserved key `material_build_recovery:<generation_id>` must exactly match the set declared by the receipt and bind each live hash; any extra, missing, or drifted entry blocks operation. Never hand-edit or delete these facts to clear a blocker.

### Structured-ingestion sources of truth

The ordinary knowledge-base entry point is:

```text
python scripts/ingest_course.py --materials <dir> --workspace <ws> --json
```

In order, it performs dependency preflight, material parsing, structured compilation, progress-state initialization, visual indexing/back-linking, and final validation. The default `core` route handles PDF, DOCX, PPTX, XLSX, common standalone rasters, txt, and Markdown; actual PDF capabilities still depend on the current preflight result. Pass `--artifact-mode` only when the student explicitly provides a standing `chat|visual` choice. Omitting it preserves an existing choice or defaults to `chat`.

Exit codes and content readiness are separate:

| Exit code | `process_success` | Meaning | Next step |
| --- | --- | --- | --- |
| `0` | `true` | The engineering process completed; JSON readiness is `ready` or `usable_with_gaps` | For the latter, report every warning first |
| `10` | `true` | The engineering process completed, but readiness is `blocked` | Enter typed review; teaching and quizzes are forbidden |
| Any other nonzero | `false` | A dependency, input, path, or operation failed | Fix the failure and rerun the same command |

For every source, `source_manifest.json` stores a source ID derived from its canonical relative path, its path relative to the material root, SHA-256, byte count, media type, and parse status. The absolute material-root path and page-by-page accounting live in `build_manifest.json` (`source_root` / `page_quality`) and are not part of the SourceRecord schema. After moving materials or a workspace, rebuild or rebind ingestion; source drift must not be treated as an ordinary warning. Before sharing a workspace, also note that `source_root` may expose a local path.

Each `ContentUnit` row contains at least a stable location identifier `unit_id`, `source_id`/source hash, relative source path, element type, location ordinal and page anchor, order, extraction method/confidence, and provenance. It may also store bbox, parent unit, section path, chapter/phase ID, formula/HTML, asset path/role, and question-answer pairing. Element types include title/heading/text/list/table/formula/figure/diagram/caption/code/speaker_notes/question/answer/page_anchor/other. `source_id = hash(canonical relative path)`; `unit_id = hash(source_id, page/location, bbox, kind, ordinal)` and **does not include source bytes or normalized content**. Consequently, the same location may keep the same ID after the material or payload is revised. An exact revision must also check `source_sha256`; derived deduplication/claim facts additionally bind the full unit payload's `unit_sha256`. Never describe the ID alone as content-derived proof of revision.

`page`/`page_anchor` is a common field name, but its meaning is adapter-defined and is not always a physical paper page:

| Source | Actual meaning of `page` |
| --- | --- |
| PDF | 1-based PDF page ordinal (when the backend can enumerate it) |
| PPTX | 1-based slide ordinal |
| XLSX | 1-based worksheet ordinal; a worksheet is page-equivalent, not print pagination |
| standalone raster | A single-image page-equivalent fixed at 1 |
| DOCX | A 1-based logical segment split only at explicit OOXML page breaks; without an explicit break there is usually one segment, **not a physical page number produced by Word rendering** |
| txt/Markdown | Form-feed-separated segments when present, otherwise one logical segment |

Every location known and enumerated by an adapter must have a `page_anchor`, including blank or scanned PDF pages. A file whose locations cannot be enumerated retains a source-level review issue; never invent a page count.

### ingestion-v2 parser receipts

`.ingest/parser_receipts.json` has the form `{ "schema_version": 1, "receipts": [...] }`. Every discovered source has **exactly one** receipt. Its fields must be **exactly** `schema_version=1`, `adapter`, nullable `adapter_version/module/distribution`, `source_file/source_sha256/media_type`, sorted-unique `requested_pages/produced_pages`, nonnegative integer `discovered_page_count`, `config_sha256`, `status=success|review_required|failed|unsupported`, and the exact `policy={"network":false,"upload":false,"install":false}`. `produced_pages` must equal the live page graph: for success/review-required with `requested_pages=[]`, it must be `1..discovered_page_count`; for a requested subset, it must exactly equal the request and remain in bounds; failed/unsupported must produce no pages. An unknown or duplicate source, or any schema/revision/config/page/policy drift, blocks v2.

An optional runner result object must be **exactly** `{pages, discovered_page_count, warnings?}`; full extraction and requested subsets are validated under the coverage rules above. A normalized page's `source_language` is only `zh|en`; a unit is classified only from its own payload. Formula/symbol-only units may use `zxx`, do not inherit page language, and do not support a zh/en Guide; everything else enters typed review. Core also emits a receipt. A receipt proves only the exact route/revision/config/accounting. Docling/MinerU do not enter the local receipt route: only after the user explicitly names one may a configured remote/cloud host separately disclose the upload/privacy boundary and provide results. The project never probes, downloads, installs, imports, or executes a heavy local package and does not accept a callable local runner. Without a remote integration, continue with core plus typed visual review.

### Dedicated XLSX and standalone-raster routes

- XLSX uses stdlib OOXML and does not execute formulas. Each worksheet is page-equivalent, and the route preserves order, sparse cells/values, formulas and cached values, tables, merges, and safe rasters. A missing cached/shared formula, hidden sheet, external/network formula, or unsupported relationship enters review.
- PNG/JPEG/GIF/BMP/TIFF/WebP first undergo signature, size, dimensions, and hash verification, then materialize a one-page `source_page` asset as needed. Only an explicitly named `<stem>.ocr.txt` or `<image.ext>.txt` may declare an OCR sidecar. It still enters ingestion as an independent `SourceRecord`, parser receipt, and content unit, and the image anchor binds its path/hash/size; the image unit does not absorb its text. An ordinary same-stem `.txt/.md` is independent course material and is never paired automatically. Without a qualifying sidecar, emit `standalone_raster_needs_ocr` for local OCR/vision or typed review; empty text must not count as success. Animated or multi-frame GIF/WebP, APNG, and multi-page TIFF are not flattened to one page: mark the source `failed` and emit a blocking typed review issue.

`base_*` files are deterministic baselines; compiled units/mappings equal the baseline plus the applied ledger. Never edit either by hand. A `ReviewIssue` binds a stable ID, source hash, reason/page/evidence/target/severity/action, and has status pending/claimed/validated/applied/blocked/resolved/unrecoverable/superseded. Any blocking issue not in a terminal state makes readiness `blocked`.

`ingest_review.py --workspace <ws> --json <command>` provides `list/show/claim/validate-patch/apply/apply-batch/mark-unrecoverable/rebuild`. Every patch must be evidence-bound and issue-specific, and the ledger is append-only. `apply-batch` coalesces only derived compilation at the end of the batch. `ai_review_manifest.json` is a legacy view only.

A cross-source `pair_qa` operation must sort `source_revisions` by `source_id` and fully bind the current `source_id/source_sha256` for both prompt and answer. Drift in either source stops replay of the old patch, reopens the corresponding review issue, and requires review against the new revision. An old ledger remains replay-compatible only when the current compiled pair can prove that the revisions on both sides and their mutual pairing are exactly unchanged.

### Canonical groups and source conflicts (derived facts)

ingestion-v2 deterministically rebuilds four sidecars from the current `content_units.jsonl` and `source_manifest.json`:

- `duplicate_candidates.jsonl`: only after compatibility keys such as chapter, kind family, source side, and provenance agree, records an exact fingerprint or a near candidate that reaches the threshold; each candidate binds the source hash and full-unit hash.
- `canonical_groups.jsonl`: by default, automatically groups only exact fingerprints. Each group retains every member revision reference and a deterministic `display_unit_id`. This is a display/retrieval-folding hint: it does not delete occurrences, rewrite `unit_id`, or merge multiple sources into one. The current deterministic compiler never folds a near candidate into a group; near matches remain candidates. `reviewed_near` in the schema also requires an explicit `decision_patch_id`; high similarity or a hand-edited sidecar cannot turn it into a canonical fact.
- `source_conflicts.jsonl`: models answer/boolean/numeric/formula/provenance/visual/textual divergence separately from member revisions. `status=unresolved` fails closed: teaching, quizzing, material claims, and phase completion are forbidden.
- `source_priorities.jsonl`: records a priority tier/basis bound to a source revision. Priority is review evidence, **not an automatic winner**. Conflict resolution requires explicit evidence/review decision and must not silently choose by filename or ordering.

These files are derived facts and are replaced during rebuilding. The validator checks schema, live revision/graph, and integrity hashes and recomputes them using the in-code canonical `DedupConfig()`. The current schema has no trusted custom-config receipt, so changing manifest config/hash in sync still blocks. Emptying a file, retaining only the display member, or hand-editing conflict status does not resolve anything.

### Exact-location claim records and Guide binding

Ordinary ingestion need not create `.ingest/claim_records.jsonl`; an ingestion-v2 typed Guide must have it and the chapter receipt before validation/import. Each record binds an agent-authored `claim_text` at Guide subject `chapter/entity_type/entity_id/field/language/claim_index` to a `UnitRevisionRef`, `payload_field=text|latex`, the full payload hash, and `QuoteSpan(start,end,offset_unit=unicode_codepoint,text,sha256)`.

- `create` reads strict `{schema_version, proposals}`, computes revision/payload/span hashes and IDs from live units/sources, and atomically merges by the complete subject key by default; only `--replace-all` replaces everything. A repeated quote requires the code-point `start`.
- `import` atomically replaces the sidecar after strict validation of the complete JSONL; `verify --manifest <workspace-relative-guide.json> --chapter <N>` validates explicit bindings and writes `claim_verification_receipts/chNN.json`.

`create.claim_ids` contains only records newly created by that batch and reports created/retained/replace_all. Any global sidecar-hash change makes every older chapter receipt stale, so finish the complete mutation batch before verifying each chapter again. Verification counts only claim IDs explicitly referenced by the Guide (knowledge/formula `source_refs`, walkthrough `source_trace`, and omission/semantic-exclusion refs). Unreferenced records are excluded from the verified count but remain bound by the global hash. Subject coordinates must uniquely locate an authored string exactly equal to `claim_text`. Create/import/verify and Guide import share `.ingest/mutation.lock` to prevent a concurrent mutation between live reading and publication.

The published fact snapshot also binds the exact bytes of `parser_receipts.json` and cross-checks `page_quality` in the build manifest, current source revisions, the typed review queue, and the append-only ledger. `ClaimVerificationReceipt.fact_snapshot_sha256` hashes that canonical snapshot and participates in the receipt ID. Therefore, even a legitimate parser-identity update accompanied by a re-signed build manifest makes an old receipt stale. A missing or drifted receipt, or a contradiction between parser and review state, causes claim verification, Guide import, and rendering to fail closed.

This is an **ingestion-v2-only** typed-Guide gate. A claim must attach to the same source ref, with an exactly compatible unit ID and role; the validator recomputes the facts live, so file presence does not prove passage. Material-assertion coverage consists of:

- Every target-language knowledge-point `explanation` directly supported by a nonempty textual unit in the same source language: a `concept` ref plus a `concept_evidence` claim;
- Every formula's `latex`: a `formula` ref plus a `formula_evidence` claim;
- Walkthrough `prompt_text` printed as text: a `question` ref plus a `question_evidence` claim. When a `full_prompt` source image replaces the original text, do not invent a duplicate prompt claim;
- Every answer language whose `answer_provenance.<language>=material`: an `answer|solution` ref plus an `answer_evidence` claim.

`knowledge_points[].explanation_provenance` may explicitly mark each complete explanation-language key as `material|ai_translation|ai_supplement`. When omitted for compatibility with an old Guide, all authored explanations fail closed as `material`. `material` requires same-language source text and a claim; `ai_translation` requires a declared material explanation/claim in another source language; `ai_supplement` must not carry a material claim. Notebook/HTML/PDF display the label. AI translation/teaching explanation and `ai_supplemented|ai_generated` answers must not masquerade as material claims; v1/legacy does not claim the v2 gate.

Verification scope is fixed to `location_only`, identity `claim-location-v1`: it proves authored membership/text identity; that the quote is an exact Unicode code-point slice of the live payload; that unit/source/payload have not drifted; and that a prompt claim does not borrow an answer-side unit. `guide_content_sha256` hashes canonical JSON rather than file bytes and binds the source manifest, units, groups, conflicts, and all claims. It does not prove entailment, support, correctness, or completeness; a person must still judge semantics and provenance. Any Guide/fact-hash change makes the receipt stale.

### Optional host extensions

Runtime retrieval defaults to the stdlib BM25 implementation in `scripts/retrieve.py`. Dense/RRF/reranker code in the source checkout is for offline experiments only; it does not mean the installed package enables those paths and does not replace the default route.

[`langgraph-host-adapter.md`](langgraph-host-adapter.md) preserves only the explicitly requested remote/cloud host contract; local graph construction is disabled. A remote checkpoint/thread stores only a routing hint or bounded receipt. Every transition still rehydrates from `study_state.json`, `.ingest/`, and the runtime/Guide/QA receipts.

## 2. Question-bank item schema (`quiz_bank.json`)

The top level is a **JSON array**, and every element is one question object.

### Common fields

| Field | Required | Description |
| --- | --- | --- |
| `id` | ✅ | Unique question identifier (no duplicates in the array) |
| `chapter` (or `phase`) | Strongly recommended | Chapter/phase membership (integer or string). Chapter quizzes filter on this field, so an item without it cannot be selected. Because `ingest.py` does not require it, the validator emits a **warning**, not an error |
| `type` | ✅ | Question type: one of the six types below |
| `question` | ✅ | Prompt |
| `answer` **or** `answer_status` | See §3 | Standard answer; use `answer_status: "unknown"` when no answer is available |
| `explanation` | Recommended | Explanation shown after an incorrect response |
| `source` | Recommended | Provenance label; see §3 |

### Type-specific fields for the six question types

| `type` | Required | Recommended/optional |
| --- | --- | --- |
| `choice` | `options` (nonempty array) | `answer` = correct option |
| `subjective` | — | `keywords` (used for key-point matching during grading; **strongly recommended**) |
| `diagram` | — | `diagram_type` (for example `avl_tree`), `expected_steps`, or `rendering_notes`/`render_hint` (**recommended**; run the algorithm before drawing) |
| `fill_blank` | — | `acceptable_answers` (array, when multiple answers are acceptable) |
| `true_false` | Boolean `answer` (`true`/`false`, or `真/假`, `对/错`, `T/F`) | `explanation` (recommended) |
| `code` | — | `language` (for example `python`), `expected_behavior`, or `tests` (**recommended**) |

Structured ingestion uses narrowly scoped typed review for two heuristic gaps. Every question defaulted to `subjective` gets its own `type_defaulted` issue, bound to that question's `external_id`, question unit, prompt-source revision, and page anchor. Only a legacy payload with neither `external_ids` nor `target_unit_ids` keeps the expanded “review the whole question bank” behavior. Applying a type correction to one question therefore does not also close a question in another chapter of the same PDF.

When a gradable subjective question lacks `keywords` and has a material-backed official answer, ingestion also creates a `subjective_keywords_missing` issue. The **answer unit is its only target**, and its evidence/source hash/page also belongs to the official answer file. The reviewer writes narrow, gradable points to `metadata.keywords` on the answer unit. During question-bank compilation, existing `keywords` in question metadata take precedence; otherwise, the compiler inherits `metadata.keywords` from the paired answer. Thus a separate workbook and solution book cannot use the prompt revision as fake answer evidence. Without an official paired answer, ingestion does not automatically generate keywords or pretend the item can be graded.

## 3. Provenance

Preventing hallucinations requires more than “locking content into the wiki.” The workspace must distinguish answers **from the student's materials** from answers **supplied by AI**; otherwise the student may mistake invented content for the teacher's emphasis.

Allowed `source` values:

- `teacher` / `material` — 🟢 From the student's uploaded teacher highlights, textbook, or past paper; high confidence.
- `ai_generated` — ⚠️ Generated by AI (for example, when the teacher supplied no answer). **This visible label is itself mandatory.**
- `mixed` — Partly from materials and partly AI-supplemented.
- `unknown` — No answer yet or unknown provenance.

**Mandatory rules enforced by validator errors/warnings:**

1. **Never disguise an AI-generated answer as teacher-provided.** If an item carries an AI-generation marker (`source: ai_generated` or Boolean field `ai_generated: true`), its `source` **must** be `ai_generated` or `mixed`, and **must not** be `teacher`/`material`. A violation is an **error**.
2. **Label a missing answer honestly.** A legacy/handwritten workspace without `answer` still receives a **warning**. A new workspace with `.ingest/` also receives a blocking review issue; readiness remains `blocked` until an evidence-backed official answer is added, an AI answer is explicitly labelled, or the item is explicitly marked unrecoverable. A successful import process does not mean the content is ready for quizzes.
3. **Missing `source`.** An item with an answer but no `source` receives a **warning** recommending provenance completion.

> `chapter` (or `phase`) is used to filter questions for chapter review and is strongly recommended on every item. Because `ingest.py` does not require it, omission produces a **warning** rather than making the workspace invalid.

> These fields agree with the bundled [Chinese question-bank template](../locales/zh/templates/quiz_bank_template.json) and [English quiz-bank template](../locales/en/templates/quiz_bank_template.json). `VALID_QUIZ_TYPES` in `ingest.py` defines the six types above. This specification adds optional per-type fields and provenance validation for static checking by `validate_workspace.py`; it **does not change existing generation logic**.

## 4. Asset dependencies and original-page references

A quiz/example that depends on a diagram or table cannot be answered unless its prompt asset is actually displayed. The following fields are backward-compatible. `ingest_course.py` materializes PDF/OOXML/XLSX/raster assets where possible and preserves their true locations; an unbound asset enters typed review, and validation/selection **fails closed** when a required figure is missing. A DOCX logical segment and XLSX worksheet page-equivalent are not physical pages. The lower-level `build_raw_input_from_workspace.py → ingest.py → build_visual_index.py → validate_workspace.py` chain is diagnostic only. Capabilities come from `check_deps.py`, and nothing is automatically installed, networked, or uploaded.

### Optional fields added to an item

| Field | Type | Description |
| --- | --- | --- |
| `source_file` | string | Original file containing the prompt (for example `ch01.pdf`) |
| `source_pages` | list[int] | Pages containing the prompt (**positive integers starting at 1**) |
| `answer_source_file` | string | Original file containing the answer |
| `answer_source_pages` | list[int] | Pages containing the answer (positive integers) |
| `assets` | list[asset] | Images and other resources required by the prompt/answer; see below |
| `requires_assets` | bool | When `true`, **the item cannot be served without a valid asset** (validator error; selector skips it) |
| `maybe_requires_assets` | bool | Conservative forward-looking marker. When `true`, runtime and validator fail closed exactly as for `requires_assets=true` until a prompt-side asset can be displayed first |
| `question_text_status` | `"full"` \| `"stub"` \| `"page_reference"` | Prompt completeness. `full` is self-contained; `stub` is incomplete and requires `source_pages` or `assets`; `page_reference` says “see page” and requires `source_file` plus `source_pages` (and valid assets when it depends on a figure) |

### Asset object

```json
{
  "path": "references/assets/ch01_p012_quiz_1_1.png",
  "role": "question_context",
  "type": "page_image",
  "caption": "Venn diagram for Quiz 1.1",
  "source_file": "ch01.pdf",
  "source_sha256": "<sha256 of ch01.pdf>"
}
```

- Current ingestion independently binds `source_file` and `source_sha256` for every asset. When an answer-side crop comes from a student's submitted homework PDF but `answer_source_file` points to a separate official solution book, these per-asset provenance fields are mandatory. A legacy asset without `source_file` remains compatible through role-based inference.
- **role** ∈ `question_context` / `answer_context` / `figure` / `table` / `diagram` / `worked_solution` / `student_attempt`
  - **Prompt-side roles** (shown to the student before asking) = `question_context` / `figure` / `diagram` / `table`. An item with `requires_assets=true` or `maybe_requires_assets=true` must have **at least one valid prompt-side asset**. Answer-side assets alone (`answer_context` / `worked_solution`) cannot display the prompt before asking and therefore fail closed.
  - **Answer-side roles** (shown only during solution/review) = `answer_context` / `worked_solution`. This schema does not add `question` or `prompt` roles. An external system using those names should map them to an existing prompt-side role before import.
  - `student_attempt` preserves a crop/original page from the student's submitted work and independently binds its own `source_file` and `source_sha256`. It is neither prompt evidence nor an official/material answer. It cannot satisfy prompt visibility, official-answer coverage, or Study Guide answer coverage and cannot be used for a material claim, wiki, or retrieval. The current tutor/Guide does not display it. A future student-answer comparison requires a separate explicit policy.
  - `student_attempt` **globally taints its physical path**. Once a physical file appears as `student_attempt` in a quiz, teaching example, or any content unit (including another chapter or nested assets), that same physical file cannot be reused as prompt, answer, concept, rendering, retrieval, or claim evidence. Normalize safe relative paths before comparison: safe `/` and `\` separator aliases are equivalent, as are case aliases on Windows. Taint is asymmetric: without a `student_attempt`, different items/units may legitimately reuse the same official prompt/answer file. Within one item, using the same physical file as both prompt and answer is still a direct leak and fails closed. When official evidence and a student attempt occupy distinct paths, preserve the official asset; do not remove it merely because the same item has a separate attempt image.
- **type** ∈ `page_image` / `crop_image` / `diagram` / `table_image` / `other_image`
- Current item-level crops use `type=crop_image` and treat `source_page`, `source_bbox_pdf_points`, `crop_receipt_id`, `crop_spec_sha256`, `semantic_purity_schema_version`, `required_context_ids`, `content_scope`, and exact `isolation` as an indivisible set of compact controls. The full `CropReceipt` lives in the current `.ingest/parse_report.json.crop_receipts` and binds item/side/role, source file and source hash, page box and crop bbox, selection evidence, renderer configuration, and output path/hash/dimensions. The Study Guide author live-validates the one-to-one relation between each compact declaration and full receipt through `crop_receipt_id`, then binds the complete receipt hash into the per-item explanation request. Missing, duplicate, stale, full-page fallback, or source/preview mismatch blocks operation. When layout cannot determine a target region, a revision-bound model/human bbox may be imported with explicit `--crop-annotations`, but the annotation must bind the current PDF-page preview hash and dimensions rendered live by the builder through the same backend. Even a page proven to contain only one item requires a page-box crop receipt; a bare `page_image` cannot bypass per-item isolation.
  Current authoring accepts only semantic-purity schema v2. Target-only uses empty `required_context_ids` and `isolation=target_item_only`; only a prompt may use nonempty sorted-unique contexts with `isolation=target_with_required_context`. An answer is always target-only. Current v2 target/context/detected item IDs share the same safe-Unicode stable technical-key contract across the question bank, teaching manifest, `ContentUnit.external_id`, and typed Guide. Pure control-plane IDs such as crop/region/renderer/chapter continue to use their own portable/hash contracts. Receipt/semantic v1 and old v2 receipts missing this compact-control set are historical read-only evidence and cannot enter a current Study Guide.
- A non-string `role`, `type`, or `question_text_status` (array/object/etc.) is an **error**, not a validator crash. `requires_assets` and `maybe_requires_assets` must be actual Boolean `true`/`false`; a string such as `"false"` is an **error**.

### Visual-first display contract (enforced at runtime)

For any item with `requires_assets=true` or `maybe_requires_assets=true`:

1. **Before asking, explaining, hinting, or solving**, display every question-side asset first.
2. Use only question-side assets at first (`question_context` / `figure` / `diagram` / `table`).
3. Label each displayed prompt image PER THE REPLY-LANGUAGE MODE, and include its role/caption when available: `中文`/`双语` sessions use `题面图` for both the image ALT text and the visible label; `English` sessions use `Question-side asset` for both. Behavior probes accept the zh form as well as the legacy bilingual composite `题面图 / question-side asset` (probes only run on zh-mode transcripts). See [`language-policy.md`](language-policy.md).
4. Do not show answer-side assets (`answer_context` / `worked_solution`) before all question-side assets have already been shown. Never treat `student_attempt` as either question-side or official answer-side evidence.
5. If the asset file is missing/unreadable, the UI cannot render it, or the runtime can only print an unrenderable path, **skip the item or stop with a clear explanation**. Do not proceed as if the image was shown.
6. Show answer-side assets only during solution/review, after the question-side asset display has happened, and label them per the reply-language mode: `中文`/`双语` -> `答案图`, `English` -> `Answer-side asset` (probes also accept the legacy `答案图 / answer-side asset` composite).

`stub` / `page_reference` items follow the same principle: the visible prompt context must appear before teaching, quizzing, hinting, or solving. If the original page/resource is not renderable in the current UI, the item is not safe to ask or explain as a complete prompt.

### Markdown/local-path display guidance

- Prefer the workspace-relative asset path stored in the schema:
  `![题面图 / question-side asset: Venn diagram](references/assets/ch01_p012_quiz_1_1.png)`.
- Do **not** emit slash-prefixed Windows drive-letter pseudo-paths in Markdown image links.
- If a host requires an absolute path and you have verified that it renders, use that host's supported form. Otherwise show the normal local path as an instruction (for example `D:\course\ws\references\assets\a.png`) and treat the image as **not displayed** for the contract above.
- The skill must not claim that an image was displayed when it only printed a path or a non-rendering Markdown link.

### Path-safety rules (enforced by the validator)

- An asset must be a relative path inside the workspace (preferably under `references/assets/`). Absolute paths, `..`, URL/network paths, and symlink escapes are forbidden. Every host enforces portable Win32 constraints: no path segment may end in an ASCII space or dot; contain control characters or `< > \" | ? * :`; or use a reserved device name such as `CON`/`PRN`/`AUX`/`NUL`/`COM1..9`/`LPT1..9`, including with extensions and Win32-recognized superscript-digit variants. Therefore `a.png.` and `NUL.txt`, for example, cannot become physical aliases of another safe path on Windows.
- `requires_assets=true` or `maybe_requires_assets=true` requires nonempty, readable, safe assets and at least one prompt-side asset; missing assets or answer-side-only assets are errors. A `stub` needs a source page or prompt-side asset; a `page_reference` needs a safe `source_file` plus positive-integer `source_pages`.
- Contract violations in Boolean/string/enum/page types produce structured errors. A missing non-required asset produces only a warning. Old question banks without these fields remain valid, and any question type may require an asset.

### Dual visual indexes (P0-V2, recall first)

`build_visual_index.py --workspace <ws> --materials <dir>` generates two rebuildable indexes:

- `image_question_index.json`: per-question requires/maybe flags, prompt/answer assets, source pages, and answer status. `prompt_suspects` and `answer_suspects` respectively mean that a visual source page lacks a prompt or answer asset; legacy `suspects` is an alias only. `prompt_suspects=0` does not prove answer/wiki completeness.
- `figure_page_index.json`: detected pages, visual types, and detected/embedded/missing `wiki_visual_coverage`, grouped by chapter with per-page reasons. Detection uses recall-first structural, layout, then lexical heuristics. Without structural capability it writes `media_signals=false` and emits a warning, never claiming complete human semantic coverage.

By default the tool only reports. `--apply` first jointly preflights the quiz bank, teaching examples, content units, and every prompt/answer repair target in the batch. Reuse of one physical file across prompt and answer sides of the same logical item, reuse of any `student_attempt` physical identity, an unsafe/schema conflict, or incompatibility with existing ownership makes the whole batch fail before creating a backup, writing images, changing the bank, or replacing indexes; original bytes and existing derived files stay unchanged. When there is no student-attempt taint, legitimate official prompt/answer reuse across different items remains idempotently rebuildable. Only after preflight passes does it back up the bank and attach either `question_context` plus `maybe_requires_assets=true`, or only `answer_context`. `--apply-wiki` idempotently back-links by page anchor (30 pages per chapter by default); overflow/failure remains in missing. After writing, it rereads all three sides.

The global ordering gate puts answer-only pages in `deferred_answer_pages`, excluding them from concepts/wiki galleries. Legacy or hand-embedded images whose ownership cannot be proved enter `manual_answer_exposure_pages` and block with nonzero status. A page containing both prompt and answer enters `shared_prompt_answer_pages`; without a reviewed crop it also enters `shared_prompt_answer_blocker_pages`. Every `*_count` must equal its array length. A complete manifest missing a safe array must be rebuilt; it cannot default to empty to bypass answer leakage. Only a true legacy workspace with no manifest trio uses the compatibility route. Printing a path or ignoring a nonzero exit is not a repair.

Tools: `list_image_questions.py`, `list_figure_pages.py`, and `show_question_assets.py` (outputs the prompt-image Markdown that must be displayed first; contract violation exits 1). NUL/control bytes in PDF text emit a warning; returned text does not prove that a spatial diagram survived extraction.

## 5. Question-tag system (A2, optional and backward-compatible)

A question `id` and a teaching-example ID share the same stable technical-key contract. Current ingestion always writes a safe Unicode string of 1–200 characters. Whitespace; control, format, surrogate, replacement, or Unicode noncharacter code points; and ``[]#|`/\`` are forbidden. A finite numeric legacy input is normalized to a string before publication; when an input omits an ID, the compiler still generates a stable `qN`. An existing legacy bank with integer IDs remains read-only compatible and compares them by their string forms. float/bool IDs, illegal characters, and IDs duplicated after string/integer normalization cannot enter runtime, progress, or a typed Guide.

When the same technical key enters `.ingest/content_units.jsonl`, it is stored as `ContentUnit.external_id`, so parser/review/add/replace boundaries enforce the same contract. A free-form number or display title printed in the original handout belongs in metadata/text fields. It cannot use `external_id` to bypass later Guide gates.

Optional fields on each item (old question banks remain valid):

| Field | Value | Meaning |
| :-- | :-- | :-- |
| `source_type` | `homework` / `lecture_quiz` / `example` / `practice_exam` / `exam` / `other` | Question-source category (orthogonal to `source`, which labels **answer** provenance) |
| `knowledge_points` | Array of nonempty strings | Knowledge-point tags assessed by the item |
| `difficulty` | Integer 1–5 | Difficulty (written back by the A7 scorer; manual annotation is also allowed) |
| `difficulty_reason` | Nonempty string | Reason for difficulty (for example “multistep conditional distribution”) |

The default pool mixes source types. After a scope is persisted, a temporary out-of-scope selection must first announce `⚠️ 临时覆盖你的 <范围> 范围偏好`; a restricted pool excludes and counts items without `source_type`. `select_questions.py` composes filters; optional SQLite is only a generated cache. Knowledge postings enter `retrieval_index.json` directly. The builder may produce `source_type="homework"` and retain question/answer pages; other values require trusted annotation.

## 6. Teaching-example layer (`teaching_examples.json`)

The builder may emit `teaching_examples` separately from `quiz_bank`, and ingestion writes the corresponding file:

- `quiz_bank.json` is the only grading/answer source. Its items must be suitable for selection, student response, and comparison against an answer.
- `teaching_examples.json` is only a reachability manifest, not a second answer source. An Example with a complete demonstration but no independent answer may remain outside the bank. Each item contains a unique `id`, chapter/phase, `paired_problem|worked_example`, prompt/answer sources, and assets; its ID may overlap the bank. `id` is the technical key shared across the manifest, notebook, and typed Guide. After trimming leading/trailing whitespace, it must be 1–200 characters and may use safe Unicode (such as `例题1` or `über_2`), but must not contain whitespace; control, format, surrogate, replacement, or noncharacter code points; or ``[]#|`/\``. If a printed question number/display name violates this contract, preserve it in a title, source, or other display-metadata field. Do not put it in the equally constrained `ContentUnit.external_id`, and do not cause an unmigrated ID drift by rewriting it to ASCII-only. If the ID itself cannot form a GitHub Markdown slug (for example, it contains only emoji/punctuation), notebook insertion also requires a human-readable title that yields a nonempty anchor.
- The tutor lazily reads only the current chapter through `list_teaching_examples.py --workspace <ws> --chapter <N> --json`. It may add `--next-pending` only when stored `preferences.interaction_style=step_by_step`, `processing_mode=full`, and `no_questions=false` make step mode effective. In all other cases, effective cadence is `batch`; a stored step preference remains dormant in lightweight/no-questions mode. `--next-pending` requires `<N>` to equal `current_phase`. Under one workspace lock it reads a consistent snapshot of the manifest, state, notebook bindings, and baseline, returns the first pending item in manifest order, and reports total/completed/pending/next, `teaching_example_roster_exhausted`, and unexpected evidence outside the chapter manifest. Two bindings must not reuse one `notebook_ref`. The operation fails closed on a missing manifest, duplicate ID, unresolvable item scope, damaged binding/state structure, parent/leaf reparse point, non-directory or non-regular file, realpath escape, invalid UTF-8, unterminated fenced block, parse/block corruption, or unexpected evidence. Only a missing notebook file/entry or drift in anchor/marker/hash/revision may demote completed evidence to manifest-order pending as a stale binding. It reports bounded stable problem codes through `stale_binding_count`, at most 32 manifest-order `stale_binding_ids` / `stale_binding_problems`, and `stale_binding_diagnostics_truncated`. A canonical mount reports a structurally valid stale binding or append-only new current-roster item as `usable_with_gaps`, preserving only the recovery route that reteaches and records in manifest order. Structural/scope/baseline corruption remains `blocked`, and the old Guide and completion receipt cannot be reused. Never infer progress from notebook-file presence or the word “Continue.” The lock makes one read internally consistent but is not a reservation, so two concurrent tutors may still receive the same pending item. Lightweight mode does not call this selector.
- `teaching_baseline.json` is a retained fact whose `policy` is exactly `append_only`: ingestion may only merge new IDs. Every ID must retain a current teaching snapshot in `teaching_examples.json` under the same canonical chapter. The same ID remaining in `quiz_bank.json` cannot substitute for the teaching snapshot or make the roster legally exhausted. A disappearing ID cache, cross-chapter drift, or disagreement between per-chapter mappings and the complete set fails loudly; never hand-edit or clear it. Only a legacy workspace without this file reads `ingest_report.json.teaching_example_ids`.

When old input omits the field, ingestion neither creates nor overwrites it. Only an explicit empty array means the producer confirms there are no examples.

## 7. Phase evidence (`study_state.json.phase_evidence`)

A phase cannot complete solely through `phase_checklist[].done=true`. On the explicit full route, `phase_evidence[phase]` contains:

- `wiki`: a `references/wiki/*.md` path, which must match the wiki assigned to that phase in `study_plan.md`.
- `visual`: the two visual manifests or references to local assets under `references/assets/`.
- `teaching_examples`: teaching-example IDs for the current phase. Every item must be recorded when the phase manifest is nonempty; when empty, this evidence is N/A. IDs without a corresponding binding are legal batch/legacy teaching history and must not be deleted or rewritten merely because cadence changes.
- `teaching_example_bindings` (optional): an object array containing only per-item evidence produced by `record-taught-example`. Each object's fields must be **exactly** `{ "id", "notebook_ref", "notebook_block_sha256", "manifest_item_sha256" }`. The `id` must also occur in this phase's `teaching_examples`; `notebook_ref` must identify that ID's `walkthrough` block with the reserved marker; and two bindings must not reuse one ref. The two SHA-256 values bind the complete notebook block and current manifest item. Once a binding exists, its live marker/type/ID/anchor, notebook-block hash, manifest-item hash, and corresponding notebook evidence must continue to match even after switching back to `batch` or making the step preference dormant.
- `notebook`: a `notebook/*.md#real-anchor`; both path and anchor must exist and belong to the current chapter.
- `checkpoint`: `{ "id": "question-bank ID", "outcome": "passed|wrong|skipped" }`. An ID alone does not prove a correct response, and the item must belong to the current phase.

Write evidence with `update_progress.py ... record-phase-evidence --kind <kind> --ref <ref> [--outcome passed|wrong|skipped]`. Complete a phase with `complete-phase --status covered_unverified|verified [--next-phase N]`.

Full-mode single-item cadence must first write a complete seven-step walkthrough with `notebook.py add-entry --type walkthrough --teaching-example`, then pass its real anchor to `record-taught-example --id <id> --notebook-ref <path#anchor>`. If the ID yields an empty Markdown slug, provide a descriptive title that yields a nonempty anchor. Under the same workspace lock/save, the latter verifies effective step mode, the current full phase, the first pending manifest item, walkthrough type, ID, and reserved marker, then atomically writes ordinary evidence, the notebook ref, and the four-field binding above. Step-by-step evidence must not be split into two generic evidence commands. Guide notebook publication preserves a valid bound marked walkthrough byte-for-byte. It fails closed on a stale binding or on a notebook marker without a valid binding; Guide rewriting cannot “refresh” evidence. If re-ingestion appends a new item to the current roster in the same chapter or reveals a recoverable stale binding, mounting downgrades an old completed phase only to `usable_with_gaps`; structural/baseline corruption remains `blocked`. Recording the first pending item in order clears the old `status/done`, and the chapter must rebuild its Guide and complete again. A student's “Continue/I understand” is routing input, not completion evidence. `teaching_example_roster_exhausted=true`, including a zero-example roster, means only that the full teaching roster has no pending item; it cannot bypass Guide, bank, typed-unit, asset, checkpoint, or phase gates, and it does not affect independent lightweight batch evidence. Progress is keyed by stable question ID; switching output language does not automatically reset recorded IDs.

On the full route, `covered_unverified` requires complete wiki, visual, notebook, and nonempty teaching-manifest coverage. `verified` additionally requires at least two distinct handled checkpoints and at least one pass. A workspace with `.ingest/` must also validate the current chapter's `profile=full` typed Guide; that validator owns denominator and unit/ref completeness.

The lightweight route instead uses the `lightweight_batches` array. Each current strict event binds a `batch_id`, visual/teaching receipt IDs, `notebook/chNN.md#anchor` and its entry hash, source hash, ascending `inspected_pages`, and sorted-unique stable `taught_item_ids`. Page numbers are visual context only and cannot pretend that every item on a page was taught. Legacy `pages` events may remain only with a terminal legacy attempt as non-upgradable audit history. At phase completion, every batch currently declared for the phase must be `taught`, and the event set must match those batches exactly one-to-one after rechecking live source, visual assets, and notebook entry. It does not read or require leftover full-route wiki/visual/teaching evidence and does not load the typed-Guide gate. Lightweight may reach `covered_unverified`; `verified` still requires at least two distinct handled checkpoints from an existing standard bank and at least one pass. `no_questions=true` caps completion at covered; `≤1天` by itself does not forbid bank checkpoints.

The `integrity` objects in both visual indexes must bind, from one generation, schema/time/mode, bank, teaching/baseline/report, wiki, assets, images counted in coverage, PDF content/path hashes, and each canonical output. Completion rehashes these inputs; any drift makes the indexes stale and requires rebuilding. required/maybe items also receive a live readability check for a prompt-side asset; an old snapshot or empty suspects list is insufficient.

Only a complete visual/teaching trio enables the old full-evidence hard gate. A true legacy workspace may receive compatibility warnings, while a partial or broken new manifest fails loudly. `.ingest/` independently enables the full typed-Guide gate; a standing visual choice additionally requires `artifact_ready=ready`. Lightweight uses the batch-event gate above. Both routes can complete only the current phase and advance only to its immediately adjacent next phase in the plan.

## 8. Validator-result semantics

`scripts/validate_workspace.py --json` reports two dimensions:

- `ok=true` / `exit_code=0`: structured validation completed with no globally fatal errors; warnings may still exist.
- Top-level `readiness=ready|usable_with_gaps|blocked` remains a compatibility summary, but an action must also inspect `capabilities.workspace_structural|teaching_ready|quiz_ready|artifact_ready`. For example, readiness for chat teaching does not imply readiness for grading, and the existence of an HTML/PDF file does not mean a Study Guide passed visual inspection.

Therefore schema validity, `prompt_suspects=0`, or any single 100% coverage value must not be rewritten as “all content is complete.” A higher-level report must preserve the true denominator, every remaining warning, and the original readiness term.

## 8.1 Lightweight on-demand sessions

`study_state.json.processing_mode` allows only `lightweight|full`. A missing, legacy, unknown, or wrongly typed value is effectively treated as `lightweight`; only explicit `full` opens `ingest_course.py`. This choice is independent of `artifact_mode=chat|visual`.

`study_state.json.answer_explanation_mode` allows only `ordinary|isolated` and is independent of both choices above. The storage-level safe fallback for a new, missing, legacy, or invalid value remains `ordinary`. `ordinary` still requires every ingestion-v2 Guide item to have a detailed beginner-first `answer_explanation`, but the explanation is written directly in mode-bound annotations and carries no isolation receipt. Only `isolated` enables per-item requests, coverage, host receipts, and the final contract. At entry to a full-v2 Guide, the host performs a capability handshake. It may default to `isolated` when the user has not opted out only if it can prove that each item starts in a fresh independent child context and can restrict that child's input and tools to the exact single-item request. It tells the user once that this consumes additional model quota/time on the current host. This native route requires neither a second API key nor external-upload consent. If a capability is missing, inherited, or unverifiable, remain in `ordinary`. A separately billed external Provider is only a fallback after the user explicitly names it and still requires two-stage consent: an exact no-upload plan, then exact-plan upload consent after checking per-item/image scope, call count, current price, and retention/privacy boundary. Model family, subscription, API key, `full`, or `visual` does not prove isolation capability or authorize external upload.

Lightweight mode creates no `.ingest/`, wiki, question bank, Study Guide, or PDF. Existing full artifacts may remain but cannot enter the lightweight completion denominator. Material-processing state lives in `.lightweight/session.json`; session schema 2 has these top-level fields:

```text
schema_version; session_type=on_demand_visual; processing_mode=lightweight;
workspace; materials; created_at; updated_at; source_inventory[]; batches[];
quiz_bank_baseline; migration_history[]
```

Initialization records only safe relative filenames, media types, sizes, and `mtime_ns`; it does not read bodies and writes `content_sha256=null`. `plan` accepts only PDF pages or a definitely single-frame PNG/JPEG/BMP page-equivalent with `chapter=current_phase`. Lightweight mode neither guesses nor flattens GIF/WebP/TIFF and similar formats. Each batch has at most 8 pages, and the entire session has at most one active `planned|visual_ready` batch. A batch binds the primary source revision, chapter, ascending unique page numbers, `answer_dependencies[]`, and the state sequence `planned -> visual_ready -> taught`. An unfinished attempt may enter `abandoned`; when taught evidence must be redone, it may enter the auditable terminal state `superseded`:

- If the current phase already has a complete lightweight completion badge and the student plans a new exact slice that is not yet taught, `plan` first publishes the planned batch, then revokes phase status/completion time/mode and `checklist.done`. If the second step is interrupted, rerunning the exact same plan only finishes reopening progress. A different slice, different revision, or partially damaged completion record fails closed. Replanning an already taught identical slice remains idempotent and does not reopen the phase.
- `record-visual` requires a page-by-page bijection over the planned pages, `inspection_method=model_visual`, and one independent visible asset per page. A one-page batch has `contact_sheet_groups=[]` and uses the page image directly. In a multi-page batch, each contact sheet holds at most 4 pages and all primary planned pages must be partitioned exactly once. Contact sheets are `overview_only` and cannot replace per-page assets. With fixed row-major layout, the minimum is approximately 768 px per tile: 1536×768 for 2 tiles and 1536×1536 for 3 or 4 tiles.
- `.lightweight/session.json` remains session schema 2; a new `lightweight_visual_batch` receipt is fixed at schema 3. Every primary page must enumerate stable `teaching_item_ids` for items with a prompt component on that page. The same cross-page item may repeat on multiple pages. Top-level `teaching_items[]` must be in exact bijection with the union of all page IDs and is no longer nested under `figure_questions`. Every prompt component's parent page must declare that item, and every page/item declaration must in turn be covered by at least one prompt component from that page.
  Each item declares `kind=text|figure|mixed`, nonempty `prompt_components[]`, possibly empty `answer_components[]`, and `answer_display_phase=solution_or_review_only`. `kind` must truthfully follow prompt-component roles, so a text-only question with an official answer in another file is not misreported as an image question.
- Every component declares a unique `component_id`, `component_role`, ascending-unique `required_context_ids`, exact `allowed_detected_item_ids`, and a source-qualified crop binding. Prompt allowed IDs may be only `[target] + sorted(contexts)` or nonempty context-only `sorted(contexts)`. An answer component must use the former and contain the target; a context-only crop cannot masquerade as an answer. Every component must exactly cover the contexts it declares, and every item must have at least one prompt component in which the target is visible. A figure/mixed item must contain a visible figure/diagram/table component; a text item must not falsely claim an image.
- `register-answer-dependency --batch-id <id> --source <path> --pages <range>` remains an additive-union operation during `planned`. `set-answer-dependency ... --pages <exact-range> --reason <reason>` replaces or narrows the exact pages of one bound source; `remove-answer-dependency ... --reason <reason>` removes it. Every real change writes hash-bound `answer_dependency_history`. Retrying the same set does not append an event. Retrying the same remove must reuse the original reason and returns `changed=false`; an unknown or never-bound source still fails closed. A batch has at most 4 dependency sources and at most 4 dependency pages in total; sources must also be PDFs or single-frame PNG/JPEG/BMP files.
- Every `dependency_page` must have page-by-page coverage and `purpose=answer_locator_only`. It is locator/detail context only and the full page must never enter solution. Both primary and dependency pages explicitly declare `content_types` and `answer_provenance=student_attempt|official_solution|none|unknown`; the declarations must agree. Only an `official_solution` parent may produce an answer component, and every registered answer page declared official must be covered by at least one answer component. Multiple answer pages and components are allowed. A student-attempt or unknown page may remain locator/detail context but cannot satisfy material-answer, solution, or teaching-answer evidence.
- Each `model_calls` row binds a unique `call_id`, `host`, `model`, source-qualified `locations` carrying `source_id/source_path/source_sha256/page`, and each input asset's path/hash. Bare page numbers are insufficient. Every contact sheet enters exactly one `overview` call containing only that sheet. Primary/dependency pages and prompt components that need detailed inspection enter `detail`; one detail call may combine multiple prompt components only when they belong to the same target. Answer components enter only `solution`, and one call likewise belongs to only one target. A component crop must not be consumed twice across ordinary stages.
- Every prompt/answer component also has exactly one independent `crop_review` visual call whose only input is that crop. It binds crop hash, target, side, component ID/role, context/allowed IDs, model invocation, and time. Through `model_vision`, it must detect IDs exactly equal to `allowed_detected_item_ids` and prove that no unrelated prompt/answer or student attempt is present. A bbox, filename, or successful script crop is not itself proof of semantic purity.
- All visual assets must live under `.lightweight/assets/` and must not reference or reuse a full-build asset path. Whether the source was PDF, PNG, JPEG, or BMP, canonical page/contact/prompt/answer/dependency evidence must be a readable, non-link-backed PNG whose extension agrees with PNG magic bytes and whose SHA-256 and measured dimensions are bound. The general crop minimum is 64×64; a page/dependency page is at least 480×480; a contact sheet follows the 768 px/tile minimum above.
- `abandon --batch-id <id> --reason <specific reason of 5-500 characters>` may close only `planned` or `visual_ready` as `abandoned`. It preserves prior status, reason, time, and a digest-bound receipt. It cannot delete an old attempt, rewrite its reason, or abandon `taught`. The same source/revision/phase/pages may later be `plan`ned as a new batch with an incremented attempt suffix. An `abandoned` attempt is outside the phase-completion denominator and does not consume the active slot.
- `replace-taught --batch-id <id> --reason <specific reason>` is the only taught-redo route. It changes the old batch to `superseded`, retaining the complete visual/teaching receipts, notebook binding, original progress event, reason, and successor ID, then creates a new `planned` attempt for the same primary source/chapter/pages slice. The successor retains the exact pages of the original dependencies, while rehashing and validating current source revisions, and records inheritance in `inherited` history. The old attempt/event is never deleted but leaves the current-batch denominator. The new attempt must repeat the schema-3 visual, teaching, and publication workflow.
- An old schema-2 visual receipt and the `dedicated_figure_question_assets` legacy token strategy are immutable read-only history. An active attempt using a legacy strategy, including `planned` without a receipt, permits only `status` or an auditable `abandon`; dependency changes, replanning, `record-visual`, and `mark-taught` are forbidden. A legacy `visual_ready` attempt is likewise quarantined read-only. A schema-3 receipt must bind the `dedicated_teaching_item_component_assets` generic strategy. The only exit for a legacy active attempt is an auditable abandonment with a reason, followed by a separate schema-3 attempt. Never silently upgrade an old strategy or receipt.
- `mark-taught --notebook-entry notebook/chNN.md#anchor --taught-item-ids <id1,id2,...>` requires the anchor to uniquely identify one durable notebook entry in the current chapter. It revalidates the current phase, source revision, and live hash/magic/dimensions of every visual image, and requires the supplied IDs to exactly equal every teaching item enumerated by the visual receipt. The new taught receipt/event stores `inspected_pages` separately from `taught_item_ids`. Under the workspace publication lock, the command first atomically writes the taught receipt/session and then atomically publishes the canonical event to `study_state.json.phase_evidence[phase].lightweight_batches`. These two files do not form a single filesystem snapshot for arbitrary readers. If the second step is interrupted, repeating the identical `mark-taught` recognizes the same receipt and idempotently completes the progress event.
- `status` obtains a genuinely read-only snapshot by retrying until session/state file generations remain unchanged. It neither creates a lock nor opens an existing lock in write mode. Routine mounting in the workspace validator performs only bounded metadata and physical-identity checks and does not stream-hash sources/assets. Exact stream hashes are computed only by `plan`, dependency registration/replacement/removal, `record-visual`, `mark-taught`, phase completion, and explicit `status --verify-live`. A `taught` batch in another phase still has its immutable session-receipt/progress-event identity checked and counts as `unchecked_historical`, not “live reverified.” It returns to current live scope only after switching back to that phase.
- Teaching explanations remain detailed and beginner-friendly; reducing input tokens must not shorten them.
- Metadata/physical-identity drift immediately invalidates a batch; exact-hash drift at a critical transition also fails closed. Replan an unfinished attempt and use `replace-taught` for an already taught attempt. Neither route may rewrite or reuse old visual/teaching receipts.

`.lightweight/session.json` records only material-page processing state. `study_state.json` remains the sole source of truth for learning phases, mistakes, confusions, and the knowledge window. On first `init`, the session freezes an immutable stat-only baseline for the then-current `references/quiz_bank.json`: existence, size, mtime, and physical identity. It neither opens, parses, nor hashes the bank at startup. If the bank was absent then, is added later, is replaced, or drifts from that baseline, it cannot support lightweight `verified`. Only an explicit selection, checkpoint recording, or completion transition opens the bank, applies the shared runtime-eligibility gate, and creates `bank_binding_id`, `bank_sha256`, and `item_sha256` for a qualifying item. `verified` requires two distinct revision-bound handled checkpoints and at least one `passed`. Legacy `{id,outcome}` rows may remain as history but do not count toward the lightweight verified denominator. Without a qualifying pre-existing standard bank, never fabricate a quiz; the phase is capped at `covered_unverified`.

`artifact_mode` is a separate persistent preference. In lightweight mode, even when the stored preference is `visual`, status must report `artifact_mode_preference=visual`, `artifact_mode_effective=chat`, and `artifact_mode_dormant=true`. Study Guide author/import/render/QA remains forbidden. Only after explicitly switching to `processing_mode=full` and confirming again does the preference become active. An ordinary reconfirm that omits `--processing-mode` preserves an existing canonical choice; only a new, missing, legacy, unknown, or wrongly typed value safely defaults to `lightweight`.

## 9. Mathematical sources of truth and human-readable teaching artifacts

Markdown is a searchable, diffable, traceable source of truth; it is not a typeset textbook. `study_state.json.artifact_mode` allows only `chat|visual`:

- A legacy workspace with no field behaves like `chat`: preserve ordinary chat, state, and notebook without automatically compiling HTML/PDF.
- `visual` must be an explicit persistent user choice and follows typed manifest → render → receipt → full-page QA. Delivery/completion is allowed only when `artifact_ready=ready`; a one-shot print request temporarily overrides output without changing the preference.
- When `processing_mode=lightweight`, `visual` is only a dormant preference, effective mode remains `chat`, and neither a one-shot nor standing request may bypass the full-processing gate.
- An unknown value is treated as `chat` with a warning. Never infer a subscription tier, and no value authorizes silent installation.

Write the setting with `update_progress.py --workspace <ws> set --artifact-mode chat|visual`.

TeX uses only `$...$`/`$$...$$`; ordinary parentheses/brackets or bare commands are not delimiters. The validator ignores code, but raw/pseudo-LaTeX produces a warning and downgrades readiness to `usable_with_gaps`; it does not guess at formula corrections. Validate and atomically import a typed manifest before producing the human-readable version:

```text
python scripts/study_guide_content.py --workspace <ws> validate --chapter <N> --input <draft.json> --json
python scripts/study_guide_content.py --workspace <ws> import --chapter <N> --input <draft.json> --json
```

v2 first requires an exact-location claim receipt for the workspace-local draft. Under the ingestion mutation lock, import revalidates live facts through publication, invalidates old HTML/PDF/render/QA first, and then publishes the signed manifest. v1 must not fabricate a receipt.

`notebook/chNN.guide.json` is the renderer/completion-gate input. `full` must cover the current chapter's teaching examples, every bank item (`gradable=false` items serve only as teaching examples), and deduplicated IDs of typed question units. `abridged` requires a complete omission ledger and cannot complete a phase. `source_unit_ids` and reasoned `semantic_exclusions` must exactly partition material/AI-recovered semantic units; formulas cannot be excluded. This proves only an explicit denominator. Every item records source/answer provenance, prompt language, formulas/variables/substitutions/steps/answer, a detailed per-item answer explanation, and source trace. Current ingestion-v2 `authoring_protocol_version=2` forbids legacy `self_check`, requires `answer_explanation` plus per-language `ai_supplement` provenance for every item, and uses top-level `answer_explanation_mode` to choose the receipt contract explicitly: `ordinary` forbids an `answer_explanation_contract` and per-item receipts; `isolated` requires exact request/response/provider receipts and a top-level contract. A historical protocol-v2 manifest without mode but with a complete isolated contract that remains currently verifiable may pass only the canonical read-only seam `study_guide_content.py validate --chapter <N>` **with `--input` omitted**. It cannot be imported, complete a phase, render, or receive QA. Every explicit input, library-level ordinary validator, and new write must include mode; the compatibility path must never disguise ordinary as isolated. When a strict crop-receipt upgrade changes only packet/asset binding, `study_guide_author.py rebase-annotations` may, under the publication lock, update only permitted packet/mode bindings and remove old paired `self_check` fields. Nothing is published before the complete validator passes.

Only `isolated` creates per-item model requests. The preferred invocation is a native independent child agent of the current host; an external Provider is allowed only after the user explicitly names it and completes both authorizations. Each invocation sees only this item's exact question, answer, target language, fixed beginner-first prompt, and target-scoped assets: `target_item_only`, or prompt-only `target_with_required_context` with exact sorted `required_context_ids`; an answer must be `target_item_only`. Attachment bindings must preserve `semantic_purity_schema_version`, `required_context_ids`, and exact `isolation`. Model output may contain only `answer_explanation` and non-rendered `coverage`. For every target language, `coverage` lists the addressed subparts and at least two reasoning steps and confirms coverage of the formula/rule and final meaning. Both enter the response hash, append-only ledger, and final receipt; the typed Guide copies only the explanation body. An independent host receipt supplies and binds provider/model/invocation/fresh-context-or-stateless/tool-disabled declarations to the exact input hash. It is a host declaration, not a sandbox or model self-attestation. Old schema-1 ledger events are non-reusable history only. `ordinary` creates none of these files or declarations, but its explanation body remains subject to the same beginner-first detail, provenance, no-answer-self-check, and rendering gates. Both routes require answer/solution evidence for a material answer and a visible label for an AI answer. Prompt and answer must bind the exact normalized payload; `notebook_anchor` must already be persisted; a `full_prompt` image replaces repeated source/OCR text and adds only translation; `figure_only` does not replace prompt text.

Changing language or explanation mode makes old manifests, artifacts, notebook bindings, claims, and QA stale. v2 forbids changing only manifest language/mode or relocalizing an old explanation. Starting at `study_guide_author.py prepare`, repeat target-language and target-mode annotations, notebook persistence, compilation, claims, and verify/import. `isolated` must also repeat every per-item request and receipt chain. ingestion-v1 may only validate its existing canonical manifest in place; it cannot import, relocalize, render, or obtain a new completion/QA result. Visual mode then rerenders and inspects every page.

After the checklist gate passes, render through an explicit backend:

```text
python scripts/study_guide_render.py --workspace <ws> --chapter <N> --profile full --pdf-backend html
python scripts/study_guide_render.py --workspace <ws> --chapter <N> --profile full --pdf-backend browser --pdf
```

The output is HTML, optional PDF, and a schema-3 receipt. The renderer guarantees MathML/data-URI handling, visible answers, and example deduplication. The receipt binds typed manifest, HTML, canonical PDF path/hash, backend, conversion input, time, start-gate identity, and conversion-run hash as one chain. `native` must also record an adapter ID and exact version from the machine table's allow-list, then atomically transition from `awaiting_native_pdf` to `qa_pending` through `study_guide_render.py --bind-native`. An unbound, partially bound, or side-loaded PDF cannot be accepted. `source_packet` is diagnostic only and does not satisfy `artifact_ready`. After PDF generation, run `study_guide_qa.py render`, inspect every PNG, then accept with `accept --inspected-pages all` plus a `--page-verdict N=pass:<notes>` for each page. Any hash drift makes the result stale. `visual_qa.status=ready`, complete page evidence, and zero defects are all mandatory. See [`pdf-capability-adapters.md`](pdf-capability-adapters.md) for backends; always inspect the latest full-page rendering before delivery.
