# 可选的外部 OpenAI API 回退方案

中文 · [English](openai-study-guide-adapter.md)

此适配器是按题生成 Study Guide 详解时单独计费、需要主动选择的回退方案，只适用于已经确认的完整 ingestion-v2 工作区。它**不是**默认的隔离路线。如果当前宿主能够启动一个全新的子智能体，并且只向它提供准确的单题数据包和受限工具，就应改用这个宿主原生子智能体：它不需要额外 API key，并且仍处于宿主账户自身的额度与隐私边界内。

绝不能仅因为已有 key，或宿主/模型来自 OpenAI，就选择此适配器。只有在原生路线不可用或用户有意拒绝原生路线之后，用户又明确要求把所选题目发送到 OpenAI API 时才能使用它。轻量模式下始终不可用，也绝不能根据模型名称、订阅、`processing_mode` 或 `artifact_mode` 自动启用。

## 先用原生路线，再考虑外部回退

原生子智能体路线必须为每道题创建一个全新的子智能体，并且只传入：

- 固定的零基础优先指令和目标语言；
- 准确的原题，以及存在时的官方答案；
- 仅限当前目标题的题面/答案资源。

它不能接收主对话历史、其他题目、课程 wiki、notebook 或无关资源，并且必须禁用文件系统、网络和其他工具。如果宿主不能如实强制执行上下文边界与工具边界，就使用普通撰写流程。不得自动继续回退到此外部适配器。原生宿主回执和 API 适配器回执都只是对已应用控制措施的声明，不是对加密级沙箱隔离的证明。

## 外部 API 能力与授权门禁

外部回退仍保留两个相互独立的授权阶段。在改变模式或撰写 API 计划之前，智能体必须先取得一次**不上传、只做规划的选择授权**：

1. 宿主能够直接向 OpenAI API 发起 HTTPS 调用，并能保证 secret 不进入课程工作区、回执、日志和 Git。
2. 所选模型接受图片输入并支持 Structured Outputs。
3. 已告知用户 ChatGPT/Codex 订阅与 OpenAI API 计费彼此独立、数据会由哪个 Provider 接收，以及当前服务的数据保留/隐私边界。
4. 用户已明确允许在本地写入模式、数据包、标注、请求和计划；这个规划阶段不上传任何课程内容。

执行 `run` 之前，第二次授权必须绑定到已经生成的计划：披露准确的隔离题目/图片上传范围、调用次数和字节清单；另行查阅当前官方价格，并在说明假设的前提下给出有上下界的费用估算；然后取得用户针对该准确 `plan_id` 的明确授权。该计划不可能预先知道最终准确的输入 token、输出 token 或费用。

API key、规划授权、原生隔离偏好或此前的视觉模式选择，都不等同于最终上传授权。
如果缺少规划授权，保持 `answer_explanation_mode=ordinary`；如果只缺少最终授权，已经准备好的隔离计划保持休眠，不会发生任何 Provider 调用。普通 Guide 仍然要求每道题都有面向零基础学习者的详细解释。

## 命令

`probe` 不会发起网络请求，也绝不会泄露 key：

```bash
python scripts/host_adapters/openai_study_guide.py --workspace <ws> --json probe
```

这只是本地配置/key 预检。`ok=true` 并不验证端点可达性、模型访问权限、额度、计费或实时速率限制。

取得第一次不上传规划授权后，必须先明确启用这一延展功能，**然后**再准备撰写事实。模式会绑定到数据包/模板/标注，因此状态改变后，普通模式生成的数据包会立即过期。重新执行准备；只把生成的包装对象中的 `annotations` 对象复制到规范标注路径，填完其中每一个空值和哨兵值，并且在验证通过前不得继续。隔离模板有意省略 `answer_explanation`。随后准备请求集合：

```bash
python scripts/update_progress.py --workspace <ws> set --answer-explanation-mode isolated
python scripts/study_guide_author.py --workspace <ws> prepare --chapter <N> --json
# Copy only the template wrapper's `annotations` object to
# notebook/chNN.authoring-annotations.json and fill that target completely.
python scripts/study_guide_explain.py --workspace <ws> prepare --chapter <N> --json
```

生成准确的披露计划。`--limit` 可用于另外取得授权的小规模试用；省略它会选择全部待处理题目。检查 `selected_upload_scope`：其中逐项列出题目/请求、instruction/model-input/output-schema 的哈希与字节数，以及每个附件的 side/path/ID/hash/bytes。当用户需要检查实际题目/答案 JSON，而不仅是有边界的元数据时，应为每个请求使用报告中给出的 `inspect_request_command`：

```bash
python scripts/host_adapters/openai_study_guide.py --workspace <ws> --json plan \
  --chapter <N> --model <vision+structured-output-model> --detail high
```

智能体必须在此命令之外查阅当前官方价格来估算费用，并说明估算的假设与不确定性。不得声称 `plan` 能算出准确价格。

只有用户接受该计划后，宿主才能执行它。调用次数确认值必须等于 `selected_call_count`，计划确认值必须等于 `plan_id`。该 ID 绑定准确的待处理请求/题目、附件路径与哈希、模型、图片 detail、输出上限、超时、零重试策略、key 来源，以及准确生效凭据的单向绑定。绑定标识符绝不会泄露或存储 key，但在规划后更换 key/项目凭据会强制生成新的披露计划。因此，过期选择或调用次数相同但选择不同的情况都会在上传前失败：

