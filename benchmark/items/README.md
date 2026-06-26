# items/ —— 金标题集（ground truth）

测试的核心。每道题是一行 JSON（JSONL 格式），既给"标准答案"，也给"答案出自材料哪一句"
（supporting span）——后者让裁判判"答得是否忠于材料"，也是数值题判分的依据。

复制 `items.example.jsonl` 为 `items.jsonl`，按下面规范、依据你**自己的课件/作业**改写。

## 字段规范

| 字段 | 必填 | 说明 |
| :-- | :-: | :-- |
| `id` | ✓ | 唯一编号，如 `q1` |
| `question` | ✓ | 题面（学生真会问的问题） |
| `gold_answer` | ✓ | 标准答案（简洁、确定）。越界题（answerable=false）留空 `""` |
| `supporting_span` | ✓* | 支撑答案的**材料原文摘抄** + 出处；越界题留空 |
| `source_file` | ✓* | 该题出自哪个材料文件，如 `materials/ds/ch5_search.pdf` |
| `answer_type` | ✓ | `factual`（事实）/ `definition`（定义）/ `numeric`（计算/数值） |
| `answerable` | ✓ | `true`=材料能答；`false`=**越界探针**（材料里没有，正确行为是弃答） |
| `tolerance` | numeric 时 | 数值题允许误差，如 `0`（精确）或 `0.01` |

## 怎么编（建议流程）

1. **通读你的讲义/作业**，挑出 15~40 个高频考点，写成问题 + 标准答案。
2. **每条标注 `supporting_span`**：把答案依据的那句材料原文抄进来，并写清 `source_file`。
   （可以让 AI 起草，但你要逐条核对——AI 起草、人工把关。）
3. **故意放几条越界探针**（`answerable:false`）：学生可能会问、但你的材料根本没讲的问题
   （如"考试在哪个教室""老师叫什么"）。这是检验 skill"宁可说不知道也不编"的关键——
   它直接对应 skill 宣称的"物理防幻觉"。
4. **计算题**（`answer_type:"numeric"`）：尽量来自作业，记准确数值 + `tolerance`；这类题由脚本
   **确定性判分**（不经过 LLM 裁判），最干净。
5. 存成 `items.jsonl`。**小而全标注 > 大而粗糙**：30 题左右、每题都有金标和出处，就足够出一份可信报告。

> 平台/英文版规划：题面与裁判语言无关，现在用中文，将来做英文版时同一套 harness 直接复用，
> 只需另出一份英文 `items.jsonl`。
