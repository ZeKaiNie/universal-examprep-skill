# Agent portability

Behavior lives in `skills/`; `AGENTS.md` is the compact fallback. Install the whole
runtime, including `scripts/`, `locales/`, `docs/`, and `prompts/`. Root `SKILL.md` is
language-neutral; skill-aware hosts may enter at `skills/exam-cram/SKILL.md`.
Concept-confusion capture lives at [`skills/confusion-tracker/SKILL.md`](../skills/confusion-tracker/SKILL.md).
The English compatibility entry is [`locales/en/SKILL.md`](../locales/en/SKILL.md).

## Installation and host entry points

An Agent with terminal and network access can install from GitHub after the user grants
the required command, network, and out-of-workspace write permissions. A ZIP download
is only a fallback when the host cannot clone a repository.

| Host | Supported install/entry route |
| --- | --- |
| [Codex](https://learn.chatgpt.com/docs/agent-configuration/skills.md) | Clone into `$CODEX_HOME/skills/universal-exam-cram-coach`, then reload skills; enter through `SKILL.md`, `skills/*`, or `AGENTS.md`. |
| [Claude Code](https://code.claude.com/docs/en/slash-commands) | Clone into `~/.claude/skills/universal-exam-cram-coach` or `.claude/skills/universal-exam-cram-coach`; enter through `SKILL.md` or `skills/*`. |
| [Cursor](https://cursor.com/docs/skills) | Clone into `~/.cursor/skills/universal-exam-cram-coach`, `~/.agents/skills/universal-exam-cram-coach`, or the corresponding project directory. |
| [Windsurf](https://docs.windsurf.com/zh/windsurf/cascade/skills) | Clone into `~/.codeium/windsurf/skills/universal-exam-cram-coach`, `.windsurf/skills/universal-exam-cram-coach`, or `.agents/skills/universal-exam-cram-coach`. |
| [Gemini CLI](https://geminicli.com/docs/cli/skills/) | Run `gemini skills install https://github.com/ZeKaiNie/universal-examprep-skill.git`, then reload skills. |
| [Antigravity](https://antigravity.google/docs/skills) | Clone into `~/.gemini/config/skills/universal-exam-cram-coach` or `.agents/skills/universal-exam-cram-coach`. |
| ChatGPT / Claude Web | Use [`prompts/web_prompt.md`](../prompts/web_prompt.md) or [`prompts/web_prompt.en.md`](../prompts/web_prompt.en.md); do not claim local writes that the web host cannot perform. |

Copyable generic request for a network-capable Agent:

```text
Install the latest Agent Skill from https://github.com/ZeKaiNie/universal-examprep-skill into your officially supported user-level or project-level skills directory. Ask before running terminal commands, using the network, or writing outside the workspace. Load the skill after installation and report the installed path and version; do not merely download it.
```

When the course contains PDFs, formulas, or question images, prefer the host's desktop
app or IDE UI. A terminal remains useful for installation and diagnostics, but its
ordinary chat view may not render local images or clickable links consistently. This is
a recommendation, not a claim that every desktop host supports every rich-media format.

## Native per-item child-agent capability

The preferred isolated explanation route uses the current host's own child Agent and
does not require a separate API key. Enable it by default only when the active host can
start a fresh or independent child context **and** restrict that child's effective input
and tools to one exact question packet. Otherwise use ordinary authoring; never label an
ordinary turn as isolated and never switch to an external Provider automatically.

| Host | Documented boundary | Default for per-item explanation |
| --- | --- | --- |
| [Codex](https://learn.chatgpt.com/docs/agent-configuration/subagents.md) | Native subagents; use a no-history child and a restricted/read-only agent profile for the exact packet. | Native isolated when those controls are active. |
| [Claude Code](https://code.claude.com/docs/en/sub-agents) | A non-fork custom subagent has an independent context and can have an explicit tool allowlist. Its system prompt still exists, and general-purpose agents may load project instructions. | Native isolated only with a dedicated minimal subagent. |
| [Cursor](https://cursor.com/docs/subagents) | Subagents have a clean context but inherit the parent Agent's tools. | Ordinary when the project requires a hard tool-disabled boundary. |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli/blob/main/docs/core/subagents.md) | Subagents use independent context loops and can have restricted tool lists. | Native isolated when the restricted child is available. |
| [Antigravity](https://antigravity.google/docs/cli-features) | Independent subagent sessions are available and the host can control child tools. | Native isolated when those controls are active. |
| [Windsurf](https://docs.windsurf.com/zh/windsurf/cascade/skills) | No official general-purpose clean-context child-agent contract was confirmed for this use. | Ordinary. |
| Other or web-only hosts | Capability is unknown or unavailable. | Ordinary. |

Each native child receives only the fixed beginner-first instruction, target language,
the exact original question, the official answer when one exists, and target-scoped
question/answer assets. It receives no other questions, course wiki, notebook, main-chat
history, filesystem, network, or tools. The host imports only the structured explanation
and coverage result. This boundary reduces accidental context leakage; it is a host
declaration, not a cryptographic sandbox attestation. Native calls still consume the
host account's model quota, time, and tokens.

## File-based hosts

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

Teaching cadence is optional rather than a fourth startup choice. Omit it to preserve an existing value (new/missing state is `batch`), or pass `--interaction-style step_by_step` to `confirm` / run `update_progress.py --workspace <ws> set --interaction-style step_by_step`. The saved preference is effective only on the explicit full route with `no_questions=false`; otherwise it is dormant and effective cadence is `batch`. Missing, legacy, or invalid `processing_mode` fails closed to lightweight, so it never activates step-by-step full-route behavior by implication. A step host selects from one locked manifest/state/baseline/notebook snapshot, writes the first-pending walkthrough with `notebook.py add-entry --teaching-example`, then uses `update_progress.py record-taught-example` to bind the ID, anchor, notebook-block hash, and manifest-item hash. Quiz/teaching/notebook/Guide IDs share the safe-Unicode ≤200 contract. Unbound IDs remain batch history. Missing entries and anchor/marker/hash/revision drift become pending for ordered repair; unsafe filesystem topology, invalid UTF-8/fences/blocks, schema/duplicate/shared-ref/out-of-roster evidence remain fatal. A completed full phase with only recoverable/new-roster pending items mounts `usable_with_gaps`, but Guide and completion stay strict. Every retained baseline ID needs a current teaching snapshot in the same canonical chapter under exact `policy=append_only`; quiz-only is not a substitute.

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

## Teaching artifacts and host extensions

Missing/unknown `artifact_mode` is `chat`. `artifact_mode` remains an independent durable preference: if it is `visual` while `processing_mode=lightweight`, status/readiness report `artifact_mode_preference=visual`, `artifact_mode_effective=chat`, and `artifact_mode_dormant=true`. The preference is retained for a later explicit switch to `full`; lightweight never enters Study Guide authoring/rendering. In full mode, explicit standing `visual` or a one-shot request may enter the linked [`PDF capability routes`](pdf-capability-adapters.md); no mode permits silent installation. Structured completion requires the current full-mode typed guide. Visual delivery additionally requires matching hashes, every-page QA, no unresolved defect, and `artifact_ready=ready`; language changes stale prior artifacts.

`answer_explanation_mode` is another independent host boundary. The normal authoring
context always provides a detailed beginner-first explanation. The effective value may
default to `isolated` only when the host passes the native child-agent gate above; each
item then gets a fresh restricted child and the result enters the canonical receipt
chain. No extra API key or Provider upload consent is needed for that native route.
Hosts that fail the gate remain `ordinary` and must not fabricate an isolation receipt.

The separate OpenAI API implementation is an explicit-request fallback, not the default
isolated route. It keeps credentials outside the student workspace and retains its
two-stage consent boundary: first a no-upload plan after Provider/billing/privacy
disclosure, then consent for the exact item/image scope, call count, plan ID, and bounded
price estimate before upload. A host subscription is not OpenAI API billing, and an API
key is not upload consent. See
[`openai-study-guide-adapter.md`](openai-study-guide-adapter.md).

Ingestion-v2 Guide claims bind exact same-unit refs and current guide/source/content/fact/parser-receipt hashes; the receipt proves authored-text membership plus location/revision, not semantic support. Legacy/v1 does not claim this gate.

An explicitly requested remote/cloud LangGraph host may implement the optional [`LangGraph contract`](langgraph-host-adapter.md); the local module retains only dependency-free receipt/routing helpers and a `build_exam_graph()` rejection, not an unreachable local graph body. Checkpoints/interrupts contain bounded routing contracts, never course truth; resume rehydrates current state and receipts. Web hosts cannot claim local commands, `.ingest/`, or writes. Host-specific rule copies must be generated from or tested against `AGENTS.md`.
