# 能力 × 测试层覆盖矩阵 (Feature × Tier Coverage Matrix)

> 配套 [`testing-audit.md`](testing-audit.md)。一句话结论：**很多能力被「结构/指令」层静态测到，却没有被「行为」层执行验证。**

**图例**：✅ 覆盖　🟡 部分（结构/schema 层，**或** Tier 2 的确定性 mock 探测——非真 LLM 行为）　❌ 未覆盖　— 不适用

> **Tier 2 列说明**：🟡 = 本 PR（T2）的**确定性 mock 探测器**已覆盖该行为的产物断言（进 CI、零成本）；它**不等于**真 LLM 行为已验证——真 agent 冒烟是 opt-in、不进 CI（见 [`../behavior_smoke/`](../behavior_smoke/)）。
>
> **Tier 4 列说明**：🟡 = PR T4 的**确定性 replay harness** [`../drift/`](../drift/) 在**多轮长会话**维度上覆盖该行为（回放脚本化 transcript + 快照，进根级测试、零成本）；同样**不等于**真 LLM 长会话已验证——真 agent 长会话是 opt-in、未实现、不进 CI。

| 能力 | Tier 0 单元/静态 | Tier 1 工作区校验器 | Tier 2 行为冒烟 | Tier 3 完整 benchmark | Tier 4 长程漂移 | 当前缺口 | 可加的便宜指标 |
| :-- | :--: | :--: | :--: | :--: | :--: | :-- | :-- |
| 目标保持 (goal retention) | ❌ | — | ❌ | ❌ | 🟡（replay：跑题/拒绝原计划的比例） | 真 LLM 长会话未验（opt-in） | scripted 长会话里跑题短语计数 |
| 计划遵守 (plan adherence) | ❌ | 🟡（进度 current_phase ∈ plan） | ❌ | ❌ | 🟡（replay：未授权删/换/加阶段计数） | 真 LLM 长会话未验（opt-in） | study_plan 快照 diff |
| LLM Wiki 惰性加载 | ❌（仅指令文本） | — | 🟡（best-effort 占位 `count_wiki_reads`，不在 CI 断言） | 🟡（skill 臂用 wiki，但不验惰性） | 🟡（replay：wiki 越章读 / unique 文件数） | 真「只读相关一章」需真 LLM 才能验 | scripted transcript 里读文件调用数 = 1 章 |
| 一键 ingest 冷启动 | ✅（ingest 端到端单测） | 🟡（校验产物结构） | — | ❌ | — | 已较充分覆盖 | 维持现状 |
| 无 Python 降级写盘 | ❌ | — | 🟡（mock `no_python_fallback`：手写工作区过 Tier-1 校验） | ❌ | — | 真禁用 Python 的端到端仍未测 | 确定性：手写工作区 → `validate_workspace` 退 0 |
| quiz_bank-only 出题 | 🟡（题库 schema） | 🟡（schema） | 🟡（mock `quiz_bank_only`：题号∈题库 + 编造题号被抓） | ❌ | 🟡（replay：整段会话编题率 / 越 phase 出题） | 真 LLM 出题未验（opt-in） | scripted 出题 → 断言每题 id ∈ 题库 |
| 六种题型 | ✅（schema 接受 6 型） | ✅（schema 强制） | 🟡（fixture 覆盖全 6 型并过校验） | ❌（题集只有 factual/definition/numeric） | — | 真 LLM 按型出题/判分未验 | 固定题库上每型一题判分冒烟 |
| 画图题先跑算法再画 | ❌ | 🟡（`diagram_type` 告警） | ❌（fixture 有 render_hint，但无「先算后画」行为场景） | ❌ | — | 行为未测 | transcript 先跑算法再渲染 |
| 0 基础重点题精讲（→ A5 七步模板） | ❌ | — | 🟡（mock `zero_basic_key_question`：好例走完整 ①-⑦ + 来源块；旧两段式无 ①-⑦ 被判不合格） | ❌ | — | 真 LLM 精讲未验（opt-in） | scripted「重点题」请求 → 断言七步 + 来源块 |
| 七步讲解模板 + 每题来源块（A5） | 🟡（`teaching_template` 探测器单测：绑定圆圈序号 / 标题边界 / 逐题分段 / ⑦ 溯源） | — | 🟡（mock `teaching_template`：①-⑦ 齐全按序、②在④前、⑦落到 wiki/页、每题来源块 题目来源｜答案来源｜🟢/🟡/⚠️、AI 答案双 ⚠️、默认止于来源块、opt-in 收尾块；十余反例全被抓） | ❌ | ❌ | 真 LLM 讲题输出未验（opt-in） | scripted 讲题 → 断言七步 + 来源块 + ⚠️ |
| 视觉题 题面图门禁（P0-V1 / A1） | ✅（`show_question_assets`/索引脚本单测） | ✅（校验器拒「视觉必需但缺题面侧 asset」的工作区） | 🟡（mock `visual_first_assets`：题面图先行 + 11 反例——答案图先出/泄题/未标注/迟到/不安全路径/只打印路径 全被抓） | ❌ | ❌ | 真 LLM 渲染顺序未验（opt-in） | scripted 视觉题 → 断言题面图先于讲解/答案图 |
| 范围过滤 + 越界覆盖声明（A2） | 🟡（`select_questions` 按 source_type 选题单测） | 🟡（schema：source_type 标签） | 🟡（mock `scope_override`：越范围出题前 verbatim「⚠️ 临时覆盖…范围偏好」先于第一题；反例被抓） | ❌ | ❌ | 真 LLM 范围切换未验（opt-in） | scripted 越范围出题 → 断言覆盖声明先于题 |
| 学习模式 × 时间宽裕度（A6） | ✅（`update_progress` set/归一/旧四模式迁移/未知值保留 单测；紧迫别名归一） | 🟡（state schema：mode/time_budget） | 🟡（mock `time_budget_no_questions`：≤1天好例无学生问句、反例任何面向用户问句被抓） | ❌ | 🟡（replay `mode_urgent_no_questions`：≤1天档 `urgent_mode_questions`=0 + `urgent_mode_persisted`=1，紧迫开场须推断并落盘 零基础从头讲+≤1天） | 真 LLM 分档节奏未验（opt-in） | scripted ≤1天会话 → 断言零提问 + 落盘 |
| 知识点窗口（A6，3-7天/>7天） | ✅（`window-add`/`window-set-status` 单测：按章定位、多章歧义 fail-loud、幂等回填、init 往返无损） | 🟡（state schema：knowledge_window 对象数组） | 🟡（mock `knowledge_window_recheck`：窗口外须真复核，>7天须出题实测；否定式/默认收口不算复核——**仅判措辞，不落 state 行**） | ❌ | 🟡（B3 replay `window_persist`：长会话里窗口条目随讲解登记 `window_rows_added`≥2，进出用状态迁移不删行 `window_rows_lost`=0——静默丢窗口条目会红；坏行缺 point 时 fail-loud） | 真 LLM 窗口进出未验（opt-in） | scripted 窗口外点 → 断言回问或实测 |
| 结构化进度状态 study_state.json（A4） | ✅（`update_progress` 迁移/set/add/render/幂等 单测——含偏好/窗口落 state） | ✅（Tier-1 校验 state schema + 断点∈计划） | 🟡（mock **仅**断言 错题/疑难 写入 state 落行 `state_after`——**偏好/窗口的 state 落行由 Tier 0 单测覆盖，Tier 2 未断言**） | ❌ | 🟡（replay：state 唯一事实源、md 手改后写检测 `md_write_after_state`、双写一致——针对错题/疑难行） | 真 LLM 状态更新未验（opt-in） | scripted 学习事件 → 断言 state 落行、md 为生成视图 |
| 提示 / 跳过 / 错题归档 | ❌ | 🟡（进度模板含错题区） | 🟡（mock `hint_skip_mistake_archive`：逃生通道 + 错题行写入） | ❌ | 🟡（replay：错题行新增 + 跨轮不丢） | 真 LLM 逃生流未验（opt-in） | scripted 连错 2 次 → 断言三选项 + 错题行 |
| confusion-tracker 行为 | 🟡（子技能存在于 `skills/`） | 🟡（进度模板含疑难区） | 🟡（mock `confusion_tracking`：疑难表新增一行） | ❌ | 🟡（replay：疑难行新增 + 跨轮不丢） | 真 LLM 疑难捕获未验（opt-in） | scripted 概念疑问 → 断言疑难行 |
| study_progress 断点恢复 | 🟡（ingest rerun 不覆盖进度） | 🟡（current_phase ∈ plan） | 🟡（mock `checkpoint_recovery`：从进度读出当前阶段 2） | ❌ | 🟡（replay：断点重置检测 resumed vs expected） | 真 LLM 续跑未验（opt-in） | 预置进度@阶段 N → 新会话 → 断言从 N 续 |
| 来源标注 🟢/🟡/⚠️ | ✅（校验器 + 语言测试） | ✅（`ai_generated` 标记强制） | 🟡（mock `provenance_labels`：输出含全部 canonical 标注） | 🟡（越界弃答间接） | 🟡（replay：后续解释轮标注保真率） | 真 LLM 运行时输出未验（opt-in） | scripted「AI 补充」答 → 断言 🟡/⚠️ 出现 |
| AI 生成答案警告 | ✅（校验器拒未标记） | ✅ | 🟡（mock：⚠️ 标注 + fixture 含 ai_generated 项） | 🟡 | — | 真 LLM 运行时输出未验（opt-in） | 同上 |
| 中文学生可见输出 | ✅（语言 / 控制层测试） | — | ❌ | ❌ | — | 运行时语言未测 | scripted → 断言输出为简体中文 |
| 英文控制层 | ✅（control-plane 测试） | — | — | — | — | 已覆盖 | 维持现状 |
| 本地化边界 | ✅（localization 测试） | — | — | — | — | 已覆盖 | 维持现状 |
| web_prompt 兜底 | 🟡（中文优先 + 规则存在） | — | ❌ | ❌ | — | 行为流程未测 | 结构：web_prompt 含分步 + 来源标注 |
| 路径 / 进度安全 | ✅（校验器大量 + ingest） | ✅ | — | — | — | 已覆盖 | 维持现状 |
| 工作区校验器 | ✅（校验器单测） | ✅ | — | — | — | 已覆盖 | 维持现状 |

