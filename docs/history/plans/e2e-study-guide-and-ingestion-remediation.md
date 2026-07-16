# E2E Study Guide and Ingestion Remediation Plan

Status: implementation merged through PR #38; source-integrity hardening and final EEC 160 acceptance in progress

Owner: Codex

Baseline: `origin/main` at `c5137f2` after PRs #24–#38

Primary audit: `D:\EEC 160\universal-examprep-e2e-audit-2026-07-14.md`

Real-course acceptance workspace: `D:\EEC 160-exam-workspace-acceptance` (clean rebuild; the audited workspace remains read-only evidence)

Real-course source materials: `D:\EEC 160`

## 1. Goal and non-negotiable behavior

Deliver two independently reviewable and independently mergeable pull requests.

1. The first PR repairs the end-to-end teaching and Study Guide path. A document may be called a Study Guide only after its current chapter has source-backed concepts, complete example coverage, persisted walkthroughs, language-complete explanations, capability-specific readiness, and a consistent PDF/QA receipt.
2. The second PR expands the lightweight ingestion/retrieval core with redistribution-safe layout/OCR fixtures, optional host-runner parser adapters, dedicated XLSX/image/scan routes, duplicate/conflict facts, exact-location claim receipts bound to the current guide revision (not semantic-entailment proof), and an evidence gate for any future optional hybrid retriever.

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
  metadata/control-byte repairs, fail-closed supplied/local formula-quality merging, and a compact
  optional LangGraph Study Guide stage guard.

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

### 4.2 Optional high-fidelity parser adapters

- [x] Introduce a narrow normalized adapter protocol and capability/extraction receipts for core plus MinerU/Docling host-runner identities. Package presence alone is not an executable vendor integration.
- [x] Keep MinerU/Docling imports absent from the normal path. The adapter itself does not install, access the network, or upload; its policy is a validated configuration declaration, not a sandbox/attestation of the host-supplied runner, whose operator owns enforcement.
- [x] Route only eligible PDF/OOXML files through the explicitly selected runner while XLSX/raster retain dedicated core routes, and validate normalized element/provenance semantics regardless of backend.
- [x] Document the local-only privacy/install boundary and record exact backend/module/distribution/version/config/source/output anchors in parser receipts.

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

- [x] Correct statements that described host-runner adapters as turnkey vendor parsers, source files/IDs as immutable/content-derived revisions, experimental RRF as production support, or DOCX logical segments as physical pages.
- [x] Keep runtime manuals concise and put exact parser/fact/claim/retrieval schemas in directly linked references.
- [x] Keep the source checkout's benchmark/tests distinct from the slim runtime manifest while shipping the executable runtime adapters/contracts.
- [x] Keep the Gold Set project-authored and redistribution-safe; run skill/document consistency validation before delivery.

### 4.8 Optional workflow orchestration boundary

- [x] Document the executable workflow as an explicit state graph (`confirm -> ingest -> review loop -> validate -> tutor/notebook -> typed guide -> render -> per-page QA -> complete`) with fail-closed transition guards.
- [x] Keep the local Python commands, workspace state, and persisted receipts as the normative zero-extra-dependency execution core.
- [x] Define an optional LangGraph host adapter for agents that already use LangGraph, mapping checkpoints to bounded routing hints and human-in-the-loop interrupts to existing typed review/visual-QA commands.
- [x] Keep LangGraph out of the default dependency set; require lazy import, a host-supplied durable checkpointer, and receipt/state rehydration so a graph checkpoint or resume flag never substitutes for source-hash, review, QA, or completion truth.
- [x] Serialize every coordinated validator-visible writer under the workspace registry/state/ingestion lock protocol. Official CLI conflict paths write nothing; completion uses one bound snapshot; input and fact parsing use stable byte generations; no parent lock spans a child process. Treat out-of-band writers and custom non-idempotent host APIs as explicit trusted-host boundaries rather than capabilities supplied by LangGraph.

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
| PR 1 merged | complete | upstream PR [#24](https://github.com/ZeKaiNie/universal-examprep-skill/pull/24), merge commit `85cf7473263aebcec75cbed226248ce10298aa1a`; Ubuntu/Windows × Python 3.8/3.12 CI all passed |
| PR 2 implementation | complete | Gold fixtures, optional parser and LangGraph host adapters, dedicated XLSX/raster paths, canonical/conflict facts, exact-location claim receipts, evidence-gated retrieval, package cleanup, and coordinated publication/input snapshot hardening implemented; final adversarial review found P0=0 and residual P1=0 |
| PR 2 tests | complete | final full suite: 1865 passed, 39 optional-platform skips; focused evidence: ingestion/retrieval/package 381 passed (7 skips), publication/registry/host/LangGraph 235 passed (5 skips), claim/host/LangGraph 70 passed; Python compile, 11 skill validations, `git diff --check`, and the 93-file 592,678-byte runtime package cap passed |
| PR 2 merged | complete | upstream PR [#25](https://github.com/ZeKaiNie/universal-examprep-skill/pull/25), merge commit `d5a458626f79afd83b18b9110a0d1f233cb21695` |
| EEC visual hotfix merged | complete | upstream PR [#26](https://github.com/ZeKaiNie/universal-examprep-skill/pull/26), merge commit `2d75c07d3d05c26e94e26f700e0c8a20e14f7487` |
| Review batching/A-B pairing merged | complete | upstream PR [#27](https://github.com/ZeKaiNie/universal-examprep-skill/pull/27), merge commit `0622c8c30c2b63f5df2814890aa5a2ecfbd935b9`; Windows/Linux × Python 3.8/3.12 passed |
| Follow-up ingestion/Guide hardening merged | complete | upstream PRs #28–#38; current `origin/main` is `c5137f2` |
| Final acceptance hotfix implementation | complete | roster-driven prompt-only crops and visual-review blockers; fail-closed formula/control obligations; typed metadata, chapter inheritance, and cross-source revision replay guards; optional canonical-receipt LangGraph stage guard |
| Final acceptance hotfix tests | complete | full suite: 1,917 passed, 40 optional-platform skips; focused ingestion/homework/readiness/LangGraph matrix: 617 passed; 11 skill validations, Python compile, `git diff --check`, and runtime package test passed |
| Source-integrity/chapter-scope hardening | complete | frozen adversarial review P0=0/P1=0; final full suite 2,168 passed with 41 optional-platform skips; 11 root/sub-skill validations passed in Python UTF-8 mode; all 50 shipped Python files parse under the Python 3.8 grammar; `git diff --check` and focused transaction/raster/Guide matrices passed |
| Runtime package budget | verified | final deterministic Windows build: 97 files / 569,659 bytes; enforced cap remains 570,000 bytes, leaving 341 bytes of measured headroom |
| EEC 160 rebuilt | pending | — |
| Chapter 1 full visual QA | pending | — |
| Post-acceptance release | pending | authorized; version/tag/release notes wait for merged-runtime EEC acceptance |

Update this ledger after each completed milestone. Do not mark a milestone complete from a prose claim alone; link it to a commit, test command, receipt, or generated artifact.
