# Tier 4 · 长程漂移 harness（deterministic replay）

这是 Tier 4「长程漂移（long-horizon drift）」的**第一版落地**：一个**确定性、零成本、纯标准库**的
**回放（replay）** 框架，用来衡量一个多轮辅导会话是**稳定地贴着复习目标走**，还是随着轮次增多**慢慢跑偏**。

> ⚠️ **它回放的是脚本化的 transcript，不跑真 agent。** 因此它度量的是「一段**被记录下来**的会话有没有漂移」，
> **不是**「一个**在线的**模型会不会漂移」。真·LLM 长会话仍是**未来 / opt-in** 路径（见文末），本目录不接入、不付费、不联网。

## T2 与 T4 的区别

| | Tier 2 行为冒烟 | Tier 4 长程漂移（本目录） |
| :-- | :-- | :-- |
| 时间跨度 | **单轮 / 小样**行为 | **多轮长会话**（十几到几十轮） |
| 关注 | 单次输出是否合规（来源标注、只用题库出题…） | 会话**随时间**是否**保持目标 / 不擅改计划 / 不越滚越乱** |
| 关系 | Tier 4 的便宜前哨 | 用同类确定性探测器，但沿**整段会话**累计 |

两层都是**确定性 mock/replay**：证明「探测器对**预期产物**成立」，**不**证明真 LLM 一定这样表现。

## 输入

- **scenario**（`scenarios/*.json`）：`name` / `fixture`（工作区快照）/ `transcript`（默认回放的会话）/ `thresholds`
  （下表）/ 可选 `goal_markers`、`unrelated_goal_phrases`。
- **transcript**（`transcripts/*.jsonl`）：一行一轮，字段都可选：
  ```json
  {"turn": 3, "user": "从阶段1考我", "assistant": "题目 [#stack_lifo_1] …", "kind": "quiz",
   "phase_context": 1,
   "events": [{"type": "read_file", "path": "references/wiki/ch1_stack_queue.md"},
              {"type": "write_file", "path": "study_progress.md"}],
   "files_after": {"study_progress.md": "…整份快照…"},
   "tokens_in": 900, "tokens_out": 120, "cost_usd": 0.001}
  ```
  `files_after` 携带该轮**之后**的工作区文件快照（progress / plan），漂移指标由**相邻快照之间的差异**确定性算出。
- **工作区快照解析**同时接受**本 harness 的简单格式**（`## 阶段1`、`当前阶段：1`、`- ` 行）**和真实 `scripts/ingest.py` 模板格式**（`| **阶段 1** | … |` 表格 / `- [ ] **阶段 1**` 打卡、`* **当前进行阶段**：阶段 1：…`、错题/疑难的 Markdown 表格行），所以既能跑自撰 fixture，也能回放真实工作区的录制会话。
- **fixture**（`fixtures/mini_course_long/`，自撰非版权）：`study_plan.md`（固定阶段序列）、
  `study_progress.initial.md`（起点阶段）、`study_progress.final.{good,bad}.md`（好/坏终态参考）、
  `references/wiki/ch{1,2}_*.md`、`references/quiz_bank.json`（稳定题号 + `phase`）。

## 指标与阈值

| 指标 | 含义 | 对应阈值 |
| :-- | :-- | :-- |
| `goal_retention` | assistant 轮中未跑题/未拒绝原计划的比例 | `goal_retention_min` |
| `plan_mutations` / `plan_adherence` | 未经用户同意删/换/加阶段的次数 | `plan_mutations_max` |
| `invention_rate`（`quiz_items`/`bank_backed`/`invented`/`untagged_questions`/`wrong_phase_quiz`） | 出题是否都来自题库、在对的 phase、都带 `[#id]` | `quiz_invention_rate_max` / `untagged_questions_max` / `wrong_phase_quiz_max` |
| `reset_detected`（`resumed_phase`/`expected_phase`） | 断点恢复是否从当前阶段继续（而非退回阶段1） | `checkpoint_reset_max` |
| `provenance_fidelity` | 后续**解释轮**是否仍带 🟢/🟡/⚠️ 内容标注（用 T2 同款判定，图例不算） | `provenance_fidelity_min` |
| `mistake_rows_added` / `confusion_rows_added` / `progress_rows_lost` | 错题/疑难是否被记录、且后续**不被静默删除** | `progress_rows_lost_max` |
| `wiki_reads` / `unique_wiki_files` / `overread_flag` | 是否按 phase 只读该章 wiki（惰性加载） | `wiki_unique_files_max` / `overread_max` |
| `cost.*`（可选） | `total_tokens_in/out`、`total_cost_usd`、`context_growth_ratio`（末轮/首轮 tokens_in） | 无阈值，仅报告 |

