# 版本沿革 / Changelog

中文在前，English below.

> 运行时文本（`SKILL.md`、`README*.md`）只描述当前行为，版本历史集中记录在本文件。

## V5.1 — 2026-09-02 · 图片回到对话里

- **自动裁图**：`setup` 用 `pypdfium2` 读取 PDF 页面对象，把讲义里的矢量图/嵌入图按区域裁成 PNG（`figures/`），`next`、`chapter`、`ask` 在对应页下方列出路径；PPTX/DOCX 的嵌入图直接抽出；`figure <文件> <页> --crop` 可手动截任意区域。
- **题面图 / 答案图**：题目或解答所在的印刷区域含图时自动裁出，`quiz` 先给题面图，`check` 才给答案图；SKILL §4 要求把图真正放进对话而不是打印路径。
- **真实作业结构**：`hw2 (4)(1).pdf` ↔ `homework2solutions.pdf` 这类文件名自动配对；教材题号 `Problem 1.3.10` 与 `Problem 1.3.10 Solution` 按标号配对；扫描/手写页（学生自己交的作业）识别并跳过，绝不当作题面或答案；题干只有教材题号时明确提示“题干不在材料里”。
- **更快的 PDF 文本**：优先 `pypdfium2`（EEC 160 约 1000 页 3 秒），`pypdf` 仍可作后备。
- **弱模型实测工具**：`eval/agent_smoke.py` 可驱动 Antigravity 的 Gemini（flash_lite / flash / pro）或 Claude Code（Haiku）跑完整学习回合并打分（是否只用给定命令、是否引用页码、是否展示图片、是否标注来源）。
- 文档：`docs/feature-audit.md` 逐项核查 v4.3 功能在 v5 的状态。

### V5.1 (English)

Figures are back in the chat: `setup` crops vector drawings and embedded images from PDF pages with `pypdfium2` (plus PPTX/DOCX media), lists them under each slice, and crops the printed question / solution regions that contain a picture (question figure before asking, answer figure only when explaining). Real homework layouts are handled: `hw2 (4)(1).pdf` pairs with `homework2solutions.pdf`, textbook labels `Problem 1.3.10` pair with their solutions, scanned/handwritten pages are detected and skipped. `eval/agent_smoke.py` drives Antigravity (Gemini) or Claude Code headlessly through a session and scores the transcript.

## V5.0 — 2026-09-01 · 推倒重来：给普通学生用的版本

**为什么重写。** v4.3 的运行时有 14.4 万行 Python、495 个文件、36 个子命令、108 个参数；智能体开讲前要读约 14 万字符的技能说明，再跑 5 条脚本、自己把 PDF 逐页渲染成 PNG、做 contact sheet 和逐块 crop review。大部分代码在做回执、代际账本、哈希绑定和回滚事务，而不是教学生。小模型根本跑不动，大模型也要十几轮才能开讲。

**现在是什么。** 一份不到 1000 词的 `SKILL.md` + 一个 `coach.py`（约 1800 行，纯标准库，PDF 可选装 `pypdf`）：

