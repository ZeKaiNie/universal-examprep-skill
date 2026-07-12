# v4 全面重构计划书 —— 从「备考技能」到「备考引擎」

> 定位：这不是 v3 的延续，而是按新项目标准做的一次整体重构。本文档是唯一的规划事实源：目标、架构、分阶段路线图、验收标准、风险全部在此。
> 依据：撰写前对现有仓库做了 5 路全面审计（scripts 国际化 / skill 文本面 / wiki-RAG / 输出面 / 分发体积），文中所有「现状」均有真实文件与行号支撑，不是推测。

---

## 0. 六个目标（本次重构的宗旨）

| # | 目标 | 一句话 |
|---|---|---|
| G1 | **语言彻底分层** | 学生首次对话三选一（中文 / English / 双语），此后所有 skill 文本与脚本输出只走所选语言，中英文本物理分离到不同文件夹 |
| G2 | **wiki 知识库 RAG 化** | 从「按文件名整章猛读」升级为「带索引、带元数据、带打分、带弃答门限」的轻量检索 |
| G3 | **回答落盘（笔记本化）** | 讲解/判分不再「聊完即散」，全部写进带目录、按章组织的 md 笔记本，随时可回目录跳转 |
| G4 | **错题本目录化** | 错题从「状态行」升级为「按章成文的完整错题笔记」，与目录互链 |
| G5 | **cheatsheet 编译器化 + PDF 成品** | 考前小抄不再凭空生成，而是对笔记本+错题本+知识点窗口的确定性「编译」，最终产出**用户指定页数的打印级 PDF** |
| G6 | **分发瘦身** | 学生装的是 ~35 个文件的运行时包，而不是 326 个文件、3.4 MB 的完整开发仓库 |
| G7 | **工作区落点可控 + 首次引导** | 工作区只建在用户明确确认的路径并持久记住；技能激活后主动问材料文件夹在哪 / 教学生怎么用 |

**非目标**（明确不做，防止发散）：核心链路不引入任何第三方依赖（嵌入模型等重后端只做可选插件）；不抛弃 Codex/Cursor/网页端兼容（AGENTS.md 与 web prompt 路线保留）；不破坏旧工作区（一律自动迁移+备份）；benchmark 不重写（作为 v4 的回归门禁复用）。

---

## 1. 现状体检（审计结论摘要）

### 1.1 语言混杂的真实结构

审计了全部 12 个脚本（5,940 行）与 14 个 skill 文本面（1,133 行）后，混杂不是均匀分布的，而是分三类：

- **A 类：控制台消息**（~440 条用户可见字符串；估算口径 = 漏斗调用点约 180 处 × 多行/多串展开，精确清单以 P1 产出的 msgid 目录为准）——全部走各脚本的 `_die()/warn()/err()` 漏斗，**可以干净抽离**；
- **B 类：持久化 schema 值**——中文字符串本身就是数据契约：`study_state.json` 里的 `mode="零基础从头讲"`、窗口状态 `在窗口/窗口外/已实测`、错题行状态 `待复盘`，以及生成的 `study_plan.md` 里被 3 个脚本回读解析的 `阶段 N` 表格。**这类不能「翻译」，只能「换成语言中性代号 + 迁移」**；
- **C 类：输入识别词表**——文件名/内容分类的中英正则（如判断题的 `真/假/对/错`）。**这类必须永远中英同时在，本地化它反而是 bug**。

好消息（审计实证）：所有 JSON key 已是英文、所有生成文件名已是 ASCII、机器可读警告已用 `no_materials:` 这类 ASCII 前缀——**中文只存在于「值」里**，分层的地基已经天然存在。

坏消息：枚举词表在 `update_progress.py`、`select_hard_questions.py`、`show_question_assets.py` 三处各自硬编码（无共享常量模块），已经开始漂移；测试里约 **1,035 条断言钉死了中文消息原文**；`docs/localization.md` + `tests/test_localization_boundary.py` 目前锁定的政策恰恰是「暂缓 locales/ 拆分」——v4 第一步就是正式改写这条政策。

