---
name: exam-cheatsheet
description: >
  全员通关后生成考前极简速记小抄（Cheat Sheet）与总复习走查 walkthrough.md：把各章核心公式/结论、
  老师勾的重点题答题框架、错题与疑难点的速记口诀，压缩成考场能默写的一两页。当复习收尾、用户要
  「考前小抄/速记/总结」时使用。
license: MIT
---

# exam-cheatsheet — pre-exam cheatsheet

## Purpose
Compress everything already mastered into a one-to-two-page, printable, copy-by-hand cram sheet, written to `walkthrough.md` in the workspace. Summarize only mastered content. Do not teach new material and do not invent new questions.

## Activation
Trigger when all study phases are basically cleared and review is wrapping up, OR when the user asks for 「给我一份考前小抄 / 速记 / 总复习」 (a pre-exam cheat sheet, quick-recall sheet, or final review).

## Inputs
- `references/wiki/` — core conclusions/formulas per chapter. Iterate through **all mastered chapters** — from `study_state.json`'s `current_phase`/`phase_checklist` when it exists (the A4 source of truth), else `study_progress.md`, against `study_plan.md` — reading each chapter slice one at a time (never dump the whole wiki into context at once) so the sheet covers every mastered chapter.
- `references/quiz_bank.json` — teacher-flagged key items and their answer frameworks.
- Weak-spot source: `study_state.json` (`mistake_archive` / `confusion_log` / `phase_checklist`) when it exists — the A4 source of truth; else `study_progress.md` (mistakes, confusion entries, per-chapter mastery; a generated view that may be stale). Read mistakes and confusion entries FIRST.

## Workflow
1. **Load weak spots first.** Read mistakes and confusion entries — from `study_state.json` when it exists, else `study_progress.md` — before anything else, so the cram sheet prioritizes what the user still loses points on.
2. **Extract the skeleton.** For each chapter keep only the highest-frequency / highest-scoring formulas, conclusions, and one-sentence term definitions. Drop everything else.
3. **Answer templates.** For teacher-flagged key items in `references/quiz_bank.json`, give a copy-it-in-the-exam answer framework / solution-step list; attach a 3-minute mnemonic.
4. **Weak-spot column.** List mistakes and still-open confusion entries in their own column as the last thing to review before the exam.
5. **Mark provenance** (canonical labels in [`docs/language-policy.md`](../../docs/language-policy.md)): tag each line with 🟢 来自资料 / 🟡 AI补充，可能与你老师讲的不完全一致 / ⚠️ AI生成答案，非老师/教材提供, so AI-added lines are never mistaken for teacher emphasis.
6. **Write output.** Write `walkthrough.md` to the workspace: per-chapter quick-recall + key-item templates + weak-spot list; refresh the progress panel at the end.
7. Never invent teacher emphasis that is not in the materials. If the materials do not flag a point, do not present it as a teacher-flagged item.

## Output Contract
- Write `walkthrough.md`: per-chapter quick recall + key-item answer templates + weak-spot list, with a refreshed progress panel at the end.
- Every line carries one provenance label: 🟢 来自资料 / 🟡 AI补充，可能与你老师讲的不完全一致 / ⚠️ AI生成答案，非老师/教材提供.
- Keep it to one or two printable, hand-copyable pages.
- Student-facing output defaults to Simplified Chinese; a persisted `study_state.json` `language` (`English`/`双语`) switches it per exam-cram's dispatch rule (canonical tokens verbatim). (See [`docs/language-policy.md`](../../docs/language-policy.md).)

## Student-facing Output
考前最后一小时速记小抄，按这几栏压缩（每条一行、能照写，标清来源）：

```text
【必背结论/公式】
- ……（🟢 来自资料）
- ……（🟡 AI补充，可能与你老师讲的不完全一致）

【老师强调】
- ……（老师勾的重点题答题框架，考场照写拿分）

【常见易错点/坑】
- ……（最容易丢分或答偏的地方）

【3分钟速记口诀】
- ……

【还没拿下（考前再看一眼）】
- 错题：……　疑难点：……
```


Render per the persisted `study_state.json` `language` (`中文` default / `English` / `双语`); canonical tokens stay verbatim with a trailing gloss — see [`exam-cram`](../exam-cram/SKILL.md) Output Contract for the dispatch and composition rules.

## Boundaries
- Do not put content into the cram sheet that the materials do not cover unless it is tagged 🟡 or ⚠️.
- The cram sheet is a compression, not a replacement for systematic review, and not a shortcut around the source-labeling and quiz_bank-only rules.
