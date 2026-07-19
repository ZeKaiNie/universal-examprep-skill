# 工作区文件格式 (Workspace File Format)

本技能建出的备考工作区有一套固定结构与题库 schema。本文件是**规范化文档**，也是
[`scripts/validate_workspace.py`](../scripts/validate_workspace.py) 校验的依据。

## 1. 工作区结构

```text
<workspace>/
  study_plan.md; study_state.json; study_progress.md
  exam_runtime_receipt.json; ingest_report.json
  .lightweight/session.json         # lightweight 按需页批次账本（不与 .ingest 混用）
  .lightweight/assets/              # lightweight 专属 page/contact/prompt/answer 图片
  .ingest/                         # 构建/审查事实；不得手改
    source_raw_input.json; parse_report.json; source_manifest.json
    material_build_pending.json; material_build_receipt.json
    material_build_recovery/<generation_id>.json
    pending_ingest.json             # 仅编译回滚事务中存在
    parser_receipts.json           # v2 每来源一条 local-only receipt
    base_content_units.jsonl; content_units.jsonl
    base_chapter_phase_mappings.jsonl; chapter_phase_mappings.jsonl
    duplicate_candidates.jsonl; canonical_groups.jsonl
    source_conflicts.jsonl; source_priorities.jsonl
    claim_records.jsonl; claim_verification_receipts/chNN.json
    review_queue.jsonl; review_patches.jsonl; pending_patch.json
    unbound_review.json; ai_review_manifest.json; evidence/
    build_manifest.json; mutation.lock
  references/
    wiki/chN_*.md; quiz_bank.json; teaching_examples.json
    teaching_baseline.json; retrieval_index.json; terms.json
    figure_page_index.json; image_question_index.json; assets/
  notebook/; notebook/chNN.guide.json
  study_guide/                     # HTML/PDF/receipt；qa/ 为逐页证据
```

- `study_state.json` 是进度事实源；`study_progress.md` 是生成视图。`references/wiki/` 只接受安全相对名 `^[\w.\-]+\.md$`，其章节应与 `study_plan.md`/当前阶段一致；疑难点由 confusion-tracker 写入 state 并投影视图。
- `notebook.py add-entry/rebuild --lang` 接受规范 `zh|en|bilingual`；省略时继承 `study_state.json.language`。双语条目仍只落一份可审计正文，但 meta label、章标题和派生 index 标题同时显示中英，不得把双语正文伪装成 `lang=zh`。旧 zh/en 标签继续可逆解析，同章同 ID 同类型仍按原规则幂等替换。
- `exam_start.py` 的 `confirm` 与 `recover-material-build` 是 `exam_runtime_receipt.json` 仅有的受支持 writers：`confirm` 只用于普通首次/无 pending 确认，`recover-material-build` 只用于已确认 pair 且存在精确 pending generation 的显式恢复。receipt 绑定绝对 package root、根 `SKILL.md` 版本、运行面 SHA-256、Git identity（或不可用原因）、Python 与 UTC；workspace 必须在 package 外。每次 ingestion 重算 identity，缺失、畸形、link-backed 或漂移均 fail-closed，绝不修改安装包求匹配。
- `.ingest/` 的 manifest、units、review ledger 与 build manifest 必须同代；材料或派生 hash 漂移即拒绝旧产物。v2 还强制 parser receipts 与四个 dedup/conflict sidecar；缺文件、schema/revision/page graph/policy 不一致均 fail-closed。v1 可读但不得冒充 v2 门禁。
- `pending_patch.json` 只允许事务中存在；残留即阻断并要求恢复/重建。`mutation.lock` 仅互斥，不是内容事实。
- `material_build_pending.json` 是 builder→compiler 的 fail-closed 代际门闩。只有 builder 成功才发布新一代：pending 必须先于本轮资产、raw input 和 parse report 变更，并绑定旧 build manifest、新 raw/report、候选三层资产策略及迁移收据。Builder 返回非零时不替换 canonical raw/report 或公开资产，诊断只在当次命令结果中；发布失败且无法完整回滚时则保留 blocker。Pending 存在时，普通 mutation/publication（包括 review、claim、Guide）和 validation 全部拒绝；只有明确 generation-aware 的 builder/compiler 路径可绕过该检查。唯一允许的旧角色修正是有一一对应收据的 `answer_context → student_attempt`。
- Material compiler 为结构化事实、build manifest、wiki、题库/教学例题、检索索引、报告/计划及 material pending/receipt 切换建立一个有界事务。它在任一 target 变更前写 `pending_ingest.json` 与备份；显式失败立即回滚，进程中断则使 validation 保持 blocked，并由下一次持锁 mutation 先恢复全部登记 target。Builder 已发布的 candidate 资产/raw/report 不在该 compiler 回滚集中，因而 material pending 会继续阻断并允许重试同一 generation。
- 成功 finalization 写入 `material_build_receipt.json`，把 build manifest 设为 schema `2`，以严格 `material_build` 对象和 `artifacts` 中的 raw/report/receipt 三元精确 hash 绑定 generation，复验 live bytes 后才最后删除 material pending。当前协议产物不得被 refresh/重写成 schema `1`；缺失或漂移的 receipt、`material_build` 契约或三元 artifact binding 都阻断 validation。Legacy schema `1` 仍可读，但不声称通过该 generation gate。`ingest_course.py` 只在 compiler 子进程成功后才初始化 `study_state.json`/写入 artifact preference；这些 learner-state 操作不属于 compiler 事务。
- Pending 存续期间若 `exam_runtime_receipt.json` 缺失或漂移，普通 `exam_start.py confirm` 不得改写 provenance；必须显式运行 `exam_start.py recover-material-build --action resume|supersede`。`resume` 只接受同一 generation：源文件完整时直接消费原字节，blocker-first 中断导致源缺失时允许 builder 重建，但重建 generation 不同则零发布失败并要求 `supersede`。`supersede` 产生 schema `2` successor，`supersedes_generation_id` 只指向直接前代。
- `.ingest/material_build_recovery/<generation_id>.json` 是严格、generation-addressed 的恢复日志；单日志最多 64 个 authorization event，祖先链最多 64 条 direct-child edge。每个 abandoned outcome 必须指向直接 child，receipt 最多记录 64 条 ancestor abandonment 加 1 条当前 resume completion（共 65 行）。Compiler 回滚事务同时覆盖 pending、receipt、当前及全部祖先日志；最终 manifest 的保留键 `material_build_recovery:<generation_id>` 必须与 receipt 声明集合完全一致并绑定 live hash，额外/缺失/漂移均阻断。不得手改或删除这些事实来解除 blocker。

### 结构化 ingestion 事实源

常规建库入口是：

```text
python scripts/ingest_course.py --materials <dir> --workspace <ws> --json
```

它依次做依赖预检、材料解析、结构化编译、进度状态初始化、视觉索引/回挂和最终校验。默认 `core`
路线处理 PDF、DOCX、PPTX、XLSX、常见独立 raster、txt 与 Markdown；实际 PDF 能力仍以当前预检为准。
`--artifact-mode` 只在学生明确给出长期 `chat|visual` 选择时传入；省略表示保留已有选择或默认 `chat`。

退出码与内容就绪状态分离：

| 退出码 | `process_success` | 含义 | 下一步 |
| --- | --- | --- | --- |
| `0` | `true` | 工程流程完成；JSON readiness 为 `ready` 或 `usable_with_gaps` | 后者先逐项报告 warning |
| `10` | `true` | 工程流程完成，但 readiness 为 `blocked` | 进入 typed review，禁止授课/测验 |
| 其他非零 | `false` | 依赖、输入、路径或操作失败 | 修复失败原因后重跑同一命令 |

`source_manifest.json` 为每个来源保存由 canonical 相对路径派生的 source ID、材料根目录相对路径、SHA-256、字节数、媒体类型和
解析状态。绝对材料根路径与逐页 accounting 位于 `build_manifest.json`（`source_root` / `page_quality`），
不属于 SourceRecord schema。移动材料或工作区后必须重新建库/重绑，不能把 source drift 当成普通 warning；
分享工作区前也应注意 `source_root` 可能暴露本机路径。