### 1.2 wiki 作为 RAG 的 8 个缺口

1. **切片粒度 = 整章**。实测 PSYC 工作区每章 2.2 万–5.6 万字符；更糟的是章内是**单行无结构文本**，开头还带着爬虫残留的 `p { font-size: 14px; }`——连「读相关小节」的退路都没有。
2. **没有任何检索索引**。选章 = 文件名 (`ch01.md`…`ch20.md`，零语义) + 阶段指针 + 智能体自由发挥。
3. **没有块级元数据**。页码溯源只在题库条目上有，wiki 段落答不出「这句话来自哪页」。
4. **没有跨语言桥**。材料是英文、学生问中文（「旁观者效应」→ "bystander effect"），全靠模型隐式知识，不可测不可控。
5. **没有打分与二跳**。当前章没答案时没有任何受认可的第二跳（SKILL.md 反而禁止离开当前章）；仓库里唯一带 `min_score` 弃答门限的检索契约躺在未接线的 `spike/llamaindex_rag/` 里。
6. **ingest 零噪声处理**。逐字写盘，CSS 残留/口头语/重复标题全部入库。
7. **没有增量重建**。加一份材料 = 全量覆写，wiki 里已有的 🟢/🟡 段落标注会被静默销毁。
8. **没有检索评测钩子**。benchmark 只记最终答案，智能体到底打开了哪章从未被记录——而金标本来就带 `source_file` 和逐字 `supporting_span`，检索命中率其实**现在就能测**。

### 1.3 输出面：一切答案都在蒸发

逐项盘点后的结论：**学生复习时最想回看的东西，全都只活在聊天里**——七步精讲、判分反馈、提示、复盘清单，全部 chat-only；唯一落盘的「含答案 md」是最后一刻才生成的 `walkthrough.md`。错题在 `study_state.json` 里只剩一行 `{"id","chapter","note","status"}`，题面、学生的错答、正确讲解都没了。

### 1.4 分发：83.6% 是死重

git 追踪 326 个文件 / 3.4 MB，其中运行时真正需要的只有 **35 个文件 / 564 KB（16.4%）**。占比前三的全是学生用不到的：benchmark/ 30.2%、吉祥物 PNG 27.4%、tests/ 24.0%。v3.0 release 没挂构建产物，自动源码 zip 约 1.8 MB；同压缩率的运行时包只要 **223 KB（小 8 倍）**。

---

## 2. v4 目标架构

### 2.1 新目录树（提案）

```
universal-examprep-skill/
├── SKILL.md                      # 唯一触发入口：轻薄路由器（语言中性，见 2.2）
├── AGENTS.md                     # 通用智能体兜底契约（保留，改指向新结构）
├── skills/                       # 9 个子技能的「控制层」：纯英文逻辑，语言中性
│   └── exam-tutor/
│       ├── SKILL.md              # 触发 frontmatter + Workflow/Boundaries（不含任何学生可见文案）
│       └── ...
├── locales/                      # ★ 新：语言包，中英彻底分文件夹
│   ├── zh/
│   │   ├── skills/exam-tutor.md  # 该技能全部学生可见文案：七步模板、来源块、收尾块……
│   │   ├── messages.json         # 全部脚本消息（msgid → 中文）
│   │   └── templates/            # 中文版 md 模板（笔记本/进度/小抄骨架）
│   └── en/                       # 同构的英文包（en 版技能文案 / messages.json / templates）
├── scripts/                      # 语言中性引擎（目录名保留——改名 core/ 是纯装饰，会碰 34 处
│   │                             #   技能引用 + 数百处测试路径，评审噪声巨大；「core」只是逻辑称谓）
│   ├── i18n.py                   # ★ 共享常量 + 语言包加载（canonical 代号 ↔ 显示文案）
│   ├── ingest.py                 # v2：切片/清洗/元数据/索引/术语表/增量
│   ├── retrieve.py               # ★ BM25 检索 + top-k 打分 + min_score 弃答门限（纯标准库）
│   ├── notebook.py               # ★ 笔记本引擎：追加条目/重建目录/锚点/回链
│   ├── cheatsheet_render.py      # ★ 打印 HTML + 无头浏览器 PDF + 页数拟合
│   ├── update_progress.py        # 状态机（枚举全部换 canonical 代号 + workspaces 注册表）
│   └── ...（validate/select/visual 系列保留）
├── prompts/ docs/                # 保留（prompts 双语两份 + 结构对齐测试；templates 迁入 locales，见 3.3）
└── ── 开发区（不进分发包）──
    benchmark/  tests/  spike/  assets/  .github/
```

