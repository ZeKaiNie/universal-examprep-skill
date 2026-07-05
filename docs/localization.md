# 本地化边界 / Localization Boundary

本文档定义本技能的**本地化策略与边界**。当前**有意暂不**把学生侧中文模板拆进 `locales/`——只在真正引入第二种打包语言时才做。This document defines the localization policy. We **intentionally do NOT** split student-facing templates into `locales/` yet.

---

## 1. 当前模型 / Current model

- **控制层（control plane）** 位于 `skills/*/SKILL.md`（`exam-*` 与 `confusion-tracker` 都算），控制段用**英文**，包含固定六段：
  - `Purpose`
  - `Activation`
  - `Inputs`
  - `Workflow`
  - `Output Contract`
  - `Boundaries`
- **学生侧示例 / 模板** 目前放在**产出学生侧输出的技能**（`exam-tutor` / `exam-quiz` / `exam-review` / `exam-cheatsheet` / `exam-cram` / `exam-ingest` / `exam-help` / `confusion-tracker`）的 `## Student-facing Output` 段（与控制逻辑同文件）。`exam-audit` 是只读体检、**没有**学生侧模板，将来拆 locale 时也无需为它建 locale 文件。
- **学生侧默认语言 = 简体中文（Simplified Chinese）**，除非用户另有要求。
- **根目录 `SKILL.md` 与 `prompts/web_prompt.md`** 维持**中文优先（Chinese-first）的兼容入口**，本 PR 不改写它们。

> 控制层（英文、精确、可测）与学生侧（自然简体中文）的分层定义见 [`language-policy.md`](language-policy.md)；技能集合结构见 [`skill-architecture.md`](skill-architecture.md)。

---

## 2. 为什么现在不拆模板 / Why templates are not split yet

- **还没有第二种打包语言**：目前只打包了简体中文一种，拆 `locales/` 没有第二个对象。
- **把示例放在控制逻辑旁边更易评审**：同一个 `SKILL.md` 里既有「该怎么做」又有「该产出什么样的中文」，reviewer 一眼能对上。
- **降低「加载了控制规则却漏掉学生侧模板」的风险**：拆成多文件后，host 可能只加载控制段而没加载对应 locale 文件，导致学生侧输出缺失。
- **拆分只应在真正加入第二个 locale 时进行**：在那之前，拆分只会徒增文件加载复杂度与同步（sync）风险。

---

A8b 补充：第二语言层目前以 **en 平行块**形式内联在各子技能的 `## Student-facing Output`
（`skills/exam-tutor` / `exam-quiz` 的 English rendering 块 + 其余子技能的 dispatch 指针）；
回复语言由 `study_state.json.language` 派发（`中文`/`English`/`双语`，见
[`language-policy.md`](language-policy.md) 的 Language state & dispatch 与锚点不变性原则）。
拆分 `locales/` 目录仍**暂不**做。

A8c 附记（**撤回旧口径**）：A8c 已落地为**同仓同装的英文入口面**——`SKILL.en.md` +
`prompts/web_prompt.en.md`，均为**锚点保持的派生渲染**（source of truth 仍是对应中文文件，
见 [`language-policy.md`](language-policy.md) 的 A8c 小节）。它**不是**第二个打包 locale、
**不**触发 `locales/` 拆分；拆分留待真正的**第二**种打包语言（完整 anchor-free 第二语言层）
出现时再做，届时内联 en 块与 en 入口面即拆分素材源。

## 3. 将来的 locale 拆分规则 / Future locale split rule

**仅当**某个未来 PR 真正加入第二种语言时，才引入：

```text
locales/
  zh-CN/
  <new-locale>/
```

届时：

- 把**学生侧模板**从 skills 里移出到 locale 文件。
- **英文控制层保持不变**（control plane unchanged）。
- 保留 **`zh-CN` 作为兜底（fallback）**。
- 加测试：**每个技能都指向其 locale 文件**。
- **locale 文件不得复制 / 重写控制行为**（must not duplicate control behavior）。
- locale 文件**只**包含：标签（labels）、示例（examples）、模板（templates）、进度提示（progress messages）、考前小抄措辞（cheat-sheet wording）。

---

## 4. 翻译规则 / Translation rules

将来做本地化时必须：

- **语义上保留三类来源标注**（canonical provenance categories，措辞见 [`language-policy.md`](language-policy.md)）：
  - 来自资料（material-sourced，🟢）
  - AI 补充（AI supplemental，🟡）
  - AI 生成答案（AI-generated answer，⚠️）
- **保持「AI 生成答案非老师/教材提供」的警示清晰**，绝不让 AI 生成内容看起来像老师给的标准答案。
- **不得削弱「只从 `quiz_bank.json` 出题」**（quiz_bank-only quizzing）。
- **不得削弱进度断点**（progress checkpointing，`study_progress.md`）。
- **不得改动 schema、workflow 或安全边界**（safety boundaries）。
- **避免直译**：若逐字翻译会让学生侧文本不自然，应改成目标语言里自然、应试的说法。

---

## 5. 当前必须保留的中文标签 / Current required Chinese labels

下列是当前学生侧的 **canonical 中文词汇**——部分嵌在 `## Student-facing Output` 模板里（如 `当前阶段`），部分是判分反馈 / 诚实弃答 / 来源标注时的固定说法（如 `资料里没有明确答案`、来源标注）。它们必须保留在打包的 `zh-CN` 学生侧；将来拆 locale 时原样带到 `zh-CN` 文件。来源标注用词以 [`language-policy.md`](language-policy.md) 为准（canonical 单一来源）。

- `当前阶段`
- `题面图`、`这题在问什么`、`图里要读的量`、`核心公式`、`逐步演算`、`答案自检`、`知识点溯源`（A5 七步讲解模板的七个块标题，exam-tutor）
- `题目来源`、`答案来源`（A5 每题固定来源块，exam-tutor 与 exam-quiz 判分反馈）
- `这题考什么`、`标准答题步骤`（exam-quiz 判分反馈用语）
- `易错点`、`3分钟速记`、`现在轮到你`（讲解收尾块——**默认不输出**，仅学生要求或存有偏好时按需给；输出时用这三个 canonical 措辞。`易错点` 另用于 exam-quiz 判分反馈与小抄栏目）
- `已记录到错题本`
- `资料里没有明确答案`（诚实弃答）
- `🟡 AI补充，可能与你老师讲的不完全一致`（AI 补充提醒，canonical 见 `language-policy.md`）
