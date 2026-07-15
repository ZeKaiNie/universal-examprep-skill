# 工作区文件格式 (Workspace File Format)

本技能建出的备考工作区有一套固定结构与题库 schema。本文件是**规范化文档**，也是
[`scripts/validate_workspace.py`](../scripts/validate_workspace.py) 校验的依据。

## 1. 工作区结构

```text
<workspace>/
  study_plan.md; study_state.json; study_progress.md
  exam_runtime_receipt.json; ingest_report.json
  .ingest/                         # 构建/审查事实；不得手改
    source_raw_input.json; parse_report.json; source_manifest.json
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
- `exam_start.py confirm` 是 `exam_runtime_receipt.json` 唯一 writer。receipt 绑定绝对 package root、根 `SKILL.md` 版本、运行面 SHA-256、Git identity（或不可用原因）、Python 与 UTC；workspace 必须在 package 外。每次 ingestion 重算 identity，缺失、畸形、link-backed 或漂移均 fail-closed，绝不修改安装包求匹配。
- `.ingest/` 的 manifest、units、review ledger 与 build manifest 必须同代；材料或派生 hash 漂移即拒绝旧产物。v2 还强制 parser receipts 与四个 dedup/conflict sidecar；缺文件、schema/revision/page graph/policy 不一致均 fail-closed。v1 可读但不得冒充 v2 门禁。
- `pending_patch.json` 只允许事务中存在；残留即阻断并要求恢复/重建。`mutation.lock` 仅互斥，不是内容事实。

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
normalized page/element 的可选 `source_language` 只接受显式 `zh|en`；mixed、formula-only 或未知值不得猜测，留空并
产生 typed review。core 也出 receipt；receipt 只证明 exact route/revision/config/accounting。Docling/MinerU 需 host
显式选择并提供 callable runner；适配器自身不安装、联网或上传。policy 是受校验的配置声明，不是 runner sandbox/
attestation；host 负责约束 runner 内部行为。

### XLSX 与 standalone raster 专线

- XLSX 走 stdlib OOXML，不执行公式；每 worksheet 是 page-equivalent，并保留顺序、稀疏 cell/value、公式与 cached value、table、merge 和安全 raster。cached/shared formula 缺失、hidden sheet、外部/网络公式或 unsupported relationship 进入 review。
- PNG/JPEG/GIF/BMP/TIFF/WebP 先验 signature/size/dimensions/hash，再按需物化单页 `source_page` asset。只有显式命名的 `<stem>.ocr.txt` 或 `<image.ext>.txt` 可声明 OCR sidecar；它仍作为独立 `SourceRecord`/parser receipt/content unit 入库，并由图片 anchor 绑定 path/hash/size，图片 unit 不吞并其文本。普通同 stem `.txt/.md` 只是独立课程材料，绝不自动配对；无合格 sidecar 时产生 `standalone_raster_needs_ocr`，交 local OCR/vision 或 typed review，不能把空文本当成功。animated/multi-frame GIF/WebP、APNG 与 multi-page TIFF 不压平成单页：source 记为 `failed` 并产生 blocking typed review。

`base_*` 是确定性基线，compiled units/mappings 是基线 + applied ledger；均不得手改。`ReviewIssue` 绑定稳定 ID、source hash、reason/page/evidence/target/severity/action，状态为 pending/claimed/validated/applied/blocked/resolved/unrecoverable/superseded；blocking issue 未终态即 `blocked`。

`ingest_review.py --workspace <ws> --json <command>` 提供 `list/show/claim/validate-patch/apply/apply-batch/mark-unrecoverable/rebuild`；patch 必须证据绑定且每个 issue 独立，ledger append-only。`apply-batch` 只把派生编译合并到批次末尾。`ai_review_manifest.json` 仅为 legacy view。

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

可选 [`langgraph-host-adapter.md`](langgraph-host-adapter.md) 只为已有 LangGraph host 包装本地命令；checkpoint/thread 仅存 routing hint/有界 receipt，每次 transition 仍从 `study_state.json`、`.ingest/`、runtime/Guide/QA receipts 重新 hydration。

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
  "caption": "Venn diagram for Quiz 1.1"
}
```