- `setup <文件夹>` 一条命令约 1 秒完成整门课：读 PDF/PPTX/DOCX/MD/TXT/HTML，按“一个文件一章”或“文件内 第N章/Chapter N 标题”切章节，从作业/试卷抽题并与解答文件自动配对（如 `q1.pdf` ↔ `q1_sol.pdf`、`作业2.txt` ↔ `作业2答案.txt`），建 BM25 检索（英文分词 + 中文双字组），写出可读的 `chapters/chNN_标题.md`。
- `next` 按固定长度分段吐出带 `[文件 p.页码]` 锚点的原文，智能体只负责讲；`ask` 检索并在无命中时明确返回“资料没讲”（退出码 4）；`quiz` → `check` → `answer` 完成出题、看参考答案、记录；`done` 区分“已验证”（答对过本章材料题）与“已讲完”；`note`/`cheatsheet` 把章节总结、疑难点和错题拼成小抄；`status` 跨对话恢复。
- 小模型适配：`--slice` 控制每段长度；每条命令最后一行写明下一条命令；`--lang zh|en` 让 CLI 输出与学生语言一致；不要求模型写任何 JSON。
- 保留并简化的原有能力：三色来源标签、只从资料出题、错题/疑难点、进度持久化、小抄、双语。删除：完整建库/轻量按需双模式、Study Guide HTML/PDF 渲染与逐页 QA、crop 回执、答案污染契约、代际恢复、LangGraph/OpenAI/MinerU/Docling 适配、benchmark 矩阵、三层语言包、知识点窗口、3×4 模式矩阵、工作区注册表、运行时哈希回执。
- 测试从 1000+ 用例 / 12 分钟降到 6 个文件 / 约 1 秒；CI 仍覆盖 Ubuntu/Windows × Python 3.8/3.12。
- 样例：仓库内置一门中文《数据结构》三章 + 作业 + 模拟卷；`python samples/fetch.py` 下载 MIT 6.006 六讲讲义 + Quiz 1 与官方解答、耶鲁 PSYC 110 四讲文字稿。实测记录见 `docs/v5-refactor.md`。

**兼容性。** v4 工作区（`references/wiki`、`.ingest/`、`study_state.json` v1）不再被读取；对原资料夹重新运行 `setup` 即可，约一秒。

### V5.0 (English)

Ground-up rewrite. v4.3 had grown to 144k lines of Python, 495 files, 36 sub-commands and 108 flags; an agent had to read ~140 KB of skill text and run five scripts, then render PDF pages to PNG itself, before teaching anything. Most of that code produced receipts, generation ledgers, hash bindings and rollback journals rather than teaching.

v5 is a <1,000-word `SKILL.md` plus one `coach.py` (~1.8k lines, stdlib; `pypdf` optional for PDF text). `setup` ingests a whole course in about a second (PDF/PPTX/DOCX/MD/TXT/HTML → chapters → questions paired with solution files → BM25 index with CJK bigrams). `next` streams material slices with `[file p.N]` anchors; `ask` retrieves or says the materials do not cover it; `quiz`/`check`/`answer`/`done` run the drill loop and distinguish *verified* from *covered*; `note`/`cheatsheet` build the handout; `status` restores across chats. Small-model friendly: `--slice`, next-command hints on every output, no JSON authoring. Removed: full/lightweight dual mode, Study Guide HTML/PDF and page QA, crop receipts, answer-taint contracts, generation recovery, LangGraph/OpenAI/MinerU/Docling adapters, the benchmark matrix, three-layer language packs, knowledge windows, the 3×4 mode matrix, workspace registry and runtime hash receipts. Tests: 1000+ cases / 12 min → 6 files / ~1 s. Old v4 workspaces are not read; re-run `setup` on the materials folder.

## V4.3 — 2026-07-18

- **入口与发行说明更正**：v4.3 中英文 README 与 Release Notes 现在保持双语；英文页语言切换恢复显示“中文”。安装说明改为各智能体可直接复制并自行联网安装的提示词，手动下载只作无网络备用；同时恢复完整 benchmark 图表、成本表和 Star History，并为每项延展功能给出 5 分制建议与可复制启用口令。
- **宿主内部逐题子智能体优先**：full-v2 Study Guide 在宿主可验证“每题全新独立上下文 + 单题输入/工具限制”时默认使用内部子智能体，无需另一把 API Key；首次说明额外额度/时间。能力不完整或无法确认则保持普通详解。另行计费的外部 Provider 降为用户明确点名后的备用路线，继续执行两阶段价格、隐私、范围和上传授权。
- **学生入口改写**：中英文 README 改为从“交什么材料、第一次选什么、每章会怎样讲”开始，用学生能直接执行的例子解释轻量按需、完整建库、普通功能与延展功能；中文正文移除不必要的英文工程术语，底层状态机、回执和适配器细节改由维护文档承载。
- **默认轻量按需学习**：启动时明确区分普通的 `lightweight` 与延展的 `full` 处理；默认只盘点文件、按当前章节分批视觉读取，保留题图、学习状态机与完整教学输出，同时不预建全库、不自动生成 Study Guide/PDF。MinerU、Docling 与 LangGraph 继续保持显式点名、远端托管且另行同意后才可用。
- **可恢复的逐题精讲节奏**：完整建库模式新增 `batch|step_by_step` 规范化选择；逐题模式严格按 manifest 顺序推进，用稳定题号、来源修订、notebook marker 与内容哈希记录完成证据。过期证据可审计地重开，结构损坏则失败关闭，不能用一句“继续”冒充学习完成。
- **隔离式答案详解与可选 OpenAI 适配**：Study Guide 的每道题都可生成只包含该题、答案和目标裁剪图的独立无工具请求，并以宿主收据绑定输入、附件和覆盖元数据；优先使用宿主内部独立子智能体，OpenAI 适配器默认关闭且只在用户明确要求、完成准确计划与上传授权后调用。
- **语义纯净裁剪与可验证教材**：题面/答案组件必须通过目标级裁剪复核，禁止整页夹带无关题目、答案或学生作答；逐字段 `claim → quote span → source unit`、材料代次、解释收据、双语翻译和可读公式共同进入 typed Guide 门禁。

