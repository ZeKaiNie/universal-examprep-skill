---
name: exam-quiz
description: >
  从标准题库 references/quiz_bank.json 抽取本章题目对学生测验并判分，支持 6 大题型（选择、主观、
  画图、填空、判断、代码）。主观题用「要点检索制」对照 keywords 判分，连续答错两次给提示/跳过/归档。
  禁止现场编题。当某一阶段学完需要刷题检验、或用户要求测验/模考时使用。
license: MIT
---

# exam-quiz — question drilling & grading

Quiz the student from the question bank and grade against stored answers. Never invent questions or answers on the fly.

## Purpose
Pull chapter/phase-scoped items from `references/quiz_bank.json`, present one item at a time across the six quiz types, grade each answer, run the escape hatch on repeated failures, and archive skipped/wrong items (via `update_progress.py add-mistake` when `study_state.json` exists, else `study_progress.md`). Hand control back to `exam-cram` after the checkpoint.

## Activation
- Trigger after a phase is studied and needs a checkpoint quiz, or when the user asks 「测一下 / 来几道题 / 模考」.

## Inputs
- `references/quiz_bank.json` — the question bank. Each item carries `type`, `answer`, `explanation`, `source`, and a `chapter` OR `phase` tag; subjective items carry `keywords`. Filter selection by `chapter` or `phase`. An item with neither tag cannot be selected for a chapter quiz. Items MAY also carry `difficulty` (1-5) + `difficulty_reason` written by A7's `scripts/score_difficulty.py` — an honest **heuristic lower bound** from structural signals (`跨知识点数`/`结构`/`需读图`/`多页解答`/`章节位置`/`题型`), never a semantic judgement and never per-student; treat the number as an ordering floor, not truth.
- Current chapter number — select only items whose `chapter` (or matching `phase`) equals it.

> If `exam-ingest` produced the bank, require every item to carry `chapter`/`phase`. Without it, the chapter quiz reports "no items found" even when the bank holds matching items.

## Workflow
1. **Select & gate items**: filter by matching `chapter` OR `phase` (the bank uses both fields; filtering on `chapter` alone drops items tagged only with `phase`). If the bank contains relevant items, never write new questions.
   - **Scope filter (A2 source taxonomy)** — the default pool is **mixed** (all `source_type`s). When the student restricts the range (e.g. 「只做作业题」 = homework-only), record it as a SCOPE FILTER in the progress state and select via it (official tool: `python scripts/select_questions.py --workspace <ws> --source-type homework`). Items with no `source_type` tag are EXCLUDED from a restricted scope and their count reported — never silently serve untagged items as if they matched.
   - **Explicit scope override** — if a later request needs items OUTSIDE the active scope (e.g. homework-only is active but the student asks for lecture figure questions), announce the override BEFORE serving them with the verbatim student-facing marker 「⚠️ 临时覆盖你的 homework-only 范围偏好」 (substitute the active scope name). A one-turn override never silently changes the recorded scope; ask whether to switch permanently.
   - **Difficulty × mastery ordering (A7)** — when the student wants targeted practice (「挑难题」 / 「先补弱点」 / a mode-driven session), order items with `python "${CLAUDE_SKILL_DIR}/scripts/select_hard_questions.py" --workspace <ws> --chapter <当前章> -n <k>` (the script resolves from the skill package root — the student workspace has no `scripts/`; never resolve from cwd) instead of ad-hoc picking. **For a checkpoint quiz pass `--chapter <当前章>` (exact-chapter filter)** — `select_hard_questions` defaults to the whole bank, and `--chapter` is the ONLY exact-current-chapter scope. Do **not** use `--from-chapter N` for a checkpoint: it means every numeric chapter number ≥ N (「所有数值章号 ≥ N」), which pulls in later unstudied chapters; `--from-chapter` is only for 某章起步补弱 (start from chapter N onward). It combines the bank's `difficulty` (scored by `${CLAUDE_SKILL_DIR}/scripts/score_difficulty.py`; if unscored it computes on the fly, no write) with the student's `study_state.json` mastery (`错题`/`疑难` → weak, 窗口外 → weak, 在窗口/已实测 → mastered) and honors the A6 learning mode: 查缺补漏 serves weak-first 先易后难 then mastered 先难挑战; 零基础从头讲 is globally 先易后难. It honors the recorded scope (`study_state.scope`, untagged excluded per A2; `--source-type all` overrides to the mixed pool for one turn AFTER you announce the override); 某章起步补弱 **requires an explicit `--chapter` or `--from-chapter`** (it never guesses a chapter from `current_phase`). Deterministic heuristic ordering — no LLM ranking; the visual-first asset gate and scope filter below still apply to every item it returns.
   - **Visual-first asset gate (fail-closed)** — before asking an item, apply the single runtime contract in [`docs/file-format.md`](../../docs/file-format.md) §4:
     - For `requires_assets=true` or `maybe_requires_assets=true`: **before asking, explaining, hinting, or solving**, actually render/show every question-side asset (`question_context` / `figure` / `diagram` / `table`) inline and label it `题面图 / question-side asset`. **Merely printing the file path is not enough**; the student must see the prompt image.
     - Use only question-side assets at first. Do **not** show answer-side assets (`answer_context` / `worked_solution`) before the question-side assets; show them only during solution/review and label them `答案图 / answer-side asset`.
     - **Do not ask the item if any required question-side asset is missing, unreadable, unrenderable in the current UI, or only available as a non-rendering path** — say the item is blocked because the prompt asset cannot be shown, then pick another safe `full` item if one is available.
     - Prefer workspace-relative Markdown paths such as `references/assets/...`; never emit malformed slash-prefixed Windows drive-letter links, and never claim an image was displayed if the link did not render.
     - If `question_text_status` is `stub` or `page_reference`: **do not treat the text as a complete standalone question** — surface the prompt asset or original page first. If neither can be displayed (no asset, original not in the workspace, or web/no-image), **skip it** rather than asking a question the student cannot see.
