# 测试分层 (Test Tiers)

> **本文件 = 简明的「分层口径」权威定义（canonical tier taxonomy）。** 详细现状审计见
> [`testing-audit.md`](testing-audit.md)，能力 × 层覆盖表见 [`coverage-matrix.md`](coverage-matrix.md)。
> 三份文档对各层（尤其 **Tier 2 = 行为冒烟**）的定义必须一致。

完整的防幻觉 benchmark（真实跑各模型）**很贵**——一次完整单轮矩阵就要几十美元、几小时，
长程漂移测试更是以「天」计的订阅额度。为避免「每个小改动都跑全量」，我们把测试分成 5 层，
**越靠前越便宜、越该频繁跑；越靠后越贵、越要手动触发**。

| 层 | 内容 | 成本 | 何时跑 |
| --- | --- | --- | --- |
| **Tier 0 单元/静态测试** | `python -m unittest discover -s tests`（ingest、validator、技能结构、语言策略等，纯 stdlib，无网络/LLM） | 秒级、$0 | **每次提交 / CI 必跑** |
| **Tier 1 工作区校验** | `python scripts/validate_workspace.py <ws>`（已建工作区的结构 / 题库 schema / 来源标注 / 路径安全） | 秒级、$0 | 改 schema/ingest/技能时（本地或手动）；其校验逻辑已由 Tier 0 在 `tests/fixtures/` 上覆盖 |
| **Tier 2 行为冒烟（behavioral smoke，尚未实现）** | **scripted agent 行为冒烟**：极小的**自撰 fixture 工作区** + **脚本化提示** + 对**产出文件/输出的确定性断言**；默认**不**做付费真跑。验证如**惰性加载 / quiz_bank-only 抽题 / 来源标注 / 提示·跳过 / 疑难追踪 / 断点恢复 / 无 Python 降级**等**行为是否真的发生** | 分钟级、近 $0 | 改技能行为时（**待实现**，本 PR 不实现） |
| **Tier 3 完整 benchmark 矩阵** | **6.006 为全量三臂**（`closedbook`/`rawfiles`/`skill`）；**PSYC 为部分**（`closedbook`/`skill` 全量，`rawfiles` 仅 Opus 全、Sonnet 少量、无 Haiku，`report_matrix.py` 里 PSYC 也只定义了 closedbook/skill）。数据见 [`testing-audit.md`](testing-audit.md) | 几十美元、几小时 | **仅手动触发**，发布数据/方法变更时 |
| **Tier 4 长程漂移 benchmark** | 模拟 15–30 轮连续辅导会话（**未来工作**，见下） | 以天计额度 | 重大版本、专门排期时手动 |

> **Tier 2 ≠ 「基准管线 mock 自检」**：`run_benchmark.py --mock`（用 `items/items.example.jsonl` 空跑、
> 只验证 benchmark 管线是否端到端连通）是一个**独立的「基准管线 mock 自检 (benchmark pipeline mock check)」**。
> 它只证明管线跑得通，**不是** canonical 的 Tier 2 行为冒烟，也不替代它——Tier 2 测的是**技能行为**，不是管线连通性。

## CI 策略（诚实标注）
- **CI（`.github/workflows/ci.yml`）当前只跑 Tier 0**（`python -m unittest discover -s tests -v`）——零成本、跨平台、无密钥。
- **Tier 1 校验器的逻辑已由 Tier 0 测试 + `tests/fixtures/` 覆盖**；但**目前还没有**一个单独的 CI 步骤在「真实 ingest 出来的工作区」上跑 `validate_workspace.py`（本 PR 不新增 CI 步骤）。
- **Tier 2–4 不进自动 CI**，避免给每个 PR 付费。

## Tier 2 与 Tier 4 的区别
两者都测「行为」，但**粒度与成本不同**：Tier 2 是**单场景、确定性、近零成本**的行为冒烟（一次脚本化交互 + 对产物断言）；
Tier 4 是**多轮长会话**下的漂移测量（下节），昂贵且需 LLM 裁判。Tier 2 是 Tier 4 的便宜前哨，二者都**尚未实现**（分别是后续 PR T2 / T4）。

## Tier 4：长程漂移 benchmark（未来工作，本 PR 不实现）
针对 PR #7 讨论指出的盲区——当前 benchmark 只测「单题准确率」，复现不出「多轮长会话里逐渐崩坏」
（目标漂移、擅改计划、脱离题库自己编题、断点恢复失败）。设计：模拟 15–30 轮连续复习会话、
中途故意插入干扰提问，对照「裸文件 agent」vs「使用本技能」，量化：

- **目标保持率 (goal retention)**：N 轮后是否仍按原计划/原目标推进。
- **计划遵守率 (plan adherence)**：是否擅自改写 `study_plan.md`。
- **题库忠实度 / 编题率 (quiz-bank fidelity / invention rate)**：AI 出的题有多少能在 `quiz_bank.json` 里找到（编题率 = 找不到的比例）。
- **断点恢复一致性 (checkpoint recovery)**：重启后能否从 `study_progress.md` 正确恢复状态。
- **来源标注忠实度 (provenance fidelity)**：AI 补充/生成内容是否都带 🟡/⚠️ 标注。
- **每轮成本 (cost per turn)** 与 **上下文增长 (context size growth)**：随轮数的代价曲线。

许多指标可**确定性**测量（编题率 = 字符串/JSON 比对、计划遵守 = 文件 diff、断点恢复 = 状态比对），
只有「目标保持率」需要 LLM 裁判——因此 Tier 4 仍能把昂贵的 LLM 调用压到最低。

> 本仓库目前的低成本结构校验只覆盖 **Tier 0–1**；**Tier 2 行为冒烟**与 **Tier 4 长程漂移**都**尚未实现**，
> 分别是后续 **PR T2 / T4** 的工作。本文件只定义口径，不实现它们。
