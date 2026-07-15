---
name: exam-review
description: >
  考前复盘已记录错题与概念疑难：重做原题、复述疑难、更新已订正/已回顾/待回顾状态，并形成最后扫雷清单。
  进入最终复习阶段或用户要求复盘、查漏补缺时使用。
license: MIT
---

# exam-review — mistake and confusion review

## Purpose

Clear recorded mistakes/confusions before the exam. Replay only existing records; teach no new chapter and invent no question.

## Activation

Use at final review or when the student explicitly asks to replay mistakes/find gaps.

## Inputs

- `study_state.json`'s `mistake_archive` and `confusion_log` when state exists; otherwise generated 「❌ 错题档案」 and confusion compatibility rows.
- `references/quiz_bank.json`, used to fetch each recorded mistake by exact ID.

## Workflow

1. **Replay recorded mistakes.** Reload state (or `update_progress.py show`), fetch each exact bank item, and ask it again. Never add an unrecorded/bank-external question.

   For `requires_assets=true` or `maybe_requires_assets=true`, before asking, explaining, hinting, or solving, render every question-side `question_context` / `figure` / `diagram` / `table` asset, labelled `题面图` or `Question-side asset`. Only later may solution/review show `答案图` / `Answer-side asset`. A path is not an image. Missing/unreadable or UI-unrenderable prompt assets cause a fail-closed skip; `stub` / `page_reference` also require the original prompt page first. See [`exam-quiz`](../exam-quiz/SKILL.md) and [`docs/file-format.md`](../../docs/file-format.md) §4.

2. **Update mistakes.** Correct replay → `已订正`; still wrong → explain from the stored explanation and retain it.

3. **Replay confusions.** Reload `confusion_log`; ask the student to restate what/why/how. Correct restatement → `已回顾`; vague → explain once and retain `待回顾`.

4. **Persist the open list first.** Compile unresolved mistakes plus `待回顾` confusions for the final sprint/`exam-cheatsheet`. Pipe each conclusion/list to `python "${CLAUDE_SKILL_DIR}/scripts/notebook.py" --workspace <ws> add-entry --chapter <ch> --type review --id <slug> --title <gist>`; same IDs replace and rebuild the index. Then send a digest and language-pack notebook link. If writing fails, say so and give the full list in chat; file-less clients use chat/text breakpoints.

5. **Persist row status.** If `study_state.json` is absent and Python works, run `update_progress.py --workspace <ws> init` first; only when Python truly cannot run may Markdown rows be changed in place. Write results via `update_progress.py set-mistake-status` / `set-confusion-status`, and genuinely new rows via `add-mistake` / `add-confusion`. A nonzero Python command is a fail-loud error, not permission to hand-edit. Never leave a mastered row stale or overwrite another skill's writes.

## Output Contract

- Produce one 「还没拿下的清单」 with current `已订正` / `已回顾` / `待回顾` statuses, notebook link, and refreshed panel.
- Persist conclusions before the digest and persist every status via `update_progress.py set-mistake-status` / `set-confusion-status`.
- Student prose is English by default, Simplified Chinese for a Chinese opening, or explicit bilingual blocks.

## Language packs

- `中文` → [`../../locales/zh/skills/exam-review.md`](../../locales/zh/skills/exam-review.md)
- `English` → [`../../locales/en/skills/exam-review.md`](../../locales/en/skills/exam-review.md)
- `双语` → compose both blockwise, zh then `> EN:`, under [`docs/language-policy.md`](../../docs/language-policy.md)

Display aliases are normalized to `zh`, `en`, or `bilingual`.

## Boundaries

- `study_state.json` is the source of truth; mutate rows only via `update_progress.py`. Mark replay results via `update_progress.py set-mistake-status` / `set-confusion-status`; `set-check` alone is not a substitute.
- Default question scope is mixed. A restricted scope excludes/counts missing `source_type`; announce any one-turn override first: 「⚠️ 临时覆盖你的 <scope> 范围偏好」 / `⚠️ Temporarily overriding your <scope> scope preference`. Use `scripts/select_questions.py`.
- Share confusion rows with `confusion-tracker`; replay only recorded bank items and never erase concurrent writes.
