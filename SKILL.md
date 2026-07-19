---
name: universal-exam-cram-coach
description: "帮助学生在临考前进行结构化极速复习：解析课程资料/大纲/重点，按章节生成 wiki 知识库与标准题库，组织针对性刷题与判分，并记录复习进度和错题。当用户即将考试、需要快速复习计划、练习题、错题复盘或考前小抄时使用（关键词：期末/备考/复习/刷题/划重点/错题；exam, cram, study plan, quiz, review）。不适用于长期学习规划、与考试无关的写作或编程任务。"
license: MIT
metadata:
  version: "4.3"
  author: ZeKaiNie
---

# Universal Exam Cram Coach — Root Router

This language-neutral router dispatches last-minute exam prep to the chapter-wiki, bank-only, persistent control layer and its wording packs; it is not a duplicate manual.

## Language dispatch

Read the canonical, language-neutral `study_state.json.language` code and load the matching compatibility entry plus its per-skill wording pack BEFORE emitting any student-visible output:

- `zh` (display choice `中文`) → [`locales/zh/SKILL.md`](locales/zh/SKILL.md) plus the selected sub-skill's zh wording pack under `locales/zh/skills/`
- `en` (display choice `English`) → [`locales/en/SKILL.md`](locales/en/SKILL.md) plus the selected sub-skill's en wording pack under `locales/en/skills/`
- `bilingual` (display choice `双语`) → compose the zh and en wording block by block, with zh first and a `> EN:` mirror for each block (composition rules in [`docs/language-policy.md`](docs/language-policy.md))

`中文`, `English`, and `双语` remain accepted user-facing input aliases. On first contact one combined ask sets mode, budget, and language, then show the independent material-processing choice `轻量按需（推荐） / 完整建库`; `exam_start.py confirm` persists them with the exact workspace/materials receipt. Missing, urgent, accepted-default, and legacy processing choices mean `lightweight`; only explicit `full` opens complete ingestion. A later reconfirm with no processing flag preserves an existing canonical choice. Later `update_progress.py set --language` applies next turn. Default English unless the student opened in Chinese; bilingual is explicit-only.

## Control layer (behavior)

Behavior lives in [`skills/exam-cram/SKILL.md`](skills/exam-cram/SKILL.md) and these subskills:

| Sub-skill | Role |
|---|---|
| [`exam-ingest`](skills/exam-ingest/SKILL.md) | Build/validate workspace |
| [`exam-tutor`](skills/exam-tutor/SKILL.md) | Lazy chapter teaching |
| [`exam-study-guide`](skills/exam-study-guide/SKILL.md) | Typed guide and visual artifact gate |
| [`exam-quiz`](skills/exam-quiz/SKILL.md) | Bank-only selection/grading |
| [`exam-review`](skills/exam-review/SKILL.md) | Replay mistakes/confusions |
| [`exam-cheatsheet`](skills/exam-cheatsheet/SKILL.md) | Final handout |
| [`exam-audit`](skills/exam-audit/SKILL.md) | Read-only workspace health check |
| [`exam-help`](skills/exam-help/SKILL.md) | Quick reference |
| [`confusion-tracker`](skills/confusion-tracker/SKILL.md) | Concept-confusion tracking |

Generic-agent fallback: [`AGENTS.md`](AGENTS.md).

## Install & run essentials