**工作区**（学生侧，ingest 生成）新增：

```
<workspace>/
├── references/
│   ├── wiki/chNN.md              # 章文件仍逐字整章写盘（执行期偏离：小节块做成索引内的
│   │                             #   「逻辑块」而非物理拆文件——现有全部契约零破坏；检索直接
│   │                             #   返回块文本，物理拆分列入 backlog 再评估）
│   ├── wiki_meta.json            # ★ 每章元数据：内容哈希/块数/章号（增量重建的地基）
│   ├── retrieval_index.json      # ★ BM25 倒排索引（小节级逻辑块，ingest 时构建）
│   ├── terms.json                # ★ 本课程中英术语对照（跨语言检索桥）
│   └── quiz_bank.json            # 保留
├── notebook/                     # ★ 笔记本（G3）
│   ├── index.md                  #   总目录：章节 → 每条讲解/判分的锚点链接
│   └── ch02.md                   #   本章全部落盘回答（七步精讲/判分反馈，带锚点）
├── mistakes/                     # ★ 错题本（G4）
│   ├── index.md                  #   错题目录：按章分组 + 状态一览
│   └── ch02.md                   #   完整错题笔记：题面/错答/病因/正解/来源
├── cheatsheet.md                 # ★ G5 编译产物（替代 walkthrough.md）
└── study_state.json              # 保留（枚举迁移为 canonical 代号）
```

### 2.2 语言分层的关键设计决策

**skill 文本：按文件夹彻底分开（采纳）。** 每个子技能拆成「控制层」（`skills/<name>/SKILL.md`，纯英文逻辑，学生永远看不到）+「语言包」（`locales/zh|en/skills/<name>.md`，学生可见的一切文案）。首次对话合并首问（模式×时间×语言，沿用现有设计）确定语言并持久化后，**控制层只加载对应语言包**；双语 = zh 包 + `> EN:` 镜像组合规则（沿用现有 T5 组合锁，不做第三套文案）。

两个边界明确写死：① **触发不受语言分层影响**——各 SKILL.md 的 frontmatter `description` 保持中英双语关键词（现状即如此，是触发面而非学生可见面，纯净 lint 本来就豁免 frontmatter），确保中文开场也能可靠触发；② **「固定」= 缺省绑定而非锁死**——`set --language` 中途切换保留（现有能力），切换后下一轮起改挂新语言包。

**scripts：逻辑一份 + 语言包分文件夹（修正原始设想，理由如下）。** 把 Python 逻辑复制成 zh/en 两份是维护陷阱——审计已实证枚举词表在 3 个脚本里各自硬编码并开始漂移，两棵代码树只会放大它。v4 方案：

- `scripts/i18n.py` 成为唯一词表源：canonical 代号（英文蛇形，如 `mode=from_scratch`、`window=in_window`、`status=to_review`）+ 按 `locales/<lang>/messages.json` 渲染显示文案；
- **学习循环脚本**（update_progress / validate_workspace / select_hard_questions / show_question_assets——学生复习期间反复运行、输出学生相邻）的用户可见消息走 msgid + 语言包；**建库侧一次性脚本**（ingest / build_raw_input / visual 系列——纯 agent 面，智能体读后以学生语言转述）的控制台消息保持 zh 并在 localization.md 记录豁免（执行期收窄：440 条全量 msgid 化的回报不抵回归风险，`no_materials:` ASCII 前缀模式保留为机器可读缝）；
- 脚本自动读 `study_state.json.language` 决定输出语言（保留 `--lang` 覆盖）；
- **持久化文件从此只存 canonical 代号**——中文不再是 schema。旧工作区在 `update_progress init/load` 时自动迁移（沿用 `_MODE_MIGRATION` 的先例：识别旧中文枚举→写入代号→备份原文件）。

