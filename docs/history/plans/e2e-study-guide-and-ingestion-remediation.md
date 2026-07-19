# E2E Study Guide and Ingestion Remediation Plan

Status: the combined lightweight/full defect batch is statically frozen; fresh EEC 160 lightweight functional/visual re-acceptance is complete and full-path acceptance is now active, while regression changes and test execution remain intentionally deferred

Owner: Codex

Baseline: `origin/main` at `9935a64` after PRs #24–#40

Primary audit: `D:\EEC 160\universal-examprep-e2e-audit-2026-07-14.md`

Real-course full acceptance workspace: `D:\EEC 160-exam-workspace-acceptance`

Real-course lightweight acceptance workspace (first pass): `D:\EEC 160-lightweight-acceptance`

Real-course lightweight acceptance workspace (second pass): `D:\EEC 160-lightweight-acceptance-v2`

Planned fresh lightweight re-acceptance workspace: `D:\EEC 160-lightweight-acceptance-v3`

Real-course source materials: `D:\EEC 160`

## Current execution order (maintainer correction, 2026-07-17)

This final development cycle is deliberately phase-separated.  Do not interleave
regression work with interfaces that are still changing:

1. **Feature closure first.** Finish the lightweight/full mode contracts, crop and
   per-item explanation pipeline, evidence/state transitions, and static
   documentation alignment. During this phase use read-only/static inspection and
   `git diff --check` only—do not run tests, compilation, or EEC acceptance.
2. **Real functional/visual acceptance second.** Freeze the feature surface, then
   exercise representative EEC 160 data in lightweight, full, and bilingual paths.
   Judge teaching usefulness, figure/answer isolation, completeness, latency, and
   every rendered Study Guide page before changing regression fixtures.
3. **Concentrated regression phase last.** Update stale tests once to the accepted
   contracts, run the complete suite once, and repair the resulting defect batch
   together. Re-run only the smallest relevant checks while fixing that batch, then
   perform one final full gate before PR/release.

This order supersedes any older checklist wording that implies test-as-you-build for
the current branch. Historical test evidence below remains release history, not a
command to repeat it during feature development.

The first real-data pass is allowed to reopen feature work only as one frozen defect
batch. Finish that entire batch with static review, rerun the affected real-data
acceptance from fresh/current evidence, freeze remaining defects together, and only
then edit or execute regression tests. Do not alternate one defect, one test, one
feature.

### First representative acceptance snapshot (2026-07-17)

- Lightweight EEC 160 exercised `init → plan → external answer dependency → visual
  receipt → bilingual notebook → taught → covered_unverified → replace-taught → new
  evidence`. It proved the lazy route and source/hash/supersession gates, while exposing
  missing answer provenance, semantic crop purity, taught-item scope, dependency
  inheritance, bilingual notebook, read-only status, and completion-reopen contracts.
- Full EEC 160 remained ingestion-v2 structurally valid: 27 PDF sources, 1,077 pages,
  3,210 units, 27 parser receipts, no source conflicts. Chapter 1 validation returned
  `usable_with_gaps`; the artifact gate correctly blocked the old Guide.
- `study_guide_author.py prepare --chapter 1` reduced the immediate visual blockers to
  19 item assets: five page-shaped lecture prompts plus fourteen legacy homework
  crops. The accepted fix is an incremental source/pixel/semantic-bound strict crop
  backfill, not re-parsing all 1,077 PDF pages.
- The existing canonical bilingual annotations contain 11 knowledge points, 326
  formula groups, and 46 walkthroughs. A crop-only packet change must use a narrow
  fail-closed annotation rebase that updates the packet binding and removes paired
  deprecated self-check fields, rather than spending model tokens re-authoring all
  educational prose.
- No tests, compilation, `py_compile`, distribution build, or second EEC run may occur
  until this acceptance-defect batch is statically frozen.

### Second representative acceptance snapshot — lightweight path (2026-07-18)

- A fresh workspace confirmed `from_scratch + le1d + bilingual + lightweight + chat`,
  inventoried 28 files without parsing their content, and planned only one requested PDF
  page. No MinerU, Docling, LangGraph, OCR, full ingestion, or Study Guide render ran.
- The state machine correctly preserved two evidence-backed fail-closed abandonments
  instead of accepting dishonest visual receipts. The first target, Problem 1.1.2,
  requires a Venn diagram and prompt regions separated by unrelated Quiz 1.1 items;
  one rectangular `prompt_binding` cannot preserve the required context and prove
  `target_item_only` simultaneously.
- The second target, Problem 1.2.1(a), has a clean target-only prompt and official-answer
  crop, but item-scoped prompt/answer bindings exist only under `figure_questions`.
  A text-only item therefore cannot bind a cross-file official answer crop without being
  falsely classified as figure-based.
- Registered answer dependencies are monotonic unions. A planned batch cannot narrow or
  remove an accidentally over-registered solution page; the only honest recovery is to
  abandon and create an `-r2` batch. Add an auditable planned-state replace/remove command.
- The single-page work order reports `contact_sheet_groups=[[page]]` although the emitted
  manifest and validator correctly forbid contact sheets for a one-page batch. Remove this
  contradictory token-wasting instruction.
- These findings are acceptance defects, not test failures. Do not patch them individually;
  complete the full-path acceptance, freeze one combined defect set, then repair it once.

### Second representative acceptance snapshot — full path (2026-07-18)

- The frozen runtime reconfirmed the existing full/bilingual/visual workspace. Current
  validation remains structurally `ready`, teaching/quiz `usable_with_gaps`, and artifact
  `blocked`, with the same 27 sources, 1,077 pages, 3,210 units, 27 parser receipts,
  79 non-blocking Chapter 1 review issues, and zero source conflicts.
- Current `study_guide_author.py prepare` reproducibly reports the same 19 prompt-asset
  blockers: five whitespace-heavy lecture/example pages and fourteen legacy homework
  crops. A 19-cell visual inventory confirmed that most are semantically item-specific,
  while Problem 1.1.2 is not: the required Quiz 1.1 Venn context and target subquestions
  are separated by unrelated Quiz 1.1 exercises.
- Existing strict backfill supports only one rectangular region and requires
  `detected_item_ids=[target_item_id]`. That cannot represent either disjoint required
  context or questions that legitimately embed a referenced theorem/example. The full
  path therefore needs a revision-bound multi-region/composite crop and an explicit
  `required_context_ids` allowlist; it must still reject every unrelated item and all
  student-attempt pixels.
- The blocked packet correctly prevented `rebase-annotations`; the existing 947 KB
  authored annotations were not modified. This confirms the migration gate is fail-closed.
- Full rendering and all-page QA cannot begin honestly until the shared composite/context
  crop limitation is repaired. The combined lightweight/full defect set is now frozen;
  implement it as one batch, rerun both real-data paths, and only then enter regression work.

## 1. Goal and non-negotiable behavior

Deliver two independently reviewable and independently mergeable pull requests.

