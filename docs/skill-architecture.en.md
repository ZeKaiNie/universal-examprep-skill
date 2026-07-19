# Skill Architecture

English · [中文](skill-architecture.md)

This skill collection is organized as “language-neutral entry point → single control layer → replaceable wording layer → structured ingestion fact layer → compiled learning layer.” The goal is to let different hosts discover the entry point while preventing rules, languages, and derived artifacts from drifting independently.

## 1. Entry points and sources of truth

- [`SKILL.md`](../SKILL.md) is the language-neutral router: it reads the canonical value of `study_state.json.language`, then locates the main skill and the corresponding wording pack. It no longer carries the complete workflow.
- [`skills/exam-cram/SKILL.md`](../skills/exam-cram/SKILL.md) is the main orchestrator; each `skills/*/SKILL.md` owns the behavioral contract for one responsibility.
- [`locales/zh/SKILL.md`](../locales/zh/SKILL.md) and [`locales/en/SKILL.md`](../locales/en/SKILL.md) are lightweight compatibility indexes, not separate Chinese and English behavioral sources of truth.
- [`AGENTS.md`](../AGENTS.md) is the condensed safety baseline for generic agents that do not read the complete skill collection; detailed behavior remains governed by the relevant sub-skill.

## 2. Skill collection

```text
skills/
  exam-cram/          # Checkpoint restoration, mode selection, and phase orchestration
  exam-ingest/        # Material ingestion, warning takeover, and workspace initialization
  exam-tutor/         # Lazy teaching for the current chapter and seven-step walkthroughs
  exam-study-guide/   # profile=full typed manifest → HTML/PDF → receipt/full-page QA
  exam-quiz/          # Question-bank selection and grading
  exam-review/        # Mistake and confusion review
  exam-cheatsheet/    # Pre-exam cheatsheet
  exam-audit/         # Read-only workspace health check
  exam-help/          # Quick-reference card
  confusion-tracker/  # Concept-confusion tracking
```

The complete path for concept-confusion tracking is [`skills/confusion-tracker`](../skills/confusion-tracker/SKILL.md). Like the other sub-skills, it lives in the shared control layer rather than in a language pack.

Each sub-skill retains the six-section structure Purpose / Activation / Inputs / Workflow / Output Contract / Boundaries. Cross-skill relationships use links instead of duplicating entire workflows.

The default material route is `processing_mode=lightweight`: `exam_start` registers the exact materials/workspace pair, runtime receipt, and learning choices, while `scripts/lightweight_session.py` maintains on-demand page batches for the current phase. The model visually reads only the current PDF pages or PNG/JPEG/BMP files that are definitely single-frame; it neither invokes full ingestion nor produces a Study Guide/PDF. Each batch contains at most eight primary pages, and at most one batch may be active. A single page does not create a contact sheet. Multi-page overview contact sheets partition pages exactly in groups of at most four, row-major, at approximately no less than 768 px per tile. New visual receipts use schema 3: primary pages enumerate stable `teaching_item_ids`, and top-level items declare `text|figure|mixed` plus generic prompt/answer components. A detail call may combine only prompt components for the same target; a solution call may combine only answer components for the same target. Each component then receives an independent single-image visual review that must detect exactly its declared target/context IDs and exclude unrelated content and student work. When an answer lives in another source, incremental `register-answer-dependency` binds only the required pages. While planned, `set-answer-dependency` may replace or narrow that dependency, and `remove-answer-dependency` may remove it with an audit record. A dependency page is locator/detail evidence only; only an official parent may supply an answer component, and every registered official page must be covered. Every model input binds path/hash/host/model and source-qualified locations. All canonical evidence is isolated as PNG under `.lightweight/assets/` and verified by magic/dimension/hash checks. An unfinished attempt may be closed and replanned only through an `abandon` receipt carrying a reason/digest. Legacy schema-2 `visual_ready` attempts are quarantined read-only and may only be auditably abandoned; they are never silently upgraded. Taught progress cannot be abandoned. When it must be redone, `replace-taught` preserves the superseded attempt/event, revalidates and inherits the exact answer dependencies, and plans a successor for the same primary slice. Only explicit `processing_mode=full` enters `exam-ingest`. Both routes share the `study_state.json` learning state machine, while the lightweight route additionally stores page-batch evidence in `.lightweight/session.json`. An ordinary reconfirm without a processing flag preserves the existing canonical choice; only a new, missing, legacy, or invalid value safely defaults to lightweight.