- **材料代次显式恢复与可审计替代**：Pending generation 遇到 runtime receipt 缺失/漂移时，普通 `confirm` 不再形成互锁或覆盖 provenance；新增 generation-bound `recover-material-build --action resume|supersede`。`resume` 只允许同代精确重建，candidate 不同即零发布失败；`supersede` 通过 schema `2` 逐条绑定 direct predecessor。Generation-addressed 恢复日志、64-event/64-edge/65-receipt 上限、compiler 全事务回滚、receipt completion 与 manifest 精确保留键/hash 集合共同阻止静默丢代、shortcut ancestry 与崩溃后的半完成状态。
- **Builder→compiler 代际失败关闭**：`ingest_course.py` 只在 builder 成功时发布新一代，且在任何资产、raw input 或 parse report 变更前先写入 hash-bound `material_build_pending.json`；builder 返回非零时不覆盖 canonical parse report/raw input，也不发布候选资产，而发布回滚若不完整则保留 blocker。Pending 绑定旧 build manifest、新 raw/report、完整候选资产策略与迁移收据；普通 mutation/publication（含 review、claim 与 Guide）以及 validation 在 pending 期间全部 fail closed。编译器只接受实际角色差异与收据一一对应的 `answer_context → student_attempt`。
- **全编译器事务与可恢复意图**：material generation 的结构化事实、build manifest、wiki/题库/教学例题、检索索引、报告/计划及 pending→receipt 切换现在纳入同一个有界 `pending_ingest.json` 回滚事务。进程中断时 validator 拒绝读为 ready，下一次 mutation 先恢复全部登记 target 再继续；成功后写入 build-manifest schema `2` 的严格 `material_build` 契约与 raw/report/receipt 三元 hash，不得降格到 schema `1`。`ingest_course.py` 后置的 learner-state 初始化/偏好写入不在该编译事务内。
- **旧版作业截图角色迁移**：仅在路径、章节/题目归属、作业来源、嵌套 provenance、源文件与资产 SHA-256、实时字节及物理文件身份全部唯一且一致时，允许把旧版 `answer_context` 精确升级为 `student_attempt`；候选策略会被冻结并在资产处理后、首个 JSON 替换前重新审计，任何别名、hardlink 或并发漂移都失败关闭。
- **失败建库不再污染最后一次成功报告**：builder 返回非零时，`ingest_course.py` 不替换 canonical raw input、parse report 或公开资产；失败诊断只返回在当次命令 payload/stderr。Marker 仍只接受布尔值，成功却请求抑制发布、或使用非布尔值都会拒绝执行。
- **跨平台发行包可复现且边界受控**：`build_dist.py` 在压缩前把运行时文本统一规范化为 LF，并仅移除 Python tokenizer 标记为非语义布局的 `NL`；显著 token、AST、shebang 与编码声明均有回归保护。CRLF/LF checkout 因而生成同一份 ZIP。v4.3 同包交付默认轻量路径与按需启用的完整建库/严格 Guide 工具链，审计候选约 776 KiB，硬上限相应调整为 850,000 B；默认启动仍不加载或执行延展路径。