每行 `ContentUnit` 至少包含稳定位置标识 `unit_id`、`source_id`/source hash、来源相对路径、元素类型、位置序号与
页锚、顺序、提取方法/置信度和 provenance；可选保存 bbox、父单元、section path、chapter/phase ID、
公式/HTML、asset path/role 及题答配对。元素类型包括 title/heading/text/list/table/formula/figure/
diagram/caption/code/speaker_notes/question/answer/page_anchor/other。`source_id = hash(canonical relative path)`；
`unit_id = hash(source_id, page/location, bbox, kind, ordinal)`，**不包含 source bytes 或 normalized content**。
因此同一位置在材料或 payload 改版后可能保留同一 ID；精确 revision 必须同时检查 `source_sha256`，派生
dedup/claim facts 还绑定完整 unit payload 的 `unit_sha256`。不得把 ID 本身称为 content-derived revision proof。

`page`/`page_anchor` 是统一字段名，但语义由 adapter 决定，并不总是物理纸页：

| 来源 | `page` 的真实含义 |
| --- | --- |
| PDF | 1-based PDF page ordinal（在后端能枚举时） |
| PPTX | 1-based slide ordinal |
| XLSX | 1-based worksheet ordinal；worksheet 是 page-equivalent，不是打印分页 |
| standalone raster | 固定为 1 的单图 page-equivalent |
| DOCX | 只按 OOXML 中显式 page-break 切出的 1-based 逻辑 segment；无显式 break 时通常只有一个 segment，**不是 Word 渲染后的物理页码** |
| txt/Markdown | form-feed 切分（存在时）或单一逻辑 segment |

每个 adapter 已知并枚举的位置都必须有 `page_anchor`；空白/扫描 PDF 页也不例外。无法枚举位置的文件保留
source-level review issue，不能伪造页数。

### ingestion-v2 parser receipts

`.ingest/parser_receipts.json` 是 `{ "schema_version": 1, "receipts": [...] }`。每个已发现 source **恰好一条**，
receipt 字段必须**恰好**为 `schema_version=1`、`adapter`、可空的 `adapter_version/module/distribution`、
`source_file/source_sha256/media_type`、sorted-unique `requested_pages/produced_pages`、非负整数
`discovered_page_count`、`config_sha256`、`status=success|review_required|failed|unsupported` 与精确
`policy={"network":false,"upload":false,"install":false}`。`produced_pages` 必须等于 live page graph：成功/待审且
`requested_pages=[]` 时须为 `1..discovered_page_count`；请求子集时须与请求完全相等且不越界；failed/unsupported
不得产出页。未知/重复 source 或任一 schema/revision/config/page/policy 漂移都阻断 v2。

可选 runner 返回对象必须**恰好**为 `{pages, discovered_page_count, warnings?}`；完整抽取与请求子集按上段覆盖规则校验。
normalized page 的 `source_language` 仅 `zh|en`；unit 仅按自身 payload 分类，纯公式/符号可用 `zxx`，不继承
page language 或支撑 zh/en Guide；其余进 typed review。core 也出 receipt；receipt 只证明 exact route/revision/config/accounting。Docling/MinerU 不进入本地 receipt 路线：只有用户点名后，已配置的远程/云端 host 才可另行披露上传/隐私边界并提供结果；本地不探测、不下载、不安装、不导入、不执行重型包，也不接受 callable local runner。没有远程集成时继续 core + typed visual review。

### XLSX 与 standalone raster 专线

- XLSX 走 stdlib OOXML，不执行公式；每 worksheet 是 page-equivalent，并保留顺序、稀疏 cell/value、公式与 cached value、table、merge 和安全 raster。cached/shared formula 缺失、hidden sheet、外部/网络公式或 unsupported relationship 进入 review。
- PNG/JPEG/GIF/BMP/TIFF/WebP 先验 signature/size/dimensions/hash，再按需物化单页 `source_page` asset。只有显式命名的 `<stem>.ocr.txt` 或 `<image.ext>.txt` 可声明 OCR sidecar；它仍作为独立 `SourceRecord`/parser receipt/content unit 入库，并由图片 anchor 绑定 path/hash/size，图片 unit 不吞并其文本。普通同 stem `.txt/.md` 只是独立课程材料，绝不自动配对；无合格 sidecar 时产生 `standalone_raster_needs_ocr`，交 local OCR/vision 或 typed review，不能把空文本当成功。animated/multi-frame GIF/WebP、APNG 与 multi-page TIFF 不压平成单页：source 记为 `failed` 并产生 blocking typed review。

`base_*` 是确定性基线，compiled units/mappings 是基线 + applied ledger；均不得手改。`ReviewIssue` 绑定稳定 ID、source hash、reason/page/evidence/target/severity/action，状态为 pending/claimed/validated/applied/blocked/resolved/unrecoverable/superseded；blocking issue 未终态即 `blocked`。

`ingest_review.py --workspace <ws> --json <command>` 提供 `list/show/claim/validate-patch/apply/apply-batch/mark-unrecoverable/rebuild`；patch 必须证据绑定且每个 issue 独立，ledger append-only。`apply-batch` 只把派生编译合并到批次末尾。`ai_review_manifest.json` 仅为 legacy view。

跨来源 `pair_qa` 操作必须在 `source_revisions` 中按 `source_id` 排序并完整绑定题面与答案的当前 `source_id/source_sha256`；任一来源漂移都会停止旧补丁回放、重新打开对应 review issue，并要求基于新 revision 复核。旧 ledger 仅在当前 compiled pair 的两侧 revision 与互相配对关系都能被精确证明未变时兼容回放。

### canonical groups 与 source conflicts（派生事实）

ingestion-v2 会从当前 `content_units.jsonl` + `source_manifest.json` 确定性重建四个 sidecar：

- `duplicate_candidates.jsonl`：仅在 chapter/kind-family/source-side/provenance 等 compatibility key 相容后，
  记录 exact fingerprint 或达到阈值的 near candidate；候选绑定 source hash 与 full-unit hash。
- `canonical_groups.jsonl`：默认只自动成组 exact fingerprint；每个 group 保存所有 member revision refs 和一个
  确定性 `display_unit_id`。这是 display/retrieval 折叠提示，不会删除 occurrence、改写 `unit_id` 或把来源合并成一份。
  当前确定性 compiler 不会把 near candidate 折叠为 group；它们保持候选。schema 中的 `reviewed_near` 也要求显式
  `decision_patch_id`，不能因为相似度高或手改 sidecar 就成为 canonical fact。
- `source_conflicts.jsonl`：把 answer/boolean/numeric/formula/provenance/visual/textual divergence 与成员 revision
  分开建模；`status=unresolved` 时 fail-closed，不能授课、出题、生成 material claim 或完成阶段。
- `source_priorities.jsonl`：记录绑定 source revision 的 priority tier/basis。priority 是审查证据，**不是自动 winner**；
  conflict resolution 必须有显式 evidence/review decision，不能靠文件名/排序静默选择。

这些文件是派生事实；重建会替换。validator 检查 schema、live revision/graph 与 integrity hash，并以代码内 canonical
`DedupConfig()` 重算；当前 schema 没有可信 custom-config receipt，因此同步篡改 manifest config/hash 也会阻断。清空、只留 display member 或手改 conflict status 都不算解决。

### exact-location claim records 与 guide binding

普通 ingestion 可不建 `.ingest/claim_records.jsonl`；ingestion-v2 typed Guide 在 validate/import 前必须有它和本章 receipt。每条记录把 guide subject
`chapter/entity_type/entity_id/field/language/claim_index` 的 agent-authored `claim_text` 绑定到 `UnitRevisionRef`、
`payload_field=text|latex`、完整 payload hash 与 `QuoteSpan(start,end,offset_unit=unicode_codepoint,text,sha256)`。

- `create` 读严格 `{schema_version, proposals}`，从 live unit/source 算 revision/payload/span hashes 与 ID；默认按完整 subject key 原子 merge，`--replace-all` 才全量替换。重复 quote 必须给 code-point `start`。
- `import` 严格校验完整 JSONL 后原子替换；`verify --manifest <workspace-relative-guide.json> --chapter <N>` 校验显式 binding 并写 `claim_verification_receipts/chNN.json`。

`create.claim_ids` 只含本批新记录，并报告 created/retained/replace_all；任一全局 sidecar hash 变化都会使所有旧章节 receipt stale，应完成整批 mutation 后逐章 verify。verify 只计 guide 显式引用的 claim IDs（knowledge/formula `source_refs`、walkthrough `source_trace`、omission/semantic-exclusion refs）；未引用记录不进 verified count，但仍被全局 hash 绑定。subject coordinates 必须唯一定位到与 `claim_text` 完全相等的 authored string。create/import/verify/Guide import 共用 `.ingest/mutation.lock`，防止 live-read 到发布之间并发 mutation。

