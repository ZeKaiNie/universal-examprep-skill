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
   - **PDF / 材料文件夹 → 用官方入口，不要手写临时解析脚本**：当输入是一**文件夹的讲义/作业 PDF** 时，运行 `python <package-root>/scripts/build_raw_input_from_workspace.py --materials <dir> --out raw_input.json --asset-root <workspace>/references/assets --report parse_report.json`。它会保留**原页出处**（`source_file`/`source_pages`）、把依赖图的页**整页渲染成 PNG asset**、抽取讲义 **Example/Quiz 的题—解对**进题库，并产出**解析报告**（提取/跳过/警告及所用后端）。PDF 文本/渲染是**可选依赖**——文本 `pip install pypdf`，渲染 `pip install pymupdf`（自带 PNG）或 `pypdfium2 Pillow`；缺依赖时脚本会**清晰报错**告诉你装什么。纯 `.txt/.md` 材料无需任何依赖。`--asset-root` 应指向 `<workspace>/references/assets`（渲染开启而未指定时 auto 跳过并告警、required 报错）。读 `parse_report.json` 的警告（如 `likely_asset_required_but_no_image`、`answer_spans_multiple_files`）以决定是否补装渲染后端或人工核对。详见 [`docs/file-format.md`](../../docs/file-format.md) §4。
3. Run the package-root `scripts/ingest.py`: `python <package-root>/scripts/ingest.py --input <temp-json>`. When the full package is installed in Claude Code, `${CLAUDE_SKILL_DIR}` points at the package root — use `python "${CLAUDE_SKILL_DIR}/scripts/ingest.py" --input <…>`.
4. No-Python fallback. If the script fails (e.g. `python is not recognized`), switch immediately and silently: use file-write tools to build, from `templates/`, the files `references/wiki/chN_*.md`, `references/quiz_bank.json`, `study_plan.md`, and `study_progress.md`.
5. Label provenance (canonical labels in [`docs/language-policy.md`](../../docs/language-policy.md)). In wiki paragraphs, distinguish 🟢 来自资料 from 🟡 AI补充，可能与你老师讲的不完全一致. For a question the teacher gave no answer to and AI answers instead, mark the answer ⚠️ AI生成答案，非老师/教材提供.

## Output Contract
- Produce the standard workspace: `references/wiki/`, `references/quiz_bank.json`, `study_plan.md`, `study_progress.md`.
- The quiz_bank schema supports **asset-aware fields** for **image-dependent lecture questions** (e.g. a Venn diagram shown on a slide, a page figure/table): `source_file`/`source_pages`, `assets` (under `references/assets/`), `requires_assets`, `maybe_requires_assets`, `question_text_status`. When a question depends on a figure that isn't transcribed into text: **only set `requires_assets=true` or `maybe_requires_assets=true` when you actually attach a valid question-side image asset** under `references/assets/` (the validator rejects visual-required items whose asset is missing/unreadable or answer-side-only). **Note `scripts/ingest.py` only writes `references/wiki/`, `quiz_bank.json`, and the plan/progress files — it does NOT create or copy `references/assets/`.** So if you reference an asset, **you must write the image file under `<workspace>/references/assets/` yourself** (file-write tools) before/after running ingest; otherwise leave the visual-required fields unset/false. If you have **only a source-page reference and no image**, set `question_text_status="page_reference"` with `source_file`+`source_pages` and **leave `requires_assets` / `maybe_requires_assets` unset** — the page reference tells the tutor to surface the page without the hard asset requirement. (The official builder takes the **opposite, fail-closed** stance for a figure it *detected but couldn't render*: it keeps `requires_assets=true` with the missing asset recorded, so the workspace won't validate until you install a render backend or supply the image. Both are intentional — hand-authoring stays graceful and never emits an invalid workspace, while the builder forces a genuinely-needed figure to surface rather than silently dropping it.) These fields are **optional and backward-compatible** (old banks stay valid); the official builder `scripts/build_raw_input_from_workspace.py` emits them from PDF material (see Workflow step 2). See [`docs/file-format.md`](../../docs/file-format.md) §4.
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
