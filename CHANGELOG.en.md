# Changelog

English · [中文](CHANGELOG.md)

> Runtime skill text (`SKILL.md` / `AGENTS.md` / `skills/**` / `prompts/` / `docs/`) describes current behavior directly and **no longer mentions version numbers**. Version history is centralized in this file for traceability.

## Unreleased

## V4.3 — 2026-07-18

- **Entry-point and release-note corrections**: The Chinese and English v4.3 READMEs and Release Notes are now bilingual, and the English page’s language switch once again displays “中文.” Installation instructions now provide copyable prompts that let each Agent install over the network directly; manual downloads are only an offline fallback. The complete benchmark charts, cost table, and Star History have also been restored, and every extended feature now has a five-point recommendation score plus a copyable enable command.
- **Prefer host-internal per-item child agents**: A full-v2 Study Guide defaults to internal child agents when the host can verify “a fresh independent context for every item + single-item input/tool restrictions,” with no second API Key required; the first use explains the additional quota/time. If capability is incomplete or unverifiable, ordinary explanations remain in use. A separately billed external Provider is demoted to an explicit-user-request fallback and continues to require two-stage authorization covering price, privacy, scope, and upload.
- **Student entry points rewritten**: The Chinese and English READMEs now begin with “what materials to provide, what to choose the first time, and how each chapter will be taught,” using directly actionable student examples to explain lightweight on-demand processing, full knowledge-base builds, ordinary features, and extended features. Unnecessary English engineering terminology has been removed from the Chinese body; low-level state-machine, receipt, and adapter details now live in maintainer documentation.
- **Lightweight on-demand learning by default**: Startup now clearly separates ordinary `lightweight` processing from extended `full` processing. By default, the system inventories filenames and visually reads only current-chapter batches, preserving question figures, the learning state machine, and complete teaching output without prebuilding the entire knowledge base or automatically generating a Study Guide/PDF. MinerU, Docling, and LangGraph remain available only when named explicitly, remotely hosted, and separately consented to.
- **Recoverable per-item teaching cadence**: Full-build mode adds a canonical `batch|step_by_step` choice. Step-by-step mode follows manifest order strictly and records completion evidence through stable question IDs, source revisions, notebook markers, and content hashes. Stale evidence may be reopened auditably; structural damage fails closed. A single “continue” can never impersonate learning completion.
- **Isolated answer explanations and optional OpenAI adapter**: Every Study Guide item can produce an independent, tool-free request containing only that item, its answer, and target-scoped crop images, with a host receipt binding the input, attachments, and coverage metadata. Host-internal independent child agents are preferred. The OpenAI adapter is off by default and runs only after the user explicitly requests it and completes exact planning and upload authorization.
- **Semantically pure crops and verifiable textbooks**: Prompt/answer components must pass target-level crop review. Whole pages containing unrelated questions, answers, or student work are forbidden. Per-field `claim → quote span → source unit`, material generation, explanation receipts, bilingual translation, and readable formulas jointly gate the typed Guide.

- **Explicit material-generation recovery and auditable supersession**: When a pending generation encounters a missing/drifted runtime receipt, ordinary `confirm` no longer creates a deadlock or overwrites provenance. A generation-bound `recover-material-build --action resume|supersede` is added. `resume` permits only an exact same-generation reconstruction and fails with zero publication when the candidate differs. `supersede` binds each direct predecessor through schema `2`. Generation-addressed recovery logs, 64-event/64-edge/65-receipt limits, full compiler transaction rollback, receipt completion, and the exact manifest-retained key/hash set jointly prevent silent generation loss, shortcut ancestry, and half-completed crash states.
- **Builder→compiler generation fail-closed behavior**: `ingest_course.py` publishes a new generation only after the builder succeeds and writes hash-bound `material_build_pending.json` before changing any asset, raw input, or parse report. A nonzero builder result neither overwrites the canonical parse report/raw input nor publishes candidate assets; if publication rollback is incomplete, the blocker is retained. Pending binds the old build manifest, new raw/report data, the complete candidate-asset policy, and migration receipts. All ordinary mutations/publications—including review, claims, and Guides—and validation fail closed while pending exists. The compiler accepts only an `answer_context → student_attempt` role difference that is in exact one-to-one correspondence with receipts.
- **Whole-compiler transaction and recoverable intent**: A material generation’s structured facts, build manifest, wiki/question bank/teaching examples, retrieval index, reports/plans, and pending→receipt transition now participate in one bounded `pending_ingest.json` rollback transaction. If interrupted, the validator refuses to treat the workspace as ready, and the next mutation restores every registered target before continuing. On success, build-manifest schema `2` records the strict `material_build` contract plus the raw/report/receipt hash triple; it cannot be downgraded to schema `1`. Learner-state initialization/preference writes performed afterward by `ingest_course.py` are outside this compiler transaction.
- **Legacy homework screenshot role migration**: A legacy `answer_context` may be upgraded exactly to `student_attempt` only when path, chapter/item ownership, homework source, nested provenance, source-file and asset SHA-256, live bytes, and physical file identity are all unique and consistent. The candidate policy is frozen and re-audited after asset processing but before the first JSON replacement. Any alias, hardlink, or concurrent drift fails closed.
- **Failed ingestion no longer contaminates the last successful report**: When the builder returns nonzero, `ingest_course.py` does not replace canonical raw input, the parse report, or published assets; diagnostics from the failure are returned only in that command’s payload/stderr. Markers still accept booleans only. A successful run that requests publication suppression, or a non-boolean value, is rejected.
- **Reproducible, bounded cross-platform distribution**: Before compression, `build_dist.py` normalizes runtime text to LF and removes only `NL` tokens classified by the Python tokenizer as non-semantic layout. Significant tokens, AST, shebangs, and encoding declarations have regression protection. CRLF and LF checkouts therefore produce the same ZIP. The v4.3 package ships both the default lightweight route and the on-demand full-ingestion/strict-Guide toolchain; the audited candidate is approximately 776 KiB and the hard limit is accordingly raised to 850,000 B. Default startup still does not load or execute extended routes.