`preferences.interaction_style` is an optional teaching cadence orthogonal to processing/artifact/answer-explanation mode. It stores only `batch|step_by_step`; new state or legacy state without the field uses `batch`. A stored step preference is effective only when `processing_mode=full` and `no_questions=false`. Otherwise the effective cadence is `batch`, while the original preference is retained and reported as dormant. The per-item route takes a consistent manifest/state/notebook/baseline snapshot under the workspace lock and reads the next item in manifest order. After writing the complete seven-step walkthrough, `record-taught-example` atomically records the ID, anchor, notebook block hash, and manifest item hash. A `teaching_examples` ID without a binding is legal batch history; once a binding exists, it must pass live validation regardless of the current cadence. A Guide preserves a marked block with a valid binding and rejects either a stale binding or a marker without a valid binding. This cadence does not alter the lightweight page-batch state machine and does not treat “Continue” or the mere existence of a notebook file as completion.

## 3. Lifecycle routing

| User action | Primary skill | Main current slice read |
| --- | --- | --- |
| Provide materials and use default lightweight learning | `exam-cram` / `exam-tutor` | Current raw-material pages and `.lightweight/session.json` |
| Explicitly build the complete knowledge base | `exam-ingest` | Raw-material directory, `.ingest/` source inventory, and typed review |
| Learn the current chapter | `exam-tutor` | One wiki chapter plus the current chapter’s teaching-example slice |
| Answer current-chapter questions | `exam-quiz` | Current-chapter filter results from the question bank |
| Review mistakes/confusions | `exam-review` | Unmastered state items plus the corresponding questions |
| Build/generate a chapter textbook | `exam-study-guide` | Verified `notebook/chNN.guide.json` plus its referenced assets; the receipt/QA is visual-delivery evidence |
| Produce a final-cram cheatsheet | `exam-cheatsheet` | Mistakes, confusions, knowledge window, and wiki |
| Run a health check | `exam-audit` | Workspace inventory and consistency evidence |

Lightweight batches advance through `planned → visual_ready → taught`. `record-visual` verifies the page bijection, schema-3 generic items/components, and the division of overview/detail/solution/crop-review inputs. Detailed teaching first writes `notebook/chNN.md#anchor`; then `mark-taught --taught-item-ids <exact IDs>` publishes the taught receipt and `phase_evidence[phase].lightweight_batches` under the workspace lock. If progress publication is interrupted after the receipt, rerunning the same command repairs it idempotently. Completing a phase requires every declared batch for the current phase to be taught, with progress events corresponding one-to-one to current, non-superseded attempts through `inspected_pages + taught_item_ids`; inspecting pages does not mean that every item on them was taught. A superseded predecessor/event remains available for audit but is excluded from the current completion denominator. This route requires neither `.ingest/`, a wiki, nor a typed Guide. Initial lightweight setup records an immutable stat-only baseline only for a standard question bank that already exists at that moment; it does not parse or hash the bank. Only an explicit quiz/checkpoint opens the bank and binds the exact bank/item revision. `covered_unverified` is available; `verified` requires two distinct revision-bound handled checkpoints from that unchanged pre-existing bank and at least one pass. Routine mount/status checks metadata plus physical identity only. Exact stream hashes are calculated only during planning, answer-dependency registration, visual/teaching publication, phase completion, or explicit `status --verify-live`. Taught history outside the current phase checks only immutable receipts/events and is reported as `unchecked_historical` to make clear that live evidence has not yet been reverified.

`artifact_mode` is orthogonal to the processing route. In lightweight mode, a saved `visual` value is only a dormant preference and effective output is fixed to `chat`; the Study Guide route can open only after an explicit switch to full. The full orchestrator, workspace builder, and lower-level compiler all recheck the exact pair, runtime, learning choices, and `processing_mode=full`; a lower-level command cannot bypass these gates.

## 4. State, ingestion, and content layers

```text
<workspace>/.ingest/               # Source of truth for ingestion/takeover; never hand-edit
  source_manifest.json             # Raw-material revisions, hashes, and parse status
  parser_receipts.json             # Per-source ingestion-v2 parser/revision/config/location receipts
  base_content_units.jsonl         # Deterministic parse baseline
  content_units.jsonl              # Compiled view of baseline + applied patches
  chapter_phase_mappings.jsonl     # Explicit mappings between actual chapters and learning phases
  duplicate_candidates.jsonl       # Derived exact/near candidates
  canonical_groups.jsonl           # Display/retrieval folding facts that preserve every occurrence
  source_conflicts.jsonl           # Explicit conflicts; unresolved conflicts fail closed
  source_priorities.jsonl          # Revision-bound review evidence, not a silent winner
  claim_records.jsonl              # Exact-location claims for ingestion-v2 typed Guides
  claim_verification_receipts/     # Required v2 Guide location-only + guide/fact hash bindings
  review_queue.jsonl               # Typed AI/human takeover lifecycle
  review_patches.jsonl             # Append-only, replayable patch ledger
  build_manifest.json              # Page accounting and integrity hashes
study_state.json                 # Sole source of truth for structured progress
study_progress.md                # Human-readable view rendered from state
references/wiki/chNN_*.md        # Per-chapter knowledge sources
references/quiz_bank.json        # Sole quiz-question source
references/retrieval_index.json  # Freshness-bound lightweight retrieval derivative
references/teaching_examples.json
references/teaching_baseline.json
notebook/chNN.md                 # Persisted complete teaching and feedback
notebook/chNN.guide.json         # Verified profile=full typed completion manifest for the current chapter
mistakes/chNN.md                 # Mistake mirror
study_guide/chNN.html|pdf        # Derived reading artifact for the current chapter
study_guide/chNN.receipt.json    # Manifest/HTML/PDF hashes and QA status
study_guide/qa/chNN_pNNN.png     # Per-page acceptance evidence for the latest PDF
```

