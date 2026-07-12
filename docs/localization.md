# 本地化边界 / Localization Boundary

本文档定义本技能的**本地化策略与边界**。自 v4 起，**locales/ 语言包拆分正式启用**：旧政策「暂缓拆分、等第二种打包语言出现」的触发条件已经满足——英文已是完整的第二打包语言（英文默认回复 + 英文入口面），继续内联只会放大双语文本的漂移风险。This document defines the localization policy. As of v4 the `locales/` split is **approved and active**: English is now a full second bundled language, which is exactly the trigger the previous "defer until a second locale exists" policy was waiting for.

---

## 1. v4 模型 / The v4 model

- **控制层（control plane）** 位于 `skills/*/SKILL.md`（`exam-*` 与 `confusion-tracker` 都算），控制段用**英文**，包含固定六段：
  - `Purpose`
  - `Activation`
  - `Inputs`
  - `Workflow`
  - `Output Contract`
  - `Boundaries`
- **学生可见文案**拆入语言包目录，中英物理分离：

```text
locales/
  zh/
    skills/<name>.md    # 各子技能的学生侧文案（七步模板、来源块、判分反馈……）
    messages.json       # 全部脚本用户可见消息（msgid → 中文）
    templates/          # 中文 md 模板（进度/笔记本/错题本/小抄骨架）
  en/                   # 同构英文包
```

- **脚本逻辑只有一份**（`scripts/`，语言中性）：持久化文件与 JSON 只存语言中性 canonical 代号；用户可见消息经 `scripts/i18n.py` 按语言包渲染。**逻辑不进语言包**（locale 文件 must not duplicate control behavior，只含标签、示例、模板、进度提示、小抄措辞）。
- **英文控制层保持不变**（control plane unchanged）：语言包拆分只动学生可见文案，不动控制段。
- **学生侧默认语言 = English**（学生用中文开场则切简体中文 Simplified Chinese；`双语` 为显式第三选项）。语言选择在首次对话合并首问中确定，存 `study_state.json` 的 `language`，中途可用 `set --language` 切换。
- **回退（fallback）**：找不到所选语言包时回退 `zh` 包（历史 canonical 语言，覆盖最全）。
- `exam-audit` 是只读体检、无学生侧模板，**无需** locale 文件。

> 控制层（英文、精确、可测）与学生侧（单语言纯净）的分层定义见 [`language-policy.md`](language-policy.md)；技能集合结构见 [`skill-architecture.md`](skill-architecture.md)。

---

## 2. 拆分启用的理由 / Why the split is now active

- **第二种打包语言已经存在**：英文默认回复 + 英文入口面已随上一版发布——旧政策等待的「第二 locale」触发条件成立。
- **内联双语已实证漂移**：同一份枚举词表曾在三个脚本里各自硬编码并出现分叉；手工镜像的双语文本没有机械对齐检查就会越漂越远。
- **配套约束同 PR 落地**：语言包结构对齐测试（zh/en 两包的 msgid 集合、技能文案锚点集合必须相等）与双向纯净 lint 一起进仓，「拆而不散」。

---

## 3. 语言包规则 / Language-pack rules

- 每个产出学生侧输出的技能都在 `locales/<lang>/skills/` 有对应文案文件；**每个技能指向其 locale 文件**（控制层显式声明加载哪个包）。
- locale 文件**不得复制 / 重写控制行为**（must not duplicate control behavior）。
- locale 文件**只**包含：标签（labels）、示例（examples）、模板（templates）、进度提示（progress messages）、考前小抄措辞（cheat-sheet wording）。
- zh/en 两包结构必须对齐：msgid 集合相等、技能文案文件集合相等、锚点集合相等——由结构对齐测试强制。
- `双语` 模式不设第三套文案：zh 包为主体 + 逐块 `> EN:` 镜像（组合规则见 `language-policy.md`）。

---

## 4. 翻译规则 / Translation rules

本地化时必须：

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

## 5. zh 包必保留的中文标签 / Required Chinese labels (zh pack)

下列是学生侧的 **canonical 中文词汇**——部分嵌在学生侧模板里（如 `当前阶段`），部分是判分反馈 / 诚实弃答 / 来源标注时的固定说法（如 `资料里没有明确答案`、来源标注）。它们必须原样保留在 `zh` 语言包中。来源标注用词以 [`language-policy.md`](language-policy.md) 为准（canonical 单一来源）。

- `当前阶段`
- `题面图`、`这题在问什么`、`图里要读的量`、`核心公式`、`逐步演算`、`答案自检`、`知识点溯源`（七步讲解模板的七个块标题，exam-tutor）
- `题目来源`、`答案来源`（每题固定来源块，exam-tutor 与 exam-quiz 判分反馈）
- `这题考什么`、`标准答题步骤`（exam-quiz 判分反馈用语）
- `易错点`、`3分钟速记`、`现在轮到你`（讲解收尾块——**默认不输出**，仅学生要求或存有偏好时按需给；输出时用这三个 canonical 措辞。`易错点` 另用于 exam-quiz 判分反馈；小抄为四段版式：必背结论/公式 → 例题 → 例题解答 → 要点解释）
- `已记录到错题本`
- `资料里没有明确答案`（诚实弃答）
- `🟡 AI补充，可能与你老师讲的不完全一致`（AI 补充提醒，canonical 见 `language-policy.md`）
