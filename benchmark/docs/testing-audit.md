# 测试与 Benchmark 审计 (Testing & Benchmark Audit)

> 这是一份**诚实的现状快照**，用来在投入昂贵测试之前，把「我们到底测了什么、没测什么」说清楚。
> 非营销文档。结论指向后续 PR（见文末），本 PR（T1）只整理文档与一致性守卫，不新增行为测试、不跑付费 benchmark。

---

## 1. 测试分层与实际运行 (test tiers)

| 层 | 是什么 | 进 CI？ | 成本 | 现实 |
| :-- | :-- | :--: | :-- | :-- |
| **Tier 0** | `python -m unittest discover -s tests`（stdlib 单元/静态测试套件） | ✅ Ubuntu + Windows × Py 3.8 + 3.12 | $0，纯标准库、无网络、无 API key | CI **唯一**实际运行的一层 |
| **Tier 1** | `python scripts/validate_workspace.py <工作区>`（已建工作区结构/schema/来源标注/路径安全） | ❌ 仅文档化 | $0 | 校验器**逻辑**通过 Tier 0 的单测在 `tests/fixtures/` 上被执行；但**没有单独的 CI 步骤**在真实「ingest 产物」上跑该 CLI，也**没有 `ingest → validate` 的集成步骤** |
| **Tier 2** | 行为冒烟（scripted 提示 + 对产物的结构断言） | ❌ | — | **尚未实现（not implemented）**，见 §4 |
| **Tier 3** | 完整 benchmark（`gen.py` → 判分 → 矩阵报告） | ❌ | **昂贵**：单轮矩阵约几十美元 / 数小时 | 手动、临时触发 |
| **Tier 4** | 长程漂移（long-horizon drift） | ❌ | 以天计额度 | **未来工作（future work）**，目前无 harness |

**CI 现状**：`.github/workflows/ci.yml` 只有一步 `python -m unittest discover -s tests -v`，即只跑 Tier 0；全部零成本。README 也已据此澄清为「CI 实际只跑 Tier 0」——Tier 1 校验器的逻辑由 Tier 0 单测在 `tests/fixtures/` 上覆盖，但该 CLI 本身不是独立 CI 步骤，也没有 `ingest → validate` 的集成步骤。

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

`scripts/validate_workspace.py` 对一个**已建好的备考工作区**做零成本校验：目录结构、`quiz_bank.json` schema（题型/选项/答案在选项内/主观题关键词/diagram_type/true_false 布尔/source 枚举/`ai_generated` 标记）、`references/wiki/` 路径安全、进度文件一致性；退出码 `0` 通过 / `1` 有错 / `2` 致命（结构损坏或非法 JSON）。当前它由 Tier 0 单测在 fixtures 上驱动，缺少「真实 ingest 产物上跑 CLI」的独立 CI 步骤。

## 4. Tier 2 行为冒烟：尚未实现 (not implemented)

**目前没有任何一层执行技能的真实交互行为。** Tier 0 是静态的、Tier 3 是 Q&A grounding。技能的下列行为**既没有单测、也没有 benchmark**：

- LLM Wiki 惰性加载（是否只读相关一章，而非全量）
- quiz_bank-only 出题（是否只从题库抽题、不即兴编题）
- 六种题型的**实际判分**（题库 schema 有，行为没有）
- 画图题先跑算法再画
- 0 基础重点题精讲
- 提示 / 跳过 / 错题归档
- 概念疑难点追踪行为
- study_progress 断点恢复
- 运行时来源标注（🟢/🟡/⚠️）的实际输出
- 无 Python 环境降级写盘

这些目前**只有「指令文本存在」被静态测到，行为未被执行验证**——这正是 Tier 2 要补的缺口（见 [`coverage-matrix.md`](coverage-matrix.md)）。本 PR 不实现 Tier 2，只为它铺路。

## 5. Tier 3 完整 benchmark：手动、昂贵

跨 `课程 × 模型 × 臂` 的真实 LLM 矩阵，单轮约几十美元 / 数小时。它测的是**有据问答的 grounding / 幻觉 / 越界弃答**，不是交互式辅导流程。只在「故意改了技能行为 / 加了新课程材料 / 发布前」时手动触发，不应进 CI、不应为每个小改动跑。

## 6. Tier 4 长程漂移：未来工作

长对话（数十轮 + 中途干扰）下的目标保持 / 凭空编造率 / 断点恢复，按天计额度。**目前仅作为未来工作记录，无 harness。**

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
- **`report_matrix.py` 只渲染、不计算**：它读取预先算好的 `results/matrix/summary.json` 出图。
- **`summary.json` 缺提交版聚合器**：当前 `summary.json` 是**预先计算（precomputed）**的产物；仓库里**还没有**一条从「`gen.py` 生成的答案 + 判分缓存」聚合出 `summary.json` 的提交版脚本。补齐这个**聚合器（aggregator）是未来 PR T3**。（另注：`rejudge.py` 重判后写的是 `summary_corrected.json`，而 `report_matrix.py` **只读** `summary.json`——二者不接，重判结果不会自动进矩阵报告。）
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
5. **Tier 3 / Tier 4 必须手动触发**：完整 benchmark 与长程漂移绝不进 CI、绝不为小改动跑。

---

## 后续 PR（本 PR 只做 T1）

- **T1（本 PR）**：审计文档 + 覆盖矩阵 + benchmark 文档一致性守卫（零成本，纯文档/测试）。
- **T2**：Tier 2 行为冒烟 fixture（自撰非版权迷你材料 + 结构断言）。
- **T3**：benchmark 缓存/续跑/成本日志 + **`summary.json` 聚合器**。
- **T4**：长程漂移 harness。
- **T5**：判分校准（扩 kappa 样本、加 near-miss 越界探针、跨家族裁判、修正数值抽取）。
