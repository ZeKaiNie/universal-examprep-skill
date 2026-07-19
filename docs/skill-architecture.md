# Skill Architecture — 技能集合结构说明

这套技能按“语言中性入口 → 单一控制层 → 可替换文案层 → 结构化建库事实层 → 编译后的学习层”组织，目标是让不同宿主能找到入口，同时避免规则、语言和派生产物各自漂移。

## 1. 入口与事实源

- [`SKILL.md`](../SKILL.md) 是语言中性路由器：读取 `study_state.json.language` 的规范值，定位主技能和对应文案包。它不再承载完整流程。
- [`skills/exam-cram/SKILL.md`](../skills/exam-cram/SKILL.md) 是主编排器；各 `skills/*/SKILL.md` 各自拥有一个职责的行为契约。
- [`locales/zh/SKILL.md`](../locales/zh/SKILL.md) 与 [`locales/en/SKILL.md`](../locales/en/SKILL.md) 是轻量兼容索引，不是中文/英文两份行为事实源。
- [`AGENTS.md`](../AGENTS.md) 是给不读取完整技能的通用代理的浓缩安全底线；详细行为仍以相应子技能为准。

## 2. 技能集合

```text
skills/
  exam-cram/          # 恢复断点、模式与阶段编排
  exam-ingest/        # 材料建库、告警接管、工作区初始化
  exam-tutor/         # 当前章惰性授课、七步精讲
  exam-study-guide/   # profile=full typed manifest → HTML/PDF → receipt/全页 QA
  exam-quiz/          # 题库抽题与判分
  exam-review/        # 错题和疑难复盘
  exam-cheatsheet/    # 考前小抄
  exam-audit/         # 只读工作区体检
  exam-help/          # 速查卡
  confusion-tracker/  # 概念疑难追踪
```

其中概念疑难追踪的完整路径是 [`skills/confusion-tracker`](../skills/confusion-tracker/SKILL.md)，与其他子技能一样位于共享控制层，而不是语言包中。

每个子技能保持 Purpose / Activation / Inputs / Workflow / Output Contract / Boundaries 六段结构。跨技能只链接，不复制整段流程。

默认材料路线是 `processing_mode=lightweight`：`exam_start` 登记精确材料/工作区 pair、
运行时回执和学习选择，`scripts/lightweight_session.py` 维护当前阶段的按需页批次，模型
只视觉读取当前 PDF 页或能确定为单帧的 PNG/JPEG/BMP；它不调用全量 ingestion，也不产生
Study Guide/PDF。每个批次最多 8 个主页面且最多一个活动批次；单页不建 contact sheet，
多页 overview contact sheets 以最多 4 页一组精确分区，并按 row-major 每 tile 至少约
768 px。新 visual receipt 使用 schema 3：primary pages 枚举稳定 `teaching_item_ids`，顶层
items 声明 `text|figure|mixed` 与通用 prompt/answer components。detail 只可合并同一 target
的 prompt components，solution 只可合并同一 target 的 answer components；逐 component
单图视觉复核必须精确检测其声明的 target/context IDs，并排除无关内容和学生作答。答案位于
其他来源时，增量 `register-answer-dependency` 只绑定所需页；planned 状态可用
`set-answer-dependency` 替换/收窄，或用 `remove-answer-dependency` 审计移除。dependency page
只作 locator/detail；只有 official parent 可提供 answer component，且每个已注册 official
页都必须被覆盖。所有模型输入都绑定路径/hash/host/model 与 source-qualified locations；
所有规范证据都以 PNG
隔离在 `.lightweight/assets/` 并经 magic/dimension/hash 校验。未完成 attempt 只能通过带
reason/digest 的 `abandon` 回执关闭后再规划；旧 schema-2 `visual_ready` 只读隔离且只能
auditably abandon，不会静默升级。taught progress 不可放弃，需重做时由 `replace-taught`
保留 superseded attempt/event，重验并继承准确答案依赖，再规划相同主切片的 successor。显式
`processing_mode=full` 才进入 `exam-ingest`。两条路线
共用 `study_state.json` 学习状态机，轻量路线另以 `.lightweight/session.json` 保存页批次
证据。普通 reconfirm 不传 processing flag 时保留已有规范选择；只有新建/缺失/旧版/非法
值安全默认 lightweight。

