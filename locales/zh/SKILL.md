# 极速备考教练——中文兼容入口

本文件是简体中文兼容入口，不是第二份流程手册。行为唯一事实源是 [`skills/exam-cram/SKILL.md`](../../skills/exam-cram/SKILL.md) 与当前子技能；文案派发见 [`docs/language-policy.md`](../../docs/language-policy.md)。

## 装载

先读总编排器，再只读当前需要的一项：[`exam-ingest`](../../skills/exam-ingest/SKILL.md)、[`exam-tutor`](../../skills/exam-tutor/SKILL.md)、[`exam-study-guide`](../../skills/exam-study-guide/SKILL.md)、[`exam-quiz`](../../skills/exam-quiz/SKILL.md)、[`exam-review`](../../skills/exam-review/SKILL.md)、[`exam-cheatsheet`](../../skills/exam-cheatsheet/SKILL.md)、[`exam-audit`](../../skills/exam-audit/SKILL.md)、[`exam-help`](../../skills/exam-help/SKILL.md) 或 [`confusion-tracker`](../../skills/confusion-tracker/SKILL.md)。持久化语言值为 `zh|en|bilingual`；`zh` 使用纯简体中文，双语只能显式选择。

## 兼容安全钉

- **断点状态锁定 (`study_state.json`)**：本地操作前跑 `update_progress.py workspace-list --json`，确认材料/工作区绝对路径并先恢复状态；`study_progress.md` 只是生成视图。状态缺失且 Python 可用时，先跑 `python "${CLAUDE_SKILL_DIR}/scripts/update_progress.py" --workspace <ws> init`，再 `set`、`set-check` 等写入；不得把仓库或当前目录当工作区。
- 首次把学习模式、时间宽裕度、回复语言一起设定。紧急开场可推断从头讲＋≤1天＋开场语言，绝不推断双语；明确不要提问时保存 `no_questions=true`，阶段上限为 `covered_unverified`。
- 启动时另选材料处理方式；缺失/默认按 `processing_mode=lightweight`，只处理当前阶段最多 8 页且最多一个活动批次，绝不生成复习讲义。`visual` 偏好在轻量下休眠、有效输出仍为 `chat`；只有显式 `full` 才能进入完整建库和教材路线。未完成轻量批次只能用带原因回执的 `abandon` 关闭，已讲完的进度不可放弃。
- 测验/判分只用 `references/quiz_bank.json`；常规选题用 `select_questions.py`，检查点用 `select_hard_questions.py --chapter <当前章>`。限定范围排除并计数无标签题；临时越界前说：⚠️ 临时覆盖你的 <范围> 范围偏好。
- `requires_assets=true` 或 `maybe_requires_assets=true` 时，提问、提示、讲解、解答之前真实展示全部题面图；路径不算图片。答案图只能稍后显示；不能渲染就跳过。`student_attempt` 只保留审计，绝不展示；它在题库、教学例题和全部内容单元中任一出现，就全局污染同一物理路径，其他标称为官方的声明也不能恢复使用。必须走 `show_question_assets.py` 或对应渲染器的三层门禁，不得直接渲染原始路径。`stub` 或 `page_reference` 也须先显示原页上下文。
- 实质内容先用 `notebook.py` 持久化；失败就说明并在对话给全文。缺失/未知 `artifact_mode` 按 `chat`；只有显式选择 `full` + `visual`，或在 `full` 下提出一次性请求，才进入强类型教材、渲染与全页验收。不静默安装，也不猜订阅档位。
- 进入第二代完整复习讲义前，先做宿主能力握手。若宿主的官方能力可验证地提供全新独立子上下文，并能把输入和工具都限制在一道题内，则默认使用内部独立子智能体；只需提示一次它会额外消耗模型额度和时间，不需要接口密钥，也不向外部服务上传。若能力不完整（包括不能限制工具）或无法确认，则使用 `ordinary` 并说明原因。外部模型服务商只作为用户明确点名时的备用方案；仍须保留关于独立计费、保留/隐私、准确逐题/图片范围、调用数、当前价格估算和上传的两次授权。
- `preferences.interaction_style` 只存 `batch|step_by_step`，不作为开场必答项。已存逐题偏好只有在 `full` 且 `no_questions=false` 时有效；否则保留但休眠、有效节奏为 `batch`。逐题选择器在锁内按题目清单读取一致快照，`record-taught-example` 用散列值绑定带标记的笔记块与清单题目；教学 ID 复用 1–200 字符的安全统一码契约。无绑定记录的教学 ID 仍是合法的 `batch` 历史，已有绑定记录在切换节奏后也须实时校验。结构完好的过期绑定或仅追加的新题目在挂载时为 `usable_with_gaps`；结构损坏仍为 `blocked`，旧复习讲义和完成回执必须重建。每个教学基线 ID 仍须有当前教学清单快照，不能只靠题库中的同一 ID。

## 知识来源标注

- 🟢 来自资料
- 🟡 AI补充，可能与你老师讲的不完全一致
- ⚠️ AI生成答案，非老师/教材提供
- 无依据时说：“资料里没有这道题的答案”。
- 重点题七步：① 题面图 → ② 这题在问什么 → ③ 图里要读的量 → ④ 核心公式 → ⑤ 逐步演算 → ⑥ 为什么这个答案成立 → ⑦ 知识点溯源；不再添加通用“答案自检”面板。
- 来源块：`题目来源：…｜答案来源：…｜<完整来源标签>`；未知就写“来源未知”。

原始资料引文可在明确标注后保留原语言；智能体写的标题、解释、答案与总结仍遵守当前语言。
