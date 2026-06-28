---
name: exam-audit
description: >
  只读检查一个已生成的备考工作区是否健康并报告问题，默认不做任何修改。核对 references/wiki 章节、
  quiz_bank.json 题型与来源标注、study_plan/study_progress 锚点与一致性，列出缺失/越权/未标来源的
  答案等隐患。当用户怀疑工作区有问题、或想在开始复习前体检时使用。
license: MIT
---

# exam-audit — 工作区体检（只读）

检查 `exam-ingest` 建出来的工作区有没有坑。**默认只报告，不改文件**（要改须用户明确许可）。

## Activation
- 用户怀疑工作区不对（章节缺、题判不了、进度乱）；或想在正式复习前先体检。

## Inputs
- 工作区：`references/wiki/`、`references/quiz_bank.json`、`study_plan.md`、`study_progress.md`。

## Workflow（逐项核对，只读）
1. **结构**：`study_plan.md` 列的每个阶段都有对应 `references/wiki/chN_*.md` 文件？有没有孤儿章节或断链？
2. **题库**：每题 `type` 属 6 类之一？`choice` 有 `options`？`subjective` 有 `keywords`？缺 `answer` 的题有没有 ⚠️/`source: ai_generated` 标注？
3. **来源诚实**：有没有 AI 生成的答案被当成老师标准答案（缺 ⚠️ 标注）？wiki 有没有该标 🟡 却没标的 AI 补充段落？
4. **进度一致**：`study_progress.md` 里**已渲染出来的**阶段打卡行与 `study_plan.md` 的阶段对得上吗？错题 ID 在题库里都找得到吗？（注意：模板锚点 `<!-- PHASE_CHECKLIST -->` 在 `ingest.py` 生成时已被替换、正常成品里**本就不该再出现**——不要把它的缺失当成问题。）
5. **安全**：有没有 `references/wiki/` 之外的可疑写入、`../`/绝对路径残留。

## Output format
- 一份「问题清单」：每条含【级别(阻断/警告/提示)】+【位置文件】+【现象】+【建议修法】；末尾给总体结论（可用 / 需修）。
- **不自动修复**；如用户许可再逐项改，或交回 `exam-ingest` 重建。

## Boundaries
- 默认零改动、零删除——这是体检不是施工。
- 不臆断老师意图；只报客观不一致，把判断权交给学生。
