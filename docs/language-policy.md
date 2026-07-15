# 语言策略 / Language Policy

本项目采用一个英文控制层和两个学生文案包。根 [`SKILL.md`](../SKILL.md) 是**语言中性路由器**；行为的唯一事实源是 [`skills/exam-cram/SKILL.md`](../skills/exam-cram/SKILL.md) 与各子技能；`locales/zh/`、`locales/en/` 只负责 student-facing wording、模板和兼容入口，不能复制或改写控制流程。

The project uses an English control plane and two student-facing wording packs. Root [`SKILL.md`](../SKILL.md) is a **language-neutral router**. Behavioral rules live once in [`skills/exam-cram/SKILL.md`](../skills/exam-cram/SKILL.md) and its sub-skills; `locales/zh/` and `locales/en/` provide Simplified Chinese or English wording, templates, and compatibility entries without owning workflow logic.

## 1. Language state and canonical dispatch

- 状态字段：`study_state.json.language`。
- 持久化规范值是语言中性代号 `zh`、`en`、`bilingual`。显示输入 `中文`、`English`、`双语` 与旧状态值仍可由 `update_progress.py set --language` 接受并迁移，但新写入统一保存代号。
- 首次普通对话在“一次合并首问”中确定学习模式、时间宽裕度和回复语言，并用一次 `set` 调用保存。默认 `English`；学生用中文开场则默认 `中文`。
- 紧迫开场静默推断语言和其余两项并立刻授课，绝不推断 `双语`。中途切换从下一条回复生效。
- 脚本对缺失语言的中文兜底只服务旧工作区，不改变新会话的默认策略。

Dispatch is exact:

| Persisted value | Wording source | Student-visible rendering |
| --- | --- | --- |
| `zh` | `locales/zh/skills/<skill>.md` | Simplified Chinese only; display choice `中文` |
| `en` | `locales/en/skills/<skill>.md` | English only; display choice `English` |
| `bilingual` | both packs | Chinese block, then a `> EN:` mirror for every block; display choice `双语` |

## 2. SINGLE-LANGUAGE PURITY

- `中文`：智能体生成的学生可见 prose 不得夹英文句子。
- `English`: agent-authored student-visible prose contains no CJK.
- `双语`：不是第三套翻译文件；逐块中文在前、英文镜像在后，每一侧分别保持纯净。
- 允许任何模式保留：代码 span、路径、命令、JSON 键、数学和单位符号、题号、emoji。

### Source-quotation exception / 原文引用例外

原始课件、考试题、老师答案中的**逐字引文**可以保留原语言，避免悄悄改写证据；与当前回复语言不同时必须明确标成“原文引用”或 `Original-language quotation`。该例外仅覆盖忠实引文，不覆盖智能体生成内容。

A verbatim quotation from source material may stay in its original language when explicitly labeled. Every agent-authored heading, transition, explanation, notice, generated solution, and summary still follows the selected language. Thus an English handout may contain a clearly labeled Chinese exam question, but it may not contain Chinese agent commentary around it.

## 3. EN CANONICAL VOCABULARY

`English` 模式的固定话术使用下表，不自创竞争性措辞：

| Category | Chinese canonical | English canonical |
| --- | --- | --- |
| Provenance | 🟢 来自资料 | 🟢 From your materials |
| Provenance | 🟡 AI补充，可能与你老师讲的不完全一致 | 🟡 AI-supplemented — may differ from what your teacher taught |
| Provenance | ⚠️ AI生成答案，非老师/教材提供 | ⚠️ AI-generated answer — not from your teacher or textbook |
| Walkthrough | ① 题面图 | ① Question figure |
| | ② 这题在问什么 | ② What's being asked |
| | ③ 图里要读的量 | ③ What to read off the figure |
| | ④ 核心公式 | ④ Core formula |
| | ⑤ 逐步演算 | ⑤ Step-by-step solution |
| | ⑥ 答案自检 | ⑥ Answer self-check |
| | ⑦ 知识点溯源 | ⑦ Source trace |
| Source block | `题目来源：…｜答案来源：…｜<标签>` | `Question source: … \| Answer source: … \| <label>` |
| Unknown source | 来源未知 / 来源页未知 | Source unknown / Source page unknown |
| Optional closers | 易错点 / 3分钟速记 / 现在轮到你 | Common pitfalls / 3-minute mnemonic / Your turn |
| Receipts | 已记录到错题本 / 已记录到疑难点 | Recorded to the mistake archive / Recorded to the confusion log |
| Stage references | 阶段 N / 从阶段 N 继续 | Stage N / Resuming from Stage N |
| Honest abstention | 资料里没有这道题的答案 | The materials do not contain an answer to this question. |
| Scope override | `⚠️ 临时覆盖你的 <范围> 范围偏好` | `⚠️ Temporarily overriding your <scope> scope preference` |
| Assets | 题面图 / 答案图 | Question-side asset / Answer-side asset |
| Progress panel | 备考科目 / 当前复习 / 进度打卡 / 错题累积 | Subject / Current stage / Progress / Mistake log |