`preferences.interaction_style` 是与 processing/artifact/answer-explanation mode 正交的可选教学节奏，只存 `batch|step_by_step`，新建或缺失旧状态按 `batch`。已存 step 只有在 `processing_mode=full` 且 `no_questions=false` 时才 effective；其他情况下 effective cadence 为 `batch`，原偏好保留并标为 dormant。逐题路线在 workspace lock 内取得 manifest/state/notebook/baseline 一致快照并按 manifest 顺序读取下一项，完整写完七步 walkthrough 后，以 `record-taught-example` 原子写入 ID、anchor、notebook block hash 与 manifest item hash。无 binding 的 `teaching_examples` ID 是合法 batch 历史；已有 binding 无论当前 cadence 如何都必须 live 校验。Guide 保留有效绑定的 marked block，拒绝 stale binding 或无有效 binding 的 marker。它不改变 lightweight 的页批次状态机，也不把 Continue 或 notebook 文件存在性当成完成。

## 3. 生命周期路由

| 用户动作 | 主处理技能 | 只读的主要当前切片 |
| --- | --- | --- |
| 提供材料、默认轻量学习 | `exam-cram` / `exam-tutor` | 原材料当前页、`.lightweight/session.json` |
| 显式完整建库 | `exam-ingest` | 原材料目录、`.ingest/` 来源清单与 typed review |
| 讲当前章 | `exam-tutor` | 一个 wiki 章节 + 当前章教学例题切片 |
| 做当前章题 | `exam-quiz` | 题库的当前章筛选结果 |
| 复盘错题/疑难 | `exam-review` | 状态中的未掌握项 + 对应题目 |
| 建立/生成章节教材 | `exam-study-guide` | 已验证的 `notebook/chNN.guide.json` + 其引用资产；receipt/QA 是视觉交付证据 |
| 冲刺小抄 | `exam-cheatsheet` | 错题、疑难、知识窗口与 wiki |
| 体检 | `exam-audit` | 工作区清单与一致性证据 |

轻量批次按 `planned → visual_ready → taught` 推进。`record-visual` 验证页面双射、
schema-3 通用 item/component、overview/detail/solution/crop-review 输入分工；详细授课先写
`notebook/chNN.md#anchor`，再由 `mark-taught --taught-item-ids <exact IDs>` 在 workspace
lock 下发布 taught receipt
与 `phase_evidence[phase].lightweight_batches`。如果进度文件发布在 receipt 之后中断，
同一命令可幂等补齐。阶段完成要求所有已声明当前阶段批次都 taught，且 progress events
以 `inspected_pages + taught_item_ids` 与当前未被 supersede 的 attempts 一一对应；看过页面
不等于讲完页面中所有题。superseded predecessor/event 仍审计保留但
不进入当前完成分母。它不要求 `.ingest/`、wiki 或 typed Guide。首次轻量初始化只为当时
已存在的标准题库记录不可变 stat-only baseline，不解析/哈希题库；只有显式测验/checkpoint
才打开题库并绑定 exact bank/item revision。`covered_unverified` 可用，`verified` 要求该
未漂移预存题库中的两个不同 revision-bound handled checkpoints 和至少一个 pass。
日常 mount/status 仅检查 metadata + physical identity；exact stream hash 仅在 plan、答案
依赖注册、视觉/教学发布、阶段完成或显式 `status --verify-live` 时计算。非当前阶段的 taught
历史只核对不可变 receipt/event，并以 `unchecked_historical` 明示尚未现场复验。

`artifact_mode` 与处理路线正交：lightweight 下保存的 `visual` 仅是 dormant preference，
effective output 固定为 `chat`；只有显式切换 full 后才可能进入 Study Guide。full
orchestrator、workspace builder 和 lower-level compiler 都复核 exact pair、runtime、
learning choices 与 `processing_mode=full`，不能从底层命令绕过。

## 4. 状态、建库与内容层

```text
<workspace>/.ingest/               # 建库/接管事实源；不得手改
  source_manifest.json             # 原材料版本、哈希与解析状态
  parser_receipts.json             # ingestion-v2 逐来源 parser/revision/config/location receipt
  base_content_units.jsonl         # 确定性解析基线
  content_units.jsonl              # 基线 + 已应用 patch 的编译视图
  chapter_phase_mappings.jsonl     # 真实章节与学习阶段显式映射
  duplicate_candidates.jsonl       # exact/near 派生候选
  canonical_groups.jsonl           # 保留所有 occurrence 的 display/retrieval 折叠事实
  source_conflicts.jsonl           # 显式冲突；unresolved fail-closed
  source_priorities.jsonl          # revision-bound 审查证据，不是静默 winner
  claim_records.jsonl              # ingestion-v2 typed Guide 的 exact-location claims
  claim_verification_receipts/     # v2 Guide 强制 location-only + guide/fact hash binding
  review_queue.jsonl               # typed AI/人工接管生命周期
  review_patches.jsonl             # append-only、可回放补丁 ledger
  build_manifest.json              # page accounting 与完整性哈希
study_state.json                 # 结构化进度唯一事实源
study_progress.md                # 由状态渲染的可读视图
references/wiki/chNN_*.md        # 按章知识源
references/quiz_bank.json        # 唯一测验题源
references/retrieval_index.json  # freshness-bound 轻量检索派生物
references/teaching_examples.json
references/teaching_baseline.json
notebook/chNN.md                 # 持久化完整教学与反馈
notebook/chNN.guide.json         # 当前章已验证的 profile=full 强类型完成清单
mistakes/chNN.md                 # 错题镜像
study_guide/chNN.html|pdf        # 当前章派生阅读产物
study_guide/chNN.receipt.json    # manifest/HTML/PDF 哈希与 QA 状态
study_guide/qa/chNN_pNNN.png     # 最新 PDF 的逐页验收证据
```

