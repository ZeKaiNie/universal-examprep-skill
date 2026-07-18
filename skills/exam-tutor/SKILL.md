---
name: exam-tutor
description: >
  按章节惰性加载授课：每次只读当前阶段的一个 wiki 章节，用生活隐喻讲概念、解剖公式；重点题固定走
  题面图→问题→读图量→公式→演算→答案详解→溯源七步，画图题先运行算法。用于讲懂当前章或老师勾出的重点题。
license: MIT
---

# exam-tutor — chapter teaching

## Purpose

Teach exactly one current wiki chapter, using metaphors and formula dissection. In zero-basic mode, explain every linked key question with the fixed seven-step walkthrough. Run algorithms before rendering diagrams. This skill teaches; `exam-quiz` alone quizzes and scores.

## Activation

Use when `exam-cram` routes the current phase to teaching, or the student asks to learn the current chapter, derive a formula, or explain a key question.

## Inputs

- In `processing_mode=lightweight`: one schema-3 visually accepted current-page batch
  from `.lightweight/session.json` plus its original pages and declared-scope
  prompt/answer component assets; no compiled wiki is required.
- `references/wiki/chN_*.md`: the one current chapter; never read the whole wiki.
- `references/teaching_examples.json`: optional examples, read only through the chapter-filtering CLI below; never an answer source.
- `study_state.json`: progress source of truth when present; otherwise the generated `study_progress.md` compatibility view.

## Workflow

1. **Load one slice.** Read `study_state.json.processing_mode` first. In
   `lightweight`, call `lightweight_session.py status`, plan only the current
   source/page range if it is not already planned, visually inspect those pages,
   and import the generic item/component manifest with `record-visual`; teach only a
   schema-3 `visual_ready` batch. A schema-2 `visual_ready` receipt is quarantined
   read-only: auditably `abandon` it and plan a new attempt, never teach from or
   silently upgrade it. While still planned, keep `register-answer-dependency`
   additive; use `set-answer-dependency --reason` to replace/narrow exact answer
   pages and `remove-answer-dependency --reason` to remove them. Do not call
   ingestion/OCR, preload later pages, or require a
   wiki. In `full`, read exactly one current `references/wiki/chN_*.md`. A missing
   full-mode file means abstain, name it, and never improvise. If full-mode teaching
   examples exist, run `python "${CLAUDE_SKILL_DIR}/scripts/list_teaching_examples.py"
   --workspace <ws> --chapter <N> --json` and use only its returned slice. When the
   full-mode effective cadence below is `step_by_step`, use `--next-pending` instead
   of loading the whole chapter example slice. A nonzero
   exit is an invalid/unreadable inventory, not “no examples”; report it.

2. **Teach reproducibly.** Give each concept one concrete metaphor. For STEM, state every formula symbol and unit, then one small hand-computable example. Persist math as `$...$` or `$$...$$`; never leave raw `\frac`, `\sum`, or other TeX as the final reading view.

