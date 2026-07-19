---
name: exam-help
description: >
  备考教练的一屏速查卡：工作流、3×4 学习选择、产物偏好、工作区文件、6 大题型、来源规则与子技能路由。
  用户问怎么用、有哪些模式、文件用途或支持题型时使用。
license: MIT
---

# exam-help — quick-reference card

## Purpose

Render one read-only card covering the validated workflow, three modes, four time tiers, `lightweight|full`, `chat|visual`, workspace truth/views, six quiz types, provenance, and subskill routing.

## Activation

Use only when the student asks how the suite works, which modes/types exist, or what workspace files mean.

## Inputs

No files, arguments, or state reads. The caller supplies the selected language.

## Workflow

1. Emit the matching static language-pack card: persisted `zh`, `en`, or `bilingual`, otherwise an explicit ad-hoc request.
2. Read no wiki, bank, plan, progress, or workspace file; run no script/subskill.
3. Stop without tutoring, quizzing, ingesting, grading, or initialization.

The card must say:

- `processing_mode=lightweight` is the default/recommended startup choice. It
  inventories names, then visually processes only the current-phase PDF pages or
  definitely single-frame PNG/JPEG/BMP (maximum eight primary pages and one active
  batch). Overview contact sheets group at most four pages at roughly 768 px/tile;
  page/prompt/dependency detail and target-answer-only solution calls use
  source-qualified locations and canonical PNG evidence under `.lightweight/assets/`.
  Bind exact external answer pages with `register-answer-dependency`; dependency
  pages are locator/detail only. Figure prompt/answer crops stay distinct. An
  unfinished batch can close only through receipt-backed `abandon --reason`; taught
  progress cannot be abandoned, while `replace-taught --reason` preserves it as
  superseded history and plans an exact-slice successor. It keeps the page-batch /
  progress state machines and creates no Study Guide/PDF. `processing_mode=full` is
  explicit opt-in and opens the complete ingestion/review route. Input-token savings
  never shorten the teaching explanation.
- `artifact_mode` is independent from processing intensity and never inferred from
  subscription. Missing/legacy/unknown means `chat`. In full mode, `chat` stops
  without PDF while standing `visual` additionally requires typed Guide, render,
  receipt hashes, every-page QA, and `artifact_ready=ready`. A one-shot artifact
  leaves stored state unchanged. In lightweight, a saved `visual` preference is
  dormant and effective output remains `chat`; it never builds a Study Guide.
- `answer_explanation_mode` is also independent. Its stored-schema fallback is
  `ordinary`, and every full-Guide item still gets a detailed beginner-first
  explanation. At full-v2 Guide entry, a verified host-native child with a fresh
  independent context plus exact single-item input/tool restrictions makes
  `isolated` the default unless the learner opted out; tell the learner once about
  extra host quota/time, with no second API key or external upload. Missing or
  incomplete capability stays `ordinary` and is stated honestly. A separately
  billed external Provider is explicit-request-only and retains no-upload planning,
  current pricing/privacy disclosure, and exact-plan upload consent. A model name,
  subscription, key, `full`, or `visual` alone proves neither route.
- `ingest_course.py` is the full-mode build entry; exit 10 is process success with
  blocked readiness, and teaching remains forbidden. `.ingest/` is full-mode
  build/review truth, `.lightweight/session.json` is on-demand page-batch truth,
  `study_state.json` is progress truth, and Markdown is a generated view.
- Lightweight completion uses current-phase taught-batch/notebook events and skips
  typed Guide/full-build evidence; superseded predecessors/events remain history but
  leave the current denominator. `verified` needs revision-bound checkpoints from an
  unchanged bank that pre-existed lightweight initialization; startup captures only
  its immutable stat baseline. Routine health checks use metadata/physical identity,
  not stream hashes; exact hashes occur at transitions/completion or explicit
  `status --verify-live`. Older taught history is `unchecked_historical` until that
  phase is resumed.
- MinerU, Docling, and LangGraph are named-request-only and remote/cloud-only. Never
  download, install, probe, import, or execute them locally.
- Provenance is exact: 🟢 来自资料 / 🟡 AI补充，可能与你老师讲的不完全一致 / ⚠️ AI生成答案，非老师/教材提供.

## Output Contract

Output exactly one help card and mutate nothing. Student prose is English by default, Simplified Chinese for a Chinese opening, or explicit bilingual composition.

## Language packs

- `中文` → [`../../locales/zh/skills/exam-help.md`](../../locales/zh/skills/exam-help.md)
- `English` → [`../../locales/en/skills/exam-help.md`](../../locales/en/skills/exam-help.md)
- `双语` → compose both blockwise, zh then `> EN:`, under [`docs/language-policy.md`](../../docs/language-policy.md)

Display aliases are normalized to `zh`, `en`, or `bilingual`.

## Boundaries

This card is read-only. Route actual review to `exam-cram`.