发布 fact snapshot 还绑定 `parser_receipts.json` 的精确字节，并交叉核验 build manifest 内的 `page_quality`、当前 source revision、typed review queue 与 append-only ledger；`ClaimVerificationReceipt.fact_snapshot_sha256` 是该 canonical snapshot 的哈希并参与 receipt ID，因此即使合法更新 parser identity 并同步重签 build manifest，旧 receipt 也会 stale。收据缺失、漂移或 parser/review 状态矛盾都会使 claim verify、Guide import 与 render 失败关闭。

这是 **ingestion-v2 only** typed-guide gate：claim 必须挂在同一 source ref，unit ID 与 role 精确兼容；validator 现场重算，文件存在不等于通过。material assertion 覆盖为：

- 每个目标语言中、由相同 source language 的非空 textual unit 直接支撑的 knowledge-point `explanation`：
  `concept` ref + `concept_evidence` claim；
- 每个 formula 的 `latex`：`formula` ref + `formula_evidence` claim；
- 作为文字打印的 walkthrough `prompt_text`：`question` ref + `question_evidence` claim；`full_prompt` source image
  已替代原文时不伪造重复 prompt claim；
- `answer_provenance.<language>=material` 的每个答案语言：`answer|solution` ref + `answer_evidence` claim。

`knowledge_points[].explanation_provenance` 可按 explanation 的完整语言键显式标记 `material|ai_translation|ai_supplement`；省略时为兼容旧 Guide 而把所有 authored explanation 按 `material` 失败关闭。`material` 需要同语言 source text 与 claim，`ai_translation` 需要另一来源语言的已声明 material explanation/claim，`ai_supplement` 不得携带 material claim；notebook/HTML/PDF 都显示该标签。AI translation/教学解释及 `ai_supplemented|ai_generated` 答案不得伪装成 material claim；v1/legacy 不声称通过 v2 gate。

验证范围固定 `location_only`、identity `claim-location-v1`：证明 authored membership/text identity、quote 是 live payload 的精确 Unicode code-point slice、unit/source/payload 未漂移，且 prompt claim 未借 answer-side unit。`guide_content_sha256` 哈 canonical JSON（非文件 bytes），并绑定 source manifest、units、groups、conflicts、完整 claims。它不证明 entailment/support/正确性/完整性；人工仍须判断语义与 provenance。任一 guide/fact hash 改变即 stale。

### 可选 host 扩展

运行时检索默认是 `scripts/retrieve.py` 的 stdlib BM25；source checkout 中的 dense/RRF/reranker 仅为离线实验，不代表安装包已启用，也不替换默认路线。

[`langgraph-host-adapter.md`](langgraph-host-adapter.md) 只保留显式请求的远程/云端 host 契约；本地 graph construction 被禁用。远程 checkpoint/thread 仅存 routing hint/有界 receipt，每次 transition 仍从 `study_state.json`、`.ingest/`、runtime/Guide/QA receipts 重新 hydration。

## 2. 题库项 schema (`quiz_bank.json`)

顶层是一个 **JSON 数组**，每个元素是一道题（对象）。

### 公共字段

| 字段 | 必需 | 说明 |
| --- | --- | --- |
| `id` | ✅ | 题目唯一标识（数组内不得重复） |
| `chapter`（或 `phase`） | 强烈建议 | 所属章节/阶段（整数或字符串）。章节测验按它过滤抽题，缺了该题会抽不到。因 `ingest.py` 不强制，校验器对缺失只**告警**不报错 |
| `type` | ✅ | 题型，见下方 6 类之一 |
| `question` | ✅ | 题干 |
| `answer` **或** `answer_status` | 见 §3 | 标准答案；无答案时用 `answer_status: "unknown"` |
| `explanation` | 建议 | 解析（学生做错时给出） |
| `source` | 建议 | 来源标注，见 §3 |

### 六大题型的专属字段

| `type` | 必需 | 建议/可选 |
| --- | --- | --- |
| `choice` 选择 | `options`（非空数组） | `answer` = 正确选项 |
| `subjective` 主观/计算 | — | `keywords`（要点检索判分用，**强烈建议**） |
| `diagram` 画图 | — | `diagram_type`（如 `avl_tree`）、`expected_steps` 或 `rendering_notes`/`render_hint`（**建议**，画图先跑算法） |
| `fill_blank` 填空 | — | `acceptable_answers`（当有多个可接受答案时，数组） |
| `true_false` 判断 | `answer` 为布尔型（`true`/`false`，或 `真/假`、`对/错`、`T/F`） | `explanation`（建议） |
| `code` 代码/改错 | — | `language`（如 `python`）、`expected_behavior` 或 `tests`（**建议**） |

结构化 ingestion 对这两类启发式缺口采用窄作用域 typed review：每一道默认判成 `subjective` 的题各自生成一条
`type_defaulted` issue，绑定该题的 `external_id`、question unit、题面来源 revision 与页锚；只有旧 payload
既没有 `external_ids` 也没有 `target_unit_ids` 时，才保留“全题库复核”的 legacy 扩大行为。因此应用一题的
题型修订不会顺带关闭同一 PDF 中另一章的题。

若可评分主观题缺 `keywords` 且已经配有资料来源的官方答案，ingestion 另生成
`subjective_keywords_missing` issue。该 issue **以 answer unit 为唯一 target**，evidence/source hash/page
也属于官方答案文件；reviewer 在 answer unit 的 `metadata.keywords` 写入窄而可评分的要点。编译题库时
question metadata 中已有的 `keywords` 优先，否则才继承 paired answer 的 `metadata.keywords`。这样独立作业册
与解答册不会借题面 revision 冒充答案证据；没有官方 paired answer 时不会自动生成关键词或假装可判分。

## 3. 来源标注 (Provenance)

防幻觉的关键不只是「锁进 wiki」，还要分清答案**来自学生资料**还是**AI 补的**——否则学生会把 AI 编的当成老师重点。

`source` 取值：

- `teacher` / `material` —— 🟢 来自学生上传的老师重点/教材/真题，可信度高。
- `ai_generated` —— ⚠️ 由 AI 生成（老师没给答案时代答）。**这本身就是必需的可见标注。**
- `mixed` —— 部分来自资料、部分 AI 补充。
- `unknown` —— 暂无答案、来源未知。

**强制规则（校验器据此报错/告警）：**

1. **不得把 AI 生成的答案伪装成老师提供**：若一道题带 AI 生成标志（`source: ai_generated` 或布尔字段 `ai_generated: true`），其 `source` **必须**是 `ai_generated` 或 `mixed`，**不得**标成 `teacher`/`material`。违反 → **错误**。
2. **缺答案如实标注**：legacy/手写工作区缺 `answer` 时仍报**告警**；带 `.ingest/` 的新工作区会同时产生 blocking review issue，在补入有证据的官方答案、明确标注 AI 答案，或显式标记不可恢复前，readiness 为 `blocked`。导入进程成功不等于内容已可用于测验。
3. **缺 `source`**：有答案但未标 `source` → **告警**（建议补全来源）。

> `chapter`（或 `phase`）用于章节复习过滤抽题，强烈建议每题都带；但 `ingest.py` 不强制，故缺失只报**告警**（不判工作区无效）。

> 这些字段与随包提供的 [中文题库模板](../locales/zh/templates/quiz_bank_template.json) / [English quiz-bank template](../locales/en/templates/quiz_bank_template.json) 一致；`ingest.py` 的 `VALID_QUIZ_TYPES`
> 定义了上述 6 类。本规范在其基础上补充了各题型的可选字段与来源校验，供 `validate_workspace.py` 静态检查使用，**不改变既有生成逻辑**。

## 4. 资源依赖与原页引用 (asset-aware fields)

依赖图/表的 Quiz/Example 若未真正显示题面资源就不可作答。以下字段向后兼容；`ingest_course.py` 尽量物化 PDF/OOXML/XLSX/raster asset 并保留真实 location，无法绑定则 typed review，缺图时校验/出题 **fail-closed**。DOCX logical segment 与 XLSX worksheet page-equivalent 不是物理页。低层 `build_raw_input_from_workspace.py → ingest.py → build_visual_index.py → validate_workspace.py` 仅供诊断；能力以 `check_deps.py` 为准，不自动安装/联网/上传。