1. The first PR repairs the end-to-end teaching and Study Guide path. A document may be called a Study Guide only after its current chapter has source-backed concepts, complete example coverage, persisted walkthroughs, language-complete explanations, capability-specific readiness, and a consistent PDF/QA receipt.
2. The second PR expands the lightweight ingestion/retrieval core with redistribution-safe layout/OCR fixtures, an optional remote/cloud parser boundary, dedicated XLSX/image/scan routes, duplicate/conflict facts, exact-location claim receipts bound to the current guide revision (not semantic-entailment proof), and an evidence gate for any future optional hybrid retriever.

The following output rules are explicit acceptance requirements:

- Organize a full guide as `knowledge point -> formula/meaning -> every mapped example -> worked solution -> formula substitution -> detailed beginner-first answer explanation -> source trace`; do not restore the deprecated generic self-check panel.
- Include examples from lecture/handout, homework, quiz, and mock/past exam whenever the chapter inventory contains them. A chapter is not complete while a mapped knowledge point or example remains unhandled.
- When a prompt-side original-page image already contains the full original question, do not repeat the same original-language prompt as body text. In bilingual mode, the text area may contain only the faithful translation into the other language, clearly labeled as a translation; the original image remains the source quotation.
- Never publish broken extraction as mathematical prose. Recover standard math into `$...$`/`$$...$$` and MathML, or show a source-backed image/crop and explicitly mark the unrecovered text.
- An explicit request for complete coverage overrides a time-budget abbreviation. The explicit `abridged` profile may prioritize or omit items only with a deterministic omission ledger and may not mark the chapter complete; there is no implicit `auto` profile that guesses what to drop.
- Preserve the existing source-hash, path-safety, atomic-write, answer-side asset ordering, and structured review guarantees.

## 2. Delivery topology

### PR 1 — Study Guide E2E remediation

Branch: `codex/study-guide-e2e-remediation`

Base: `origin/main`

Planned merge order: first

### PR 2 — lightweight high-fidelity ingestion and verified retrieval

Branch: `codex/high-fidelity-ingestion-verification`

Base: updated `origin/main` after PR 1 merges

Planned merge order: second

The maintainer authorized merge without a separate approval round and later explicitly authorized
a new release.  Release metadata/tagging remains last: it may happen only after the merged runtime
passes the real EEC 160 rebuild and all-page visual acceptance.

### Post-audit amendment — real EEC acceptance hotfixes

The first clean EEC forward run exposed source-specific parser contracts that the synthetic
fixtures did not exercise.  They are tracked as separate, independently tested changes instead
of being hidden inside workspace-local patches:

- PR #26: visual/source-page ingestion hotfix.
- PR #27: replay-safe batch review and lettered `Quiz/Example N.N(A/B)` pairing.
- Final acceptance PR (this branch): roster-driven prompt-only homework crops, strict typed-review
  metadata/control-byte repairs, fail-closed supplied/local formula-quality merging, and the
  receipt/state transition contract later retained only for explicitly requested remote/cloud
  LangGraph hosts (local graph construction is now disabled).

### Source-integrity amendment — chapter scope and student-attempt isolation

The first real Chapter 1 authoring pass exposed a separate evidence-boundary defect: submitted
homework pages could contain both the original prompt and a student's handwritten/OCR answer, while
the course also supplied a distinct official solution.  Treating every crop from the submission as
ordinary prompt/answer evidence could leak student work into the Guide, claims, retrieval, repair
writers, or Cheatsheet.  The current hardening batch therefore adds and tests all of the following
before the final rebuild:

- a typed `student_attempt` asset role with workspace-wide physical-path taint across quiz,
  teaching-example, and content-unit layers;
- strict chapter-scoped authoring while retaining whole-workspace structural/path/identity checks;
- canonical portable-path rules, including Win32 aliases, device names, control characters, and
  reparse-point rejection;
- live-policy-bound public render/repair/claim helpers so omitted, `None`, empty, or caller-forged
  policy arguments cannot weaken the workspace policy;
- preflight-before-write and staged-publication guarantees for visual repair, raw-input assets,
  Study Guides, and Cheatsheets; and
- teaching-only handling for non-gradable worked demonstrations, without inventing assessment
  answers or dropping them from a full Guide.

## 3. PR 1 work breakdown

### 3.1 Executable session and artifact gates

- [x] Add one machine-readable start/preflight command that distinguishes workspace confirmation, learning choices, ingestion permission, teaching readiness, quiz readiness, and artifact readiness.
- [x] Make first-write commands fail closed when required confirmation/state facts are absent, while preserving the documented urgent-open exception.
- [x] Record the installed source revision and dirty state in a read-only receipt so a locally patched installation cannot be described as pristine upstream.
- [x] Add bounded summaries, detail files/cursors, and resume receipts for large review/validation output.

### 3.2 Fact-layer correctness

- [x] Fix split question/solution source-page ownership once, at the metadata construction boundary.
- [x] Keep ungradable worked examples teaching-only; do not create missing-answer blockers or selectable quiz records for them.
- [x] Add explicit `gradable` semantics and selector/validator safeguards for legacy overlap.
- [x] Require per-item evidence details for genuine unrecoverable missing answers; do not accept a generic batch reason as semantic proof.

### 3.3 Capability-specific readiness

- [x] Replace the single ambiguous readiness result with a backward-compatible capability matrix: structural, teaching, quiz, and artifact.
- [x] Aggregate warnings by reason/status/current chapter and keep normal JSON output bounded; write exhaustive details to an explicit file or cursor stream.
- [x] Prevent success wording when the requested capability is false.

### 3.4 Tutor-to-guide content contract

- [x] Define a persisted chapter teaching manifest that maps each knowledge point to formulas, every associated example, source type, assets, solution, walkthrough/notebook anchor, and coverage status.
- [x] Require full seven-step walkthrough content for key questions and an explicit concise-but-complete walkthrough contract for the remaining examples.
- [x] Require notebook/teaching evidence before a `study_guide` render. An unguided compilation must use the distinct `source_packet` artifact type.
- [x] Gate chapter completion on all knowledge points and all in-scope examples, not merely on presence of files.
- [x] Deduplicate a shared teaching/quiz ID into one guide card while preserving both its teaching and self-test roles.
- [x] Use explicit `full` and `abridged` guide profiles. Explicit “all/complete” selects `full`; `abridged` emits an omission ledger and cannot prove complete chapter coverage. Deliberately omit an implicit `auto` mode so the runtime never guesses what the student permits it to drop.

### 3.5 Language, quotation, and prompt-image policy

- [x] Make neutral persisted enum codes the schema truth and keep localized display values at the presentation boundary.
- [x] Separate agent-authored prose, source quotations, translations, and synthetic notices in the renderer schema.
- [x] Lint every bilingual agent-authored block for a Chinese and English mirror.
- [x] Label one-language source evidence as an original-language quotation.
- [x] When a full prompt image is present, suppress duplicate original prompt text; in bilingual mode show only the other-language translation when available.
- [x] Ensure delivery receipts and command summaries obey the persisted language mode.