这样「文件夹层面」中英依然彻底分开（`locales/zh/` vs `locales/en/`），但逻辑零复制。

**测试爆炸半径的控制（重要）**：约 1,035 条中文断言大多数断的是**中文消息原文**——只要 zh 语言包的文案与现文案逐字相同、且 zh 是无状态时的缺省渲染，这批断言**大部分自然存活**；真正必须重写的是枚举/schema 迁移相关（约 200–300 条）与 12 个路径钉死测试文件（skills-tree 审计已逐个列出名单）。`docs/localization.md` 与 `test_localization_boundary.py` 的「暂缓拆分」政策在 P0 正式废止并改写。

### 2.3 RAG 升级设计（对应 8 缺口）

| 缺口 | v4 方案 | 落点 |
|---|---|---|
| 1 粒度 | ingest v2 按 `##` 小节切片（目标 800–1,500 字符/块）；**无结构长文本重建是独立交付物 R-slice**（见表后） | `scripts/ingest.py` |
| 6 噪声 | 切片前清洗：剥 CSS/HTML 残留、去连续重复行、口头语过滤（保守白名单式，宁少勿滥删） | 同上 |
| 3 元数据 | 每章元数据入 `wiki_meta.json`（章号/块数/内容哈希）；小节标题+词窗摘要由检索索引直接服务（页级溯源打通入 backlog） | 同上 |
| 2 索引 | `retrieval_index.json`：纯标准库 BM25 倒排索引 + 每章每节一句摘要构成的 TOC；智能体先查索引、只读命中小节 | ★ `scripts/retrieve.py` |
| 5 打分/弃答 | 采纳 spike 已定义的契约：`retrieve(q) → [Chunk{text,score,source}]`，top-k + `min_score` 门限，低于门限→「材料中未涵盖」先于任何生成 | 同上（spike 的 LlamaIndex 嵌入后端保留为可选插件，核心零依赖） |
| 4 跨语言 | ingest 时生成 `terms.json`（课程术语中英对照，由 AI 在建库阶段一次性产出+人可校对）；检索前做查询扩展 | `scripts/ingest.py` + `retrieve.py` |
| 7 增量 | 每章内容哈希入 `wiki_meta.json`；重跑 ingest 只重建变化章，保护已有段落标注 | `scripts/ingest.py` |
| 8 评测 | benchmark 增加检索指标：金标已带 `source_file`+逐字 span → **harness 补工具轨迹记录（P3 显式交付物，现 harness 只记最终答案）**，产出 recall@k 与「命中章」率；切片前后 A/B 用现有三臂直接跑 | `benchmark/`（复用，不重写） |

**R-slice（P3 最硬的一块，单独立项）**：批评审查指出「按 `##` 切片」在旗舰数据上一次都不会命中——PSYC 各章是**零标题、单行 2–5 万字符**的退化文本，所谓退路（段落/句群重建）才是主路径。设计：先做确定性清洗（剥 CSS/HTML 残留、还原换行），再按句群+滑窗聚类切块（纯标准库），块边界优先落在话题转折词/讲课口头语标点上；**独立验收**：PSYC 20 章全部切成 ≤2,000 字符的块、每块可定位回原转录偏移、金标逐字 span 100% 落在唯一块内（不跨块截断）。

**旧工作区兼容**：`retrieve.py` 检测不到 `retrieval_index.json` 时**优雅降级**为现行为（章文件直读），并提示可重跑 ingest 升级；不强制重建（原始材料可能已不在盘上）。

### 2.4 笔记本化输出（G3/G4/G5 一体设计）

