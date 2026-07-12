---
name: exam-review
description: >
  考前易错扫雷与疑难复盘：读取 study_progress.md 的错题档案与「概念疑难点记录」，重新调取原题做扫雷
  测试，逐条请学生复述疑难点并更新状态（待回顾/已回顾）。与 confusion-tracker 协同。当进入最终复习
  阶段、或用户要求复盘错题/查漏补缺时使用。
license: MIT
---

# exam-review — mistake & confusion review

## Purpose
Clear the backlog of recorded mistakes and confusions before the exam. Replay only previously recorded items; teach no new chapters and add no new questions.

## Activation
Run when the student enters the final review stage, or asks to 「复盘错题 / 查漏补缺 / 考前过一遍」 (replay mistakes / find gaps / final pass).

## Inputs
- Review backlog source: `study_state.json` (`mistake_archive` / `confusion_log`) when it exists — the structured-state source of truth; otherwise `study_progress.md`'s ❌ 错题档案 and 💡 概念疑难点记录 (the md is a generated view that may be stale).
- `references/quiz_bank.json`: source items, keyed by id, re-fetched for each recorded mistake.

## Workflow
1. Reload mistake records — from `study_state.json`'s `mistake_archive` when it exists (read it or `update_progress.py show`; the generated md may be stale/hand-edited), else from `study_progress.md`. For each recorded mistake, read its item id, fetch that exact item from `references/quiz_bank.json` by id, and have the student redo it. Replay only items already recorded; never invent or add new questions.
   - **Visual-first asset gate (fail-closed)** — if the re-fetched item has `requires_assets=true` or `maybe_requires_assets=true`, apply the same contract as [`exam-quiz`](../exam-quiz/SKILL.md) and [`docs/file-format.md`](../../docs/file-format.md) §4: before asking, explaining, hinting, or solving, render/show every question-side asset (`question_context` / `figure` / `diagram` / `table`) inline and label it per §4 in the active reply language (`中文`/`双语` `题面图`, `English` `Question-side asset`). Do not show answer-side assets (`answer_context` / `worked_solution`) before those prompt assets; show them only during solution/review and label them per §4 in the active reply language (`中文`/`双语` `答案图`, `English` `Answer-side asset`). If a question-side asset is missing/unreadable, the UI cannot render it, or you only have a non-rendering path (including malformed slash-prefixed Windows drive-letter Markdown), **skip the replay** and say it is blocked for lack of visible prompt context. Treat `stub`/`page_reference` text as non-standalone: surface the prompt asset or original page first, else skip it rather than replaying an item the student cannot see.
2. If the student answers a replayed item correctly, mark it 已订正 in the record. If still wrong, re-explain using the item's `explanation` field and keep it in the record.
3. Reload the confusion-tracker entries — from `study_state.json`'s `confusion_log` when it exists, else from `study_progress.md`. Read each entry aloud and have the student restate it in their own words (what it is, why it works that way).
4. If the student restates an entry correctly, set its status to 已回顾. If still vague, re-explain once and keep its status 待回顾.
5. Compile the open list: items still marked wrong plus entries still 待回顾. Hand this list to the final sprint and to `exam-cheatsheet` as priority input (it biases which knowledge points get the hard worked examples).
   - **Persist-first (notebook CLI)** — review conclusions do not evaporate in chat: write the open list (the 「还没拿下的清单」) and each replay's conclusion into the notebook BEFORE replying: `echo <review body> | python "${CLAUDE_SKILL_DIR}/scripts/notebook.py" --workspace <ws> add-entry --chapter <ch> --type review --id <slug> --title <review gist>` (body via STDIN; same `--id` replaces in place; `notebook/index.md` rebuilds; the script resolves from the skill package root). The chat reply is a digest of the list plus the pack-provided link line (zh 「完整复盘：`notebook/chNN.md#<anchor>`｜目录：`notebook/index.md`」, en `Full review: notebook/chNN.md#<anchor> | Index: notebook/index.md`). On a failed write, TELL the student and give the full list in chat; file-less clients keep chat-only output per `exam-cram`'s capability dispatch.