### 3.6 Formula, text, and HTML safety

- [x] Reject or sanitize NUL and unsafe control bytes before Markdown/HTML persistence.
- [x] Treat pending formula/garbled-text review facts as content requirements even when the wiki contains no `$` delimiters.
- [x] Require MathML or a readable source image/crop for every formula-bearing block included in an artifact.
- [x] Extend HTML validation with control-byte, content-layer, walkthrough, bilingual, quotation/provenance, duplicate-ID, and coverage checks.

### 3.7 PDF route and visual QA

- [x] Persist one artifact receipt covering requested profile, content gates, selected backend, preflight backend, converter, PDF digest, QA renderer, page count, and check results.
- [x] Enforce backend consistency from preflight through conversion and QA.
- [x] Add print page numbers, useful running context, orphan-heading/card checks, abnormal blank-page checks, and original-size template/formula sampling.
- [x] Refuse final delivery until both pedagogical/content lint and visual QA pass.

### 3.8 PR 1 regression matrix

- [x] First-contact fixture: normal opening stops before write; urgent opening follows the explicit exception.
- [x] Split question source A / solution source B fixture.
- [x] Worked-example teaching-only and legacy `gradable=false` selection tests.
- [x] Empty-notebook `study_guide` rejection and explicit `source_packet` acceptance.
- [x] Complete knowledge-point/example manifest and chapter completion gates.
- [x] Full prompt image suppression in Chinese/English and translation-only text in bilingual mode.
- [x] Bilingual authored-content lint and original-quotation labeling.
- [x] NUL rejection, formula-review gate, MathML/source-image fallback.
- [x] Teaching/quiz overlap deduplication and full/abridged profile receipts.
- [x] Backend receipt consistency, page numbering, blank/orphan-page lint, bounded validator/review output.
- [x] Windows unit/integration/behavior suites plus repository CI coverage for the supported Linux runtime.

## 4. PR 2 work breakdown

### 4.1 Redistribution-safe Gold Set

- [x] Add tiny, project-authored PDF fixtures for text layout, multicolumn order, vector/table/formula pages, image-only scans, and shared prompt/answer crop behavior.
- [x] Store stdlib generation sources and expected location/unit/citation facts so binary fixtures are reproducible and redistribution-safe.
- [x] Add parser-capability matrix tests that skip optional adapters honestly rather than weakening the default stdlib floor.

### 4.2 Optional high-fidelity parser boundary

- [x] Keep the normalized local core adapter/parser-receipt protocol, but remove MinerU/Docling from the local executable route. Package presence or a callable local runner is never an integration or permission.
- [x] Never probe, download, install, import, or execute MinerU/Docling locally. Only an explicit named request may reach a separately configured remote/cloud host after service/upload/retention/privacy disclosure and separate consent.
- [x] Keep XLSX/raster and the normal PDF/OOXML core routes local. A remote result must return through a revision-bound host boundary and satisfy normalized provenance semantics; if no integration exists, report it unavailable and continue core + typed visual review.
- [x] Keep ingestion-v2 local parser receipts exact and local-only (`network/upload/install=false`); they do not pretend to attest or represent a separately operated remote parser.

### 4.3 XLSX, standalone images, and scans

- [x] Add XLSX OOXML ingestion for workbook/sheet/cell/table/formula/image metadata without requiring Excel or evaluating formulas.
- [x] Add standalone raster ingestion with signature-checked dimensions/hashes, one page-equivalent anchor, OCR/vision review tasks, and strict UTF-8 sidecar text where present.
- [x] Add a scan path that preserves evidence and routes to an installed local OCR/vision runner or typed agent review; no fake empty-text success.

### 4.4 Canonical groups, near duplicates, and source conflicts

- [x] Add revision-bound `canonical_group` derived facts without rewriting the location-derived `unit_id` or any source occurrence.
- [x] Implement deterministic exact/near-duplicate candidates using normalized fingerprints and a configurable similarity threshold; near candidates are not automatically canonical.
- [x] Preserve every source occurrence while folding only validated display/retrieval duplicates.
- [x] Model conflicts separately with revision-bound source priority, differing claims/answers, and explicit resolution status; priority never silently chooses a winner and unresolved conflicts fail closed.

### 4.5 Exact-location claim verification

- [x] Add a strict claim record linking an authored claim position to an exact Unicode code-point quote span and source unit/revision.
- [x] Verify exact quote containment/offsets, payload and source/unit digests, prompt-vs-answer-side boundaries, and bound artifact freshness.
- [x] Limit the location-only receipt to explicitly referenced claims whose subject coordinates locate authored guide text exactly equal to `claim_text`, then bind it to the canonical strict-JSON current guide hash plus source/content/group/conflict/claim hashes. Wire the ingestion-v2 typed-guide gate to recompute that receipt, require same-ref unit/role binding, and cover direct material knowledge-point explanations, formulas, printed prompts, and material answers while preserving v1 compatibility. Explicitly do not claim that the quote entails, supports, proves, or semantically agrees with the authored assertion; provenance still requires agent/human judgment.

### 4.6 Evidence-gated hybrid retrieval

- [x] Keep BM25 as the default and publish a strict reproducible query-Gold/run schema with Recall@k/MRR, near-miss, hard-negative, source, and index bindings. The committed synthetic sample is intentionally insufficient and is not promotion evidence.
- [x] Define experimental retriever-result/fusion receipts plus bounded RRF/reranker helpers without presenting dense/RRF/reranker as a production student backend.
- [x] Permit Dense + Sparse/RRF/reranking only when a sufficient frozen **real multi-course** recall Gold Set crosses every documented quality/safety/resource threshold and the optional backend separately passes consent/capability checks.
- [x] Return `INSUFFICIENT_EVIDENCE` or `NO_GO` when appropriate and keep heavyweight runtime dependencies absent. Even `GO_OPTIONAL` authorizes only an opt-in backend, not replacement of BM25.

### 4.7 Documentation honesty and packaging

- [x] Correct statements that described parser identities as turnkey vendor integrations, source files/IDs as immutable/content-derived revisions, experimental RRF as production support, or DOCX logical segments as physical pages.
- [x] Keep runtime manuals concise and put exact parser/fact/claim/retrieval schemas in directly linked references.
- [x] Keep the source checkout's benchmark/tests distinct from the slim runtime manifest while shipping the executable runtime adapters/contracts.
- [x] Keep the Gold Set project-authored and redistribution-safe; run skill/document consistency validation before delivery.

### 4.8 Optional workflow orchestration boundary

- [x] Document the executable workflow as an explicit state graph (`confirm -> ingest -> review loop -> validate -> tutor/notebook -> typed guide -> render -> per-page QA -> complete`) with fail-closed transition guards.
- [x] Keep the local Python commands, workspace state, and persisted receipts as the normative zero-extra-dependency execution core.
- [x] Deprecate the local LangGraph adapter from the default/runtime path. Preserve the explicit
  state graph in the zero-dependency core rather than downloading or importing LangGraph locally.