Rules:

- A source block ends with one complete provenance sentence, never an emoji alone.
- English uses ASCII `|`; Chinese uses full-width `｜`.
- Optional closers remain off unless the student asks or a stored preference requests them.
- Unknown metadata is stated honestly; filenames and pages are never invented.

## 4. THREE-LAYER CONTRACT: PERSISTED / JUDGING-LAYER VOCABULARY

Language is split into three layers that must not be conflated:

1. **Machine schema.** JSON keys, stable IDs, issue/patch statuses, reason codes, CLI subcommands, and structured control output keep their defined machine spelling (normally English). Never translate `issue_id`, `content_unit_id`, `pending`, `validated`, `applied`, or an equivalent schema token merely because the student selected Chinese.
2. **Canonical domain values.** New `study_state.json` writes use the neutral codes defined by `scripts/i18n.py`: modes `from_scratch|shore_up|fill_gaps`, time budgets `le1d|d1_3|d3_7|gt7d`, languages `zh|en|bilingual`, and the documented status codes. Historical Chinese display values remain migration inputs and generated-view wording, not the new schema truth. Persisted values are never localized per session. When prose needs to mention a code, put it in a code span and explain it in the active language.
3. **Human-readable views.** Agent-authored chat, notebook explanations, study guides, receipts, and summaries follow the selected student language. A renderer that still produces a Chinese-canonical compatibility view (notably a legacy/generated progress view) does not exempt the agent from English purity: treat that file as a state-backed machine/compatibility artifact and restate its meaning in the active language instead of pasting it as English prose.
**Language-switch consequence.** Changing `study_state.json.language` makes a prior-language `chNN.guide.json`, HTML/PDF, receipt, and QA stale for completion/delivery. Relocalize or source-consciously author every newly required block, re-import the typed manifest, and—when visual output is requested—rerender and repeat all-page QA. `≤1天` may shorten both language blocks but may not omit either side of bilingual content.

Script output must also declare which layer it belongs to. JSON intended for automation stays machine-stable; a message intended for the student uses the locale catalog. Tests for English rendering use the English vocabulary above, while state and ingestion-ledger tests use exact canonical schema/value spellings.

## 5. Control-plane / student-facing ownership

Control-plane rules belong in `skills/*/SKILL.md` and must be imperative and testable: activation, read/write order, schemas, exit handling, path safety, bank-only quizzes, visual gates, progress writes, and anti-fabrication. A behavior change is made there once.

Student-facing wording belongs in `locales/<lang>/skills/*.md`, `messages.json`, and templates. Locale files may define labels, examples, receipts, headings, and natural phrasing, but must not create a different workflow, fallback, source policy, or completion rule. The two `locales/<lang>/SKILL.md` files are compact compatibility indices, not full duplicate manuals.

## 6. Web compatibility

The web prompts cannot write local state or run repository scripts. They may consume a pasted `study_state.json` only as read-only evidence and must return a copyable progress panel. All other safety rules remain:

- quizzes use a mounted `quiz_bank.json` only;
- no bank means teaching may continue but the chapter is capped at `covered_unverified`;
- visual-dependent items fail closed when their prompt assets cannot render;
- the web agent never claims a local file write succeeded.

## 7. Maintenance checks

Any wording or dispatch change must update both language packs in the same change and pass:

- canonical-value routing checks;
- English zero-CJK and Chinese output-purity checks;
- locale roster/message-key parity;
- Markdown relative-link validation;
- semantic contract tests for bank-only quizzes, no-state initialization, urgent cadence, and source-quotation boundaries.

Related: [`localization.md`](localization.md) · [`skill-architecture.md`](skill-architecture.md) · [`agent-portability.md`](agent-portability.md).
