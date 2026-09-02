# v5 重构记录：删了什么、留了什么、实测对比

日期：2026-09-01。所有数字都在本机（Windows 11，Python 3.12）实测，命令可复现。

## 1. 旧版的问题（为什么不是修修补补）

| 指标 | v4.3 |
|---|---|
| Python 行数 | 144,435 行，495 个文件 |
| CLI 表面 | 36 个子命令、108 个参数 |
| 智能体开讲前要读的技能文本（zh 学生） | `SKILL.md` + `locales/zh/SKILL.md` + `skills/exam-cram/SKILL.md` + `skills/exam-tutor/SKILL.md` + 语言包 ≈ 140 KB（约 3.5 万 token），全部 skill/AGENTS/locales 文本 240 KB |
| 开讲前的脚本调用 | `workspace-list` → `exam_start confirm` → `lightweight_session init` → `status` → `plan`（5 次）；`confirm` 会对安装包 110 个文件 3.8 MB 做两遍 SHA-256 并调用 git 两次；之后**每条**命令再各做两遍 |
| 谁来读 PDF | 轻量模式下脚本不碰 PDF 字节：要求智能体自己把每页渲染成 PNG、拼 4 页 contact sheet、对每个裁剪块单独发起一次 “crop review” 模型调用并写 schema-3 回执 |
| 代码用途（子代理逐文件统计） | `exam_start.py` 约 75% 是回执/哈希/恢复；`lightweight_session.py` 5772 行中约 88% 是回执校验、答案污染契约、状态机审计 |
| 测试 | 1000+ 用例，本机 `unittest discover` 12 分 1 秒 |

结果是：大模型要十几轮对话才能讲到第一句课；小模型（短上下文、指令跟随弱）根本无法完成“schema-3 visual receipt”这类要求；学生需要先理解 lightweight/full、chat/visual、ordinary/isolated、batch/step_by_step 四组独立开关。

## 2. 新版是什么

```
SKILL.md          约 1100 词，一张表说明每种学生请求对应哪条命令
coach.py          入口
coach/            extract · chapters · questions · index · state · figures · cli · text（约 2.5k 行，v5.1 含裁图）
samples/          内置中文《数据结构》样例；fetch.py 下载 MIT 6.006 + Yale PSYC 110
tests/            8 个文件，40+ 用例，约 1 秒
```

一条 `setup` 完成整门课，之后只有 7 条命令构成完整学习循环：`next / ask / quiz / check / answer / note / done`。

### 保留并改进的原有功能

| 原功能 | 现在 |
|---|---|
| 🟢/🟡/⚠️ 来源标签 | 保留；`next`、`ask`、`check` 的输出自带 🟢 与 `文件 p.页码`，模型只需在自己补充的内容上标 🟡/⚠️ |
| 只从资料出题、不自编题冒充 | 保留；`quiz` 只读 `quiz_bank.json`；无题时明确提示“只能出 AI 自编题并注明 ⚠️” |
| `verified` 与 `covered_unverified` 的区分 | 保留为 `verified` / `done`：答对过本章材料题才算已验证 |
| 错题本、疑难点、笔记、小抄 | 保留：`answer wrong` 自动进错题并优先重考；`note --type confusion/summary`；`cheatsheet` 拼小抄 |
| 跨对话进度 | 保留：`study_state.json` 一个文件，`status` 恢复 |
| 中英双语 | 保留：CLI 输出按工作区语言切换，自动识别资料语言 |
| BM25 检索 + 中文双字组 | 保留并简化：无命中时退出码 4，模型据此说“资料没讲” |
| 章节识别 | 改进：文件名编号（lec3 / 第3章 / 03-）、文首 “Lecture N: 标题”、整本文件内 “第N章/Chapter N” 标题拆分，三种布局都实测通过 |
| 题目抽取 | 新增：`Problem N` / `1.` / `第N题` / `（1）` 多种编号，节内重新计数（一、选择题 … 二、填空题），题目文件与解答文件自动配对，选择项、判断、填空类型识别，分值抽取 |
| PDF 读取 | 改进：`pypdfium2`（或 `pypdf`）可选装即可读文本，`pypdfium2` 还负责裁图（旧版要求智能体逐页渲染成图）；扫描/手写页自动识别并跳过 |
| 小模型适配 | 新增：`--slice` 控制每段字数；每条输出末尾写明下一条命令；不要求模型产出 JSON |