- **Complete-chapter Study Guide gate**: `profile=full` now uses all current-chapter teaching examples, all bank records—including `gradable=false` teaching items—and typed question units as its deduplicated denominator. Chapter/language, prompt replacement, answer provenance, notebook evidence, per-field claims, and live source revisions all fail closed before import and rendering.
- **Source and asset integrity**: Safe physical identity now recognizes hardlinks/path aliases and global `student_attempt` contamination consistently, binding declared SHA-256 values to live bytes. PNG/JPEG/WebP/GIF/BMP use shared strict decoding checks; damaged images cannot enter ingestion, teaching display, Guides, QA, or cheatsheets.
- **Atomic batch publication and generation consistency**: Builder, visual-index, Study Guide, and cheatsheet multi-file/image publication now includes preflight, journaling, rollback, and fault-injection coverage. Normal ingestion binds compiler input to the exact raw-input generation produced by the builder, preventing another build result from being inserted between the two locks.
- **Auditable visual textbooks**: Prompt images precede answer images. A complete prompt image no longer causes duplicate pasting of the prompt text; bilingual mode adds only the target-language translation. HTML/PDF and per-page visual-QA receipts retain one content/asset snapshot, and damaged images, drift, or stale artifacts are no longer misreported as deliverable.

## V4.2 — 2026-07-14

> See [`docs/history/plans/knowledge-ingestion-hardening.md`](docs/history/plans/knowledge-ingestion-hardening.md) for the complete review, design, and implementation record.

- **Structured course ingestion**: Adds `ingest_course.py` as the sole normal entry point from PDF/DOCX/PPTX/txt/Markdown to a validated workspace. Exit code `0` means learning may begin; `10` means engineering completed but content issues still block readiness.
- **Recoverable, traceable fact layer**: Ingestion intermediate state is centralized under `.ingest/` as the source manifest, ContentUnits, chapter mappings, and evidence files. Stable IDs, strict schemas, source hashes, page numbers, and asset provenance make compiled results rebuildable and source drift detectable; multi-file transactions can roll back after interruption.
- **Typed AI takeover**: Every warning, skip, missing answer, and low-confidence page enters a ReviewIssue queue and append-only ReviewPatch ledger. `ingest_review.py` provides claim, validate, apply, mark-unrecoverable, rebuild, and revalidation flows, so “AI will take over” is no longer just a log message.
- **Lightweight retrieval and publication gates**: Structure-aware chunks, concept postings, index-integrity checks, and deterministic Recall@1/5 and MRR evaluation use only the standard library. Validator and runtime share `ready` / `usable_with_gaps` / `blocked`, preventing “structurally runnable” from being misreported as “materials complete.”
- **Document and visual extraction hardening**: DOCX/PPTX extraction covers tables, content controls, formula/list review signals, speaker notes, hidden objects, and image hashes. Visual and answer content continues to fail closed; uncertain content enters review instead of being silently dropped.
- **Skill and repository structure convergence**: Main skill, sub-skills, bilingual wording, file formats, and cross-host guidance now share readiness, provenance, and page-anchor contracts. Completed historical plans/release notes are archived, while duplicate indexes, the caption gallery, and the LlamaIndex spike already absorbed by production retrieval are retired to keep the student runtime package lightweight.

