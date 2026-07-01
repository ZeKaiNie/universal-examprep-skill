---
name: exam-review
description: >
  考前易错扫雷与疑难复盘：读取 study_progress.md 的错题档案与「概念疑难点记录」，重新调取原题做扫雷
  测试，逐条请学生复述疑难点并更新状态（待回顾/已回顾）。与 confusion-tracker 协同。当进入最终复习
  阶段、或用户要求复盘错题/查漏补缺时使用。
license: MIT
---

# exam-review — 错题与疑难复盘

## Purpose
Clear the backlog of recorded mistakes and confusions before the exam. Replay only previously recorded items; teach no new chapters and add no new questions.

## Activation
Run when the student enters the final review stage, or asks to 复盘错题 / 查漏补缺 / 考前过一遍 (replay mistakes / find gaps / final pass).

## Inputs
- `study_progress.md`: the ❌ 错题档案 (mistake records) and 💡 概念疑难点记录 (confusion-tracker entries).
- `references/quiz_bank.json`: source items, keyed by id, re-fetched for each recorded mistake.

## Workflow
1. Reload mistake records from `study_progress.md`. For each recorded mistake, read its item id, fetch that exact item from `references/quiz_bank.json` by id, and have the student redo it. Replay only items already recorded; never invent or add new questions.
   - **Visual-first asset gate (fail-closed)** — if the re-fetched item has `requires_assets=true` or `maybe_requires_assets=true`, apply the same contract as [`exam-quiz`](../exam-quiz/SKILL.md) and [`docs/file-format.md`](../../docs/file-format.md) §4: before asking, explaining, hinting, or solving, render/show every question-side asset (`question_context` / `figure` / `diagram` / `table`) inline and label it `题面图 / question-side asset`. Do not show answer-side assets (`answer_context` / `worked_solution`) before those prompt assets; show them only during solution/review and label them `答案图 / answer-side asset`. If a question-side asset is missing/unreadable, the UI cannot render it, or you only have a non-rendering path (including malformed slash-prefixed Windows drive-letter Markdown), **skip the replay** and say it is blocked for lack of visible prompt context. Treat `stub`/`page_reference` text as non-standalone: surface the prompt asset or original page first, else skip it rather than replaying an item the student cannot see.
2. If the student answers a replayed item correctly, mark it 已订正 in the record. If still wrong, re-explain using the item's `explanation` field and keep it in the record.
3. Reload the confusion-tracker entries from `study_progress.md`. Read each entry aloud and have the student restate it in their own words (what it is, why it works that way).
4. If the student restates an entry correctly, set its status to 已回顾. If still vague, re-explain once and keep its status 待回顾.
5. Compile the open list: items still marked wrong plus entries still 待回顾. Hand this list to the final sprint and to `exam-cheatsheet` as priority input.
6. Write results back to `study_progress.md`: update each existing item/entry status **in place** (已订正 / 已回顾 / 待回顾); append only genuinely new records. Never leave a mastered item as a stale wrong/待回顾 row. Do not overwrite other skills' writes.

## Output Contract
- Produce one "还没拿下的清单" (not-yet-mastered list): recorded mistakes plus confusion entries, each with its current status (已订正 / 已回顾 / 待回顾). End with a refreshed progress panel.
- Update each mistake/confusion status **in place** in its existing `study_progress.md` row (已订正 / 已回顾 / 待回顾); append only genuinely new records. Never leave a mastered item still marked wrong/待回顾. Then return control to `exam-cram`.
- Student-facing output defaults to Simplified Chinese unless the user asks otherwise. (See [`docs/language-policy.md`](../../docs/language-policy.md).)

## Student-facing Output
- **错题重做**：这道你上次错在「……」。同一道题再做一遍——这次盯住 ……。做对了我就把它从错题本划掉（标「已订正」）。
- **疑难复述**：你之前卡在「……」这个概念。用你自己的话讲一遍：它是什么、为什么这样。讲清楚 → 标「已回顾」；还含糊 → 我再讲一次，保留「待回顾」。
- **缺口小结**：还没拿下的——错题：……；疑难点：……。这几条留到 `exam-cheatsheet` 重点列。

## Boundaries
- Replay only recorded items. Never add a question that is not already in the records or the quiz bank.
- Share `study_progress.md` with `confusion-tracker`: append confusion entries and update status in place; never overwrite another skill's writes.