- [x] If a user explicitly requests cloud orchestration, require a host-provided cloud connector,
  separate upload/privacy consent, and receipt/state rehydration; a remote checkpoint never
  substitutes for source hashes, review, QA, or completion truth.
- [x] Serialize every coordinated validator-visible writer under the workspace registry/state/ingestion lock protocol. Official CLI conflict paths write nothing; completion uses one bound snapshot; input and fact parsing use stable byte generations; no parent lock spans a child process. Treat out-of-band writers and custom non-idempotent host APIs as explicit trusted-host boundaries rather than capabilities supplied by LangGraph.

### 4.9 Lightweight-first runtime amendment

The default student experience becomes progressive visual study rather than eager whole-course
ingestion. The structured/full route remains available, but it is an explicit choice and does not
silently activate heavyweight local dependencies.

Design evidence:

- OpenAI's current vision guidance makes `low` detail a 512 px, lower-cost triage
  surface and reserves `high`/`original` for fidelity-sensitive inspection. The
  implementation therefore uses a contact sheet only to choose pages, then reopens
  the selected full page or contextual crop:
  <https://developers.openai.com/api/docs/guides/images-vision>.
- OpenAI's published `hatch-pet` skill uses a final contact sheet and bounded
  lightweight visual workers to reduce parent-context image payload while preserving
  visual QA coverage. We reuse the two-stage inspection pattern, not its image
  generation workflow:
  <https://github.com/openai/skills/blob/main/skills/.curated/hatch-pet/SKILL.md>.
- Anthropic's vision guide confirms that image cost scales with visual patches and
  warns that important text must stay legible and necessary context must not be
  cropped away. This supports low-resolution triage followed by original-resolution
  formula/question inspection:
  <https://platform.claude.com/docs/en/build-with-claude/vision>.
- OpenAI's skill-creator guidance recommends progressive disclosure: keep the core
  route and selection rule loaded, and load variant-specific resources only when
  selected. The lightweight/full split follows that boundary:
  <https://github.com/openai/skills/blob/main/skills/.system/skill-creator/SKILL.md>.

- [x] Add canonical `processing_mode=lightweight|full` to persisted state and prompt for it once
  at startup, independently from the combined learning-mode/time/language choice and from
  `artifact_mode`. Accepted default, urgent, unanswered, missing, legacy, or invalid means
  `lightweight`; ordinary reconfirm without the flag preserves an existing canonical choice.
- [x] In lightweight mode, inventory filenames/media/size/mtime only; do not hash or parse every
  PDF or build the wiki/bank at startup. Bind the exact source hash only when the current phase
  plans a PDF/standalone-raster batch (maximum eight pages and one active batch).
- [x] For multi-page batches, require overview-only contact sheets that partition the pages
  exactly once in groups of at most four, then reopen formula/table/diagram/question pages and
  figure prompt crops as detail inputs. Contact sheets never replace source/page evidence.
- [x] Keep the local learning and page-batch state machines, notebook, progress, mistake, and
  confusion tracking. Do not invent or append a lightweight mini bank; without a pre-existing
  standard bank, completion is capped at `covered_unverified`.
- [x] Preserve every prompt-side figure and show it before asking or teaching the item. If the
  figure cannot be rendered at readable resolution, skip/fail closed rather than teach a text-only
  approximation.
- [x] Keep stored `artifact_mode` independent. In lightweight mode a saved `visual` preference is
  dormant and effective output is `chat`; Study Guide authoring/rendering remains unavailable
  until explicit `full`, while explanation detail is never shortened.
- [x] Add bounded image-input controls: eight-page windows, exact four-up overview partitioning,
  selective page/prompt detail reopening, answer-only solution calls, exact input receipts, and
  live hash/magic/dimension validation. Keep every visual input under `.lightweight/assets/` so
  it cannot reuse/contaminate full artifacts. Do not suppress output detail.
- [x] Add a receipt-backed `abandon --reason` transition for unfinished planned/visual-ready
  batches. Preserve the prior attempt for audit, allow a replacement attempt, exclude abandoned
  batches from coverage, and prohibit abandoning durable taught progress.
- [x] Bound routine mount/status validation to active and current-phase batches. Keep immutable
  receipt/event checks for non-current taught history, report it as `unchecked_historical`, and
  restore full source/asset/notebook live revalidation only when that phase becomes current again.
- [x] MinerU, Docling, and LangGraph are never installed, imported, executed, probed, or accepted
  as callable runners locally. An explicit named cloud-enhanced
  request requires a host cloud connector plus separate upload/privacy consent; otherwise core
  visual/state-machine behavior remains local and zero-extra-dependency.
- [x] Update startup/root/README/AGENTS/skill/architecture/file-format contracts and the migration
  behavior so prior full artifacts remain intact while the selected runtime route is enforced.
- [ ] Update/freeze the concentrated regression fixtures, distribution packaging, and release
  notes after real EEC 160 functional/visual acceptance.

### 4.10 Study Guide reader-noise, crop, and isolated-explanation amendment

The first real Chapter 1 render exposed three authoring problems that structural coverage and
page-level lint did not catch. These are release blockers for the structured/full route.

- [x] Replace repeated prose provenance banners in the student HTML/PDF with one opening legend.
  Strip labels from paragraph starts, append a compact provenance emoji at the end of the
  relevant content run, and collapse consecutive runs with the same provenance to one marker.
  Keep the full machine-readable provenance in the typed manifest/receipts.
- [x] Add explicit, revision-bound crop selections for prompt- and answer-side assets. A
  page-shaped screenshot containing unrelated questions/solutions must not render uncropped;
  use a normalized bbox selected by vision/human review or a deterministic locator, preserve the
  original asset hash and source reference, and fail closed on invalid/stale crop evidence.
- [x] Remove the generic `self_check` panel from new structured/full guides. Require a detailed
  `answer_explanation` for every walkthrough and render it after the answer/answer asset.
- [x] Generate each answer explanation through an isolated per-item request containing only the
  exact question, that item's answer, required figures/crops, language, and a fixed beginner-first
  explanation instruction. Never expose other questions, answer keys, course text, or executable
  source instructions to that call.
- [x] Keep model execution outside the deterministic compiler: emit one hash-bound request per
  item, accept one strict response per item from a host-selected LLM/sub-agent, validate schema,
  language, math, request hash, item ID, and injection-safe field boundaries, then mechanically
  merge complete responses. No API key, provider SDK, network call, or silent upload belongs in
  the repository script.
- [x] Make the isolated response set resumable and idempotent, record model/provider identity when
  the host can report it, and bind the exact request/response hashes into the authoring packet,
  notebook block, typed Guide, render receipt, and validator. Missing or stale item responses
  block `full` publication.
- [ ] Rebuild EEC 160 Chapter 1 with cropped assets and all isolated explanations, rerender every
  page, and repeat all-page visual QA from page 1.

### 4.11 Frozen second-acceptance defect batch

Implement all items below before any further EEC command or regression change. Do not
alternate individual fixes with individual tests.

