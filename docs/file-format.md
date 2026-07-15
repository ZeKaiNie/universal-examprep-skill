# 工作区文件格式 (Workspace File Format)

本技能建出的备考工作区有一套固定结构与题库 schema。本文件是**规范化文档**，也是
[`scripts/validate_workspace.py`](../scripts/validate_workspace.py) 校验的依据。

## 1. 工作区结构

```text
<workspace>/
  study_plan.md            # 阶段复习计划（各阶段关联哪个 wiki 章节）
  study_state.json         # 结构化进度唯一事实源（存在时）
  exam_runtime_receipt.json # confirm 原子写入的运行来源/版本证明；运行时不得改 package
  study_progress.md        # state 的生成视图；无 Python 时才手工维护
  ingest_report.json       # 导入告警、当前快照统计与兼容基线
  .ingest/                 # 结构化导入事实源（新工作区；不得手改）
    source_raw_input.json  # 编译器输入快照（诊断/重放，不是学生教材）
    parse_report.json      # 稳定解析报告
    source_manifest.json   # 原材料路径、版本哈希、解析状态
    base_content_units.jsonl # 确定性解析器基线
    content_units.jsonl    # 基线 + 已应用 review patch 的编译视图
    base_chapter_phase_mappings.jsonl
    chapter_phase_mappings.jsonl
    review_queue.jsonl     # 带严重级别、证据、状态的 AI/人工接管队列
    review_patches.jsonl   # append-only、可回放的已应用 patch ledger
    pending_patch.json     # 仅事务中存在；残留表示上次补丁应用中断并阻断 readiness
    unbound_review.json    # 尚不能安全绑定到单一来源的告警
    ai_review_manifest.json # 旧消费者兼容视图；不是审查事实源
    evidence/              # 按来源/哈希保存的不可变接管证据快照
    build_manifest.json    # 输入/派生产物哈希与逐页质量路由
    mutation.lock          # 审查写操作互斥锁；运行时临时文件
  references/
    wiki/
      ch1_concepts.md      # 编译后的分章概念教学源（按需 lazy-load）
      ch2_*.md
    quiz_bank.json         # 标准题库（唯一测验/判分题源）
    teaching_examples.json # 可选教学例题层（不是判分答案源，按章 lazy-load）
    teaching_baseline.json # append-only 教学例题保留事实源；不得手改或缩减
    figure_page_index.json # 材料视觉页 + wiki 视觉覆盖
    image_question_index.json # 题面/答案侧视觉覆盖
    retrieval_index.json   # freshness-bound BM25/知识点检索索引
    terms.json             # 可选课程术语桥
    assets/                # 本地题面、答案与 wiki 页面图
  notebook/                # 持久化讲解/反馈（条目锚点可作阶段证据）
    chNN.guide.json        # 已验证的强类型章节教材清单
  study_guide/             # chNN.html/PDF + receipt；qa/ 保存逐页验收证据
```

约定：

- `exam_runtime_receipt.json` records the absolute package root, root `SKILL.md` version, SHA-256 manifest/digest of the shipped runtime surface, Git commit/branch/dirty state (or an explicit unavailable reason), Python executable, and UTC creation time. `exam_start.py confirm` is its only writer. The workspace must be outside the installed package. Every ingestion start recomputes the identity and fails closed on a missing, malformed, link-backed, or drifted receipt; it never edits the installed skill/package to make the comparison pass.
- `references/wiki/` 下每个文件名须为安全相对名 `^[\w.\-]+\.md$`（不得含 `..`、绝对路径、子目录穿越）。
- `study_progress.md` 的「当前阶段」应能对应到 `study_plan.md` 列出的某个阶段。
- `study_progress.md` 应含「💡 概念疑难点记录」区（由 confusion-tracker 维护）。
- `.ingest/` 存在时，`source_manifest.json`、`content_units.jsonl`、`review_queue.jsonl`
  和 `build_manifest.json` 是同一构建的事实源；只用 `scripts/ingest_review.py` 改变接管状态或应用 patch。
  原材料、wiki、题库或检索索引哈希漂移时，validator/retriever 必须拒绝旧派生产物。