- **完整章节 Study Guide 门禁**：`profile=full` 现在以当前章全部 teaching examples、全部 bank 记录（含 `gradable=false` 教学项）和 typed question units 为去重分母；章节/语言、题面替代、答案 provenance、notebook、逐字段 claim 与 live source revision 均在导入和渲染前失败关闭。
- **来源与资产完整性**：统一按安全物理身份识别 hardlink/路径别名和全局 `student_attempt` 污染，绑定声明 SHA-256 与 live bytes；PNG/JPEG/WebP/GIF/BMP 使用共享严格解码校验，损坏图片不能进入建库、教学显示、Guide、QA 或小抄。
- **整批原子发布与代际一致性**：builder、visual index、Study Guide 与 cheatsheet 的多文件/图片发布新增预检、journal、回滚及故障注入覆盖；normal ingestion 将编译输入绑定到 builder 产出的精确 raw-input generation，避免两次锁之间混入另一轮建库结果。
- **可审计视觉教材**：题面图优先、答案图延后，完整题面图不再重复粘贴原文（双语时仅补目标语言翻译）；HTML/PDF 与逐页视觉 QA receipt 保持同一内容/资产快照，坏图、漂移或残留产物不再被误报为可交付。

## V4.2 — 2026-07-14

> 完整审查、设计与实施记录见 [`docs/history/plans/knowledge-ingestion-hardening.md`](docs/history/plans/knowledge-ingestion-hardening.md)。

- **结构化课程建库**：新增 `ingest_course.py` 作为 PDF/DOCX/PPTX/txt/Markdown 到已校验工作区的唯一常规入口；返回码 `0` 表示可进入学习，`10` 表示工程流程成功但 readiness 仍被内容问题阻断。
- **可恢复、可追溯的事实层**：建库中间态统一落入 `.ingest/` 的 source manifest、ContentUnit、chapter mapping 与证据文件；稳定 ID、严格 schema、源文件哈希、页码与资产 provenance 让编译结果可重建、来源漂移可检测，多文件事务在中断后可回滚。
- **类型化 AI 接管**：所有 warning、skip、缺答案与低置信页面进入 ReviewIssue 队列和 append-only ReviewPatch 账本；`ingest_review.py` 提供认领、校验、应用、不可恢复标记、重建与复验流程，不再把“AI 会接手”停留在日志里。
- **轻量检索与发布门禁**：结构感知 chunk、概念 postings、索引完整性校验及确定性 Recall@1/5、MRR 评估共用标准库实现；validator 与运行时统一输出 `ready` / `usable_with_gaps` / `blocked`，避免结构可运行被误报为资料完整。
- **文档与视觉提取加固**：DOCX/PPTX 提取覆盖表格、内容控件、公式/列表复核信号、讲者备注、隐藏对象与图片哈希；视觉与答案内容继续 fail-closed，无法确定的内容进入复核队列而不是静默丢失。
- **技能与仓库结构收敛**：主技能、子技能、双语文案、文件格式和跨宿主说明统一 readiness、来源与页锚点契约；完成的历史计划/发布说明归档，退役重复索引、caption gallery 与已被生产检索器吸收的 LlamaIndex spike，保持学生运行时包轻量。

## V4.1 — 2026-07-14

> 完整实施记录见 [`docs/history/plans/PLAN-v4.1-real-world-hardening.md`](docs/history/plans/PLAN-v4.1-real-world-hardening.md)。

