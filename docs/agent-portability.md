# Agent Portability — 这套技能怎么在不同代理里加载

本技能是一个**可移植的技能集合**：核心行为只写一份（在 `skills/`，浓缩版在 `AGENTS.md`），
各家代理（host）通过**薄适配**把它加载进去——能力强的 host 直接指向 `skills/`，只支持
「项目规则/指令」的 host 则用与 `AGENTS.md` 对齐的副本。

## Adapter Rule（核心原则）
> **适配层要薄。核心行为住在 `skills/`；任何被复制到 host 的规则文本，都必须与 `AGENTS.md` 保持对齐。**
> 能指就别抄：host 支持 skills 时让它指向现有 `skills/`；只有 host 逼你复制时才复制，且复制后要能验证它没跑偏。

两层心智模型：
- **技能层（skill-tier）** host —— 加载完整 `skills/*/SKILL.md`，**含 `skills/confusion-tracker/SKILL.md`**（它现已并入 `skills/`，是 `exam-tutor` 概念记录与 `exam-review` 疑难复盘所用的子技能）。仍建议整仓安装，以便拿到 `scripts/`、`templates/`、`docs/`、`prompts/`。
- **指令层（instruction-tier）** host —— 只能吃一份「项目规则」，给它一份与 `AGENTS.md` 对齐的浓缩规则。

## 支持矩阵

| Host | Files | Notes |
| --- | --- | --- |
| Claude Code | 根 `SKILL.md` / `skills/*` | 项目内 `.claude/skills/` 或全局 `~/.claude/skills/` 安装；技能层 |
| Codex | `AGENTS.md` / `skills/*` | `AGENTS.md` 作指令兜底，或作为技能包加载 `skills/`；指令/技能层 |
| Cursor | `AGENTS.md`（或未来 `.cursor/rules/`） | 项目规则兜底，规则文本须与 `AGENTS.md` 对齐 |
| Windsurf | `AGENTS.md`（或未来 rules 文件） | 同上，项目规则兜底 |
| ChatGPT / Claude Web | `prompts/web_prompt.md` | 无本地写盘；该提示词已含 V2.1 来源标注/防编题规则。用文本「进度 Summary」做断点，手动挂载题库后只从中出题 |
| Generic agents | `AGENTS.md` | 一屏浓缩兜底契约 |

> 兼容性：根目录 `SKILL.md` 仍是默认/兼容入口（承载完整 V2.1 协议）。新支持技能集合的 host 可改用
> `skills/exam-cram/SKILL.md` 作主入口；二者描述同一行为。

## 未来适配（本 PR 不实现）
- `.cursor/rules/*.mdc`、`.windsurf/rules/*.md` 等 host 专用规则副本，连同一个「副本与 `AGENTS.md` 对齐」的检查脚本（见 `docs/skill-architecture.md` 的 Future work）。
