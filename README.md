<div align="center">

<img src="assets/exam-panic.jpg" width="200" alt="Exam Cram Coach" />

# 期末极速备考教练

*只剩一晚。你什么都没复习。它不会瞎编。*

中文 · [English](README.en.md)

[![stars](https://img.shields.io/github/stars/ZeKaiNie/universal-examprep-skill?style=flat&color=blue)](https://github.com/ZeKaiNie/universal-examprep-skill/stargazers)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/ZeKaiNie/universal-examprep-skill/actions/workflows/ci.yml/badge.svg)](https://github.com/ZeKaiNie/universal-examprep-skill/actions)
[![agents](https://img.shields.io/badge/works%20with-6%20agents-brightgreen.svg)](docs/agent-portability.md)
[![tokens](https://img.shields.io/badge/tokens-−90%25-orange.svg)](#怎么做到的)

**闭卷 <10% → 装上技能 ~90%+** · Token −90% · 越界弃答 100% · 6 个 agent

</div>

你认识他。考试前夜，头发乱成一团，眼睛瞪得溜圆，一整门课一个字没看。这个技能是给他的——不再灌一堆它自己都拿不准的"知识"，只讲你资料里真有的东西，其余的老实说"资料里没有"。

**30 秒上手** —— 克隆整个仓库，然后对你的 agent 说一句话：

```bash
git clone https://github.com/ZeKaiNie/universal-examprep-skill .claude/skills/universal-exam-cram-coach
# 对 Claude Code / Cursor 说："用这个技能初始化我的备考空间"，再把讲义/大纲/真题丢进来
```

---

## Before / after

**装上技能**——每条结论都带来源，能核对：

> **[#vis_q1]** 题面图里阴影区域表示哪个集合关系？
> **A 与 B 的交集。**
> `题目来源：hw02.pdf 第 3 页｜答案来源：hw02_sol.pdf｜🟢 来自资料`

**闭卷 / 裸 agent**——听起来一样自信，但你无从判断真假：

> 阴影是**并集**。<sub>（资料里其实是交集；没有来源标注，无从核对——这正是"瞎编"发生的地方。）</sub>

区别不在口气，在于**每个结论能不能落回你的材料**。

---

## Numbers

两门毫不相干的名校公开课，**同一模型、同一题，只改"给不给它材料"**。题目是从课程转录里挖的、常识答不出的细节（教授本人举的例子、点名的冷门研究、具体数字）——不给材料时模型只能靠先验，几乎全崩。

<div align="center"><img src="benchmark/docs/img/hard_psyc_correct_zh.svg" width="600" alt="闭卷 vs 装上技能 正确率对比" /></div>

正确率，越高越好（判分 Sonnet）：

| 课程 · 模型 | 闭卷 | 裸文件 + 通用 agent | 装上技能 |
|---|:--:|:--:|:--:|
| PSYC 110 · Opus 4.8 | 9% | 96% | **100%** |
| PSYC 110 · Sonnet 4.6 | 7% | 96% | 87% |
| PSYC 110 · Haiku 4.5 | 9% | 89% | **96%** |
| 6.006 · Haiku 4.5 | 31% | 85% | **89%** |

两个领域（人文事实召回 / 算法推导）结论一致：**不给材料模型答不出，grounding 才是正确率的来源**。技能与"裸文件 agent"精度接近但更省——它只取压缩过的相关章节，不是每题翻检整堆原始文件。

<details><summary>每题成本（技能真正的差异：同精度更省）</summary>

技能只取压缩过的相关章节，裸文件 agent 每题都要翻检整堆原始文件——所以同精度下技能更省：

| 每题成本 | 闭卷 | 裸文件 agent | 装上技能 |
|---|:--:|:--:|:--:|
| PSYC 110 | $0.033 | $0.117 | **$0.102** |
| 6.006 | $0.034 | $0.066 | **$0.063** |

</details>

完整方法、三臂设计、成本、人工 kappa 校准、局限 → **[测试报告](benchmark/REPORT.md)**。

---

## 怎么做到的

一条"能不编就不编"的阶梯：

1. **只从资料出题** —— 测验题来自 `quiz_bank.json` 真题库，不即兴编题。
2. **来源强制标注** —— 每条结论标 `🟢 来自资料` / `🟡 AI 补充，可能与老师讲的不一致` / `⚠️ AI 生成答案`，绝不冒充教材。
3. **资料里没有就说没有** —— 遇到资料未覆盖的问题，如实弃答，不硬编（实测越界弃答 100%）。
4. **画图题先跑算法再画** —— 二叉树 / 图遍历这类题，后台跑标准算法求出拓扑再渲染，禁止凭空想象。
5. **图依赖题 fail-closed** —— 需要配图却没图的题绝不出，不给学生一道没法答的题。
6. **惰性加载 wiki** —— 按章切片、按进度加载，长对话不撑爆上下文，**Token −90%**。

---

## Install

### Claude Code

```bash
git clone https://github.com/ZeKaiNie/universal-examprep-skill .claude/skills/universal-exam-cram-coach
```

项目内 `.claude/skills/` 或全局 `~/.claude/skills/` 均可。

### Codex / Cursor / Windsurf / Antigravity

克隆仓库，让 agent 读 `AGENTS.md`（一屏兜底契约）或加载 `skills/`。这些工具能直接写盘、跑脚本。

### 网页版（ChatGPT / DeepSeek / Gemini / 豆包）

无法写本地文件，改用一键平替提示词：复制 [`prompts/web_prompt.md`](prompts/web_prompt.md)（英文版 [`web_prompt.en.md`](prompts/web_prompt.en.md)）发给它，再贴上材料。

> 完整加载矩阵（各 agent 支持程度、入口文件）见 [`docs/agent-portability.md`](docs/agent-portability.md)。英文用户另有派生英文面 [`SKILL.en.md`](SKILL.en.md)。

---

## 子技能

单体技能拆成 9 个单一职责子技能，agent 按需加载：

| 子技能 | 做什么 |
|---|---|
| `exam-cram` | 主协调器——编排四步工作流 + 学习模式路由 |
| `exam-ingest` | 从材料建工作区（wiki + 题库 + 进度） |
| `exam-tutor` | 按章惰性授课（含零基础七步精讲、画图先跑算法） |
| `exam-quiz` | 题库抽题判分（选择/主观/画图/填空/判断/代码 6 题型） |
| `exam-review` | 错题与概念疑难点复盘 |
| `exam-cheatsheet` | 考前速记小抄 |
| `exam-audit` | 只读检查工作区健康度 |
| `exam-help` | 一屏速查卡（工作流 / 模式 / 文件约定） |
| `confusion-tracker` | 自动记录复习中的概念疑问，形成考前盲区清单 |

---

## Development

零成本、可频繁跑的结构化校验（不烧 API 额度）：

```bash
python -m unittest discover -s tests -v          # 单元测试（纯 stdlib，进 CI）
python scripts/validate_workspace.py path/to/ws  # 校验一个建好的备考工作区
```

真·付费 benchmark 很贵（一次矩阵几十美元/几小时），只手动跑——操作手册见 [`benchmark/docs/running-real-runs.md`](benchmark/docs/running-real-runs.md)，分层策略见 [`benchmark/docs/test_tiers.md`](benchmark/docs/test_tiers.md)。工作区文件格式见 [`docs/file-format.md`](docs/file-format.md)。

---

## FAQ

**电脑没装 Python？** 不影响。agent 发现没有 Python 会自动切"手动写盘模式"，自己创建 wiki 目录，体验无差别。

**只有照片 / PDF 扫描件 / 录音？** 先用任意免费网页多模态 AI 转成纯文字（"把重点和题目提取成纯文字，保留星号重点标记"），贴进一个 `.txt` 再让 agent 建库；后续纯文本流程丝滑。录音同理，先转录再喂。

**测验卡在一道题？** 直接说"这题太难/我想跳过"，会自动归档到错题本、放行，最后统一重温。

**跟"直接把文件夹丢给 AI"有啥区别？** 精度接近，但技能更省（每题只取相关章节，不翻整堆文件），且对越弱的模型帮助越大。详见[报告](benchmark/REPORT.md)。

---

## License

[MIT](LICENSE)。欢迎 PR 贡献更多科目模板或脚本。祝临考冲刺的你考神附体。🎓

<div align="center">

[![Star History](https://api.star-history.com/svg?repos=ZeKaiNie/universal-examprep-skill&type=Date)](https://star-history.com/#ZeKaiNie/universal-examprep-skill&Date)

</div>
