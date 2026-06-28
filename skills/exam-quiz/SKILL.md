---
name: exam-quiz
description: >
  从标准题库 references/quiz_bank.json 抽取本章题目对学生测验并判分，支持 6 大题型（选择、主观、
  画图、填空、判断、代码）。主观题用「要点检索制」对照 keywords 判分，连续答错两次给提示/跳过/归档。
  禁止现场编题。当某一阶段学完需要刷题检验、或用户要求测验/模考时使用。
license: MIT
---

# exam-quiz — 抽题判分

只从题库出题、按标准答案判分。**绝不现场编造题目或答案。**

## Activation
- 某阶段学完，需要刷题检验；或用户要求「测一下 / 来几道题 / 模考」。

## Inputs
- `references/quiz_bank.json`（题库；每题**必须带 `chapter`（或 `phase`）**——抽题按它过滤，缺了该题在章节测验里就抽不到；并带 `type`、`answer`、`explanation`、`source`，主观题带 `keywords`）。
- 当前章节号（只抽本章 `chapter` 的题）。

> 若由 `exam-ingest` 生成题库，须保证每道题都带 `chapter`/`phase`，否则即便题库里有题，章节测验也会「找不到题」。

## Workflow
1. **标准抽题**：按当前阶段**匹配题目的 `chapter` 或 `phase`** 过滤出题（题库里两种字段都可能用，只看 `chapter` 会漏掉只标了 `phase` 的题）；题库里有相关题就**绝不**自己编题。
2. **按 6 大题型判分**：
   - `choice` 选择 — 比对 `answer` 选项。
   - `subjective` 主观/计算 — 「要点检索制」：作答是否覆盖该题 `keywords` 与关键步骤，意思对即通过，给相似度反馈。
   - `fill_blank` 填空 — 比对标准填项（容忍同义表述）。
   - `true_false` 判断 — 比对真假并要求简述理由。
   - `code` 代码/改错 — 看关键修改点/输出是否符合 `answer`。
   - `diagram` 画图 — 不靠想象判图：按 `render_hint` **先跑标准算法**得到结构再与学生作答比对；提醒老师画法优先。
3. **逃生通道**：答错先给逻辑漏洞 + 原题 `explanation` + 提示；**连续答错 2 次**主动给「查看提示 / 跳过并归档错题 / 继续」三选一，按选择放行。
4. **归档**：跳过或答错的题写入 `study_progress.md` 错题档案。
5. **来源诚实**：题/答 `source` 为 `ai_generated` 的，判分时提示「⚠️ 此题答案由 AI 生成、非老师提供，仅供参考」。

## Output format
- 一次一题，判分给「过/未过 + 要点反馈」；末尾刷新进度面板。
- 更新 `study_progress.md` 打卡与错题档案，交回 `exam-cram`。

## Boundaries
- 题库有相关题时不自编题；无答案不硬判，标 ⚠️ 或如实说明。
- 画图题不凭记忆判定对错——以程序跑出的标准结构为准。
