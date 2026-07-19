# PDF capability adapters

本仓库把“如何组织考试教材”和“某个 Agent 如何操作 PDF”分开。前者由本仓库的
`exam-study-guide` skill 与 `scripts/study_guide_render.py` 负责；后者可以复用宿主原生能力，
但不能改变本仓库的来源标注、题面图先于答案图、路径安全和逐页视觉验收契约。

本页所有 Study Guide/PDF 路线只属于显式 `processing_mode=full`。轻量模式即使保存了
`artifact_mode=visual`，该偏好也处于 dormant，effective output 仍为 `chat`；一次性 PDF
请求也不能绕过 full-processing gate。轻量页批次的宿主原生 PDF 视觉读取不是“生成
Study Guide”，其证据必须留在 `.lightweight/assets/` 并遵守轻量会话契约。

机器可读路由表见 [`pdf-capability-adapters.json`](pdf-capability-adapters.json)。表中的 GitHub
引用固定到 `review_commit`；安装命令仍会获取上游当前版本，因此只能在用户明确同意后执行，
并应重新检查许可证、实际加载的 skill 和行为差异。

## 路由顺序

1. 宿主已经提供且探测成功的 PDF skill：选定 `native`；完成本章预检后，用仓库渲染器生成经过校验的自包含 HTML，再让原生能力把这份 HTML 打印/转换到规范 PDF 路径；不重复安装，也不要求 Edge/Chrome。
2. 没有原生能力：使用本仓库 `exam-study-guide` 与本地 Edge/Chrome 浏览器打印后备。
3. 后备能力不足且用户同意：只建议安装该宿主的官方来源。
4. 依赖、浏览器或渲染能力仍缺失：明确失败并给出缺项；禁止留下含 raw LaTeX 的伪成品。

每条实际执行路径都必须遵守同一顺序：**验证/import 强类型章节教学清单 → 探测并选定后端 →
对当前章和该后端做依赖预检 → 生成并验证 HTML → 生成 PDF → `study_guide_qa.py render` →
逐页视觉检查 → 显式 accept**。选择原生能力不等于先生成文件；依赖预检仍须发生在章节 HTML 渲染之前。

公式渲染使用经审查固定的 `latex2mathml==3.60.0`（MIT，审查 commit
`de87cf0f228416e3152218c12b8bdb4ee6f4ecca`）。该版本支持本仓库的 Python 3.7+ 范围；上游当前
版本要求更高的 Python，不能给旧运行时一条无法安装的“最新版”命令。wheel/sdist SHA-256、来源与
许可证记录在机器表的 `audited_dependencies`。首次材料预检只处理从原材料即可确定的 PDF 读取/渲染依赖，
不会因为 `visual` 偏好就猜测当前章一定有公式或一定走浏览器。章节事实源落盘并选定后端后，运行：

```text
python scripts/check_deps.py --workspace <ws> --chapter <N> --artifact-mode visual --pdf-backend <native|browser|html>
```

只有本章实际含标准公式（包括 typed manifest 的公式与代入式）时才把 `latex2mathml` 标为需要；
`formula_hint`、控制字符或乱码等未恢复证据产生 `chapter_math_status=needs_recovery` 并阻止 visual，绝不能
解释成“本章没有公式”。只有选中 `browser` 后端时才把 Edge/Chrome 标为需要。缺失时只给固定安装建议，
仍须用户同意。

`native` 不是“把一个 PDF 放进目录就算完成”。以 `--pdf-backend native` 生成 HTML 后，章节 receipt 会保持
`awaiting_native_pdf`，并公开本次转换必须使用的 `html_sha256` 与 `conversion_start_gate_sha256`。宿主适配器
在转换前记录这两个值、机器表声明的 `adapter_id`、实际加载的精确版本和 UTC 开始时间，只消费这份精确 HTML，
并把结果写到规范路径 `<ws>/study_guide/chNN.pdf`。记录 UTC 完成时间后运行：

```text
python scripts/study_guide_render.py --workspace <ws> --chapter <N> --pdf-backend native --bind-native --native-pdf-path <ws>/study_guide/chNN.pdf --native-adapter-id <declared-id> --native-adapter-version <exact-version> --conversion-input-html-sha256 <receipt-html-sha256> --conversion-start-gate-sha256 <receipt-gate-sha256> --conversion-started-at <UTC-Z> --conversion-completed-at <UTC-Z> --json
```