**核心契约改动**：「**先落盘、再在聊天里给摘要+链接**」成为**全部学生可见技能的缺省 Output Contract**——不限于 tutor/quiz/review。豁免走白名单且必须显式声明：仅限「可从状态确定性再生」的内容（进度面板、exam-help 静态速查卡、escape-hatch 一次性提示）；此外的任何实质性回答（含随口的概念问答、confusion 解答、复盘结论）一律落盘。落盘链路如下：

1. 每次七步精讲/判分反馈 → `scripts/notebook.py add-entry --chapter 2 --type walkthrough|feedback --id q13`，内容写入 `notebook/ch02.md` 尾部（带锚点 `#q13`、来源块、时间戳），目录 `notebook/index.md` 确定性重建；
2. 聊天回复 = 3–5 行摘要 + `完整解答：notebook/ch02.md#q13 ｜ 目录：notebook/index.md`；
3. 错题触发 `add-mistake` 时**同步**写 `mistakes/chNN.md` 完整条目（题面/学生答案/病因/正解/来源块），`study_state.json` 错题行新增 `entry` 字段指向锚点——状态行与笔记互为索引。**旧错题迁移**：v3 状态行只剩 `{id,chapter,note,status}`（题面/错答已不可复原），迁移时生成「存根条目」——标注 `（旧版错题，无原始题面）`+ 按 id 回查题库补题面，学生下次复盘该题时由 exam-review 现场补全为完整条目；
4. **cheatsheet 编译器**（G5）：输入 = `mistakes/`（最高权重）+ 知识点窗口（窗口外优先）+ `notebook/` 高频章节 + wiki 必背点，输出 `cheatsheet.md`（自带目录，四段式版式沿用 v3；**取代并退役 `walkthrough.md`**，旧文件保留不删、技能文本指针更新）。从「生成」变「编译」——**可溯源性有机械检查**：validate_workspace 新增 lint「cheatsheet 每个要点须携带可解析的 `notebook/`、`mistakes/` 或 wiki 锚点，坏锚即红」；
5. **网页端降级**：无文件系统的客户端（web prompt 路线）保持现有 chat-only + 文本断点模式，笔记本契约只对能写盘的智能体生效（控制层按能力分派，沿用 exam-cram 现有的 file-less fallback 判断）；
6. **小抄 PDF 成品**（G5 终态）：编译出的 `cheatsheet.md` 经 `scripts/cheatsheet_render.py` 渲染为**打印优化 HTML**（多栏紧凑排版、字号/行距可调、`@page` 边距 ≥12 mm——打印机会吞边），再经无头浏览器（探测本机 Edge/Chrome，`--headless --print-to-pdf`，零新增依赖）输出 PDF；**页数拟合循环**：先按「字符数/页」启发式估初值，产出后由智能体动用视觉能力核查——超页则降字号/并栏、尾页空白 >15% 则升字号回填，直到**恰好等于用户指定页数且尽可能拥挤**；无头浏览器不可用时降级为「打开 HTML → Ctrl+P」并给出打印参数指引。

### 2.5 工作区落点与首次引导（G7）

现状实证：exam-ingest 的工作区落点是 **"default: current workspace root"**——不问就落在当前目录，正是「模型在用户不知道的地方建了工作区」的根源。v4 契约：

1. **建区必确认**：任何工作区创建前，必须让用户明确确认目标路径（给出建议默认值，但**静默创建 = 违约**，进 behavior_smoke 红线场景）；
2. **持久注册表**：`~/.exam-cram/workspaces.json`（用户主目录，跨会话）记录 课程 → 工作区绝对路径 + 材料文件夹 + 最近使用时间；由 `scripts/update_progress.py` 新增 `workspace-register / workspace-list` 子命令维护；
3. **激活即引导**：技能被下载激活后、注册表为空时，走首次引导——先问「材料文件夹在哪」，没有材料则转入使用教学（30 秒版：放材料 → 建库 → 开始复习的三步演示）；注册表非空时问「继续哪门课」，直接挂载对应工作区；
4. **防迷路**：每次会话开场的进度面板带一行工作区绝对路径，学生永远知道自己的文件在哪。

