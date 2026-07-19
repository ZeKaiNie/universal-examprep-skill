# exam-help — zh 学生侧文案包

> 这里只放文案；行为见 [skills/exam-help/SKILL.md](../../../skills/exam-help/SKILL.md)。

## Student-facing Output

### 启动时先选材料处理方式

- `lightweight`（轻量按需，推荐且默认）：只处理当前阶段的 PDF 页或一张可确定为单帧的 `PNG/JPEG/BMP` 图片，一个活动批次最多 8 个主页面；合图每组最多 4 页、每个小图块至少约 768 像素且只用于概览。外部答案页用 `register-answer-dependency` 精确绑定，依赖整页只能定位/细看，解答调用只接收带来源限定的目标答案裁图。全部规范视觉证据都是 `.lightweight/assets/` 下的 PNG，题面/答案裁图彼此独立。未完成批次只能用带原因回执的 `abandon` 关闭；已讲完的批次不能用该命令放弃，需重做时用 `replace-taught --reason`，把旧尝试和事件保留为已取代历史，并为同一切片规划后继尝试。继续记录学习状态，但不全量建库、不生成复习讲义/PDF，也不会为了省输入令牌而缩短讲解。
- `full`（完整建库）：显式选择后才运行完整资料导入、审查和知识库流程。
- `artifact_mode=chat|visual` 与材料处理强度分开；完整建库不等于自动生成 PDF。轻量模式下已保存的 `visual` 会休眠，当前有效输出仍为 `chat`，也不生成复习讲义。
- `answer_explanation_mode=ordinary|isolated` 也独立。默认 `ordinary` 仍逐题给出详细、零基础友好的答案解释，但不调用第二家模型服务商，也不声称上下文隔离。`isolated` 是默认关闭且仅用于第二代完整建库的延展功能，分两次同意：先说明模型服务商/API 独立计费和保留/隐私边界，只授权不上传的规划；再对准确规划方案的逐题/图片范围、调用数和按当前价格给出的估算作最终上传同意。`GPT` 模型、订阅、API 密钥、`full` 或 `visual` 都不会自动启用它。
- `preferences.interaction_style=batch|step_by_step` 是可选的 `full` 教学节奏，不是另一项开场必答题。已存逐题偏好只有在 `full` 且 `no_questions=false` 时有效；否则保留但休眠、有效节奏为 `batch`。“继续”只做导航，逐题完成必须有带标记的精讲笔记及原子 `record-taught-example` 绑定记录。旧的无绑定记录 ID 仍是合法的 `batch` 历史，已有绑定记录在切换节奏后仍须实时校验。
- `MinerU`、`Docling`、`LangGraph` 只有用户点名后才能提议，而且只能使用已配置并说明上传/隐私边界的远程/云端服务；绝不在学生本机下载、安装或运行。

### 四步地图

1. 默认 `lightweight_session` 只打开当前页批次；显式选择 `full` 才由 `exam-ingest` 建立并校验知识库/题库、处理阻断项。
2. `exam-tutor`：每次只讲当前页/一章并持久化；只有 `full` 阶段完成前才验证/导入完整强类型教材。
3. `exam-quiz`：只从题库选题判分。
4. `exam-review`＋`exam-cheatsheet`：复盘错题疑难；获授权才渲染打印版。

### 选择与文件

- 模式：零基础从头讲、某章起步补弱、查缺补漏；时间：≤1天、1-3天、3-7天、>7天。只有明确不要提问才停止互动并把完成上限设为 `covered_unverified`。
- `artifact_mode=chat` 是安全默认：保留对话、状态、笔记，**不自动生成章节 `HTML/PDF`**。只有显式选择 `full` + `visual` 才请求强类型教材→渲染→回执→全页验收；一次性请求不改持久偏好，也不能绕过 `full` 门禁。不猜订阅、不静默安装。
- `.ingest/` 是建库/审查事实；`study_state.json` 是进度事实，`study_progress.md` 是生成视图；`references/wiki/chN_*.md` 是章节源，`references/quiz_bank.json` 是唯一测验源；`notebook/` 存教学，`study_guide/` 存门禁后的派生物。
- 建库退出 `0` 表示 `ready` 或已明确警告的 `usable_with_gaps`，`10` 表示内容阻断，其他非零是操作失败。
- 轻量阶段只要求当前阶段所有未被取代的批次都已讲完并绑定笔记/进度，可到 `covered_unverified`；`verified` 要求轻量初始化前已存在且未漂移的题库中至少两个绑定资料版本的检查点、至少一题通过。启动只固化不可变的文件状态基线；后来新增/漂移的题库和旧式未绑定检查点不能计入。日常健康检查只查元数据/物理身份；精确散列值只在转换/完成或显式 `status --verify-live` 时计算，历史已讲完记录计为 `unchecked_historical`。

### 题型与来源卡

题型：`choice`、`subjective`、`diagram`、`fill_blank`、`true_false`、`code`。

- 🟢 来自资料
- 🟡 AI补充，可能与你老师讲的不完全一致
- ⚠️ AI生成答案，非老师/教材提供

不得自编关卡或把 AI 内容伪装成课程证据。详见[语言规范](../../../docs/language-policy.md)。
