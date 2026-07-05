# 语言策略 / Language Policy

本技能是**双语架构**：控制层用英文求精确可靠，学生可见层用自然简体中文。**不要把整套技能翻译成单一语言**——该用英文的地方保留英文（提升代理执行可靠性），学生真正看到的输出用中文。

This skill is **bilingual by design**: an English *control plane* for precision and reliability, and a Simplified-Chinese *student-facing layer* for natural, exam-oriented tutoring. Do **not** translate the whole skill into one language. Keep English where it improves agent reliability; use Chinese where the student actually sees the output.

---

## 双语落地范围 / Scope & rollout

The bilingual split lands in two steps and is now realized:
1. **Policy + provenance** — establish the language policy and mirror the canonical provenance labels into every entrypoint.
2. **Control-plane conversion** — the modular `skills/exam-*` files use **English control sections** (Purpose / Activation / Inputs / Workflow / Output Contract / Boundaries) while keeping **Simplified-Chinese student-facing examples** under `Student-facing Output`.
   (A8a) This is now **lint-enforced zero-CJK**: `tests/test_control_plane_language.py` scans every
   non-exempt `## ` section of `skills/*/SKILL.md`, the whole `AGENTS.md`, and every script's argparse
   `description`/`epilog`/`help` contract. Chinese may appear in control text only via three structural
   escapes: 「…」 (verbatim student-visible phrasing), `…` (code spans / persisted values), or the
   canonical-token allowlist (`ALLOWED_TOKENS`). Exempt zones stay Chinese by design: YAML frontmatter
   (trigger surface), `## Student-facing Output` bodies, CJK-headed template sections, the root
   `SKILL.md`, and `prompts/web_prompt.md`.

Root `SKILL.md` stays **Chinese-first** as the compatibility entrypoint, and `prompts/web_prompt.md` stays Chinese-first — neither is rewritten wholesale.

- 模块化 `skills/exam-*`：英文控制段 + `Student-facing Output` 下的中文学生示例（已落地）。
- 根目录 `SKILL.md` / `prompts/web_prompt.md`：维持**中文优先**，不整体改写。

---

## Language state & dispatch（A8b：回复语言）

- 持久化：`study_state.json.language`，canonical `中文` / `English` / `双语`（别名经 `update_progress.py`
  `--language` 归一；未知值保留 + 告警）。缺省/为空 = `中文`（所有旧工作区行为逐字节不变）。
- 首问：并入 A6 的**一次合并首问**（模式 × 时间宽裕度 × 语言，语言行三语呈现）；紧迫开场按学生开场语言
  静默推断，**绝不推断 `双语`**。会话中途 `set --language <值>` 随时切换，下一条回复生效。
- `双语` 是**组合规则**而非第三套模板：逐块 zh 在前、`> EN:` 镜像随后；锚点只出现一次（token+gloss 形态）。

### ANCHOR-INVARIANCE PRINCIPLE（锚点不变性，MUST）

以下十类 canonical 字面在**任何语言模式下逐字节原样输出**（英文/双语模式在 token **之后或下一行**加
英文 gloss，绝不改写 token 内部——它们被 behavior_smoke / drift 从 transcript 解析、被测试钉死、
或持久化在学生工作区里）：

1. 三个来源标注 canonical 标签（🟢/🟡/⚠️ 全文）
2. 范围覆盖声明 「⚠️ 临时覆盖你的 <scope> 范围偏好」
3. 七步模板块标（圈号 + canonical 中文名：① 题面图 … ⑦ 知识点溯源）
4. 来源块行 `题目来源：…｜答案来源：…` 与 来源未知/来源页未知
5. 收尾块名 易错点 / 3分钟速记 / 现在轮到你
6. 错题本/错题档案 与回执 已记录到错题本 / 已记录到疑难点
7. 阶段引用 `阶段 N`
8. 窗口复核提示语（还记得 / 复述 / 做题实测 类）
9. 双语资产标签 题面图 / question-side asset、答案图 / answer-side asset
10. 弃答 canonical 资料里没有这道题的答案（及其变体）

持久化文件与脚本输出在所有模式下保持中文 canonical；向非中文学生转述脚本回执/失败时，引用中文原文
并附英文复述——**绝不在翻译中丢失 fail-loud 内容**。

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
- 不要借语言改写**削弱以下行为**：知识来源标注、零基础重点题精讲、画图先跑算法、6 大题型、quiz_bank 抽题、`study_progress.md` 进度断点、疑难追踪、路径 / 进度安全、网页可移植。

> 相关文档：[`skill-architecture.md`](skill-architecture.md)（技能结构）· [`agent-portability.md`](agent-portability.md)（跨 host 可移植）· [`localization.md`](localization.md)（本地化边界：为何暂不拆 `locales/`、将来怎么拆）· 根目录 `SKILL.md` / `AGENTS.md`（完整 / 浓缩规则）。