## V4.1 — 2026-07-14

> See [`docs/history/plans/PLAN-v4.1-real-world-hardening.md`](docs/history/plans/PLAN-v4.1-real-world-hardening.md) for the complete implementation record.

- **Real-course completeness hardening**: Visual coverage is split into wiki, prompt, and answer sides. Blank/image-only PDF pages enter the denominator, and reattachment accepts only images carrying original-page provenance. The index binds workspace input, original PDF content/path inventory, and derived-result hashes, then checks freshness at phase completion. Answer-only pages are deferred to the solution section; manually exposing answers early or sharing a whole page between prompt and answer fails closed.
- **Teaching examples no longer disappear during question-bank cleanup**: Adds `references/teaching_examples.json`, append-only `references/teaching_baseline.json`, and a per-chapter listing tool. Smaller raw input or rewritten reports cannot reduce the baseline. The gradable question bank remains the sole answer source, while the teaching layer guarantees worked-example reachability.
- **Phase completion becomes an evidence gate**: Wiki, visual, teaching-example, notebook, and checkpoint evidence is written to `phase_evidence`. `verified` and `covered_unverified` are separated; legacy workspaces remain compatible but do not impersonate complete evidence.
- **Human-readable chapter textbooks**: Canonical `$...$` / `$$...$$` math sources, raw/pseudo-delimiter LaTeX lint, pinned audited `latex2mathml==3.60.0`, offline MathML, self-contained images, structured bilingual UI, per-chapter HTML, and optional PDF. Prompt images are always before answer images; timeouts and stale artifacts are cleaned fail-loud.
- **Write and supply-chain hardening**: Ingestion wiki/question-bank/index/plan/progress/report output uses guarded atomic replacement, rejects symlinks, and safely detaches hardlinks. Dependency preflight and runtime both verify the exact MathML version rather than accepting “some version is installed.”
- **Cross-Agent PDF adapters**: Codex, Claude Code, and generic Agent Skills use separate capability routes with official source/review commit/license records. Third-party skills are never silently downloaded; restricted-license implementations are linked rather than copied.
- **Quota-friendly artifact modes**: Adds persistent `artifact_mode=chat|visual`. Legacy workspaces default to `chat`, retaining chat + notebook/state without automatically generating chapter HTML/PDF or a cheatsheet PDF. Only explicit `visual` persists visual-textbook generation. One-shot HTML/PDF/print requests may override temporarily without changing state. The Agent neither detects nor guesses subscription plans.
- **Stricter result semantics**: The validator explicitly emits `ready` / `usable_with_gaps` / `blocked`; `ok=true` means only that the structure is runnable. Root skill metadata and release version are aligned.

## V4.0 — 2026-07-12

> See [`docs/history/plans/PLAN-v4.md`](docs/history/plans/PLAN-v4.md) for the complete design and implementation roadmap. The changelog previously jumped directly from V3.0 to V4.1; this section restores the already released V4.0 history and does not represent another release.

- **Language and state layering**: Introduces `locales/zh|en` language packs, a shared i18n layer, and legacy-workspace migration to reduce coupling between control logic and student-facing wording.
- **Lightweight retrieval**: Builds a pure-standard-library BM25 index by chunk, with Chinese/English terminology bridges, top-k, minimum-score abstention, and retrieval traces. Production absorbed the result contract from the early LlamaIndex spike without a heavy runtime dependency.
- **Persisted notebook and mistake book**: Explanations, grading, confusions, and review are persisted by chapter, with deterministic catalog rebuilding for rereading and traceability.
- **Cheatsheet and PDF compilation**: Compiles a pre-exam cheatsheet from persisted sources of truth, with page constraints, HTML/PDF output, and visual inspection.
- **Workspace and distribution slimming**: Adds workspace registration and confirmation, builds a slim ZIP from an explicit runtime manifest, and attaches artifacts in the release flow.

## V3.0

> Turns the V2.1 foundation—chaptered knowledge base + fixed question bank + provenance labels + modular `skills/`—into a complete exam-preparation engine: it handles real exam papers, adapts teaching to time remaining, speaks the learner’s language, and for the first time backs “never make things up” with reproducible evaluation. Everything below was added after V2.1. See [`docs/releases/v3.md`](docs/releases/v3.md) for the release announcement.

### Teaching that adapts to the learner