### 题项新增可选字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `source_file` | string | 题面所在原始文件（如 `ch01.pdf`） |
| `source_pages` | list[int] | 题面所在页码（**正整数，从 1 起**） |
| `answer_source_file` | string | 答案所在原始文件 |
| `answer_source_pages` | list[int] | 答案所在页码（正整数） |
| `assets` | list[asset] | 题目/答案依赖的图片等资源，见下 |
| `requires_assets` | bool | 为 `true` 时：**没有有效 asset 就不能出这道题**（校验器报错、出题跳过） |
| `maybe_requires_assets` | bool | 面向未来的保守标记；为 `true` 时运行时与校验器按 `requires_assets=true` 同样 fail-closed，直到题面侧 asset 能先显示出来 |
| `question_text_status` | `"full"` \| `"stub"` \| `"page_reference"` | 题面完整度：`full` 可独立成题；`stub` 题面残缺、须配 `source_pages` 或 `assets`；`page_reference` 题面是“见某页”、须有 `source_file`+`source_pages`（依赖图时还须有效 assets） |

### asset 对象

```json
{
  "path": "references/assets/ch01_p012_quiz_1_1.png",
  "role": "question_context",
  "type": "page_image",
  "caption": "Venn diagram for Quiz 1.1",
  "source_file": "ch01.pdf",
  "source_sha256": "<sha256 of ch01.pdf>"
}
```

- 新版 ingestion 为每个 asset 独立绑定 `source_file` 与 `source_sha256`。当答案侧裁图
  来自学生提交的作业 PDF，而 `answer_source_file` 指向另一份官方答案册时，必须使用
  这组逐资产来源字段；旧 asset 缺少 `source_file` 时仍按 role 兼容推断。

- **role** ∈ `question_context` / `answer_context` / `figure` / `table` / `diagram` / `worked_solution` / `student_attempt`
  - **题面侧 role**（出题前会展示给学生）= `question_context` / `figure` / `diagram` / `table`。`requires_assets=true` 或 `maybe_requires_assets=true` 的题**至少要有一个题面侧的有效 asset**；只有答案侧 asset（`answer_context` / `worked_solution`）无法在出题前展示题目，会被判 fail-closed。
  - **答案侧 role**（只在解答/复盘阶段展示）= `answer_context` / `worked_solution`。本 schema 不新增 `question` / `prompt` role；外部系统若使用这类名字，导入前应映射到现有题面侧 role。
  - `student_attempt` 保留学生提交作业中的裁图/原页，并逐资产绑定自身的 `source_file` 与 `source_sha256`。它既不是题面证据，也不是官方/资料答案；不得满足题面可见性、官方答案或 Study Guide 答案覆盖，不得用于 material claim、wiki 或 retrieval。当前 tutor/Guide 不显示它；未来若增加学生作答对照，必须使用独立且显式的策略。
  - `student_attempt` 按**物理路径全局污染**：在 quiz、teaching examples 或任意 content unit（含其他章节及嵌套 assets）出现一次后，同一物理文件不得再以题面、答案、概念、渲染、检索或 claim 证据使用。比较前先做安全相对路径规范化；安全的 `/` 与 `\` 分隔符别名等价，Windows 上大小写别名也等价。污染是非对称的：没有 `student_attempt` 时，不同 item/unit 可合法复用同一官方 prompt/answer 文件；同一 item 把同一物理文件同时标为题面与答案仍视为直接泄漏并 fail-closed。官方与学生作答位于不同路径时必须保留官方资产，不得因同 item 含独立作答图而误删。
- **type** ∈ `page_image` / `crop_image` / `diagram` / `table_image` / `other_image`
- 新版题目级裁图使用 `type=crop_image`，并把
  `source_page`、`source_bbox_pdf_points`、`crop_receipt_id`、
  `crop_spec_sha256`、`semantic_purity_schema_version`、
  `required_context_ids`、`content_scope` 与精确 `isolation`
  作为不可拆分的 compact controls。完整 `CropReceipt` 保存在当前
  `.ingest/parse_report.json.crop_receipts`，绑定 item/side/role、源文件与源 hash、
  页框与 crop bbox、选择证据、renderer 配置、输出路径/hash/尺寸。Study Guide
  author 会现场按 `crop_receipt_id` 验证 compact 声明与完整 receipt 的一一对应，
  再把完整 receipt hash 绑定进逐题解释请求；缺失、重复、stale、整页回退或源/预览
  不一致都阻断。布局无法确定目标区域时可从显式 `--crop-annotations` 导入
  revision-bound model/human bbox，但 annotation 必须绑定 builder 用同一后端现场渲染的
  当前 PDF 页 preview hash 与尺寸。经证明单题独占的整页也要生成 page-box crop receipt，
  不能以裸 `page_image` 绕过逐题隔离。
  当前 authoring 只接受 semantic-purity schema v2：target-only 使用空
  `required_context_ids` 与 `isolation=target_item_only`；只有 prompt 可使用非空、
  排序唯一的 contexts 与 `isolation=target_with_required_context`。answer 必须始终
  target-only。current v2 的 target/context/detected item IDs 与题库、教学清单、
  `ContentUnit.external_id` 及 typed Guide 共用同一安全 Unicode 稳定技术键契约；
  crop/region/renderer/chapter 等纯控制面 ID 仍使用各自的 portable/hash 契约。
  receipt/semantic v1 以及缺少这组 compact controls 的旧 v2 receipt
  仅可读取为历史，不得进入当前 Study Guide。
- `role` / `type` / `question_text_status` 若写成非字符串（数组/对象等）→ **报错**（校验器不崩溃）；`requires_assets` / `maybe_requires_assets` 必须是真正的布尔 `true`/`false`，字符串 `"false"` 之类 → **报错**。

### Visual-first display contract（运行时强制）

For any item with `requires_assets=true` or `maybe_requires_assets=true`:

1. **Before asking, explaining, hinting, or solving**, display every question-side asset first.
2. Use only question-side assets at first (`question_context` / `figure` / `diagram` / `table`).
3. Label each displayed prompt image PER THE REPLY-LANGUAGE MODE, and include its role/caption when available: `中文`/`双语` sessions use `题面图` for both the image ALT text and the visible label; `English` sessions use `Question-side asset` for both. Behavior probes accept the zh form as well as the legacy bilingual composite `题面图 / question-side asset` (probes only run on zh-mode transcripts). See docs/language-policy.md.
4. Do not show answer-side assets (`answer_context` / `worked_solution`) before all question-side assets have already been shown. Never treat `student_attempt` as either question-side or official answer-side evidence.
5. If the asset file is missing/unreadable, the UI cannot render it, or the runtime can only print an unrenderable path, **skip the item or stop with a clear explanation**. Do not proceed as if the image was shown.
6. Show answer-side assets only during solution/review, after the question-side asset display has happened, and label them per the reply-language mode: `中文`/`双语` → `答案图`, `English` → `Answer-side asset` (probes also accept the legacy `答案图 / answer-side asset` composite).

`stub` / `page_reference` items follow the same principle: the visible prompt context must appear before teaching, quizzing, hinting, or solving. If the original page/resource is not renderable in the current UI, the item is not safe to ask or explain as a complete prompt.

### Markdown / local path display guidance

- Prefer the workspace-relative asset path stored in the schema:
  `![题面图 / question-side asset: Venn diagram](references/assets/ch01_p012_quiz_1_1.png)`.
- Do **not** emit slash-prefixed Windows drive-letter pseudo-paths in Markdown image links.
- If a host requires an absolute path and you have verified that it renders, use that host's supported form. Otherwise show the normal local path as an instruction (for example `D:\course\ws\references\assets\a.png`) and treat the image as **not displayed** for the contract above.
- The skill must not claim that an image was displayed when it only printed a path or a non-rendering Markdown link.

### 路径安全规则（校验器强制）

- asset 必须是工作区内相对路径（推荐 `references/assets/`）；禁止绝对路径、`..`、URL/网络和 symlink escape。所有 host 都执行可移植 Win32 约束：任一路径段不得以 ASCII 空格/点结尾，不得含控制字符或 `< > \" | ? * :`，也不得使用 `CON`/`PRN`/`AUX`/`NUL`/`COM1..9`/`LPT1..9` 等保留设备名（含扩展名及 Win32 认可的上标数字变体）。因此 `a.png.`、`NUL.txt` 等不能在 Windows 上成为另一份安全路径的物理别名。
- `requires_assets=true` 或 `maybe_requires_assets=true` 要求非空、可读、安全且至少一个题面侧 asset；缺失/只有答案侧均为 error。`stub` 需 source page 或题面 asset；`page_reference` 需安全 `source_file` + 正整数 `source_pages`。
- bool/string/enum/page 类型不合约均结构化报错；非 required 的缺失 asset 只 warning。旧题库不含这些字段仍有效；任意题型都可要求 asset。

