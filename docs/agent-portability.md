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
| ChatGPT / Claude Web | `prompts/web_prompt.md`（English: `prompts/web_prompt.en.md`） | 无本地写盘；该提示词已含来源标注/防编题规则。用文本「进度 Summary」做断点，手动挂载题库后只从中出题 |
| Generic agents | `AGENTS.md` | 一屏浓缩兜底契约 |

## 文件型 host 的建库边界

能读写本地文件并运行 Python 的 host 先执行同一个确认门禁，再使用常规建库入口：

```bash
python scripts/exam_start.py status --materials <dir> --workspace <ws> --json
python scripts/exam_start.py confirm --course <name> --materials <dir> --workspace <ws> --mode <mode> --time-budget <tier> --language <zh|en|bilingual> --json
python scripts/ingest_course.py --materials <confirmed-materials-dir> --workspace <confirmed-workspace-dir> --json
```

`confirm` 只在用户已经确认精确路径和三项学习选择后调用；它一次写入 exact-pair confirmation、`study_state.json` 与 runtime provenance receipt。`ingest_course.py` 和真正的 Study Guide 都会重验这些事实，不能用裸 `workspace-register` 绕过。

该入口处理 PDF、DOCX、PPTX、纯文本和 Markdown，建立 `.ingest/` 结构化事实源，编译学生工作区并运行最终 validator。正常返回值有明确业务语义：

- exit `0`：readiness 为 `ready` 或 `usable_with_gaps`；后者必须先向学生说明仍存在的警告；
- exit `10`：流水线执行完成，但 readiness 为 `blocked`；不得开始授课、测验或阶段完成；
- 其他非零值：命令或数据失败，必须修复或明确报告，不能伪装成无 Python 降级。

需要 AI 接管时，host 通过 `scripts/ingest_review.py` 的 `list` / `show` / `claim` / `validate-patch` / `apply` / `mark-unrecoverable` / `rebuild` 子命令处理类型化 ReviewIssue。`.ingest/review_queue.jsonl` 与 `.ingest/review_patches.jsonl` 是审查事实源；不要手改派生 wiki、题库或兼容性报告来绕过阻断。每次材料变化、补丁应用或重建后都重新验证 readiness。

Web host 不能执行这条流水线、不能拥有 `.ingest/` 账本，也不能声称本地建库或写盘已经成功。它只能使用用户实际粘贴或挂载的材料与题库，并在回复末尾给出可复制的进度断点。

PDF/教材产物先经过资源偏好门禁，再做能力级路由：缺少 `artifact_mode` 的旧工作区与未知值都按 `chat`
处理，不自动探测 PDF 能力、不自动生成 HTML/PDF，也不猜用户的订阅套餐。只有用户显式持久化
`visual`，或本次明确要求 HTML/PDF/打印版时，才进入 host-specific 路由；单次请求不改持久状态。
不同 host 不共享同一安装入口，详见 [`pdf-capability-adapters.md`](pdf-capability-adapters.md) 与机器可读的
[`pdf-capability-adapters.json`](pdf-capability-adapters.json)。任何模式都不授权静默下载安装外部 skill 或依赖。

`.ingest/` 存在时，所有 host 都必须在阶段完成前消费并验证当前章 `notebook/chNN.guide.json`，且要求
`profile=full`；`chat` 到此停止，不需要 PDF。所有视觉 route 都从这份已验证清单生成，只有 receipt 中的
manifest/HTML/PDF 哈希仍匹配、全部页面已验收、零未解决缺陷且 `artifact_ready=ready` 才能交付并完成阶段。
修改课程语言后旧 manifest/产物立即 stale：先 relocalize 或补齐语言块，再重新渲染并重复全页 QA。

> 兼容性：根目录 `SKILL.md` 是默认触发入口和语言中性路由器，按 `study_state.json.language` 的规范值加载
> `skills/` 中的共享控制规则与 `locales/zh/SKILL.md` / `locales/en/SKILL.md` 的轻量兼容文案索引。
> 完整行为只承载在 `skills/` 控制层；语言入口不是可独立漂移的第二份手册。支持技能集合的 host 也可直接用
> `skills/exam-cram/SKILL.md` 作主入口。

## 其他适配

仓库当前不捆绑 `.cursor/rules/*.mdc` 或 `.windsurf/rules/*.md` 等 host 专用副本。若后续新增，副本必须由 `AGENTS.md` 生成或经对齐测试验证，不能手工形成新的行为事实源。