- 正常静止状态不应存在 `pending_patch.json`；它若残留，表示补丁事务在 ledger/compiled view/issue 状态全部一致前中断，validator 必须阻断并要求恢复或重建。`mutation.lock` 只负责并发互斥，不是内容事实。

### 结构化 ingestion 事实源

常规建库入口是：

```text
python scripts/ingest_course.py --materials <dir> --workspace <ws> --json
```

它依次做依赖预检、材料解析、结构化编译、进度状态初始化、视觉索引/回挂和最终校验。`--artifact-mode`
只在学生明确给出长期 `chat|visual` 选择时传入；省略表示保留已有选择或默认 `chat`。

退出码与内容就绪状态分离：

| 退出码 | `process_success` | 含义 | 下一步 |
| --- | --- | --- | --- |
| `0` | `true` | 工程流程完成；JSON readiness 为 `ready` 或 `usable_with_gaps` | 后者先逐项报告 warning |
| `10` | `true` | 工程流程完成，但 readiness 为 `blocked` | 进入 typed review，禁止授课/测验 |
| 其他非零 | `false` | 依赖、输入、路径或操作失败 | 修复失败原因后重跑同一命令 |

`source_manifest.json` 为每个来源保存稳定 source ID、材料根目录相对路径、SHA-256、字节数、媒体类型和
解析状态。绝对材料根路径与逐页 accounting 位于 `build_manifest.json`（`source_root` / `page_quality`），
不属于 SourceRecord schema。移动材料或工作区后必须重新建库/重绑，不能把 source drift 当成普通 warning；
分享工作区前也应注意 `source_root` 可能暴露本机路径。

每行 `ContentUnit` 至少包含稳定 `unit_id`、`source_id`/source hash、来源相对路径、元素类型、页码与
页锚、顺序、提取方法/置信度和 provenance；可选保存 bbox、父单元、section path、chapter/phase ID、
公式/HTML、asset path/role 及题答配对。元素类型包括 title/heading/text/list/table/formula/figure/
diagram/caption/code/speaker_notes/question/answer/page_anchor/other。每个已知页面都必须有
`page_anchor`，空白或扫描页也不例外。

`base_*` 文件只保存确定性解析基线；`content_units.jsonl` 与映射文件是基线加已应用 patch 的编译视图。
两者都不能手改。`ReviewIssue` 保存稳定 issue ID、source hash、reason codes、页码、证据引用、目标单元、
severity、说明、建议动作和状态。生命周期状态为 pending/claimed/validated/applied/blocked/resolved/
unrecoverable/superseded；blocking issue 在未进入终态前令 readiness=`blocked`。

接管命令为 `scripts/ingest_review.py --workspace <ws> --json <command>`：用 `list`/`show` 查看，`claim`
原子认领，`validate-patch`/`apply` 校验并应用证据绑定补丁，`mark-unrecoverable --reason ...` 明确关闭
无法恢复项，`rebuild` 从基线 + ledger 重编译 wiki/题库/检索索引。`review_patches.jsonl` 是 append-only
事实源；`.ingest/ai_review_manifest.json` 只是 legacy 兼容视图。

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

> 这些字段与 [`templates/quiz_bank_template.json`](../templates/quiz_bank_template.json) 一致；`ingest.py` 的 `VALID_QUIZ_TYPES`
> 定义了上述 6 类。本规范在其基础上补充了各题型的可选字段与来源校验，供 `validate_workspace.py` 静态检查使用，**不改变既有生成逻辑**。

## 4. 资源依赖与原页引用 (asset-aware fields)

讲义里很多 **Quiz / Example** 题依赖一张图：文氏图（Venn）、页内插图、表格等。题面文字本身不足以独立成题——**不显示那张图，学生根本无法作答**。为此题库项新增一组**可选、向后兼容**字段（老题库不带这些字段仍然有效）。常规入口 `ingest_course.py` 会从 PDF、DOCX、PPTX、txt/Markdown 建立来源与结构化单元；PDF 页面和 OOXML 嵌入图片在能力可用时写成 asset 并保留原页/幻灯片出处。无法确定的图片归属进入 typed review，校验器与出题在缺图时 **fail-closed**。

