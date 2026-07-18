# Runtime workspace contract

This compact reference is the student-package edition of `file-format.md`. The source checkout
keeps the exhaustive schema/audit reference; shipped validators and writers are the executable
authority.

## 1. Ownership and layout

- `study_state.json` is learning-state truth; `study_progress.md` is generated. Mutate them only
  through `update_progress.py`. `exam_runtime_receipt.json` binds the exact runtime and workspace.
- In full mode, `.ingest/` holds parser receipts, source/unit facts, review ledgers, generation
  blockers, claims, and build manifests. Never hand-edit them. Pending transactions, unresolved
  conflicts, stale hashes, missing receipts, and revision drift fail closed.
- `references/wiki/`, `quiz_bank.json`, `teaching_examples.json`, indexes, and assets are compiled
  views. `notebook/` is durable teaching evidence; `study_guide/` holds optional visual artifacts.
- Use contained workspace-relative paths. Files, parent directories, and temporary targets must be
  regular non-link/reparse entries.

## 2. Questions and teaching evidence

`quiz_bank.json` is a JSON array. Each item needs a stable unique `id`, supported `type`, prompt,
chapter/phase scope, and either a provenance-labelled answer or explicit unknown status. Quizzes
draw and grade only these items; never invent an official answer.

`teaching_examples.json` is an ordered teaching roster, not a quiz pool. Full-mode step-by-step
progress is recorded only by `record-taught-example`, binding the item ID to its exact manifest
snapshot and marked notebook block. Notebook presence or a student's “Continue” is not evidence.

## 3. Visible assets and provenance

- Show every prompt-side `question_context`, `figure`, `diagram`, and `table` before asking,
  hinting, explaining, or solving. A printed path is not a rendered image.
- Show official `answer_context` and `worked_solution` assets only during the solution.
  `student_attempt` is never official evidence and taints that physical path across all aliases.
- One item cannot reuse one physical file on prompt and answer sides. Missing, unreadable,
  unrenderable, escaped, or tainted required evidence causes a fail-closed skip/block.
- End teaching and grading with the active-language question source, answer source, and canonical
  material/AI label. Unknown provenance remains explicitly unknown.

## 4. Full ingestion and validation

Use `ingest_course.py` only after exact path/runtime confirmation and explicit full mode. Core
adapters cover PDF, DOCX, PPTX, XLSX, common standalone raster, text, and Markdown as reported by
dependency preflight. Parser receipts bind source hash, adapter/config, location accounting, and
`network=false`, `upload=false`, `install=false` declarations. Heavy parsers remain explicit,
consented remote-host extensions.

Interpret outcomes literally: exit `0` is `ready` or `usable_with_gaps`; exit `10` is a completed
but `blocked` build; other nonzero exits are operation failures. Read every warning and typed review
issue. Study Guide claims and target-scoped crops must match current source/unit/asset hashes before
validation, rendering, or phase completion.