- [x] Generalize lightweight schema-3 item evidence so `text`, `figure`, and `mixed`
  teaching items all support one-or-more prompt components and zero-or-more official
  answer components; keep exact, cross-page `teaching_item_ids` and legacy schema-2
  terminal history. Legacy figure-only active strategies are read-only and may only be
  auditably abandoned.
- [x] Represent disjoint but necessary context explicitly. Every crop component declares
  its role and `required_context_ids`; semantic review must detect exactly the target plus
  those contexts, while rejecting unrelated items and student attempts. Page/item and
  prompt-component coverage closes in both directions; every answer component still
  contains its target.
- [x] Add planned-state exact dependency replacement/removal, preserve it through
  `replace-taught`, and remove the contradictory single-page contact-sheet work order.
- [x] Add deterministic, revision-bound multi-region composition to full crop receipts and
  incremental backfill without reparsing PDFs. Bind every source/pixel/PDF region, layout,
  candidate/output hash, semantic verdict, and source/parser revision.
- [x] Statically review and freeze this batch once. Tracked `git diff --check` and no-index
  whitespace checks for all new scripts passed; no tests, compilation, rendering, or EEC
  commands ran during the repair batch.
- [ ] Rerun lightweight Problem 1.1.2 and text-only cross-file answer cases, backfill all
  19 Chapter 1 assets, rebase annotations, generate all isolated explanations,
  compile/render, and perform all-page visual QA.

### Fresh lightweight re-acceptance snapshot (2026-07-18)

- Clean workspace `D:\EEC 160-lightweight-acceptance-v3` initialized in
  `lightweight` + `chat` mode with 28 source inventory records and zero eager batches.
  The finished workspace contains no `.ingest/`, wiki, Study Guide, PDF, or HTML.
- Problem 1.1.2 passed as one `mixed` item with three independent prompt components
  (shared text, Venn diagram, and target subquestions) plus one official-solution
  component from another PDF. The page-level student attempt stayed outside every
  teaching crop. Visual receipt `lw-visual-a98a921394ced761e4235f87` and taught
  receipt `lw-taught-3c207db2260f03a0f6ebad20` bind the exact assets and bilingual
  beginner walkthrough.
- `replace-taught` preserved the immutable predecessor event, inherited the exact
  answer dependency page and original `registered_at`, revalidated both source
  revisions, created a fresh planned successor, and reopened the phase checklist.
  The acceptance-only successor was then auditably abandoned.
- Problem 1.2.1(a) passed as a generic `text` item with a prompt-only crop and a
  cross-file official answer crop. Visual receipt
  `lw-visual-792ff2bd4a13091ebc3f70c9` and taught receipt
  `lw-taught-b78664750d445d64222082e5` prove that the old figure-only namespace no
  longer blocks text teaching. The walkthrough also names the source prompt's
  inconsistent `hm` example and follows the official `hc` encoding.
- Exact answer-dependency expansion, replacement, idempotent replacement, removal,
  idempotent removal, and re-registration all passed against the real solution PDF.
  Only source pages 2 and 3 plus cached solution page 1 were rendered; teaching output
  remained unabridged and bilingual.
- Freeze these acceptance defects for one post-acceptance repair batch; do not patch
  them while full-path acceptance is running:
  1. `init` does not create `.lightweight/assets/`, so the first host-native render
     fails until the host creates an undocumented output directory.
  2. `register-answer-dependency --help` does not document its accepted `1-4,7`
     page-range grammar, making the valid multi-page form difficult to discover.
  3. `status.answer_taint_status` reports `blocked_tainted_or_unknown` for a taught
     batch whenever the full primary page contains a student attempt, even when every
     teaching crop independently proves `student_attempt_present=false` and the bound
     answer crop comes from an official solution. Preserve the conservative page fact,
     but separate it from the item-level publication/teaching verdict so the status
     panel does not claim a successfully taught item is blocked.

### Fresh full-path pre-backfill snapshot (2026-07-18)

- After refreshing the exact full/visual runtime receipt, workspace validation returned
  structural `ready`, teaching/quiz `usable_with_gaps`, artifact `blocked`, 27 source
  receipts, 1,077 pages, 3,210 units, 79 active Chapter 1 review issues, zero Chapter 1
  high-risk blockers, and zero source conflicts. Before that refresh, the same validator
  leaked an uncaught `FullProcessingRequired(registered_workspace_gate_blocked)` traceback
  instead of returning its documented structured fail-closed result.
- `study_guide_author prepare` reproduced exactly 19 prompt-side crop blockers in packet
  `587f0dfbe269ea1a776546719581d7c120427d97e363e7a5c1da19e2624aaf7d`:
  five lecture page images and fourteen homework legacy qcrops.
- Every blocker was visually inspected. Five lecture candidates now have fixed, tight
  pixel boxes in `.ingest/crop-review/`. Of the homework crops, eleven are clean
  target-only prompts, Problem 1.3.10 requires its real Theorem 1.4 context,
  Problem 1.1.2 requires a three-region target-plus-Quiz-1.1-context composite, and
  Problem 1.5.2 must discard an unrelated Example 1.18 accidentally included above the
  actual phone-call problem. Every `*_acrop*` remains student-attempt evidence and is
  forbidden as prompt, context, or official answer.
- The current incremental backfill cannot accept this real workspace, so authoring cannot
  yet advance to annotation rebase, isolated explanations, compile, render, or all-page
  QA. Freeze and repair the following as one acceptance-defect batch before rerunning EEC:
  1. Reconcile exact-path declarations across raw item assets, content-unit top-level
     assets, and nested paired-unit mirrors. Missing `type`/bbox fields must inherit only
     from one unambiguous canonical declaration; a prompt asset mirrored inside its paired
     answer unit must not become a foreign answer-page owner. Publication must update all
     mirrors once without emitting duplicate compact assets.
  2. Permit a revision/hash/source-bbox-bound legacy prompt crop to be the parent of a
     tighter single or deterministic composite crop. Bind and map through the parent's
     declared PDF bbox explicitly; do not mislabel that bbox as the full PDF page, invent
     an undeclared full-page parent, or reparse the PDF. Keep answer-side official-only
     policy and target/context semantic-v2 review unchanged.
  3. Preserve inline material solutions for `teaching_role=worked_example`. Example 1.6
     and Example 1.18 currently contain their worked result in the source page but compile
     as `answer_status=unknown` with no answer evidence, which would wrongly turn a
     source-backed worked example into an AI-generated answer.
  4. Catch runtime/workspace gate exceptions at the validator boundary and return stable
     JSON readiness/errors rather than a Python traceback.
  5. Replace the misleading hard-coded `parser_invoked=false` / `pdfs_reparsed=0` claim
     with a compiler-execution receipt that names the only invoked compiler command and
     binds before/after parser/source-manifest hashes. The source-byte revision check is
     allowed but must not be described as independent process attestation.
  6. Bind page geometry to a current source/parser fact or explicitly name it as reviewed
     parent geometry. Do not present annotation-supplied `page_box_pdf_points` as an
     independently verified parser fact when parser receipts do not contain geometry.

