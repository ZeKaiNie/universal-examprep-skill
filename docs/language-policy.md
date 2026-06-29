# 语言策略 / Language Policy

本技能是**双语架构**：控制层用英文求精确可靠，学生可见层用自然简体中文。**不要把整套技能翻译成单一语言**——该用英文的地方保留英文（提升代理执行可靠性），学生真正看到的输出用中文。

This skill is **bilingual by design**: an English *control plane* for precision and reliability, and a Simplified-Chinese *student-facing layer* for natural, exam-oriented tutoring. Do **not** translate the whole skill into one language. Keep English where it improves agent reliability; use Chinese where the student actually sees the output.

---

## 双语落地范围 / Scope & rollout

The bilingual split lands in two steps and is now realized:
1. **Policy + provenance** — establish the language policy and mirror the canonical provenance labels into every entrypoint.
2. **Control-plane conversion** — the modular `skills/exam-*` files use **English control sections** (Purpose / Activation / Inputs / Workflow / Output Contract / Boundaries) while keeping **Simplified-Chinese student-facing examples** under `Student-facing Output`.

Root `SKILL.md` stays **Chinese-first** as the compatibility entrypoint, and `prompts/web_prompt.md` stays Chinese-first — neither is rewritten wholesale.

- 模块化 `skills/exam-*`：英文控制段 + `Student-facing Output` 下的中文学生示例（已落地）。
- 根目录 `SKILL.md` / `prompts/web_prompt.md`：维持**中文优先**，不整体改写。

---

## English control plane（控制层 = 英文优先）

These instructions are read by the agent (Claude / Codex), **not** the student. Prefer English, and keep them **precise, imperative, and testable**:

- **Workflow** — step order; what each step reads and writes.
- **Activation** — when a (sub)skill triggers.
- **Boundaries** — what the skill must not do.
- **Schema / Inputs / Outputs** — file layout and required fields (e.g. `quiz_bank.json` fields, validator exit codes, the `study_progress.md` contract).
- **Test rules** — exactly what the tests assert.
- **Safety rules** — path safety, progress-file protection, quiz_bank-only quizzing, anti-fabrication.

Write concrete, checkable behavior. **Avoid vague words** like "properly", "comprehensively", "as needed", "appropriately" unless the exact behavior is defined right there.

> 例：不要写「妥善处理越界提问」，要写「越界提问 → 标 🟡 AI 补充，或如实弃答」。

> 注：模块化 `skills/exam-*` 的控制段**已转为英文**；根 `SKILL.md` 维持**中文优先**（兼容入口），不强制逐句改写。新增控制指令一律遵循上面的英文 / 精确 / 可测原则。

---

## Chinese student-facing layer（学生可见层 = 简体中文）

Everything the student actually reads **defaults to Simplified Chinese unless the user asks otherwise**:

- 讲解（teaching explanations）
- 判分反馈（quiz feedback）
- 错题与疑难复盘（mistake & confusion review）
- 考前小抄（cheat sheet）
- 进度面板与提示（progress messages）
- 网页端提示词（`prompts/web_prompt.md`）

### 中文语气要求（必须）

- **具体**：说清「考什么、怎么答、哪里易错」，不要空泛。
- **简短**：一句能说清就不写一段；考前没时间读长文。
- **应试导向**：围绕「考场上怎么拿分」，给可照写的步骤 / 口诀。
- **不抽象、不翻译腔**：用中国学生平时说话的方式，别用「进行一个……的处理 / 对……加以系统阐述」这类生硬表达。

反例（别这样写）：「请对该知识点进行全面且系统的阐述。」
正例（这样写）：「这题考什么：……；标准答题步骤：1.… 2.… 3.…；易错点：……。」

### 常用中文标签（统一用词，便于学生扫读）

| 标签 | 用在哪里 |
| --- | --- |
| `当前阶段` | 进度面板 / 每轮开头点位置 |
| `这题考什么` | 讲题 / 判分时先点考点 |
| `标准答题步骤` | 给可照写的解题 / 得分步骤 |
| `易错点` | 提醒最容易丢分的地方 |
| `3分钟速记` | 口诀 / 极简记忆法 |
| `现在轮到你` | 把球抛回给学生练 |
| `已记录到错题本` | 归档错题后的回执 |
| `资料里没有明确答案` | 诚实弃答 |
| `🟡 AI补充，可能与你老师讲的不完全一致` | AI 补充内容的提醒（与下方 canonical 🟡 一致） |

### 来源标注用词（防幻觉，全技能统一）

本节是来源标注用词的**唯一权威来源（canonical）**：根目录 `SKILL.md`、`AGENTS.md`、`skills/exam-*` 各入口都以这里为准，避免不同入口出现「竞争性」标注。

- 🟢 **来自资料** — 直接源自学生上传的老师重点 / 教材 / 真题，可信度高。
- 🟡 **AI补充，可能与你老师讲的不完全一致** — 资料没覆盖、AI 用自身知识补的背景，提醒以老师为准。
- ⚠️ **AI生成答案，非老师/教材提供** — 老师只勾了题没给答案、由 AI 代答的，每一个都要标。

各入口可在上述标记后追加说明（如「以老师为准」），但**核心标记与措辞须与本节一致**；**绝不**把 AI 生成 / 补充的内容写得像老师给的标准答案——那本身就是一种幻觉。

---

## 不要做的事（Out of scope for this policy）

- 不要把整套技能翻成纯英文或纯中文。
- 不要为了「统一语言」把英文控制指令改成中文而牺牲精确性。
- 不要把学生看的中文写成翻译腔 / 学术腔。
- 不要借语言改写**削弱 V2.1 行为**：知识来源标注、零基础重点题精讲、画图先跑算法、6 大题型、quiz_bank 抽题、`study_progress.md` 进度断点、疑难追踪、路径 / 进度安全、网页可移植。

> 相关文档：[`skill-architecture.md`](skill-architecture.md)（技能结构）· [`agent-portability.md`](agent-portability.md)（跨 host 可移植）· 根目录 `SKILL.md` / `AGENTS.md`（完整 / 浓缩协议）。
