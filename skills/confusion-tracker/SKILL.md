---
name: confusion-tracker
description: 教学过程中自动捕获和记录学习者的概念疑难点（"为什么/是什么/怎么推/什么意思"类型的问题），保存到进度文件的"概念疑难点记录"区，形成考前回顾清单。
license: MIT
version: 1.0.0
tags: [teaching, tracking, review]
status: stable
---

# confusion-tracker — concept-confusion tracking

## Purpose
Capture the learner's concept-level confusions (why / what / how-derived questions — not quiz answers) during tutoring and record them into the 「概念疑难点记录」 section of `study_progress.md`, building a pre-exam review list. Used by `exam-tutor` (while teaching) and `exam-review` (during the final sweep).

## Activation
- During tutoring, when the learner asks a concept question matching: 「为什么…？」/「…是什么、什么意思？」/「这个公式怎么推、怎么来的？」/「…的重点是什么？」/「讲一下…」, or any clarification follow-up that is not a quiz answer.
- Skip for: pure quiz answering (right or wrong), and chit-chat that needs no concept explanation.

## Inputs
- The progress-file path (e.g. `study_progress.md`), read at session start.
- The current chapter/phase name being taught.

## Workflow
1. **Detect** — decide whether the follow-up is a concept question (not a quiz item or its answer).
2. **Answer** — give a concise, clear explanation grounded in the current wiki chapter. Label the source: 🟢 来自资料 for material-sourced content, 🟡 AI补充，可能与你老师讲的不完全一致 for AI-supplied background. Never present AI-added content as the teacher's.
3. **Record** — persist the confusion: `关联章节` / `疑难点` (one line) / `解答要点` (≤2 sentences) / `状态` (default 待回顾). When `study_state.json` exists, the ONLY valid write path is `python "${CLAUDE_SKILL_DIR}/scripts/update_progress.py" --workspace <ws> add-confusion --chapter <ch> --note <疑难点/解答要点>` — the md table is a generated view and a hand-appended row is lost on the next render. Without state (no-Python fallback), append to the 「## 💡 概念疑难点记录」 table in `study_progress.md` directly, auto-incrementing the `序号` column.
   - **Persist-first (notebook CLI)** — the state row stays exactly as above; ADDITIONALLY persist the full explanation itself (step 2's answer, provenance labels included) so it survives outside chat: `echo <explanation body> | python "${CLAUDE_SKILL_DIR}/scripts/notebook.py" --workspace <ws> add-entry --chapter <ch> --type confusion --id <slug> --title <confusion gist>` (body via STDIN; same `--id` replaces in place; `notebook/index.md` rebuilds; the script resolves from the skill package root). The receipt line then carries the pack-provided link line (zh 「完整解答：`notebook/chNN.md#<anchor>`｜目录：`notebook/index.md`」, en `Full explanation: notebook/chNN.md#<anchor> | Index: notebook/index.md`). On a failed notebook write, TELL the student (the chat explanation already delivered stands as the copy); file-less clients keep chat-only output per `exam-cram`'s capability dispatch.
4. **Confirm** — tell the learner it was logged (e.g. 「已记录到疑难点」) in one short line, without breaking the teaching flow.

## Output Contract
- Persist one confusion record (`关联章节` / `疑难点` / `解答要点` / `状态`): with `study_state.json`, the output contract IS the `update_progress.py add-confusion` call (the md table regenerates from state); without state, append one row to the 「## 💡 概念疑难点记录」 table in `study_progress.md` (`序号` auto-increments).
- **Persist-first default**: the full confusion explanation is ALSO written into `notebook/chNN.md` via the notebook CLI (`--type confusion`, Workflow step 3) — the state row records that the confusion exists, the notebook entry preserves the explanation itself; the receipt carries the pack-provided link line. File-less clients keep chat-only output.
- During the final sweep, read the confusion records and have the learner restate each: update `状态` **in place** — 待回顾 → 已回顾 when explained correctly; keep 待回顾 and re-explain otherwise. Never overwrite other skills' writes.
- Student-facing output defaults to English (Simplified Chinese if the student opened in Chinese); a persisted `study_state.json` `language` (`中文`/`English`/`双语`) switches it per exam-cram's dispatch rule with single-language purity.

## Language packs
Student-visible wording for this skill lives in per-language packs — load the one matching `study_state.json.language` BEFORE emitting any student-visible output:
- `zh` → [`../../locales/zh/skills/confusion-tracker.md`](../../locales/zh/skills/confusion-tracker.md)
- `en` → [`../../locales/en/skills/confusion-tracker.md`](../../locales/en/skills/confusion-tracker.md)
- `bilingual` → compose from the zh pack with a `> EN:` mirror line per block (rules in [`../../docs/language-policy.md`](../../docs/language-policy.md))
Unset language → this is the first conversation: the merged first-ask (mode × time budget × language) decides it; default en unless the student opened in Chinese.

## Boundaries
- **Structured progress state**: when `study_state.json` exists it is the SINGLE SOURCE OF TRUTH — record via `python "${CLAUDE_SKILL_DIR}/scripts/update_progress.py" --workspace <ws> add-confusion`, update review status via `set-confusion-status --id <qid>|--index <N> --status 已回顾/待回顾`; never hand-patch the generated `study_progress.md`. If the state write fails, TELL the user; never continue as if it saved.
- Only record concept questions; never quiz or grade (that is `exam-quiz`).
- Concept answers carry the canonical provenance labels (🟢 来自资料 / 🟡 AI补充，可能与你老师讲的不完全一致 / ⚠️ AI生成答案，非老师/教材提供); never disguise AI-added content as teacher-provided.
- Share the progress state with `exam-review`: in state-backed workspaces both skills go through `update_progress.py` (append via add-confusion, status via set-confusion-status); in md-only workspaces append/update `study_progress.md` in place. Never overwrite other skills' writes.