## 读这张表

- **左两列（Tier 0 / Tier 1）大量 ✅/🟡**：结构、schema、来源标注规则、路径安全、ingest 都被零成本测到。
- **Tier 2 现有确定性 mock 层（🟡）**：`scenarios.json` 共 **13 个行为场景**，其中 **12 个在确定性 `--mock` 里被断言、进 CI、零成本**，1 个（`lazy_load_best_effort`）标了 `best_effort:true`、确定性模式下报 `SKIP` 不断言（需 transcript/真 LLM 才能验）。12 个断言场景 = 原 7 个（quiz_bank-only / 来源标注 / 提示·跳过·错题归档 / 疑难追踪 / 断点恢复 / 无 Python 降级 / 0 基础精讲已升级为七步）+ A 线新增 5 个（`visual_first_assets` 题面图门禁 / `scope_override` 越界覆盖声明 / `teaching_template` 七步模板+来源块 / `time_budget_no_questions` ≤1天严禁提问 / `knowledge_window_recheck` 窗口外复核）。清单见 [`../behavior_smoke/README.md`](../behavior_smoke/README.md)。
- **Tier 4 新增确定性 replay 层（🟡）**：[`../drift/`](../drift/) 在**多轮长会话**维度覆盖目标保持 / 计划遵守 / 编题率 / 断点恢复 / 来源标注保真 / 进度持久 / wiki 越章读，以及 A6 新增的 `mode_urgent_no_questions`（≤1天档零提问 + 紧迫开场推断并落盘）与 B3 新增的 `window_persist`（知识点窗口长会话持久化：`window_rows_added`/`window_rows_lost`）；回放脚本化 transcript + 快照，进根级测试、零成本。
- **B3：Tier 4 的真 agent 路径（opt-in）**：`run_drift.py --llm` 不再是 skeleton——委托 [`../drift/run_live_smoke.py`](../drift/run_live_smoke.py) 驱动真 agent 长会话（token 上限、失败中止、fixture 沙箱）→ 录 T5b 日志 → 转 JSONL → 用**同一套** `compute_metrics`/阈值判分 → 记账。需 `RUN_SKILL_DRIFT_LLM=1`、会产生真实调用成本、**CI 绝不运行**。
- **B1 收尾扫描原则**：A 线每个已修缺陷都对应一个**会红的**确定性测试（Tier 0 单测或 Tier 2 mock 探测器的反例断言），本表逐条登记其落点；真 LLM 行为仍是 opt-in（未进 CI）。
- **但 🟡 ≠ ✅**：确定性层（Tier 2 mock、Tier 4 replay）只证明「探测器对预期产物/脚本化会话成立」，**不证明真 LLM agent 一定产出这些行为**——真行为覆盖需 opt-in 的 `--llm` 路径（默认关闭、未实现、不进 CI）。惰性加载与画图先算后画仍是 best-effort/未覆盖。
- **多数缺口正是用确定性结构断言补齐的**（对产物文件断言），无需 LLM 裁判。详见 [`testing-audit.md`](testing-audit.md) §9 与 [`../behavior_smoke/README.md`](../behavior_smoke/README.md)。
