---
name: exam-help
description: >
  备考教练的速查卡：一屏列出工作流四步、3 学习模式 × 4 时间宽裕度、工作区文件约定、6 大题型、防幻觉与来源标注规则，
  以及各子技能何时用。当用户问「这个技能怎么用 / 有哪些模式 / 文件都是干嘛的 / 支持什么题型」时使用。
license: MIT
---

# exam-help — quick-reference card

## Purpose
Render a single-screen reference card for the exam-cram skill suite: the four-step workflow, 3 learning modes and 4 time tiers, workspace file conventions, six quiz types, anti-hallucination and provenance rules, and when to use each subskill. Read-only.

## Activation
Activate when the user asks how this skill works, what modes exist, what each workspace file is for, or which quiz types are supported (e.g. 「这个技能怎么用 / 有哪些模式 / 文件都是干嘛的 / 支持什么题型」).

## Inputs
None. Take no files, no arguments, no workspace state. Emit the static card from the language packs (see Language packs below).

## Workflow
1. Print the reference card from the language packs (see Language packs below). If a workspace with a persisted `study_state.json` `language` is in play, follow it (`中文` → zh pack verbatim, `English` → en pack, `双语` → bilingual composition per exam-cram's dispatch rule); otherwise honor an explicit ad-hoc language request. exam-help itself reads no state — the caller passes the language.
2. Do not read, scan, or load any workspace files (`references/wiki/`, `references/quiz_bank.json`, `study_progress.md`, `study_plan.md`).
3. Do not run `scripts/ingest.py` or any subskill.
4. End. Do not start tutoring, quizzing, ingesting, or grading.

## Output Contract
- Output exactly one help card; perform no further action.
- Mutate no state: write/create/delete no files; do not touch `study_progress.md` or any workspace artifact.
- Do not teach, quiz, grade, or initialize a workspace.
- Preserve provenance markers verbatim where shown: 🟢 来自资料 / 🟡 AI补充，可能与你老师讲的不完全一致 / ⚠️ AI生成答案，非老师/教材提供.
- Student-facing output defaults to English (Simplified Chinese if the student opened in Chinese); a persisted `study_state.json` `language` (`中文`/`English`/`双语`) switches the card's rendering per exam-cram's dispatch rule, and an explicit ad-hoc request is honored when no workspace is in play.

## Language packs
Student-visible wording for this skill lives in per-language packs — load the one matching `study_state.json.language` BEFORE emitting any student-visible output:
- `zh` → [`../../locales/zh/skills/exam-help.md`](../../locales/zh/skills/exam-help.md)
- `en` → [`../../locales/en/skills/exam-help.md`](../../locales/en/skills/exam-help.md)
- `bilingual` → compose from the zh pack with a `> EN:` mirror line per block (rules in [`../../docs/language-policy.md`](../../docs/language-policy.md))
Unset language → this is the first conversation: the merged first-ask (mode × time budget × language) decides it; default en unless the student opened in Chinese.

## Boundaries
This card is read-only and executes no teaching action. To start reviewing, tell `exam-cram` your subject and remaining time.