2. **Grade by the six quiz types**:
   - `choice` — compare against the `answer` option.
   - `subjective` — keyword-coverage grading: pass if the answer covers the item's `keywords` and key steps; accept equivalent wording; report coverage feedback.
   - `fill_blank` — compare against the standard fill (accept synonyms).
   - `true_false` — compare the verdict and require a one-line reason.
   - `code` — check the key edits/output against `answer`.
   - `diagram` — do not judge the figure from memory: follow `render_hint` to run the standard algorithm first, derive the structure, then compare against the student's drawing; state that the instructor's drawing method takes precedence.
3. **Escape hatch**: on a wrong answer, give the logic gap + the item's `explanation` + a hint. On the 2nd consecutive wrong answer, offer three choices — view hint / skip and archive the wrong item / continue — and proceed per the choice.
4. **Archive**: record skipped or wrong items — with `study_state.json`, run `python "${CLAUDE_SKILL_DIR}/scripts/update_progress.py" --workspace <ws> add-mistake --id <qid> --chapter <ch> --note <错误原因>` (the script resolves from the skill package root — the student workspace has no `scripts/`; never resolve from the current directory) (hand-editing the generated md loses the row on the next render); without state, write into the `study_progress.md` wrong-item archive.
5. **Source honesty + per-item source block (A5)**: when an item's or answer's `source` is `ai_generated`, flag it at grading time with 「⚠️ AI生成答案，非老师/教材提供」 (reference only, verify against the instructor/textbook). Additionally, after grading EVERY item, emit the fixed one-line source block: `题目来源：<文件名> 第<N>页（<source_type>）｜答案来源：<文件名/老师·教材提供/AI 推导（无教材答案）>｜<canonical 溯源标签>` — the trailing label is exactly one of 🟢 来自资料 / 🟡 AI补充，可能与你老师讲的不完全一致 / ⚠️ AI生成答案，非老师/教材提供. When the answer is AI-supplied (no stored / textbook answer), the label MUST be ⚠️ AND the `解析/参考答案` block title carries ⚠️ (e.g. `参考答案（⚠️ AI生成答案，非老师/教材提供）`). Missing source metadata → write 「来源未知」, never fabricate a filename or page. Same contract as [`exam-tutor`](../exam-tutor/SKILL.md)'s teaching template.

