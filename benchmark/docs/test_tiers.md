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
| **Tier 2 行为冒烟（behavioral smoke）** | **scripted agent 行为冒烟**：极小的**自撰 fixture 工作区** + **脚本化提示** + 对**产出文件/输出的确定性断言**（覆盖 quiz_bank-only / 来源标注 / 提示·跳过·错题归档 / 疑难追踪 / 断点恢复 / 无 Python 降级 / 0 基础精讲）。**确定性 mock 层已落地**（见 [`../behavior_smoke/`](../behavior_smoke/)，纯 stdlib、进 CI）；**真 LLM 行为验证仍 opt-in、不进 CI（尚未实现，仅 skeleton）**；惰性加载为 best-effort | 默认近 $0 | mock 层每次改技能行为时（进 CI）；真 agent 冒烟 opt-in 手动 |
| **Tier 3 完整 benchmark 矩阵** | **6.006 为全量三臂**（`closedbook`/`rawfiles`/`skill`）；**PSYC 为部分**（`closedbook`/`skill` 全量，`rawfiles` 仅 Opus 全、Sonnet 少量、无 Haiku，`report_matrix.py` 里 PSYC 也只定义了 closedbook/skill）。**T3 起聚合层 [`aggregate_matrix.py`](matrix_pipeline.md) + fixture 流水线已提交、机制可从干净 checkout 复现**；但**完整发布矩阵仍需私有/中间产物 + 付费运行**。数据见 [`testing-audit.md`](testing-audit.md) | 几十美元、几小时（fixture 机制 $0） | **仅手动触发**，发布数据/方法变更时 |
| **Tier 4 长程漂移 benchmark** | 多轮长会话下的漂移测量。**T4 起：确定性 replay harness 已落地**（[`../drift/`](../drift/)，回放脚本化 transcript + 快照，纯 stdlib、进根级测试）；**真 LLM 长会话仍 opt-in、未实现、不进 CI**（见下） | replay $0；真 LLM 以天计额度 | replay 每次改技能行为可跑；真 LLM 手动 |

> **Tier 2 ≠ 「基准管线 mock 自检」**：`run_benchmark.py --mock`（用 `items/items.example.jsonl` 空跑、
> 只验证 benchmark 管线是否端到端连通）是一个**独立的「基准管线 mock 自检 (benchmark pipeline mock check)」**。
> 它只证明管线跑得通，**不是** canonical 的 Tier 2 行为冒烟，也不替代它——Tier 2 测的是**技能行为**，不是管线连通性。

## CI 策略（诚实标注）
- **CI（`.github/workflows/ci.yml`）当前只跑 Tier 0**（`python -m unittest discover -s tests -v`）——零成本、跨平台、无密钥。
- **Tier 1 校验器的逻辑已由 Tier 0 测试 + `tests/fixtures/` 覆盖**；但**目前还没有**一个单独的 CI 步骤在「真实 ingest 出来的工作区」上跑 `validate_workspace.py`（本 PR 不新增 CI 步骤）。
- **Tier 2 的确定性 mock 层已进 CI**（包含在 `tests/test_behavior_smoke.py`，纯 stdlib、零成本）；但 **Tier 2 的真 LLM 行为冒烟（opt-in）与 Tier 3–4 不进自动 CI**，避免给每个 PR 付费。

## Tier 2 与 Tier 4 的区别
两者都测「行为」，但**粒度与成本不同**：Tier 2 是**单场景、确定性、近零成本**的行为冒烟（一次脚本化交互 + 对产物断言）；
Tier 4 是**多轮长会话**下的漂移测量（下节）。Tier 2 是 Tier 4 的便宜前哨。**Tier 2 的确定性层已由 PR T2 落地**（真 LLM 行为仍 opt-in、不进 CI）；**Tier 4 的确定性 replay harness 已由 PR T4 落地**（回放脚本化 transcript，纯 stdlib、进根级测试），**真 LLM 长会话仍 opt-in、未实现、不进 CI**。

## Tier 4：长程漂移 benchmark（确定性 replay 已落地；真 LLM 长会话仍未来工作）
针对 PR #7 讨论指出的盲区——当前 benchmark 只测「单题准确率」，复现不出「多轮长会话里逐渐崩坏」
（目标漂移、擅改计划、脱离题库自己编题、断点恢复失败）。**T4 已落地一个确定性 replay harness**
[`../drift/`](../drift/)：回放脚本化的多轮 transcript + 工作区快照（自撰非版权 fixture），对下列指标做
**确定性**测量并与阈值比对（纯 stdlib、零成本、进根级测试，`python benchmark/drift/run_drift.py --all`）：

- **目标保持率 (goal retention)**：N 轮后是否仍按原计划/原目标推进。
- **计划遵守率 (plan adherence)**：是否未经用户同意改写 `study_plan.md` 的阶段序列。
- **题库忠实度 / 编题率 (quiz-bank fidelity / invention rate)**：AI 出的题是否都能在 `quiz_bank.json` 里找到、且在对的 phase（编题率 = 找不到的比例）。
- **断点恢复一致性 (checkpoint recovery)**：重启后能否从 `study_progress.md` 的当前阶段继续（而非退回阶段1）。
- **来源标注忠实度 (provenance fidelity)**：后续解释轮是否都带 🟢/🟡/⚠️ 内容标注（用 T2 同款判定，图例不算）。
- **进度持久性 (mistake/confusion persistence)**：错题/疑难被记录后是否**不被静默删除**。
- **每轮成本 / 上下文增长（可选）** 与 **wiki 惰性加载 / 越章读**：随轮数的代价曲线与是否按 phase 只读该章。

> 现状：**Tier 0–1** 低成本结构校验 + **Tier 2 确定性 mock 层**（PR T2）+ **Tier 4 确定性 replay harness**
> （PR T4）均已落地进（根级）测试；**Tier 2 的真 LLM 行为验证与 Tier 4 的真 LLM 长会话仍 opt-in、不进 CI、
> 尚未实现**（都只是 opt-in skeleton，绝不返回成功）；**完整长程 LLM benchmark（以天计额度）仍为未来工作**。