> `build_raw_input_from_workspace.py` → `ingest.py` → `build_visual_index.py` → `validate_workspace.py` 是维护者定位单步缺陷的低层诊断链，不是学生常规入口。PDF 文本可用 pypdf 或 PyMuPDF；页面渲染可用 PyMuPDF，或 pypdfium2 + Pillow。具体缺项只以 `check_deps.py`/orchestrator 的当前路线报告为准，不在文档中硬编码“唯一安装命令”。纯 DOCX/PPTX/txt/Markdown 的基础解析使用标准库；复杂布局、加密/损坏 OOXML、扫描页和不受支持格式会进入接管队列。

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

- 必须是**相对路径**，且停留在工作区内；**推荐放 `references/assets/`** 下。
- **禁止**：绝对路径、`..` 穿越、URL/网络抓取、符号链接逃出工作区。
- `requires_assets=true` 或 `maybe_requires_assets=true` 时：`assets` 非空，且每个 asset 路径安全、**文件必须真实存在**（缺失是**错误**不是告警）。
- `requires_assets=false` 但带了 assets：合法；asset 文件缺失只**告警**。
- `requires_assets=true` 而题型不是 `diagram` 也合法——很多文字题同样依赖一张表/图/Venn。

### 校验逻辑小结

| 情形 | 结果 |
| --- | --- |
| 老题库（不带这些字段） | ✅ 有效（向后兼容） |
| `requires_assets=true` 或 `maybe_requires_assets=true` 但无 assets / asset 缺失或**不可读** / 路径不安全 | ❌ 错误（fail-closed） |
| `requires_assets=true` 或 `maybe_requires_assets=true` 但只有答案侧 asset（无题面侧有效 asset） | ❌ 错误（出题前无可展示的题面） |
| `question_text_status=stub` 但无 `source_file`+`source_pages` 且无**题面侧有效** asset | ❌ 错误 |
| `question_text_status=page_reference` 但缺 `source_file`/`source_pages`（或 `source_file` 非字符串） | ❌ 错误 |
| asset `role`/`type` 取值非法、`source_pages` 非正整数、`source_file`/`answer_source_file` 非字符串 | ❌ 错误 |
| `source_file`/`answer_source_file` 为绝对路径 / 含 `..` 穿越 / URL | ❌ 错误（provenance 名不得指出材料外） |
| `requires_assets` / `maybe_requires_assets` 非布尔，或 `role`/`type`/`question_text_status` 为非字符串 | ❌ 错误（结构化报错，不崩溃） |

### 视觉双索引（P0-V2，召回优先）

`scripts/build_visual_index.py --workspace <ws> --materials <课程文件夹>` 在 `references/` 下生成两个索引（可再生成物，可随时重建）：

- **`image_question_index.json`** —— 每道题的视觉档案（requires/maybe、题面/答案 asset 路径、`source_file`/`source_pages`、有无官方答案、答案页是否视觉页）+ 按章汇总。疑漏必须分开读：`prompt_suspects` 是题目出处页命中视觉页但无可用题面 asset；`answer_suspects` 是答案出处页命中视觉页但无可用答案 asset。旧字段 `suspects` 仅是 `prompt_suspects` 的兼容别名。**`prompt_suspects=0` 不能证明答案侧或 wiki 侧完整。**
- **`figure_page_index.json`** —— 材料里**每个已检测视觉页**（文件 + 页码 + 视觉类型 `figure/table/diagram/chart/graph/plot/screenshot/circuit/tree/map/geometry/flowchart`），以及 `wiki_visual_coverage`：`detected` / `embedded` / `missing` 总数、按 wiki 章节计数和逐页状态/原因。判定是**分层确定性启发式、不绑任何学科**：① 结构信号（页内嵌图/矢量对象，需 `pip install pymupdf`，没有关键词的图页也能抓到）→ ② 表格列间距、图号/表号与坐标轴排版 → ③ 多学科中英词面（最弱）。缺 PyMuPDF 时结构信号缺失，索引会如实标 `media_signals=false` 并告警。它是召回优先的确定性候选集，不是“人工确认的全部语义图片”。