- **role** ∈ `question_context` / `answer_context` / `figure` / `table` / `diagram` / `worked_solution`
  - **题面侧 role**（出题前会展示给学生）= `question_context` / `figure` / `diagram` / `table`。`requires_assets=true` 或 `maybe_requires_assets=true` 的题**至少要有一个题面侧的有效 asset**；只有答案侧 asset（`answer_context` / `worked_solution`）无法在出题前展示题目，会被判 fail-closed。
  - **答案侧 role**（只在解答/复盘阶段展示）= `answer_context` / `worked_solution`。本 schema 不新增 `question` / `prompt` role；外部系统若使用这类名字，导入前应映射到现有题面侧 role。
- **type** ∈ `page_image` / `crop_image` / `diagram` / `table_image` / `other_image`
- `role` / `type` / `question_text_status` 若写成非字符串（数组/对象等）→ **报错**（校验器不崩溃）；`requires_assets` / `maybe_requires_assets` 必须是真正的布尔 `true`/`false`，字符串 `"false"` 之类 → **报错**。

### Visual-first display contract（运行时强制）

For any item with `requires_assets=true` or `maybe_requires_assets=true`:

1. **Before asking, explaining, hinting, or solving**, display every question-side asset first.
2. Use only question-side assets at first (`question_context` / `figure` / `diagram` / `table`).
3. Label each displayed prompt image PER THE REPLY-LANGUAGE MODE, and include its role/caption when available: `中文`/`双语` sessions use `题面图` for both the image ALT text and the visible label; `English` sessions use `Question-side asset` for both. Behavior probes accept the zh form as well as the legacy bilingual composite `题面图 / question-side asset` (probes only run on zh-mode transcripts). See docs/language-policy.md.
4. Do not show answer-side assets (`answer_context` / `worked_solution`) before all question-side assets have already been shown.
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

- asset 必须是工作区内相对路径（推荐 `references/assets/`）；禁止绝对路径、`..`、URL/网络和 symlink escape。
- `requires_assets=true` 或 `maybe_requires_assets=true` 要求非空、可读、安全且至少一个题面侧 asset；缺失/只有答案侧均为 error。`stub` 需 source page 或题面 asset；`page_reference` 需安全 `source_file` + 正整数 `source_pages`。
- bool/string/enum/page 类型不合约均结构化报错；非 required 的缺失 asset 只 warning。旧题库不含这些字段仍有效；任意题型都可要求 asset。

### 视觉双索引（P0-V2，召回优先）

`build_visual_index.py --workspace <ws> --materials <dir>` 生成两个可重建索引：

- `image_question_index.json`：逐题 requires/maybe、题面/答案 assets、来源页与答案状态。`prompt_suspects`/`answer_suspects` 分别表示视觉来源页缺题面/答案 asset；legacy `suspects` 仅别名。`prompt_suspects=0` 不证明答案/wiki 完整。
- `figure_page_index.json`：检测页、视觉类型与 `wiki_visual_coverage` 的 detected/embedded/missing、分章和逐页理由。检测按结构→排版→词面的召回优先启发式；缺结构能力时写 `media_signals=false` 并告警，不宣称人工语义全覆盖。

默认只报告。`--apply` 备份 bank 后分别挂 `question_context` + `maybe_requires_assets=true` 或仅挂 `answer_context`；`--apply-wiki` 按页锚幂等回挂（默认每章 30 页），超限/失败仍列入 missing。回写后重读三侧结果。

