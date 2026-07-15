---
name: exam-ingest
description: >
  从学生上传的课件/大纲/老师勾的重点/真题，一键初始化并验证备考工作区：解析 PDF、DOCX、PPTX、
  txt/md，建立分章节 LLM Wiki、标准题库、结构化接管队列与进度状态；仅在 Python 确实无法运行时
  明确降级为手动写盘。当工作区尚未建立、资料发生变化、或建库 readiness 被阻断时使用。
license: MIT
---

# exam-ingest — validated workspace initialization

## Purpose

Convert a confirmed materials folder into a validated cram workspace. Build and repair the knowledge base only; do not teach or grade. The normal path produces structured ingestion facts under `.ingest/`, compiled chapter wiki and bank files, progress state, visual evidence, and an explicit readiness verdict before handing control back to `exam-cram`.

## Activation

Activate when the confirmed workspace lacks its wiki, bank, or progress state; when the student supplies new/changed course materials; or when `validate_workspace.py` reports ingestion readiness `blocked`. Do not treat the mere existence of generated files as proof that the workspace is ready.

## Inputs

- A student-confirmed materials directory containing PDF, DOCX, PPTX, txt, or Markdown; photos/scans, damaged/encrypted files, unsupported formats, and ambiguous problem/solution pairs may require evidence-backed AI/human review.
- A target workspace directory explicitly confirmed by the student. Never default to the repository, process current directory, or an inferred course folder. The workspace must be separate from the materials tree so reruns cannot ingest generated outputs. If no workspace is confirmed, use `update_progress.py workspace-list --json`, then ask the student to select or provide one before writing anything.

## Workflow

1. **Pass the executable start gate, then use the official ingestion entry.** The exact materials/workspace pair and all three choices must already have been persisted with `exam_start.py confirm` as specified by `exam-cram`; a bare registry row or `update_progress.py set` is insufficient. Verify read-only with `exam_start.py status --materials <dir> --workspace <ws> --json`, then run from the package root:

   ```text
   python scripts/ingest_course.py --materials <dir> --workspace <ws> --json [--course-name <name>] [--lang zh|en] [--artifact-mode chat|visual]
   ```

   The orchestrator performs dependency preflight, deterministic extraction, provenance-preserving structured compilation, state initialization, visual indexing/repair, and canonical workspace validation. It never installs a dependency. Pass `--artifact-mode` only for an explicit standing student choice; omit it to retain the existing/default `chat` preference.
2. **Interpret process and readiness separately.** Exit `0` means the engineering process completed and the JSON readiness is `ready` or `usable_with_gaps`; preserve and report any warnings in the latter. Exit `10` means `process_success=true` but `readiness=blocked`: do not teach, quiz, or claim completion. Any other nonzero is a dependency, input, or operation failure. For a missing required capability, ask once with the active language pack's consent line, install only on yes, then rerun the same command. A business/data failure is never evidence that Python is absent.
3. **Take over typed issues one by one.** Treat `.ingest/review_queue.jsonl` as the canonical lifecycle, not `.ingest/ai_review_manifest.json` (legacy view only). Start with:

   ```text
   python scripts/ingest_review.py --workspace <ws> --json list
   python scripts/ingest_review.py --workspace <ws> --json show <issue_id>
   python scripts/ingest_review.py --workspace <ws> --json claim <issue_id>
   ```

   Read each issue's source hash, page/evidence references, reason codes, description, and suggested action. Recover scans/images through the host's available OCR/vision path; inspect ambiguous chapter or problem/solution assignments against the original pages; never infer an official answer from filename alone.