- **真实课程完整性加固**：视觉覆盖拆为 wiki / 题面 / 答案三侧；空白/纯图 PDF 页也进入分母，回挂只认可带原页 provenance 的图片，索引同时绑定工作区输入、原始 PDF 内容/路径清单和派生结果哈希并在阶段完成时检查 freshness。答案专属页延后到解答区；手工提前暴露和题答共享整页 fail-closed。
- **教学例题不再随题库清理消失**：新增 `references/teaching_examples.json`、append-only `references/teaching_baseline.json` 与按章列举工具；较小 raw input/重写报告不能缩减基线，可判分题库仍是唯一答案源，教学层只保证 worked examples 可达。
- **阶段完成改为证据门禁**：wiki、视觉、教学例题、notebook 与 checkpoint 写入 `phase_evidence`；`verified` 和 `covered_unverified` 分离，旧工作区兼容但不冒充完整。
- **人类可读章节教材**：标准 `$...$` / `$$...$$` 数学事实源、raw/伪分隔 LaTeX lint、固定审计版 `latex2mathml==3.60.0`、离线 MathML、自包含图片、结构化双语 UI、按章 HTML 与可选 PDF；题面图固定早于答案图，超时/残留产物 fail-loud 清理。
- **写盘与供应链加固**：ingest 的 wiki/题库/索引/计划/进度/报告全部使用受保护原子替换，符号链接拒绝、硬链接安全断开；依赖预检和运行时都校验精确 MathML 版本，不因“任意版本已安装”误放行。
- **跨 Agent PDF 适配**：Codex、Claude Code 与通用 Agent Skills 使用独立能力路由，记录官方来源/审查 commit/许可证；不静默下载第三方 skill，受限许可证实现只链接不复制。
- **额度友好的产物模式**：新增持久化 `artifact_mode=chat|visual`。旧工作区默认 `chat`，保留对话 + notebook/state 而不自动生成章节 HTML/PDF 或小抄 PDF；只有用户显式选择 `visual` 才持续生成视觉教材，单次 HTML/PDF/打印请求可临时覆盖且不改状态。智能体不探测或猜测订阅套餐。
- **结论语义收紧**：validator 明确输出 `ready` / `usable_with_gaps` / `blocked`；`ok=true` 只代表结构可运行。根 skill metadata 与发布版本对齐。

## V4.0 — 2026-07-12

> 完整设计与实施路线见 [`docs/history/plans/PLAN-v4.md`](docs/history/plans/PLAN-v4.md)。此前 changelog 从 V3.0 直接跳到 V4.1；本节补齐已发布的 V4.0 历史，不代表一次新的发布。

- **语言与状态分层**：引入 `locales/zh|en` 语言包、共享 i18n 层与旧工作区兼容迁移，减少控制逻辑和学生可见文案的耦合。
- **轻量检索**：按块构建纯标准库 BM25 索引，支持中英术语桥、top-k、最低分弃答和检索轨迹；生产实现吸收了早期 LlamaIndex spike 的结果契约，无需运行时重依赖。
- **笔记本与错题本落盘**：讲解、判分、疑难点和复盘按章持久化，并由确定性目录重建保持可回看、可追溯。
- **小抄与 PDF 编译**：从持久化事实源编译考前小抄，支持页数约束、HTML/PDF 输出及视觉检查。
- **工作区与分发瘦身**：增加工作区注册和确认流程；以显式运行时清单构建精简 zip，并在发布流程附加产物。

## V3.0

> 把 V2.1 的地基（分章知识库 + 固定题库 + 来源标注 + 模块化 `skills/`）建成完整备考引擎：会处理真实试卷、按剩余时间调整教法、说你的语言，并第一次用可复现实测给「绝不瞎编」背书。以下全部为 V2.1 之后新增。发布通告见 [`docs/releases/v3.md`](docs/releases/v3.md)。

### 教学：会因人而变