全局顺序门禁把 answer-only 页放入 `deferred_answer_pages`，不进 concepts/wiki gallery；无法证明归属的旧/手工嵌图列入 `manual_answer_exposure_pages` 并非零阻断。题答同页进入 `shared_prompt_answer_pages`，无审核裁图还进 `shared_prompt_answer_blocker_pages`。所有 `*_count` 必须等于数组长度；完整 manifest 缺安全数组必须重建，不能默认空值绕过泄题。真正无 manifest trio 的 legacy 才走兼容路径；打印路径或忽略非零退出不是修复。

工具：`list_image_questions.py`、`list_figure_pages.py`、`show_question_assets.py`（输出应先展示的题面图 Markdown；违约 exit 1）。PDF 文本含 NUL/控制字节会告警；返回字符串不证明空间图表已保留。

## 5. 题目标签体系（A2，可选字段，向后兼容）

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
- `teaching_examples.json` 仅为可达性清单，不是第二答案源；无独立答案但有完整演示的 Example 可不进 bank。项含唯一 `id`、chapter/phase、`paired_problem|worked_example`、题答来源与 assets；ID 可与 bank 重叠。
- tutor 只用 `list_teaching_examples.py --workspace <ws> --chapter <N> --json` 惰性读取当前章。
- `teaching_baseline.json` 是 append-only 保留事实：ingest 只能并入新 ID，同 ID 换章或从 bank+教学层同时消失均 fail-loud；不得手改/清零。legacy 无该文件时才读 `ingest_report.json.teaching_example_ids`。

旧 input 缺字段时不创建/覆盖；显式空数组才表示生产者确认无例题。

## 7. 阶段证据 (`study_state.json.phase_evidence`)

新 manifest 工作区不能靠 `phase_checklist[].done=true` 完成；`phase_evidence[phase]` 包含：

- `wiki`: `references/wiki/*.md` 路径；必须匹配 `study_plan.md` 为该阶段指定的 wiki。
- `visual`: 两个视觉 manifest 或 `references/assets/` 下的本地资产引用。
- `teaching_examples`: 当前阶段教学例题 ID；该阶段清单非空时必须全部记录，为空时此项 N/A。
- `notebook`: `notebook/*.md#真实锚点`，路径和锚点都必须存在且属于当前章。
- `checkpoint`: `{ "id": "题库ID", "outcome": "passed|wrong|skipped" }`；只有 ID 不能证明答对，且题项必须属于当前阶段。

用 `update_progress.py ... record-phase-evidence --kind <kind> --ref <ref> [--outcome passed|wrong|skipped]` 写入；完成用 `complete-phase --status covered_unverified|verified [--next-phase N]`。

`covered_unverified` 要求 wiki/visual/notebook/非空教学清单全覆盖；`verified` 再需至少 2 个不同 handled checkpoints 且 1 个 pass。`.ingest/` 工作区还须先验证当前章 `profile=full` typed Guide；分母与 unit/ref 完整性由其 validator 负责。`no_questions=true` 上限为 covered；`≤1天` 本身不禁 bank checkpoint。

两视觉索引的 `integrity` 必须同代绑定 schema/time/mode、bank、teaching/baseline/report、wiki、assets、计入 coverage 的图片及 PDF 内容/路径 hashes，并各绑 canonical 输出。完成时重哈希；任一输入漂移即 stale 并重建。required/maybe 题还现场检查可读题面 asset，旧快照/空 suspects 不足。

完整 visual/teaching trio 才启用旧证据硬门禁；真 legacy 可兼容告警，partial/broken 新 manifest fail-loud。`.ingest/` 独立启用 full typed-guide 门禁；standing visual 还需 `artifact_ready=ready`。只能完成 current phase 并推进到计划中的紧邻下一阶段。

## 8. Validator 结论语义

`scripts/validate_workspace.py --json` 同时输出两个维度：

- `ok=true` / `exit_code=0`：结构化验证过程完成且没有全局致命错误；warnings 仍可能存在。
- 顶层 `readiness=ready|usable_with_gaps|blocked` 保留兼容汇总，但实际动作还必须查看 `capabilities.workspace_structural|teaching_ready|quiz_ready|artifact_ready`。例如可聊天授课不代表可判分，存在一个 HTML/PDF 也不代表教材通过视觉验收。

