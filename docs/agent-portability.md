# Agent Portability

Behavior lives in `skills/`; `AGENTS.md` is the compact fallback. Prefer references over copies; any host copy must stay aligned. Install the whole runtime, including `scripts/`, `locales/`, `docs/`, and `prompts/`; confusion capture is [`skills/confusion-tracker/SKILL.md`](../skills/confusion-tracker/SKILL.md).

| Host | Entry/boundary |
| --- | --- |
| Claude Code | root `SKILL.md` or `skills/*` |
| Codex | `AGENTS.md` or `skills/*` |
| Cursor / Windsurf / generic | aligned `AGENTS.md` fallback |
| ChatGPT / Claude Web | `prompts/web_prompt.md`; English: [`prompts/web_prompt.en.md`](../prompts/web_prompt.en.md); mounted data only, no local-write claims |

Root `SKILL.md` is language-neutral; locale compatibility indices include [`locales/en/SKILL.md`](../locales/en/SKILL.md). Skill-aware hosts may enter at `skills/exam-cram/SKILL.md`.

## 文件型 host

Only after the student confirms exact, separate materials/workspace paths and the three learning choices:

```bash
python scripts/exam_start.py status --materials <dir> --workspace <ws> --json
python scripts/exam_start.py confirm --course <name> --materials <dir> --workspace <ws> --mode <mode> --time-budget <tier> --language <zh|en|bilingual> --processing-mode lightweight --json
python scripts/lightweight_session.py init --materials <dir> --workspace <ws> --json
python scripts/lightweight_session.py plan --materials <dir> --workspace <ws> --chapter <current-phase> --source <relative.pdf|png|jpg|jpeg|bmp> --pages <range> --json
# Only when an official answer is in another source/page:
python scripts/lightweight_session.py register-answer-dependency --materials <dir> --workspace <ws> --batch-id <id> --source <relative.pdf|png|jpg|jpeg|bmp> --pages <range> --json
# To replace/narrow or remove that planned dependency without erasing audit history:
python scripts/lightweight_session.py set-answer-dependency --materials <dir> --workspace <ws> --batch-id <id> --source <relative.pdf|png|jpg|jpeg|bmp> --pages <exact-range> --reason <concrete-reason> --json
python scripts/lightweight_session.py remove-answer-dependency --materials <dir> --workspace <ws> --batch-id <id> --source <relative.pdf|png|jpg|jpeg|bmp> --reason <same-reason-on-retry> --json
# Host renders/imports the exact visual manifest, teaches and persists notebook/chNN.md#anchor:
python scripts/lightweight_session.py record-visual --materials <dir> --workspace <ws> --batch-id <id> --manifest <json> --json
# If the unfinished scope must be closed before teaching:
python scripts/lightweight_session.py abandon --materials <dir> --workspace <ws> --batch-id <id> --reason <concrete-reason> --json
python scripts/lightweight_session.py mark-taught --materials <dir> --workspace <ws> --batch-id <id> --notebook-entry notebook/chNN.md#anchor --taught-item-ids <id1,id2,...> --json
# If already-taught evidence must be redone without erasing history:
python scripts/lightweight_session.py replace-taught --materials <dir> --workspace <ws> --batch-id <id> --reason <concrete-reason> --json
# Routine status is metadata/identity-only; request stream hashes explicitly when needed:
python scripts/lightweight_session.py status --materials <dir> --workspace <ws> --verify-live --json
# Only after an explicit full-build choice:
python scripts/exam_start.py confirm --course <name> --materials <dir> --workspace <ws> --mode <mode> --time-budget <tier> --language <zh|en|bilingual> --processing-mode full --json
python scripts/ingest_course.py --materials <dir> --workspace <ws> --json
```

Successful lightweight `init` creates and safety-checks the workspace-local
`.lightweight/assets/` directory. A host may write the requested page/contact/crop PNGs
there immediately; it must not rely on an undocumented manual directory-creation step.