### 2.6 分发（G6）

**依赖预检清单（执行期新增，用户反馈：装好后一跑才发现缺库）**：`scripts/check_deps.py` 是可选依赖的唯一清单+探测器（PDF 文本后端 / PyMuPDF 渲染 / 本机浏览器），exam-ingest Workflow 第 0 步强制预检——按材料实际内容判定「需要/暂不需要/可选降级」，缺了就给出精确安装命令并一句话征询学生同意后由智能体代装（绝不静默装、绝不等运行时炸）。清单只此一处，技能文本仅指向工具，防两处漂移。

三层递进，互不排斥：

1. **立即做（一次性小改）**：加 `.gitattributes` 对 benchmark/ tests/ spike/ assets/ .github/ 标 `export-ignore` → GitHub 自动源码 zip 直接瘦成运行时体积，零工具成本；
2. **v4 标配**：`scripts/build_dist.py`（纯标准库 zipfile）按显式清单打运行时包（~223 KB），CI 挂到每个 release；清单本身进测试——「新脚本忘了进清单」会红；README 安装节改为「下载 release 包解压到 `.claude/skills/`」为主、git clone 为开发者路径；
3. **可选增强**：`.claude-plugin/plugin.json` 插件化（现有 skills/ 目录结构与插件约定几乎完全吻合），Claude Code 用户获得原生安装/更新体验；脚本调用点的 `${CLAUDE_SKILL_DIR}` 需兼容 `${CLAUDE_PLUGIN_ROOT}`（运行时 md 内约 28 处：根三入口 7 + 子技能 21）。**不做**独立运行时仓库（分裂 star/issue，同步成本最高，与协作者身份不符）。

---

## 3. 现有资产处置表

### 3.1 九个子技能

| 子技能 | v4 处置 |
|---|---|
| exam-cram | 保留为总路由：合并首问（模式×时间×**语言**）→ 持久化 → 语言包分派 + 笔记本能力探测 |
| exam-ingest | 围绕 ingest v2 重写：切片/清洗/元数据/索引/术语表/增量 |
| exam-tutor | 教学逻辑保留（七步模板是 v3 核心资产），Output Contract 改为「落盘→摘要」 |
| exam-quiz | 六题型判分保留，反馈落盘 `notebook/`，错题同步写 `mistakes/` |
| exam-review | 改为读 `mistakes/` 目录复盘，复盘结论写回笔记本（不再 chat 蒸发） |
| exam-cheatsheet | 重写为编译器（2.4 第 4 条） |
| exam-audit | 扩展：加笔记本/索引/术语表一致性体检 |
| exam-help | 速查卡按语言包各生成一份 |
| confusion-tracker | 保留，疑难点条目同步落 `notebook/`（带锚点回链） |

### 3.2 十二个脚本

| 脚本 | v4 处置 |
|---|---|
| ingest.py | **重写**（v2，2.3 全表落点） |
| update_progress.py | **大改**：枚举 canonical 化 + 状态迁移 + msgid 化（改动最重，最先做） |
| build_raw_input_from_workspace.py | 保留（147 KB 的解析引擎是资产），清洗逻辑部分前移入它，消息 msgid 化 |
| validate_workspace.py | 扩展：校验 notebook/ 结构、索引与 wiki 一致性、canonical 枚举 |
| select_questions.py / select_hard_questions.py / score_difficulty.py | 保留，词表改引 `scripts/i18n.py`（消灭三处硬编码漂移） |
| build_knowledge_index.py | **并入** retrieval 索引后退役（quiz-tag 索引是新索引的子集） |
| build_visual_index.py / list_figure_pages.py / list_image_questions.py | 保留，消息 msgid 化 |
| show_question_assets.py | 保留；已有的 zh/en 内联双语改为走语言包（顺手消灭它私藏的语言映射副本） |
| （新增） | `scripts/i18n.py`、`scripts/retrieve.py`、`scripts/notebook.py`、`scripts/build_dist.py` |