- **七步讲解模板**：重点题精讲固定顺序 ① 题面图 → ② 这题在问什么 → ③ 图里要读的量 → ④ 核心公式 → ⑤ 逐步演算 → ⑥ 答案自检 → ⑦ 知识点溯源（章节 + wiki + 可点击原文页链接），含文科变体（关键句/核心概念/逐点展开论证）；旧版【考点拆解】/【标准答题模板/步骤】并入 ②/④⑤。**默认输出到来源块为止**：【易错点】/【3 分钟速记】/【现在轮到你】不再是必要阶段，仅学生主动要求或存有偏好（`--pref 收尾块=…`）时输出。
- **每题固定来源块**：`题目来源：…｜答案来源：…｜<🟢/🟡/⚠️>`（讲解与判分反馈均适用）；无教材答案时 ⑤/解析块标题必须带 ⚠️ AI生成答案，非老师/教材提供。
- **模式系统重做：3 学习模式 × 4 时间宽裕度**（替换旧 normal/sprint/panic/mock）。学习模式：零基础从头讲 / 某章起步补弱 / 查缺补漏（首次对话须问清，存 `study_state.json.mode`）；时间宽裕度：≤1天 / 1-3天 / 3-7天 / >7天（存 `time_budget`，叠加在模式上，决定提问节奏）。**≤1天严禁提问**（任何问题都在浪费复习时间）；1-3天随机回问困惑点；3-7天知识点窗口系统；>7天窗口外用对应难题实测。旧四模式 `set --mode` 遇 normal/sprint/panic/mock 自动迁移并警告，未知值保留原值并警告，绝不静默改写。
- **知识点窗口持久化**：`study_state.json.knowledge_window`，经 `update_progress.py window-add` / `window-set-status`（在窗口/窗口外/已实测），进度面板新增「🪟 知识点窗口」区（窗口内默认还会、窗口外先问是否记得）。
- **确定性难度评分 + 难度×掌握状态出题**：按难度和你的掌握状态抽题，而非随机。
- **范围过滤 + 官方选题器**：可指定只从某章/某来源（如仅作业）出题，越界时明确提示（`scripts/select_questions.py` + per-item `source_type` 分类）。
- **结构化进度状态**：`study_state.json` 为唯一事实源，进度 md 为其生成视图。
- **讲解模板偏好**：变体选择存 `study_state.json` 的 preferences（与模式分离），进度面板 ⚙️ 偏好区显示。

### 试卷与图像管线

- **真题试卷管线**：识别真实试卷（`source_type=exam`），答案册防泄进题面，逐页零静默丢失（`ai_review_manifest` 标出需人工接管的部分）。
- **作业 / 答案 ingest**：题答分离的 PDF 自动配对（或内联 Solution）。
- **题型识别 + 未知题型告警**：无法归类的题不会被静默丢给学生。
- **视觉优先呈现**：依赖图的题没有图就不出；通用视觉双索引 + 疑漏召回网 + 官方视觉工具，兜住本会漏掉的配图；wiki 配图与图题召回补强。

### 多语言

- **回复语言状态层**：中文 / English / 双语，`--language` 归一化（含别名表），跨对话持久化。
- **英文入口面**：`SKILL.en.md` / `prompts/web_prompt.en.md` / `AGENTS.md`（派生渲染 + 发现性对齐）；**默认英文**——学生用中文开场才切中文；脚本层空值兜底保留中文兼容旧工作区。
- **单语言纯净原则**：学生侧输出绝不中英混杂，EN canonical 词表 + 双向纯净 lint 强制；控制层零 CJK、运行时面零阶段代号。

### 防幻觉实测 benchmark（首次成体系）

- **通用三臂 × 三模型矩阵 runner**（配置驱动 + fixture 课程 + `--mock` 端到端）：闭卷 / 裸文件+通用智能体 / 本技能，跨 Opus 4.8、Sonnet 4.6、Haiku 4.5；矩阵管线加固（账本死锁根修、崩溃残段自愈、指纹盲点、成本虚标对抗审计）。
- **材料锚定金标**：题目取自 Yale PSYC 110 讲义转录与 MIT 6.006 讲义/习题集（另备一套 MIT 6.006 官方真题卷用于对照），每题答案逐字锚定原材料，另加材料里根本没有答案的越界探针。
- **判分校准**：数值判分加固 + 通用 kappa 校准（人工 κ=0.833、0.875，分歧全为判分偏严，数字保守）+ 跨家族提醒 + near-miss 建议；崩溃未判的题以「各 3 独立裁判多数表决」重判。
- **结果**：材料专属细节正确率 PSYC 三模型 11%–13% → 100%、6.006 → 91%；越界题如实弃答 60%–90% → 100%；每题成本低于裸文件智能体（同精度，PSYC 约省 15%、6.006 约省 5%；技能按章惰性加载而非整本灌入，长会话 token 消耗设计上省约 90%）。产品化双语报告（发布守卫 + SVG 图表），见 `benchmark/REPORT.md` / `REPORT.en.md`。
- 行为冒烟接线：`teaching_template`、`time_budget_no_questions`（≤1天不提问）、`knowledge_window_recheck`（窗口外被回问/出题），T4 长程漂移新增模式漂移场景，`--llm` 真 agent 冒烟转正（opt-in）。