常规材料入口是 `scripts/ingest_course.py`：预检 → 解析 → 结构化编译 → 状态初始化 → 视觉索引 → validator。core route 覆盖 PDF/DOCX/PPTX/XLSX/常见 raster/txt/Markdown；XLSX worksheet、standalone raster 与 DOCX logical segment 都保留各自 location 语义，不能冒充物理页。退出 10 表示程序完成但内容 readiness 被阻断，必须用 `scripts/ingest_review.py` 逐项接管；不能因为 wiki/题库已经出现就开始教学。review patch 绑定来源哈希并写入 append-only ledger，再重编译 wiki、题库和检索索引。大量独立问题可在每项完成视觉检查、claim、单独 patch 与 `validate-patch` 后使用 `apply-batch`：ledger 身份与事务仍逐项保留，只把派生编译推迟到批次末尾。

ingestion-v2 的 parser receipt 对每个 source 绑定 exact hash/media、adapter/version/config、produced location inventory 和 `network/upload/install=false` policy。`source_id` 由 canonical path 派生，`unit_id` 由 source/location/bbox/kind/ordinal 派生；二者都不是 content revision hash，精确 revision 由 source/full-unit digest 另行绑定。canonical groups/conflicts 是可重建派生事实：保留所有来源 occurrence；near match 不自动成组；priority 不静默裁决；unresolved conflict fail-closed。

Docling/MinerU 只允许用户点名后由已配置的远程/云端 host 提供；学生本地运行时不探测、不下载、不安装、不导入、不执行重型包，也不接受 callable local runner。远程 host 必须另行披露上传与隐私边界；没有该集成就继续 core + typed visual review。生产检索仍为 stdlib BM25；Dense + Sparse/RRF/reranker 只有在充分的 frozen 真实多课程 recall Gold Set 通过 gate 后才可作为 opt-in，当前 synthetic sample 明确不足。ingestion-v2 typed Guide 强制 claim sidecar + chapter receipt：validator 现场重算 canonical strict-JSON guide/fact hashes，要求显式 claim 的 authored subject/text、same-ref unit/role 与 source location/revision 均匹配，并覆盖直接 material KP explanation、formula、printed prompt 和 material answer；v1 保持兼容。`location_only` 从不证明语义蕴含。

状态存在时只能经 `scripts/update_progress.py` 修改；无状态但 Python 可用时先 `init`。`study_progress.md`、wiki/题库、检索索引、HTML 和 PDF 都是面向不同用途的编译/派生视图，不能反向覆盖 `.ingest/` 或 `study_state.json` 的事实源。

## 5. 语言层

- 控制规则：`skills/*/SKILL.md`，以英文、精确、可测试的流程为主；触发 metadata、规范状态值和逐字学生话术是明确例外。
- 学生文案：`locales/<lang>/skills/*.md`、消息目录和模板。
- 持久化规范语言值：`zh` / `en` / `bilingual`；`中文` / `English` / `双语` 只是显示别名和旧状态迁移输入，根路由器先归一化再派发。
- 原始材料的逐字引文可保留原语言并标明；智能体生成 prose 必须遵守所选语言。
- 机器契约：JSON keys、stable IDs、hash、reason code 和 lifecycle status 固定，不随翻译改变。
- 人类可读视图：智能体生成的 notebook、回执与教材按选择语言渲染；状态枚举保持 canonical。若 legacy/generated 进度视图仍是中文 canonical，代理只把它当状态视图读取，再按当前语言复述，不能把两层混为一谈。

详见 [`language-policy.md`](language-policy.md) 和 [`localization.md`](localization.md)。

## 6. 关键不可变式

