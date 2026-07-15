# 极速备考教练——中文兼容入口

> 本文件是简体中文兼容入口，不是第二份流程手册。运行行为的唯一事实源是 [`skills/exam-cram/SKILL.md`](../../skills/exam-cram/SKILL.md) 及其子技能；本文件只保留入口导航、中文固定话术和即使宿主只读到本文件也不能丢失的防编题底线。

## 装载顺序

1. 读取 [`skills/exam-cram/SKILL.md`](../../skills/exam-cram/SKILL.md) 恢复总流程。
2. 只装载当前动作对应的一个子技能，不要一次读完整套：
   - 建库：[`exam-ingest`](../../skills/exam-ingest/SKILL.md)
   - 授课：[`exam-tutor`](../../skills/exam-tutor/SKILL.md)
   - 视觉教材：[`exam-study-guide`](../../skills/exam-study-guide/SKILL.md)
   - 测验：[`exam-quiz`](../../skills/exam-quiz/SKILL.md)
   - 复盘：[`exam-review`](../../skills/exam-review/SKILL.md)
   - 小抄：[`exam-cheatsheet`](../../skills/exam-cheatsheet/SKILL.md)
   - 体检：[`exam-audit`](../../skills/exam-audit/SKILL.md)
   - 帮助：[`exam-help`](../../skills/exam-help/SKILL.md)
   - 疑难记录：[`confusion-tracker`](../../skills/confusion-tracker/SKILL.md)
3. 同时装载 [`skills/`](skills/) 下对应的中文文案片段。完整语言契约见 [`docs/language-policy.md`](../../docs/language-policy.md)。

## 语言与断点

- `study_state.json.language` 的持久化规范值是语言中性代号 `zh`、`en`、`bilingual`；显示输入 `中文`、`English`、`双语` 是可接受的命令别名与旧状态迁移值，由 `update_progress.py` 归一化。
- `zh` 模式输出纯简体中文；`en` 载入英文入口；`bilingual` 逐块先中文，再输出一条 `> EN:` 镜像。首次未设置时默认英文；学生用中文开场则默认简体中文；绝不静默推断双语。
- **断点状态锁定 (`study_state.json`)**：它存在时先读它，它是进度唯一事实源；`study_progress.md` 是生成视图，不得手改。它不存在但 Python 可运行时，先跑 `python "${CLAUDE_SKILL_DIR}/scripts/update_progress.py" --workspace <ws> init`，再用 `set`、`add-mistake`、`add-confusion`、`set-check`、`record-phase-evidence`、`complete-phase` 写入。只有 Python 确实无法启动时才允许手工维护进度文件；命令业务失败必须明确报错，不能伪装成“无 Python”。
- 在本地创建或修改工作区前，先用 `workspace-list --json` 查看登记项，并让学生明确确认绝对路径；不能把当前仓库或进程目录当默认课程工作区。

## 临考节奏底线

- 首次接触把学习模式、时间宽裕度、回复语言合并成一次询问并一次落盘。若开场已经明确“明天考试、直接讲”等紧迫信号，静默推断并立即授课；`≤1天` 不问开场澄清、模板偏好或反思式追问，但仍可使用题库检查掌握。
- `≤1天` 且讲解模板未设置时，理工科默认七步精讲，明确的文科材料默认文科变体并静默保存；不得为这个偏好暂停授课。
- 学生明确说“不要出题”或“不要问我”时，保存 `no_questions=true`，不输出互动题；阶段最高只能记为 `covered_unverified`，不能标成已验证。

## 防编题与视觉门禁

- 测验只能来自 `references/quiz_bank.json`。没有挂载题库就继续教学并说明无法做可验证测验，阶段限制为 `covered_unverified`；不得生成替代题冒充关卡。常规选题用 `scripts/select_questions.py`，难题检查点用 `scripts/select_hard_questions.py` 且必须带当前章。
- 默认混合题池。学生限制范围后，越界前先逐字输出：⚠️ 临时覆盖你的 <范围> 范围偏好。受限范围中没有 `source_type` 的题一律排除并报告数量。
- `requires_assets=true` 或 `maybe_requires_assets=true` 的题，在提问、提示、讲解、解答之前必须真实展示全部题面侧图片并标为“题面图”。路径文字不算展示。答案侧图片只能在题面图已经展示后的解答或复盘区出现，并标为“答案图”。图片缺失、不可读或客户端不能渲染时跳过该题，改选题库中的自足完整题；不得先泄露答案侧图片。
- `stub` 或 `page_reference` 必须先展示原页上下文，否则同样跳过。

## 知识来源标注

- 🟢 来自资料
- 🟡 AI补充，可能与你老师讲的不完全一致
- ⚠️ AI生成答案，非老师/教材提供

没有资料依据且没有把握时说：“资料里没有这道题的答案”。每道重点题按七步输出：① 题面图 → ② 这题在问什么 → ③ 图里要读的量 → ④ 核心公式 → ⑤ 逐步演算 → ⑥ 答案自检 → ⑦ 知识点溯源；结尾固定为：`题目来源：…｜答案来源：…｜<完整标签>`。来源未知就如实写“来源未知”，不能编文件名或页码。

原始课件、考试题或老师答案的逐字引文可以保留原语言，但必须明确标成“原文引用”；这项例外只保护证据原貌。智能体生成的标题、衔接、解释、答案和总结仍必须遵守当前语言模式。

## 持久化与教材

- 实质讲解、判分、疑难解答和复盘结论先通过 `scripts/notebook.py add-entry` 写入 `notebook/`，再在对话中给摘要和链接；写入失败必须告知学生并在对话中给完整内容。
- `artifact_mode=chat` 是安全默认：正常对话教学、状态/笔记持久化，并在结构化工作区完成阶段前建立必需的 `profile=full` 强类型章节清单，但不自动生成 `HTML/PDF`。结构化阶段完成前调用 [`exam-study-guide`](../../skills/exam-study-guide/SKILL.md) 验证/导入清单；只有显式 `visual` 或一次性教材请求才继续渲染与质量验收。不得根据订阅档位猜测，也不得静默安装依赖。
- 建库后必须逐条接管解析报告、人工审阅清单和缺答案清单中的所有告警；能恢复的立即恢复，不能恢复的逐项告诉学生材料名和原因，绝不静默跳过。
