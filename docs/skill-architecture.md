# Skill Architecture — 技能集合结构说明

这套技能按“语言中性入口 → 单一控制层 → 可替换文案层 → 结构化建库事实层 → 编译后的学习层”组织，目标是让不同宿主能找到入口，同时避免规则、语言和派生产物各自漂移。

## 1. 入口与事实源

- [`SKILL.md`](../SKILL.md) 是语言中性路由器：读取 `study_state.json.language` 的规范值，定位主技能和对应文案包。它不再承载完整流程。
- [`skills/exam-cram/SKILL.md`](../skills/exam-cram/SKILL.md) 是主编排器；各 `skills/*/SKILL.md` 各自拥有一个职责的行为契约。
- [`locales/zh/SKILL.md`](../locales/zh/SKILL.md) 与 [`locales/en/SKILL.md`](../locales/en/SKILL.md) 是轻量兼容索引，不是中文/英文两份行为事实源。
- [`AGENTS.md`](../AGENTS.md) 是给不读取完整技能的通用代理的浓缩安全底线；详细行为仍以相应子技能为准。

## 2. 技能集合

```text
skills/
  exam-cram/          # 恢复断点、模式与阶段编排
  exam-ingest/        # 材料建库、告警接管、工作区初始化
  exam-tutor/         # 当前章惰性授课、七步精讲
  exam-study-guide/   # profile=full typed manifest → HTML/PDF → receipt/全页 QA
  exam-quiz/          # 题库抽题与判分
  exam-review/        # 错题和疑难复盘
  exam-cheatsheet/    # 考前小抄
  exam-audit/         # 只读工作区体检
  exam-help/          # 速查卡
  confusion-tracker/  # 概念疑难追踪
```

其中概念疑难追踪的完整路径是 [`skills/confusion-tracker`](../skills/confusion-tracker/SKILL.md)，与其他子技能一样位于共享控制层，而不是语言包中。

每个子技能保持 Purpose / Activation / Inputs / Workflow / Output Contract / Boundaries 六段结构。跨技能只链接，不复制整段流程。

## 3. 生命周期路由

| 用户动作 | 主处理技能 | 只读的主要当前切片 |
| --- | --- | --- |
| 提供材料、首次建库 | `exam-ingest` | 原材料目录、`.ingest/` 来源清单与 typed review |
| 讲当前章 | `exam-tutor` | 一个 wiki 章节 + 当前章教学例题切片 |
| 做当前章题 | `exam-quiz` | 题库的当前章筛选结果 |
| 复盘错题/疑难 | `exam-review` | 状态中的未掌握项 + 对应题目 |
| 建立/生成章节教材 | `exam-study-guide` | 已验证的 `notebook/chNN.guide.json` + 其引用资产；receipt/QA 是视觉交付证据 |
| 冲刺小抄 | `exam-cheatsheet` | 错题、疑难、知识窗口与 wiki |
| 体检 | `exam-audit` | 工作区清单与一致性证据 |

## 4. 状态、建库与内容层

```text
<workspace>/.ingest/               # 建库/接管事实源；不得手改
  source_manifest.json             # 原材料版本、哈希与解析状态
  base_content_units.jsonl         # 确定性解析基线
  content_units.jsonl              # 基线 + 已应用 patch 的编译视图
  chapter_phase_mappings.jsonl     # 真实章节与学习阶段显式映射
  review_queue.jsonl               # typed AI/人工接管生命周期
  review_patches.jsonl             # append-only、可回放补丁 ledger
  build_manifest.json              # page accounting 与完整性哈希
study_state.json                 # 结构化进度唯一事实源
study_progress.md                # 由状态渲染的可读视图
references/wiki/chNN_*.md        # 按章知识源
references/quiz_bank.json        # 唯一测验题源
references/retrieval_index.json  # freshness-bound 轻量检索派生物
references/teaching_examples.json
references/teaching_baseline.json
notebook/chNN.md                 # 持久化完整教学与反馈
notebook/chNN.guide.json         # 当前章已验证的 profile=full 强类型完成清单
mistakes/chNN.md                 # 错题镜像
study_guide/chNN.html|pdf        # 当前章派生阅读产物
study_guide/chNN.receipt.json    # manifest/HTML/PDF 哈希与 QA 状态
study_guide/qa/chNN_pNNN.png     # 最新 PDF 的逐页验收证据
```