- **Seven-step walkthrough template**: Key-question walkthroughs follow ① prompt image → ② what the question asks → ③ quantities read from the figure → ④ core formula → ⑤ step-by-step work → ⑥ answer self-check → ⑦ knowledge-point source trace (chapter + wiki + clickable original-page link), with a humanities variant (key sentence/core concept/point-by-point argument). The old 【考点拆解】/【标准答题模板/步骤】 sections are incorporated into ②/④⑤. **Default output stops after the source block**: 【易错点】/【3 分钟速记】/【现在轮到你】 are no longer required stages and appear only when the student asks or has a stored preference (`--pref 收尾块=…`).
- **Fixed source block for every item**: `题目来源：…｜答案来源：…｜<🟢/🟡/⚠️>` applies to explanations and grading feedback. When no material answer exists, the title of ⑤/the explanation block must include ⚠️ AI生成答案，非老师/教材提供.
- **Mode system rebuilt: 3 learning modes × 4 time budgets** (replacing normal/sprint/panic/mock). Learning modes are 零基础从头讲 / 某章起步补弱 / 查缺补漏 and must be established in the first conversation, then stored in `study_state.json.mode`. Time budgets are ≤1天 / 1-3天 / 3-7天 / >7天 and are stored in `time_budget`; they layer on top of mode to determine question cadence. **≤1天 strictly forbids questions** because every question wastes review time. For 1-3天, confusing points are rechecked randomly; 3-7天 uses the knowledge-window system; >7天 tests out-of-window points with matching hard questions. Legacy normal/sprint/panic/mock values supplied to `set --mode` migrate with a warning. Unknown values are preserved with a warning and never silently rewritten.
- **Persistent knowledge window**: `study_state.json.knowledge_window`, managed through `update_progress.py window-add` / `window-set-status` (在窗口/窗口外/已实测). The progress panel adds a “🪟 知识点窗口” section; in-window knowledge is assumed retained, while out-of-window knowledge is checked first.
- **Deterministic difficulty scoring + difficulty × mastery selection**: Selects questions by difficulty and the learner’s mastery state rather than randomly.
- **Scope filters + official selector**: Questions may be restricted to one chapter/source, such as homework only. Any override is announced explicitly (`scripts/select_questions.py` + per-item `source_type` classification).
- **Structured progress state**: `study_state.json` is the sole source of truth; the progress Markdown is its generated view.
- **Walkthrough-template preferences**: Variant choices live in `study_state.json` preferences, separate from mode, and appear in the progress panel’s ⚙️ preference section.

### Exam-paper and image pipeline

- **Past-paper pipeline**: Recognizes real exam papers (`source_type=exam`), keeps answer keys out of prompts, and guarantees zero silent per-page loss (`ai_review_manifest` identifies sections requiring human takeover).
- **Homework/answer ingestion**: Automatically pairs separate question and answer PDFs, or inline Solutions.
- **Question-type recognition + unknown-type warning**: Unclassifiable questions are never silently handed to students.
- **Visual-first presentation**: A figure-dependent question is not asked without its figure. A generic dual visual index, possible-omission recall net, and official visual tools recover figures that would otherwise be missed, strengthening both wiki illustrations and visual-question recall.

### Multiple languages

- **Reply-language state layer**: 中文 / English / 双语, normalized by `--language` including aliases and persisted across conversations.
- **English entry surfaces**: `SKILL.en.md` / `prompts/web_prompt.en.md` / `AGENTS.md` with derived rendering and discoverability alignment. **English by default**—switch to Chinese only when the student opens in Chinese; script-layer empty-value fallback retains Chinese for legacy-workspace compatibility.
- **Single-language purity**: Student-facing output never mixes Chinese and English. An EN canonical vocabulary and bidirectional purity lint enforce this. The control layer contains zero CJK, and runtime surfaces contain no phase codes.

### Hallucination benchmark (first complete system)

