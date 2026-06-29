# Changelog / 版本沿革

> 运行时技能文本（`SKILL.md` / `AGENTS.md` / `skills/**` / `prompts/` / `docs/`）直接描述当前行为、**不再提版本号**；版本历史集中记录在本文件，便于追溯。

## V2.1

- **知识来源透明化协议**：🟢 来自资料 / 🟡 AI补充，可能与你老师讲的不完全一致 / ⚠️ AI生成答案，非老师/教材提供。
- **零基础重点题精讲模式**：对几乎没学过的学生，按【考点拆解】+【标准答题模板/步骤】+【易错点】+【3分钟速记】逐题精讲。
- **画图题确定性协议**（`type: "diagram"`）：先真实运行标准算法得到结构，再渲染成图，绝不凭记忆手绘。
- **6 大题型**：`choice / subjective / diagram / fill_blank / true_false / code`。
- **工程化重构**（[PR #11](https://github.com/ZeKaiNie/universal-examprep-skill/pull/11)，不改既有行为）：
  - 模块化技能集合 `skills/`（`exam-cram` 主协调器 + 子技能）+ 根 `AGENTS.md` 兜底；
  - 双语控制层（英文控制段 + 简体中文学生侧）与 canonical 来源标注；
  - 工作区校验器 `scripts/validate_workspace.py`（纯标准库）；
  - 架构文档 `docs/`（skill-architecture / agent-portability / language-policy / file-format）；
  - 测试扩展（覆盖 ingest、工作区校验、技能结构、语言策略、控制层双语、技能集合自洽）+ CI 矩阵（Ubuntu/Windows × Python 3.8/3.12）。
- **防幻觉实测（benchmark）公平性改进**：加「裸文件 + 通用 agent」对照、成本维度、人工 kappa 校准。
- **confusion-tracker 并入 `skills/`**：疑难点追踪不再是 `skills/` 之外的外部依赖。

## V2.0

- **LLM Wiki 目录结构化 + 惰性加载**：按章节物理切片（`references/wiki/`），按进度只读当前章节，Token 消耗大幅下降。
- **一键零摩擦冷启动 ingest**：学生只给大纲/真题，AI 后台解析、拼 JSON、切片、初始化进度，**无需手写 JSON**。
- **无 Python 环境自动降级**：脚本不可用时无感切换为「手动写盘模式」，由 AI 直接铺设工作区。
- **标准题库 `quiz_bank.json` 抽题**：测验只从题库出题判分，杜绝 AI 即兴编题。
- **测试逃生通道**：查看提示 / 连续答错 2 次跳过并归档错题。
- **概念疑难点追踪**（`confusion-tracker`）：自动捕获「为什么 / 怎么推导」类追问，形成考前盲区清单。
- **运行安全与进度保护**：文件名安全过滤、路径防穿越/防篡改、进度覆盖前自动备份、强制 UTF-8 输出。
- **单元测试 + GitHub Actions CI**。
