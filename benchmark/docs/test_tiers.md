# 测试分层 (Test Tiers)

完整的防幻觉 benchmark（真实跑各模型）**很贵**——一次完整单轮矩阵就要几十美元、几小时，
长程漂移测试更是以「天」计的订阅额度。为避免「每个小改动都跑全量」，我们把测试分成 5 层，
**越靠前越便宜、越该频繁跑；越靠后越贵、越要手动触发**。

| 层 | 内容 | 成本 | 何时跑 |
| --- | --- | --- | --- |
| **Tier 0 单元测试** | `python -m unittest discover -s tests -v`（ingest、validator、结构等，纯 stdlib，无网络/LLM） | 秒级、$0 | **每次提交 / CI 必跑** |
| **Tier 1 工作区校验** | `python scripts/validate_workspace.py <ws>`（+ `tests/fixtures/` 样例工作区静态校验） | 秒级、$0 | 每次改动 schema/ingest/技能时；CI 可跑 fixtures |
| **Tier 2 冒烟 benchmark** | 3–5 题的极小 fixture 题集，验证 benchmark 管线端到端（建议 `--mock` 或极小真跑） | 分钟级、近 $0 | 改 benchmark 管线时本地手动；默认**不**做付费真跑 |
| **Tier 3 完整单轮 benchmark** | 现有三臂×多模型矩阵（6.006 / PSYC 全量） | 几十美元、几小时 | **仅手动触发**，发布数据/方法变更时 |
| **Tier 4 长程漂移 benchmark** | 模拟 15–30 轮连续辅导会话（未来工作，见下） | 以天计额度 | 重大版本、专门排期时手动 |

## CI 策略
- CI（`.github/workflows/ci.yml`）只跑 **Tier 0**（必要时加 Tier 1 的 fixtures 校验）——零成本、跨平台、无密钥。
- Tier 2–4 **不进自动 CI**，避免给每个 PR 付费。

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

> 本 PR（PR B）只做 Tier 0–1 的低成本结构校验，为 Tier 4 打地基（schema 明确、工作区可程序化检查），
> **不实现 Tier 4 本身**。