3. **Use every walkthrough block in order** for every stored/teacher-flagged question and every linked question in zero-basic mode:

   **Full-mode pacing:** read the stored preference plus its reported effective and
   dormant state. `study_state.json.preferences.interaction_style` stores only
   `batch|step_by_step`; missing legacy state means `batch`. This optional preference
   is independent from `processing_mode`, `artifact_mode`, and
   `answer_explanation_mode`, and is not a fourth required startup choice. Persist an
   explicit change only with `update_progress.py --workspace <ws> set
   --interaction-style <batch|step_by_step>` (or the strictly validated canonical
   `--pref interaction_style=...`). It never changes the lightweight page-batch route.

   - Effective `batch`: use the normal full-mode flow. A true
     `preferences.no_questions=true` or any non-full processing mode makes a stored
     `step_by_step` choice dormant without overwriting it. A stored `batch` choice
     remains ordinary batch cadence.
   - Effective `step_by_step`: call `list_teaching_examples.py --workspace <ws>
     --chapter <N> --next-pending --json`. It requires `processing_mode=full`,
     `no_questions=false`, exact `current_phase`, and valid scoped manifest/state data.
     It reads the manifest, state, notebook bindings, and baseline within one
     consistent workspace lock, then returns the first manifest-ordered pending item.
     Unbound IDs already present in `phase_evidence[N].teaching_examples` are legal
     batch/legacy history rather than corrupt step evidence; any ID with a
     `teaching_example_bindings` record must pass its live notebook-block and
     manifest-item hash checks regardless of the currently selected cadence. Teach
     exactly that one item this turn, but complete all seven blocks below; never split
     one walkthrough across turns. Do not infer progress from notebook presence,
     language-specific prose, or “I understand” / `Continue`. Report
     `unexpected_evidence` separately. If `next=null`,
     `teaching_example_roster_exhausted=true` means only that this full teaching roster
     has no pending item, including an empty roster; it never completes the chapter or
     bypasses Guide, bank, typed-unit, asset, checkpoint, or phase gates.
     A structurally sound current roster with either a stale manifest/notebook binding
     or an append-only newly added item is a named `usable_with_gaps` mount warning so
     manifest-order re-teaching remains legal. Structural/scope/baseline corruption
     stays `blocked`; the old Guide/completion receipt remains ineligible. Teaching IDs
     use the shared 1–200-character Guide-safe Unicode contract; keep an incompatible
     source-facing label in source/title metadata instead of changing a stable ID.

   - **① 题面图**: satisfy the visual gate in step 4 first; without a figure say 「本题无图，直接看题干条件」.
   - **② 这题在问什么**: explain the ask and `考点` in plain language. Never jump from the prompt to ④.
   - **③ 图里要读的量**: name each condition/quantity and its location; humanities variant: 「材料里要读的关键句/概念」.
   - **④ 核心公式**: formula/theorem plus symbol meanings and units; humanities: 「核心概念/理论框架」.
   - **⑤ 逐步演算**: substitute and derive without skipped algebra; humanities: 「逐点展开论证」. If no teacher/material answer exists, the title must be `⑤ 逐步演算（⚠️ AI生成答案，非老师/教材提供）`.
   - **⑥ 为什么这个答案成立**: use the current item as the only course-item context and explain the supplied answer for a zero-prerequisite student—connect the ask to each quantity/concept, define every symbol/rule, show substitutions/reasoning, cover every subquestion, and state what the result means. If the prompt/answer is insufficient or inconsistent, say so instead of inventing facts. Do not add a generic answer-self-check panel.
   - **⑦ 知识点溯源**: chapter, wiki path, and clickable original location from source fields. Unknown location must say 「来源页未知」; never invent it. Humanities may append one 「可能考点：…」 line.

   Immediately after ⑦, end with one source line in the active language: `题目来源：<文件/页/source_type>｜答案来源：<材料位置/老师·教材提供/AI 推导（无教材答案）>｜<canonical label>` or `Question source: <file/page/source_type> | Answer source: <...> | <label>`. Missing metadata says 「来源未知」 / `Source unknown`. The label is exactly one canonical sentence from [`docs/language-policy.md`](../../docs/language-policy.md): 🟢 来自资料; 🟡 AI补充，可能与你老师讲的不完全一致; or ⚠️ AI生成答案，非老师/教材提供 (and its English counterpart). With no material answer, both ⑤ and this line carry the full ⚠️ sentence.

   The seven blocks plus source line are the complete default. 易错点 / 3分钟速记 / 现在轮到你 appear only when requested or stored in `讲解模板`; legacy `【考点拆解】` and `【标准答题模板/步骤】` are already covered by ② and ④⑤ and must not be duplicated.

   Honor a stored `讲解模板` preference. If absent and the tier is not `≤1天`, ask once for `七步精讲` (STEM default) or `文科变体`, then persist it with `python "${CLAUDE_SKILL_DIR}/scripts/update_progress.py" --workspace <ws> set --pref 讲解模板=<七步精讲|文科变体>`. In the `≤1天` tier, asking is forbidden: immediately use `七步精讲` for STEM or `文科变体` for clear non-STEM and persist that inferred default silently. Neither variant may remove a block or source line. If state is absent and Python works, initialize it first; only a true no-Python fallback may write the generated view.

   Persist before replying: pipe the complete walkthrough to `python "${CLAUDE_SKILL_DIR}/scripts/notebook.py" --workspace <ws> add-entry --chapter <N> --type walkthrough --id <qid> --title <gist>`. Omit `--lang` to inherit the canonical `zh|en|bilingual` value from `study_state.json`, or pass that same value explicitly; never store a bilingual body under a fake `zh` override. The same chapter/id replaces in place and rebuilds `notebook/index.md`. For effective full-mode `step_by_step`, add `--teaching-example`; after that succeeds, use only `update_progress.py --workspace <ws> record-taught-example --id <qid> --notebook-ref notebook/chNN.md#<anchor>`. The latter validates full/current phase, manifest membership and order, exact anchor, walkthrough type, matching ID, and the reserved marker, then atomically writes the ID, notebook anchor, and one `teaching_example_bindings` object containing exactly `id`, `notebook_ref`, `notebook_block_sha256`, and `manifest_item_sha256`. Never replace this with two loose `record-phase-evidence` writes. Acknowledgement/Continue is routing input only. Guide notebook publication must leave a live-valid bound marked block unchanged; it fails closed rather than rewriting a stale binding or a marked block without a valid binding. Then reply with a 3–5 line digest and the language-pack link. In effective `step_by_step`, append the active-language continuation wording outside the persisted walkthrough; bilingual renders Chinese then its pure-English `> EN:` mirror. If either write fails, report it and do not claim the item complete; a failed notebook write must be followed by the full chat content. File-less clients use chat plus a text breakpoint.

