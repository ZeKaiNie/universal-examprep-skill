---
name: exam-help
description: >
  备考教练的速查卡：一屏列出工作流四步、四种模式、工作区文件约定、6 大题型、防幻觉与来源标注规则，
  以及各子技能何时用。当用户问「这个技能怎么用 / 有哪些模式 / 文件都是干嘛的 / 支持什么题型」时使用。
license: MIT
---

# exam-help — 速查卡

一屏看懂这套备考技能。详细协议见根目录 `SKILL.md` 与各子技能。

## 四步工作流
1. **建库**（`exam-ingest`）：上传资料 → 自动建 wiki + 题库 + 进度。
2. **授课**（`exam-tutor`）：按章惰性加载，隐喻讲概念 / 重点题精讲 / 画图先跑算法。
3. **测验**（`exam-quiz`）：题库抽题判分，错两次给提示/跳过/归档。
4. **复盘 + 小抄**（`exam-review` / `exam-cheatsheet`）：清错题与疑难点，出考前速记。

## 四种模式（argument-hint）
- `normal` 均衡 · `sprint` 高频速刷 · `panic` 零基础重点题精讲+小抄 · `mock` 先考后讲。

## 工作区文件
- `references/wiki/chN_*.md` 分章知识库（唯一知识源，按需读） · `references/quiz_bank.json` 标准题库（唯一答案源）
- `study_plan.md` 阶段计划 · `study_progress.md` 进度 + 错题 + 💡疑难点（每轮更新、重启先读）

## 6 大题型
`choice` 选择 · `subjective` 主观/计算 · `diagram` 画图 · `fill_blank` 填空 · `true_false` 判断 · `code` 代码。

## 防幻觉与来源标注
- 只在 wiki/题库范围内教学判分；资料没有就如实弃答。
- 🟢 来自资料 · 🟡 AI 补充（可能与老师不一致）· ⚠️ 答案由 AI 生成、非老师提供。
- 题库有相关题就不自编题；不把 AI 生成内容伪装成老师提供。

## 子技能何时用
`exam-ingest` 建库 · `exam-tutor` 讲 · `exam-quiz` 测 · `exam-review` 复盘 · `exam-cheatsheet` 小抄 · `exam-audit` 只读体检 · `exam-cram` 总编排。

## Boundaries
本卡只读、不执行教学动作；要开始复习直接对 `exam-cram` 说明你的科目与剩余时间。