默认**只报告不改**。`--apply` 会先备份 `quiz_bank.json.bak`，再分别修复两侧：题面疑漏挂 `question_context` 并标 `maybe_requires_assets=true`；答案疑漏只挂 `answer_context`，绝不改变题面门禁或提前展示顺序。`--apply-wiki` 把检测页按 `<!-- source.pdf p.N -->` 页锚幂等回挂到 wiki；默认每章最多 30 页，超出上限或渲染失败的页仍完整保留在 `missing` 清单并带原因。回写后必须重新读取三侧结果，而不是沿用回写前计数。

题面/答案页角色是全局顺序门禁：只被答案出处引用的视觉页进入 `deferred_answer_pages`，不进入 concepts/wiki gallery，也不计作 wiki `missing`；`--apply` 只会把它放进对应 item 的 `answer_context`。较早生成的答案页 wiki block 可由 `--apply-wiki` 幂等移除；无法证明归属的手工/兼容式嵌图会保留原文但写入 `manual_answer_exposure_pages`，索引命令非零退出并阻断阶段完成。若同一整页同时含题面与解答，不能把整页自动当作安全题面图或 wiki 概念图；它进入 `shared_prompt_answer_pages`，尚无经审核题面裁图时还必须进入 `shared_prompt_answer_blocker_pages`。相应 `*_count` 必须与数组长度一致；完整视觉证据 manifest 缺任一安全数组不会默认成空，而是要求重建索引，防止兼容/手写空字段绕过泄题门禁。真正没有视觉/教学 manifest trio 的 legacy 工作区仍走下文的兼容路径。打印路径或忽略一次非零退出均不算修复。

配套官方工具：`list_image_questions.py`（按章 总数×requires×maybe×题面疑漏）、`list_figure_pages.py`（视觉页清单，可按类型过滤）、`show_question_assets.py`（输出某题应先展示的题面图 Markdown，POSIX 相对路径，违约即 exit 1）。PDF 页文本含 NUL/控制字节会进入视觉索引/validator 告警，因为“文本后端返回字符串”不等于空间图表已经被语义保留。

## 5. 题目标签体系（A2，可选字段，向后兼容）

每道题可携带（老题库不带这些字段仍完全有效）：

| 字段 | 取值 | 含义 |
| :-- | :-- | :-- |
| `source_type` | `homework` / `lecture_quiz` / `example` / `practice_exam` / `exam` / `other` | 题目来源分类（正交于 `source` 的**答案**来源标注） |
| `knowledge_points` | 非空字符串数组 | 该题考察的知识点标签 |
| `difficulty` | 1–5 整数 | 难度（A7 的评分器回写；手工标注亦可） |
| `difficulty_reason` | 非空字符串 | 难度理由（如「多步条件分布」） |

**范围过滤契约**：默认混合题池；学生限定范围（如 homework-only）后即为记录在进度状态里的 scope 过滤器——
越范围出题前必须先输出「⚠️ 临时覆盖你的 <范围> 范围偏好」；未标 `source_type` 的题在限定范围内一律排除并报告数量。
官方工具：`scripts/select_questions.py`（组合筛选 + 可选 `--export-sqlite` 生成查询缓存，缓存是生成物不进仓库）。
知识点 postings 已由 `ingest.py` 直接并入 `references/retrieval_index.json`，不再另造会与主检索漂移的 `knowledge_index.json`。

**生产者**：`scripts/build_raw_input_from_workspace.py` 自 A3 起自动产出 `source_type="homework"` 的作业题（题答分离 PDF 配对 / inline Solution / 中英标记），页码出处齐全；其余 source_type 值可手工标注或由后续 ingest 增强补齐。