该命令不调用适配器、不联网、不安装依赖，也不渲染 PDF；它在 workspace publication lock 内重新验证当前 typed
manifest、HTML、full-processing/runtime gate、适配器 allow-list、规范 PDF 路径/签名/哈希和时间顺序，然后原子地
把 receipt 变为 `qa_pending`。任一不匹配都会保留原来的未绑定 receipt，因此旁路写入、旧 PDF 或手工改 receipt
都不能进入 QA。适配器 ID/版本是由宿主声明并纳入 conversion hash 的身份，不是对宿主沙箱行为的证明；宿主若
无法提供实际加载的精确版本，就不能使用 `native` 绑定，应明确回退到 `browser` 或只交付 HTML。

无论走哪条路径，最终 PDF 都必须把每一页渲染为 PNG，检查最新渲染，并在已知视觉缺陷为零后运行：

```text
python scripts/study_guide_qa.py --workspace <ws> --chapter <N> --json render
python scripts/study_guide_qa.py --workspace <ws> --chapter <N> accept --inspected-pages all --reviewer <name> --reviewer-kind agent --page-verdict 1=pass
```

多页 PDF 必须为每一页重复传入一次 `--page-verdict N=pass:<notes>`；上面是一页 PDF 的最小命令形状。

只有 receipt 的哈希仍匹配且 `visual_qa.status=ready` 才能交付。外部 skill 负责提供工具能力，不能替代验收。

## Agent-specific adapters

### Codex

- 首选：运行时已经可见的 `pdf` skill。
- 原生绑定使用机器表中的稳定 ID `codex.pdf`，版本必须填写宿主实际加载的精确 skill/plugin 版本，不能写 `latest` 或猜测值。
- 探测成功后先生成 `study_guide/chNN.html`，再由该原生 skill 把这份 HTML 转成规范路径的 PDF；预检使用
  `--pdf-backend native`，不得因为本地浏览器缺失提前失败。
- 当前插件目录：[`openai/plugins`](https://github.com/openai/plugins)。
- [`openai/skills` 的历史 PDF skill](https://github.com/openai/skills/tree/49f948faa9258a0c61caceaf225e179651397431/skills/.curated/pdf)
  只作为 Apache-2.0 行为参考；该仓库已声明 deprecated，不作为新的安装建议。
- 如果原生 `pdf` 不可见，直接使用本仓库后备，不静默从历史目录安装。

### Claude Code

- 若 `pdf` 或 `document-skills:pdf` 已可见，直接使用。
- 原生绑定使用机器表中的稳定 ID `claude_code.document-skills.pdf`，版本必须填写宿主实际加载的精确插件版本。
- 已安装能力探测成功时，预检使用 `--pdf-backend native`，并让它消费已经校验的章节 HTML；只有回退到
  仓库浏览器打印时才改用 `--pdf-backend browser`。
- 缺失时可在得到用户同意后运行官方命令：

  ```text
  /plugin marketplace add anthropics/skills
  /plugin install document-skills@anthropic-agent-skills
  ```

- 审查快照是 Anthropic 官方仓库的
  [`skills/pdf`](https://github.com/anthropics/skills/tree/9d2f1ae187231d8199c64b5b762e1bdf2244733d/skills/pdf)。
  该文档 skill 是 proprietary/source-available，许可证禁止复制、派生和再分发，因此本仓库只链接，
  不 vendor、不“针对场景改写”它。
- 上游当前存在插件可能加载超出声明范围 skill 的
  [公开 issue](https://github.com/anthropics/skills/issues/1087)。安装后应检查实际 skill 列表；
  本仓库不会自动执行该安装。

### 通用 Agent Skills host

- 本仓库的 `skills/exam-study-guide/SKILL.md` 遵循
  [Agent Skills specification](https://agentskills.io/specification)，由宿主按普通项目 skill 加载。
- 如果宿主不会自动发现 skill，也可以直接运行 `scripts/study_guide_render.py`；仓库后备属于
  `--pdf-backend browser`，因此只有这一路径才要求 Edge/Chrome。依赖缺失时按预检/脚本的 fail-loud
  信息处理。
- Cursor、Windsurf 或仅支持项目规则的 Agent 走这一后备路径，并继续读取 `AGENTS.md` 的核心契约。

## 选择与升级规则

- “热门”只能用于发现候选，不是采用标准。候选必须同时满足官方来源、任务匹配、许可证可用、
  可固定审查版本和可回退。
- 每次改动 `review_commit` 都要重新阅读 skill、许可证和安装清单，并运行合成教材回归。
- 不把外部 GitHub URL 写成运行时自动下载钩子；复习流程不应因网络、上游漂移或供应链变更而失控。