- 知识来源透明：🟢 来自资料 / 🟡 AI补充，可能与你老师讲的不完全一致 / ⚠️ AI生成答案，非老师/教材提供。
- 测验只来自 `quiz_bank.json`；没有题库或题面资产不可见时失败关闭。
- 学习模式和时间档位影响节奏，但不解除来源、状态或资产门禁。
- `preferences.interaction_style` 只存 `batch|step_by_step`，缺失按 `batch`。`step_by_step` 只改变 full 教学清单的每轮题数；`no_questions=true` 或 lightweight 会保留已存偏好、报告 dormant，并使用有效 `batch`。逐题完成必须遵循 manifest-first 顺序，并写入 `{id, notebook_ref, notebook_block_sha256, manifest_item_sha256}` binding；quiz/teaching/notebook/Guide 共用 1–200 字符的安全 Unicode ID 契约，两个 binding 不得共享同一 `notebook_ref`。未绑定 ID 是合法 batch 历史；一旦绑定，即使切回 batch 也继续接受 notebook/manifest live 校验。只有 notebook 条目缺失以及 anchor/marker/hash/revision 漂移可重新进入 pending；不安全路径或文件类型、越界、非法 UTF-8、未闭围栏、坏 block、schema/重复/unexpected evidence 均失败关闭。已完成 full phase 的结构合法新项或可修复 stale 项只在 mount 降为 `usable_with_gaps`，旧 Guide 与完成证据仍失效。每个 `teaching_baseline.json` ID 都必须在同一 canonical chapter 的当前 `teaching_examples.json` 中保留快照，且 policy 精确为 `append_only`；quiz-only 同 ID 不可替代。`teaching_example_roster_exhausted` 不是章节完成，不能替代 Guide、题库、typed-unit、资产、checkpoint 或 phase 门禁。
- 图结构题先运行确定性算法，再渲染。
- 教学、判分、疑难和复盘先写 notebook，再返回对话摘要。
- full/结构化工作区在阶段完成前必须验证当前章 `profile=full` typed manifest；`artifact_mode=chat` 到此停止，不要求 HTML/PDF。lightweight 阶段只走 current-phase taught-batch + notebook/progress live-binding 门禁，绝不加载 typed Guide。
- `answer_explanation_mode` 与 processing/artifact mode 正交；存储层缺失/旧值安全回退为 `ordinary`。两种模式都要求每题详细、初学者可读的答案解释；ordinary 在正常 annotations 中生成并禁止 isolation receipt。进入 full-v2 Guide 时先做宿主原生子智能体能力握手：只有能证明每题全新独立上下文，且输入与工具都可限制到准确单题请求时，才在用户未退出的情况下默认持久化 `isolated`，并只提示一次额外宿主额度/时间；不需要第二把 API Key 或外部上传同意。能力缺失、继承或无法确认就保持 ordinary。外部 Provider 是用户明确点名后的备用路线，仍需不上传的 exact planning 与价格/保留/隐私披露后的 exact-plan upload consent。模型系列、订阅、API Key、`full` 或 `visual` 本身不构成能力或上传授权证据。
- `visual` 或一次性请求才进入视觉产物流程；`visual` 只有在 receipt 哈希匹配、逐页全部验收、零未解决缺陷且 `artifact_ready=ready` 后才能交付和完成阶段，不能把“请求生成”写成“已经成功”。
- 修改 `study_state.json.language` 或 `answer_explanation_mode` 会使旧 manifest/HTML/PDF/QA 与 mode-bound authoring 链 stale；ingestion-v2 必须从目标语言/模式 annotations 开始重新执行 notebook、compile、claims、verify/import，isolated 还要重做逐题 explanation receipt。不能 relocalize 或改名复用旧解释。唯一历史例外是已有、mode-less、带完整且当前可复验 isolated contract 的 protocol-v2 canonical manifest：只能省略 `--input` 做 CLI 原位 validate，不能 import/render/QA/完成阶段或作为库级兼容输入；任何修改都按显式 mode 重建。ingestion-v1 只读保留既有 canonical manifest，不能 import/relocalize/render；任何修改先迁移/重建为 v2。之后按需重新渲染和全页验收。
- 建库程序的 warnings、skipped、人工审阅项和缺答案项必须逐条接管。
- 结构化工作区 `readiness=blocked` 时禁止进入授课、测验或阶段完成。
- LangGraph 只有用户点名后才可由远程/云端 host 编排同一组命令；本地 `build_exam_graph()` 明确拒绝运行。远程 checkpoint/resume flag 仍只是 routing hint，不是事实源；每个门禁从 `study_state.json`、`.ingest/`、runtime/guide/QA receipts 重新验证，详见 [`langgraph-host-adapter.md`](langgraph-host-adapter.md)。

## 7. 验证层

仓库测试覆盖：

- skill frontmatter 与目录清单；
- zh/en 文案、消息键和模板结构对齐；
- 语言纯净与 canonical 标签；
- 状态初始化和写入边界；
- 题库、范围、视觉资产和来源契约；
- 工作区 schema/validator；
- 分发包必须包含路由器、控制层、语言包和脚本；
- Markdown 相对链接与模板样例硬编码。

新增功能应更新现有事实源及对应语义测试，不应再创建一份 release-era 手册或复制整段规则。