### 视觉双索引（P0-V2，召回优先）

`build_visual_index.py --workspace <ws> --materials <dir>` 生成两个可重建索引：

- `image_question_index.json`：逐题 requires/maybe、题面/答案 assets、来源页与答案状态。`prompt_suspects`/`answer_suspects` 分别表示视觉来源页缺题面/答案 asset；legacy `suspects` 仅别名。`prompt_suspects=0` 不证明答案/wiki 完整。
- `figure_page_index.json`：检测页、视觉类型与 `wiki_visual_coverage` 的 detected/embedded/missing、分章和逐页理由。检测按结构→排版→词面的召回优先启发式；缺结构能力时写 `media_signals=false` 并告警，不宣称人工语义全覆盖。

默认只报告。`--apply` 先对 quiz、teaching examples、content units 与本批全部题面/答案修复目标做联合策略预检；同一逻辑 item 把同一物理文件跨题面/答案侧复用、任意 `student_attempt` 物理身份复用、不安全/schema 冲突，或与现有所有权不兼容时，整批在创建备份、写图片、改 bank 或替换索引之前失败，原字节与现有派生文件保持不变。没有 student-attempt 污染时，不同 item 间合法的官方题面/答案复用仍可幂等重建。预检通过后才备份 bank，并分别挂 `question_context` + `maybe_requires_assets=true` 或仅挂 `answer_context`；`--apply-wiki` 按页锚幂等回挂（默认每章 30 页），超限/失败仍列入 missing。回写后重读三侧结果。

全局顺序门禁把 answer-only 页放入 `deferred_answer_pages`，不进 concepts/wiki gallery；无法证明归属的旧/手工嵌图列入 `manual_answer_exposure_pages` 并非零阻断。题答同页进入 `shared_prompt_answer_pages`，无审核裁图还进 `shared_prompt_answer_blocker_pages`。所有 `*_count` 必须等于数组长度；完整 manifest 缺安全数组必须重建，不能默认空值绕过泄题。真正无 manifest trio 的 legacy 才走兼容路径；打印路径或忽略非零退出不是修复。

工具：`list_image_questions.py`、`list_figure_pages.py`、`show_question_assets.py`（输出应先展示的题面图 Markdown；违约 exit 1）。PDF 文本含 NUL/控制字节会告警；返回字符串不证明空间图表已保留。

## 5. 题目标签体系（A2，可选字段，向后兼容）

题目 `id` 与教学例题共用同一稳定技术键契约：当前 ingest 一律写为 1–200 字符的安全 Unicode 字符串，禁止空白、控制/格式/代理/替换/Unicode noncharacter code point 或 ``[]#|`/\``；有限数字的旧输入会在发布前规范为字符串，缺 ID 的输入仍由 compiler 生成稳定 `qN`。已有整数 ID 的 legacy bank 仍可只读兼容并按其字符串形式比较，float/bool、非法字符以及字符串/整数规范后重复的 ID 均不可进入 runtime、进度或 typed Guide。

同一技术键进入 `.ingest/content_units.jsonl` 时保存为 `ContentUnit.external_id`，因此 parser/review/add/replace 边界也执行相同契约；原讲义上的自由格式编号或展示标题放在 metadata/文本字段，不能借 `external_id` 绕过后续 Guide 门禁。

每题可选（旧题库仍有效）：

| 字段 | 取值 | 含义 |
| :-- | :-- | :-- |
| `source_type` | `homework` / `lecture_quiz` / `example` / `practice_exam` / `exam` / `other` | 题目来源分类（正交于 `source` 的**答案**来源标注） |
| `knowledge_points` | 非空字符串数组 | 该题考察的知识点标签 |
| `difficulty` | 1–5 整数 | 难度（A7 的评分器回写；手工标注亦可） |
| `difficulty_reason` | 非空字符串 | 难度理由（如「多步条件分布」） |

默认混合题池；持久化 scope 后，临时越界须先提示「⚠️ 临时覆盖你的 <范围> 范围偏好」，限定池排除并计数无 `source_type` 项。`select_questions.py` 组合筛选；可选 SQLite 仅为生成缓存。knowledge postings 直接进 `retrieval_index.json`。builder 可产 `source_type="homework"` 并保留题答页；其余值由可信标注补齐。

## 6. 教学例题层 (`teaching_examples.json`)

builder 可在 `quiz_bank` 外输出 `teaching_examples`，ingest 写入同名文件：

- `quiz_bank.json` 是唯一判分/答案源；题项必须适合抽取、作答与对照答案。
- `teaching_examples.json` 仅为可达性清单，不是第二答案源；无独立答案但有完整演示的 Example 可不进 bank。项含唯一 `id`、chapter/phase、`paired_problem|worked_example`、题答来源与 assets；ID 可与 bank 重叠。`id` 是跨 manifest、notebook 与 typed Guide 共用的技术键：去除输入两端空白后须为 1–200 个字符，可使用安全 Unicode（如 `例题1`、`über_2`），但不得含空白、控制/格式/代理/替换/noncharacter code point 或 ``[]#|`/\``。原讲义题号/展示名若不满足此契约，应保存在标题、来源或其他 display metadata 字段，不得写入同样受稳定技术键契约约束的 `ContentUnit.external_id`，也不能用 ASCII-only 重写造成无迁移的 ID 漂移；若 ID 自身无法形成 GitHub Markdown slug（例如只含 emoji/标点），notebook 写入还必须提供可形成非空 anchor 的人类标题。
- tutor 只用 `list_teaching_examples.py --workspace <ws> --chapter <N> --json` 惰性读取当前章。仅当已存 `preferences.interaction_style=step_by_step`、`processing_mode=full` 且 `no_questions=false`（即 effective step）时可加 `--next-pending`；其他情形 effective cadence 为 `batch`，已存 step 偏好在 lightweight/no-questions 下保留但标为 dormant。`--next-pending` 要求 `<N>` 等于 `current_phase`，在同一 workspace lock 内读取 manifest、state、notebook binding 与 baseline 的一致快照，按 manifest 顺序返回第一道 pending，并报告 total/completed/pending/next、`teaching_example_roster_exhausted` 与不在本章清单中的 unexpected evidence。两个 binding 不能复用同一 `notebook_ref`。manifest 缺失、重复 ID、任一条目无可解析 scope、binding/state 结构损坏，以及 parent/leaf reparse、非目录或非普通文件、realpath escape、非法 UTF-8、未闭 fenced block、parse/block 结构损坏和 unexpected evidence 均失败关闭；只有 notebook 文件/条目缺失或 anchor/marker/hash/revision 漂移可作为 stale binding 从 completed 移回 manifest-order pending，并通过 `stale_binding_count`、最多 32 个 manifest-order `stale_binding_ids` / `stale_binding_problems` 及 `stale_binding_diagnostics_truncated` 给出有界稳定问题码。canonical mount 把结构合法的 stale binding 或 append-only current-roster 新增项报为 `usable_with_gaps`，只为按 manifest 顺序重讲并重新记录的恢复路径保持开放；结构/scope/baseline 损坏仍是 `blocked`，旧 Guide 与 completion receipt 也不能因此继续复用。绝不从 notebook 文件存在性或“继续”推断进度。该锁保证一次读取内部一致，但不是 reservation，两个并发 tutor 仍可能取得同一 pending 项。lightweight 不调用此选择器。
- `teaching_baseline.json` 是 `policy` 精确等于 `append_only` 的保留事实：ingest 只能并入新 ID；每个 ID 必须在 `teaching_examples.json` 中保有同一 canonical chapter 的当前教学快照，`quiz_bank.json` 中仍有同 ID 不能替代教学快照、也不能令 roster 合法耗尽。ID 缓存消失、跨章漂移、逐章映射与全集不一致均 fail-loud；不得手改/清零。legacy 无该文件时才读 `ingest_report.json.teaching_example_ids`。