### 打磨与工程

- **四段式考前小抄**：必背 → 例题 → 例题解答 → 要点解释。
- **只读工作区体检** `exam-audit`：直接读事实源的健康检查；架构收敛（删死模板、修 init 阶梯 bug、stale 措辞清理）。
- 统一运行账本（live smoke / rejudge 自动记账）；1000+ 单元测试 + Ubuntu/Windows × Python 3.8/3.12 CI；当时包含实验性 `spike/llamaindex_rag` LlamaIndex RAG 独立实验（其契约后来并入 V4.0 生产检索器，实验目录已在后续版本退役）。

## V2.1

- **知识来源透明化协议**：🟢 来自资料 / 🟡 AI补充，可能与你老师讲的不完全一致 / ⚠️ AI生成答案，非老师/教材提供。
- **零基础重点题精讲模式**：对几乎没学过的学生，按【考点拆解】+【标准答题模板/步骤】+【易错点】+【3分钟速记】逐题精讲。
- **画图题确定性协议**（`type: "diagram"`）：先真实运行标准算法得到结构，再渲染成图，绝不凭记忆手绘。
- **6 大题型**：`choice / subjective / diagram / fill_blank / true_false / code`。
- **工程化重构**（[PR #11](https://github.com/ZeKaiNie/universal-examprep-skill/pull/11)，不改既有行为）：
  - 模块化技能集合 `skills/`（`exam-cram` 主协调器 + 子技能）+ 根 `AGENTS.md` 兜底；
  - 双语控制层（英文控制段 + 简体中文学生侧）与 canonical 来源标注；
  - 工作区校验器 `scripts/validate_workspace.py`（纯标准库）；
  - 架构文档 `docs/`（skill-architecture / agent-portability / language-policy / file-format）；
  - 测试扩展（覆盖 ingest、工作区校验、技能结构、语言策略、控制层双语、技能集合自洽）+ CI 矩阵（Ubuntu/Windows × Python 3.8/3.12）。
- **防幻觉实测（benchmark）公平性改进**：加「裸文件 + 通用 agent」对照、成本维度、人工 kappa 校准。
- **confusion-tracker 并入 `skills/`**（`skills/confusion-tracker/SKILL.md`）：疑难点追踪不再是 `skills/` 之外的外部依赖；随后清理删除了根目录遗留的 `confusion-tracker/` 兼容文件夹（迁移说明只保留在本 CHANGELOG，不再保留持久根文件夹）。

## V2.0

- **LLM Wiki 目录结构化 + 惰性加载**：按章节物理切片（`references/wiki/`），按进度只读当前章节，Token 消耗大幅下降。
- **一键零摩擦冷启动 ingest**：学生只给大纲/真题，AI 后台解析、拼 JSON、切片、初始化进度，**无需手写 JSON**。
- **无 Python 环境自动降级**：脚本不可用时无感切换为「手动写盘模式」，由 AI 直接铺设工作区。
- **标准题库 `quiz_bank.json` 抽题**：测验只从题库出题判分，杜绝 AI 即兴编题。
- **测试逃生通道**：查看提示 / 连续答错 2 次跳过并归档错题。
- **概念疑难点追踪**（`confusion-tracker`）：自动捕获「为什么 / 怎么推导」类追问，形成考前盲区清单。
- **运行安全与进度保护**：文件名安全过滤、路径防穿越/防篡改、进度覆盖前自动备份、强制 UTF-8 输出。
- **单元测试 + GitHub Actions CI**。