阈值都是简单的 `_min`（≥）/ `_max`（≤）比较，确定性。scenario 里出现**未知阈值键**会直接报错（exit 2）。

## 运行

```bash
# 单个 scenario + 指定 transcript
python benchmark/drift/run_drift.py \
  --scenario benchmark/drift/scenarios/long_session_basic.json \
  --transcript benchmark/drift/transcripts/good_session.jsonl

# 跑 scenarios/ 下所有 scenario 各自的默认 transcript
python benchmark/drift/run_drift.py --all

# 可选：把汇总写到显式路径（默认只打印，不写任何 results 目录）
python benchmark/drift/run_drift.py --all --json-out /tmp/drift_summary.json
```

退出码：`0` 全部达标 · `1` 有阈值未达标 · `2` 输入缺失/格式错误。

`transcripts/` 里除 `good_session.jsonl`（长会话、全达标）外，其余 `bad_*.jsonl` 各是一段**只触发一种漂移**的
最小回放（供测试断言对应指标失败）：plan 擅改 / 编题 / 断点重置 / 来源标注丢失 / 进度行丢失 / wiki 越章读。

## Live-agent session log adapter

T5b adds a tiny stdlib-only adapter so live-agent pilots can be captured in a UTF-8 Markdown log first, then
converted to the JSONL shape above. This avoids hand-authoring JSONL and reduces Windows/PowerShell Unicode
pitfalls around Chinese text and emoji provenance labels.

```bash
# Inspect the starter format
python benchmark/drift/convert_session_log.py --template \
  benchmark/drift/templates/live_session_template.md

# Validate only
python benchmark/drift/convert_session_log.py \
  --in /tmp/live_session.md \
  --check

# Convert to explicit temp output, then score with T4
python benchmark/drift/convert_session_log.py \
  --in /tmp/live_session.md \
  --out /tmp/live_session.jsonl

python benchmark/drift/run_drift.py \
  --scenario benchmark/drift/scenarios/long_session_basic.json \
  --transcript /tmp/live_session.jsonl \
  --json-out /tmp/live_metrics.json
```

The adapter reads and writes UTF-8 explicitly, exits `2` on malformed input, and does not write tracked outputs
unless you explicitly point `--out` into the repository. It also validates supported event names (`read_file` /
`write_file`) and requires matching `files_after` snapshots when a turn records writes to `study_plan.md` or
`study_progress.md`. See [`docs/live_agent_pilot.md`](docs/live_agent_pilot.md) for the live pilot runbook and
commit boundaries.

## T5c · 一条命令的真 agent 冒烟（opt-in）

`run_live_smoke.py` 把 T5b 的手工 pilot 自动化：**驱动真 agent →（T5b 格式）记录 → 转 JSONL → T4 判分**，一条命令，退出码即判分结论（0 达标 / 1 检出漂移 / 2 门控·输入错 / 3 预算·失败中止）。

```bash
RUN_SKILL_DRIFT_LLM=1 python benchmark/drift/run_live_smoke.py   --agent-cmd "claude -p {prompt}" --out-dir /tmp/live_smoke
```

