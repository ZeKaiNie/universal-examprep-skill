---
name: confusion-tracker
description: 教学过程中自动捕获和记录学习者的概念疑难点（"为什么/是什么/怎么推/什么意思"类型的问题），保存到进度文件的"概念疑难点记录"区，形成考前回顾清单。
license: MIT
version: 1.0.0
tags: [teaching, tracking, review]
status: stable
---

# confusion-tracker — 概念疑难点追踪

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
3. **Record** — append the confusion to the 「## 💡 概念疑难点记录」 table in `study_progress.md`: 关联章节 / 疑难点 (one line) / 解答要点 (≤2 sentences) / 状态 (default 待回顾). Auto-increment 序号 from the existing rows.
4. **Confirm** — tell the learner it was logged (e.g. 「已记录到疑难点」) in one short line, without breaking the teaching flow.

## Output Contract
- Append one row to the 「## 💡 概念疑难点记录」 table in `study_progress.md` (序号 / 关联章节 / 疑难点 / 解答要点 / 状态).
- During the final sweep, read the confusion records and have the learner restate each: update 状态 **in place** — 待回顾 → 已回顾 when explained correctly; keep 待回顾 and re-explain otherwise. Never overwrite other skills' writes.
- Student-facing output defaults to Simplified Chinese unless the user asks otherwise.

## Student-facing Output
进度文件里的表格格式（学生侧中文，序号按已有记录递增）：

```text
## 💡 概念疑难点记录

| 序号 | 关联章节 | 疑难点 | 解答要点 | 状态 |
|:---|:---|:---|:---|:---|
| 1 | 晶体结构 | 为什么FCC是ABC堆垛？ | 第三层落C凹坑→FCC，落A→HCP | 待回顾 |
```

记录完后给一句简短回执（如「已记录到疑难点」），不打断教学节奏。

## Boundaries
- Only record concept questions; never quiz or grade (that is `exam-quiz`).
- Concept answers carry the canonical provenance labels (🟢 来自资料 / 🟡 AI补充，可能与你老师讲的不完全一致 / ⚠️ AI生成答案，非老师/教材提供); never disguise AI-added content as teacher-provided.
- Share `study_progress.md` with `exam-review`: append new entries and update status in place; do not overwrite other skills' writes.
