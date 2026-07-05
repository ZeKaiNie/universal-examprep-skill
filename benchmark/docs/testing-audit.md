# 测试与 Benchmark 审计 (Testing & Benchmark Audit)

> 这是一份**诚实的现状快照**，用来在投入昂贵测试之前，把「我们到底测了什么、没测什么」说清楚。
> 非营销文档。结论指向 PR 路线（见文末）。当前进度：**T1**（审计文档 + 一致性守卫）、**T2**（Tier 2 确定性行为冒烟层）、**T3/T3.1**（提交版聚合器 `aggregate_matrix.py` + fixture 流水线 + 判分↔聚合桥）、**T4**（Tier 4 长程漂移**确定性 replay harness** [`drift/`](../drift/)）已落地；T2 的**真 LLM 行为**、T4 的**真 LLM 长会话**与 **T5** 仍待做（完整发布矩阵仍需私有/付费产物）。本文件是跨 PR 维护的活快照，不跑付费 benchmark。

---

## 1. 测试分层与实际运行 (test tiers)

| 层 | 是什么 | 进 CI？ | 成本 | 现实 |
| :-- | :-- | :--: | :-- | :-- |
| **Tier 0** | `python -m unittest discover -s tests`（stdlib 单元/静态测试套件） | ✅ Ubuntu + Windows × Py 3.8 + 3.12 | $0，纯标准库、无网络、无 API key | CI **唯一**实际运行的一层 |
| **Tier 1** | `python scripts/validate_workspace.py <工作区>`（已建工作区结构/schema/来源标注/路径安全） | 🟡 集成测试随 Tier 0 进 CI | $0 | 校验器逻辑由 Tier 0 单测覆盖；**B6 起**另有确定性 `ingest → validate` 集成测试（`tests/test_ingest_validate_integration.py`：真跑两个 CLI，真实 ingest 产物必须过校验、篡改/缺图/坏 JSON 必须 1/2 退出）——经根测试进 CI，无独立 Actions 步骤（刻意不加 CI 配置） |
| **Tier 2** | **行为冒烟**：确定性 mock 层（见 [`behavior_smoke/`](../behavior_smoke/)） | ✅ mock 进 CI | 近 $0 | 确定性层已落地；**T5c 起有 opt-in 真 agent 冒烟 runner**（[`drift/run_live_smoke.py`](../drift/run_live_smoke.py)：驱动→记录→T4 判分一条命令，env 门控、不进 CI）；**真实模型的验证运行尚未实现（未实际跑过真模型）**，见 §4 |
| **Tier 3** | 完整 benchmark（`gen.py` → 判分 → 矩阵报告） | ❌ | **昂贵**：单轮矩阵约几十美元 / 数小时 | 手动、临时触发 |
| **Tier 4** | 长程漂移（long-horizon drift） | 🟡 replay 层进（根级）测试 | replay $0；真 LLM 以天计额度 | **确定性 replay harness 已落地**（[`drift/`](../drift/)，回放脚本化 transcript，纯 stdlib、零成本）；**真 LLM 长会话仍 opt-in、未实现、不进 CI** |

**CI 现状**：`.github/workflows/ci.yml` 只有一步 `python -m unittest discover -s tests -v`，即只跑 Tier 0（**现已包含 `tests/test_behavior_smoke.py`——Tier 2 的确定性 mock 层**）；全部零成本。README 也已据此澄清为「CI 实际只跑 Tier 0」——Tier 1 校验器的逻辑由 Tier 0 单测在 `tests/fixtures/` 上覆盖；**B6 起** `ingest → validate` 的确定性集成测试（真跑两个 CLI）也落在根 `tests/` 随 Tier 0 进 CI（仍无独立 Actions 步骤，刻意不加 CI 配置）。

---

## 2. Tier 0 校验什么

`tests/` 下是一组 stdlib 单元/静态测试套件（运行 `python -m unittest discover -s tests -v` 查看当前数量与明细），覆盖：