旧 input 缺字段时不创建/覆盖；显式空数组才表示生产者确认无例题。

## 7. 阶段证据 (`study_state.json.phase_evidence`)

阶段不能只靠 `phase_checklist[].done=true` 完成。显式 full 路线的
`phase_evidence[phase]` 包含：

- `wiki`: `references/wiki/*.md` 路径；必须匹配 `study_plan.md` 为该阶段指定的 wiki。
- `visual`: 两个视觉 manifest 或 `references/assets/` 下的本地资产引用。
- `teaching_examples`: 当前阶段教学例题 ID；该阶段清单非空时必须全部记录，为空时此项 N/A。没有对应 binding 的 ID 是合法的 batch/legacy 教学历史，不得仅因切换节奏而删除或重写。
- `teaching_example_bindings`（可选）：只为 `record-taught-example` 产生的逐题证据保存对象数组；每项字段必须**恰好**为 `{ "id", "notebook_ref", "notebook_block_sha256", "manifest_item_sha256" }`。`id` 必须同时存在于本阶段 `teaching_examples`，`notebook_ref` 必须指向该 ID 带保留 marker 的 `walkthrough` 块，且两个 binding 不得复用同一 ref；两个 SHA-256 分别绑定完整 notebook 块和当前 manifest item。绑定一旦存在，即使后来改回 `batch` 或使 step 偏好 dormant，live marker/type/ID/anchor、notebook 块哈希、manifest item 哈希及对应 notebook evidence 仍必须全部匹配。
- `notebook`: `notebook/*.md#真实锚点`，路径和锚点都必须存在且属于当前章。
- `checkpoint`: `{ "id": "题库ID", "outcome": "passed|wrong|skipped" }`；只有 ID 不能证明答对，且题项必须属于当前阶段。

用 `update_progress.py ... record-phase-evidence --kind <kind> --ref <ref> [--outcome passed|wrong|skipped]` 写入；完成用 `complete-phase --status covered_unverified|verified [--next-phase N]`。

full 单题节奏必须先用 `notebook.py add-entry --type walkthrough --teaching-example` 写完整七步 walkthrough，再把其真实 anchor 交给 `record-taught-example --id <id> --notebook-ref <path#anchor>`；若 ID 本身生成空 Markdown slug，必须提供可生成非空 anchor 的描述性 title。后者在同一 workspace 锁/保存中校验 effective step、当前 full phase、manifest 中的第一道 pending、walkthrough 类型、ID 与保留 marker，并原子写入普通 evidence、notebook ref 与上述四字段 binding。step-by-step 不得拆成两个通用 evidence 命令。Guide notebook publication 遇到有效 binding 时原样保留该 marked walkthrough；binding 已 stale，或 notebook 出现 marker 却没有有效 binding 时失败关闭，不能用 Guide 重写来“刷新”证据。若重新 ingest 后同章 current roster append 新题，或出现可恢复 stale binding，旧 completed phase 在 mount 时只降为 `usable_with_gaps`；结构/基线损坏仍为 `blocked`。按顺序记录第一条 pending 后会清除旧 `status/done`，必须重建 Guide 并重新完成本章。学生回复“继续/看懂了”只是路由输入，不是完成证据。`teaching_example_roster_exhausted=true`（包括零例题）只表示 full teaching roster 无待讲项，不能绕过 Guide、题库、typed unit、资产、checkpoint 或 phase 门禁，也不影响 lightweight 的独立 batch 证据。该机制按稳定题目 ID 记进度；切换输出语言不会自动重置已记录 ID。

full 路线的 `covered_unverified` 要求 wiki/visual/notebook/非空教学清单全覆盖；
`verified` 再需至少 2 个不同 handled checkpoints 且 1 个 pass。`.ingest/`
工作区还须先验证当前章 `profile=full` typed Guide；分母与 unit/ref 完整性由其
validator 负责。

lightweight 路线改用 `lightweight_batches` 数组；每个当前严格事件绑定
`batch_id`、visual/teaching receipt ID、`notebook/chNN.md#anchor` 及其 entry hash、
source hash、升序 `inspected_pages` 与排序去重的稳定 `taught_item_ids`。页码只表示视觉
上下文，不能冒充“整页所有题都已讲”。旧 `pages` 事件只可随 terminal legacy attempt
作为不可升级的审计历史保留。完成阶段时，当前 phase 声明的所有 batch 都必须已 `taught`，
而事件集合与这些 batch 精确一一对应并重新核对 live source、视觉资产与 notebook
entry。它不读取/要求 full 路线遗留的 wiki/visual/teaching evidence，也不加载 typed
Guide 门禁。lightweight 可到 `covered_unverified`；`verified` 仍需已有标准题库中的
至少 2 个不同 handled checkpoints 且 1 个 pass。`no_questions=true` 上限为 covered；
`≤1天` 本身不禁 bank checkpoint。

两视觉索引的 `integrity` 必须同代绑定 schema/time/mode、bank、teaching/baseline/report、wiki、assets、计入 coverage 的图片及 PDF 内容/路径 hashes，并各绑 canonical 输出。完成时重哈希；任一输入漂移即 stale 并重建。required/maybe 题还现场检查可读题面 asset，旧快照/空 suspects 不足。

完整 visual/teaching trio 才启用旧 full 证据硬门禁；真 legacy 可兼容告警，
partial/broken 新 manifest fail-loud。`.ingest/` 独立启用 full typed-guide 门禁；
standing visual 还需 `artifact_ready=ready`。lightweight 使用上述批次事件门禁。两条
路线都只能完成 current phase 并推进到计划中的紧邻下一阶段。

## 8. Validator 结论语义

`scripts/validate_workspace.py --json` 同时输出两个维度：

- `ok=true` / `exit_code=0`：结构化验证过程完成且没有全局致命错误；warnings 仍可能存在。
- 顶层 `readiness=ready|usable_with_gaps|blocked` 保留兼容汇总，但实际动作还必须查看 `capabilities.workspace_structural|teaching_ready|quiz_ready|artifact_ready`。例如可聊天授课不代表可判分，存在一个 HTML/PDF 也不代表教材通过视觉验收。

因此 schema 校验通过、`prompt_suspects=0` 或某一覆盖率为 100% 都不能被单独改写成“全部内容完整”。上层报告必须保留真实分母、剩余 warning 与 readiness 原词。

## 8.1 轻量按需会话

`study_state.json.processing_mode` 只允许 `lightweight|full`；缺失、旧版、
未知或坏类型的有效值都按 `lightweight` 处理，只有显式 `full` 才打开
`ingest_course.py`。它与 `artifact_mode=chat|visual` 相互独立。

`study_state.json.answer_explanation_mode` 只允许 `ordinary|isolated`，并与上述两项
独立。新建、缺失、legacy 或非法值的有效语义一律为 `ordinary`。`ordinary` 仍要求
ingestion-v2 Guide 的每题都有详细、零基础友好的 `answer_explanation`，但解释直接写在
mode-bound annotations 中，不带 Provider/隔离回执；`isolated` 才启用逐题请求、coverage、
host receipt 与最终 contract。该延展模式仅用于 full-v2，并采取两阶段同意：先获知
Provider/API 独立计费与保留/隐私边界，只同意不上传的本地规划后才显式写入；准确 plan
生成后，再核对逐题/图片范围与调用数，由 Agent 按当前官方价格给出有假设的估算，并对
exact plan 作最终上传同意。它不能由模型系列、订阅、API Key、`full` 或 `visual` 推断。

轻量模式不创建 `.ingest/`、wiki、题库、Study Guide 或 PDF；已有 full 产物可保留但
不能作为 lightweight 完成分母。其材料处理状态写入
`.lightweight/session.json`，schema 2 的顶层字段为：

```text
schema_version; session_type=on_demand_visual; processing_mode=lightweight;
workspace; materials; created_at; updated_at; source_inventory[]; batches[];
quiz_bank_baseline; migration_history[]
```

初始化只记录安全相对文件名、媒体类型、大小和 `mtime_ns`，不读取正文，
`content_sha256=null`。`plan` 仅接受 `chapter=current_phase` 的 PDF 页或能确定为单帧的
PNG/JPEG/BMP page-equivalent；GIF/WebP/TIFF 等不会在轻量模式里猜测或压平。
每个 batch 最多 8 页，全 session 最多一个 `planned|visual_ready` 活动批次。batch 绑定
主 source revision、chapter、正序去重页号、`answer_dependencies[]` 和
`planned -> visual_ready -> taught` 状态；未完成 attempt 可进入 `abandoned`，已教 attempt
需要重做时可进入 `superseded` 审计终态：