## Output Contract
- Present one item at a time; grade as pass/not-pass plus key-point feedback; refresh the progress panel at the end.
- Each graded item's feedback ends with the one-line source block `题目来源：…｜答案来源：…｜<🟢/🟡/⚠️>` (Workflow step 5); an AI-supplied answer carries ⚠️ in both the `解析/参考答案` block title and the source label.
- Update the check-in log and wrong-item archive — via `update_progress.py` (add-mistake / set-check) when `study_state.json` exists, else in `study_progress.md` — then hand control back to `exam-cram`.
- Student-facing output defaults to Simplified Chinese; a persisted `study_state.json` `language` (`English`/`双语`) switches it per exam-cram's dispatch rule (canonical tokens verbatim). (See [`docs/language-policy.md`](../../docs/language-policy.md).)
- Provenance labels in feedback are verbatim student-facing markers: 🟢 来自资料 / 🟡 AI补充，可能与你老师讲的不完全一致 / ⚠️ AI生成答案，非老师/教材提供.

## Student-facing Output
判分反馈用简短、具体的中文，先点考点再给改进：

- **答对**：✅ 对了。这题考什么：……（一句点考点）。顺手记个易错点：……。
- **每题判分反馈末尾固定加一行**：题目来源：hw02.pdf 第 3 页（homework）｜答案来源：hw02_sol.pdf 第 1 页｜🟢 来自资料（AI 生成答案时末尾标签用 ⚠️，且解析块标题带 ⚠️）。
- **部分对**：🟡 思路对了一半——你答到了「……」，但漏了「……」这一步，补上就满分。
- **答错**：❌ 这里错了：……（指出逻辑漏洞）。标准答题步骤：1.… 2.…。再看一眼原题解析。
- **连错两次**：要不要 ① 查看提示　② 跳过并归档错题　③ 再想想？选 ② 我就「已记录到错题本」，考前再扫雷。
- **题/答为 AI 生成**：⚠️ AI生成答案，非老师/教材提供，仅供参考，请和老师/教材核对。

### English rendering (`language=English`)

Grade in English around the LANGUAGE-INVARIANT anchors: the receipt keeps its canonical form with a
trailing gloss — 已记录到错题本 (recorded to the mistake archive)；the escape-hatch option ② keeps the
错题本/归档 tokens (e.g. "② skip & archive to 错题本"); the scope-override marker is emitted verbatim
BEFORE any out-of-scope item, gloss after: 「⚠️ 临时覆盖你的 <scope> 范围偏好」 (temporarily overriding
your <scope> scope preference); the per-item source block line stays 100% verbatim Chinese with its
`> EN:` gloss on the following line. ✅/🟡/❌ feedback prose is English. For `language=双语`, apply
[`exam-cram`](../exam-cram/SKILL.md)'s composition rule (zh unit first, `> EN:` mirror, anchors once).

## Boundaries
- **Structured progress state (A4)**: when `study_state.json` exists it is the SINGLE SOURCE OF TRUTH — update it via `python "${CLAUDE_SKILL_DIR}/scripts/update_progress.py" --workspace <ws> set/add-mistake/add-confusion/render` (script path resolves from the skill package root); `study_progress.md` is a GENERATED view (hand edits are lost on the next render — never hand-patch it). If a state write fails, TELL the user; never continue as if it saved. Without `study_state.json` (no-Python fallback), a hand-maintained md stays valid.

- When the bank holds relevant items, do not write your own. With no stored answer, do not force a verdict — mark ⚠️ or state the limitation plainly.
- Do not judge diagram items from memory — the algorithm-derived standard structure is the reference.
- **Fail-closed on assets**: never ask an item whose `requires_assets=true` or `maybe_requires_assets=true` when a required question-side asset is missing, unreadable, or cannot be displayed (e.g. web-only). A blocked item is skipped, not improvised — choose a full-text item instead. The validator (`scripts/validate_workspace.py`) rejects a workspace whose visual-required item lacks valid question-side asset files, so a clean workspace won't reach you in that state.
- **Use the official visual tools instead of ad-hoc parsing (P0-V2)**: to emit a visual item's prompt-side image Markdown deterministically, run `python <package-root>/scripts/show_question_assets.py --workspace <ws> --id <qid>` (it fail-closes with exit 1 when the contract can't be met — then skip the item). When the student asks visual statistics (e.g. "which chapter has the most figures"), answer on **both metrics** — quiz-bank visual items (`scripts/list_image_questions.py`, per-chapter total × requires × maybe × suspects) AND material figure pages (`scripts/list_figure_pages.py`) — and say which metric is which; if `image_question_index.json` is missing, build it first via `scripts/build_visual_index.py` rather than counting by hand.
