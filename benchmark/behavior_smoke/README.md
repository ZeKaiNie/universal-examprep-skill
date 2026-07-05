# Tier 2 — 行为冒烟 (Behavioral Smoke)

把技能当作**辅导工作流**来测，而不是只测静态文件（Tier 0/1）或有据问答（Tier 3 benchmark）。
口径定义见 [`../docs/test_tiers.md`](../docs/test_tiers.md)；现状审计见 [`../docs/testing-audit.md`](../docs/testing-audit.md)。

## 两条路径

| 路径 | 命令 | 成本 | 进 CI？ |
| :-- | :-- | :-- | :--: |
| **默认（确定性）** | `--mock` / `--check-fixture` | $0，纯 stdlib、无网络、无 LLM、无 API key | ✅ |
| **可选（真 agent）** | `--llm`（需 `RUN_SKILL_BEHAVIOR_LLM=1`） | 跑 `claude -p`（订阅，不需 API key） | ❌ 默认禁用 |

```bash
# 默认：零成本、可进 CI
python benchmark/behavior_smoke/run_behavior_smoke.py --check-fixture   # 校验 mini-course 工作区
python benchmark/behavior_smoke/run_behavior_smoke.py --mock            # 在 mock 输出上跑确定性探测器

# 可选：真 agent 冒烟（必须同时给 env 与 flag，否则拒绝运行）
RUN_SKILL_BEHAVIOR_LLM=1 python benchmark/behavior_smoke/run_behavior_smoke.py --llm
```

不给任何参数时只打印帮助，**绝不**调用 LLM。

## 覆盖了什么

自撰的小型工作区 [`fixtures/mini_course/`](fixtures/mini_course)（**非版权内容**，通用 CS 常识）覆盖全部 6 种题型，
通过 `scripts/validate_workspace.py`。每个场景（[`scenarios.json`](scenarios.json)）对应一个**确定性探测器**：

| 场景 | 行为主张 | 默认（确定性）判定方式 |
| :-- | :-- | :-- |
| `quiz_bank_only` | 出题只用题库、不即兴编题 | mock 输出里的题号必须全在 `quiz_bank.json`；反例（编造题号）必须被判不合格 |
| `provenance_labels` | 区分来源并用 canonical 标注 | mock 输出含全部 🟢/🟡/⚠️ canonical 标注 |
| `hint_skip_mistake_archive` | 连错两次给提示/跳过/归档 | mock 输出含逃生通道 + mock 进度有错题行 |
| `confusion_tracking` | 「为什么」类疑问写入进度 | mock 进度的疑难点区新增一行 |
| `checkpoint_recovery` | 从当前阶段续而非重启 | 从进度读出当前阶段 = 2，且续跑消息指向阶段 2 |
| `no_python_fallback` | 无 Python 手写产出仍完整 | 手写工作区通过 Tier-1 校验 |
| `zero_basic_key_question` | 0 基础精讲含结构化小节 | mock 输出含 考点拆解（或 这题在问什么）+ 标准答题步骤（或 逐步演算）；易错点/3分钟速记 为可选收尾块不再要求 |
| `teaching_template` | A5 七步讲解模板 + 每题来源块 | ①-⑦ 齐全按序（②在④前）、⑦ 落到章节/wiki；来源行 题目来源｜答案来源｜canonical 标签；AI 答案 ⚠️ 进来源行与答案块标题；默认到来源块为止、未经要求的收尾块被抓（学生要求了则豁免）；7 个反例全被抓 |
| `visual_first_assets` | 视觉题先展示题面侧 asset | mock 输出必须先出现带 `题面图 / question-side asset` 标签的真实 fixture 本地图片；反例（答案图先出现 / 题目前泄露答案图或正文 / 未标注答案图 / 图片前正文 / 题后插图 / `问题：` 后迟到图片 / 不安全或缺失路径 / 只打印路径）必须不合格 |
| `scope_override` | 越范围出题须先声明（A2） | mock 输出在第一道题**之前**出现 verbatim「⚠️ 临时覆盖你的 <范围> 范围偏好」；反例（题后才声明 / 不声明）必须不合格 |
| `language_first_ask` | 首问一次合并 模式×时间×语言（A6/A8b），语言行三语呈现；紧迫开场静默推断 | mock 好例=三语语言行+一条三旗标 set；反例漏语言行被抓；紧迫变体=零问句+`--language` ∈ canonical，紧迫反例收尾提问被抓 |
| `time_budget_no_questions` | ≤1天档严禁向学生提问（A6） | mock 好例纯讲解、无面向学生的问句；反例（问「你想先复习哪一章？」「还有问题吗？」「Should I…」等收尾/通用问句）必须不合格；自答式反问不误伤 |
| `knowledge_window_recheck` | 窗口外知识点须真复核（A6） | 3-7天好例回问/实测均可、反例默认还会被抓；>7天（`require_test`）只认出题实测——只口头「还记得吗」的坏例被抓；否定式（不问/不实测/我就当你会了）不算复核、否定式安全声明（不会默认你会）不误伤 |
| `lazy_load_best_effort` | 只读当前章节 | **best-effort**：确定性模式跳过；需 transcript/LLM 才能真验 |

## 什么是 best-effort / 没覆盖

- **`lazy_load`** 只能在有真实工具调用 transcript（或 `--llm` 真跑）时验证「只读了当前一章」，
  确定性模式仅提供占位探测器 `count_wiki_reads`，**不在 CI 断言**。
- **确定性模式只证明探测器逻辑对 mock 产物成立**——它**不**证明真实 agent 一定产出这些行为。
  真实 LLM 行为覆盖需要跑可选的 `--llm` 路径（默认关闭、不进 CI）。
- **确定性探测器是 smoke 启发式，不是语义评分器**：它们用结构 / 题号 / 题面匹配 / 章节范围 / 否定词
  等手段抓**常见**的伪造与误判（编题、把合法题号贴到编造题面、未标号问题、否定逃生通道、空状态占位行、
  跑偏章节、只列标签图例不标注答案等），但**无法穷尽**任意 LLM 输出的所有改写。真正的语义判定留给 opt-in 的
  `--llm` 路径与未来的 LLM 裁判（Tier 3/4）——这也是把它放在「行为冒烟」而非「行为评分」的原因。

## 边界（这不是什么）

- 这**不是**完整 benchmark，**不替代** Tier 3（完整矩阵）/ Tier 4（长程漂移）。
- 默认路径**不**跑模型、**不**联网、**不**读 API key、**不**产生费用。
- `--llm` 是 skeleton：本 PR 只交付 harness 与确定性层，不接入付费真跑。
- 产物写入 `results/`（已 gitignore），fixture 在改动前会先复制到临时目录。