- **Generic three-arm × three-model matrix runner** (configuration-driven + fixture courses + end-to-end `--mock`): closed-book / raw files + generic Agent / this skill, across Opus 4.8, Sonnet 4.6, and Haiku 4.5. The matrix pipeline was hardened against ledger deadlock, crashed trailing-fragment recovery, fingerprint blind spots, and inflated cost reporting.
- **Material-anchored gold labels**: Questions come from Yale PSYC 110 lecture transcripts and MIT 6.006 lecture notes/problem sets, with a separate official MIT 6.006 exam paper for comparison. Every answer is anchored verbatim to the source materials, with out-of-scope probes for which the materials contain no answer at all.
- **Grading calibration**: Hardened numerical grading + generic kappa calibration (human κ=0.833 and 0.875; every disagreement was an overly strict grade, so the reported numbers are conservative) + cross-family warning + near-miss suggestions. Crashed/ungraded items were regraded by majority vote among three independent judges each.
- **Results**: Accuracy on material-specific details rose from 11%–13% to 100% across the three PSYC models and to 91% on 6.006. Honest abstention on out-of-scope questions rose from 60%–90% to 100%. Per-question cost was lower than the raw-file Agent at the same accuracy—about 15% lower for PSYC and 5% for 6.006. Because the skill lazily loads by chapter rather than injecting the entire book, long-session token use is designed to be approximately 90% lower. See `benchmark/REPORT.md` / `REPORT.en.md` for the productized bilingual report with release guards and SVG charts.
- Behavior-smoke wiring: `teaching_template`, `time_budget_no_questions` (no questions for ≤1天), and `knowledge_window_recheck` (ask/test out-of-window points); T4 long-horizon drift adds a mode-drift scenario, and real-Agent smoke through `--llm` becomes functional (opt-in).

### Polish and engineering

- **Four-part pre-exam cheatsheet**: 必背 → 例题 → 例题解答 → 要点解释.
- **Read-only workspace health check** `exam-audit`: Reads sources of truth directly; architecture convergence removes dead templates, fixes the init ladder bug, and cleans stale wording.
- Unified run ledger with automatic live-smoke/rejudge records; 1,000+ unit tests plus Ubuntu/Windows × Python 3.8/3.12 CI. At that time the repository included the experimental `spike/llamaindex_rag` standalone LlamaIndex RAG experiment. Its contract was later absorbed into the V4.0 production retriever, and the experiment directory was retired in a later release.

## V2.1

- **Knowledge-provenance transparency protocol**: 🟢 来自资料 / 🟡 AI补充，可能与你老师讲的不完全一致 / ⚠️ AI生成答案，非老师/教材提供.
- **Beginner-focused key-question walkthrough mode**: For students who have learned almost nothing, teach each item through 【考点拆解】+【标准答题模板/步骤】+【易错点】+【3分钟速记】.
- **Deterministic diagram-question protocol** (`type: "diagram"`): Actually run the standard algorithm to obtain the structure before rendering it; never sketch from memory.
- **Six question types**: `choice / subjective / diagram / fill_blank / true_false / code`.
- **Engineering refactor** ([PR #11](https://github.com/ZeKaiNie/universal-examprep-skill/pull/11), with no change to existing behavior):
  - modular skill collection in `skills/` (`exam-cram` main coordinator + sub-skills) plus root `AGENTS.md` fallback;
  - bilingual control layer (English control sections + Simplified Chinese student surface) and canonical provenance labels;
  - pure-standard-library workspace validator `scripts/validate_workspace.py`;
  - architecture documentation under `docs/` (skill-architecture / agent-portability / language-policy / file-format);
  - expanded tests covering ingestion, workspace validation, skill structure, language policy, bilingual control layers, and collection self-containment, plus an Ubuntu/Windows × Python 3.8/3.12 CI matrix.
- **Hallucination-benchmark fairness improvements**: Adds the “raw files + generic Agent” control, cost measurements, and human kappa calibration.
- **confusion-tracker moved into `skills/`** (`skills/confusion-tracker/SKILL.md`): Confusion tracking is no longer an external dependency outside `skills/`. The leftover root `confusion-tracker/` compatibility directory was subsequently removed; migration notes remain only in this CHANGELOG rather than in a permanent root directory.

## V2.0

- **Structured LLM Wiki directory + lazy loading**: Physically slices content by chapter under `references/wiki/` and reads only the current chapter according to progress, substantially reducing token usage.
- **One-command, zero-friction cold-start ingestion**: The student provides only a syllabus/past papers; AI parses in the background, assembles JSON, slices chapters, and initializes progress, with **no hand-written JSON required**.
- **Automatic fallback without Python**: If scripts are unavailable, seamlessly switches to “manual write mode,” in which AI directly lays out the workspace.
- **Standard `quiz_bank.json` selection**: Quizzes draw and grade only from the question bank, eliminating improvised AI questions.
- **Quiz escape route**: View a hint, or skip after two consecutive wrong answers and archive the mistake.
- **Concept-confusion tracking** (`confusion-tracker`): Automatically captures “why / how is this derived?” follow-ups into a pre-exam blind-spot list.
- **Runtime safety and progress protection**: Safe filename filtering, path-traversal/tampering protection, automatic backup before progress overwrite, and forced UTF-8 output.
- **Unit tests + GitHub Actions CI**.
