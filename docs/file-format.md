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
