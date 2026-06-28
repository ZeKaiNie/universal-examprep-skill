---
name: exam-review
description: >
  考前易错扫雷与疑难复盘：读取 study_progress.md 的错题档案与「概念疑难点记录」，重新调取原题做扫雷
  测试，逐条请学生复述疑难点并更新状态（待回顾/已回顾）。与 confusion-tracker 协同。当进入最终复习
  阶段、或用户要求复盘错题/查漏补缺时使用。
license: MIT
---

# exam-review — 错题与疑难复盘

把整轮积累的错题与疑难点集中清算。**复盘已有记录，不教新章节。**

## Activation
- 进入最终复习阶段；或用户要求「复盘错题 / 查漏补缺 / 考前过一遍」。

## Inputs
- `study_progress.md` 的 ❌ 错题档案 与 💡 概念疑难点记录。
- `references/quiz_bank.json`（按错题 ID 调原题）。

## Workflow
1. **错题扫雷**：按错题档案的题 ID 从题库重新调原题，让学生再做一遍；仍错则再讲 `explanation` 并保留在档。
2. **疑难复述**：逐条读「概念疑难点记录」，请学生用自己的话复述/解释。
3. **状态更新**：能正确解释的疑难点 → 标「已回顾」；仍模糊的 → 保持「待回顾」并再讲一次。
4. **缺口汇总**：列出仍未过的错题与未回顾的疑难点，作为最后冲刺与小抄（`exam-cheatsheet`）的重点输入。

## Output format
- 一份「还没拿下的清单」（错题 + 疑难点）+ 各自当前状态；末尾刷新进度面板。
- 更新 `study_progress.md` 的错题/疑难点状态，交回 `exam-cram`。

## Boundaries
- 只复盘已记录项，不新增题库里没有的题。
- 与 `confusion-tracker` 写同一份 `study_progress.md`：**追加**疑难点、就地更新状态，避免覆盖他人写入。
