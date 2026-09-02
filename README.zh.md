<div align="center">

<img src="assets/exam-panic.png" width="160" alt="期末极速备考教练" />

# 期末极速备考教练

*把课程文件夹交给它：按你的资料讲课、只从资料出题、记得你学到哪。*

中文 · [English](README.md)

[![MIT](https://img.shields.io/badge/协议-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/ZeKaiNie/universal-examprep-skill/actions/workflows/ci.yml/badge.svg)](https://github.com/ZeKaiNie/universal-examprep-skill/actions)

</div>

一个 [Agent Skill](SKILL.md) 加一个小 Python 工具。把课件、笔记、作业、往年题交给任何编程智能体（Claude Code、Codex、Cursor、Gemini CLI……），它会：

- 读取 PDF / PPTX / DOCX / Markdown / TXT / HTML，自动切成章节；
- 把讲义里的图、题目和解答里的图裁成 PNG，讲题时直接放进对话；
- 从作业和试卷里抽出真题和参考答案（`hw2.pdf` 自动配 `hw2solutions.pdf`，`Problem 1.3.10` 配它的解答）；
- 按章讲解，每句话都标出处 `文件 p.页码`；
- 只用资料里的题考你，记住错题和笔记；
- 每句话都标明来源：🟢 来自资料 · 🟡 AI 补充 · ⚠️ AI 生成答案。

整门课的准备工作约 1 秒完成，所有东西都在资料旁边的 `exam-cram/` 文件夹里。

## 两分钟试一下

```bash
git clone https://github.com/ZeKaiNie/universal-examprep-skill exam-cram-coach
cd exam-cram-coach
pip install pypdfium2      # 可选：PDF 文本 + 裁图（只装 pypdf 则只有文本）
python coach.py setup samples/zh-data-structures --days 2
python coach.py next       # 第一段讲课材料，带出处
python coach.py quiz       # 作业和模拟卷里的题
```

然后对智能体说：

```text
用期末极速备考教练。资料在 D:\课程\数据结构，三天后考试，从第一章开始。
```

它会运行 `setup`，给你看章节清单，然后从 `next` 开始讲。

想用真实公开课试：`python samples/fetch.py` 会下载 MIT 6.006 六讲讲义 + Quiz 1 及官方解答、耶鲁 PSYC 110 四讲文字稿（均为 CC BY-NC-SA，见 [samples/README.md](samples/README.md)）。

## 它怎么用这些命令

| 你说 | 它运行 | 你得到 |
|---|---|---|
| “开始” / “继续” | `coach.py next` | 下一段资料，用大白话讲，带 `[lec2.pdf p.3]` 出处，附这几页裁出的配图 |
| 提问 | `coach.py ask "…"` | 只根据命中的段落回答；查不到就说“资料没讲” |
| “考考我” | `coach.py quiz` → `check` → `answer` | 作业/试卷真题（带题面图），按参考答案（带答案图）判分，错题自动记录 |
| “这章学完了” | `coach.py note --type summary` + `done` | 章节标记为 *已验证*（答对过本章材料题）或 *已讲完* |
| “做小抄” | `coach.py cheatsheet` | 由你的章节总结、疑难点、错题拼成的 `cheatsheet.md` |
| 几天后开新对话 | `coach.py status` | 精确回到上次停的地方 |

## 安装成技能

把本文件夹复制或 clone 到智能体的技能目录（如 `~/.claude/skills/exam-cram-coach`、`~/.codex/skills/…`、`~/.cursor/skills/…`、`~/.gemini/antigravity/skills/…`），确保 `python` 可用。唯一可选依赖是 `pip install pypdfium2`：有它就能读 PDF 文本并裁图（只装 `pypdf` 则只有文本）；都没装时 PDF 会被列为“需要 pypdfium2”，DOCX/PPTX/Markdown 照常。扫描件和手写页（比如你自己交的作业）会被识别并跳过，绝不当答案展示。

小模型/本地模型：用 `setup --slice 2000` 让每段讲课材料更短；每条命令的最后一行都写着下一条该跑什么，模型不必记 CLI。整份技能说明约 1100 词。

## `exam-cram/` 里有什么

| 文件 | 用途 |
|---|---|
| `study_state.json` | 进度、当前章、作答记录、错题、笔记（唯一状态文件） |
| `progress.md`、`notebook.md` | 给人看的进度和笔记视图 |
| `chapters/chNN_标题.md` | 带 `[文件 p.页码]` 锚点的章节全文 |
| `quiz_bank.json` | 题目、答案、出处、所属章、配图路径 |
| `figures/`、`figures.json` | 裁好的 PNG：讲义配图（按页）、题面图、答案图 |
| `index.json` | `ask` 用的检索块 |
| `cheatsheet.md` | `cheatsheet` 的输出 |

## 命令

```text
setup <文件夹> [--days N] [--lang zh|en] [--name 课程] [--start 章] [--slice 字数] [--fresh]
status | next [--repeat|--back] | chapter N [--part K] | goto N [--restart]
ask "关键词" [-k 5] [--chapter N]
quiz [-n 3] [--chapter N] [--all] | check <题号> | answer <题号> right|wrong|skip [--note …]
done [--chapter N] | note "…" [--type summary|confusion|note] | mistakes [--answers]
cheatsheet [--out 文件] | figures [--chapter N] [--file F] [--page P] | figure <文件> <页> [--crop x0,y0,x1,y1]
doctor | help
```

`--workspace 路径`（或环境变量 `EXAM_CRAM_WORKSPACE`）指定工作区；不指定则用上次的。

## 开发

```bash
python -m unittest discover -s tests -v     # 约 1 秒，纯标准库（没装 pypdfium2 时跳过 PDF 测试）
python eval/agent_smoke.py claude --model claude-haiku-4-5-20251001 --materials <文件夹>   # 驱动真实智能体跑一轮并打分
```

第 5 版是推倒重来：原来 14.4 万行的运行时（回执、代际账本、裁剪复核、benchmark 矩阵、三层语言分发）换成了约 1800 行真正面向学生的代码。删了什么、前后实测对比见 [CHANGELOG.md](CHANGELOG.md)、[docs/v5-refactor.md](docs/v5-refactor.md)；v4 的每项功能在 v5 里有没有，见 [docs/feature-audit.md](docs/feature-audit.md)。

## 协议

[MIT](LICENSE)。样例课程保留各自协议（见 `samples/README.md`）。