- Under [`scripts/`](scripts/), use `exam_start.py status`, then `exam_start.py confirm --course <name> --materials <dir> --workspace <ws> --mode <mode> --time-budget <tier> --language <lang> --processing-mode <lightweight|full>`. It writes the confirmation/state/runtime receipt. Default `lightweight_session.py` inventories names and processes only current-phase PDF pages or definitely single-frame PNG/JPEG/BMP sources through host-native vision: at most eight primary pages and one active batch. A single page uses no contact sheet; multi-page overview sheets partition primary pages in groups of at most four at roughly 768 px per tile. New schema-3 visual receipts require the generic component token strategy and enumerate stable teaching-item IDs plus generic `text|figure|mixed` prompt/answer components. A cross-page item repeats on each page that supplies one of its prompt components, with exact page↔component coverage. Detail calls may combine only same-target prompt components, solution calls only same-target answer components, and every component crop receives a separate semantic review that detects exactly its declared target/context IDs with no unrelated content or student attempt. Only prompt components may be context-only; every answer component contains its target. Page answer provenance prevents student attempts or unknown pages from masquerading as official solutions, and every registered official-solution page must contribute an answer component. Additive `register-answer-dependency` binds exact answer-locator pages; planned batches may auditably replace/narrow or remove a binding with `set-answer-dependency` / `remove-answer-dependency`. All canonical visible evidence is PNG under `.lightweight/assets/`, with exact model-input receipts and hash/magic/dimension checks. Schema-2 visual receipts and the legacy figure-only token strategy remain read-only history; any legacy-strategy active attempt is restricted to status or auditable abandon and cannot silently become schema 3. An unfinished planned/visual-ready batch may close only with receipt-backed `abandon --reason`; `replace-taught --reason` preserves a taught predecessor/event as superseded history, revalidates its dependency revisions, and plans an exact-slice successor with the same dependency pages. After an unabridged walkthrough, `mark-taught --taught-item-ids <exact IDs>` binds `notebook/chNN.md#anchor`, distinguishes inspected pages from taught items, and recoverably publishes `phase_evidence.lightweight_batches`; only current unsuperseded attempts enter the completion denominator. Routine status is generation-stable and read-only; validation checks metadata plus physical identity only. Exact hashes are reserved for state transitions, completion, or explicit `status --verify-live`. Lightweight `verified` additionally requires two revision-bound checkpoints, including one pass, from the immutable stat-only baseline of a quiz bank that pre-existed initialization. It runs no full ingestion, Study Guide, or PDF. Explicit `full` opens `ingest_course.py`; the orchestrator and lower-level workspace builder/compiler all enforce the same exact-pair/runtime/choices/full gate. Exit 10 routes to typed `ingest_review.py`. `update_progress.py` owns `study_state.json`; `study_progress.md` is generated. Official selectors are `select_questions.py` / `select_hard_questions.py`.
- Workspace file contract (wiki / quiz bank / state / asset metadata): [`docs/file-format.md`](docs/file-format.md).
- Language policy (single-language purity, EN canonical vocabulary, persisted canonical values): [`docs/language-policy.md`](docs/language-policy.md).
- Host loading: [`docs/agent-portability.md`](docs/agent-portability.md).
- PDF capabilities differ by host; use the audited, no-silent-download routing table in [`docs/pdf-capability-adapters.md`](docs/pdf-capability-adapters.md).
- Missing/legacy `artifact_mode` is `chat`; explicit standing `visual` or a one-shot chapter artifact invokes `exam-study-guide`, while cheat-sheet PDF uses `exam-cheatsheet`. An ambiguous PDF request asks which once. Never infer subscription. Persist with `update_progress.py set --artifact-mode chat|visual`.
- `processing_mode` and `artifact_mode` are independent. Lightweight never generates a Study Guide; a saved `visual` preference remains dormant and effective output stays `chat` until explicit `full`. Full does not imply a PDF. MinerU, Docling, and LangGraph are explicit-named-request, remote/cloud-host-only capabilities and are never probed, downloaded, installed, imported, executed, or accepted as callable local runners.
- `preferences.interaction_style` stores only `batch|step_by_step`. A stored step-by-step choice is effective only in `full` with `no_questions=false`; otherwise it is retained but dormant and effective cadence is `batch`. In effective step mode, select the first pending `teaching_examples.json` item from one locked snapshot and persist it through the marker-bound `record-taught-example` path. Existing unbound teaching IDs are valid batch history; a bound ID carries exact notebook-block and manifest-item hashes that remain live-validated after cadence changes. Guide publication preserves valid bound blocks and rejects stale or unbound markers. Every teaching-baseline ID must still have a current teaching-manifest snapshot; a quiz-only copy is insufficient.
- In an ingestion-v2 structured workspace, `answer_explanation_mode` is independent from processing/artifact mode. Its stored-schema fallback is `ordinary`, but full-v2 Guide entry must first perform a native-child capability handshake. When the host can prove a fresh independent child context per item and can restrict its input and tools to that exact item, default to `isolated` unless the user opted out; persist the mode, notify once about extra host quota/time, and require no second API key or external-upload consent. Otherwise stay `ordinary` and explain the limitation. Both routes run `study_guide_author.py prepare`, fill fixed annotations, require one detailed beginner-first explanation per item, persist notebooks, compile, create/attach/verify claims, and import the canonical full Guide. In `ordinary`, the annotation contains the explanation with `ai_supplement` provenance and claims no isolation. In `isolated`, each fresh/stateless tool-disabled invocation sees only the fixed question, official answer when present, target language, and target-scoped assets; it returns `answer_explanation` plus non-rendered `coverage` and is imported with a separate host-owned receipt. A separately billed external Provider is an explicit-user-request fallback only and retains no-upload planning plus exact-plan pricing/privacy/upload consent. A model family, subscription, API key, `full`, or `visual` alone never proves native isolation. Target-scoped means `target_item_only`, or prompt-only `target_with_required_context` with exact sorted `required_context_ids`; answer assets remain target-only. Packet, annotations, notebook bindings, manifest, rendering and QA all bind the chosen mode. A language/mode/fact/asset change makes the chain stale; only `isolated` reruns the per-item receipt chain. New v2 Guides omit generic self-check panels. A hand-written complete v2 Guide draft is a no-Python-only, unverified fallback. Ingestion-v1 remains read-only and cannot claim current v2 gates.
