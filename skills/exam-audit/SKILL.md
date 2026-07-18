---
name: exam-audit
description: >
  只读检查一个已生成的备考工作区是否健康并报告问题，默认不做任何修改。核对 .ingest 原材料版本、
  内容单元、接管队列与派生产物完整性，以及 wiki、题库、视觉证据、计划和进度的一致性。当用户怀疑
  工作区有问题、建库 readiness 被阻断、或想在开始复习前体检时使用。
license: MIT
---

# exam-audit — workspace health check (read-only)

## Purpose
Inspect a prep workspace built by `exam-ingest` and report health issues. This is a read-only inspector. Do NOT fix anything by default; only fix after the user explicitly grants permission. Emit a concrete issue report; never silently modify or delete files.

## Activation
Activate when the user suspects the workspace is broken (missing chapters, ungradable quiz items, inconsistent progress), or when the user wants a pre-review health check before studying. Do not activate to build, teach, or grade.

## Inputs
- `references/wiki/` — chapter knowledge files (`chN_*.md`).
- `references/quiz_bank.json` — quiz items.
- `references/teaching_examples.json` — optional parallel teaching inventory; examples may remain here even when they are deliberately excluded from the gradable bank.
- `references/teaching_baseline.json` — append-only retention baseline for new workspaces; it must never shrink on re-ingest and must not be hand-edited.
- `references/figure_page_index.json` / `references/image_question_index.json` — regenerable three-sided visual coverage evidence (wiki / prompt / answer).
- `.ingest/source_manifest.json` — source-root-relative paths, source hashes, media types, and parse status.
- `.ingest/base_content_units.jsonl` / `content_units.jsonl` — deterministic extraction baseline and the compiled view after replaying validated patches.
- `.ingest/base_chapter_phase_mappings.jsonl` / `chapter_phase_mappings.jsonl` — explicit chapter-to-study-phase identity.
- `.ingest/review_queue.jsonl` / `review_patches.jsonl` — typed issue lifecycle and append-only evidence-bound patch ledger.
- `.ingest/build_manifest.json` / `unbound_review.json` — source root, page-quality accounting, input/derived hashes, and issues not yet bound to one source record.
- `ingest_report.json` — import counts, current-snapshot statistics, missing-answer alerts, and the legacy retention fallback.
- `study_plan.md` — phase plan with chapter anchors.
- `study_state.json` — the structured progress state (the SINGLE SOURCE OF TRUTH when present: `phase_checklist`, `mistake_archive`, `confusion_log`).
- `study_progress.md` — a GENERATED VIEW of the state (rendered phase checkpoints and recorded wrong-question IDs); stale/hand-edited when it drifts from `study_state.json`.

## Workflow
Inspect read-only. Open and parse files; never write, rename, or delete. Check each item below and record every failure as a concrete issue (file path + what is wrong).

