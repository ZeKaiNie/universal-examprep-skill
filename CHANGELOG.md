# Changelog / 版本沿革

> 运行时技能文本（`SKILL.md` / `AGENTS.md` / `skills/**` / `prompts/` / `docs/`）直接描述当前行为、**不再提版本号**；版本历史集中记录在本文件，便于追溯。

## 未发布（教学层 A6）

- **模式系统重做：3 学习模式 × 4 时间宽裕度**（替换旧 normal/sprint/panic/mock）。学习模式：零基础从头讲 / 某章起步补弱 / 查缺补漏（首次对话须问清，存 `study_state.json.mode`）；时间宽裕度：≤1天 / 1-3天 / 3-7天 / >7天（存 `time_budget`，叠加在模式上，决定提问节奏）。
- **≤1天严禁提问**（任何问题都在浪费复习时间）；1-3天随机回问困惑点；3-7天知识点窗口系统（窗口内默认还会、窗口外先问是否记得）；>7天窗口外用对应难题实测。
- **知识点窗口持久化**：`study_state.json.knowledge_window`，经 `update_progress.py window-add` / `window-set-status`（在窗口/窗口外/已实测），进度面板新增「🪟 知识点窗口」区。
- **旧四模式迁移废弃**：`set --mode` 遇 normal/sprint/panic/mock 自动迁移并警告（panic→零基础从头讲＋≤1天、sprint→查缺补漏＋1-3天、normal/mock→查缺补漏）；未知模式/时间档保留原值并警告，绝不静默改写。
- behavior_smoke 新增 `time_budget_no_questions`（≤1天不提问）与 `knowledge_window_recheck`（窗口外知识点被回问/被出题）；T4 drift 新增模式漂移场景。

## 未发布（教学层 A5）

- **七步讲解模板**：重点题精讲固定顺序 ① 题面图 → ② 这题在问什么 → ③ 图里要读的量 → ④ 核心公式 → ⑤ 逐步演算 → ⑥ 答案自检 → ⑦ 知识点溯源（章节 + wiki + 可点击原文页链接），含文科变体（关键句/核心概念/逐点展开论证）；旧版【考点拆解】/【标准答题模板/步骤】并入 ②/④⑤。**默认输出到来源块为止**：【易错点】/【3 分钟速记】/【现在轮到你】不再是必要阶段，仅学生主动要求或存有偏好（`--pref 收尾块=…`）时输出。
- **每题固定来源块**：`题目来源：…｜答案来源：…｜<🟢/🟡/⚠️>`（讲解与判分反馈均适用）；无教材答案时 ⑤/解析块标题必须带 ⚠️ AI生成答案，非老师/教材提供。
- **讲解模板偏好**：变体选择存 `study_state.json` 的 preferences（`update_progress.py set --pref 讲解模板=…`，与模式分离），进度面板 ⚙️ 偏好区显示。
- 行为冒烟新增 `teaching_template` 场景：抓「跳过②直接贴公式」「公式先行乱序」「来源块缺失/无标签」「AI 答案 ⚠️ 缺标（来源行或标题）」「未经要求擅自附加收尾块」。

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
- **confusion-tracker 并入 `skills/`**（`skills/confusion-tracker/SKILL.md`）：疑难点追踪不再是 `skills/` 之外的外部依赖；随后清理删除了根目录遗留的 `confusion-tracker/` 兼容文件夹（迁移说明只保留在本 CHANGELOG，不再保留持久根文件夹）。

## V2.0

- **LLM Wiki 目录结构化 + 惰性加载**：按章节物理切片（`references/wiki/`），按进度只读当前章节，Token 消耗大幅下降。
- **一键零摩擦冷启动 ingest**：学生只给大纲/真题，AI 后台解析、拼 JSON、切片、初始化进度，**无需手写 JSON**。
- **无 Python 环境自动降级**：脚本不可用时无感切换为「手动写盘模式」，由 AI 直接铺设工作区。
- **标准题库 `quiz_bank.json` 抽题**：测验只从题库出题判分，杜绝 AI 即兴编题。
- **测试逃生通道**：查看提示 / 连续答错 2 次跳过并归档错题。
- **概念疑难点追踪**（`confusion-tracker`）：自动捕获「为什么 / 怎么推导」类追问，形成考前盲区清单。
- **运行安全与进度保护**：文件名安全过滤、路径防穿越/防篡改、进度覆盖前自动备份、强制 UTF-8 输出。
- **单元测试 + GitHub Actions CI**。
