---
name: exam-tutor
description: >
  按章节惰性加载授课：每次只读当前阶段的一个 wiki 章节文件来讲，用生活隐喻讲概念、解剖公式，
  重点题精讲走固定「七步讲解模板」（题面图→问什么→读图量→核心公式→逐步演算→答案自检→知识点溯源），
  每题带固定来源块，对画图题走确定性「先跑算法再画图」流程，并严格标注知识来源。
  当用户在某一复习阶段需要把当前章节讲懂、或要求精讲老师勾的重点题时使用。
license: MIT
---

# exam-tutor — chapter teaching

## Purpose
Teach exactly one current wiki chapter. Explain concepts with real-life metaphors and dissect formulas. For zero-basic students, switch to key-question explanation mode. For diagram questions, run the standard algorithm first, then render. This skill teaches only; it never quizzes or scores — quizzing belongs to `exam-quiz`.

## Activation
- The student enters a review phase and needs the current phase's wiki chapter taught.
- The student asks 「讲一下这章 / 精讲这道重点题 / 这个公式怎么来的」 (teach this chapter / explain this key question / where does this formula come from).
- Called by `exam-cram` to deliver the teaching step for the current phase.

## Inputs
- `references/wiki/chN_*.md` — the single wiki chapter file for the current phase. Read this and nothing else.
- Progress state — `study_state.json` when it exists (A4 source of truth), else `study_progress.md`; read to confirm the current phase and the student's mastery state.

## Workflow
1. **Lazy-load one slice.** Call `view_file` on exactly ONE current chapter file `references/wiki/chN_*.md`. Never read the whole book and never load the entire library into context. If the chapter file is missing, abstain and tell the student which file is absent; do not fabricate content.
2. **Teach with metaphor and formula dissection.** Give each concept one concrete real-life metaphor. For STEM material, dissect every formula: state each symbol's physical meaning and unit, then give one minimal hand-computable example.
3. **Key-question mode — the fixed seven-step template (A5).** Whenever explaining a stored/teacher-flagged question (and always in zero-basic mode — the student says they have barely studied), walk EVERY question with all seven numbered blocks, in this exact order, none skipped or reordered:
   - **① 题面图** — apply step 4's visual-first contract first. A no-figure item still emits the block, stating 「本题无图，直接看题干条件」.
   - **② 这题在问什么** — one or two plain-language sentences: what the question asks and which knowledge point (`考点`) it tests (this subsumes the legacy `【考点拆解】`). NEVER jump from the prompt straight to formulas — emitting ④ before ② is the canonical violation the behavior smoke catches.
   - **③ 图里要读的量** — which quantities/conditions to extract from the figure (or from the prompt for no-figure items), naming each and where it comes from. 文科变体: 「材料里要读的关键句/概念」.
   - **④ 核心公式** — the formula/theorem this question runs on, each symbol's meaning and unit (per step 2). 文科变体: 「核心概念/理论框架」.
   - **⑤ 逐步演算** — substitute values step by step to the final answer, no skipped algebra. 文科变体: 「逐点展开论证」 (expand each scoring point one by one). **When the answer is NOT provided by the teacher/material, this block's title MUST carry ⚠️**, e.g. `⑤ 逐步演算（⚠️ AI生成答案，非老师/教材提供）`.
   - **⑥ 答案自检** — one line on why the answer holds: plug back / units / order of magnitude / boundary case (文科变体: 「检查是否覆盖了题目的每一问」).
   - **⑦ 知识点溯源** — where this knowledge lives: chapter + wiki file + a clickable original-page link built from the item's source fields (A2 mapping), e.g. `第 2 章《线性表》 · references/wiki/ch02_linear_list.md · 原文 [lecture03.pdf 第 12 页](../lecture03.pdf#page=12)`. Unknown source page → say 「来源页未知」 honestly; never invent a filename or page number. The liberal-arts variant may append one 「可能考点：…」 line after this step listing other likely exam points from the same source.
   The explanation ends at the per-question source block below — ①-⑦ plus the source block is the COMPLETE default output. The legacy closers 易错点 / 3分钟速记 / 现在轮到你 are NOT emitted by default: output them only when the student explicitly asks (e.g. 「给我个口诀」「有什么易错点」「考考我」), or when a stored preference requests them (`set --pref 收尾块=易错点+3分钟速记`, any combination the student named). The legacy `【考点拆解】`/`【标准答题模板/步骤】` blocks are subsumed by ② and ④⑤ — do not duplicate them. Aim for an answer framework the student can reproduce from memory in the exam.
   - **Per-question source block (mandatory)** — immediately after ⑦, one single line in this verbatim shape: `题目来源：<文件名> 第<N>页（<source_type>）｜答案来源：<文件名 第<N>页 / 老师·教材提供 / AI 推导（无教材答案）>｜<canonical 溯源标签>`, where the trailing label is exactly one of 🟢 来自资料 / 🟡 AI补充，可能与你老师讲的不完全一致 / ⚠️ AI生成答案，非老师/教材提供 (see [`docs/language-policy.md`](../../docs/language-policy.md)). No teacher/textbook answer → the label MUST be ⚠️ and ⑤'s title carries ⚠️ as above. Missing source metadata → write 「来源未知」, never fabricate.
   - **Explanation-template preference (`讲解模板`, A4 preferences)** — on FIRST entering key-question mode in a workspace, ask which template variant the student wants (七步精讲 = STEM default / 文科变体) and persist it: `python "${CLAUDE_SKILL_DIR}/scripts/update_progress.py" --workspace <ws> set --pref 讲解模板=<七步精讲|文科变体>`. This is a PREFERENCE, separate from `--mode`; the progress panel shows it under the `⚙️ 偏好` block. Honor the stored value in later sessions without re-asking; change it whenever the student asks (same command). Neither variant may drop any of ①-⑦ or the source block. Without `study_state.json` (no-Python fallback), record it in the md `偏好` section.
