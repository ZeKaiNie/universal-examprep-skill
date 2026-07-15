# Universal Exam Cram Coach — English Compatibility Entry

This is a compact compatibility entry, not a second workflow manual. The behavioral source of truth is [`skills/exam-cram/SKILL.md`](../../skills/exam-cram/SKILL.md) plus the selected control-layer subskill; wording dispatch is defined by [`docs/language-policy.md`](../../docs/language-policy.md).

## Dispatch

Load the orchestrator, then exactly one needed subskill: [`exam-ingest`](../../skills/exam-ingest/SKILL.md), [`exam-tutor`](../../skills/exam-tutor/SKILL.md), [`exam-study-guide`](../../skills/exam-study-guide/SKILL.md), [`exam-quiz`](../../skills/exam-quiz/SKILL.md), [`exam-review`](../../skills/exam-review/SKILL.md), [`exam-cheatsheet`](../../skills/exam-cheatsheet/SKILL.md), [`exam-audit`](../../skills/exam-audit/SKILL.md), [`exam-help`](../../skills/exam-help/SKILL.md), or [`confusion-tracker`](../../skills/confusion-tracker/SKILL.md). Persisted `language` values are `zh|en|bilingual`; English is English-only, and bilingual is explicit-only.

## Compatibility safety pins

- Before local work, run `update_progress.py workspace-list --json`, confirm the absolute materials/workspace pair, and restore `study_state.json` first. It is progress truth; `study_progress.md` is generated. If state is absent and Python works, run `update_progress.py --workspace <ws> init` before `set`, `set-check`, or other writes. Never use the repository/cwd as the workspace.
- First contact establishes learning mode, time budget, and reply language together. Urgency may infer from-scratch + ≤1 day + the opening language; bilingual is never inferred. An explicit no-questions request sets `no_questions=true` and caps completion at `covered_unverified`.
- Quiz and grading use only `references/quiz_bank.json`. Use `select_questions.py` normally and `select_hard_questions.py --chapter <current>` for a checkpoint. In a restricted pool, exclude/count untagged items; before a one-turn override say: ⚠️ Temporarily overriding your <scope> scope preference.
- For `requires_assets=true` or `maybe_requires_assets=true`, Before asking, explaining, hinting, or solving, render every Question-side asset. A path is not an image. Show an Answer-side asset only later; if rendering fails, skip the item. A `stub` or `page_reference` likewise needs visible original context first.
- Persist substantive work through `notebook.py`; report a failed write and provide the full content in chat. Missing/unknown `artifact_mode` means `chat`; only explicit `visual` or a one-shot request enters typed Guide/render/QA, without silent installs or subscription guesses.

## Canonical English wording

- 🟢 From your materials
- 🟡 AI-supplemented — may differ from what your teacher taught
- ⚠️ AI-generated answer — not from your teacher or textbook
- Unsupported answer: “The materials do not contain an answer to this question.”
- Key-question blocks: ① Question figure → ② What's being asked → ③ What to read off the figure → ④ Core formula → ⑤ Step-by-step solution → ⑥ Answer self-check → ⑦ Source trace.
- Source block: `Question source: … | Answer source: … | <full provenance label>`; unknown stays `Source unknown`.

Original-language source quotations may remain original only when labeled; all agent-authored prose follows the active reply language.
