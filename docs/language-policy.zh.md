# 语言策略

中文 · [English](language-policy.md)

本项目采用英文控制层，并提供两套面向学生的文案包。根目录的 [`SKILL.md`](../SKILL.md) 是语言中性的路由器；行为规则位于 [`skills/exam-cram/SKILL.md`](../skills/exam-cram/SKILL.md) 及其子技能中。`locales/zh/` 和 `locales/en/` 分别包含简体中文或英文文案、模板与精简兼容索引，绝不包含相互竞争的工作流逻辑。

## 1. 语言状态与分派

`study_state.json.language` 只持久化 `zh`、`en` 或 `bilingual`。`中文`、`English`、`双语` 和旧值只作为迁移/显示别名接受，不会成为新的存储值。首次接触时一并设置学习模式、时间预算和语言；除非开场使用中文，否则默认使用英文。紧急开场会推断开场所用语言，但绝不会推断双语；之后切换语言会从下一条回复开始生效。

| 值 | 文案 | 呈现方式 |
| --- | --- | --- |
| `zh` | `locales/zh/skills/<skill>.md` | 仅简体中文（`中文`） |
| `en` | `locales/en/skills/<skill>.md` | 仅英文（`English`） |
| `bilingual` | 两者 | 每个中文块后紧跟对应的 `> EN:` 镜像（`双语`） |

## 2. 单语纯度

- `zh`：智能体为学生撰写的正文中不得包含英文句子。
- `en`：智能体为学生撰写的正文中不得包含中日韩文字。
- `bilingual`：组合两个保持纯净的语言块；它不是第三套翻译包。
- 代码、路径、命令、JSON 键、数学表达式/单位、题目 ID 和 emoji 可以保持语言中性。

### 原文引用例外

逐字引用的原题、引文或教师答案，只有在明确标记为 `Original-language quotation` / 「原文引用」时才能保留原语言。此例外绝不适用于智能体撰写的标题、过渡语、解释、通知、生成答案或总结。

## 3. 英文规范词汇

英文输出必须使用下列字节完全一致的形式：

| 类别 | 中文规范形式 | 英文规范形式 |
| --- | --- | --- |
| 来源标记 | 🟢 来自资料 | 🟢 From your materials |
| 来源标记 | 🟡 AI补充，可能与你老师讲的不完全一致 | 🟡 AI-supplemented — may differ from what your teacher taught |
| 来源标记 | ⚠️ AI生成答案，非老师/教材提供 | ⚠️ AI-generated answer — not from your teacher or textbook |
| 逐题精讲 | ① 题面图 | ① Question figure |
| | ② 这题在问什么 | ② What's being asked |
| | ③ 图里要读的量 | ③ What to read off the figure |
| | ④ 核心公式 | ④ Core formula |
| | ⑤ 逐步演算 | ⑤ Step-by-step solution |
| | ⑥ 为什么这个答案成立 | ⑥ Why this answer works |
| | ⑦ 知识点溯源 | ⑦ Source trace |
| 来源块 | `题目来源：…｜答案来源：…｜<标签>` | `Question source: … \| Answer source: … \| <label>` |
| 未知来源 | 来源未知 / 来源页未知 | Source unknown / Source page unknown |
| 可选收尾 | 易错点 / 3分钟速记 / 现在轮到你 | Common pitfalls / 3-minute mnemonic / Your turn |
| 记录回执 | 已记录到错题本 / 已记录到疑难点 | Recorded to the mistake archive / Recorded to the confusion log |
| 阶段引用 | 阶段 N / 从阶段 N 继续 | Stage N / Resuming from Stage N |
| 如实弃答 | 资料里没有这道题的答案 | The materials do not contain an answer to this question. |
| 范围覆盖 | `⚠️ 临时覆盖你的 <范围> 范围偏好` | `⚠️ Temporarily overriding your <scope> scope preference` |
| 资源 | 题面图 / 答案图 | Question-side asset / Answer-side asset |
| 进度面板 | 备考科目 / 当前复习 / 进度打卡 / 错题累积 | Subject / Current stage / Progress / Mistake log |

来源行必须以一句完整的来源标记说明结束，绝不能只放一个 emoji。英文使用 `|`，中文使用 `｜`。可选收尾只在用户提出要求或已存储相应偏好时出现。绝不能编造未知的文件名或页码。

可打印 Study Guide 使用另一套低噪声约定：在开头图例中把每个来源 emoji 及其完整含义恰好解释一次，之后只在相关段落/连续内容的末尾放置 emoji。来源相同的连续段落共用一个末尾标记。这种显示压缩绝不能删除完整的强类型来源 sidecar 或回执。

## 4. 三层契约：持久化值 / 判定层词汇

1. **机器 schema：** JSON 键、稳定 ID、问题/补丁状态、原因代码、CLI 命令和自动化 JSON 保持其定义的拼写；绝不能翻译 `issue_id`、`content_unit_id`、`pending`、`validated` 或 `applied` 等 token。
2. **规范值：** 新的状态写入使用 `from_scratch|shore_up|fill_gaps`、`le1d|d1_3|d3_7|gt7d`、`zh|en|bilingual` 和已记录的状态代码。历史显示值只能作为迁移输入或生成视图中的文案。
3. **人类视图：** 对话、notebook 正文、Guide、回执和总结使用所选语言。遇到没有本地化的兼容视图，应当用所选语言重新表述，不能直接把它粘贴成面向学生的正文。

`notebook.py` 接受全部三个规范值。在 `bilingual` 模式下，智能体撰写的正文保留规定的中文块及其 `> EN:` 镜像，而同一条持久化记录的元数据和派生索引使用精简的 `中文 / English` 标签；调用方不得仅为绕过双语 CLI 错误而强制传入 `--lang zh`。

语言切换会使此前语言的强类型 Guide、HTML/PDF、回执和 QA 过期：应根据来源重新本地化/撰写、重新导入；如果请求视觉输出，还要重新渲染并再次执行逐页 QA。`≤1天` 可以缩短双语块，但绝不能省略任何一侧。脚本自动化 JSON 保持机器稳定；面向学生的消息使用 locale 目录。

## 5. 归属

控制层激活、执行顺序、schema、退出码、路径安全、题库/资源门禁、状态写入和防编造规则只在 `skills/*/SKILL.md` 中定义一次。面向学生的文案、标签、回执和模板位于 `locales/<lang>/`；语言包不能改变工作流、回退策略、来源规则或完成规则。

## 6. 网页兼容

网页提示词把粘贴进来的状态视为只读，并返回可复制的面板。它们只能从已挂载的题库出题；没有题库时完成状态最高只能到 `covered_unverified`；缺少题面资源时必须失败关闭；绝不能声称已经完成本地写入。

## 7. 维护检查

两套语言包必须同步更新，并运行规范路由、中英文纯度、名册/消息一致性、相对链接、仅题库出题、状态初始化、紧急模式和原文引用契约测试。另见：[智能体兼容性](agent-portability.zh.md)。