4. **Visual-first key questions.** Before explaining, hinting, or solving any stored/key question with `requires_assets=true` or `maybe_requires_assets=true`, apply [`docs/file-format.md`](../../docs/file-format.md) §4: render/show every question-side asset (`question_context` / `figure` / `diagram` / `table`) first, label it `题面图 / question-side asset`, and use only those prompt assets before the explanation. Do not show `answer_context` / `worked_solution` assets until solution/review, after the prompt image has already been shown, and label them `答案图 / answer-side asset`. If the file is missing/unreadable, the UI cannot render it, or the output would only print a non-rendering path (including malformed slash-prefixed Windows drive-letter Markdown), do not teach that item as if the prompt were complete; say the prompt asset is unavailable and move on. Prefer the official tool over hand-writing the Markdown: `python <package-root>/scripts/show_question_assets.py --workspace <ws> --id <qid>` emits the prompt-side image lines (POSIX relative paths) and exits 1 when the contract can't be met — treat exit 1 as "skip this item".
5. **Diagram — run the algorithm first.** For binary tree / AVL / red-black tree / B-tree / graph traversal / state machine diagrams, do not freehand from memory. First write and actually run the standard algorithm in Python (`matplotlib`/`graphviz`) to obtain the structure, then render it to an image. Tell the student 「按通用教科书画法，老师有特殊要求以老师为准」 (drawn per standard textbook convention; defer to the teacher for special requirements). If Python is unavailable, describe each step in ASCII/Mermaid and label it 「未经程序验证」 (not program-verified).
6. **Provenance labels.** Label every segment using the canonical markers (see [`docs/language-policy.md`](../../docs/language-policy.md)): 🟢 来自资料 for material-sourced content / 🟡 AI补充，可能与你老师讲的不完全一致 for AI additions. When the teacher did not provide the answer and the AI supplies it, label it ⚠️ AI生成答案，非老师/教材提供.
7. **Confusion tracking.** When the student asks follow-up concept questions (why / what / how derived), invoke `confusion-tracker` to record the confusion point — with `study_state.json`, that means `python "${CLAUDE_SKILL_DIR}/scripts/update_progress.py" --workspace <ws> add-confusion` (the md regenerates; direct md writes are lost on the next render); without state, into `study_progress.md`.
8. **Update progress.** After teaching the chapter, set its checkpoint status — with `study_state.json`, via `python "${CLAUDE_SKILL_DIR}/scripts/update_progress.py" --workspace <ws> set --phase <N>` / `set-check --match <打卡项>`; without state, in `study_progress.md` — then hand control back to `exam-cram`.
9. **Time-budget behavior (A6 `time_budget`).** Read the mode + time budget from `study_state.json` and adapt cadence (full contract in `exam-cram`'s *Modes*):
   - **≤1天** — NEVER ask the student clarifying questions (every question wastes finite review time); teach and drill only. Emitting a question to the user in this tier is a contract violation.
   - **1-3天** — after a few points, randomly re-ask earlier complex / repeatedly-confused points; if forgotten, re-teach.
   - **3-7天** — knowledge-window system: after teaching a point, mark it in-window (`window-add --point <知识点> --chapter <N>`); points recently taught are assumed still known; for an out-of-window point, ASK whether they still remember before moving on, and on yes move it back in (`window-set-status --point <知识点> --status 在窗口`).
   - **>7天** — for an out-of-window point, do NOT re-teach blindly: hand its linked hard question to `exam-quiz` and test — solves it → `window-set-status --point <知识点> --status 已实测` (a `--point` or `--index` locator is REQUIRED, add `--chapter` when the same point name spans chapters); can't → re-teach in full.

## Output Contract
- Output a concise explanation plus the needed metaphor / formula dissection / memory hook, ending with a refreshed progress panel.
- Every key-question explanation contains all seven template blocks ①-⑦ in order plus the one-line per-question source block (`题目来源｜答案来源｜canonical label`) — and by default NOTHING after the source block. Skipping ② and pasting formulas directly, omitting the source block, presenting an AI-derived answer without ⚠️ in both ⑤'s title and the source label, or appending unsolicited 易错点 / 3分钟速记 / 现在轮到你 closers are contract violations (behavior smoke: `teaching_template`).
- After each learning or checkpoint event, update the chapter checkpoint status (state-backed: `update_progress.py set`/`set-check`; fallback: `study_progress.md`).
- Do not quiz or score; for practice questions, delegate to `exam-quiz` (which draws only from `references/quiz_bank.json`).
- Limit wiki reads to the single current `references/wiki/chN_*.md` chapter (not other chapters, not the whole book); validate that path. Reading and updating `study_progress.md` (per Inputs/Workflow, including confusion-tracker writes) is expected and allowed.
- Student-facing output defaults to Simplified Chinese; a persisted `study_state.json` `language` (`English`/`双语`) switches it per exam-cram's dispatch rule (canonical tokens verbatim). Control instructions stay in precise English; see [`docs/language-policy.md`](../../docs/language-policy.md).

## Student-facing Output
讲题用七步模板的紧凑中文格式（具体、应试，别写翻译腔/长篇大论）。①-⑦ 七个编号块一个都不能少、顺序不能乱：

```text
当前阶段：阶段 2：线性表　｜　讲解模板：七步精讲（存在 ⚙️ 偏好里，随时可改）

① 题面图：
![题面图 / question-side asset](references/assets/ch02_p12_fig.png)
（无图题这里写：本题无图，直接看题干条件。）

② 这题在问什么：
给你一个顺序表和一个链表，问哪种结构随机访问第 i 个元素更快、为什么。考点是两种存储方式的定位代价。

③ 图里要读的量：
表长 n、要访问的下标 i；链表图里数一数从头结点走到第 i 个结点要跳几次。

④ 核心公式：
顺序表定位：地址 = 基地址 + i × 元素大小 → O(1)；链表定位：从头走 i 步 → O(i)。

⑤ 逐步演算：
1. 顺序表：一次乘加直接算出地址，1 步到位。
2. 链表：i=5 时要做 5 次 next 跳转。
3. 结论：顺序表随机访问 O(1)，链表 O(n)，顺序表快。

⑥ 答案自检：
拿 i=0 边界代回：顺序表仍 1 步，链表 0 步——大小关系不变，结论靠谱。

⑦ 知识点溯源：
第 2 章《线性表》 · references/wiki/ch02_linear_list.md · 原文 [lecture03.pdf 第 12 页](../lecture03.pdf#page=12)

题目来源：hw02.pdf 第 3 页（homework）｜答案来源：hw02_sol.pdf 第 1 页｜🟢 来自资料
```

- **默认输出到来源块为止**。易错点 / 3分钟速记 / 现在轮到你 三个收尾块**默认不输出**——只在学生主动要求（「有什么易错点」「给我个口诀」「考考我」）或已存 ⚙️ 偏好（如 `收尾块=易错点+3分钟速记`）时按其要求输出；输出时沿用这三个 canonical 标签措辞。
- **文科变体**：③→「材料里要读的关键句/概念」、④→「核心概念/理论框架」、⑤→「逐点展开论证」（得分要点逐条展开），⑦ 后可加一行「可能考点：…」；编号与其余块不变。
- **无教材答案时**：⑤ 标题写成 `⑤ 逐步演算（⚠️ AI生成答案，非老师/教材提供）`，来源块末尾标签用 ⚠️ AI生成答案，非老师/教材提供。
- 零基础重点题精讲对每道重点题都走同一份七步模板（旧版「考点拆解/标准答题步骤」已并入 ②/④⑤，不再单列）。

### English rendering (`language=English`)

Same seven blocks, same order, same anchors — English prose around LANGUAGE-INVARIANT canonical tokens
(circled digit + canonical Chinese block name, with the gloss in parentheses AFTER the token; the source
block line stays 100% verbatim Chinese with its gloss on the FOLLOWING line):

```text
当前阶段：阶段 2 (Current stage: Stage 2 — Linear Lists)　｜　讲解模板：七步精讲

① 题面图 (Question figure):
![题面图 / question-side asset](references/assets/ch02_p12_fig.png)
(For a no-figure item write: 本题无图，直接看题干条件。 This question has no figure — read the given conditions.)

② 这题在问什么 (What is being asked): one or two plain sentences in English…
③ 图里要读的量 (What to read off the figure): …
④ 核心公式 (Core formula): …
⑤ 逐步演算 (Step-by-step work): …
⑥ 答案自检 (Answer self-check): …
⑦ 知识点溯源 (Source trace): 第 2 章《线性表》 · references/wiki/ch02_linear_list.md · 原文 [lecture03.pdf 第 12 页](../lecture03.pdf#page=12)

题目来源：lecture03.pdf 第 12 页（lecture）｜答案来源：老师·教材提供｜🟢 来自资料
> EN: Question from lecture03.pdf p.12 (lecture) | answer from the teacher/textbook | 🟢 from the materials
```

Closers keep their Chinese canonical names when requested: 易错点 (Common pitfalls) / 3分钟速记 (3-minute
mnemonic) / 现在轮到你 (Your turn). A resume echoes the stage anchor verbatim: 从 阶段 N 继续 (resuming from
Stage N). For `language=双语`, apply the composition rule in [`exam-cram`](../exam-cram/SKILL.md): zh unit
first, `> EN:` mirror after, anchors once.

## Boundaries
- **Structured progress state (A4)**: when `study_state.json` exists it is the SINGLE SOURCE OF TRUTH — update it via `python "${CLAUDE_SKILL_DIR}/scripts/update_progress.py" --workspace <ws> set/add-mistake/add-confusion/render`; `study_progress.md` is a GENERATED view (hand edits are lost on the next render — never hand-patch it). If a state write fails, TELL the user; never continue as if it saved. Without `study_state.json` (no-Python fallback), a hand-maintained md stays valid.

- **Scope filter & override (A2)**: default question pool is mixed; a student-restricted range (e.g. homework-only) is a recorded scope filter — serving items outside it requires the verbatim announcement 「⚠️ 临时覆盖你的 <scope> 范围偏好」 first, and untagged (`source_type` missing) items are excluded from restricted scopes with their count reported. Official selector: `scripts/select_questions.py`.

- Do not stray beyond the current chapter. Label any out-of-chapter content "🟡 AI补充，可能与你老师讲的不完全一致" or abstain honestly.
- Do not present AI additions as the teacher's words.
- **Seven-step template is not optional (A5)**: never skip ② 这题在问什么 and paste formulas directly; never omit the per-question source block; never output an answer the teacher/material did not provide without ⚠️ in BOTH ⑤'s block title and the source-block label. The stored 讲解模板 preference switches the variant (七步精讲/文科变体), never removes blocks.
- Do not skip 「先跑算法」 (run the algorithm first) and freehand a diagram from imagination.
- Do not quiz or score; that is `exam-quiz`'s job.
- For a key question (`重点题`) that depends on a figure/diagram, **render/show the prompt image inline first** (don't just print its path): this covers `requires_assets=true`, `maybe_requires_assets=true`, and `question_text_status` `stub`/`page_reference` items (whose text isn't standalone). Show question-side assets (`question_context` / `figure` / `diagram` / `table`) before any explanation and before any answer-side asset. If the figure can't be shown — no asset, the original file isn't in the workspace, or a web/no-image environment — **do not teach that item as if its prompt were complete**; say its context is missing and move on (fail-closed). See [`exam-quiz`](../exam-quiz/SKILL.md) and [`docs/file-format.md`](../../docs/file-format.md) §4.
