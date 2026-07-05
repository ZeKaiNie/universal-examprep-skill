# Skill Architecture — 技能集合结构说明

本文档解释这套备考技能从「单体 SKILL.md」走向「可移植技能集合」后的结构，以及各项防幻觉能力落在哪里。
**本次重构只加结构、文档与测试，不改 `scripts/ingest.py` 逻辑，不改变任何既有行为。**

> A8b: student-facing rendering is dispatched by `study_state.json.language` (`中文` default / `English` / `双语`); canonical tokens are language-invariant (see docs/language-policy.md).

## 1. 兼容入口（不破坏现有用法）
- 根目录 **`SKILL.md`** 保持为**默认 / 兼容入口**，仍承载完整防编题与来源标注规则。已经按旧方式安装本技能的 host 不受影响。
- 新支持技能集合的 host 可改用 **`skills/exam-cram/SKILL.md`** 作主入口——它与根 `SKILL.md` 描述同一行为。
- **`AGENTS.md`** 是给「不读完整 SKILL.md 的通用代理」的一屏浓缩契约（防幻觉核心底线）。

## 2. 技能集合布局
```
skills/
  exam-cram/        # 主技能：编排者，承载阶梯/模式/契约
  exam-ingest/      # 子：从学生材料初始化工作区（wiki + 题库 + 进度）
  exam-tutor/       # 子：按章惰性加载授课（含零基础重点题精讲、画图先跑算法）
  exam-quiz/        # 子：题库抽题判分，支持 6 大题型
  exam-review/      # 子：错题 + 概念疑难点复盘
  exam-cheatsheet/  # 子：考前小抄 / 总复习走查
  exam-audit/       # 子：只读体检工作区，报告问题不改
  exam-help/        # 子：速查卡
  confusion-tracker/  # 子：概念疑难点追踪（写 study_progress.md），被 exam-tutor / exam-review 调用
```
每个子技能**单一职责**、各自有 frontmatter（`name` / `description` / `license`）与「触发 / 输入 / 工作流 / 输出 / 边界」五段；彼此**交叉引用而非复制**。

## 3. 子技能 ↔ 备考生命周期
| 阶段 | 子技能 |
| --- | --- |
| 冷启动建库 | `exam-ingest` |
| 按章授课 | `exam-tutor`（+ `skills/confusion-tracker` 记疑难点） |
| 刷题判分 | `exam-quiz` |
| 错题/疑难复盘 | `exam-review`（+ `skills/confusion-tracker`） |
| 考前小抄 | `exam-cheatsheet` |
| 工作区体检 | `exam-audit` |
| 速查 | `exam-help` |

> **双语分层**：模块化 `skills/exam-*` 用**英文控制段**（Purpose / Activation / Inputs / Workflow / Output Contract / Boundaries）+ `Student-facing Output` 下的**简体中文学生示例**；根 `SKILL.md` 维持中文优先作兼容入口。详见 [`language-policy.md`](language-policy.md)。学生侧模板目前**有意与控制逻辑同文件、暂不拆 `locales/`**（边界与将来怎么拆见 [`localization.md`](localization.md)）。

## 4. 当前能力落点
- **知识来源透明化（provenance）**：贯穿全部子技能——🟢 来自资料 / 🟡 AI补充，可能与你老师讲的不完全一致 / ⚠️ AI生成答案，非老师/教材提供（canonical 见 `docs/language-policy.md`）；契约写在 `exam-cram` 的 *Knowledge provenance* 与 `AGENTS.md` 规则 4–5、8。
- **3 学习模式 × 4 时间宽裕度**（A6）：`exam-cram` 的 *Modes* 段（零基础从头讲 / 某章起步补弱 / 查缺补漏，叠加 ≤1天/1-3天/3-7天/>7天）——首次对话问清、存 `study_state.json` 的 `mode`/`time_budget`；3-7天/>7天档的知识点窗口存 `knowledge_window`（`update_progress.py window-add/window-set-status`）。零基础「重点题精讲」= `零基础从头讲` 模式 + `exam-tutor` 的七步模板工作流（旧 `panic` 已迁移至此）。
- **画图题确定性处理（`type: "diagram"`）**：`exam-tutor`（讲）与 `exam-quiz`（判）的「先跑算法再画图」流程。
- **6 大题型**（`choice / subjective / diagram / fill_blank / true_false / code`）：`exam-quiz`，与 `scripts/ingest.py` 的 `VALID_QUIZ_TYPES` 一致。
- **confusion-tracker**：位于 `skills/confusion-tracker/` 的子技能（与其他子技能同级），由 `exam-tutor` 在教学时记录概念疑难点、`exam-review` 在复盘阶段调起。

## 5. Future work（后续 PR，本 PR 不含）
- **schema 校验 + workspace validator**：用 stdlib JSON Schema 校验 `quiz_bank.json` / `raw_input.json`，并加一个「检查已建工作区是否健康」的脚本（`exam-audit` 的程序化版本）。
- **规则副本对齐测试**：把 `AGENTS.md` 作为单一事实源，为各 host 规则副本加一个 stdlib 对齐检查（副本须等于 `AGENTS.md` 正文；`SKILL.md` 与 `AGENTS.md` 共有的防幻觉不变式逐字出现）。
- **long-horizon drift benchmark**：模拟 15–30 轮长会话 + 中途干扰，量化目标保持率 / 编题率 / 断点恢复一致性（对照「裸文件 agent」vs「使用本技能」），坐实本技能真正要解决的「长程漂移」痛点。