The normal material entry point is `scripts/ingest_course.py`: preflight → parse → structured compile → state initialization → visual index → validator. The core route supports PDF/DOCX/PPTX/XLSX/common raster/txt/Markdown. XLSX worksheets, standalone rasters, and DOCX logical segments retain their own location semantics and must not be represented as physical pages. Exit 10 means the process completed but content readiness is blocked; `scripts/ingest_review.py` must take over each issue. The presence of a wiki/question bank is not permission to begin teaching. A review patch binds the source hash, is written to the append-only ledger, and then recompiles the wiki, question bank, and retrieval index. For many independent issues, after each one receives visual inspection, a claim, an individual patch, and `validate-patch`, `apply-batch` may be used. Ledger identity and transaction boundaries remain per item; only derivative compilation is deferred until the end of the batch.

An ingestion-v2 parser receipt binds each source’s exact hash/media, adapter/version/config, produced location inventory, and `network/upload/install=false` policy. `source_id` derives from the canonical path, while `unit_id` derives from source/location/bbox/kind/ordinal. Neither is a content revision hash; the exact revision is separately bound by source/full-unit digests. Canonical groups/conflicts are rebuildable derived facts: all source occurrences are preserved; near matches are not automatically grouped; priority does not silently decide a winner; unresolved conflicts fail closed.

Docling/MinerU may be provided only by a configured remote/cloud host after the user explicitly names the capability. The student’s local runtime never probes, downloads, installs, imports, or executes those heavy packages and does not accept a callable local runner. A remote host must separately disclose upload and privacy boundaries. Without such an integration, the workflow continues with core + typed visual review. Production retrieval remains stdlib BM25. Dense + Sparse/RRF/reranker may become opt-in only after a sufficiently large frozen real multi-course recall Gold Set passes the gate; the current synthetic sample is explicitly insufficient. An ingestion-v2 typed Guide requires a claim sidecar plus chapter receipt. The validator recomputes canonical strict-JSON guide/fact hashes live and requires the authored subject/text, same-ref unit/role, and source location/revision of every explicit claim to match. Coverage includes direct material knowledge-point explanations, formulas, printed prompts, and material answers. v1 remains compatible. `location_only` never proves semantic entailment.

When state exists, it may be modified only through `scripts/update_progress.py`; when state is absent but Python works, run `init` first. `study_progress.md`, the wiki/question bank, retrieval index, HTML, and PDF are compiled/derived views for different purposes. They must never overwrite the `.ingest/` or `study_state.json` sources of truth in reverse.

## 5. Language layer

- Control rules: `skills/*/SKILL.md`, primarily English, precise, testable workflows; trigger metadata, canonical state values, and verbatim student-facing phrases are explicit exceptions.
- Student-facing wording: `locales/<lang>/skills/*.md`, message catalogs, and templates.
- Persisted canonical language values: `zh` / `en` / `bilingual`; `中文` / `English` / `双语` are display aliases and legacy-state migration inputs only. The root router normalizes before dispatch.
- Verbatim quotations from source materials may retain their original language with a label; agent-generated prose must follow the selected language.
- Machine contracts: JSON keys, stable IDs, hashes, reason codes, and lifecycle statuses remain fixed across translations.
- Human-readable views: agent-generated notebooks, receipts, and textbooks render in the selected language, while state enumerations remain canonical. If a legacy/generated progress view still uses Chinese canonical wording, the agent reads it only as a state view and then restates it in the current language; it must not conflate the two layers.

See [`language-policy.md`](language-policy.md) and [`localization.md`](localization.md).

## 6. Key invariants

