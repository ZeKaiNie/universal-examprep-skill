---
name: exam-ingest
description: >
  从学生上传的课件/大纲/老师勾的重点/真题，一键初始化备考工作区——拼出 ingest.py 所需的
  raw_input.json，运行脚本切出分章节 LLM Wiki、标准题库与进度文件；无 Python 时无感降级为手动写盘。
  当工作区尚未建立、或用户刚提供资料需要冷启动时使用。
license: MIT
---

# exam-ingest — 工作区初始化

## Purpose
Convert scattered prep materials into the fixed workspace structure that `exam-cram` depends on. Build the knowledge base only; do not teach or grade. Produce `references/wiki/`, `references/quiz_bank.json`, `study_plan.md`, and `study_progress.md`, then hand control back to `exam-cram`.

## Activation
Activate when the workspace is missing — that is, any of `references/wiki/`, `references/quiz_bank.json`, or `study_progress.md` is absent. Also activate when the user has just uploaded courseware/syllabus/highlights/past exams, or explicitly requests 「初始化 / 建库 / 开始备考」(initialize / build the bank / start prepping).

## Inputs
- Student-uploaded materials: text, textbook page images, teacher-marked highlights, past exam papers, lecture audio transcripts.
- Target workspace directory (default: current workspace root).

## Workflow
1. Parse the materials. Extract knowledge points, core formulas, high-frequency question types, and term definitions. Group them by chapter or phase.
2. Build `raw_input.json` in the background so it matches `scripts/ingest.py`. Auto-construct an object with `course_name`, `phases[]`, and `quiz_bank[]`, and write it to a temp directory. Never ask the user to write or edit this JSON.
   - Every quiz item MUST carry `chapter` (or `phase`); without it, chapter review cannot retrieve the item. Every item MUST carry `source`: `teacher` (from the teacher/past exams) or `ai_generated` (added by AI).
   - Set each item's type to one of six: `choice / subjective / diagram / fill_blank / true_false / code`.
3. Run the package-root `scripts/ingest.py`: `python <package-root>/scripts/ingest.py --input <temp-json>`. When the full package is installed in Claude Code, `${CLAUDE_SKILL_DIR}` points at the package root — use `python "${CLAUDE_SKILL_DIR}/scripts/ingest.py" --input <…>`.
4. No-Python fallback. If the script fails (e.g. `python is not recognized`), switch immediately and silently: use file-write tools to build, from `templates/`, the files `references/wiki/chN_*.md`, `references/quiz_bank.json`, `study_plan.md`, and `study_progress.md`.
5. Label provenance (canonical labels in [`docs/language-policy.md`](../../docs/language-policy.md)). In wiki paragraphs, distinguish 🟢 来自资料 from 🟡 AI补充，可能与你老师讲的不完全一致. For a question the teacher gave no answer to and AI answers instead, mark the answer ⚠️ AI生成答案，非老师/教材提供.

## Output Contract
- Produce the standard workspace: `references/wiki/`, `references/quiz_bank.json`, `study_plan.md`, `study_progress.md`.
- Emit one setup-receipt line, then hand control back to `exam-cram` for step two (teaching).
- Student-facing output defaults to Simplified Chinese unless the user asks otherwise. The cold-start receipt follows this default; see [`docs/language-policy.md`](../../docs/language-policy.md).

## Student-facing Output
一句话回执（默认简体中文），例：
  `已初始化备考空间：3 章 wiki + 18 道题（含 2 道 ⚠️ AI生成答案，非老师/教材提供），进度已建。下一步开讲第 1 章。`
  然后交回 `exam-cram` 进入第二步授课。

## Boundaries
- `scripts/ingest.py` and `templates/` live at the package root, not inside `skills/exam-ingest/`. If this subskill is installed alone (`CLAUDE_SKILL_DIR` points only at `skills/exam-ingest/`), the script and templates are unavailable — install the whole package (including root `scripts/` and `templates/`), or use the step-4 no-Python fallback to build the workspace by hand.
- Do not modify the logic of `scripts/ingest.py`; only call it.
- Use only safe filenames under `references/wiki/`. The script rejects `../`, absolute paths, and duplicate names.
- Do not fabricate a "standard answer" the teacher did not provide without the ⚠️ label. When materials are insufficient, state the gap honestly.
- Do not overwrite an existing `study_progress.md`. The script does not clear it by default; `--force` backs it up first.