## 6. 教学例题层 (`teaching_examples.json`)

官方材料 builder 在现有 `quiz_bank` 之外，平行输出顶层 `teaching_examples` 数组；`ingest.py` 将其原样持久化为 `references/teaching_examples.json`。两层用途不同：

- `quiz_bank.json` 是唯一判分/答案源；题项必须适合抽取、作答与对照答案。
- `teaching_examples.json` 是例题可达性清单，不是第二套答案源。一个没有独立标准答案、但材料中完整演示过的 Example 可以从 canonical bank 排除，同时继续供 tutor 精讲。
- 每项保留唯一 `id`、`chapter` 或 `phase`、`teaching_role`（`paired_problem` / `worked_example`）、题面/答案来源页及可用 assets。与 `quiz_bank` ID 重叠合法。
- tutor 只能惰性读取当前章：`python scripts/list_teaching_examples.py --workspace <ws> --chapter <N> --json`；不得为了讲一章把全课程清单装入上下文。
- 新工作区以 `references/teaching_baseline.json` 为独立、append-only 的保留事实源。每次 ingest 只能合并新增 ID，不能因较小的 raw input、重跑或重写 `ingest_report.json` 而缩减；同一 ID 改属其他章会 fail-loud。不要手工编辑、删除或“清零”它。基线 ID 从 `quiz_bank` 与教学层同时消失时 validator 阻断阶段完成；只从 gradable bank 移除不算丢失。
- 没有该文件的旧工作区继续回退读取 `ingest_report.json.teaching_example_ids`；这是兼容路径，不是新格式的首选事实源。

旧 raw input 未带 `teaching_examples` 时，ingest 不创建或覆盖该文件；显式空数组表示生产者确认本次没有教学例题。这样旧工作区保持兼容，新工作区则可证明例题没有在 AI 清理时整体消失。

## 7. 阶段证据 (`study_state.json.phase_evidence`)

新视觉/教学 manifest 工作区不能只靠 `phase_checklist[].done=true` 宣布整章完成。`phase_evidence` 是按阶段号索引的对象，证据字段为：

- `wiki`: `references/wiki/*.md` 路径；必须匹配 `study_plan.md` 为该阶段指定的 wiki。
- `visual`: 两个视觉 manifest 或 `references/assets/` 下的本地资产引用。
- `teaching_examples`: 当前阶段教学例题 ID；该阶段清单非空时必须全部记录，为空时此项 N/A。
- `notebook`: `notebook/*.md#真实锚点`，路径和锚点都必须存在且属于当前章。
- `checkpoint`: `{ "id": "题库ID", "outcome": "passed|wrong|skipped" }`；只有 ID 不能证明答对，且题项必须属于当前阶段。

官方写入示例：

```powershell
python scripts/update_progress.py --workspace <ws> record-phase-evidence --kind wiki --ref references/wiki/ch01.md
python scripts/update_progress.py --workspace <ws> record-phase-evidence --kind visual --ref references/figure_page_index.json
python scripts/update_progress.py --workspace <ws> record-phase-evidence --kind teaching-example --ref ch01-example-1
python scripts/update_progress.py --workspace <ws> record-phase-evidence --kind notebook --ref notebook/ch01.md#example-1
python scripts/update_progress.py --workspace <ws> record-phase-evidence --kind checkpoint --ref ch01-q1 --outcome passed
# 结构化工作区先验证/import profile=full guide；standing visual 再生成并完成全页 QA，使 artifact_ready=ready
python scripts/update_progress.py --workspace <ws> complete-phase --status verified --next-phase 2
```

`covered_unverified` 要求 wiki、visual、notebook 及非空教学清单的全覆盖；`verified` 还要求至少 2 个不同的已处理 checkpoint，其中至少 1 个 `passed`。此外，`.ingest/` 存在的结构化工作区无论采用哪种状态，都必须先由 `study_guide_content.py` 加载并验证当前章 `notebook/chNN.guide.json`，且 `profile=full`；题目分母与 source-unit/source-reference 完整性由该 typed validator 负责，`update_progress.py` 不复制第二套口径。显式 `preferences.no_questions=true` 时上限是 `covered_unverified`。`≤1天` 只跳过开场澄清/偏好询问和反思式追问，不禁止必要的题库 checkpoint；学生明确不要出题时才应用上述上限。