因此 schema 校验通过、`prompt_suspects=0` 或某一覆盖率为 100% 都不能被单独改写成“全部内容完整”。上层报告必须保留真实分母、剩余 warning 与 readiness 原词。

## 9. 数学事实源与人类教材产物

Markdown 是可检索/diff/溯源的事实源，不等于已排版教材。`study_state.json.artifact_mode` 只允许 `chat|visual`：

- 缺字段的旧工作区与 `chat` 一样，只保留正常对话、state 与 notebook，不自动编译 HTML/PDF；
- `visual` 必须由用户明确持久化，走 typed manifest → render → receipt → 全页 QA，且 `artifact_ready=ready` 才可交付/完成；一次性打印请求只临时覆盖，不改偏好；
- 未知值按 `chat` 并告警；不猜订阅等级，任何值都不授权静默安装。

写入：`update_progress.py --workspace <ws> set --artifact-mode chat|visual`。

TeX 只用 `$...$`/`$$...$$`；普通括号/方括号或裸命令不是分隔符。validator 忽略 code，但 raw/伪 LaTeX 会 warning 并降为 `usable_with_gaps`，不会自动猜改公式。人类阅读版先验证并原子导入 typed manifest：

  ```text
  python scripts/study_guide_content.py --workspace <ws> validate --chapter <N> --input <draft.json> --json
  python scripts/study_guide_content.py --workspace <ws> import --chapter <N> --input <draft.json> --json
  ```

v2 须先为 workspace-local draft 生成 exact-location claim receipt；import 在 ingestion mutation lock 内从 live facts 复验到发布，并先失效旧 HTML/PDF/render/QA 再发布签名 manifest。v1 不伪造 receipt。

`notebook/chNN.guide.json` 是 renderer/完成门禁输入。`full` 必须覆盖当前章 teaching examples、全部 bank 项（`gradable=false` 仅作教学例题）和 typed question units 的去重 IDs；`abridged` 要完整 omission ledger 且不能完成阶段。`source_unit_ids` 与带理由 `semantic_exclusions` 必须精确分割 material/AI-recovered 语义单元，公式不可排除；这只证明显式分母。每题记录 source/answer provenance、题面语言、公式/变量/代入/步骤/答案/自检/来源；material 答案需 answer/solution 证据，AI 答案显示标签。直接绑定题答 unit 必须有 `metadata.source_language=zh|en` 并精确绑定 normalized payload；页码/关键词不足。`notebook_anchor` 必须已持久化。`full_prompt` 图片替代重复原/OCR 文，仅补翻译；`figure_only` 不替代题干。

语言改变使旧 manifest/artifacts/receipts/QA stale。v2 用 `relocalize --language <zh|en|bilingual> --output <workspace-relative-draft>` 生成不覆盖 canonical 的 staging，补 claims 后 `verify_claims.py verify`，再 import；缺 `--output` 失败。v1 可一键 relocalize。视觉模式随后重渲染并全页验收。

通过清单门禁后按明确后端渲染：

  ```text
  python scripts/study_guide_render.py --workspace <ws> --chapter <N> --profile full --pdf-backend html
  python scripts/study_guide_render.py --workspace <ws> --chapter <N> --profile full --pdf-backend browser --pdf
  ```

输出 HTML、可选 PDF 与 receipt；MathML/data-URI、可见答案和例题去重由 renderer 保证。`source_packet` 仅诊断，不满足 `artifact_ready`。PDF 后运行 `study_guide_qa.py render`，检查全部 PNG，再以 `accept --inspected-pages all` + 每页 `--page-verdict N=pass:<notes>` 验收；任一 hash 漂移即 stale。`visual_qa.status=ready`、全页证据、零缺陷缺一不可。后端见 [`pdf-capability-adapters.md`](pdf-capability-adapters.md)，交付前始终检查最新全页渲染。
