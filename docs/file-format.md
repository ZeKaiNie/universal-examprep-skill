# 工作区文件格式 (Workspace File Format)

本技能建出的备考工作区有一套固定结构与题库 schema。本文件是**规范化文档**，也是
[`scripts/validate_workspace.py`](../scripts/validate_workspace.py) 校验的依据。

## 1. 工作区结构

```text
<workspace>/
  study_plan.md            # 阶段复习计划（各阶段关联哪个 wiki 章节）
  study_progress.md        # 当前断点 + 知识点打卡 + 错题档案 + 💡 概念疑难点记录
  references/
    wiki/
      ch1_concepts.md      # 分章节知识库（唯一知识源，按需 lazy-load）
      ch2_*.md
    quiz_bank.json         # 标准题库（唯一答案源）
```

约定：

- `references/wiki/` 下每个文件名须为安全相对名 `^[\w.\-]+\.md$`（不得含 `..`、绝对路径、子目录穿越）。
- `study_progress.md` 的「当前阶段」应能对应到 `study_plan.md` 列出的某个阶段。
- `study_progress.md` 应含「💡 概念疑难点记录」区（由 confusion-tracker 维护）。

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
2. **缺答案如实标注（告警）**：一道题缺 `answer` 时报**告警**（建议补 `answer`，或标 `answer_status: "unknown"` / `source: "ai_generated"`）。这与 `ingest.py` 对「无答案题」**告警但不失败**的行为一致——由 `ingest.py` 正常产出的工作区不会被 Tier 1 判为无效。
3. **缺 `source`**：有答案但未标 `source` → **告警**（建议补全来源）。

> `chapter`（或 `phase`）用于章节复习过滤抽题，强烈建议每题都带；但 `ingest.py` 不强制，故缺失只报**告警**（不判工作区无效）。

> 这些字段与 [`templates/quiz_bank_template.json`](../templates/quiz_bank_template.json) 一致；`ingest.py` 的 `VALID_QUIZ_TYPES`
> 定义了上述 6 类。本规范在其基础上补充了各题型的可选字段与来源校验，供 `validate_workspace.py` 静态检查使用，**不改变既有生成逻辑**。

## 4. 资源依赖与原页引用 (asset-aware fields)

讲义里很多 **Quiz / Example** 题依赖一张图：文氏图（Venn）、页内插图、表格等。题面文字本身不足以独立成题——**不显示那张图，学生根本无法作答**。为此题库项新增一组**可选、向后兼容**字段（老题库不带这些字段仍然有效）。配套的官方入口 **[`scripts/build_raw_input_from_workspace.py`](../scripts/build_raw_input_from_workspace.py) 从 PDF 材料产出这些字段**（整页渲染成 asset、保留原页出处、抽取 Example/Quiz 题—解对）；校验器与出题在缺图时**fail-closed**。

> 官方流程（脚本随 `python` 调用，无需可执行位）：`python scripts/build_raw_input_from_workspace.py --materials <dir> --out raw_input.json --asset-root <ws>/references/assets` → `python scripts/ingest.py -i raw_input.json -o <ws>` → `python scripts/validate_workspace.py <ws>`。PDF 文本/渲染为**可选依赖**——文本 `pip install pypdf`；渲染 `pip install pymupdf`（自带 PNG）或 `pypdfium2 Pillow`（缺 Pillow 时 pypdfium2 不算渲染后端）。缺依赖会清晰报错；纯 `.txt/.md` 无需依赖。渲染须用 `--asset-root` 指向 `<ws>/references/assets`，否则 auto 跳过渲染并告警、required 报错。

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
3. Label each displayed prompt image as `题面图 / question-side asset` and include its role/caption when available.
4. Do not show answer-side assets (`answer_context` / `worked_solution`) before all question-side assets have already been shown.
5. If the asset file is missing/unreadable, the UI cannot render it, or the runtime can only print an unrenderable path, **skip the item or stop with a clear explanation**. Do not proceed as if the image was shown.
6. Show answer-side assets only during solution/review, after the question-side asset display has happened, and label them `答案图 / answer-side asset`.

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

- **`image_question_index.json`** —— 每道题的视觉档案（requires/maybe、题面/答案 asset 路径、`source_file`/`source_pages`、有无官方答案、**答案页是否视觉页**）+ 按章汇总（总题数 × requires × maybe × 疑漏）+ **suspects 疑漏名单**：出处页命中视觉页、但题库未标图依赖且无题面 asset 的题。
- **`figure_page_index.json`** —— 材料里**每个视觉页**（文件 + 页码 + 视觉类型 `figure/table/diagram/chart/graph/plot/screenshot/circuit/tree/map/geometry/flowchart`）。判定是**分层确定性启发式、不绑任何学科**：① 结构信号（页内嵌图/大量矢量对象，需 `pip install pymupdf`，**没有关键词的图页也能抓到**）→ ② 图号/表号与坐标轴排版 → ③ 多学科中英词面（最弱）。缺 PyMuPDF 时结构信号缺失，索引会如实标 `media_signals=false` 并告警。

默认**只报告不改**；`--apply` 会把每个疑漏题的原页渲染成 PNG（挂 `question_context`/`page_image` 题面 asset）并标 `maybe_requires_assets=true`（先备份 `quiz_bank.json.bak`），因此回写后仍满足上表的 fail-closed 门控。配套官方工具：`list_image_questions.py`（按章 总数×requires×maybe×疑漏）、`list_figure_pages.py`（视觉页清单，可按类型过滤）、`show_question_assets.py`（输出某题应先展示的题面图 Markdown，POSIX 相对路径，违约即 exit 1）。

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
官方工具：`scripts/select_questions.py`（组合筛选 + 可选 `--export-sqlite` 生成查询缓存，缓存是生成物不进仓库）、
`scripts/build_knowledge_index.py`（知识点 ↔ 章节/wiki/题目 索引，页码级引用留待 A5）。

**生产者**：`scripts/build_raw_input_from_workspace.py` 自 A3 起自动产出 `source_type="homework"` 的作业题（题答分离 PDF 配对 / inline Solution / 中英标记），页码出处齐全；其余 source_type 值可手工标注或由后续 ingest 增强补齐。