`confirm` atomically writes pair confirmation, state, and runtime receipt; later gates revalidate them. Omitting `--processing-mode` preserves an existing choice, while a newly initialized workspace safely defaults to `lightweight`. The host wrapper uses the same `None`/preserve contract. Only explicit `full` opens ingestion. Both the orchestrator and lower-level workspace builder/compiler publications recheck the exact registered pair, current runtime receipt, learning choices, and `processing_mode=full`; invoking `build_raw_input_from_workspace.py` or `ingest.py` directly cannot bypass that gate. Standalone builder output that is not a workspace publication remains a compatibility utility. Core covers PDF/DOCX/PPTX/XLSX/raster/txt/Markdown with honest PDF-page, PPTX-slide, XLSX-worksheet, DOCX-logical-segment, and raster page-equivalent anchors.

Teaching cadence is a third independent state control alongside processing and answer-explanation mode, but it is optional rather than a fourth startup choice. `preferences.interaction_style` stores only `batch|step_by_step`; omit `--interaction-style` to preserve it, and treat new or missing legacy state as `batch`. A stored step preference is effective only with `processing_mode=full` and `no_questions=false`; otherwise effective cadence is `batch` and that preference is retained but dormant. Effective one-question pacing calls `list_teaching_examples.py --next-pending`, whose manifest/state/notebook/baseline read occurs inside one workspace lock and returns the first pending manifest item. The host writes the complete seven-step walkthrough with `notebook.py add-entry --teaching-example`, then uses `update_progress.py record-taught-example` instead of two loose evidence writes. That command records `{id, notebook_ref, notebook_block_sha256, manifest_item_sha256}` alongside the ordinary ID/anchor evidence. Unbound teaching IDs remain valid batch history; bound IDs stay live-validated across cadence changes. Guide notebook publication preserves a valid bound marked block and rejects a stale binding or an unbound marker. Every teaching-baseline ID must have a current teaching-manifest snapshot; a quiz-only item is not a substitute. Notebook presence and Continue/understanding messages never create completion evidence; lightweight keeps its separate page-batch state machine. The selector lock gives a consistent snapshot, not a reservation, so concurrent hosts can still receive the same pending item.

Lightweight `plan` accepts only the current phase, PDF pages or one definitely
single-frame PNG/JPEG/BMP, at most eight primary pages, and at most one active batch.
A single-page work order has `contact_sheet_groups=[]` and uses the page asset
directly. For a multi-page batch the host creates overview-only contact sheets that
partition the primary pages exactly once in groups of at most four, at roughly 768 px
per tile. New visual receipts use schema 3. Every primary page enumerates stable
`teaching_item_ids`; every item independently declares `kind=text|figure|mixed`, one
or more prompt components, and zero or more answer components. This works for
text-only prompts whose official answer is in another file without falsely marking
the item as a figure. Components declare a role, sorted `required_context_ids`, exact
`allowed_detected_item_ids`, and a source-qualified crop binding. A component may be
target-plus-context or non-empty context-only, but at least one prompt component for
the item must contain the target. Detail calls may combine multiple prompt components
only for one target, and solution calls may combine answer components only for one
target. Every component receives its own one-crop `crop_review` model invocation; its
detected IDs must exactly equal the declared allowed IDs and it must report no
unrelated content or student attempt. A bbox or filename alone is not semantic-purity
evidence.