两份视觉索引必须带完全一致的 `integrity` 快照：`schema_version`、UTC `generated_at`、生成
`mode`，以及 quiz bank、teaching manifest、append-only teaching baseline、ingest report、全部 wiki、题库资产、实际计入 wiki coverage 的图片、原始 PDF 内容与 PDF 路径清单 SHA-256；两份派生索引自身也分别绑定 canonical 输出摘要。完成阶段时重新哈希当前输入；索引后任一内容/图片/PDF 被修改、替换、增删都视为 stale，必须重跑
`build_visual_index.py`。当前章声明 `requires_assets` / `maybe_requires_assets` 的题还会独立重查可读的
题面侧 asset，不能只靠旧快照或手写空 suspects 绕过。

只有完整视觉/教学 manifest trio（含 `wiki_visual_coverage` 的 figure 索引、含 `prompt_suspects`/`answer_suspects` 的 image 索引、教学 manifest）才启用旧的视觉/教学证据硬门禁。真正的旧 schema 继续兼容并告警；partial/broken 新 manifest 必须 fail-loud，不能伪装成 legacy 以绕过门禁。独立于此，`.ingest/` 是结构化工作区标记并启用当前章 full typed-guide 门禁；standing `visual` 还会 lazy-load capability readiness，只有 `artifact_ready=ready` 才能完成阶段。阶段完成只能作用于 `current_phase`，推进只能去 `study_plan.md` 中紧接的下一阶段。

## 8. Validator 结论语义

`scripts/validate_workspace.py --json` 同时输出两个维度：

- `ok=true` / `exit_code=0`：结构化验证过程完成且没有全局致命错误；warnings 仍可能存在。
- 顶层 `readiness=ready|usable_with_gaps|blocked` 保留兼容汇总，但实际动作还必须查看 `capabilities.workspace_structural|teaching_ready|quiz_ready|artifact_ready`。例如可聊天授课不代表可判分，存在一个 HTML/PDF 也不代表教材通过视觉验收。

因此 schema 校验通过、`prompt_suspects=0` 或某一覆盖率为 100% 都不能被单独改写成“全部内容完整”。上层报告必须保留真实分母、剩余 warning 与 readiness 原词。

## 9. 数学事实源与人类教材产物

Markdown 是可检索、可 diff、可溯源的事实源，不保证每个聊天客户端都能排版数学，因此不能把
`.md` 文件本身当作已经完成的人类教材。

`study_state.json.artifact_mode` 是独立的资源偏好，canonical 值为 `chat` / `visual`：

- 缺字段的旧工作区与 `chat` 一样，只保留正常对话、state 与 notebook，不自动编译 HTML/PDF；
- `visual` 只在用户明确选择后持久化，并请求“typed manifest → HTML/PDF → receipt → 全页 QA”流程；只有 `artifact_ready=ready` 才能声明生成成功并完成阶段；
- 用户明确提出一次性 HTML/PDF/打印请求时可临时覆盖 `chat`，但不改写长期偏好；
- Agent 不读取或猜测订阅等级。未知值运行时按 `chat` 处理并告警，任何值都不授权静默装依赖。

官方写入入口：`python scripts/update_progress.py --workspace <ws> set --artifact-mode chat|visual`。

- wiki、notebook、mistakes 与 cheatsheet 中的 TeX 数学只使用 `$...$`（行内）或
  `$$...$$`（独立公式）。普通括号/方括号包裹 TeX 命令不是数学分隔符；`(A\\cup B)`、
  `[P(A)=\\frac{1}{2}]` 和正文裸 `\\sum` 都是缺陷。