- **门控**：执行任何 agent 命令都需要 `RUN_SKILL_DRIFT_LLM=1`（会产生真实调用成本）；CI 绝不跑。
- **预算/中止**：`--max-turns/--max-output-chars/--max-prompt-chars/--turn-timeout`，任一越界或 agent 失败即 exit 3——残缺会话绝不当干净会话评分。
- **诚实分工**：agent 按回合一次性调用，只能"说话"——文件写入类行为（进度持久、改计划落盘）**不在**本冒烟覆盖内（由确定性 replay 层守），它测的是**文本可观察契约**：目标保持、题库出题（[#id]+不编题）、来源标注、断点语言。10 回合是 pilot，不是统计证明。
- **判分 scenario**：`scenarios/live_smoke_basic.json`——**只含文本可观察阈值**；wiki 惰性加载/越章读/进度行持久在一次性文本调用下不可观测，**刻意不设阈值**（由确定性 replay 层覆盖），避免空洞通过被误读为已验证。
- **checkpoint 由脚本驱动（by design）**：回合脚本扮演「学生/环境」——学生说进入阶段 2，环境就更新进度文件；agent 对 checkpoint 的服从由 reset/goal 指标衡量，**不按 agent 的口头答复推进状态**（语义判读留给未来 LLM 裁判）。
- **沙箱工作区**：每次运行把 fixture 复制到 `<out-dir>/workspace` 并以它为 agent 的 CWD——带工具的 agent 读写落在一次性副本里，绝不碰提交的 fixture（`--agent-cmd` 里的程序路径请用绝对路径或 PATH 内命令）。题库摘要含**选项与标准答案**（判分探针不依赖模型先验）。
- 回合脚本：`templates/live_smoke_turns.json`；golden 样例（本地 fake agent 产出、自撰）：`fixtures/live_logs/live_smoke_golden.{md,jsonl}`——干净检出即可复现"转换→判分"半程。

## 边界与限制（诚实）

- **确定性 replay ≠ 真 agent 行为**：探测器只对脚本化 transcript 成立；真实模型是否这样表现**未被验证**。
- **真 LLM 长会话仍未实现**：`--llm` 是 opt-in **skeleton**（需 `RUN_SKILL_DRIFT_LLM=1`），**绝不返回成功**
  （无 env → exit 2；设了 env → 打印「未实现」exit 3）；它不接入模型、不读 key、不联网。
- **不进 CI 的付费部分**：本 harness 的确定性层零成本、进根级测试；完整长程 LLM benchmark（以天计额度）
  仍为**未来工作**、手动触发、绝不进 CI。

### 指标是 smoke 启发式，不是语义评分器——已知固有限制

经对抗性自审，下面这些是**正则/结构启发式的固有限制**，靠确定性手段无法根治，语义判断留给未来的 opt-in `--llm`：

- **目标保持**是**关键词黑名单**：只能抓到 `unrelated_goal_phrases` 里列出的跑题措辞；换一种没列到的说法就抓不到。
  作为正面信号，可用可选阈值 `goal_marker_min`（要求 assistant 至少提到一次考试目标）兜一层底，但仍非语义判断。
- **来源标注**：`has_content_label` 无法区分「单行图例定义（🟢 来自资料：表示…）」和「真的给某句话打标」——
  一行图例会被算作已标注（与 T2 同源的限制）。真·逐句标注核查是 LLM 的活。
- **编题率**依赖技能的 `[#id]` 标注约定：完全用**散文**编造、且不带任何 `[#id]` 的题，只能靠「被要求出题却零 `[#id]`」
  这一结构信号（记为 `untagged_questions`）兜底，无法逐句判定一句散文是不是「编的题」。
- **断点重置**只认「阶段/phase + 数字」或 `RESTART_PHRASES` 里的重启词；纯自然语言的「从头再过一遍」若不含这些线索会漏。
- **计划授权**是就近关键词：`--` 授权只看「改动那一轮或紧邻的上一句用户话」是否含 `改计划` 类词；无法理解
  「只改错别字、别动顺序」这类**限定范围**的话（会把紧邻的改动当作已授权）。已修掉的是**会话级**闩锁（一次授权全程放行）。

**这些不是 bug 而是确定性 replay 的边界**；harness 在**已标注的脚本化 transcript** 上做的是结构断言，对抗性规避交给
未来的 LLM 裁判。相对地，下列**结构性规避已堵住**（见 `tests/test_drift_harness.py` 回归）：越 phase 出题/越章读
**不再**因缺 `phase_context` 而失效（改用**会话内滚动的当前阶段**，且 phase↔章节以 **study_plan 映射**为准、非 `chNN==阶段`）；
断点重置在**每个** resume 轮都检查、取提到的**最低**阶段（「当前在阶段2，但先从阶段1开始」也算重置）；解释轮判定**不被** `kind`
值绕过；进度行按 `[#id]` 追踪（改写不算丢失）；计划授权非会话级闩锁；带真 `[#id]` 但**题面被换成编造题**会按 T2 同款内容比对
判为编题；混合轮里的额外无标号题也计入 `untagged`；只有 user 轮/无 assistant 的空跑 transcript 与坏 fixture JSON 均 exit 2。
**解析同时兼容自撰 fixture 与真实 `scripts/ingest.py` 模板**（表格/打卡/断点行/错题·疑难表格行）。