- 若当前 phase 已有完整 lightweight completion badge，而学生继续新增一个尚未 taught 的
  exact slice，`plan` 会先发布 planned batch，再撤销 phase status/completed time/mode 与
  checklist.done；第二步中断时，重跑完全相同的 plan 只补齐 progress reopening。不同切片、
  不同 revision 或部分损坏的 completion 记录 fail closed；已 taught 的同一切片重跑保持
  幂等且不重新打开阶段；
- `record-visual` 要求计划页逐页双射覆盖、`inspection_method=model_visual`、
  每页一个独立可见资产。单页 batch 的 `contact_sheet_groups=[]`，直接使用页面图；多页
  batch 的 contact sheets 每张最多 4 页，并必须把所有主计划页精确分区一次。它们只作
  `overview_only`，不能替代逐页资产；固定 row-major 尺寸下限约为每 tile 768 px（2 tile
  为 1536×768、3/4 tile 为 1536×1536）；
- `.lightweight/session.json` 仍是 session schema 2；新 `lightweight_visual_batch` 回执则固定
  使用 schema 3。每个 primary page 必须枚举在该页拥有 prompt component 的稳定
  `teaching_item_ids`；同一跨页题可在多页重复，顶层 `teaching_items[]` 与所有页面 ID 的
  union 精确双射，不再嵌套于 `figure_questions`。每个 prompt component 的 parent page 必须
  声明该 item，反之每个 page/item 声明也必须由该页至少一个 prompt component 覆盖；
  每个 item 声明 `kind=text|figure|mixed`、非空 `prompt_components[]`、可为空的
  `answer_components[]` 和 `answer_display_phase=solution_or_review_only`。kind 由 prompt
  component roles 如实决定，因此“纯文字题 + 跨文件官方答案”不会被误报为图题；
- 每个 component 声明唯一 `component_id`、`component_role`、正序去重的
  `required_context_ids`、准确 `allowed_detected_item_ids` 与 source-qualified crop binding。
  prompt allowed IDs 只能是 `[target] + sorted(contexts)` 或非空的纯 `sorted(contexts)`；
  answer component 必须使用前者并包含 target，不能用纯 context crop 冒充答案。每个
  component 必须恰好覆盖它自己声明的 contexts，并且每个 item 至少一个 prompt component
  可见 target。figure/mixed item 必须包含可见的 figure/diagram/table component，text item
  不得伪装含图；
- `register-answer-dependency --batch-id <id> --source <path> --pages <range>` 仍是 planned
  阶段的增量并集操作。`set-answer-dependency ... --pages <exact-range> --reason <reason>` 可替换
  或收窄一个已绑定来源的准确页；`remove-answer-dependency ... --reason <reason>` 可移除它。
  每次真实变化都写 hash-bound `answer_dependency_history`；相同 set 重试不追加事件，相同
  remove 重试必须复用原 reason 且返回 `changed=false`，未知/从未绑定来源仍 fail closed。
  最多 4 个 dependency sources、总计最多 4 页，来源同样只允许 PDF 或单帧 PNG/JPEG/BMP；
- 每个 `dependency_page` 都必须逐页覆盖，`purpose=answer_locator_only`，只可作为
  locator/detail 上下文，整页绝不能进入 solution。primary/dependency page 都显式声明
  `content_types` 和 `answer_provenance=student_attempt|official_solution|none|unknown`，两者
  必须一致。只有 `official_solution` parent 可产生 answer component；每个已注册且声明为
  official 的答案页都必须被至少一个 answer component 覆盖。允许多答案页和多 component；
  学生作答或未知页可保留为 locator/detail，但不能满足资料答案、solution 或教学答案证据；
- `model_calls` 每行绑定唯一 `call_id`、`host`、`model`、带
  `source_id/source_path/source_sha256/page` 的 source-qualified `locations` 与逐输入 asset
  path/hash，不能只写裸页号。每张 contact sheet 恰好进入一次只含该 sheet 的 `overview`；
  需要细看的 primary/dependency pages 与 prompt components 进入 `detail`，其中一条 detail
  可合并多个 prompt components，但只能属于同一 target。answer components 只进入
  `solution`，同一 call 也只能属于同一 target。component crop 不得跨普通 stage 重复消费；
- 每个 prompt/answer component 另有且仅有一个只输入该 crop 的独立 `crop_review` 视觉
  调用，绑定 crop hash、target、side、component ID/role、context/allowed IDs、model
  invocation 与时间。它必须由 `model_vision` 检测并返回与 `allowed_detected_item_ids` 完全相同
  的 ID，且证明没有无关题答或学生作答；bbox、文件名或脚本裁剪成功本身不构成语义纯净
  证明；
- 所有 visual assets 必须位于 `.lightweight/assets/`，不得引用或复用 full-build asset
  路径；无论 source 原本是 PDF、PNG、JPEG 或 BMP，规范 page/contact/prompt/answer/
  dependency evidence 都必须是内容可读、非 link-backed 的 PNG，扩展名与 PNG magic
  bytes 一致并绑定 SHA-256/实测尺寸。通用 crop 下限为 64×64，page/dependency page
  至少 480×480，contact sheet 使用上述 768 px/tile 下限；
- `abandon --batch-id <id> --reason <5-500 字具体原因>` 只允许把 `planned` 或
  `visual_ready` 关闭为 `abandoned`，并保留 prior status、reason、time 与 digest-bound
  receipt；不能删除旧 attempt、改写 reason 或放弃 `taught`。同一 source/revision/
  phase/pages 之后可 `plan` 成带递增 attempt suffix 的新 batch；`abandoned` 不进入阶段
  完成分母，也不占 active slot；
- `replace-taught --batch-id <id> --reason <具体原因>` 是唯一的 taught-redo 路径。它把
  旧 batch 改为 `superseded`，保留其完整 visual/teaching receipts、notebook binding、
  原 progress event、原因和 successor ID，并为相同主 source/chapter/pages 切片建立新的
  `planned` attempt。successor 保留原 dependency 的准确页，同时重新哈希并验证当前来源
  revision，以 `inherited` history 记录继承。旧 attempt/event 永不删除，但不进入当前批次
  分母；新 attempt 必须重新走 schema-3 视觉、教学和发布流程；
- 旧 schema-2 visual receipt 与 `dedicated_figure_question_assets` legacy token strategy
  只可作为不可变历史读取。采用 legacy strategy 的 active attempt（包括尚无 receipt 的
  `planned`）仅允许 `status` 或审计 `abandon`，禁止 dependency 变更、重新 plan、
  `record-visual` 和 `mark-taught`；若旧 attempt 仍是 `visual_ready`，同样只读 quarantine。
  schema-3 receipt 必须绑定 `dedicated_teaching_item_component_assets` generic strategy。
  legacy active attempt 的唯一出口是带原因的可审计 `abandon`，之后另建 schema-3 attempt；
  绝不静默升级旧 strategy 或旧回执；
- `mark-taught --notebook-entry notebook/chNN.md#anchor --taught-item-ids <id1,id2,...>`
  要求锚点唯一定位当前章一个
  durable notebook entry；它重验 current phase、source revision 与每张视觉图片的
  live hash/magic/dimensions，并要求传入 ID 与 visual receipt 枚举的全部教学 item 精确
  相等。新 taught receipt/event 分开保存 `inspected_pages` 与 `taught_item_ids`。命令在
  workspace publication lock 下先原子写 taught
  receipt/session，再原子发布规范事件到
  `study_state.json.phase_evidence[phase].lightweight_batches`。两文件不是任意 reader 的
  单一文件系统快照；若第二步中断，重复同一 `mark-taught` 会识别同一 receipt 并幂等
  补齐 progress event；
- `status` 通过 session/state 文件 generation 前后不变的重试快照实现真正只读，不创建
  lock，也不以写模式打开已有 lock；workspace validator 的 routine mount 路径只做有界
  metadata 与 physical
  identity 检查，不对 source/asset 做 stream hashing。exact stream hash 只在 `plan`、
  dependency 注册/替换/移除、`record-visual`、`mark-taught`、阶段完成和显式
  `status --verify-live` 计算。其他 phase 的 `taught` batch 仍核对 immutable session
  receipt/progress event 身份，统计为 `unchecked_historical` 而非“已现场复验”；切回该
  phase 后才重新进入 current live scope；