- Knowledge provenance is transparent: 🟢 来自资料 / 🟡 AI补充，可能与你老师讲的不完全一致 / ⚠️ AI生成答案，非老师/教材提供.
- Quizzes draw only from `quiz_bank.json`; missing banks or invisible prompt assets fail closed.
- Learning mode and time budget alter cadence but never remove provenance, state, or asset gates.
- `preferences.interaction_style` stores only `batch|step_by_step`, defaulting to `batch` when missing. `step_by_step` changes only the number of full-teaching-manifest items taught per turn. `no_questions=true` or lightweight retains the stored preference, reports it dormant, and uses effective `batch`. Per-item completion must follow manifest-first order and write a `{id, notebook_ref, notebook_block_sha256, manifest_item_sha256}` binding. Quiz/teaching/notebook/Guide surfaces share the safe 1–200-character Unicode ID contract, and two bindings may not share one `notebook_ref`. An unbound ID is legal batch history. Once bound, it continues to receive notebook/manifest live validation even after switching back to batch. Only a missing notebook entry and anchor/marker/hash/revision drift may return to pending. Unsafe paths or file types, escapes, invalid UTF-8, unterminated fences, malformed blocks, schema/duplicate/unexpected evidence all fail closed. A structurally valid new item or repairable stale item in a completed full phase lowers mount status only to `usable_with_gaps`, but still invalidates the old Guide and completion evidence. Every `teaching_baseline.json` ID must retain a snapshot in the current `teaching_examples.json` for the same canonical chapter under exact policy `append_only`; a quiz-only item with the same ID cannot substitute. `teaching_example_roster_exhausted` is not chapter completion and cannot replace Guide, question-bank, typed-unit, asset, checkpoint, or phase gates.
- Run a deterministic algorithm before rendering a graph-structure question.
- Teaching, grading, confusion handling, and review write to the notebook before returning a chat summary.
- Before full/structured workspace phase completion, validate the current chapter’s `profile=full` typed manifest. `artifact_mode=chat` stops there and does not require HTML/PDF. A lightweight phase uses only the current-phase taught-batch + notebook/progress live-binding gates and never loads a typed Guide.
- `answer_explanation_mode` is orthogonal to processing/artifact mode; missing/legacy storage safely falls back to `ordinary`. Both modes require a detailed, beginner-readable answer explanation for each item. Ordinary mode generates it in normal annotations and forbids an isolation receipt. On entry to a full-v2 Guide, first perform the host-native child-agent capability handshake. Default to and persist `isolated` only when the host can prove a fresh independent context for every item and restrict both input and tools to the exact single-item request, unless the user opts out. Explain the additional host quota/time once; no second API Key or external-upload consent is required. If capability is missing, inherited, or unverifiable, remain in ordinary mode. An external Provider is a fallback only when explicitly named by the user and still requires no-upload exact planning followed by exact-plan upload consent after price/retention/privacy disclosure. A model family, subscription, API Key, `full`, or `visual` is not by itself evidence of capability or upload authorization.
- Enter the visual-artifact workflow only for `visual` or a one-shot request. A `visual` artifact may be delivered and the phase completed only when receipt hashes match, every page has been accepted, no unresolved defects remain, and `artifact_ready=ready`. “Requested generation” must not be reported as “successfully generated.”
- Changing `study_state.json.language` or `answer_explanation_mode` makes the old manifest/HTML/PDF/QA and mode-bound authoring chain stale. Ingestion-v2 must rerun notebook, compile, claims, and verify/import from annotations in the target language/mode; isolated mode must also recreate each per-item explanation receipt. An old explanation cannot be reused by relocalizing or renaming it. The sole historical exception is an existing, mode-less protocol-v2 canonical manifest with a complete and currently re-verifiable isolated contract. It may only be validated in place through the CLI with `--input` omitted; it cannot be imported, rendered, QA-accepted, used for phase completion, or accepted as library-level compatibility input. Any modification requires an explicit-mode rebuild. Ingestion-v1 preserves only its existing canonical manifest read-only; it cannot import/relocalize/render, and any modification first requires migration/rebuild to v2. Rendering and full-page acceptance then run again as needed.
- Every ingestion warning, skipped item, human-review item, and missing-answer item must be taken over individually.
- A structured workspace with `readiness=blocked` cannot enter teaching, quizzes, or phase completion.
- LangGraph may orchestrate the same command set through a remote/cloud host only after the user explicitly names it. Local `build_exam_graph()` explicitly refuses to run. A remote checkpoint/resume flag remains only a routing hint, never a source of truth. Every gate revalidates from `study_state.json`, `.ingest/`, and runtime/guide/QA receipts. See [`langgraph-host-adapter.md`](langgraph-host-adapter.md).

## 7. Validation layer

Repository tests cover:

- skill frontmatter and directory inventory;
- alignment of zh/en wording, message keys, and template structure;
- language purity and canonical labels;
- state initialization and write boundaries;
- question-bank, scope, visual-asset, and provenance contracts;
- workspace schema/validator behavior;
- distribution inclusion of the router, control layer, language packs, and scripts;
- Markdown relative links and hard-coded template examples.

New features should update the existing source of truth and its corresponding semantic tests. They should not create another release-era manual or duplicate entire rule blocks.
