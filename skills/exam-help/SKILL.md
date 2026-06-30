---
name: exam-help
description: >
  备考教练的速查卡：一屏列出工作流四步、四种模式、工作区文件约定、6 大题型、防幻觉与来源标注规则，
  以及各子技能何时用。当用户问「这个技能怎么用 / 有哪些模式 / 文件都是干嘛的 / 支持什么题型」时使用。
license: MIT
---

# exam-help — 速查卡

## Purpose
Render a single-screen reference card for the exam-cram skill suite: the four-step workflow, four modes, workspace file conventions, six quiz types, anti-hallucination and provenance rules, and when to use each subskill. Read-only.

## Activation
Activate when the user asks how this skill works, what modes exist, what each workspace file is for, or which quiz types are supported (e.g. 「这个技能怎么用 / 有哪些模式 / 文件都是干嘛的 / 支持什么题型」).

## Inputs
None. Take no files, no arguments, no workspace state. Emit the static card below.

## Workflow
1. Print the reference card under Student-facing Output: the Chinese card verbatim by default; if the user explicitly asks for another language, render the same content in that language.
2. Do not read, scan, or load any workspace files (`references/wiki/`, `references/quiz_bank.json`, `study_progress.md`, `study_plan.md`).
3. Do not run `scripts/ingest.py` or any subskill.
4. End. Do not start tutoring, quizzing, ingesting, or grading.

## Output Contract
- Output exactly one help card; perform no further action.
- Mutate no state: write/create/delete no files; do not touch `study_progress.md` or any workspace artifact.
- Do not teach, quiz, grade, or initialize a workspace.
- Preserve provenance markers verbatim where shown: 🟢 来自资料 / 🟡 AI补充，可能与你老师讲的不完全一致 / ⚠️ AI生成答案，非老师/教材提供.
- Student-facing output defaults to Simplified Chinese unless the user asks otherwise.

## Student-facing Output
一屏看懂这套备考技能。详细规则见根目录 `SKILL.md` 与各子技能。

### 四步工作流
1. **建库**（`exam-ingest`）：上传资料 → 自动建 wiki + 题库 + 进度。
2. **授课**（`exam-tutor`）：按章惰性加载，隐喻讲概念 / 重点题精讲 / 画图先跑算法。
3. **测验**（`exam-quiz`）：题库抽题判分，错两次给提示/跳过/归档。
4. **复盘 + 小抄**（`exam-review` / `exam-cheatsheet`）：清错题与疑难点，出考前速记。

### 四种模式（argument-hint）
- `normal` 均衡 · `sprint` 高频速刷 · `panic` 零基础重点题精讲+小抄 · `mock` 先考后讲。

### 工作区文件
- `references/wiki/chN_*.md` 分章知识库（唯一知识源，按需读） · `references/quiz_bank.json` 标准题库（唯一答案源）
- `study_plan.md` 阶段计划 · `study_progress.md` 进度 + 错题 + 💡疑难点（每轮更新、重启先读）

### 6 大题型
`choice` 选择 · `subjective` 主观/计算 · `diagram` 画图 · `fill_blank` 填空 · `true_false` 判断 · `code` 代码。

### 防幻觉与来源标注
- 只在 wiki/题库范围内教学判分；资料没有就如实弃答。
- 🟢 来自资料 · 🟡 AI补充，可能与你老师讲的不完全一致 · ⚠️ AI生成答案，非老师/教材提供。
- 题库有相关题就不自编题；不把 AI 生成内容伪装成老师提供。

### 子技能何时用
`exam-ingest` 建库 · `exam-tutor` 讲 · `exam-quiz` 测 · `exam-review` 复盘 · `exam-cheatsheet` 小抄 · `exam-audit` 只读体检 · `exam-cram` 总编排。

### 语言 / Language
Student-facing output defaults to Simplified Chinese unless the user asks otherwise.（学生可见的讲解 / 判分 / 复盘 / 小抄默认简体中文；控制指令保持英文 / 精确。详见 [`docs/language-policy.md`](../../docs/language-policy.md)。）

## Boundaries
This card is read-only and executes no teaching action. To start reviewing, tell `exam-cram` your subject and remaining time.