```bash
python scripts/host_adapters/openai_study_guide.py --workspace <ws> --json run \
  --chapter <N> --model <same-model> --detail high \
  --consent-upload --confirm-call-count <exact-count> --confirm-plan-id <exact-plan-id>
```

该命令可以断点续跑：每个已接受的响应都通过规范的 append-only 解释 ledger 导入。重新运行时只选择当前仍待处理的请求。最后一道题导入后，除非提供了 `--no-finalize`，适配器会完成规范隔离解释回执的 finalize。

这次成功并不代表 Study Guide 已完成。继续执行规范工作流：

```bash
python scripts/study_guide_author.py --workspace <ws> persist-notebooks --chapter <N> --json
python scripts/study_guide_author.py --workspace <ws> compile --chapter <N> --json
# Then create/import claims, attach and verify them, and run study_guide_content.py
# validate/import exactly as specified by skills/exam-study-guide/SKILL.md.
```

渲染、逐页视觉 QA 和阶段完成仍是之后彼此独立的门禁。

如需返回普通路线，设置模式并重新执行准备；填写新普通模板中必需的详细 `answer_explanation` 字段，不能复用隔离模式的标注或回执：

```bash
python scripts/update_progress.py --workspace <ws> set --answer-explanation-mode ordinary
python scripts/study_guide_author.py --workspace <ws> prepare --chapter <N> --json
```

改变偏好不会重写历史隔离 Guide，也不会给它重新贴标签。新的撰写流程会绑定自己的模式，并在标注、bindings 或回执过期时失败关闭。

## Provider 请求边界

对于每道被选中的题目，适配器至多尝试一次 `POST /v1/responses`，内容只包括：

- 当前请求中固定的零基础优先指令；
- 该题准确的题目/答案 JSON 和目标语言；
- 该题受修订版本约束的题面/答案附件，每个附件均已受 Study Guide 裁剪策略限制；
- 该请求严格的 JSON 输出 schema。

请求设置 `store=false`，不提供任何工具、conversation identifier、`previous_response_id`、file store 或 background job。适配器只接受一个结构化 `output_text`，只导入 `answer_explanation` 以及不参与渲染的 coverage 对象，并且不保存原始 Provider 响应。上传前，它会重新计算每个实时附件的哈希。

自动 HTTP 重试已禁用。超时或断开连接可能产生歧义——即使适配器没有接受到响应，Provider 也可能已经收到 POST——因此命令会停止，避免冒险产生未经授权的重复调用。任何手动恢复之前，都要重新运行 `plan`，披露这种不确定性，并取得对新的准确调用次数和计划 ID 的确认。已经接受的题目不会再出现在新的待处理集合中。
命令执行期间会一直持有一个独立的工作区/章节运行 mutex，普通状态写入则保持不受阻塞；每次 POST 之前，适配器都会重新载入模式、当前请求/ledger 顺序、附件修订版本和凭据。如果出现第二个并发运行、隔离模式被撤销、请求改变或凭据改变，就会在下一次上传之前停止。

这些控制支持无状态宿主声明；它们不是加密级沙箱证明。OpenAI 仍可能根据账户的数据控制设置处理滥用监测日志。按照当前官方数据控制表，`/v1/responses` 内容默认不会用于训练，但除非适用已获批准的数据保留控制，否则滥用监测数据最长可能保留 30 天；默认的 `store=true` 应用状态行为正是此适配器发送 `store=false` 的原因。每次调用时，都必须披露当时适用的政策。

## Secret 处理

适配器先在宿主环境中查找 `OPENAI_API_KEY`，然后查找仓库本地 `.env.local` 中的 `OPENAI_API_KEY`。优先使用宿主/操作系统的 secret 存储。`.env.local` 只是被 Git 忽略的明文回退方案；Git ignore 不会加密它，也不能阻止其他本地进程/用户读取它。适配器不接受命令行 key，不打印 key，不把 key 放入回执，也不把 key 复制到学生工作区。它不会提出创建 key 或安装依赖的请求。

## 固有限制

- 并非每个基于 GPT 的智能体或宿主都能配置 key、发起任意 API 调用，或如实声明一次全新/无状态且禁用工具的调用。
- 在一次已授权运行中，每个选中题目最多对应一次 POST 尝试。因此，速率限制、延迟、图片 token 和输出 token 会随章节题目数量增长；发生歧义传输后手动恢复，仍可能导致 Provider 一侧重复处理，必须在授权新计划之前披露这一点。
- `store=false` 本身不会授予 Zero Data Retention，也不会消除滥用日志。
- 结构化 JSON 只保证形状，不保证教学正确性。现有的来源、裁剪、出处、coverage、claim 和视觉 QA 门禁仍然适用。
- 语言、提示词、材料修订版本、标注或附件的变化都会使请求/回执失效，并可能需要再次付费运行。
- 不受支持的图片编码会在上传前失败；适配器不会悄悄转换或下载资源。

官方参考资料：

- [身份验证](https://developers.openai.com/api/reference/overview#authentication)
- [图片与视觉](https://developers.openai.com/api/docs/guides/images-vision)
- [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [数据控制](https://developers.openai.com/api/docs/guides/your-data)
- [速率限制](https://developers.openai.com/api/docs/guides/rate-limits)