- **工作区校验器**：结构、题库 schema（6 种题型）、来源标注（`source` 枚举 + `ai_generated` 标记强制）、**路径安全**（符号链接、目录穿透、反斜杠、Markdown 链接、NaN）、退出码 0/1/2。
- **ingest 端到端**：生成文件、替换锚点、坏输入大声报错、拒绝穿透/重名 wiki、接受 6 种题型、缺答案告警、rerun/`--force` 备份。
- **语言策略 / 控制层双语**：canonical 来源标注用词、英文控制层标题、学生侧默认简体中文、无模糊措辞。
- **技能集合自洽**：`skills/` frontmatter（name/description/license）、AGENTS 兜底不变量、文档存在、confusion-tracker 已并入 `skills/`。
- **运行时措辞**：无版本号 / 无抽象「协议」措辞。

**性质**：这些都是**结构 / schema / 文件内容**断言 + `ingest`/`validate_workspace` 脚本测试。它们证明「指令与产物存在且格式正确」，**不执行真实 agent 行为**。

## 3. Tier 1 校验什么

`scripts/validate_workspace.py` 对一个**已建好的备考工作区**做零成本校验：目录结构、`quiz_bank.json` schema（题型/选项/答案在选项内/主观题关键词/diagram_type/true_false 布尔/source 枚举/`ai_generated` 标记）、`references/wiki/` 路径安全、进度文件一致性；退出码 `0` 通过 / `1` 有错 / `2` 致命（结构损坏或非法 JSON）。它由 Tier 0 单测在 fixtures 上驱动；**B6 起**另有 `tests/test_ingest_validate_integration.py` 在**真实 ingest 产物**上真跑该 CLI（happy 0 / 篡改 1 / 坏 JSON 2），随根测试进 CI。

## 4. Tier 2 行为冒烟：确定性层已落地，真 LLM 行为仍 opt-in（未进 CI）

本 PR（T2）落地了 Tier 2 的**确定性 mock 层**——自撰小型 fixture（`benchmark/behavior_smoke/fixtures/mini_course`，过 Tier-1 校验、覆盖全 6 题型）+ 一组**确定性探测器**，对 mock 产物断言下列行为，全部进 CI、零成本：

- quiz_bank-only 出题（题号必须 ∈ 题库；编造题号被抓）
- 来源标注 🟢/🟡/⚠️ canonical 输出
- 提示 / 跳过 / 错题归档（逃生通道 + 错题行写入）
- 概念疑难点追踪（疑难行写入进度）
- study_progress 断点恢复（读出当前阶段）
- 无 Python 环境降级写盘（手写工作区过校验）
- 0 基础重点题精讲（已升级为 A5 七步模板）

A 线各阶段又新增 **5 个 Tier 2 确定性行为场景**（`behavior_smoke/scenarios.json`，B1 收尾登记进 [`coverage-matrix.md`](coverage-matrix.md)）：

- 视觉题 题面图门禁（`visual_first_assets`，P0-V1/A1）
- 范围过滤 + 越界覆盖声明（`scope_override`，A2）
- 七步讲解模板 + 每题来源块（`teaching_template`，A5）
- ≤1天档严禁向学生提问（`time_budget_no_questions`，A6）
- 窗口外知识点须真复核（`knowledge_window_recheck`，A6；>7天须出题实测）

另有 **1 个 Tier 4 长会话 replay 场景**（`benchmark/drift/scenarios/mode_urgent_no_questions.json`，**不是** Tier 2 smoke）：模式漂移——≤1天零提问 `urgent_mode_questions` + 紧迫开场推断并落盘 `urgent_mode_persisted`。

**仍诚实**：确定性层只证明探测器对**预期产物**成立，**不证明真 LLM agent 一定产出这些行为**。**真 LLM 行为验证仍 opt-in、不进 CI、尚未实现**（`--llm` 仅 skeleton，需 `RUN_SKILL_BEHAVIOR_LLM=1`）；**LLM Wiki 惰性加载**与**画图先跑算法再画**仍为 best-effort / 未覆盖。详见 [`../behavior_smoke/README.md`](../behavior_smoke/README.md) 与 [`coverage-matrix.md`](coverage-matrix.md)。

