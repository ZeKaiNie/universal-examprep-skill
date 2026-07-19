# 智能体兼容性

中文 · [English](agent-portability.md)

行为规则位于 `skills/`；`AGENTS.md` 是精简回退入口。安装时要包含完整运行时，即 `scripts/`、`locales/`、`docs/` 和 `prompts/`。根目录的 `SKILL.md` 是语言中性的；支持技能的宿主可以从 `skills/exam-cram/SKILL.md` 进入。概念疑难追踪位于 [`skills/confusion-tracker/SKILL.md`](../skills/confusion-tracker/SKILL.md)。英文兼容入口是 [`locales/en/SKILL.md`](../locales/en/SKILL.md)；本中文阅读版对应的中文入口是 [`locales/zh/SKILL.md`](../locales/zh/SKILL.md)。

## 安装与宿主入口

具备终端和网络访问能力的智能体，可以在用户授予所需的命令执行、网络访问和工作区外写入权限后从 GitHub 安装。只有当宿主无法克隆仓库时，才使用 ZIP 下载作为回退方案。

| 宿主 | 支持的安装/入口方式 |
| --- | --- |
| [Codex](https://learn.chatgpt.com/docs/agent-configuration/skills.md) | 克隆到 `$CODEX_HOME/skills/universal-exam-cram-coach`，然后重新加载技能；从 `SKILL.md`、`skills/*` 或 `AGENTS.md` 进入。 |
| [Claude Code](https://code.claude.com/docs/en/slash-commands) | 克隆到 `~/.claude/skills/universal-exam-cram-coach` 或 `.claude/skills/universal-exam-cram-coach`；从 `SKILL.md` 或 `skills/*` 进入。 |
| [Cursor](https://cursor.com/docs/skills) | 克隆到 `~/.cursor/skills/universal-exam-cram-coach`、`~/.agents/skills/universal-exam-cram-coach` 或相应的项目目录。 |
| [Windsurf](https://docs.windsurf.com/zh/windsurf/cascade/skills) | 克隆到 `~/.codeium/windsurf/skills/universal-exam-cram-coach`、`.windsurf/skills/universal-exam-cram-coach` 或 `.agents/skills/universal-exam-cram-coach`。 |
| [Gemini CLI](https://geminicli.com/docs/cli/skills/) | 运行 `gemini skills install https://github.com/ZeKaiNie/universal-examprep-skill.git`，然后重新加载技能。 |
| [Antigravity](https://antigravity.google/docs/skills) | 克隆到 `~/.gemini/config/skills/universal-exam-cram-coach` 或 `.agents/skills/universal-exam-cram-coach`。 |
| ChatGPT / Claude 网页版 | 使用 [`prompts/web_prompt.md`](../prompts/web_prompt.md) 或 [`prompts/web_prompt.en.md`](../prompts/web_prompt.en.md)；不得声称完成了网页宿主无法执行的本地写入。 |

可复制给具备网络能力智能体的通用指令：

```text
请把 https://github.com/ZeKaiNie/universal-examprep-skill 的最新版 Agent Skill 安装到你官方支持的用户级或项目级技能目录。运行终端命令、使用网络或写入工作区以外的位置前先征求同意。安装后加载该技能，并报告安装路径和版本；不要只是把它下载下来。
```

课程包含 PDF、公式或题目图片时，优先使用宿主的桌面应用或 IDE 界面。终端仍适合安装和诊断，但普通终端对话视图可能无法稳定呈现本地图片或可点击链接。这只是一项建议，并不声称每个桌面宿主都支持所有富媒体格式。

## 原生逐题子智能体能力

首选的隔离解释路线使用当前宿主自己的子智能体，不需要单独的 API key。只有当当前宿主既能启动全新或独立的子上下文，**又**能把该子智能体的实际输入和工具限制在一个准确的单题数据包内时，才能默认启用。否则使用普通撰写流程；绝不能把普通轮次标为隔离，也绝不能自动切换到外部 Provider。

| 宿主 | 文档中说明的边界 | 逐题解释的默认方式 |
| --- | --- | --- |
| [Codex](https://learn.chatgpt.com/docs/agent-configuration/subagents.md) | 原生 subagent；针对准确数据包使用不继承历史的子智能体，并采用受限/只读的 agent profile。 | 上述控制实际生效时使用原生隔离。 |
| [Claude Code](https://code.claude.com/docs/en/sub-agents) | 非 fork 的自定义 subagent 拥有独立上下文，并可设置明确的工具 allowlist。其 system prompt 仍然存在，通用智能体也可能加载项目指令。 | 只有使用专门的最小化 subagent 时才采用原生隔离。 |
| [Cursor](https://cursor.com/docs/subagents) | Subagent 拥有干净上下文，但继承父智能体的工具。 | 项目要求严格禁用工具边界时使用普通模式。 |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli/blob/main/docs/core/subagents.md) | Subagent 使用独立上下文循环，并可设置受限工具列表。 | 受限子智能体可用时使用原生隔离。 |
| [Antigravity](https://antigravity.google/docs/cli-features) | 提供独立 subagent 会话，宿主可以控制子智能体工具。 | 上述控制实际生效时使用原生隔离。 |
| [Windsurf](https://docs.windsurf.com/zh/windsurf/cascade/skills) | 尚未确认存在适用于本场景的官方通用干净上下文子智能体契约。 | 普通模式。 |
| 其他宿主或纯网页宿主 | 能力未知或不可用。 | 普通模式。 |

每个原生子智能体只接收固定的零基础优先指令、目标语言、准确原题、存在时的官方答案，以及仅限当前目标题的题面/答案资源。它不能接收其他题目、课程 wiki、notebook、主对话历史、文件系统、网络或工具。宿主只导入结构化的解释和 coverage 结果。这一边界能减少意外上下文泄漏；它是宿主声明，不是加密级沙箱证明。原生调用仍会消耗宿主账户的模型额度、时间和 token。

## 文件型宿主

只有在学生确认了准确、相互独立的材料/工作区路径以及三个学习选择后，才能运行：

```bash
python scripts/exam_start.py status --materials <dir> --workspace <ws> --json
python scripts/exam_start.py confirm --course <name> --materials <dir> --workspace <ws> --mode <mode> --time-budget <tier> --language <zh|en|bilingual> --processing-mode lightweight --json
python scripts/lightweight_session.py init --materials <dir> --workspace <ws> --json
python scripts/lightweight_session.py plan --materials <dir> --workspace <ws> --chapter <current-phase> --source <relative.pdf|png|jpg|jpeg|bmp> --pages <range> --json
# Only when an official answer is in another source/page:
python scripts/lightweight_session.py register-answer-dependency --materials <dir> --workspace <ws> --batch-id <id> --source <relative.pdf|png|jpg|jpeg|bmp> --pages <range> --json
# To replace/narrow or remove that planned dependency without erasing audit history:
python scripts/lightweight_session.py set-answer-dependency --materials <dir> --workspace <ws> --batch-id <id> --source <relative.pdf|png|jpg|jpeg|bmp> --pages <exact-range> --reason <concrete-reason> --json
python scripts/lightweight_session.py remove-answer-dependency --materials <dir> --workspace <ws> --batch-id <id> --source <relative.pdf|png|jpg|jpeg|bmp> --reason <same-reason-on-retry> --json
# Host renders/imports the exact visual manifest, teaches and persists notebook/chNN.md#anchor:
python scripts/lightweight_session.py record-visual --materials <dir> --workspace <ws> --batch-id <id> --manifest <json> --json
# If the unfinished scope must be closed before teaching:
python scripts/lightweight_session.py abandon --materials <dir> --workspace <ws> --batch-id <id> --reason <concrete-reason> --json
python scripts/lightweight_session.py mark-taught --materials <dir> --workspace <ws> --batch-id <id> --notebook-entry notebook/chNN.md#anchor --taught-item-ids <id1,id2,...> --json
# If already-taught evidence must be redone without erasing history:
python scripts/lightweight_session.py replace-taught --materials <dir> --workspace <ws> --batch-id <id> --reason <concrete-reason> --json
# Routine status is metadata/identity-only; request stream hashes explicitly when needed:
python scripts/lightweight_session.py status --materials <dir> --workspace <ws> --verify-live --json
# Only after an explicit full-build choice:
python scripts/exam_start.py confirm --course <name> --materials <dir> --workspace <ws> --mode <mode> --time-budget <tier> --language <zh|en|bilingual> --processing-mode full --json
python scripts/ingest_course.py --materials <dir> --workspace <ws> --json
```

轻量模式下成功执行 `init` 会创建并进行安全检查的工作区本地 `.lightweight/assets/` 目录。宿主可以立即把请求的页面图、联系表或裁剪 PNG 写入其中；不得依赖未记录的手动建目录步骤。

`confirm` 会原子写入路径配对确认、状态和运行时回执；后续门禁会重新验证它们。省略 `--processing-mode` 会保留现有选择，新初始化的工作区则安全地默认为 `lightweight`。宿主包装器使用相同的 `None`/保留契约。只有明确选择 `full` 才会开放 ingestion。编排器和更底层的工作区 builder/compiler 发布过程都会重新检查准确的已注册路径配对、当前运行时回执、学习选择和 `processing_mode=full`；直接调用 `build_raw_input_from_workspace.py` 或 `ingest.py` 无法绕过该门禁。不是工作区发布的独立 builder 输出仍只是兼容工具。核心路线覆盖 PDF/DOCX/PPTX/XLSX/raster/txt/Markdown，并如实使用 PDF 页、PPTX 幻灯片、XLSX 工作表、DOCX 逻辑段和 raster 页等价锚点。

授课节奏是独立于处理模式和答案解释模式的第三项状态控制，但它是可选项，不是第四个启动选择。`preferences.interaction_style` 只存储 `batch|step_by_step`；省略 `--interaction-style` 会保留原值，新状态或缺少该字段的旧状态按 `batch` 处理。已存储的逐题偏好只有在 `processing_mode=full` 且 `no_questions=false` 时才会生效；其他情况下有效节奏为 `batch`，该偏好仍被保留但处于休眠。实际逐题节奏会调用 `list_teaching_examples.py --next-pending`；它在同一个工作区锁内读取 manifest/state/notebook/baseline，并返回 manifest 中第一道待讲题。宿主先通过 `notebook.py add-entry --teaching-example` 写入完整七步精讲，然后调用 `update_progress.py record-taught-example`，而不是执行两次松散的证据写入。该命令会在普通 ID/锚点证据旁记录 `{id, notebook_ref, notebook_block_sha256, manifest_item_sha256}`。未绑定的教学 ID 仍是有效的批量模式历史；已经绑定的 ID 在节奏切换后仍会接受实时验证。Guide notebook 发布会保留有效且已绑定的标记块，并拒绝过期 binding 或没有 binding 的 marker。teaching baseline 中的每个 ID 都必须在当前 teaching manifest 中有对应快照；仅存在于 quiz 的题目不能替代。notebook 中出现内容，以及用户说“继续”或表示理解，都不能生成完成证据；轻量模式继续使用自己独立的页面批次状态机。selector 锁只能保证一致快照，不能完成预约，因此并发宿主仍可能拿到同一道待讲题。

轻量 `plan` 只接受当前阶段、PDF 页或一个可以确定为单帧的 PNG/JPEG/BMP，主页面最多八页，并且同时最多只能有一个活动批次。单页工作单的 `contact_sheet_groups=[]`，直接使用页面资源。多页批次中，宿主创建只用于概览的联系表，把主页面恰好各划分一次，每组最多四页，每个 tile 约 768 px。新视觉回执使用 schema 3。每个主页面列出稳定的 `teaching_item_ids`；每个题目分别声明 `kind=text|figure|mixed`、一个或多个 prompt components，以及零个或多个 answer components。这样，即使纯文本题的官方答案位于另一个文件中，也不会把它错误标成图片题。组件声明 role、排序后的 `required_context_ids`、准确的 `allowed_detected_item_ids` 和带来源限定的裁剪 binding。组件可以是目标加上下文，也可以是非空的纯上下文组件，但每道题至少有一个 prompt component 必须包含目标。detail 调用只能为同一个目标组合多个 prompt components，solution 调用只能为同一个目标组合 answer components。每个组件都单独接受一次单裁剪 `crop_review` 模型调用；检测到的 ID 必须与声明允许的 ID 完全一致，并且必须报告不存在无关内容或学生作答。仅有 bbox 或文件名不能作为语义纯净证据。

每个主页面/依赖页面都声明 `content_types` 和 `answer_provenance=student_attempt|official_solution|none|unknown`。官方答案位于另一个来源时，在批次仍是 planned 状态期间，使用增量式 `register-answer-dependency` 只绑定准确的额外页面。`set-answer-dependency` 替换或缩小一个来源的准确页集；`remove-answer-dependency` 删除它。两者都会写入受哈希约束的历史，并且完全相同的重试不会追加重复事件（重试删除时必须重复使用已经记录的原因）。这些渲染页面只能作为定位/detail 上下文，本身绝不能进入 solution 调用。只有分类为 `official_solution` 的页面才能提供声明范围内的 answer component 裁剪；student-attempt 或 unknown 页面仍可作为上下文检查，但绝不能满足官方/材料答案证据。每个已注册并声明为 `official_solution` 的页面都必须贡献至少一个 answer component；支持多个页面和多个组件。每张 page/contact/prompt/answer/dependency 图片都必须是 `.lightweight/assets/` 下的规范 PNG，PNG 签名和实测尺寸一致；轻量证据不能复用完整建库资源路径，prompt/answer 裁剪也必须与页面、联系表和彼此使用不同路径。`mark-taught` 要求唯一且持久的 `notebook/chNN.md#anchor`，以及视觉回执列出的准确、排序后 `taught_item_ids`。它会单独记录 `inspected_pages`，重新验证准确的实时来源与视觉字节，并在工作区锁下发布 `phase_evidence.lightweight_batches`。如果 taught 回执先于进度文件提交，重新运行命令会幂等修复该事件。`replace-taught` 在保留 successor 中准确依赖页的同时重新验证依赖修订版本。Schema-2 视觉回执始终是不可变历史。旧 schema-2 `visual_ready` attempt 会被隔离，不能 record/teach，但可以留下审计记录地 abandon；它绝不会静默升级，新 attempt 必须生成 schema 3。

常规 `status` 使用代次稳定的只读快照，既不创建也不打开写锁。工作区验证同样只执行有边界的元数据和物理身份检查；两条路径都不会对当前或活动来源/资源进行 stream hash。其 `full_page_answer_taint_status` 保留未裁剪定位/detail 页面的保守出处状态。独立的 `answer_taint_status`、`item_crop_review_status` 和 `teaching_publication_status` 描述已审查的单题裁剪与持久教学发布，因此不会仅因为干净且有官方答案的题目所在母页还含有学生作答，就把该题报告为 blocked。
只有 `plan`、`register-answer-dependency`、`record-visual`、`mark-taught`、阶段完成或显式 `status --verify-live` 才会重新计算准确的 stream hash。非当前阶段的 taught 批次只会针对其不可变回执/事件接受结构检查，并计为 `unchecked_historical`；回到相应阶段后，它会重新进入当前实时范围。

如果 `planned|visual_ready` 批次必须在授课前关闭，`abandon` 要求具体原因并保留受 digest 约束的原状态回执。它会释放唯一的活动槽位；之后为同一材料切片执行 plan 会得到新的 attempt ID。abandoned 记录绝不会删除，也不会计为已覆盖；`taught` 批次不能 abandon。`replace-taught --reason` 是唯一的已讲重做路线：它把旧 attempt 及其准确进度事件保留为不可变 `superseded` 历史，从当前完成分母中排除这个 predecessor，并为相同主来源/章节/页面切片创建 planned successor。

轻量初始化还会为任何预先存在的 `references/quiz_bank.json` 捕获不可变、只含 stat 的 baseline；启动时不会解析或计算题库哈希。只有明确的选题/checkpoint 操作才会打开它，并为题库和合格题目创建修订版本 binding。轻量完成会跳过强类型 Guide/完整建库证据，最高可达到 `covered_unverified`；达到 `verified` 需要来自该未变更的预存 baseline 的两个不同、已处理 checkpoint 行，其中至少一个通过，并且每个合格行都要有准确的 `bank_binding_id`/`bank_sha256`/`item_sha256`。初始化时不存在、之后被替换/漂移，或只有旧版未绑定行的题库，都不能支持 `verified`。

授课节奏是可选项，不是第四个启动选择。省略它会保留现有值（新状态/缺失状态为 `batch`）；也可以向 `confirm` 传递 `--interaction-style step_by_step`，或运行 `update_progress.py --workspace <ws> set --interaction-style step_by_step`。已保存偏好只有在明确的完整建库路线且 `no_questions=false` 时才生效；否则它处于休眠，有效节奏为 `batch`。缺失、旧版或无效的 `processing_mode` 会失败关闭到轻量模式，因此绝不会隐式激活逐题完整建库行为。逐题宿主从同一个加锁的 manifest/state/baseline/notebook 快照中选题，用 `notebook.py add-entry --teaching-example` 写入第一道待讲题的精讲，然后用 `update_progress.py record-taught-example` 绑定 ID、锚点、notebook 块哈希和 manifest 题目哈希。Quiz/teaching/notebook/Guide ID 共同遵守安全 Unicode、不超过 200 字符的契约。未绑定 ID 仍保留为批量模式历史。缺失记录和 anchor/marker/hash/revision 漂移会变为等待有序修复；不安全的文件系统拓扑、无效 UTF-8/fence/block、schema/重复/共享引用/名册外证据仍是 fatal。只有可恢复或新名册题目待处理的已完成完整阶段会以 `usable_with_gaps` 挂载，但 Guide 和完成状态仍保持严格。保留的 baseline 中每个 ID 都必须在同一规范章节、准确 `policy=append_only` 下有当前教学快照；仅 quiz 题目不能替代。

如果在发布 `.ingest/material_build_pending.json` 后 ingestion 中断，随后运行时回执丢失或漂移，不要重新运行普通 `confirm`，也不要删除 blocker。必须明确选择：

```bash
python scripts/exam_start.py recover-material-build --materials <dir> --workspace <ws> --action resume --json
# If the builder now produces different bytes and resume refuses with zero publication:
python scripts/exam_start.py recover-material-build --materials <dir> --workspace <ws> --action supersede --json
python scripts/ingest_course.py --materials <dir> --workspace <ws> --json
```

`resume` 只允许准确的当前 generation；已完整绑定的来源会跳过重新解析，不完整的 blocker-first 状态只有在能重现相同 generation 时才能重建。`supersede` 创建经过审计的 schema-2 successor，并把每个 predecessor 关闭到其直接 child。恢复日志有界（每个 generation 最多 64 个事件、64 条祖先边，以及包含一个当前完成行在内最多 65 行回执），并由回执和 manifest 以事务方式绑定。

Ingestion-v2 要求每个来源都有一条本地核心 parser 回执，绑定修订版本/配置/位置计数以及 `network/upload/install=false`；准确 schema 见 [`file-format.md`](file-format.md)。Docling/MinerU 不属于这一本地路线：只有用户明确点名请求，并且通过单独配置的远程/云端宿主披露上传/隐私条款后，才能提供。学生运行时绝不能探测、下载、安装、导入、执行这两个重型解析器，也不能接受二者的本地可调用 runner。

退出码 `0` 表示 `ready` 或已披露的 `usable_with_gaps`；`10` 表示工程流程已完成但内容 blocked，因此仍禁止教学/测验/完成；其他非零值表示操作失败，绝不能解释成“没有 Python”。强类型接管只使用 `ingest_review.py list/show/claim/validate-patch/apply/apply-batch/mark-unrecoverable/rebuild`；批量 apply 对每个 issue 保留一份已验证 patch。绝不能手工编辑 ledger、事实文件、wiki 或题库。来源/patch 改变后要重建并验证。

当已注册的工作区/运行时/完整处理门禁过期或受阻时，`validate_workspace.py --json` 也会在 CLI 边界失败关闭：它返回结构化的 `readiness=blocked`、fatal errors、受阻能力原因 `full_processing_gate_blocked` 和退出码 `2`，而不是泄漏 Python traceback。

## 教材与宿主延展能力

缺失/未知的 `artifact_mode` 按 `chat` 处理。`artifact_mode` 始终是独立且持久的偏好：如果 `processing_mode=lightweight` 时其值为 `visual`，status/readiness 会报告 `artifact_mode_preference=visual`、`artifact_mode_effective=chat` 和 `artifact_mode_dormant=true`。该偏好会保留到日后明确切换到 `full`；轻量模式绝不进入 Study Guide 撰写/渲染。在完整模式下，明确持久选择 `visual` 或单次请求可以进入相应的 [`PDF 能力路线`](pdf-capability-adapters.md)；任何模式都不允许静默安装。结构化完成要求当前完整模式的强类型 Guide。视觉交付还要求哈希匹配、逐页 QA、没有未解决缺陷且 `artifact_ready=ready`；语言改变会使此前产物过期。

`answer_explanation_mode` 是另一个独立的宿主边界。普通撰写上下文始终为每道题提供面向零基础学习者的详细解释。只有宿主通过上面的原生子智能体门禁时，有效值才可以默认为 `isolated`；随后每道题都会获得一个全新且受限的子智能体，结果进入规范回执链。该原生路线不需要额外 API key，也不需要 Provider 上传授权。没有通过门禁的宿主必须保持 `ordinary`，且不得伪造隔离回执。

单独的 OpenAI API 实现是必须由用户明确请求的回退方案，不是默认隔离路线。它把凭据保留在学生工作区之外，并继续采用两阶段授权边界：先在披露 Provider/计费/隐私信息后进行不上传规划，然后在上传前对准确的题目/图片范围、调用次数、计划 ID 和有边界的价格估算取得授权。宿主订阅不等于 OpenAI API 计费，API key 也不等于上传授权。见 [`openai-study-guide-adapter.zh.md`](openai-study-guide-adapter.zh.md)。

Ingestion-v2 Guide 的 claim 会绑定准确的同一 unit refs，以及当前 guide/source/content/fact/parser-receipt 哈希；回执证明撰写文本的成员关系及位置/修订版本，不证明语义支持。Legacy/v1 不声称具备这一门禁。

明确请求的远程/云端 LangGraph 宿主可以实现可选的 [`LangGraph 契约`](langgraph-host-adapter.md)；本地模块只保留不依赖外部包的回执/路由辅助函数，以及会拒绝执行的 `build_exam_graph()`，而不是无法抵达的本地图实现。Checkpoints/interrupts 只包含有边界的路由契约，绝不成为课程事实；恢复时重新载入当前状态和回执。网页宿主不能声称执行了本地命令、`.ingest/` 或写入。宿主专用规则副本必须从 `AGENTS.md` 生成，或接受针对 `AGENTS.md` 的测试。