### Post-implementation static cross-review (2026-07-18)

No test, compile, render, or EEC command was used for this review. It found defects
that must be repaired before resuming functional acceptance:

1. EEC ContentUnit top-level `asset_path` mirrors commonly omit
   `metadata.asset_sha256` while the exact nested `metadata.assets` mirror carries the
   hash. Schema-2 backfill therefore still classified that top-level mirror as foreign
   and rejected all fourteen homework targets. Permit hash inheritance only for that
   exact same-unit top-level mirror; never relax source rows.
2. A declaration set whose source revisions are all missing must not be silently
   upgraded to the current manifest revision. At least one exact owner/mirror must bind
   the current revision and every conflicting revision must fail closed.
3. Inline worked-example recovery existed only in the full material builder. The
   current EEC generation would still require reparsing 1,077 pages. Add an explicit
   review-ledger migration based on an existing worked-example question unit, a unique
   same-source/revision/page/title material text unit, and the item's semantic-v2 prompt
   crop receipt; use standard `replace_unit + add_unit + pair_qa`, then compiler-only
   rebuild without mutating immutable source raw input.
4. A bare single-page Example with any non-empty body is not sufficient proof of a
   complete worked solution. Remove automatic `inline_material` promotion and require
   the explicit review path.
5. Reject `answer_origin=inline_material` from the quiz layer even when
   `gradable=false`; it is teaching evidence only. Also persist the real source-language
   prompt payload for figure examples instead of labelling a Chinese page-reference
   placeholder as English material.
6. Lightweight session schema remains 2 while visual receipts are schema 3. Correct the
   migration help, publish `status_schema_version=2` and
   `answer_taint_contract_version=2`, and expose full-processing gate reason/blockers as
   machine-readable fields.

Items 1-6 are now implemented. The same no-test static closure also found and fixed
three renderer/authoring defects before the EEC gate: every knowledge-point formula is
rendered even when no example references it; empty example mappings display the promised
material-absence notice; and source locations distinguish PDF pages, PPTX slides, XLSX
worksheets, and DOCX logical segments without inventing non-PDF `#page=` anchors. The
bilingual full-prompt notebook path now keeps only the missing-language translation and
points the source-language counterpart back to the image instead of duplicating OCR.

The explicit inline-worked migration is frozen as
`register-inline-worked -> claim -> draft-inline-worked -> validate-patch -> apply`.
It is teaching-only, requires one unique native `zh|en` source unit plus a current
semantic-v2 full-prompt crop, and is rejected by both raw ingestion and review compilation
if it reaches the quiz layer. The registration evidence, review queue, source status, and
build-manifest update share one rollback transaction; the patch uses a current-unit CAS
digest and the ordinary append-only ledger. Static `git diff --check` passed after this
batch. No test, Python compile, renderer, PDF, or EEC write command was used. Feature
development is now frozen; proceed to EEC functional/visual acceptance rather than
substituting unit tests for the real-data gate.

### Feature-freeze amendment: ordinary/extensions and PR #41 (2026-07-18)

Two maintainer decisions arrived after the original freeze and must be implemented
before resuming the real-data gate:

1. Product documentation and executable state must use **ordinary features** and
   **extensions**, never a low/high-tier label. The per-item fresh/stateless LLM
   explanation route is an extension, defaults off, and cannot be inferred from a GPT
   model name, an existing key, `artifact_mode=visual`, or a subscription. Enabling it
   is full-v2-only and requires two-stage consent: provider/API-billing and
   retention/privacy disclosure before a no-upload planning opt-in, then exact-plan
   item/image scope, call count, and a current-pricing estimate before upload consent.
   Ordinary full authoring still produces a detailed beginner-first explanation for
   every item but carries no isolation/provider receipt claim.
