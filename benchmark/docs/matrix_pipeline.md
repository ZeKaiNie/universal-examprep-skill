# 矩阵基准流水线 (Matrix Benchmark Pipeline)

本文件精确描述「答案/判分 → 矩阵 `summary.json` → 报告」这条流水线，以及 **T3 补上的那块**：一个被提交、被测试的聚合/转换脚本，让流水线的**机制**可以用 fixture 数据从干净检出复现。

> 重要边界：**T3 让流水线机制可用 fixture 数据复现；它并不让「已发布的完整 MIT/PSYC 基准」从干净检出重跑**——完整矩阵仍依赖私有/本地的中间产物与付费模型调用（见 §2、§5）。

## 1. 今天已有什么

| 阶段 | 脚本 | 说明 |
| --- | --- | --- |
| 生成 | `benchmark/gen.py` | **增量/补洞**助手：依赖既有 `answers.jsonl` 填补缺失格，**不是**一键全矩阵生成器。 |
| 判分 | `benchmark/judge.py` / `benchmark/rejudge.py` | 数值精确比对 + lexical exact-match + 弃答；可选 LLM 复判（缓存）。`rejudge.py` 内含 `aggregate()` 单元格逻辑，但与判分/读 `results/` 耦合。**T3.1 起** `rejudge.py` 支持 `--scores-out`（**须同时给 `--answers-out`**）零成本导出 `aggregate_matrix.py` 可读的 score/answer 行（answer 行带 `status`/`cost_usd`）——判分与聚合之间的桥。 |
| **聚合** | **`benchmark/aggregate_matrix.py`（T3 新增）** | **显式**读 answer/score 行 → `summary.json` 兼容的矩阵 summary。纯标准库、确定性、无网络/LLM。 |
| 渲染 | `benchmark/report_matrix.py` | summary → 中英双语 `report.html` + 图表 SVG。T3 起支持 `--summary` / `--out-dir`。 |

## 2. 干净检出的现实

- **已发布的矩阵产物**（`results/matrix/summary.json`、`report.html`、图表 SVG）已提交在仓库里。
- **私有/中间的答案产物**（真实模型作答日志、私有金标、付费跑的缓存）**没有**提交。
- 因此「完整 MIT 6.006 / Yale PSYC 110 矩阵」**仍需**私有/本地输入 + 付费模型运行才能从头重跑；T3 不改变这一点，也**不改动任何已发布数字**。

## 3. T3 加了什么

1. **被提交的聚合/转换脚本** `aggregate_matrix.py`：把显式的 answer/score 行聚成 summary（不再像 `rejudge.py` 那样与判分/读 `results/` 纠缠，也**绝不**静默读 `results/matrix/summary.json`）。
2. **fixture 级可复现流水线**：`benchmark/tests/fixtures/matrix_pipeline/`（自撰、微型、非版权）能端到端跑通聚合 + 渲染。
3. **显式 `report_matrix.py --summary <file> --out-dir <dir>`**：渲染器不再被迫只渲染那份已提交的 summary。

### 输入/输出 schema

- **answers.jsonl**（题目全集 + 每次作答）：必需 `course, model, arm, item_id`；可选 `answerable`（`bool`/`0`/`1`——也可改放在对应的 score 行上，即本仓库 `gen.py→judge` 路径；但**至少**答案或判分其一必须给出，否则大声报错。**若两侧都给且不一致**，按 `(course,model,arm,item_id)` 大声报错——不静默偏向任何一侧）、`status`（`"ok"`/`"infra_error"`，默认 `ok`）、`cost_usd`。`id`→`item_id`、`cost`→`cost_usd` 这两个本仓库行内别名也被接受。
- **scores.jsonl**（判分）：必需 `course, model, arm, item_id`；可选 `correct`、`hallucinated`、`abstained`、`judge_error`（`bool` 或 `0`/`1`——`judge.py` 实际写整数）、`faithfulness`（数值，须在 `[0,1]` 内）、`answerable`、`scored_by`。
- **summary（输出）**：`matrix`（主课程的 `model|arm` 单元格，渲染器读这块）、`course_matrix`（**通用**：每门课的单元格）、`cost_per_q`（课→臂的每题成本）、`psyc`（给 `--secondary-course` 的 renderer 兼容块）、`models/arms/courses/n_items/total_cost_usd/judge_model`。

### 诚实计数规则（与 `rejudge.aggregate()` 对齐）

- `infra_error`（限流/上下文报错）**不是**模型答案 → 从正确率分母**剔除**，但计入 `n_infra_error`，**绝不静默丢弃**。
- 已作答的 answerable 题若**没有对应判分** → 记为 `judge_error` 且按**答错**计（可信下界），缺判分**绝不**抬高正确率。
- `correct`/`hallucination` 是已完成 answerable 题上的比率；`faithfulness` 在真正被判过的题上取均值；`abstention_oos` 在已完成的越界题上算。失败/错误/缺失格如实呈现，**不**以抬高指标的方式吞掉失败。