4. **Apply only evidence-bound patches.** Build a strict `ReviewPatch` from the `show` operation shapes, run `validate-patch`, then `apply`. The allowed operations add/replace a content unit, assign a chapter/phase, pair a question and answer, classify an asset, or mark an issue unrecoverable. Use `mark-unrecoverable --reason ...` only after recovery is genuinely impossible. The append-only `.ingest/review_patches.jsonl` ledger is replayed into compiled outputs; never hand-edit the ledger, queue, compiled content units, wiki, or bank to bypass a gate.
5. **Rebuild and validate after review.** Run `ingest_review.py --workspace <ws> rebuild`, then `validate_workspace.py <ws> --json`. Source drift, stale hashes, unresolved blocking issues, missing page anchors, or unbound blocking review entries keep readiness blocked. `unrecoverable` issues remain visible warnings rather than disappearing.
6. **Account for every alert.** Read the stable `.ingest/parse_report.json`, `.ingest/unbound_review.json`, typed queue, and `ingest_report.json.missing_answer_ids` in full. Recover each supported gap or tell the student exactly which material remains incomplete and why. Never silently skip an alert.
7. **Advanced lower-level diagnostic path only.** To isolate a compiler/parser defect, maintainers may run `scripts/build_raw_input_from_workspace.py` and then `scripts/ingest.py` directly. This is not the normal student workflow and does not replace final validation. `scripts/ingest.py` compiles a prepared payload; it does not independently prove readiness.
8. **Three-sided visual cross-check AFTER ingest has created the workspace.** The normal orchestrator already runs `build_visual_index.py --apply --apply-wiki` and recompiles. In lower-level diagnostics, inspect wiki visual coverage, prompt suspects, answer suspects, deferred answer pages, and shared prompt/answer blockers separately. A zero count on one side proves nothing about the others; answer-only pages never enter prompt/wiki context early.
9. **True no-Python fallback only.** Manual writing is allowed only after a direct interpreter probe proves Python truly cannot start. A nonzero command is a fail-loud operation error, not permission to degrade silently. In the confirmed fallback, disclose that structured validation, typed review, source-version checks, and visual cross-checks are unavailable, then create only the minimum workspace from the selected locale templates. Missing package files are not evidence that Python is unavailable.
10. Label compiled provenance honestly: 🟢 来自资料 for material-derived content, 🟡 AI补充，可能与你老师讲的不完全一致 for an explicit supplement, and ⚠️ AI生成答案，非老师/教材提供 for a generated answer when no official answer exists.

## Output Contract

- Return a readiness-aware receipt, not a generic success claim: `ready` may hand control to teaching; `usable_with_gaps` must name the warnings before teaching; `blocked` must state the issue count/reasons and remain in review.
- Produce `.ingest/` structured facts, `references/wiki/`, `references/quiz_bank.json`, optional `references/teaching_examples.json`, append-only `references/teaching_baseline.json`, visual indices/assets, `study_plan.md`, `study_state.json`, generated `study_progress.md`, `ingest_report.json`, and a freshness-bound retrieval index.
- Every discovered source is recorded, and every page the adapter can enumerate is accounted for. Structured units retain source file/hash, page anchor, element kind, parent/section context, chapter/phase mapping, extraction method/confidence, and asset role where available. Blank/scanned known pages still receive page anchors and review evidence; a file whose pages cannot be enumerated remains an explicit source-level review issue rather than disappearing.
- Student-facing receipts use the persisted language: English by default, Simplified Chinese when the student opened in Chinese, or explicit bilingual composition. Machine JSON keys, hashes, IDs, reason codes, and statuses remain stable control-plane vocabulary.

## Language packs

Load the matching student wording before emitting a receipt:

- `中文` → [`../../locales/zh/skills/exam-ingest.md`](../../locales/zh/skills/exam-ingest.md)
- `English` → [`../../locales/en/skills/exam-ingest.md`](../../locales/en/skills/exam-ingest.md)
- `双语` → compose both packs block by block, Chinese first with a `> EN:` mirror

`zh`, `en`, and `bilingual` are the persisted canonical values. `中文`, `English`, and `双语` are display/legacy input aliases normalized before storage.

## Boundaries

- The package-root scripts and locale templates are required. If this subskill is installed alone, report the packaging error and use/install the complete package. Missing package files are not evidence that Python is unavailable and do not authorize manual fallback.
- Do not modify parser/compiler logic while acting as the exam coach. Use the public commands and typed patch lifecycle.
- Do not fabricate a standard answer, source filename, page, chapter assignment, or review resolution.
- Do not hand control back to teaching while validator readiness is `blocked`.
