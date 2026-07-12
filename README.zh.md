<div align="center">

<img src="assets/exam-panic.png" width="200" alt="期末极速备考教练" />

# 期末极速备考教练

*只剩一晚。你什么都没复习。它不会瞎编。*

中文 · [English](README.md)

[![stars](https://img.shields.io/github/stars/ZeKaiNie/universal-examprep-skill?style=flat&color=blue)](https://github.com/ZeKaiNie/universal-examprep-skill/stargazers)
[![MIT](https://img.shields.io/badge/协议-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/ZeKaiNie/universal-examprep-skill/actions/workflows/ci.yml/badge.svg)](https://github.com/ZeKaiNie/universal-examprep-skill/actions)

**从不乱编：越界 100% 弃答** · 材料里有·模型不知道的题 11% → ~99% · 上下文省 90% · 6 种智能体

</div>

你认识他。考试前夜，头发乱成一团，眼睛瞪得溜圆，一整门课一个字没看。这个技能是给他的——不再灌一堆它自己都拿不准的"知识"，只讲你资料里真有的东西，其余的老实说"资料里没有"。

**30 秒上手** —— 克隆整个仓库，然后对你的智能体说一句话：

```bash
git clone https://github.com/ZeKaiNie/universal-examprep-skill .claude/skills/universal-exam-cram-coach
# 对 Claude Code / Cursor 说："用这个技能初始化我的备考空间"，再把讲义/大纲/真题丢进来
```

---

## 装上前后

**装上技能**——每条结论都带来源，能核对：

> **[#vis_q1]** 题面图里阴影区域表示哪个集合关系？
> **A 与 B 的交集。**
> `题目来源：hw02.pdf 第 3 页｜答案来源：hw02_sol.pdf｜🟢 来自资料`

**闭卷 / 裸智能体**——听起来一样自信，但你无从判断真假：

> 阴影是**并集**。<sub>（资料里其实是交集；没有来源标注，无从核对——这正是"瞎编"发生的地方。）</sub>

区别不在口气，在于**每个结论能不能落回你的材料**。

---

## 实测数据

技能的价值是 **grounding**：把你材料里、但模型本来不知道的内容，**准确地**接上去，且**从不乱编**。两组真实测量（判分 Sonnet）：

**① 材料里有、模型不知道的题——技能把 11% 拉到 100%。** 从课程转录挖的、常识答不出的细节（教授的例子、点名的冷门研究、具体数字），闭卷全崩；把材料给回去就回来了：

<div align="center"><img src="benchmark/docs/img/hard_psyc_correct_zh.svg" width="600" alt="材料专属题：闭卷 vs 装上技能" /></div>

| 课程 · 模型 | 闭卷 | 裸文件 + 通用智能体 | 装上技能 |
|---|:--:|:--:|:--:|
| PSYC 110 · Opus 4.8 | 11% | 98% | **100%** |
| PSYC 110 · Sonnet 4.6 | 13% | 100% | **100%** |
| PSYC 110 · Haiku 4.5 | 11% | 98% | **100%** |
| 6.006 · Haiku 4.5 | 45% | 89% | **91%** |

**② 材料里根本没有的题——技能 100% 老实说"没有"。** 越界探针上，装上技能（及裸文件）两门课三个模型**全部 100% 如实弃答**；闭卷只有 60%–90%（会硬编一个像样的答案）。这是"防幻觉"最直接的度量。

<div align="center"><img src="benchmark/docs/img/oos_psyc_abst_zh.svg" width="560" alt="越界探针：如实弃答率" /></div>

技能与"裸文件智能体"精度接近但更省——它只取压缩过的相关章节，不是每题翻检整堆原始文件：

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

1. **只从资料出题** —— 测验题来自 `quiz_bank.json` 真题库，不即兴编题。
2. **来源强制标注** —— 每条结论标 `🟢 来自资料` / `🟡 AI 补充，可能与老师讲的不一致` / `⚠️ AI 生成答案`，绝不冒充教材。
3. **资料里没有就说没有** —— 遇到资料未覆盖的问题，如实弃答，不硬编（实测越界弃答 100%）。
4. **画图题先跑算法再画** —— 二叉树 / 图遍历这类题，后台跑标准算法求出拓扑再渲染，禁止凭空想象。
5. **图依赖题缺图不出** —— 需要配图却没图的题绝不出，不给学生一道没法答的题。
6. **分章知识库按需加载** —— 按章切片、按进度加载，长对话不撑爆上下文，**上下文省 90%**。

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
| **≤ 1 天** | 极限冲刺——**绝不向你提问**，静默推断默认（零基础从头讲），直接开讲 |
| **1–3 天** | 抓重点，压缩非核心 |
| **3–7 天** | 正常节奏，会回问你哪些章有把握 |
| **> 7 天** | 从容——对你说"有把握"的章**出题实测**而非只口头确认 |

**偏好**（记住你的习惯）：讲解模板要不要带【易错点】/【3 分钟速记】收尾块、回复语言（中文 / English / 双语）、每章的知识点掌握窗口（`window-add` / `window-set-status`）——都持久化，随时说一句就改。详见 [`docs/language-policy.md`](docs/language-policy.md) 与 [`docs/skill-architecture.md`](docs/skill-architecture.md)。

---

## 安装

### Claude Code

**推荐——运行时精简包**（约 230 KB 的 zip，只含技能本体，不带开发用的 benchmark/测试）：

到[最新 release](https://github.com/ZeKaiNie/universal-examprep-skill/releases/latest) 下载 `universal-exam-cram-coach.zip`，解压到 `.claude/skills/universal-exam-cram-coach/`（项目内或全局 `~/.claude/skills/` 均可）。

无需预装任何依赖——核心是纯标准库。材料里有 PDF 时，智能体会在建库**之前**运行自带的依赖预检（`scripts/check_deps.py`），把需要的安装命令一次性问清装好，绝不中途报错。

**或克隆整仓**（开发者路径，约 3.4 MB）：

```bash
git clone https://github.com/ZeKaiNie/universal-examprep-skill .claude/skills/universal-exam-cram-coach
```

### Codex / Cursor / Windsurf / Antigravity

克隆仓库，让智能体读 `AGENTS.md`（一屏兜底契约）或加载 `skills/`。这些工具能直接写盘、跑脚本。

### 网页版（ChatGPT / DeepSeek / Gemini / 豆包）

无法写本地文件，改用一键平替提示词：复制 [`prompts/web_prompt.md`](prompts/web_prompt.md)（英文版 [`web_prompt.en.md`](prompts/web_prompt.en.md)）发给它，再贴上材料。

> 完整加载矩阵（各智能体支持程度、入口文件）见 [`docs/agent-portability.md`](docs/agent-portability.md)。英文用户另有派生英文面 [`locales/en/SKILL.md`](locales/en/SKILL.md)。

---

## 子技能

单体技能拆成 9 个单一职责子技能，智能体按需加载：

| 子技能 | 做什么 |
|---|---|
| `exam-cram` | 主协调器——编排四步工作流 + 学习模式路由 |
| `exam-ingest` | 从材料建工作区（知识库 + 题库 + 进度） |
| `exam-tutor` | 按章惰性授课（含零基础七步精讲、画图先跑算法） |
| `exam-quiz` | 题库抽题判分（选择 / 主观 / 画图 / 填空 / 判断 / 代码 6 题型） |
| `exam-review` | 错题与概念疑难点复盘 |
| `exam-cheatsheet` | 考前速记小抄 |
| `exam-audit` | 只读检查工作区健康度 |
| `exam-help` | 一屏速查卡（工作流 / 模式 / 文件约定） |
| `confusion-tracker` | 自动记录复习中的概念疑问，形成考前盲区清单 |

九个子技能都在 [`skills/`](skills/) 目录下（如 [`skills/confusion-tracker/SKILL.md`](skills/confusion-tracker/SKILL.md)），按任务惰性加载。

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

**电脑没装 Python？** 不影响。智能体发现没有 Python 会自动切"手动写盘模式"，自己创建知识库目录，体验无差别。

**只有照片 / PDF 扫描件 / 录音？** 先用任意免费网页多模态 AI 转成纯文字（"把重点和题目提取成纯文字，保留星号重点标记"），贴进一个 `.txt` 再让智能体建库；后续纯文本流程丝滑。录音同理，先转录再喂。

**测验卡在一道题？** 直接说"这题太难 / 我想跳过"，会自动归档到错题本、放行，最后统一重温。

**跟"直接把文件夹丢给 AI"有啥区别？** 精度接近，但技能更省（每题只取相关章节，不翻整堆文件），且对越弱的模型帮助越大。详见[报告](benchmark/REPORT.md)。

---

## 开源协议

[MIT](LICENSE)。欢迎提交贡献更多科目模板或脚本。祝临考冲刺的你考神附体。🎓

<div align="center">

[![Star History](https://api.star-history.com/svg?repos=ZeKaiNie/universal-examprep-skill&type=Date)](https://star-history.com/#ZeKaiNie/universal-examprep-skill&Date)

</div>