Every primary/dependency page declares `content_types` plus
`answer_provenance=student_attempt|official_solution|none|unknown`. When the official
answer is in another source, additive `register-answer-dependency` binds only the
exact extra pages while the batch is still planned. `set-answer-dependency` replaces
or narrows one source's exact page set; `remove-answer-dependency` removes it. Both
write hash-bound history, and exact retries do not append duplicate events (a removal
retry must repeat its recorded reason). Those rendered pages
are locator/detail context only and can never enter a solution call themselves. Only
a page classified `official_solution` may supply a declared-scope answer component crop; a
student-attempt or unknown page remains inspectable context but can never satisfy
official/material answer evidence. Every registered page declared
`official_solution` must contribute at least one answer component; multiple pages and
components are supported. Every page/contact/prompt/answer/dependency image
is canonical PNG under `.lightweight/assets/`, with matching PNG signature and
measured dimensions; lightweight evidence cannot reuse a full-build asset path, and
prompt/answer crops are distinct from pages, contacts, and each other. `mark-taught`
requires a unique durable `notebook/chNN.md#anchor` plus the exact sorted
`taught_item_ids` enumerated by the visual receipt. It separately records
`inspected_pages`, revalidates exact live source and visual bytes, and publishes
`phase_evidence.lightweight_batches` under the workspace lock. If the taught receipt
commits before the progress file, rerunning the command repairs the event
idempotently. `replace-taught` revalidates dependency revisions while preserving the
exact dependency pages in the successor. Schema-2 visual receipts remain immutable
history. A legacy schema-2 `visual_ready` attempt is quarantined from record/teach but
can be auditably abandoned; it never upgrades silently, and a new attempt must produce
schema 3.

Routine `status` uses a generation-stable read-only snapshot and neither creates nor
opens a lock for writing. Workspace validation is likewise bounded to metadata and
physical-identity checks; neither path stream-hashes current or active sources/assets.
Its `full_page_answer_taint_status` preserves the conservative provenance of uncropped
locator/detail pages. The separate `answer_taint_status`, `item_crop_review_status`, and
`teaching_publication_status` describe the reviewed item crops and durable teaching
publication, so a clean officially answered item is not reported as blocked merely
because its parent page also contains a student attempt.
Exact stream hashes
are recomputed only by `plan`, `register-answer-dependency`, `record-visual`,
`mark-taught`, phase completion, or explicit `status --verify-live`. A non-current
taught batch is checked structurally against its immutable receipt/event and counted
as `unchecked_historical`; returning to that phase brings it back into the current
live scope.

If a `planned|visual_ready` batch must be closed before teaching, `abandon` requires
a concrete reason and preserves a digest-bound prior-status receipt. It frees the
single active slot, and a later plan of the same slice receives a new attempt ID.
An abandoned record is never deleted or counted as covered; a `taught` batch cannot
be abandoned. `replace-taught --reason` is the only taught-redo path: it retains the
old attempt and its exact progress event as immutable `superseded` history, excludes
that predecessor from the current completion denominator, and creates a planned
successor for the same primary source/chapter/pages slice.

Lightweight initialization also captures an immutable, stat-only baseline for any
pre-existing `references/quiz_bank.json`; startup does not parse or hash the bank.
Only explicit selection/checkpoint work opens it and creates a revision binding over
the bank and eligible item. Lightweight completion skips typed Guide/full evidence
and may reach `covered_unverified`; `verified` needs two distinct handled checkpoint
rows from that unchanged pre-existing baseline, at least one pass, and exact
`bank_binding_id`/`bank_sha256`/`item_sha256` on every qualifying row. A bank absent
at initialization, replaced/drifted later, or represented only by legacy unbound
rows cannot support `verified`.

If ingestion is interrupted after `.ingest/material_build_pending.json` is published and the runtime receipt is then missing or drifted, do not rerun ordinary `confirm` and do not delete the blocker. Choose explicitly:

```bash
python scripts/exam_start.py recover-material-build --materials <dir> --workspace <ws> --action resume --json
# If the builder now produces different bytes and resume refuses with zero publication:
python scripts/exam_start.py recover-material-build --materials <dir> --workspace <ws> --action supersede --json
python scripts/ingest_course.py --materials <dir> --workspace <ws> --json
```

`resume` is exact-generation-only; complete bound sources bypass reparsing, while incomplete blocker-first state may be rebuilt only if it reproduces the same generation. `supersede` creates an audited schema-2 successor and closes every predecessor to its direct child. Recovery logs are bounded (64 events per generation, 64 ancestor edges, and at most 65 receipt rows including one current completion) and are transactionally bound by the receipt and manifest.

