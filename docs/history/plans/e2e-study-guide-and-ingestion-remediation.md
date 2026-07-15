# E2E Study Guide and Ingestion Remediation Plan

Status: in progress

Owner: Codex

Baseline: `origin/main` at `d9835578ade4ea828b8c5d86c2b19bf10eb41384`

Primary audit: `D:\EEC 160\universal-examprep-e2e-audit-2026-07-14.md`

Real-course acceptance workspace: `D:\EEC 160-exam-workspace-vnext` (clean rebuild; the audited workspace remains read-only evidence)

Real-course source materials: `D:\EEC 160`

## 1. Goal and non-negotiable behavior

Deliver two independently reviewable and independently mergeable pull requests.

1. The first PR repairs the end-to-end teaching and Study Guide path. A document may be called a Study Guide only after its current chapter has source-backed concepts, complete example coverage, persisted walkthroughs, language-complete explanations, capability-specific readiness, and a consistent PDF/QA receipt.
2. The second PR expands the lightweight ingestion/retrieval core with redistribution-safe layout/OCR fixtures, optional high-fidelity adapters, dedicated XLSX/image/scan routes, duplicate/conflict facts, claim-level citation verification, and an evidence gate for any future hybrid retriever.

The following output rules are explicit acceptance requirements:

- Organize a full guide as `knowledge point -> formula/meaning -> every mapped example -> worked solution -> formula substitution and self-check -> source trace`.
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

No release is part of this request. Both PRs will be ready for review and may be merged without a separate approval round, as authorized by the maintainer.

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

- [ ] Add tiny, project-authored PDF fixtures for text layout, multicolumn order, vector/table/formula pages, image-only scans, and shared prompt/answer crop behavior.
- [ ] Store generation sources and expected page/unit/citation facts so binary fixtures are reproducible and redistribution-safe.
- [ ] Add parser-capability matrix tests that skip optional adapters honestly rather than weakening the default stdlib floor.

### 4.2 Optional high-fidelity parser adapters

- [ ] Introduce a narrow adapter protocol and capability receipt for core, MinerU, and Docling routes.
- [ ] Keep MinerU/Docling imports lazy and optional; never install or upload silently.
- [ ] Route only eligible files/pages after probing, and preserve normalized element/provenance semantics regardless of backend.
- [ ] Document license/privacy/installation consent and record the exact backend/version/config in build facts.

### 4.3 XLSX, standalone images, and scans

- [ ] Add XLSX OOXML ingestion for workbook/sheet/cell/table/formula/image metadata without requiring Excel.
- [ ] Add standalone raster ingestion with dimensions, hashes, page-equivalent anchors, OCR/vision review tasks, and sidecar text where present.
- [ ] Add a scan path that renders evidence and routes to an installed OCR/high-fidelity adapter or typed agent review; no fake empty-text success.

### 4.4 Canonical groups, near duplicates, and source conflicts

- [ ] Add `canonical_group` facts without changing immutable unit identity.
- [ ] Implement deterministic exact/near-duplicate candidates using normalized fingerprints and a configurable similarity threshold.
- [ ] Preserve every source occurrence while folding display/retrieval duplicates.
- [ ] Model conflicts separately with source priority, differing claims/answers, and unresolved status; never silently choose a winner.

### 4.5 Claim-level citation verification

- [ ] Add a claim record linking normalized claim text to an exact quote span and source unit/revision.
- [ ] Verify quote containment, span offsets, source digest, answer-side leakage boundaries, and rebuild freshness.
- [ ] Require verified claims for material-provenance guide assertions; otherwise use the AI-supplemented/unknown contract.

### 4.6 Evidence-gated hybrid retrieval

- [ ] Keep BM25 as the default and publish a reproducible query Gold Set with Recall@k/MRR and hard negatives.
- [ ] Define retriever-result and fusion receipts without claiming dense/RRF/reranker support prematurely.
- [ ] Activate Dense + Sparse, RRF, or reranking only if the committed real Recall evidence crosses a documented failure threshold and the optional backend passes consent/capability checks.
- [ ] If BM25 is adequate or evidence is insufficient, record the no-go decision and do not add heavyweight runtime dependencies.

### 4.7 Documentation honesty and packaging

- [ ] Correct statements that currently describe candidate adapters, immutable revisions, content-derived IDs, RRF extension points, DOCX physical pages, or release/test state as implemented facts.
- [ ] Keep runtime manuals concise; detailed adapter and schema material belongs in directly linked references.
- [ ] Ensure the public source checkout and distribution manifest clearly distinguish source tests/CI from the slim runtime package.
- [ ] Run skill structure validation and verify no real course material enters the repository.

### 4.8 Optional workflow orchestration boundary

- [ ] Document the executable workflow as an explicit state graph (`confirm -> ingest -> review loop -> validate -> tutor/notebook -> typed guide -> render -> per-page QA -> complete`) with fail-closed transition guards.
- [ ] Keep the local Python commands and persisted receipts as the normative, zero-extra-dependency execution core.
- [ ] Define an optional LangGraph host adapter for agents that already use LangGraph, mapping checkpoints to existing receipts and human-in-the-loop interrupts to typed review/visual-QA gates.
- [ ] Do not add LangGraph to the default runtime dependency set; add an adapter only if it can call the same commands without weakening replay, idempotency, source-hash, or completion guarantees.

## 5. Real EEC 160 acceptance run

This run happens only after both PRs merge, using the merged `origin/main` code rather than a modified installed copy.

- [ ] Preserve a receipt of the pre-run workspace/source hashes and archive obsolete derived Study Guide artifacts without altering original course files.
- [ ] Run the executable start/readiness gate against the already confirmed workspace and persisted `from_scratch`, `le1d`, `bilingual`, `visual` state.
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
| PR 1 merged | pending | — |
| PR 2 implementation | pending | — |
| PR 2 tests | pending | — |
| PR 2 merged | pending | — |
| EEC 160 rebuilt | pending | — |
| Chapter 1 full visual QA | pending | — |

Update this ledger after each completed milestone. Do not mark a milestone complete from a prose claim alone; link it to a commit, test command, receipt, or generated artifact.
