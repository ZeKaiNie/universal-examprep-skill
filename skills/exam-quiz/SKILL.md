---
name: exam-quiz
description: >
  从 references/quiz_bank.json 抽取本章题目并按标准答案判分，支持选择、主观、画图、填空、判断、代码；
  主观题按 keywords 要点覆盖判分，连续错两次提供提示/跳过/归档。禁止现场编题。用于阶段检查或模考。
license: MIT
---

# exam-quiz — question drilling and grading

## Purpose

Present one chapter/phase-scoped bank item at a time, grade against its stored answer, archive wrong/skipped items through state, and return control to `exam-cram`. Never invent a question or answer.

## Activation

Use after teaching when a checkpoint is needed, or when the student asks for drills or a mock exam.

## Inputs

- Existing `references/quiz_bank.json`, whose items have `type`, answer/provenance fields, and `chapter` or `phase`; subjective items also have `keywords`.
- Current chapter/phase and `study_state.json` mastery/scope. An untagged item cannot enter a chapter checkpoint.
- Optional `difficulty` (1–5) and `difficulty_reason` from `score_difficulty.py`: a structural lower bound, never semantic truth or a per-student score.

## Workflow

1. **Select only eligible bank items.** Filter both `chapter` and `phase`. A missing bank is an incomplete workspace and returns to `exam-ingest`; an existing but empty usable pool produces no substitute and caps completion at `covered_unverified`.

   The default source pool is mixed. Persist a student restriction and select it with `scripts/select_questions.py`; exclude and count items lacking `source_type`. Before any one-turn exception say 「⚠️ 临时覆盖你的 <scope> 范围偏好」 or `⚠️ Temporarily overriding your <scope> scope preference`; do not silently change the stored scope.

   For targeted/checkpoint selection run `python "${CLAUDE_SKILL_DIR}/scripts/select_hard_questions.py" --workspace <ws> --chapter <current> -n <k>`. `--chapter` is the only exact chapter filter; `--from-chapter N` means every numeric chapter ≥N and is only for `shore_up`, never a checkpoint. Explicit cross-chapter practice may omit chapter. The selector combines structural difficulty (using `score_difficulty.py` on the fly when needed) with mistake/confusion/window mastery, mode, and stored scope. `fill_gaps` serves weak points `先易后难`, then mastered items `先难挑战`; `from_scratch` is globally `先易后难`. `shore_up` requires explicit chapter/from-chapter. Ordering is deterministic, not LLM ranking.

2. **Show prompt assets first (fail-closed).** For `requires_assets=true` or `maybe_requires_assets=true`, before asking, explaining, hinting, or solving, actually render every question-side `question_context` / `figure` / `diagram` / `table` asset, labelled `题面图` or `Question-side asset`. A path is not an image. Show `answer_context` / `worked_solution` only later, labelled `答案图` or `Answer-side asset`. Missing/unreadable files block the structured workspace; an existing asset that the UI cannot render causes an item-level skip. Prefer a safe, self-contained `full` item. `stub` and `page_reference` also require the prompt asset or original page first. Use `python <package-root>/scripts/show_question_assets.py --workspace <ws> --id <qid> --lang <zh|en>`; exit 1 means skip. See [`docs/file-format.md`](../../docs/file-format.md) §4.

3. **Grade by type.** `choice`: stored option. `subjective`: required `keywords`/steps with equivalent wording accepted and coverage reported. `fill_blank`: stored fill with valid synonyms. `true_false`: verdict plus one-line reason. `code`: required edits/output. `diagram`: run the standard algorithm from `render_hint`, derive the structure, then compare; teacher convention prevails.

4. **Use the escape hatch.** First wrong answer gets the logic gap, stored explanation, and a hint. On the second consecutive wrong answer offer view hint / skip and archive / continue.

5. **Persist evidence and feedback.** Before any write, if `study_state.json` is absent and Python works, run `python "${CLAUDE_SKILL_DIR}/scripts/update_progress.py" --workspace <ws> init`; only when Python truly cannot run may the generated Markdown be maintained directly. For every handled item record `record-phase-evidence --kind checkpoint --ref <qid> --outcome passed|wrong|skipped`; an ID alone is not mastery. Wrong/skipped items also use `python "${CLAUDE_SKILL_DIR}/scripts/update_progress.py" --workspace <ws> add-mistake --id <qid> --chapter <ch> --note <reason>`. A nonzero state command is a fail-loud write error, not permission to edit the generated view.

   Before replying, pipe full verdict, gap, explanation, and source line to `python "${CLAUDE_SKILL_DIR}/scripts/notebook.py" --workspace <ws> add-entry --chapter <ch> --type feedback --id <qid> --title <gist>`. Same chapter/id replaces in place. Wrong/skipped feedback also passes `--mistake` to mirror `mistakes/chNN.md`; that supplements, never replaces, the state row. Then send a short digest and language-pack link. If notebook writing fails, say so and give the full feedback in chat; file-less clients use chat/text breakpoints.

6. **End every graded item with one source line:** `题目来源：<file/page/source_type>｜答案来源：<material/AI>｜<label>` or `Question source: <...> | Answer source: <...> | <label>`. Missing metadata says 「来源未知」 / `Source unknown` (or `Source page unknown`), never an invented filename/page. The label is one complete canonical sentence from [`docs/language-policy.md`](../../docs/language-policy.md): 🟢 来自资料; 🟡 AI补充，可能与你老师讲的不完全一致; or ⚠️ AI生成答案，非老师/教材提供, with its English counterpart. When no material answer exists, both the `解析/参考答案` title and source line carry the full ⚠️ sentence; without a stored answer, do not force a verdict.

## Output Contract

- One item at a time; pass/not-pass plus key-point feedback; finish with the source line and refreshed progress panel.
- Persist feedback before the digest; wrong/skipped items need checkpoint evidence, state mistake row, and notebook mistake mirror.
- `exam-cram` / `exam-tutor`, not this skill, calls evidence-gated `complete-phase`.
- Student prose follows the persisted language with single-language purity: English by default, Simplified Chinese if the opening was Chinese, or explicit bilingual blocks.

## Language packs

Load before student-visible output:

- `中文` → [`../../locales/zh/skills/exam-quiz.md`](../../locales/zh/skills/exam-quiz.md)
- `English` → [`../../locales/en/skills/exam-quiz.md`](../../locales/en/skills/exam-quiz.md)
- `双语` → compose both blockwise, zh then `> EN:`, under [`docs/language-policy.md`](../../docs/language-policy.md)

Display aliases are normalized to `zh`, `en`, or `bilingual`; unset language follows the merged first ask.

## Boundaries

- `study_state.json` is the source of truth. Update it only via `python "${CLAUDE_SKILL_DIR}/scripts/update_progress.py" --workspace <ws> ...`; `study_progress.md` is generated. Fail writes loudly; initialize state whenever Python works.
- Never create a replacement item, invent a source/answer, grade a diagram from memory, or serve a visual-dependent prompt whose image was not shown.
- For visual statistics, report both quiz-bank visual items via `scripts/list_image_questions.py` (total/requires/maybe/suspects) and material figure pages via `scripts/list_figure_pages.py`. If the index is absent, build it with `scripts/build_visual_index.py`; never count by hand.