### 删除的功能（及原因）

- 完整建库 / 轻量按需双模式与 `.ingest/` 代际账本、`material_build_pending`、recovery log、mutation lock：对学生不可见，只增加失败面。
- Study Guide HTML/PDF 渲染、逐页视觉 QA、crop 回执、答案污染契约：智能体在聊天里讲课 + `chapters/*.md` 已能满足复习；打印可直接用 `cheatsheet.md`。
- LangGraph / OpenAI / MinerU / Docling 适配器：全部是 stub 或“永远不可用”的路由说明。
- benchmark 矩阵、drift、behavior smoke、calibration：与学生使用无关；核心结论（材料检索比闭卷强）不需要每次发布重跑。
- 三层语言分发（root → locales/xx/SKILL → skills/* → locales/xx/skills/*）：改为一份 SKILL.md + CLI 内置双语字符串。
- 3 学习模式 × 4 时间档、知识点窗口、`artifact_mode`、`answer_explanation_mode`、`interaction_style`、工作区注册表、运行时哈希回执：全部换成 `--days` 与 `--start` 两个参数。

## 3. 实测对比

### 3.1 启动到开讲

同一份资料：MIT OCW 6.006 六讲讲义 + Quiz 1 + 官方解答（8 个 PDF，1.9 MB）。

| | v4.3 | v5 |
|---|---|---|
| 智能体需读的技能文本 | ≈140 KB | 6.5 KB（约 1100 词，含图片规则） |
| 开讲前脚本调用次数 | 5 | 1 |
| 脚本累计耗时 | 2.46 s（未含智能体自行渲染 PDF 页、contact sheet、逐块 crop review 的模型调用） | 1.1–1.4 s（含 pypdf 抽取全部 8 个 PDF） |
| 开讲前智能体还要做什么 | 渲染 lec1 5 页为 PNG、拼 contact sheet、逐块 crop review、写 schema-3 receipt、`record-visual`、写 notebook、`mark-taught` | 无。`next` 直接给出第一段原文 |
| 生成的工作区文件 | `exam_runtime_receipt.json`（16 KB 哈希）、`.lightweight/session.json`、`.study_state.lock` | `study_state.json`、`chapters/*.md`、`quiz_bank.json`、`index.json`、`progress.md`、`notebook.md` |

### 3.2 抽取质量（真实公开课，非合成数据）

**MIT 6.006（PDF，CIDFontType0 字体）**

- 章节：6/6 正确识别编号与标题（Introduction / Data Structures / Sorting / Hashing / Linear Sorting / Binary Trees I）。
- 页眉页脚：`3 6.006 Quiz 1 Name`、`Lecture 1: Introduction` 之类的运行页眉自动去除。
- 题目：Quiz 1 共 9 题（含“写名字”那题）全部抽出，9/9 与 `q1_sol.pdf` 自动配对得到官方解答，多小问的 `Solution:` 段落按顺序保留，`Common Mistakes` 一并保留；分值 `[8 points]` 全部抽出。
- 章节归属：Quiz 是跨章的，按 BM25 自动推测并标注“章节为自动推测”；9 题中 6 题落在合理章（排序题→Linear Sorting、堆/AVL→Binary Trees），3 题（含数据库设计题）落到 Introduction。归属只影响 `quiz` 默认范围，`quiz --all` 不受影响。
- 检索：`ask "counting sort radix sort running time"` → Lecture 5 p.4 Radix Sort 段落；`ask "quantum entanglement"` → 退出码 4。
- 裁图（v5.1）：讲义 34 张图区域（表格、树、图示）；Quiz 1 中带图或带表的 5 题各裁出题面图和答案图（如 Problem 4 “Transforming Trees” 的树）。README 里的两张示例图就来自这里。

**Yale PSYC 110（HTML 转 Markdown 文字稿，每讲 2–5 万字符）**

- 章节：4/4，标题来自文首 “Lecture N - 标题”；讲内的 “Chapter 1/2/3…” 小节**没有**被误拆成章。
- 分段：Lecture 2 自动分成 10 段，每段约 5000 字符；`next` 逐段吐出。
- 检索：`Descartes dualism argument` → Lecture 2；`id ego superego` → Lecture 3；`operant conditioning reinforcement` → Lecture 4。

**中文《数据结构》样例（Markdown + TXT，仓库内置）**

- 语言自动识别为 zh，CLI 全部中文输出。
- 12 题全部抽出（作业 5 + 模拟卷 7），10 题带答案与解析；选择题选项、判断题、填空题类型正确；“二、填空题”这类节标题不再混进上一题答案；章节归属 12/12 正确。

**EEC 160 / EEC 161 Applied Probability（用户提供的真实课程，27 个 PDF，约 1000 页，30 MB）**

结构：9 个章节讲义（landscape 幻灯片，每章 47–148 页，图全是矢量绘制）、9 份作业（第 1 页是教材题号清单，其后是学生自己手写作业的扫描件）、9 份官方解答（含小图）。这是 v4 时代最难处理的布局。

- `setup` 9.3 s（pypdfium2 抽文本 + 裁图）；9/9 章节标题正确（如 “Experiments, Models, and Probabilities”，标题跨行也能拼回）。
- 题目：89 题全部按教材题号（`Problem 1.3.10`）与解答文件（`hw1solution.pdf`、`homework2solutions.pdf`、`hw5-solutions(1).pdf` 等命名不一致）自动配对，89/89 有参考答案；章节归属 89/89 按题号首段落到对应章（无一靠猜）。
- 扫描/手写页 140 页全部识别并跳过：学生自己的作业永远不会被当成题面或答案。
- 配图：讲义 235 张矢量图区域裁成 PNG（含跨 Form XObject 的坐标变换）；解答里带图的 16 题裁出“答案图”（如 Venn 图、联合 PMF 散点图），`check` 时才给出。
- 题干本身不在材料里（只有教材题号）：`quiz` 明确提示，SKILL 要求从参考答案复述已知条件并标 🟡，而不是编题面。

### 3.3 工程指标

| | v4.3 | v5 |
|---|---|---|
| Python 行数（不含测试） | ~120k | 约 2,500（v5.1，含裁图） |
| 文件数（不含 .git） | 495 | 约 45 |
| 仓库体积（不含 .git） | 10 MB | 1.4 MB（其中 0.9 MB 是 README 配图） |
| 子命令 / 参数 | 36 / 108 | 17 / 32（含 v5.1 的 `figures`、`figure`） |
| 单元测试 | 1000+ 用例，12 min | 40+ 用例，≈1 s |
| 依赖 | 声明纯标准库，但 PDF 实际需要 pypdf 或 PyMuPDF，公式需要 latex2mathml，图片需要 Pillow | 标准库；PDF 文本与裁图可选 pypdfium2（文本也可用 pypdf） |

## 4. 复现

```bash
pip install pypdfium2
python samples/fetch.py                      # 下载 MIT 6.006 与 Yale PSYC 110
python coach.py setup samples/mit-6006 --days 2 --name 6.006
python coach.py next
python coach.py quiz && python coach.py check q003
python coach.py ask "counting sort radix sort running time"
python coach.py setup samples/yale-psyc110 --days 5
python coach.py ask "Descartes dualism argument"
python -m unittest discover -s tests -v
```

## 5. 已知限制

- 章节归属对跨章试卷是推测（已标注）；作业文件名里的编号不当作章节号。
- 题目抽取依赖编号（`Problem N`、`1.`、`第N题`）；没有编号的题目不会进题库，但仍在章节正文里。
- 扫描 PDF、图片没有 OCR：扫描页被识别并跳过，纯图片文件按章列出，由智能体用自身视觉能力读。
- 没有内置中英术语表；英文资料配中文提问时，SKILL 要求模型先把关键词翻成资料语言再 `ask`。