## 5. Tier 3 完整 benchmark：手动、昂贵

跨 `课程 × 模型 × 臂` 的真实 LLM 矩阵，单轮约几十美元 / 数小时。它测的是**有据问答的 grounding / 幻觉 / 越界弃答**，不是交互式辅导流程。只在「故意改了技能行为 / 加了新课程材料 / 发布前」时手动触发，不应进 CI、不应为每个小改动跑。

## 6. Tier 4 长程漂移：确定性 replay 已落地，真 LLM 长会话仍未来工作

长对话（数十轮 + 中途干扰）下的目标保持 / 计划遵守 / 编题率 / 断点恢复 / 来源标注 / 进度持久性。**T4 已落地一个确定性 replay harness** [`drift/`](../drift/)：回放脚本化的多轮 transcript + 工作区快照（自撰非版权 fixture），对上述维度做**确定性**测量并与阈值比对——纯 stdlib、零成本、进根级测试（`python benchmark/drift/run_drift.py --all`）。**但这是回放脚本化会话、不跑真 agent**：它度量「一段被记录的会话有没有漂移」，**不**证明在线模型不会漂移。**真 LLM 长会话仍 opt-in（`--llm` + `RUN_SKILL_DRIFT_LLM=1`）、未实现、绝不返回成功、不进 CI**；完整长程 LLM benchmark（以天计额度）仍为未来工作。

---

## 7. Benchmark 管线现状（诚实标注）

- **两个并存的 runner，臂口径不同**：
  - `run_benchmark.py` 是**较早的两臂脚手架**（`baseline` / `skill`），不可断点续跑、且**丢弃成本**。
  - `gen.py` 是**较新的矩阵答案生成**路径，支持全部四臂（`closedbook` / `material` / `rawfiles` / `skill`）、**可断点续跑、按格记录成本**。但它是**按可行性排序的增量补齐器，不是从零全量生成**：`build_tasks()` 实际只排入 rawfiles（两门课）、PSYC 的 closedbook/skill、**仅 algo `material` 中报错的那些重跑**（从既有 `results/matrix/answers.jsonl` 读取），以及 PSYC material——它**依赖一份既有的 `answers.jsonl`**（`build_tasks()` 无条件打开它），而该文件**并未提交**——故在干净 checkout 上 `python benchmark/gen.py` 会直接 `FileNotFoundError`、**根本跑不起来**；它还**硬编了 MIT/PSYC 的题面与工作区**、只接受 `--limit`，并不读用户的 `config.json`/`items.jsonl`。因此**已发布矩阵既不能从干净 checkout 跑起来、也无法仅凭本仓库脚本从零复现**，这是「缺聚合器」之外的另一处再现性缺口。
- **主对照三臂统一为 `closedbook` / `rawfiles` / `skill`**：
  - `closedbook`：不给任何课程材料。
  - `rawfiles`：原始文件 + 通用 agent、不装技能——**最公平的对照**。
  - `skill`：建好的 wiki + quiz_bank、使用本技能。
  - **遗留/压力臂 `material` / dump-all**：整门课全文塞进一次提问；**非主对照**，保留为**压力脚注**（实测会淹没弱模型并触发上下文/用量上限而跑崩）。