2. Upstream [PR #41](https://github.com/ZeKaiNie/universal-examprep-skill/pull/41)
   contributes an opt-in one-key-question-per-turn teaching pace. Preserve the intent,
   but replace free-form preference aliases and notebook-text inference with canonical
   `batch|step_by_step` state and durable phase evidence. Each paced turn completes the
   whole seven-step walkthrough for one item; a learner's “understood” reply is navigation,
   not completion evidence. `no_questions=true` must not emit a pause question, the
   `le1d` route may use only a non-reflective continue boundary when the preference was
   explicitly stored, and no route may bypass the existing phase denominator.

Implementation order remains unchanged: finish both feature amendments and static
cross-review; run the EEC 160 functional/visual acceptance once; batch-fix acceptance
defects; only then write/update and execute the concentrated regression matrix. PR
publication, merge, version, and release remain last.

The amendment implementation is now statically frozen. PR #41's notebook-presence and
free-form acknowledgement inference was replaced by a manifest-ordered state machine,
marker-bound notebook blocks, exact notebook/manifest hashes, append-only same-chapter
roster checks, and a mount-only recovery path for structurally sound new items or stale
revisions. Structural/schema/path/UTF-8/Markdown corruption remains blocked, and Guide or
completion callers never inherit the mount-only exception. Quiz, teaching, notebook,
`ContentUnit.external_id`, Guide, and current strict-crop semantic target/context IDs now
share one safe-Unicode stable-key contract; historical crop v1 and lightweight batch-local
technical IDs retain their own compatibility contracts. Two independent static reviews
found no remaining P0/P1, and `git diff --check` passed with only Windows AutoCRLF notices.
The isolated upstream-PR worktree carries the same applicable hardening plus a documented
base-aware transition: because upstream `main` at PR #41 predates the explicit processing
selector, a missing `processing_mode` there means its historical implicit full route;
explicit non-`full` remains dormant, while the larger processing-mode branch keeps its
new missing-to-lightweight rule. No tests, compile, renderer, PDF, or EEC command was run
during this amendment. Feature development is frozen again; resume the real EEC gate.

## 5. Real EEC 160 acceptance run

Run a pre-merge rehearsal with the exact candidate code so real course data can expose
authoring/rendering defects before review. After both PRs merge, repeat the final gate with
merged `origin/main`; only that second run is release acceptance.

### Feature-freeze lightweight acceptance (2026-07-18)

- Fresh isolated workspace: `D:\EEC 160-lightweight-acceptance-v4`, exact materials:
  `D:\EEC 160`, `from_scratch + le1d + bilingual + lightweight + chat`.
- Startup inventoried 28 source names only, created `.lightweight/assets/` itself, and
  reported `status_schema_version=2` plus `answer_taint_contract_version=2`.
- One mixed/diagram item (Problem 1.1.2) and one text item (Problem 1.2.1(a)) were
  accepted through planned page -> exact official-answer dependency -> independent
  component crop evidence -> detailed bilingual notebook -> taught receipt. Only source
  pages 2 and 3 plus cached solution page 1 were used; all prompt/answer crops were
  visually re-opened and remained target/context scoped.
- Both taught rows preserve the conservative full-page fact
  `student_attempt_or_unknown_present` while correctly publishing item-level
  `answer_taint_status=official_answer_components_clean`,
  `item_crop_review_status=clean_declared_scope`, and
  `teaching_publication_status=published_taught`.
- Final status: two taught batches, zero planned/visual-ready/stale batches, no health
  warning/error. The workspace contains no `.ingest`, wiki, Study Guide, PDF, or HTML;
  therefore the lightweight route did not silently fall back to eager/full processing.
- This was functional real-data acceptance, not a test-suite run. No project tests or
  Python compile command were executed.

### Full-path crop-backfill acceptance blocker (2026-07-18)

- A dry run accepted all 19 visually reviewed strict crop annotations and produced
  generation `a4c37058539aa9a6f3a73fa0fac8190564dcef571a76a8de73923dfca389f797`.
  The apply path then published the candidate generation and invoked only
  `scripts/ingest.py`; parser-receipt and source-manifest hashes remained byte-identical
  before/after the compiler attempt.
- The compiler failed closed with `asset-role migration receipts do not match actual
  policy changes`. The candidate pending record carries 125 role-promotion receipts even
  though this crop-only generation adds/replaces prompt crops and does not request those
  already-consumed historical `answer_context -> student_attempt` migrations. In other
  words, the backfill copied the cumulative parse-report ledger into a field whose
  compiler contract is generation-local and receipt-bijective.
- This is acceptance defect `EEC-FULL-01`. Keep the pending generation and its exact
  execution receipt intact; do not delete or hand-edit the blocker. Repair the backfill
  plan so a candidate generation carries only role migrations newly introduced relative
  to the current compiled policy, then use the documented generation-aware recovery path
  and rerun this affected acceptance gate. No test suite or Python compile was run.
- The frozen repair clears the copied cumulative promotion ledger when constructing an
  incremental backfill generation. Backfill always replaces declarations with a new
  physical crop identity (including legacy promotion), so it introduces no in-place role
  migration; the compiler still recomputes the old/candidate policy delta and fails closed
  if that invariant is ever violated. This code repair was not accompanied by a unit-test,
  compile, or renderer run; validation resumes at the EEC functional gate.
- Upgrade recovery for the already-published failed generation is deliberately narrower:
  the compiler may treat a non-empty candidate ledger as previously consumed only when the
  recomputed live policy delta is empty, the unchanged previous build manifest binds one
  complete material receipt through both canonical bindings, its raw/report bindings agree,
  and its promotion count/hash exactly equal the candidate ledger. Any new or mixed role
  change still requires the ordinary receipt-bijective generation-local ledger.
- The exact resume then completed both workspace compiler passes without reparsing PDFs,
  formally consumed the pending generation, and exposed acceptance defect `EEC-FULL-02`:
  derived teaching rows `lecture_example_1_1` and `lecture_example_1_22` lost required
  `teaching_role` even though their current raw teaching rows still carry
  `paired_problem`. Both question units were touched by older review patches whose partial
  metadata predates `teaching_role`; `_update_quiz_item_from_units` interprets an omitted
  optional field as deletion and therefore erases the valid route identity during the
  current recompile. Freeze this as one compiler-overlay defect: omission must preserve an
  existing teaching role/title, while an explicitly supplied reviewed value may still
  replace it. The validator correctly blocked publication with exactly these two errors.
- Static root-cause inspection expanded `EEC-FULL-02` before any rerun. The same old
  question/answer `replace_unit` rows also restore their captured whole-page assets over
  the current semantic-v2 prompt crops. Two later answer corrections have CAS digests
  authored against that historical pre-crop state, so mutating each legacy operation in
  place would invalidate otherwise legitimate history. The frozen compiler-overlay repair
  therefore replays every ledger/CAS state exactly, projects only immutable-base crops
  whose live schema-2/semantic-v2 receipt has one exact `supersedes` path, and performs the
  final projection without modifying ledger bytes. A CAS mismatch may compare against that
  one canonical projected state so future post-build patches remain replayable; it still
  fails for arbitrary drift. Parse-report bytes, crop PNG bytes, and exact source bytes are
  captured in the same revalidated batch snapshot. Ambiguous, partial, multi-path, missing,
  source-conflicting, or unsafe role changes fail closed; a reviewed change *to*
  `student_attempt` remains conservatively tainted and is never upgraded to an official
  answer. Separately, omission preservation is passed only to existing teaching rows for
  `teaching_role`/`teaching_title`; quiz rows and all other optional fields retain their
  previous deletion semantics. This repair received static/diff review only—no test suite,
  Python compile, renderer, PDF, or EEC command was run while editing it.
- A follow-up static adversarial review found one pre-commit gap: an otherwise exact new
  CAS patch could discard a base crop, be written to the append-only ledger, and fail only
  on the next replay. The same overlay invariant is now enforced before every single/batch
  validate/apply commit. Such a proposed unit must already preserve each base receipt
  exactly once and must not resurrect its superseded path; historical replay remains on
  the separate compatibility path. This closure also used static review and
  `git diff --check` only.

- [x] Preserve a revision-bound pre-run source/fact/asset snapshot in the Chapter 1
  authoring packet without altering original course files.
- [x] Run the workspace/readiness gate against the already confirmed workspace and persisted
  `from_scratch`, `le1d`, `bilingual`, `visual` state; structural readiness is `ready`,
  teaching is `usable_with_gaps`, and Chapter 1 has zero high-risk unresolved issues.
- [ ] Re-ingest or replay patches with the fixed split-source and worked-example semantics; quantify all remaining review issues by capability and current chapter.
- [ ] Complete Chapter 1 source-backed formula/text/visual review before publication.
- [ ] Build the Chapter 1 teaching manifest: every knowledge point and all mapped lecture, homework, quiz, and mock/past-exam examples.
- [ ] Persist bilingual explanations and walkthroughs through the official notebook/state tools; do not hand-edit generated progress views.
- [ ] Render an explicit `full` bilingual Study Guide so the one-day preference does not silently omit the maintainer-requested complete coverage.
- [ ] Confirm that full prompt images suppress duplicate original question text and that the opposite-language translation is present where required.
- [ ] Render every PDF page to full-size PNG, inspect every page, and rerun from page one after each renderer/content fix.
- [ ] Run machine lint for NUL/control bytes, MathML/source-image formula coverage, bilingual authored blocks, source quotations/translations, example inventory coverage, duplicate IDs, backend consistency, blank pages, orphan headings, page numbering, and provenance.
- [ ] Produce an artifact/readiness receipt that names every remaining non-blocking gap. Do not call the course or chapter complete unless its evidence gates pass.

## 6. Progress ledger

| Milestone | Status | Evidence |
| --- | --- | --- |
| Audit read in full | complete | 19 findings in the 2026-07-14 report were mapped above |
| Git/GitHub baseline verified | complete | clean tree; authenticated account has write permission on upstream |
| PR 1 implementation | complete | executable session gates, capability readiness, typed Study Guide, exact coverage/source/asset/notebook binding, PDF receipt and all-page QA implemented; final adversarial review found P0=0 and residual P1=0 |
| PR 1 tests | complete | full suite: 1653 tests passed, 34 optional-platform skips; focused remediation matrix: 361 tests passed, 17 skips; Python compile and `git diff --check` passed |
| PR 1 merged | complete | upstream PR [#24](https://github.com/ZeKaiNie/universal-examprep-skill/pull/24), merge commit `85cf7473263aebcec75cbed226248ce10298aa1a`; Ubuntu/Windows × Python 3.8/3.12 CI all passed |
| PR 2 implementation | complete | Gold fixtures, parser/LangGraph host-boundary contracts (now remote-only by the later amendment), dedicated XLSX/raster paths, canonical/conflict facts, exact-location claim receipts, evidence-gated retrieval, package cleanup, and coordinated publication/input snapshot hardening implemented; final adversarial review found P0=0 and residual P1=0 |
| PR 2 tests | complete | final full suite: 1865 passed, 39 optional-platform skips; focused evidence: ingestion/retrieval/package 381 passed (7 skips), publication/registry/host/LangGraph 235 passed (5 skips), claim/host/LangGraph 70 passed; Python compile, 11 skill validations, `git diff --check`, and the 93-file 592,678-byte runtime package cap passed |
| PR 2 merged | complete | upstream PR [#25](https://github.com/ZeKaiNie/universal-examprep-skill/pull/25), merge commit `d5a458626f79afd83b18b9110a0d1f233cb21695` |
| EEC visual hotfix merged | complete | upstream PR [#26](https://github.com/ZeKaiNie/universal-examprep-skill/pull/26), merge commit `2d75c07d3d05c26e94e26f700e0c8a20e14f7487` |
| Review batching/A-B pairing merged | complete | upstream PR [#27](https://github.com/ZeKaiNie/universal-examprep-skill/pull/27), merge commit `0622c8c30c2b63f5df2814890aa5a2ecfbd935b9`; Windows/Linux × Python 3.8/3.12 passed |
| Follow-up ingestion/Guide hardening merged | complete | upstream PRs #28–#38; current `origin/main` is `c5137f2` |
| Final acceptance hotfix implementation | complete | roster-driven prompt-only crops and visual-review blockers; fail-closed formula/control obligations; typed metadata, chapter inheritance, and cross-source revision replay guards; canonical receipt/state contract later restricted to remote-only LangGraph hosts |
| Final acceptance hotfix tests | complete | full suite: 1,917 passed, 40 optional-platform skips; focused ingestion/homework/readiness/LangGraph matrix: 617 passed; 11 skill validations, Python compile, `git diff --check`, and runtime package test passed |
| Source-integrity/chapter-scope hardening | complete | frozen adversarial review P0=0/P1=0; final full suite 2,168 passed with 41 optional-platform skips; 11 root/sub-skill validations passed in Python UTF-8 mode; all 50 shipped Python files parse under the Python 3.8 grammar; `git diff --check` and focused transaction/raster/Guide matrices passed |
| Current feature closure | complete | lightweight lifecycle/cross-source-answer/checkpoint contracts; strict incremental crop backfill; review-ledger inline worked-example recovery; narrow annotation rebase; complete formula/example rendering; source-format-aware anchors; Study Guide prompt de-duplication, per-item explanation, provenance and QA contracts are frozen after independent static cross-reviews; `git diff --check` passed after the 2026-07-18 closure; deliberately no tests, compile, render, or EEC write occurred during feature development |
| PR #41 hardened integration | implementation complete; acceptance pending | canonical `batch|step_by_step`, manifest-order selector, marker/hash-bound evidence, safe recovery of new/stale roster items, strict structural failure, shared safe-Unicode IDs through full visual evidence, and an isolated upstream-base compatibility port all passed independent static P0/P1 review plus `git diff --check`; no tests/compile were run before the EEC gate |
| Second EEC acceptance defect batch | complete | generic/cross-page item components, exact required context, schema-2/legacy-strategy quarantine, dependency replacement/removal, single-page work order, deterministic composite crops, strict semantic-v2 author/explainer propagation, and tainted-parent/official-answer policy were statically frozen; tracked and all-new-script whitespace checks passed, with no tests/compile/render/EEC commands during repair |
| Fresh EEC re-acceptance | in progress | lightweight v4 acceptance is complete: two source pages and one cached official-answer page produced schema-3 mixed/text visual receipts plus bilingual taught receipts, with no eager ingestion or Study Guide artifacts. Full structured recompilation then completed from the current raw input without reparsing PDFs: 3,210 units, 184 retained teaching examples (`141 paired_problem`, `43 worked_example`), 295 bank items, and no missing teaching identity. Chapter 1 validation has zero errors and is `usable_with_gaps`; the first post-crop author packet was `ready` with all 19 former crop blockers cleared. Exact source/raw/parser hashes remained unchanged. Visual review of same-page Examples 1.6 and 1.18 exposed `EEC-FULL-03`: the receipt-bound crop was present in `metadata.assets`, while the inline-worked registrar incorrectly required the optional legacy top-level mirror. The concentrated fix accepted an absent/exact mirror and returned absolute draft paths; both real patches then validated and applied in one compile, producing two reciprocal material pairs, 3,212 units and a 261-entry ledger without adding quiz rows. Re-preparing the author packet exposed the next frozen batch: `EEC-FULL-04`, a redundant receipt-less top-level crop mirror blocked the otherwise valid nested crop; and `EEC-FULL-05`, the teaching view retained `answer_status=unknown` alongside a real `inline_material` answer. The batch fix now keeps receipt crops canonical only in `metadata.assets`, projects historical redundant top-level mirrors back to the base declaration during replay, removes exact `answer_status=unknown` only when a usable answer is compiled, and preserves other typed prompt components during inline migration. The 261-entry ledger rebuilt successfully with only two teaching-view changes; final runtime confirmation, validation, and author preparation all passed. Isolated-explanation preparation then exposed `EEC-FULL-06`: the two recovered native answer units retained a normal page-final newline, while the model-input schema requires trimmed strings. All 35 trusted Chapter 1 answer payloads were inspected together; only Examples 1.6 and 1.18 have this boundary whitespace and none approaches the size limit. The frozen repair strips outer whitespace only in the transport copy, proves normalized content identity, and leaves the original packet/source revision hash-bound; static review and the affected EEC explanation gate remain to be completed before any test suite runs. |
| Runtime package budget | pending recheck | 97 files / 569,659 bytes is historical evidence from the previous accepted build, not evidence for this enlarged branch; remeasure only in the final concentrated regression/package phase |
| EEC 160 rebuilt | pending | — |
| Chapter 1 full visual QA | pending | — |
| Post-acceptance release | pending | authorized; version/tag/release notes wait for merged-runtime EEC acceptance |

Update this ledger after each completed milestone. Do not mark a milestone complete from a prose claim alone; link it to a commit, test command, receipt, or generated artifact.
