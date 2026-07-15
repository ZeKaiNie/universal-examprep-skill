# exam-help — zh 学生侧文案包

> 这里只放文案；行为见 [skills/exam-help/SKILL.md](../../../skills/exam-help/SKILL.md)。

## Student-facing Output

### 四步地图

1. `exam-ingest`：建立并校验来源、知识库、题库和状态；先处理阻断项。
2. `exam-tutor`：每次只讲一章并持久化；结构化阶段完成前验证/导入完整强类型教材。
3. `exam-quiz`：只从题库选题判分。
4. `exam-review`＋`exam-cheatsheet`：复盘错题疑难；获授权才渲染打印版。

### 选择与文件

- 模式：零基础从头讲、某章起步补弱、查缺补漏；时间：≤1天、1-3天、3-7天、>7天。只有明确不要提问才停止互动并把完成上限设为 `covered_unverified`。
- `artifact_mode=chat` 是安全默认：保留对话、状态、笔记，**不自动生成章节 `HTML/PDF`**。显式 `visual` 才请求强类型教材→渲染→回执→全页验收；一次性请求不改持久偏好。不猜订阅、不静默安装。
- `.ingest/` 是建库/审查事实；`study_state.json` 是进度事实，`study_progress.md` 是生成视图；`references/wiki/chN_*.md` 是章节源，`references/quiz_bank.json` 是唯一测验源；`notebook/` 存教学，`study_guide/` 存门禁后的派生物。
- 建库退出 `0` 表示 `ready` 或已明确警告的 `usable_with_gaps`，`10` 表示内容阻断，其他非零是操作失败。

### 题型与来源卡

题型：`choice`、`subjective`、`diagram`、`fill_blank`、`true_false`、`code`。

- 🟢 来自资料
- 🟡 AI补充，可能与你老师讲的不完全一致
- ⚠️ AI生成答案，非老师/教材提供

不得自编关卡或把 AI 内容伪装成课程证据。详见[语言规范](../../../docs/language-policy.md)。
