---
name: exam-help
description: >
  备考教练的一屏速查卡：工作流、3×4 学习选择、产物偏好、工作区文件、6 大题型、来源规则与子技能路由。
  用户问怎么用、有哪些模式、文件用途或支持题型时使用。
license: MIT
---

# exam-help — quick-reference card

## Purpose

Render one read-only card covering the validated workflow, three modes, four time tiers, `chat|visual`, workspace truth/views, six quiz types, provenance, and subskill routing.

## Activation

Use only when the student asks how the suite works, which modes/types exist, or what workspace files mean.

## Inputs

No files, arguments, or state reads. The caller supplies the selected language.

## Workflow

1. Emit the matching static language-pack card: persisted `zh`, `en`, or `bilingual`, otherwise an explicit ad-hoc request.
2. Read no wiki, bank, plan, progress, or workspace file; run no script/subskill.
3. Stop without tutoring, quizzing, ingesting, grading, or initialization.

The card must say:

- `artifact_mode` is never a fourth first-contact question or inferred from subscription. Missing/legacy/unknown means `chat`. Both modes require the validated current `profile=full` typed guide; `chat` stops without PDF, while standing `visual` additionally requires render, receipt hashes, every-page QA, and `artifact_ready=ready`. A one-shot artifact leaves stored state unchanged. Chat final review may stay conversational; cheat-sheet PDF still needs visual or an explicit PDF/print request.
- `ingest_course.py` is the normal build entry; exit 10 is process success with blocked readiness, and teaching remains forbidden. `.ingest/` is build/review truth, `study_state.json` progress truth, and Markdown is generated view.
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
