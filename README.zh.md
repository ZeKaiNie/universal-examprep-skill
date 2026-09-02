<div align="center">

<img src="assets/exam-panic.png" width="180" alt="期末极速备考教练" />

# 期末极速备考教练 · Flash 版

*把课程文件夹交给编程智能体：它按你的课件讲课、把图放到你面前、用你自己的作业考你、记得你学到哪。*

中文 · [English](README.md)

[![收藏数](https://img.shields.io/github/stars/ZeKaiNie/universal-examprep-skill?style=flat&color=blue)](https://github.com/ZeKaiNie/universal-examprep-skill/stargazers)
[![发布](https://img.shields.io/github/v/release/ZeKaiNie/universal-examprep-skill?label=release&color=orange)](https://github.com/ZeKaiNie/universal-examprep-skill/releases/latest)
[![MIT](https://img.shields.io/badge/协议-MIT-blue.svg)](LICENSE)
[![持续集成](https://github.com/ZeKaiNie/universal-examprep-skill/actions/workflows/ci.yml/badge.svg)](https://github.com/ZeKaiNie/universal-examprep-skill/actions)

**一条命令准备好整门课 · 按你的资料讲 · 讲到哪张图就给你看哪张 · 用你的作业和真题考你 · 编的内容绝不冒充资料**

</div>

期末极速备考教练是一个 [Agent Skill](SKILL.md) 加一个很小的 Python 工具。你把课件、笔记、作业、往年题所在的文件夹交给编程智能体（Claude Code、Codex、Cursor、Windsurf、Antigravity、Gemini CLI……），之后它会：

- 读取 PDF / PPTX / DOCX / Markdown / TXT / HTML，把整门课切成章节；
- **把讲义里的图、题目和解答里的图裁出来**，讲到对应内容时直接放进对话；
- 从作业和试卷里抽出真题和参考答案（`hw2.pdf` 自动配 `hw2solutions.pdf`，“Problem 1.3.10” 自动配它的解答）；
- 一章一章用大白话讲，每个结论都标出处 `文件 p.页码`；
- 只用这些题考你，记住错题和笔记，最后拼一份小抄；
- 每句话都标明来源，你永远知道哪句是老师讲的、哪句是 AI 补的：

| 标签 | 含义 |
|---|---|
| 🟢 **来自资料** | 能追到原文件和页码 |
| 🟡 **AI 补充** | 智能体补的背景，可能和你老师讲的不完全一致 |
| ⚠️ **AI 生成答案** | 资料里没有答案，这是智能体自己算的 |

**Flash** 是这个版本的名字：整门课几秒钟准备完，技能说明只有约 1100 词，学习循环只有 7 条命令，小模型也能跑（实测 Gemini flash-lite 和 Claude Haiku）。之前的“完整版”（v4.3，带网页讲义/PDF 和一整套核验流水线）仍可从 [v4.3 发布页](https://github.com/ZeKaiNie/universal-examprep-skill/releases/tag/v4.3) 下载，区别见 [Flash 版与旧完整版](#flash-版与旧完整版)。

## 目录

1. [五分钟开始复习](#五分钟开始复习)
2. [一次复习长什么样](#一次复习长什么样)
3. [资料怎么放](#资料怎么放)
4. [Flash 版与旧完整版](#flash-版与旧完整版)
5. [`exam-cram/` 文件夹里有什么](#exam-cram-文件夹里有什么)
6. [命令一览](#命令一览)
7. [实测数据](#实测数据)
8. [常见问题](#常见问题)
9. [给开发者](#给开发者)

## 五分钟开始复习

### 第 0 步 · 你需要什么

- **一个能用终端的编程智能体**：Claude Code、Codex、Cursor、Windsurf、Antigravity、Gemini CLI，或任何能运行命令、读文件的智能体。课件以 PDF 为主时，尽量用智能体的桌面版或编辑器界面，纯终端往往显示不了图。
- **Python 3.8 或更新版本。** 在终端输入 `python --version` 检查。Windows 用户到 [python.org](https://www.python.org/downloads/) 下载安装，安装时勾选 *Add python.exe to PATH*。
- 可选但强烈建议：`pip install pypdfium2`。有它才能读 PDF 文本并裁图。没装时 PDF 会被列为“需要 pypdfium2”，PPTX / DOCX / Markdown / TXT 照常可用。

### 第 1 步 · 安装技能

**最省事：让智能体自己装。** 把下面这段发给它；它可能会请你批准联网或写入技能目录：

```text
安装期末极速备考教练技能：从 https://github.com/ZeKaiNie/universal-examprep-skill/releases/latest 下载最新发布中的 exam-cram-coach-flash.zip（或 git clone 仓库），放进你的用户级技能目录，保证 SKILL.md 和 coach.py 直接位于名为 exam-cram-coach 的文件夹内。替换旧版前先备份。然后运行 `pip install pypdfium2` 和 `python <该文件夹>/coach.py doctor`，把安装路径和 doctor 的输出告诉我。
```

各平台版本：

<details><summary>Claude Code</summary>

```text
请把 https://github.com/ZeKaiNie/universal-examprep-skill 安装或更新到 ~/.claude/skills/exam-cram-coach（SKILL.md 和 coach.py 必须直接在这个文件夹里）。覆盖旧版前先问我。然后运行 `pip install pypdfium2` 和 `python ~/.claude/skills/exam-cram-coach/coach.py doctor`，把结果给我看。
```
</details>

<details><summary>Codex</summary>

```text
请从 https://github.com/ZeKaiNie/universal-examprep-skill 安装最新版期末极速备考教练到我的 Codex 技能目录，文件夹名为 exam-cram-coach（SKILL.md 和 coach.py 直接在里面）。先备份旧版。运行 `pip install pypdfium2`，再运行 `python <安装路径>/coach.py doctor`，告诉我路径和输出，并说明是否需要新建任务才能看到技能。
```
</details>

<details><summary>Cursor</summary>

```text
请获取 https://github.com/ZeKaiNie/universal-examprep-skill，安装为 exam-cram-coach，放到 Cursor 的用户技能目录（~/.cursor/skills/ 或 ~/.agents/skills/），SKILL.md 和 coach.py 直接在里面。先备份旧版，运行 `pip install pypdfium2`，确认 Cursor 能发现 SKILL.md，并告诉我路径。
```
</details>

<details><summary>Windsurf</summary>

```text
请获取 https://github.com/ZeKaiNie/universal-examprep-skill，安装到 ~/.codeium/windsurf/skills/exam-cram-coach。下载或覆盖文件前先问我。运行 `pip install pypdfium2`，确认 Cascade 能发现 SKILL.md，并告诉我路径。
```
</details>

<details><summary>Antigravity</summary>

```text
请获取 https://github.com/ZeKaiNie/universal-examprep-skill，安装到 ~/.gemini/antigravity/skills/exam-cram-coach（SKILL.md 和 coach.py 直接在里面）。写入工作区以外的目录前先问我。运行 `pip install pypdfium2`，重新扫描技能，把路径和 `python <路径>/coach.py doctor` 的输出告诉我。
```
</details>

<details><summary>Gemini CLI</summary>

```bash
gemini skills install https://github.com/ZeKaiNie/universal-examprep-skill.git
pip install pypdfium2
```
</details>

**手动安装。** 到[最新发布页](https://github.com/ZeKaiNie/universal-examprep-skill/releases/latest)下载 `exam-cram-coach-flash.zip`，解压到智能体的技能目录，得到 `…/skills/exam-cram-coach/SKILL.md` 和 `…/skills/exam-cram-coach/coach.py`。然后打开终端运行 `pip install pypdfium2`。

在任何终端都可以自检：

```bash
python 技能路径/exam-cram-coach/coach.py doctor
```

它会打印版本、PDF 支持是否可用、上次使用的工作区。

### 第 2 步 · 把资料放进一个文件夹

课件、讲义、教材章节、作业、答案、小测、往年卷全放一个文件夹（有子文件夹也可以）。文件怎么命名能让章节和答案自动对上，见[资料怎么放](#资料怎么放)。你自己交过的手写作业扫描件放着没关系：它会被识别出来，绝不会被当成答案。

### 第 3 步 · 发第一句话

```text
用期末极速备考教练。资料在 D:\课程\概率论，三天后考试，我零基础，用中文从第 1 章开始讲。
```

智能体会运行一条命令（`coach.py setup`），一千页也只要几秒，然后回复章节清单、找到的题目数，以及提醒（例如“3 个 PDF 需要 `pip install pypdfium2`”或“文件 X 没有文字，我会直接打开看”），接着开始讲第 1 章。

### 第 4 步 · 之后正常聊天就行

| 你说 | 会发生什么 |
|---|---|
| “继续” / “下一段” | 本章下一段资料，用大白话讲，带 `[lec2.pdf p.3]` 出处和这几页裁出的图 |
| “什么是……？”“为什么……？” | 在你的资料里检索，只根据命中的段落回答；查不到就明说“资料里没有” |
| “考考我” | 一道作业或试卷真题（带题面图），按参考答案判分；错题自动记入错题本 |
| “这章学完了” | 本章标为“已验证”（答对过本章材料题）或“已讲完”，进入下一章 |
| “看看错题” / “做小抄” | 重讲待复习错题；由你的章节总结、疑难点、错题生成 `cheatsheet.md` |
| 几天后开新对话 | 说“继续备考”，它会从上次停的地方接着讲 |

## 一次复习长什么样

下面是一门概率论课上学生真实看到的内容（Gemini flash-lite 会话节选）。转录里出现 `![](…png)` 的位置，对话中显示的是 `setup` 从讲义 PDF 里裁出来的图：

> **第 1 章：集合论**
>
> 🟢 集合（Set）就是一组事物的集合。用大写字母表示集合（如 A），小写字母表示元素（如 x）。x ∈ A 表示 x 是 A 的元素，x ∉ A 表示不是。（ch01.pdf p.3）
>
> 🟢 **并集 A ∪ B**：属于 A 或属于 B（或两者都属于）的所有元素。用文氏图表示就是两个圆覆盖的总面积。（ch01.pdf p.4）
>
> *［此处显示从 ch01.pdf 第 4 页裁出的图］*
>
> 🟢 **划分（Partition）**：一组集合既互斥又共同穷举，就是全集的一个划分。（ch01.pdf p.9）
>
> 这些概念是后面概率论的基础。准备好了回复“next”，我们看具体例题。

之后考到一道只有教材题号的作业题：

> **测验 · Problem 1.1.2**（hw1.pdf p.1）
>
> 🟡 已知条件（摘自参考答案开头）：披萨要么是 Tuscan（T）要么是 Neapolitan（N），所以 {N, T} 是一个划分；M 是蘑菇，O 是洋葱。问：N 和 M 互斥吗？N 和 T 共同穷举吗？……
>
> *（学生：“我不会，直接看答案”）*
>
> 🟢 **参考答案解析**（hw1solution.pdf p.1）：因为 N = Tᶜ，N ∩ M ≠ ∅，所以 N 和 M **不**互斥；N ∪ T = S，所以它们**是**共同穷举的；从图上看 T 和 O 完全不重叠……
>
> *［此处显示从 hw1solution.pdf 第 1 页裁出的解答文氏图］*
>
> 用 `python coach.py answer q001 right` 或 `… wrong` 记录这题，或者回复“next”。

每个结论都有页码，每张图都是原文件裁出来的，答案图只在你要看答案之后才出现。裁出来的图长这样（来自 MIT 6.006 讲义和 Quiz 1，CC BY-NC-SA，`setup` 自动产生，没有任何手工处理）：

<div align="center">
<img src="docs/img/mit_lecture_figure.png" width="420" alt="从讲义页裁出的图" />
&nbsp;&nbsp;
<img src="docs/img/mit_question_figure.png" width="380" alt="连同题面一起裁出的试卷题" />
</div>

## 资料怎么放

任何布局都能用，但下面几个习惯能让章节切分和题答配对完全自动：

| 目标 | 做法 |
|---|---|
| 章节顺序正确 | 讲义文件名里带编号：`lec3.pdf`、`Lecture 03 - Sorting.pptx`、`ch05_hashing.docx`、`第3章_栈.pptx`。首页写着“Chapter 3 / 标题”的幻灯片也能识别。一整本文件内部有“第N章 / Chapter N”标题时会按标题拆开。 |
| 作业与答案配对 | 主文件名相同：`hw2.pdf` + `hw2solutions.pdf`、`作业2.txt` + `作业2答案.txt`、`q1.pdf` + `q1_sol.pdf`。下载后缀 `hw2 (4)(1).pdf` 会被忽略。 |
| 题目能被抽出 | 题目要编号：`Problem 3`、`3.`、`(3)`、`第3题`，或教材题号 `Problem 1.3.10`。“二、填空题”这类节标题会重新计数。 |
| 只有教材题号、题干不在文件夹里 | 也没问题：题目会与解答配对，`quiz` 时给出解答开头复述的已知条件并标 🟡。 |
| 图 | PDF 不用管（矢量图和嵌入图自动裁出），PPTX / DOCX 的嵌入图自动抽出。散装图片命名为 `fig3.png` 会挂到第 3 章。 |
| 扫描件、手写页 | 自动识别并跳过，绝不当作题面或答案。 |
| 语言 | 按资料自动识别，可用 `--lang zh|en` 指定。智能体用你说话的语言回复。 |

支持的输入：`.pdf`（需要 `pypdfium2` 或 `pypdf`）、`.pptx`、`.docx`、`.md`、`.txt`、`.html` 和图片文件。不读 Excel 和音频。

## Flash 版与旧完整版

| | **Flash 版（本版本，v5.x）** | 完整版（v4.3，仍可下载） |
|---|---|---|
| 准备 | 一条命令，几秒 | 多条确认命令，智能体自己渲染 PDF 页并写“回执” |
| 智能体要读的技能文本 | 约 6 KB | 约 140 KB |
| 图 | 工具从 PDF / PPTX / DOCX 裁好，随每段、每题、每个答案列出 | 智能体自己渲染页面、拼 contact sheet、逐块复核 |
| 小模型能用 | 能（实测 Gemini flash-lite、Claude Haiku） | 不能 |
| 网页讲义（HTML/PDF）、逐页视觉 QA | 无 | 有 |
| 知识点窗口、3×4 学习模式、代际账本、远端解析适配 | 无 | 有 |
| 来源标签、只出材料题、错题、笔记、小抄、跨对话进度 | 有 | 有 |

除非你明确要 v4.3 的可打印网页讲义，否则用 Flash 版。两个版本的工作区互不兼容：切换后对资料夹重新 `setup` 即可。

## `exam-cram/` 文件夹里有什么

`setup` 会在资料旁边（或 `--workspace` 指定的位置）建一个文件夹。全是纯文本和 PNG，不上传到任何地方。

| 路径 | 用途 |
|---|---|
| `study_state.json` | 唯一的状态文件：课程、当前章、各章进度、作答记录、错题、笔记 |
| `progress.md`、`notebook.md` | 给人看的进度和笔记视图（每次变化自动重生成） |
| `chapters/chNN_标题.md` | 带 `[文件 p.页码]` 锚点的章节全文，可以直接打开对照 |
| `quiz_bank.json` | 每道题的题面、答案、出处文件/页、所属章、配图路径 |
| `figures/` + `figures.json` | 裁好的图：`ch03_p12_1.png`（讲义第 12 页）、`hw1solution_q001_ans_1.png`（q001 的答案图） |
| `index.json` | `ask` 用的检索块 |
| `cheatsheet.md` | `cheatsheet` 的输出 |

想从头来过，删掉这个文件夹或运行 `setup … --fresh`。

## 命令一览

平时不用你敲，智能体会自己跑；用来查看状态或做自动化时有用。

```text
python coach.py setup <文件夹> [--days N] [--lang zh|en] [--name 课程名] [--start 章] [--slice 字数] [--fresh]
python coach.py status                        进度面板
python coach.py next [--repeat|--back]        当前章下一段 + 本段配图
python coach.py chapter N [--part K]          查看第 N 章（列出分段或打印某段）
python coach.py goto N [--restart]            切换章节
python coach.py ask "关键词" [-k 5] [--chapter N]        在资料里检索（退出码 4 = 没找到）
python coach.py quiz [-n 3] [--chapter N] [--all]        抽题（错题优先）
python coach.py check <题号>                  参考答案、出处与答案图
python coach.py answer <题号> right|wrong|skip [--note …]  记录作答
python coach.py done [--chapter N]            本章讲完，进入下一章
python coach.py note "…" [--type summary|confusion|note] [--chapter N]
python coach.py mistakes [--answers]          待复习错题
python coach.py cheatsheet [--out 文件]       生成小抄
python coach.py figures [--chapter N] [--file F] [--page P]   列出裁好的图
python coach.py figure <文件> <页> [--crop x0,y0,x1,y1] [--scale 2]   截整页或局部
python coach.py doctor                        环境检查
python coach.py help
```

`--workspace 路径`（或环境变量 `EXAM_CRAM_WORKSPACE`）指定工作区，不指定则用上次的。`--slice 2000` 让每段更短，适合小模型。

## 实测数据

以下数字均在一台 Windows 11 笔记本、Python 3.12 上实测，复现命令见 [docs/v5-refactor.md](docs/v5-refactor.md)。

**准备速度与抽取质量**

| 课程 | 文件 | `setup` 耗时 | 章节 | 题目与答案配对 | 裁图 |
|---|---|---|---|---|---|
| MIT 6.006（OCW）：6 讲讲义 + Quiz 1 + 官方解答 | 8 个 PDF，1.9 MB | 1.4 秒 | 6/6，标题正确 | 9/9 | 讲义 34 张 |
| 耶鲁 PSYC 110：4 讲文字稿 | 4 个 Markdown | 0.1 秒 | 4/4 | （无作业） | — |
| EEC 160 应用概率（私有课程）：9 份幻灯片讲义、9 份带手写扫描的作业、9 份解答 | 27 个 PDF，1000 页，30 MB | 9 秒 | 9/9，跨行标题拼回 | 89/89（按教材题号） | 讲义 235 张 + 答案图 16 张；140 页扫描件跳过 |

**弱模型真的能按它工作。** 只给 `SKILL.md` 的四轮中文会话（[docs/weak-model-test.md](docs/weak-model-test.md)）：

| 模型 | setup → next → quiz → check | 引用页码 | 🟢/🟡 标签 | 回复里嵌入的图 | 编造命令 |
|---|---|---|---|---|---|
| Gemini flash-lite（Antigravity） | ✅ | 17 | 8/1 | 10 张 | 无 |
| Gemini flash（Antigravity） | ✅ | 21 | 14/15 | 6 张 | 无 |
| Claude Haiku 4.5 | ✅ | 13 | 14/14 | 5 张 | 无 |

**与 v4.3 相比**（同一门 MIT 课）：智能体要读的技能文本 140 KB → 6.5 KB；开讲前脚本调用 5 次 → 1 次；单元测试 12 分钟 → 不到 1 秒；仓库 14.4 万行 → 2500 行。

## 常见问题

**智能体说 PDF 需要 pypdfium2。** 运行 `pip install pypdfium2`（Windows 上找不到 `pip` 时用 `py -m pip install pypdfium2`），然后让智能体重新 `setup`。只装 `pypdf` 也能读文本，但不能裁图。

**提示 `python` 不是内部或外部命令。** 到 python.org 安装并勾选 *Add python.exe to PATH*，或者把 Python 的完整路径告诉智能体。

**对话里看不到图。** 有些终端不能显示图片。用智能体的桌面版或编辑器（Claude Desktop、Codex 桌面版、Cursor、Windsurf、Antigravity），或者直接打开它打印的 PNG 路径。智能体自己总能打开文件看图。

**我的课件是没有文字的扫描件。** 会被列为“没有文字”；智能体可以用 `coach.py figure <文件> <页>` 渲染任何一页再用自己的视觉能力读。手写作业则是故意跳过的。

**题目只显示“Problem 1.4.4”。** 题干在教材里，不在你的文件夹里。`quiz` 会打印参考解答开头复述的已知条件（🟡），智能体按解答讲；它不能自己编一道别的题。

**一道题也没找到。** 看上面的命名建议；题目开头要有编号或题号。`quiz --all` 从所有章抽题。

**章节切错了。** 文件名里写上章号（`lec3`、`第3章`、`03-…`），或把合并的文件拆开。`coach.py chapter N` 能看到每章包含什么。

**有好几门课。** 每个资料夹各有自己的 `exam-cram/`；默认用上次的，也可以传 `--workspace`。

**想从头来 / 改考试日期。** `setup <文件夹> --fresh` 清空进度；`setup <文件夹> --days 2` 只改日期、保留进度。

**我的资料会被传到哪里？** 哪里都不传。工具只往 `exam-cram/` 里写文件。你的智能体会看到它打印的文字和图片，和你用智能体打开任何文件一样。

**不用智能体能用吗？** 能：每条命令都输出纯文本，`python coach.py next`、`python coach.py quiz` 本身就是一个阅读和刷题工具。

**没有 Python 的网页对话能用吗？** 技能有降级方案（SKILL.md §7），但进度、配图和测验在本地智能体里效果好得多。

## 给开发者

```bash
git clone https://github.com/ZeKaiNie/universal-examprep-skill exam-cram-coach
cd exam-cram-coach
pip install pypdfium2
python -m unittest discover -s tests -v          # 43 个用例，约 1 秒
python coach.py setup samples/zh-data-structures  # 内置中文样例课
python samples/fetch.py                           # 下载 MIT 6.006 + 耶鲁 PSYC 110（CC BY-NC-SA）
python eval/agent_smoke.py claude --model claude-haiku-4-5-20251001 --materials <文件夹>   # 驱动真实智能体跑一轮
python release.py                                 # 打包 dist/exam-cram-coach-flash.zip
```

结构：`SKILL.md`（智能体遵循的说明）、`coach.py` + `coach/`（`extract` → `chapters` → `questions` → `figures` → `index` → `state` → `cli`）、`tests/`、`samples/`、`eval/`（智能体冒烟测试与打分）、`docs/`（[重构记录](docs/v5-refactor.md)、[功能核查](docs/feature-audit.md)、[弱模型实测](docs/weak-model-test.md)）。版本历史见 [CHANGELOG.md](CHANGELOG.md)，贡献说明见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 协议

[MIT](LICENSE)。样例课程保留各自协议（见 [samples/README.md](samples/README.md)）；上面两张示例裁图来自 MIT OpenCourseWare 6.006（CC BY-NC-SA 4.0）。祝考试顺利。🎓

<div align="center">

<a href="https://www.star-history.com/?repos=ZeKaiNie%2Funiversal-examprep-skill&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=ZeKaiNie/universal-examprep-skill&type=date&theme=dark&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=ZeKaiNie/universal-examprep-skill&type=date&legend=top-left" />
 </picture>
</a>

</div>