- `validate_workspace.py` 忽略代码围栏和行内代码，但会对事实源正文中的 raw/伪分隔 LaTeX
  发出 warning，使 readiness 降为 `usable_with_gaps`。它不猜测并自动重写公式，因为错误迁移
  可能改变数学含义。
- 人类阅读版不是四层原文件拼接。Agent 先为当前章制作严格的教学清单，验证后原子导入：

  ```text
  python scripts/study_guide_content.py --workspace <ws> validate --chapter <N> --input <draft.json> --json
  python scripts/study_guide_content.py --workspace <ws> import --chapter <N> --input <draft.json> --json
  ```

  `notebook/chNN.guide.json` 是 renderer 与结构化阶段完成门禁的强类型输入。`full` 的知识点映射和 walkthrough 必须精确覆盖
  当前章 `teaching_examples`、全部题库项（`gradable=false` 仍作为教学例题而非测验题）以及 typed question units
  的去重 ID；`abridged` 也必须用逐项省略清单
  完整解释差集，但不能满足阶段完成。结构化工作区还要求知识点的 `source_unit_ids` 与带理由的
  `semantic_exclusions` 精确分割当前章全部 material/AI-recovered 语义单元；公式单元不得排除。这里的“精确覆盖”仍只证明显式分母，不证明原材料中每个语义主张都已召回。每道例题固定记录 `source_type`、`answer_provenance`、题面、语言、公式使用、变量映射、
  代入式、步骤、答案、自检和来源；`material` 答案必须有 answer/solution 来源证据，AI 答案必须显示完整
  黄/警告标签。直接绑定的题面/答案单元必须显式提供 `metadata.source_language=zh|en`；原题文字、材料答案和
  材料公式按规范化后的精确 payload 绑定，不能用同一文件/页码或模糊关键词冒充内容证据。每个 walkthrough 的
  `notebook_anchor` 也必须指向导入前已经由 `notebook.py` 持久化的真实锚点。完整题面已经在原讲义图片中时不得再粘贴 OCR/原题文字；只显示当前语言尚缺的翻译。
  仅含图表的 `figure_only` 图片不能替代题干文字。
- 用户切换 `study_state.json.language` 后，旧语言 manifest、HTML、PDF、receipt 与 QA 立即视为 stale，不能满足阶段完成或视觉交付。若目标语言块已在清单中写好，运行
  `study_guide_content.py ... relocalize --language zh|en|bilingual` 可无机器翻译地重建当前语言视图；缺少的
  教学/题面翻译必须先由 agent 按来源契约补写。未激活的翻译会保留以支持可逆切换，但不会渲染到当前教材；视觉模式还必须重新渲染并逐页验收。
- 通过清单门禁后按明确后端渲染：

  ```text
  python scripts/study_guide_render.py --workspace <ws> --chapter <N> --profile full --pdf-backend html
  python scripts/study_guide_render.py --workspace <ws> --chapter <N> --profile full --pdf-backend browser --pdf
  ```

  输出 `study_guide/chNN.html`、可选 `chNN.pdf` 和 `chNN.receipt.json`。公式转成原生 MathML，图片内嵌
  为 data URI，答案直接出现在打印文档而非隐藏控件中。每个例题卡只出现一次，并放在主要知识点之后；
  同时涉及的其他知识点在卡片中列明。`--artifact-type source_packet` 可生成单独的
  `chNN.source-packet.html` 供诊断，但它不是教材且不能满足 `artifact_ready`。
- PDF 生成后执行 `study_guide_qa.py render`，用视觉能力检查全部 PNG，再以
  `accept --inspected-pages all` 并为每页重复传入 `--page-verdict N=pass:<notes>` 后才写入验收。PDF、HTML、清单或任何页面哈希漂移都会使旧验收失效；
  `visual_qa.status=ready`、全页证据与零未解决缺陷缺一不可。
- PDF 工具按宿主选择，见 [`pdf-capability-adapters.md`](pdf-capability-adapters.md)；无论使用 Codex、
  Claude Code 或通用后备，交付前都必须逐页渲染为 PNG 并检查最新版本。
