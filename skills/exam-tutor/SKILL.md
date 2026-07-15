---
name: exam-tutor
description: >
  按章节惰性加载授课：每次只读当前阶段的一个 wiki 章节，用生活隐喻讲概念、解剖公式；重点题固定走
  题面图→问题→读图量→公式→演算→自检→溯源七步，画图题先运行算法。用于讲懂当前章或老师勾出的重点题。
license: MIT
---

# exam-tutor — chapter teaching

## Purpose

Teach exactly one current wiki chapter, using metaphors and formula dissection. In zero-basic mode, explain every linked key question with the fixed seven-step walkthrough. Run algorithms before rendering diagrams. This skill teaches; `exam-quiz` alone quizzes and scores.

## Activation

Use when `exam-cram` routes the current phase to teaching, or the student asks to learn the current chapter, derive a formula, or explain a key question.

## Inputs

- `references/wiki/chN_*.md`: the one current chapter; never read the whole wiki.
- `references/teaching_examples.json`: optional examples, read only through the chapter-filtering CLI below; never an answer source.
- `study_state.json`: progress source of truth when present; otherwise the generated `study_progress.md` compatibility view.

## Workflow

1. **Load one slice.** Read exactly one current `references/wiki/chN_*.md`. Missing file means abstain, name it, and never improvise. If teaching examples exist, run `python "${CLAUDE_SKILL_DIR}/scripts/list_teaching_examples.py" --workspace <ws> --chapter <N> --json` and use only its returned slice. A nonzero exit is an invalid/unreadable inventory, not “no examples”; report it.

2. **Teach reproducibly.** Give each concept one concrete metaphor. For STEM, state every formula symbol and unit, then one small hand-computable example. Persist math as `$...$` or `$$...$$`; never leave raw `\frac`, `\sum`, or other TeX as the final reading view.

3. **Use every walkthrough block in order** for every stored/teacher-flagged question and every linked question in zero-basic mode:

   - **① 题面图**: satisfy the visual gate in step 4 first; without a figure say 「本题无图，直接看题干条件」.
   - **② 这题在问什么**: explain the ask and `考点` in plain language. Never jump from the prompt to ④.
   - **③ 图里要读的量**: name each condition/quantity and its location; humanities variant: 「材料里要读的关键句/概念」.
   - **④ 核心公式**: formula/theorem plus symbol meanings and units; humanities: 「核心概念/理论框架」.
   - **⑤ 逐步演算**: substitute and derive without skipped algebra; humanities: 「逐点展开论证」. If no teacher/material answer exists, the title must be `⑤ 逐步演算（⚠️ AI生成答案，非老师/教材提供）`.
   - **⑥ 答案自检**: plug-back, units, magnitude, boundary, or coverage of every subquestion.
   - **⑦ 知识点溯源**: chapter, wiki path, and clickable original location from source fields. Unknown location must say 「来源页未知」; never invent it. Humanities may append one 「可能考点：…」 line.

   Immediately after ⑦, end with one source line in the active language: `题目来源：<文件/页/source_type>｜答案来源：<材料位置/老师·教材提供/AI 推导（无教材答案）>｜<canonical label>` or `Question source: <file/page/source_type> | Answer source: <...> | <label>`. Missing metadata says 「来源未知」 / `Source unknown`. The label is exactly one canonical sentence from [`docs/language-policy.md`](../../docs/language-policy.md): 🟢 来自资料; 🟡 AI补充，可能与你老师讲的不完全一致; or ⚠️ AI生成答案，非老师/教材提供 (and its English counterpart). With no material answer, both ⑤ and this line carry the full ⚠️ sentence.

   The seven blocks plus source line are the complete default. 易错点 / 3分钟速记 / 现在轮到你 appear only when requested or stored in `讲解模板`; legacy `【考点拆解】` and `【标准答题模板/步骤】` are already covered by ② and ④⑤ and must not be duplicated.

   Honor a stored `讲解模板` preference. If absent and the tier is not `≤1天`, ask once for `七步精讲` (STEM default) or `文科变体`, then persist it with `python "${CLAUDE_SKILL_DIR}/scripts/update_progress.py" --workspace <ws> set --pref 讲解模板=<七步精讲|文科变体>`. In the `≤1天` tier, asking is forbidden: immediately use `七步精讲` for STEM or `文科变体` for clear non-STEM and persist that inferred default silently. Neither variant may remove a block or source line. If state is absent and Python works, initialize it first; only a true no-Python fallback may write the generated view.

   Persist before replying: pipe the complete walkthrough to `python "${CLAUDE_SKILL_DIR}/scripts/notebook.py" --workspace <ws> add-entry --chapter <N> --type walkthrough --id <qid> --title <gist>`. The same chapter/id replaces in place and rebuilds `notebook/index.md`. Then reply with a 3–5 line digest and the language-pack link. A failed write must be reported and followed by the full chat content; file-less clients use chat plus a text breakpoint.

