<div align="center">

<img src="assets/exam-panic.png" width="200" alt="期末极速备考教练" />

# 期末极速备考教练

*只剩一晚。你什么都没复习。每个答案都应该说清来源。*

中文 · [English](README.md)

[![stars](https://img.shields.io/github/stars/ZeKaiNie/universal-examprep-skill?style=flat&color=blue)](https://github.com/ZeKaiNie/universal-examprep-skill/stargazers)
[![MIT](https://img.shields.io/badge/协议-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/ZeKaiNie/universal-examprep-skill/actions/workflows/ci.yml/badge.svg)](https://github.com/ZeKaiNie/universal-examprep-skill/actions)

**围绕你的课程资料作答** · 题库限定出题 · AI 补充强制标注 · 分章按需检索

</div>

你认识他。考试前夜，头发乱成一团，眼睛瞪得溜圆，一整门课一个字没看。这个技能是给他的：围绕你的材料讲，AI 补充会明确标注，材料支撑不了的答案会如实说明。

**30 秒上手** —— 克隆整个仓库，然后对你的智能体说一句话：

```bash
git clone https://github.com/ZeKaiNie/universal-examprep-skill .claude/skills/universal-exam-cram-coach
# 对 Claude Code / Cursor 说："用这个技能初始化我的备考空间"，再把讲义/大纲/真题丢进来
```

---

## 装上前后

**装上技能**——答案与重点题讲解带来源，能核对：

> **[#vis_q1]** 题面图里阴影区域表示哪个集合关系？
> **A 与 B 的交集。**
> `题目来源：hw02.pdf 第 3 页｜答案来源：hw02_sol.pdf｜🟢 来自资料`

**闭卷 / 裸智能体**——听起来一样自信，但你无从判断真假：

> 阴影是**并集**。<sub>（资料里其实是交集；没有来源标注，无从核对——这正是"瞎编"发生的地方。）</sub>

区别不在口气，在于**答案是否暴露证据，以及哪些部分是 AI 补充**。

---

## 实测数据

技能的价值是 **grounding**：把你材料里、但模型本来不知道的内容接上去，同时让无依据答案显形。下面数字只代表表中课程、模型和题集的实测结果，不是对所有学科和宿主的保证（判分 Sonnet）：

**① 这些实测中的材料专属题正确率明显提高。** 教授的例子、冷门研究、具体数字等细节很难靠常识回答；下表逐项给出所列课程与模型的测量结果：

<div align="center"><img src="benchmark/docs/img/hard_psyc_correct_zh.svg" width="600" alt="材料专属题：闭卷 vs 装上技能" /></div>

| 课程 · 模型 | 闭卷 | 裸文件 + 通用智能体 | 装上技能 |
|---|:--:|:--:|:--:|
| PSYC 110 · Opus 4.8 | 11% | 98% | **100%** |
| PSYC 110 · Sonnet 4.6 | 13% | 100% | **100%** |
| PSYC 110 · Haiku 4.5 | 11% | 98% | **100%** |
| 6.006 · Haiku 4.5 | 45% | 89% | **91%** |

**② 这组越界题实测的如实弃答率为 100%。** 在所测两门课、三个模型的探针中，技能组（及裸文件组）对全部越界题都选择弃答；闭卷组测得 60%–90%。样本设计与局限见测试报告。

<div align="center"><img src="benchmark/docs/img/oos_psyc_abst_zh.svg" width="560" alt="越界探针：如实弃答率" /></div>

在这些实测中，分章检索与裸文件组精度接近，每题成本如下。其机制是只取相关章节，不是每题翻检整堆原始文件：

<details><summary>每题成本（同精度更省）</summary>

| 每题成本 | 闭卷 | 裸文件智能体 | 装上技能 |
|---|:--:|:--:|:--:|
| PSYC 110 | $0.033 | $0.117 | **$0.102** |
| 6.006 | $0.034 | $0.066 | **$0.063** |

</details>

完整方法、三臂设计、判分校准、成本、局限 → **[测试报告](benchmark/REPORT.md)**。

---

## 怎么做到的

一条"能不编就不编"的阶梯：

1. **只从预建题库出题** —— 测验题来自带来源标签的 `quiz_bank.json`，不即兴编题。题库条目可以来自材料，也可以是明确标注的 AI 生成题，但绝不隐藏来源。
2. **来源强制标注** —— 每条结论标 `🟢 来自资料` / `🟡 AI补充，可能与你老师讲的不完全一致` / `⚠️ AI生成答案，非老师/教材提供`，绝不冒充教材。
3. **资料里没有就说没有** —— 遇到资料未覆盖的问题，如实弃答，不强行给答案。
4. **画图题先跑算法再画** —— 二叉树 / 图遍历这类题，后台跑标准算法求出拓扑再渲染，禁止凭空想象。
5. **图依赖题缺图不出** —— 需要配图却没图的题绝不出，不给学生一道没法答的题。
6. **分章知识库按需加载** —— 每轮只读当前章切片，不把整门课反复塞进上下文。

本地建库支持 PDF、DOCX、PPTX、纯文本和 Markdown。`scripts/ingest_course.py` 是唯一常规 orchestrator：建立结构化内容单元与页锚点、编译 wiki/题库、初始化状态并完成校验。返回码 `0` 表示 `ready` 或 `usable_with_gaps`；返回码 `10` 表示流程跑完但仍有待审问题，必须阻断授课。智能体随后逐条处理类型化审查队列与 append-only 补丁账本，再重建并复验；不能手改派生 wiki 来假装警告消失。

---

## 复习模式 · 时间宽裕度 · 偏好

技能会按你的处境调节讲解的深浅、节奏和是否追问，都记在 `study_state.json` 里、跨对话不丢。

**3 种复习模式**（怎么讲）：

| 模式 | 适合 |
|---|---|
| **零基础从头讲** | 完全没学过，从第一章逐步讲透、每道重点题走七步模板 |
| **某章起步补弱** | 前面会一些，从指定章节开始、重点补薄弱环节 |
| **查缺补漏** | 大致都学过，只做题扫盲区、错题优先 |

**4 档时间宽裕度**（多快）：

| 宽裕度 | 行为 |
|---|---|
| **≤ 1 天** | 极限冲刺——跳过开场澄清、模板偏好和反思式追问，静默推断默认并直接开讲；仍可用标准题库练习或阶段测验验证掌握 |
| **1–3 天** | 抓重点，压缩非核心 |
| **3–7 天** | 正常节奏，会回问你哪些章有把握 |
| **> 7 天** | 从容——对你说"有把握"的章**出题实测**而非只口头确认 |

**偏好**（记住你的习惯）：讲解模板要不要带【易错点】/【3 分钟速记】收尾块、回复语言（中文 / English / 双语）、明确的 `no_questions` 请求（不再输出互动题，阶段最高为 `covered_unverified`）、每章的知识点掌握窗口（`window-add` / `window-set-status`）——都持久化，随时说一句就改。详见 [`docs/language-policy.md`](docs/language-policy.md) 与 [`docs/skill-architecture.md`](docs/skill-architecture.md)。

**可选的普通教学节奏：**新旧状态都默认 `batch`（批量讲解）。在明确启用完整建库模式后，可选 `step_by_step`，让智能体严格按 `teaching_examples.json` 清单顺序，每轮完整讲完一道题的七步精讲：

```bash
python scripts/update_progress.py --workspace <工作区> set --interaction-style step_by_step
# 也可以在 exam_start.py confirm 时附加 --interaction-style step_by_step。
```

它是普通功能中的可选偏好，不是第四项启动必答题；只有 `processing_mode=full` 且 `no_questions=false` 时才有效，否则保留已存选择并报告为 dormant，当前有效节奏为 `batch`。`≤1天` 档不会主动询问。选择器只覆盖完整模式的教学例题，并在同一个 workspace 锁定快照中读取 manifest、state、baseline 与 notebook；回复“继续”不是完成证据。一次 `record-taught-example` 必须绑定当前 manifest 顺序里的第一道 pending 题，并把保留 marker 的 walkthrough 固化为 `{id, notebook_ref, notebook_block_sha256, manifest_item_sha256}`。quiz/teaching/notebook/Guide 共用 1–200 字符的安全 Unicode ID 规范，拒绝空白、Markdown/路径分隔符、控制/格式/代理/替换字符和 Unicode noncharacter；两个 binding 也不得复用同一 `notebook_ref`。未绑定的旧 ID 仍是合法 batch 历史；一旦有 binding，即使切换节奏也必须持续通过 live 校验。只有文件/条目缺失以及 anchor、marker、hash、revision 漂移可重新变成 pending；重解析点、非目录/非普通文件、越界、非法 UTF-8、未闭围栏、坏 block、重复或越出 roster 的证据都保持 fail closed。已完成 full 章节若只出现结构合法的新 roster 项或上述可修复 stale 项，挂载时降为 `usable_with_gaps` 以便按顺序重讲；Guide 与阶段完成仍严格失效，记录第一道 pending 后须重建 Guide 并重新完成。保留基线中的每个 ID 都必须在同一 canonical chapter 拥有当前 teaching snapshot，且 `policy` 必须精确为 `append_only`；只有 quiz_bank 副本不能替代。章节原有门禁仍全部生效，`teaching_example_roster_exhausted=true` 即使在零例题时也不等于章节完成；切换回复语言不会自动把已记录题目重新排入队列。

> 过渡兼容：该 PR 所在的上游基线尚未引入显式处理模式选择器，因此缺少 `processing_mode` 会被视为旧版隐式 `full`；若宿主已显式写入非 `full` 值，逐题偏好仍休眠。后续处理模式迁移会接管缺字段默认语义。

---

## 安装

### Claude Code

**推荐——运行时精简包**（只含运行时技能，不带开发用 benchmark 与测试）：

到[最新 release](https://github.com/ZeKaiNie/universal-examprep-skill/releases/latest) 下载 `universal-exam-cram-coach.zip`，解压到 `.claude/skills/universal-exam-cram-coach/`（项目内或全局 `~/.claude/skills/` 均可）。

TXT/Markdown/DOCX/PPTX 的基础建库使用标准库。首次建库前，智能体会运行自带的依赖预检（`scripts/check_deps.py`）；只有所选 PDF 或视觉路线确实需要可选包时才征求安装同意。不支持、加密、损坏或纯扫描内容会进入审查流程，不会被静默跳过。

**或克隆整仓**（开发者路径，包含 benchmark、测试和维护者文档）：

```bash
git clone https://github.com/ZeKaiNie/universal-examprep-skill .claude/skills/universal-exam-cram-coach
```

### Codex / Cursor / Windsurf / Antigravity

克隆仓库，让智能体读 `AGENTS.md`（一屏兜底契约）或加载 `skills/`。这些工具能直接写盘、跑脚本。

### 网页版（ChatGPT / DeepSeek / Gemini / 豆包）

无法写本地文件，改用一键平替提示词：复制 [`prompts/web_prompt.md`](prompts/web_prompt.md)（英文版 [`web_prompt.en.md`](prompts/web_prompt.en.md)）发给它，再贴上材料。

> 完整加载矩阵（各智能体支持程度、入口文件）见 [`docs/agent-portability.md`](docs/agent-portability.md)。根 [`SKILL.md`](SKILL.md) 是语言中性路由器，加载 [`skills/`](skills/) 的共享控制规则与 [`locales/zh/SKILL.md`](locales/zh/SKILL.md) / [`locales/en/SKILL.md`](locales/en/SKILL.md) 的轻量兼容文案入口；两种语言都不是第二份行为手册。

---

## 子技能

单体技能拆成 10 个单一职责技能，智能体按需加载：

| 子技能 | 做什么 |
|---|---|
| `exam-cram` | 主协调器——编排四步工作流 + 学习模式路由 |
| `exam-ingest` | 编排 PDF/DOCX/PPTX/文本建库、类型化 AI 审查、编译与 readiness 校验 |
| `exam-tutor` | 按章惰性授课（含零基础七步精讲、画图先跑算法） |
| `exam-study-guide` | 把单章编译为公式可读、自包含的 HTML，并可选生成经视觉验收的 PDF |
| `exam-quiz` | 题库抽题判分（选择 / 主观 / 画图 / 填空 / 判断 / 代码 6 题型） |
| `exam-review` | 错题与概念疑难点复盘 |
| `exam-cheatsheet` | 考前速记小抄 |
| `exam-audit` | 只读检查工作区健康度 |
| `exam-help` | 一屏速查卡（工作流 / 模式 / 文件约定） |
| `confusion-tracker` | 自动记录复习中的概念疑问，形成考前盲区清单 |

十个技能都在 [`skills/`](skills/) 目录下（如 [`skills/exam-study-guide/SKILL.md`](skills/exam-study-guide/SKILL.md)），按任务惰性加载。PDF 工具按宿主区分且不会静默下载，详见 [`docs/pdf-capability-adapters.md`](docs/pdf-capability-adapters.md)。

章节教材是可选输出。默认 `chat`（对话省额）在对话中教学，同时照常保存必要的进度/笔记，但不自动生成 HTML/PDF；说“省 token / 只在对话讲”或设置 `--artifact-mode chat` 即可保持此模式。说“不在乎 token / 以后每章给我打印版”或设置 `--artifact-mode visual`，才会自动生成章节 HTML + 经逐页验收的 PDF。智能体不得根据订阅套餐自行猜测；一次性的 PDF 请求也不会暗中改变长期偏好。

---

## 开发

零成本、可频繁跑的结构化校验（不烧额度）：

```bash
python -m unittest discover -s tests -v          # 单元测试（纯标准库，进 CI）
python scripts/validate_workspace.py path/to/ws  # 校验一个建好的备考工作区
```

真·付费实测很贵（一次矩阵几十美元 / 几小时），只手动跑——操作手册见 [`benchmark/docs/running-real-runs.md`](benchmark/docs/running-real-runs.md)，分层策略见 [`benchmark/docs/test_tiers.md`](benchmark/docs/test_tiers.md)。工作区文件格式见 [`docs/file-format.md`](docs/file-format.md)。

---

## 常见问题

**电脑没装 Python？** 只有直接探测确认解释器确实无法启动后，核心工作区才可明确降级为验证能力较弱的手动写盘模式。Python 能运行时的脚本或数据错误必须修复或报告，不能触发这条降级。MathML HTML/PDF 教材渲染器需要 Python，缺项会明确报出。

**订阅额度较低？** `artifact_mode=chat` 是安全默认，正常授课不会额外组织章节 HTML/PDF。只有想要可打印视觉教材时再切到 `visual`；PDF 打印主要使用本地计算，但更详细的教材组织仍可能增加上下文与生成量。

**只有照片 / PDF 扫描件 / 录音？** 把原文件直接交给建库流程：支持时会渲染并读取 PDF 页面，并把每个扫描页、跳过项和待审项放入类型化队列给 AI 接管；智能体必须认领并处理，或报告准确文件名与原因。录音仍需先转录再建库。

**测验卡在一道题？** 直接说"这题太难 / 我想跳过"，会自动归档到错题本、放行，最后统一重温。

**跟"直接把文件夹丢给 AI"有啥区别？** 多了可恢复状态、分章检索、标准题库、来源标注和缺图失败关闭。实测报告只比较其中两门课程的精度与成本，详见[报告](benchmark/REPORT.md)。

---

## 开源协议

[MIT](LICENSE)。欢迎提交贡献更多科目模板或脚本。祝临考冲刺的你考神附体。🎓

<div align="center">

<a href="https://www.star-history.com/?repos=ZeKaiNie%2Funiversal-examprep-skill&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=ZeKaiNie/universal-examprep-skill&type=date&theme=dark&legend=top-left&sealed_token=q2eC20GmpWMHMen634RnHHNopx3dtYK6mzpbK0tB8B7sBn_LT0IKz-TYsaaWMY5xLJ6i7bsHedSzBxs4DU6cD5vZ8HFc-ZD2XAlqm5MnqBbf-ZbEq8zr2A" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=ZeKaiNie/universal-examprep-skill&type=date&legend=top-left&sealed_token=q2eC20GmpWMHMen634RnHHNopx3dtYK6mzpbK0tB8B7sBn_LT0IKz-TYsaaWMY5xLJ6i7bsHedSzBxs4DU6cD5vZ8HFc-ZD2XAlqm5MnqBbf-ZbEq8zr2A" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=ZeKaiNie/universal-examprep-skill&type=date&legend=top-left&sealed_token=q2eC20GmpWMHMen634RnHHNopx3dtYK6mzpbK0tB8B7sBn_LT0IKz-TYsaaWMY5xLJ6i7bsHedSzBxs4DU6cD5vZ8HFc-ZD2XAlqm5MnqBbf-ZbEq8zr2A" />
 </picture>
</a>

</div>
