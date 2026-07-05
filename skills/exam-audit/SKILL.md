---
name: exam-audit
description: >
  只读检查一个已生成的备考工作区是否健康并报告问题，默认不做任何修改。核对 references/wiki 章节、
  quiz_bank.json 题型与来源标注、study_plan/study_progress 锚点与一致性，列出缺失/越权/未标来源的
  答案等隐患。当用户怀疑工作区有问题、或想在开始复习前体检时使用。
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
- `study_plan.md` — phase plan with chapter anchors.
- `study_progress.md` — rendered phase checkpoints and recorded wrong-question IDs.

## Workflow
Inspect read-only. Open and parse files; never write, rename, or delete. Check each item below and record every failure as a concrete issue (file path + what is wrong).

1. **Structure.** For each phase listed in `study_plan.md`, confirm a matching `references/wiki/chN_*.md` file exists. Flag orphan chapters (wiki files no phase references) and broken links (phases pointing to absent chapters).
2. **Quiz bank.** For each item in `references/quiz_bank.json`: confirm `type` is one of the six allowed types (choice / subjective / diagram / fill_blank / true_false / code); confirm `choice` items carry `options`; confirm `subjective` items carry `keywords`; confirm any item missing `answer` carries the ⚠️ marker or `source: ai_generated`.
3. **Provenance honesty.** Flag any AI-generated answer presented as the teacher's standard answer (missing the ⚠️ marker). Flag any AI-supplement wiki passage that should carry 🟡 but does not.
4. **Plan/progress consistency.** Confirm each rendered phase-checkpoint line in `study_progress.md` maps to a phase in `study_plan.md`. Confirm every wrong-question ID in `study_progress.md` exists in `references/quiz_bank.json`. Note: the template anchor `<!-- PHASE_CHECKLIST -->` is replaced by `scripts/ingest.py` at generation time and is absent from a correct finished workspace — do NOT report its absence as a problem.
5. **Path safety.** Flag suspicious writes outside `references/wiki/` and any residual `../` or absolute paths.

## Output Contract
Emit a single issue list. Each entry contains: `【级别(阻断/警告/提示)】` (severity: blocker / warning / notice) + `【位置文件】` (file path) + `【现象】` (concrete symptom) + `【建议修法】` (suggested fix). End with an overall verdict: `可用` (usable) or `需修` (needs repair).

Do NOT auto-fix. After reporting, fix item-by-item only if the user grants permission, or hand the workspace back to `exam-ingest` for rebuild.

Preserve these provenance labels VERBATIM when quoting them in findings: 🟢 来自资料 / 🟡 AI补充，可能与你老师讲的不完全一致 / ⚠️ AI生成答案，非老师/教材提供.

Student-facing output defaults to Simplified Chinese; a persisted `study_state.json` `language` (`English`/`双语`) switches it per exam-cram's dispatch rule (canonical tokens verbatim).

## Boundaries
- Zero modifications and zero deletions by default — this is an inspection, not construction.
- Do not infer the teacher's intent; report only objective inconsistencies and leave the judgment to the student.