1. **Structure.** For each phase listed in `study_plan.md`, confirm a matching `references/wiki/chN_*.md` file exists. Flag orphan chapters (wiki files no phase references) and broken links (phases pointing to absent chapters).
2. **Quiz bank.** For each item in `references/quiz_bank.json`: confirm `type` is one of the six allowed types (choice / subjective / diagram / fill_blank / true_false / code); confirm `choice` items carry `options`; treat missing `keywords` on subjective items as a grading-quality warning. An item without an answer must declare `answer_status: unknown`; in a structured workspace it also remains a blocking review issue until an evidence-backed official answer, an explicitly labeled AI answer, or an unrecoverable terminal decision is recorded.
3. **Provenance honesty.** Flag any AI-generated answer presented as the teacher's standard answer (missing the ⚠️ marker). Flag any AI-supplement wiki passage that should carry 🟡 but does not.
4. **Plan/progress consistency.** When `study_state.json` exists, treat it as the source of truth: confirm `study_progress.md` is a faithful render of it (flag drift / stale hand-edits where the md disagrees with the state), and check the state's `phase_checklist` phases map to `study_plan.md`. When no `study_state.json` exists, audit `study_progress.md` directly. Either way, confirm each rendered phase-checkpoint line maps to a phase in `study_plan.md` and every wrong-question ID exists in `references/quiz_bank.json`. Note: the template anchor `<!-- PHASE_CHECKLIST -->` is replaced by `scripts/ingest.py` at generation time and is absent from a correct finished workspace — do NOT report its absence as a problem.
5. **Teaching-example retention.** Prefer `references/teaching_baseline.json`; validate its schema, exact per-chapter mapping, append-only policy, and require every baseline ID to have a same-chapter current snapshot in `references/teaching_examples.json`. Presence of the same ID in `references/quiz_bank.json` is diagnostic overlap only and never substitutes for that teaching snapshot. Only old workspaces without the baseline file may fall back to `ingest_report.json.teaching_example_ids`. It is valid for an ungradable worked example to be absent from the bank if the teaching layer retains it; disappearance from the current teaching layer is a blocking retention gap even when a quiz item survives. Validate the current teaching manifest's IDs, chapter/phase tags, source pages, answer source, and asset paths. Read it per chapter in tutoring; do not treat the whole manifest as a new answer source.
6. **Three-sided visual completeness.** Inspect each denominator separately: `figure_page_index.json.wiki_visual_coverage` for detected material pages embedded in wiki, `image_question_index.json.prompt_suspects` for missing prompt context, and `answer_suspects` for missing answer context. A zero on one side is never evidence that the other two are complete. Require matching `integrity` snapshots and re-hash their declared quiz, teaching, wiki, and asset inputs; stale or missing freshness evidence blocks a new-manifest phase from being complete. Flag NUL/control-byte warnings and missing/capped pages. State that this is deterministic recall coverage, not semantic proof that every meaningful figure was found.
7. **Structured ingestion integrity.** When `.ingest/` exists, strictly load every source, unit, mapping, issue, patch, unbound entry, and build-manifest section. Re-hash current source bytes; source drift invalidates patches and derived indexes. Require one `page_anchor` for every accounted page, including blank/scanned pages. Require blocking issues in `pending` / `claimed` / `validated` / `blocked` to keep readiness blocked; applied/resolved/superseded issues must agree with the ledger and compiled outputs, while unrecoverable issues remain visible warnings. Verify build-manifest hashes for the wiki, bank, retrieval index, visual/teaching manifests, and structured facts.
8. **Evidence-gated phase completion.** In a structured workspace, a checked phase must carry valid `phase_evidence` plus a currently validated `notebook/chNN.guide.json` with `profile=full`; the typed validator owns the de-duplicated teaching-example + gradable-bank denominator and source-reference checks. Under standing `visual`, also require `capabilities.artifact_ready.status=ready`, matching receipt hashes, and accepted all-page QA before completion. Under `chat`, the typed manifest is still required but PDF is not. `verified` needs at least two distinct handled bank items and at least one `passed`; `preferences.no_questions=true` caps the phase at `covered_unverified`. A language change makes the old manifest/artifact stale until relocalized and, for visual output, rerendered and re-QA'd. Legacy workspaces without `.ingest/` remain compatible but must be reported as lacking the structured completeness gate.
9. **Path safety.** Flag traversal, symlink escapes, and absolute paths in fields that are defined as workspace-relative. `.ingest/build_manifest.json.source_root` is intentionally an absolute materials-root binding and is not itself an error; report it only when missing, unreadable, moved, or inconsistent with source records.

Run `python <package-root>/scripts/validate_workspace.py <workspace> --json` as the canonical static check and include its `readiness` in the report. `ok=true` means structurally runnable (no validation errors); it does not erase warnings or mean content-complete.

## Output Contract
Emit a single issue list. Each entry contains: `【级别(阻断/警告/提示)】` (severity: blocker / warning / notice) + `【位置文件】` (file path) + `【现象】` (concrete symptom) + `【建议修法】` (suggested fix). End with exactly one readiness verdict: `ready` (no errors or warnings), `usable_with_gaps` (no errors, but warnings/incomplete evidence remain), or `blocked` (one or more errors). Never translate `ok=true` into `ready` without checking warnings.

Do NOT auto-fix. After reporting, fix item-by-item only if the user grants permission, or hand the workspace back to `exam-ingest` for rebuild.

Preserve these provenance labels VERBATIM when quoting them in findings: 🟢 来自资料 / 🟡 AI补充，可能与你老师讲的不完全一致 / ⚠️ AI生成答案，非老师/教材提供.

Student-facing output defaults to English (Simplified Chinese if the student opened in Chinese); the persisted `study_state.json.language` code (`zh`/`en`/`bilingual`) switches it per exam-cram's dispatch rule with single-language purity.

## Language packs
This skill produces no student-facing template; its report is agent-composed in the student's language. There are no `locales/zh/skills/exam-audit.md` or `locales/en/skills/exam-audit.md` pack files — compose the issue report directly in the language given by `study_state.json.language`:
- `中文` → Simplified Chinese, using the zh canonical wording in [`../../docs/language-policy.md`](../../docs/language-policy.md)
- `English` → English, using the EN canonical vocabulary in [`../../docs/language-policy.md`](../../docs/language-policy.md)
- `双语` → compose zh-first with a `> EN:` mirror line per block (rules in [`../../docs/language-policy.md`](../../docs/language-policy.md))
Display aliases such as `中文`, `English`, and `双语` are normalized by `update_progress.py`; route persisted state on `zh`, `en`, or `bilingual`. Unset language → the merged first-ask decides it; default English unless the student opened in Chinese.

## Boundaries
- Zero modifications and zero deletions by default — this is an inspection, not construction.
- Do not infer the teacher's intent; report only objective inconsistencies and leave the judgment to the student.
