# Language Policy / 语言策略

The project has an English control plane and two student-facing wording packs. Root [`SKILL.md`](../SKILL.md) is a language-neutral router; behavioral rules live in [`skills/exam-cram/SKILL.md`](../skills/exam-cram/SKILL.md) and its subskills. `locales/zh/` and `locales/en/` contain Simplified Chinese or English wording, templates, and compact compatibility indices, never competing workflow logic.

## 1. Language state and dispatch

`study_state.json.language` persists only `zh`, `en`, or `bilingual`. `中文`, `English`, `双语`, and legacy values are accepted migration/display aliases, not new stored values. First contact sets mode, budget, and language together; default English unless the opening is Chinese. Urgent contact infers the opening language but never bilingual; later switches apply to the next reply.

| Value | Wording | Rendering |
| --- | --- | --- |
| `zh` | `locales/zh/skills/<skill>.md` | Simplified Chinese only (`中文`) |
| `en` | `locales/en/skills/<skill>.md` | English only (`English`) |
| `bilingual` | both | each Chinese block followed by its `> EN:` mirror (`双语`) |

## 2. SINGLE-LANGUAGE PURITY

- `zh`: agent-authored student prose contains no English sentences.
- `en`: agent-authored student prose contains no CJK.
- `bilingual`: compose the two pure blocks; it is not a third translation pack.
- Code, paths, commands, JSON keys, math/units, question IDs, and emoji may remain language-neutral.

### Source-quotation exception / 原文引用例外

A verbatim source question, quotation, or teacher answer may keep its original language only when explicitly marked `Original-language quotation` / 「原文引用」. This never exempts agent-authored headings, transitions, explanations, notices, generated solutions, or summaries.

## 3. EN CANONICAL VOCABULARY

English output uses these byte-exact forms:

| Category | Chinese canonical | English canonical |
| --- | --- | --- |
| Provenance | 🟢 来自资料 | 🟢 From your materials |
| Provenance | 🟡 AI补充，可能与你老师讲的不完全一致 | 🟡 AI-supplemented — may differ from what your teacher taught |
| Provenance | ⚠️ AI生成答案，非老师/教材提供 | ⚠️ AI-generated answer — not from your teacher or textbook |
| Walkthrough | ① 题面图 | ① Question figure |
| | ② 这题在问什么 | ② What's being asked |
| | ③ 图里要读的量 | ③ What to read off the figure |
| | ④ 核心公式 | ④ Core formula |
| | ⑤ 逐步演算 | ⑤ Step-by-step solution |
| | ⑥ 答案自检 | ⑥ Answer self-check |
| | ⑦ 知识点溯源 | ⑦ Source trace |
| Source block | `题目来源：…｜答案来源：…｜<标签>` | `Question source: … \| Answer source: … \| <label>` |
| Unknown source | 来源未知 / 来源页未知 | Source unknown / Source page unknown |
| Optional closers | 易错点 / 3分钟速记 / 现在轮到你 | Common pitfalls / 3-minute mnemonic / Your turn |
| Receipts | 已记录到错题本 / 已记录到疑难点 | Recorded to the mistake archive / Recorded to the confusion log |
| Stage references | 阶段 N / 从阶段 N 继续 | Stage N / Resuming from Stage N |
| Honest abstention | 资料里没有这道题的答案 | The materials do not contain an answer to this question. |
| Scope override | `⚠️ 临时覆盖你的 <范围> 范围偏好` | `⚠️ Temporarily overriding your <scope> scope preference` |
| Assets | 题面图 / 答案图 | Question-side asset / Answer-side asset |
| Progress panel | 备考科目 / 当前复习 / 进度打卡 / 错题累积 | Subject / Current stage / Progress / Mistake log |

A source line ends with one full provenance sentence, never an emoji alone. English uses `|`, Chinese `｜`. Optional closers require a request or stored preference. Unknown filenames/pages are never invented.

## 4. THREE-LAYER CONTRACT: PERSISTED / JUDGING-LAYER VOCABULARY

1. **Machine schema:** JSON keys, stable IDs, issue/patch statuses, reason codes, CLI commands, and automation JSON keep their defined spelling; never translate tokens such as `issue_id`, `content_unit_id`, `pending`, `validated`, or `applied`.
2. **Canonical values:** new state writes use `from_scratch|shore_up|fill_gaps`, `le1d|d1_3|d3_7|gt7d`, `zh|en|bilingual`, and documented status codes. Historical display values are migration inputs/generated-view wording only.
3. **Human views:** chat, notebook prose, guides, receipts, and summaries use the selected language. Restate a nonlocalized compatibility view; do not paste it as student prose.

A language change stales the prior-language typed guide, HTML/PDF, receipt, and QA: relocalize/source-consciously author, re-import, and, when visual output is requested, rerender and repeat every-page QA. `≤1天` may shorten bilingual blocks, never omit one side. Script automation JSON stays machine-stable; student messages use locale catalogs.

## 5. Ownership

Control-plane activation, ordering, schemas, exits, path safety, bank/asset gates, state writes, and anti-fabrication live once in `skills/*/SKILL.md`. Student wording, labels, receipts, and templates live in `locales/<lang>/`; locale packs cannot change workflow, fallback, source, or completion rules.

## 6. Web compatibility

Web prompts treat pasted state as read-only and return a copyable panel. They quiz only from a mounted bank; no bank caps at `covered_unverified`; missing prompt assets fail closed; they never claim local writes.

## 7. Maintenance checks

Update both packs together and run canonical routing, English/Chinese purity, roster/message parity, relative-link, bank-only, state-init, urgency, and source-quotation contract tests. Related: [`agent-portability.md`](agent-portability.md).