Ingestion-v2 requires one local core parser receipt per source, binding revision/config/location accounting and `network/upload/install=false`; its exact schema is in [`file-format.md`](file-format.md). Docling/MinerU are outside that local path: they may be offered only after an explicit named request through a separately configured remote/cloud host that discloses upload/privacy terms. The student runtime never probes, downloads, installs, imports, executes, or accepts a local callable runner for either heavy parser.

Exit `0` means `ready` or disclosed `usable_with_gaps`; `10` means completed process but blocked content, so teaching/quiz/completion remain forbidden; other nonzero means operation failure, never “no Python.” Typed takeover uses only `ingest_review.py list/show/claim/validate-patch/apply/apply-batch/mark-unrecoverable/rebuild`; batch apply keeps one validated patch per issue. Never hand-edit ledgers, facts, wiki, or bank. Rebuild and validate after source/patch changes.

`validate_workspace.py --json` also fails closed at the CLI boundary when the registered
workspace/runtime/full-processing gate is stale or blocked: it returns structured
`readiness=blocked`, fatal errors, blocked capability reason
`full_processing_gate_blocked`, and exit `2` instead of leaking a Python traceback.

## 教材与宿主扩展

Missing/unknown `artifact_mode` is `chat`. `artifact_mode` remains an independent durable preference: if it is `visual` while `processing_mode=lightweight`, status/readiness report `artifact_mode_preference=visual`, `artifact_mode_effective=chat`, and `artifact_mode_dormant=true`. The preference is retained for a later explicit switch to `full`; lightweight never enters Study Guide authoring/rendering. In full mode, explicit standing `visual` or a one-shot request may enter the linked [`PDF capability routes`](pdf-capability-adapters.md); no mode permits silent installation. Structured completion requires the current full-mode typed guide. Visual delivery additionally requires matching hashes, every-page QA, no unresolved defect, and `artifact_ready=ready`; language changes stale prior artifacts.

`answer_explanation_mode` is another independent host boundary. Its safe effective value is
`ordinary`: the normal authoring context still writes and validates a detailed beginner-first
answer explanation for every Guide item, but it makes no second Provider call and creates no
isolation receipt. `isolated` is a full-ingestion-v2 extension, not a GPT-family guarantee. A
host must be able to create truly fresh/stateless tool-disabled calls, keep credentials outside
the student workspace/logs/Git, bind exact item-scoped inputs, and import the structured result
through the canonical receipt chain. Consent is two-stage: Provider/API-billing and current
retention/privacy disclosure precedes a no-upload planning opt-in; only after that plan exposes
the exact item/image scope and call count, the Agent checks current official pricing and gives a
bounded estimate, and the user accepts the exact plan; only then may calls begin. A ChatGPT/Codex
subscription is not OpenAI API billing, and an API key's presence is not upload consent. A
file-less web host or any Agent that cannot truthfully meet this contract must stay on
`ordinary`; it must not fabricate a receipt or describe an ordinary model turn as isolated. The
optional OpenAI implementation and its limitations are documented in
[`openai-study-guide-adapter.md`](openai-study-guide-adapter.md).

Ingestion-v2 Guide claims bind exact same-unit refs and current guide/source/content/fact/parser-receipt hashes; the receipt proves authored-text membership plus location/revision, not semantic support. Legacy/v1 does not claim this gate.

An explicitly requested remote/cloud LangGraph host may implement the optional [`LangGraph contract`](langgraph-host-adapter.md); the local module retains only dependency-free receipt/routing helpers and a `build_exam_graph()` rejection, not an unreachable local graph body. Checkpoints/interrupts contain bounded routing contracts, never course truth; resume rehydrates current state and receipts. Web hosts cannot claim local commands, `.ingest/`, or writes. Host-specific rule copies must be generated from or tested against `AGENTS.md`.