6. Write results back: with `study_state.json`, update statuses via `update_progress.py set-mistake-status`/`set-confusion-status` and add rows via `add-mistake`/`add-confusion` (md regenerates); without state, update each `study_progress.md` row **in place** (已订正 / 已回顾 / 待回顾) and append only genuinely new records. Never leave a mastered item as a stale wrong/待回顾 row. Do not overwrite other skills' writes.

## Output Contract
- Produce one not-yet-mastered list (「还没拿下的清单」): recorded mistakes plus confusion entries, each with its current status (已订正 / 已回顾 / 待回顾). End with a refreshed progress panel.
- **Persist-first default**: that list and the session's review conclusions are written into `notebook/chNN.md` via the notebook CLI (`--type review`) BEFORE the chat reply — the chat carries a digest plus the pack-provided link line, never the sole copy (Workflow step 5). On a failed write, say so and deliver the full list in chat; file-less clients keep chat-only output.
- Persist each mistake/confusion status update — with `study_state.json`, via `update_progress.py set-mistake-status`/`set-confusion-status` (and `add-*` for genuinely new records; the md regenerates); without state, update each existing `study_progress.md` row **in place** (已订正 / 已回顾 / 待回顾) and append only genuinely new records. Never leave a mastered item still marked wrong/待回顾. Then return control to `exam-cram`.
- Student-facing output defaults to English (Simplified Chinese if the student opened in Chinese); a persisted `study_state.json` `language` (`中文`/`English`/`双语`) switches it per exam-cram's dispatch rule with single-language purity. (See [`docs/language-policy.md`](../../docs/language-policy.md).)

## Language packs
Student-visible wording for this skill lives in per-language packs — load the one matching `study_state.json.language` BEFORE emitting any student-visible output:
- `zh` → [`../../locales/zh/skills/exam-review.md`](../../locales/zh/skills/exam-review.md)
- `en` → [`../../locales/en/skills/exam-review.md`](../../locales/en/skills/exam-review.md)
- `bilingual` → compose from the zh pack with a `> EN:` mirror line per block (rules in [`../../docs/language-policy.md`](../../docs/language-policy.md))
Unset language → this is the first conversation: the merged first-ask (mode × time budget × language) decides it; default en unless the student opened in Chinese.

## Boundaries
- **Structured progress state**: when `study_state.json` exists it is the SINGLE SOURCE OF TRUTH — update it via `python "${CLAUDE_SKILL_DIR}/scripts/update_progress.py" --workspace <ws> set/add-mistake/add-confusion/set-mistake-status/set-confusion-status/set-check/render` (marking a replayed row 已订正/已回顾 goes through `set-mistake-status`/`set-confusion-status --id <qid> --status <状态>`; ticking a `知识点打卡` item goes through `set-check --match <文本>|--index <N>`); `study_progress.md` is a GENERATED view (hand edits are lost on the next render — never hand-patch it). If a state write fails, TELL the user; never continue as if it saved. Without `study_state.json` but WITH Python (a fresh, uninitialized workspace), run `update_progress.py --workspace <ws> init` to create the source of truth FIRST — do not stop at hand-editing `study_progress.md`; only when Python truly cannot run does a hand-maintained md stay valid.

- **Scope filter & override**: default question pool is mixed; a student-restricted range (e.g. homework-only) is a recorded scope filter — serving items outside it requires the scope-override line first in the active reply language (`中文` 「⚠️ 临时覆盖你的 <scope> 范围偏好」 / `English` `⚠️ Temporarily overriding your <scope> scope preference`), and untagged (`source_type` missing) items are excluded from restricted scopes with their count reported. Official selector: `scripts/select_questions.py`.

- Replay only recorded items. Never add a question that is not already in the records or the quiz bank.
- Share the progress record with `confusion-tracker`: in state-backed workspaces both skills go through `update_progress.py` (append via `add-confusion`, status via `set-confusion-status`); only in md-only workspaces append/update `study_progress.md` rows in place. Never overwrite another skill's writes.