- **`report_matrix.py` 只渲染、不计算**：默认渲染 `results/matrix/summary.json`；T3 起支持 `--summary <file> --out-dir <dir>`，可渲染**显式**的 summary（不再被迫只渲染那份已提交的）。
- **`summary.json` 聚合器已由 T3 补上**：`benchmark/aggregate_matrix.py`（**T3 新增**，纯标准库）从**显式**的 answer/score 行聚合出 `summary.json` 兼容的矩阵 summary，并有 fixture 级可复现流水线（见 [`matrix_pipeline.md`](matrix_pipeline.md)）。**但已发布的 MIT/PSYC `summary.json` 仍是预先计算（precomputed）的产物**：完整矩阵依旧依赖**私有/中间产物 + 付费模型运行**（真实答案日志、私有金标未提交），聚合器本身**不**重现已发布数字。（另注：`rejudge.py` 重判后写的是 `summary_corrected.json`，其结构是**嵌套的** `algo` / `psyc` 块，**不是** `report_matrix.py` 读的顶层 `matrix`/`n_items` 形状，故**不能**直接 `--summary summary_corrected.json` 渲染——需先转成顶层 matrix 形状，重判结果才不会自动进矩阵报告。**T3.1 补上了这座桥**：`rejudge.py --scores-out`（**须同时给 `--answers-out`**）零成本、不调 LLM 地把每题判分导出成 `aggregate_matrix.py` 可读的 score/answer 行（answer 行带 `status`/`cost_usd`），于是 `judge/rejudge → aggregate_matrix → report_matrix` 成为一条有提交、可照跑的显式路径；默认行为不变，export 只写显式路径。）
- **判分已有确定性快路径**：越界弃答 / 数值题 / 词面精确匹配在调用 LLM 裁判前就先确定，只有未决的事实/定义题才走 LLM 裁判。
- **`--mock` 只验管线**：mock 答案与 mock 裁判都是预设的，能验证管线连通，**无法捕捉真实判分质量回归**。

---

## 8. 新技能能力的覆盖缺口 (coverage gaps)

一句话总结：**结构 / schema / 指令文本被充分静态覆盖；行为几乎没有被执行验证。** 逐能力对照见 [`coverage-matrix.md`](coverage-matrix.md)。需要被**行为**测到（而目前不是）的能力：惰性加载、quiz_bank-only 出题、六题型实际判分、画图先跑算法、0 基础精讲、提示/跳过/错题归档、疑难追踪、断点恢复、运行时来源标注、无 Python 降级。

---

## 9. 省 token 原则 (token-saving principles)

1. **确定性优先**：来源标注是否出现、抽到的题是否都在题库内、进度是否写入、是否只读一章、画图题是否带 render_hint——这些用**对产物文件的结构断言**判定，**不需要 LLM 裁判**。
2. **缓存生成与判分**：`gen.py` 已按格缓存答案+成本并可续跑；判分结果应缓存（理想按裁判提示哈希键控，使提示模板变更能正确失效）。
3. **LLM 裁判只用于未决项**：仅对走不通确定性快路径的事实/定义题调用裁判。
4. **Tier 2 要小且大多确定性**：约十几条行为场景、单一便宜模型（Haiku）、绝大多数是结构断言、只极少数需要一次便宜的 grounding 调用。
5. **付费层必须手动触发**：完整 benchmark（Tier 3）与**真 LLM** 长程漂移（Tier 4 的 `--llm` 部分）绝不进 CI、绝不为小改动跑；Tier 4 的**确定性 replay 层**零成本、已进根级测试。

---

## PR 路线（T-track）

- **T1** ✅：审计文档 + 覆盖矩阵 + benchmark 文档一致性守卫（零成本，纯文档/测试）。
- **T2** ✅（确定性层）：Tier 2 行为冒烟——自撰非版权 fixture + harness + 确定性探测器（进 CI）；**真 LLM 行为冒烟为 opt-in、未进 CI、尚未实现**。
- **T3** ✅（聚合层）：提交版 `summary.json` 聚合器 [`aggregate_matrix.py`](matrix_pipeline.md) + fixture 级可复现流水线 + `report_matrix.py --summary`。**完整发布矩阵仍依赖私有/付费产物**；benchmark 缓存/续跑/成本日志为后续增量。
- **T4** ✅（确定性 replay 层）：Tier 4 长程漂移 [`drift/`](../drift/)——回放脚本化 transcript + 快照的自撰非版权 fixture，确定性测量目标保持/计划遵守/编题率/断点恢复/来源标注/进度持久性/wiki 越章读（进根级测试）；**真 LLM 长会话为 opt-in、未进 CI、尚未实现**。
- **T5**：判分校准（扩 kappa 样本、加 near-miss 越界探针、跨家族裁判、修正数值抽取）。