常规材料入口是 `scripts/ingest_course.py`：预检 → 解析 → 结构化编译 → 状态初始化 → 视觉索引 → validator。退出 10 表示程序完成但内容 readiness 被阻断，必须用 `scripts/ingest_review.py` 逐项接管；不能因为 wiki/题库已经出现就开始教学。review patch 绑定来源哈希并写入 append-only ledger，再重编译 wiki、题库和检索索引。

状态存在时只能经 `scripts/update_progress.py` 修改；无状态但 Python 可用时先 `init`。`study_progress.md`、wiki/题库、检索索引、HTML 和 PDF 都是面向不同用途的编译/派生视图，不能反向覆盖 `.ingest/` 或 `study_state.json` 的事实源。

## 5. 语言层

- 控制规则：`skills/*/SKILL.md`，以英文、精确、可测试的流程为主；触发 metadata、规范状态值和逐字学生话术是明确例外。
- 学生文案：`locales/<lang>/skills/*.md`、消息目录和模板。
- 持久化规范语言值：`zh` / `en` / `bilingual`；`中文` / `English` / `双语` 只是显示别名和旧状态迁移输入，根路由器先归一化再派发。
- 原始材料的逐字引文可保留原语言并标明；智能体生成 prose 必须遵守所选语言。
- 机器契约：JSON keys、stable IDs、hash、reason code 和 lifecycle status 固定，不随翻译改变。
- 人类可读视图：智能体生成的 notebook、回执与教材按选择语言渲染；状态枚举保持 canonical。若 legacy/generated 进度视图仍是中文 canonical，代理只把它当状态视图读取，再按当前语言复述，不能把两层混为一谈。

详见 [`language-policy.md`](language-policy.md) 和 [`localization.md`](localization.md)。

## 6. 关键不可变式

- 知识来源透明：🟢 来自资料 / 🟡 AI补充，可能与你老师讲的不完全一致 / ⚠️ AI生成答案，非老师/教材提供。
- 测验只来自 `quiz_bank.json`；没有题库或题面资产不可见时失败关闭。
- 学习模式和时间档位影响节奏，但不解除来源、状态或资产门禁。
- 图结构题先运行确定性算法，再渲染。
- 教学、判分、疑难和复盘先写 notebook，再返回对话摘要。
- 结构化工作区在阶段完成前必须验证当前章 `profile=full` typed manifest；`artifact_mode=chat` 到此停止，不要求 HTML/PDF。
- `visual` 或一次性请求才进入视觉产物流程；`visual` 只有在 receipt 哈希匹配、逐页全部验收、零未解决缺陷且 `artifact_ready=ready` 后才能交付和完成阶段，不能把“请求生成”写成“已经成功”。
- 修改 `study_state.json.language` 会使旧语言 manifest/HTML/PDF/QA stale；先 relocalize/补齐语言块，再重新渲染和验收。
- 建库程序的 warnings、skipped、人工审阅项和缺答案项必须逐条接管。
- 结构化工作区 `readiness=blocked` 时禁止进入授课、测验或阶段完成。

## 7. 验证层

仓库测试覆盖：

- skill frontmatter 与目录清单；
- zh/en 文案、消息键和模板结构对齐；
- 语言纯净与 canonical 标签；
- 状态初始化和写入边界；
- 题库、范围、视觉资产和来源契约；
- 工作区 schema/validator；
- 分发包必须包含路由器、控制层、语言包和脚本；
- Markdown 相对链接与模板样例硬编码。

新增功能应更新现有事实源及对应语义测试，不应再创建一份 release-era 手册或复制整段规则。