- 教学解释固定为详细、零基础友好，不因输入省 token 而压缩；
- metadata/physical identity 漂移会立刻使 batch 失效；关键转换的 exact hash 漂移也会
  fail closed。未完成 attempt 重新 plan，已 taught attempt 用 `replace-taught`，都不能
  改写或复用旧视觉/教学回执。

`.lightweight/session.json` 只记录材料页处理状态；学习阶段、错题、疑难与知识
窗口仍以 `study_state.json` 为唯一事实源。首次 `init` 会在 session 中固化当时
`references/quiz_bank.json` 的不可变 stat-only baseline（存在性、size、mtime、physical
identity），启动时不打开、解析或哈希题库。题库当时不存在、后来才加入、被替换或相对该
baseline 漂移时，均不能支持 lightweight `verified`。只有显式选题/记录 checkpoint/完成
转换才打开题库、执行共享 runtime-eligibility gate，并为合格题建立
`bank_binding_id`、`bank_sha256` 与 `item_sha256`。只有两个不同的 revision-bound handled
checkpoint 且至少一个 `passed` 才能标 `verified`；legacy `{id,outcome}` 可保留为历史但
不能进入 lightweight verified 分母。没有合格预存标准题库时不得伪造测验，阶段上限为
`covered_unverified`。

`artifact_mode` 是独立的持久偏好。轻量下即使 stored preference 为 `visual`，状态接口
也必须报告 `artifact_mode_preference=visual`、`artifact_mode_effective=chat`、
`artifact_mode_dormant=true`；不得进入 Study Guide author/import/render/QA。显式切换
`processing_mode=full` 并重新确认后，偏好才恢复生效。普通 reconfirm 省略
`--processing-mode` 时保留已有规范选择；新建、缺失、legacy、未知或坏类型才安全默认
到 `lightweight`。

## 9. 数学事实源与人类教材产物

Markdown 是可检索/diff/溯源的事实源，不等于已排版教材。`study_state.json.artifact_mode` 只允许 `chat|visual`：

- 缺字段的旧工作区与 `chat` 一样，只保留正常对话、state 与 notebook，不自动编译 HTML/PDF；
- `visual` 必须由用户明确持久化，走 typed manifest → render → receipt → 全页 QA，且 `artifact_ready=ready` 才可交付/完成；一次性打印请求只临时覆盖，不改偏好；
- `processing_mode=lightweight` 时 `visual` 只是 dormant preference，effective mode 固定为 `chat`，一键/长期请求都不能绕过 full-processing gate；
- 未知值按 `chat` 并告警；不猜订阅等级，任何值都不授权静默安装。

写入：`update_progress.py --workspace <ws> set --artifact-mode chat|visual`。

TeX 只用 `$...$`/`$$...$$`；普通括号/方括号或裸命令不是分隔符。validator 忽略 code，但 raw/伪 LaTeX 会 warning 并降为 `usable_with_gaps`，不会自动猜改公式。人类阅读版先验证并原子导入 typed manifest：

  ```text
  python scripts/study_guide_content.py --workspace <ws> validate --chapter <N> --input <draft.json> --json
  python scripts/study_guide_content.py --workspace <ws> import --chapter <N> --input <draft.json> --json
  ```

v2 须先为 workspace-local draft 生成 exact-location claim receipt；import 在 ingestion mutation lock 内从 live facts 复验到发布，并先失效旧 HTML/PDF/render/QA 再发布签名 manifest。v1 不伪造 receipt。

`notebook/chNN.guide.json` 是 renderer/完成门禁输入。`full` 必须覆盖当前章 teaching examples、全部 bank 项（`gradable=false` 仅作教学例题）和 typed question units 的去重 IDs；`abridged` 要完整 omission ledger 且不能完成阶段。`source_unit_ids` 与带理由 `semantic_exclusions` 必须精确分割 material/AI-recovered 语义单元，公式不可排除；这只证明显式分母。每题记录 source/answer provenance、题面语言、公式/变量/代入/步骤/答案/逐题答案详解/来源。新版 ingestion-v2 `authoring_protocol_version=2` 禁止 legacy `self_check`，要求每题的 `answer_explanation` 与逐语言 `ai_supplement` provenance，并以顶层 `answer_explanation_mode` 明确选择回执契约：`ordinary` 禁止 `answer_explanation_contract` 和逐题 receipt；`isolated` 必须具有 exact request/response/provider receipt 与顶层 contract。历史 protocol-v2 manifest 若没有 mode、但具有完整且当前仍可复验的 isolated contract，只能通过 `study_guide_content.py validate --chapter <N>` **省略 `--input`** 的 canonical read-only seam 检查；它不能 import、完成阶段、渲染或 QA。所有显式 input、库级普通 validator 和新写入都必须带 mode，绝不能借兼容路径把 ordinary 冒充 isolated。若严格裁图回执升级只改变 packet/asset 绑定，`study_guide_author.py rebase-annotations` 可在 publication lock 内只更新允许的 packet/mode binding 并成对删除旧 `self_check` 字段；完整 validator 通过前绝不发布。

只有 `isolated` 会生成逐题模型请求。每次调用只可看到这一题的 exact question、answer、target language、固定 beginner-first prompt 与该题 target-scoped assets（`target_item_only`，或仅 prompt 的 `target_with_required_context` 加精确排序的 `required_context_ids`；answer 必须是 `target_item_only`）；attachment binding 必须保留 `semantic_purity_schema_version`、`required_context_ids` 与精确 `isolation`。模型输出只能包含 `answer_explanation` 和不渲染的 `coverage`，其中 `coverage` 按目标语言列出已覆盖子问、至少两步推理，并确认公式/规则与最终含义。两者共同进入 response hash、append-only ledger 与最终 receipt；typed Guide 只复制讲解正文。provider/model/invocation/fresh-context-or-stateless/tool-disabled 声明由 host 的独立 receipt 提供并绑定准确输入 hash；这是 host declaration，不是 sandbox 或模型自证。旧 schema-1 ledger 事件仅为不可复用历史。`ordinary` 不产生这些文件或声明，但解释正文仍受相同的初学者详解、来源、禁止答案自检与渲染门禁。两条路线都要求 material 答案有 answer/solution 证据，AI 答案显示标签；题答须精确绑定 normalized payload；`notebook_anchor` 必须已持久化；`full_prompt` 图片替代重复原/OCR 文且只补翻译，`figure_only` 不替代题干。

语言或 explanation mode 改变会使旧 manifest/artifacts/notebook bindings/claims/QA stale。v2 禁止只改 manifest language/mode 或 relocalize 旧解释；必须从 `study_guide_author.py prepare` 重新完成目标语言、目标 mode 的 annotations、notebook persistence、compile、claims 与 verify/import；`isolated` 还必须重做逐题请求/回执链。ingestion-v1 仅可原位验证既有 canonical manifest，不能 import、relocalize、render 或取得新的完成/QA 结论。视觉模式随后重渲染并全页验收。

通过清单门禁后按明确后端渲染：

  ```text
  python scripts/study_guide_render.py --workspace <ws> --chapter <N> --profile full --pdf-backend html
  python scripts/study_guide_render.py --workspace <ws> --chapter <N> --profile full --pdf-backend browser --pdf
  ```

输出 HTML、可选 PDF 与 schema-3 receipt；MathML/data-URI、可见答案和例题去重由 renderer 保证。receipt 把 typed manifest、HTML、规范 PDF 路径/哈希、后端、转换输入、时间、start-gate 身份和 conversion-run hash 绑定为一条链；`native` 还必须记录机器表 allow-list 中的 adapter ID 与精确版本，并通过 `study_guide_render.py --bind-native` 原子地从 `awaiting_native_pdf` 进入 `qa_pending`。未绑定、部分绑定或旁路放入的 PDF 均不可验收。`source_packet` 仅诊断，不满足 `artifact_ready`。PDF 后运行 `study_guide_qa.py render`，检查全部 PNG，再以 `accept --inspected-pages all` + 每页 `--page-verdict N=pass:<notes>` 验收；任一 hash 漂移即 stale。`visual_qa.status=ready`、全页证据、零缺陷缺一不可。后端见 [`pdf-capability-adapters.md`](pdf-capability-adapters.md)，交付前始终检查最新全页渲染。