### 3.3 其余资产（批评审查补位——此前无处置的都在这）

| 资产 | v4 处置 |
|---|---|
| SKILL.en.md（根英文渲染） | **退役并入 `locales/en/`**：根只留单一 SKILL.md 路由器；README/AGENTS/portability 里的文件名字面指针同步改（相关 discoverability 测试钉在 P2 一并重写） |
| prompts/web_prompt.md + .en.md | 保留两份（网页端无文件系统，必须自包含）；**补结构对齐测试**——两份的锚点/段落集合必须相等（对 scripts 反漂移论证同样适用于这对手工镜像文件） |
| templates/（3 个中文模板） | **迁入 `locales/<lang>/templates/`** 并补 en 版；根 templates/ 退役；笔记本/错题本/小抄的新模板直接在语言包里出生 |
| docs/file-format.md | **P1/P3/P4 各阶段随 schema 演进同步大改**（canonical 枚举、`_meta.json`、`retrieval_index.json`、notebook/mistakes 结构都要进文档），每阶段验收含「文档与 schema 一致」 |
| docs/language-policy.md + localization.md | P0 改写政策（废止「暂缓拆分」），P2 重写为语言包规范 |
| README / README.zh.md | P6 安装节重写（release 包为主路径）；其余随发布走 |

---

## 4. 路线图（7 阶段，每阶段独立可交付、可回滚）

| 阶段 | 内容 | 主要交付物 | 验收标准 |
|---|---|---|---|
| **P0 决策冻结**（1 PR） | 本计划书评审定稿；废止「暂缓 locales」政策（改写 docs/localization.md + test_localization_boundary）；`.gitattributes` export-ignore 先行落地 | 定稿的 PLAN-v4 + 政策改写 + 瘦身 zip 立即生效 | 全测试绿；源码 zip 体积 ≤ 700 KB |
| **P1 词汇与状态层**（2–3 PR） | `scripts/i18n.py` 共享词表；`study_state.json` 枚举 canonical 化 + 自动迁移；三处硬编码词表归一 | i18n 模块 + 迁移路径 + 迁移测试 | 旧工作区 init 后自动迁移且有备份；枚举只有一个定义点 |
| **P2 语言包分离**（3–4 PR） | `locales/zh|en/` 建立；~440 条脚本消息 msgid 化（zh 文案逐字搬家控制迁移成本）；9 个子技能拆控制层/语言包；路由改造；12 个路径钉死测试重写；纯净 lint 改指向语言包 | locales/ 全树 + msgid 清单 + 新 lint | **门禁**：双向纯净 lint 全绿（en 包零 CJK、zh 包零英文 prose）+ 全套件绿；「现有 1,035 条中文断言 ≥90% 无改动存活」为成本预算而非门禁——超支说明搬家不逐字，需回查 |
| **P3 RAG 升级**（3–4 PR） | ingest v2（切片/清洗/元数据/增量）+ **R-slice 无结构长文本重建（独立验收见 2.3）** → BM25 索引与 TOC → terms.json 跨语言桥 → 检索契约+弃答门限 + **benchmark harness 工具轨迹记录**（现 harness 只记最终答案，这是前置工程而非既有能力） | scripts/retrieve.py + 新工作区结构 + harness 轨迹记录 | recall@k 可测且有数；R-slice 独立验收达标；切片 A/B 显示 token 成本下降且正确率不降；越界弃答保持 100% |
| **P4 笔记本化 + 落点引导**（2–3 PR，**依赖 P2**：改写的 Output Contract 与笔记本模板都长在 P2 产出的控制层/语言包里，先拆后改，避免二次返工） | scripts/notebook.py；全技能缺省落盘契约（豁免白名单制）；错题本目录化 + 旧错题存根迁移；**工作区注册表 + 建区必确认 + 激活引导（G7 全量）**；behavior_smoke 加「落盘契约」「静默建区红线」场景 | notebook/ + mistakes/ 全链路 + workspaces 注册表 | 实质性回答 100% 落盘且目录可跳转（白名单外无豁免）；静默建区场景必红；web 降级路径不回归 |
| **P5 小抄编译器 + PDF**（1–2 PR） | cheatsheet 从生成改编译（错题优先 + 窗口外优先 + 锚点回链）；walkthrough.md 退役；**cheatsheet_render.py（打印 HTML + 无头浏览器 PDF + 页数拟合循环）** | 新 exam-cheatsheet + cheatsheet.md + PDF 渲染器 + 溯源 lint | 溯源 lint 全绿；指定 N 页 → PDF 恰 N 页、边距 ≥12 mm、尾页空白 ≤15%（视觉核查契约）；无浏览器环境降级路径可用 |
| **P6 分发与发布**（1–2 PR） | build_dist.py + 清单测试 + CI 挂 release 附件；README 安装重写；（可选）plugin.json | ~223 KB 运行时包 | 从 zip 安装的技能全功能可用；清单漂移有测试兜底 |
| **P7 回归与发版**（1 PR） | 全量 benchmark 在 v4 上重跑（三臂×三模型）；数字对比 v3 写入报告；发 v4.0 release | v4.0 发布通告（沿用 v3 双语格式） | grounding 指标不低于 v3 基线（**基线明确定义为 REPORT 所载矩阵数：PSYC+6.006 双课程、Sonnet 判分、人工 κ=0.833/0.875 校准**——材料专属 ≥ 现值、越界弃答 =100%）；检索 recall 作为新增亮点指标 |