## 4. fixture 命令（干净检出可跑）

```bash
python benchmark/aggregate_matrix.py \
  --answers benchmark/tests/fixtures/matrix_pipeline/answers.jsonl \
  --scores  benchmark/tests/fixtures/matrix_pipeline/scores.jsonl \
  --primary-course courseA --secondary-course courseB \
  --out /tmp/examprep-summary.json

python benchmark/report_matrix.py \
  --summary /tmp/examprep-summary.json \
  --out-dir /tmp/examprep-report
```

## 5. 真实运行的预期路径（需私有输入 + 付费调用，**不在本 PR 跑**）

```text
gen.py（补/生成答案，真实模型，付费）
   → judge.py / rejudge.py（判分/复判，可选 LLM）
   → rejudge.py --deterministic --scores-out（零成本导出 score 行：判分↔聚合之间的桥）
   → aggregate_matrix.py（聚合，零成本）
   → report_matrix.py --summary <summary.json> --out-dir <dir>（渲染）
```

显式、可逐条照跑的命令（在**有真实产物**的机器上；导出/聚合/渲染三步本身零成本、不调用 LLM）：

```bash
# 1) 零成本导出 score 行（+ 严格按 (course,model,arm,item_id) 对齐的 answer 行）。
#    注意：rejudge.py 仍读私有/本地的 results 产物与金标——干净检出上没有这些文件会 FileNotFoundError；
#    本步在有真实产物的机器上跑，deterministic 模式不调用任何 LLM。导出只写 --scores-out/--answers-out
#    指定的显式路径；rejudge 本身仍会照旧写它【gitignored 的】summary_corrected.json/进度文件到
#    results/matrix/（既有行为），但【从不】触碰已发布的 results/matrix/summary.json 与 report.html。
python benchmark/rejudge.py --deterministic \
  --scores-out  /tmp/scores.jsonl \
  --answers-out /tmp/answers.jsonl

# 2) 聚合成 summary（纯标准库、零成本、确定性）
python benchmark/aggregate_matrix.py \
  --answers /tmp/answers.jsonl --scores /tmp/scores.jsonl \
  --primary-course algo --secondary-course psyc \
  --out /tmp/summary.json

# 3) 渲染到一个【自定义】目录（务必带 --out-dir）
python benchmark/report_matrix.py \
  --summary /tmp/summary.json --out-dir /tmp/report
```

几点必须讲清：

- **score-row 导出是桥**：`rejudge.py --scores-out`（**必须同时给 `--answers-out`**）把每题判分规整成 `aggregate_matrix.py` 所需字段（`answerable` 从金标带出，`scored_by` 保留判分自己的标签 lexical/llm/judge_error/infra_error），并把 **answer 行的 `status`（infra_error）与 `cost_usd`** 一并带出——否则聚合器会把 infra 失败误计为可答题、把成本算成 `$0`。**只导出 matrix/psyc 单元格**（不含 `conv_*` 收敛轮，避免 `(course,model,arm,item_id)` 键冲突；遇到重复键**大声报错**，不静默丢弃）；默认不开启、零成本、不调 LLM、export 只写显式路径。
- **`summary_corrected.json` 不能直接渲染**：`rejudge.py` 默认仍写它原本的 `summary_corrected.json`（嵌套 `algo`/`psyc` 结构），那份形状与 `report_matrix.py` 读的顶层 `matrix`/`n_items` **不同**，**不能**直接 `--summary summary_corrected.json` 喂给渲染器；要走 `--scores-out → aggregate_matrix.py` 才能得到渲染器可读的 summary。
- **自定义 summary 必须用 `--out-dir`**：渲染**自定义** `--summary` 时不带 `--out-dir`（即落到默认 `results/matrix/`）会被**拒绝**（退出码 2），以免覆盖已发布的 `results/matrix/report.html`；请始终指到别处。

## 6. 本 PR 不做什么

- **不**重跑 MIT/PSYC 真实基准；**不**改动任何已发布的结果数字/图表。
- **不**提交私有答案产物、私有金标、付费跑产物、或任何版权课程文本/页面图。
- **不**重设计报告视觉风格；**不**改 skill 运行行为、`scripts/ingest.py` 或 P0A/P0B/P0D 的材料构建器。
- **不**新增依赖、GitHub Actions、或任何网络/LLM/API 调用。

> 措辞口径：**「流水线机制可用 fixture 数据复现」**，而非「完整的已发布 MIT/PSYC 基准现在能从干净检出复现」。后者仍需私有/中间产物与付费运行。P0A/P0B 的材料构建器与本基准聚合路径**无关**，不应混为一谈。