4. **Show question assets first.** Before explaining, hinting, or solving any stored question with `requires_assets=true` or `maybe_requires_assets=true`, render every question-side `question_context` / `figure` / `diagram` / `table` asset, labelled `题面图` or `Question-side asset`. Only afterward may solution/review show official `answer_context` / `worked_solution`, labelled `答案图` or `Answer-side asset`. Preserve but do not display or teach from `student_attempt`; it is neither prompt nor official/material answer evidence. Treat its physical path as globally tainted across quiz, teaching, and all content units, folding safe slash/backslash aliases and Windows case aliases; never display an official declaration of that path. Reject same-item prompt/answer reuse. Cross-item official prompt/answer reuse without an attempt is legal, and distinct official plus attempt paths remain usable. Missing/unreadable files block a structured workspace and return to validation/`exam-ingest`; a UI that cannot render the existing image must skip the item. A path is not an image. Prefer `python <package-root>/scripts/show_question_assets.py --workspace <ws> --id <qid> --lang <zh|en>`; exit 1 means skip. Apply the same gate to `stub` / `page_reference` prompts.

   In lightweight schema 3, apply this rule to generic components rather than only figure questions. Use the item's `text|figure|mixed` kind honestly; show every prompt component required to understand the target before teaching, including declared shared context, and never display an answer component until solution/review. A detail call may combine prompt components only for the same target. Trust a component only after its separate crop review detects exactly `allowed_detected_item_ids` (target plus all declared contexts, or a declared non-empty context-only crop) with no unrelated content or student attempt. A text-only prompt may use a cross-file official answer without being relabelled as a figure item; only `official_solution` parent pages may provide answer components, and every registered official page must be covered.

5. **Run diagram algorithms first.** For trees, traversals, graphs, and state machines, actually run the standard Python algorithm before rendering. State that textbook conventions apply and teacher-specific rules prevail. Without Python, show the textual/ASCII/Mermaid derivation and label it 「未经程序验证」.

6. **Track state and provenance.** Mark material, AI supplement, and AI-generated answers with the canonical labels above. Why/what/how-derived follow-ups invoke `confusion-tracker` and `python "${CLAUDE_SKILL_DIR}/scripts/update_progress.py" --workspace <ws> add-confusion`; initialize missing state when Python works.