依赖关系：P1 → P2 → P4（词表→语言包→落盘契约，**串行**：P4 改写的契约与模板长在 P2 的产出里）；P3 与 P2/P4 可并行；P5 依赖 P4；P6/P7 收尾。预计总量 **14–20 个 PR**，每个独立过 CI + Codex 评审。

---

## 5. 风险与对策

| 风险 | 等级 | 对策 |
|---|---|---|
| 测试爆炸半径（1,035 条中文断言 + 12 个路径钉死文件） | 高 | zh 文案逐字搬家保存活；路径测试名单已在审计中逐个列出，P2 一次性重写；每 PR 全量跑 CI |
| 状态迁移出错毁掉学生进度 | 高 | 沿用现有 O_EXCL 原子写 + 迁移前强制备份；迁移测试覆盖三代状态格式（v2 中文枚举 / 旧四模式 / v4 代号） |
| 智能体不遵守「落盘」新契约（写了聊天忘了写文件） | 中 | behavior_smoke 加确定性场景（无 notebook 条目即红）；T4 长程漂移加落盘漂移检测 |
| BM25 索引质量不如「让模型自己翻」 | 中 | P3 用现有 benchmark A/B 实测定夺——若 recall 不升，索引降级为「TOC 导航 + 小节直读」，切片与元数据仍保留（它们独立有价值） |
| 双语言包内容漂移（zh 改了忘改 en） | 中 | 结构对齐测试：两包 msgid 集合必须相等、技能文案锚点集合必须相等（缺失即红） |
| 范围蔓延 | 中 | 每阶段独立可交付；任何新想法先进本文档「backlog」节而不是直接开工 |

## 6. Backlog（本轮不做，记录在案）

- **study_progress.md 生成视图的按语言渲染**：视图是 agent 中介面（学生在聊天里看到的是按语言重述的进度面板），而它的 zh 结构被 parse_md 往返、validator、drift、T4 四条链共同解析——本轮改动回报小、回归面大；先保持 zh 视图 + 代号状态，等笔记本化（学生直读面）落定后再评估
- 嵌入式语义检索后端（spike 已备契约，等 BM25 实测数据说话）
- 笔记本导出 PDF / Anki 卡片
- 多课程并行工作区管理
- 社区语言包（第三语言：按 locales/ 结构天然可扩展）

---

*本计划书为 v4 重构唯一事实源；执行中的偏离须先改本文档。审计原始报告（5 份）在会话记录中留档。*