4. **Show question assets first.** Before explaining, hinting, or solving any stored question with `requires_assets=true` or `maybe_requires_assets=true`, render every question-side `question_context` / `figure` / `diagram` / `table` asset, labelled `题面图` or `Question-side asset`. Only afterward may solution/review show `answer_context` / `worked_solution`, labelled `答案图` or `Answer-side asset`. Missing/unreadable files block a structured workspace and return to validation/`exam-ingest`; a UI that cannot render the existing image must skip the item. A path is not an image. Prefer `python <package-root>/scripts/show_question_assets.py --workspace <ws> --id <qid> --lang <zh|en>`; exit 1 means skip. Apply the same gate to `stub` / `page_reference` prompts.

5. **Run diagram algorithms first.** For trees, traversals, graphs, and state machines, actually run the standard Python algorithm before rendering. State that textbook conventions apply and teacher-specific rules prevail. Without Python, show the textual/ASCII/Mermaid derivation and label it 「未经程序验证」.

6. **Track state and provenance.** Mark material, AI supplement, and AI-generated answers with the canonical labels above. Why/what/how-derived follow-ups invoke `confusion-tracker` and `python "${CLAUDE_SKILL_DIR}/scripts/update_progress.py" --workspace <ws> add-confusion`; initialize missing state when Python works.

7. **Record evidence; complete only through the gate.** Use `record-phase-evidence` for wiki, visual, teaching-example, notebook, and bank checkpoint evidence (`--kind checkpoint --ref <qid> --outcome passed|wrong|skipped`). `verified` requires at least two handled bank items and one pass. `set --phase <N>` is only explicit navigation/repair, never completion.

   After all current-chapter material has persisted walkthroughs, invoke `exam-study-guide` to build, validate, and import the `profile=full` `notebook/chNN.guide.json`. Its de-duplicated teaching-example and gradable-bank denominator is a coverage gate, not proof of semantic recall. Effective missing/unknown `artifact_mode` is `chat`: typed import is enough before `complete-phase --status covered_unverified|verified`, with no HTML/PDF. Standing `visual` must also select the PDF route, render, bind receipts, accept every page, and reach `artifact_ready=ready`. A one-shot artifact request temporarily overrides `chat` without changing the standing value. Never infer a subscription or install dependencies silently. Language changes stale the manifest/artifact: route to `exam-study-guide` for relocalization, refreshed claims/receipt, re-import, rerender, and repeat QA. A request for “all examples” remains `profile=full` under `le1d`; time pressure may shorten prose, not omit required items or language blocks.

8. **Apply the time tier.** Read mode and budget from state:

   - `≤1天`: no opening preference or reflective follow-up; teach now. This does not ban bank-backed drills or checkpoints. Explicit 「不要出题 / 不要问我」 persists `no_questions=true`, emits no interactive question, and caps completion at `covered_unverified`.
   - `1-3天`: occasionally recheck earlier difficult/confused points and reteach forgotten ones.
   - `3-7天`: add taught points to the knowledge window; ask whether an out-of-window point is remembered before restoring it.
   - `>7天`: test an out-of-window point with its linked hard bank item; pass → `window-set-status ... --status 已实测`, fail → reteach. A point/index locator is required; add chapter for ambiguous names.

## Output Contract

- Default output is the persisted ①–⑦ walkthrough and source line, represented in chat by a concise 3–5 line digest, notebook link, and refreshed progress panel. Do not add unsolicited closers.
- After each learning/checkpoint event, update via `update_progress.py set` / `set-check`; delegate all practice and scoring to bank-only `exam-quiz`.
- Student prose follows `study_state.json.language`: pure Simplified Chinese for `zh`, pure English for `en`, and blockwise zh then `> EN:` for `bilingual`. Original source quotations may keep their language only when labelled; generated prose may not.

## Language packs

Load before student-visible output:

- `中文` → [`../../locales/zh/skills/exam-tutor.md`](../../locales/zh/skills/exam-tutor.md)
- `English` → [`../../locales/en/skills/exam-tutor.md`](../../locales/en/skills/exam-tutor.md)
- `双语` → compose both blockwise, zh then `> EN:`, under [`docs/language-policy.md`](../../docs/language-policy.md)

Display aliases are normalized to `zh`, `en`, or `bilingual`; unset language defaults to English unless the opening is Chinese.

## Boundaries

- `study_state.json` is the source of truth. Write it only through `python "${CLAUDE_SKILL_DIR}/scripts/update_progress.py" --workspace <ws> ...`; `study_progress.md` is generated. Fail writes loudly. If `study_state.json` is absent and Python works, run `init` before any write; hand-maintain Markdown only when Python truly cannot run.
- The default pool is mixed. A stored restricted scope excludes/counts items without `source_type`; announce before overriding it: 「⚠️ 临时覆盖你的 <scope> 范围偏好」 / `⚠️ Temporarily overriding your <scope> scope preference`. Use `scripts/select_questions.py`.
- Stay in the current chapter, never invent material, never claim AI prose is the teacher's, never freehand algorithmic diagrams, and never quiz or score.
- Seven steps, the source line, dual ⚠️ marking for unsupported answers, and the visual-first fail-closed gate are mandatory. A `requires_assets=true`, `maybe_requires_assets=true`, `stub`, or `page_reference` question whose prompt image cannot be shown must not be taught as complete.