7. **Record evidence; complete only through the gate.** Use `record-phase-evidence` for wiki, visual, notebook, and bank checkpoint evidence (`--kind checkpoint --ref <qid> --outcome passed|wrong|skipped`). Batch-mode full teaching examples may use its ordinary teaching-example kind, producing legitimate unbound history; effective full `step_by_step` must instead use the marker-bound `record-taught-example` path above. Bound history remains live-validated after switching back to batch. Every ID retained by `teaching_baseline.json` must have a current `teaching_examples.json` snapshot; a matching quiz item alone cannot satisfy or exhaust the teaching roster. `verified` requires at least two handled bank items and one pass. `set --phase <N>` is only explicit navigation/repair, never completion.

   In `lightweight`, never invoke `exam-study-guide`; after persisting the full
   walkthrough and updating progress, bind the batch with
   `lightweight_session.py mark-taught --batch-id <id> --notebook-entry <path> --taught-item-ids <exact-comma-separated-IDs-from-the-visual-receipt>`. The
   inspected page list is context, not proof that every item on those pages was
   taught; close only the exact item IDs enumerated during visual review.
   Plan the next pages only when the learner reaches them. Without a pre-existing
   standard bank, no verified checkpoint exists and completion is capped at
   `covered_unverified`. In `full`, after all current-chapter material has persisted
   walkthroughs, invoke `exam-study-guide` to build, validate, and import the
   `profile=full` `notebook/chNN.guide.json`. Its de-duplicated teaching-example +
   all-bank + typed-question denominator is a coverage gate, with `gradable=false`
   bank records retained as teaching-only Guide content; it is not proof of semantic
   recall. Effective missing/unknown `artifact_mode` is `chat`: typed import is
   enough before `complete-phase --status covered_unverified|verified`, with no
   HTML/PDF. Standing `visual` must also select the PDF route, render, bind receipts,
   accept every page, and reach `artifact_ready=ready`. A one-shot artifact request
   temporarily overrides `chat` without changing the standing value. Never infer a
   subscription or install dependencies silently. Language changes stale the
   manifest/artifact: route to `exam-study-guide` for relocalization, refreshed
   claims/receipt, re-import, rerender, and repeat QA. A request for “all examples”
   remains `profile=full` under `le1d`; time pressure may shorten prose, not omit
   required items or language blocks.

8. **Apply the time tier.** Read mode and budget from state:

   - `≤1天`: no opening preference or reflective follow-up; teach now. This does not ban bank-backed drills or checkpoints. Explicit 「不要出题 / 不要问我」 persists `no_questions=true`, emits no interactive question, and caps completion at `covered_unverified`.
   - `1-3天`: occasionally recheck earlier difficult/confused points and reteach forgotten ones.
   - `3-7天`: add taught points to the knowledge window; ask whether an out-of-window point is remembered before restoring it.
   - `>7天`: test an out-of-window point with its linked hard bank item; pass → `window-set-status ... --status 已实测`, fail → reteach. A point/index locator is required; add chapter for ambiguous names.

## Output Contract

- Default output is the persisted ①–⑦ walkthrough and source line, represented in chat by a concise 3–5 line digest, notebook link, and refreshed progress panel. Do not add unsolicited closers.
- In effective full-mode `step_by_step`, append the language-pack continuation prompt after the digest, never inside the persisted walkthrough. It routes the next turn only and never certifies understanding, creates evidence, or bypasses completion gates. Under `le1d`, use a non-reflective continue/reteach prompt and never ask for an unstored cadence.
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
- `interaction_style` is a full-mode teaching-manifest cadence only. Its stored value is exactly `batch|step_by_step`; step mode is effective only in full with `no_questions=false`, otherwise the stored step choice is dormant and effective cadence is batch. Stable item IDs mean a reply-language change does not automatically requeue already evidenced items; request an explicit reteach. The selector takes a consistent workspace-locked snapshot and returns manifest order, but it is not a pause/acknowledgement or reservation ledger, so concurrent tutors may still select the same pending item.
