# Visual formula audit importer

`scripts/import_formula_audit.py` converts a completed AI/human visual formula
audit into typed, evidence-bound review-patch drafts. It is deliberately not an
OCR engine and never mutates course truth: it does not claim issues, apply
patches, rebuild derivatives, or use an audit-supplied screenshot as evidence.

```bash
python scripts/import_formula_audit.py \
  --workspace /absolute/course-workspace \
  --audit /absolute/audit-part-1.json \
  --audit /absolute/audit-part-2.json \
  --output-dir /absolute/patch-drafts/formulas \
  --reviewer visual-reviewer \
  --json
```

The command accepts either `latex_formulas` + `semantic_en` or
`formulas_latex` + `semantic`. Before writing anything, it verifies that every
issue is active and `formula_hint`-only, the source revision is current, the
source file/page match, and the issue's sole content-addressed evidence JSON is
intact and names the same locator. A visual-metadata object may name the original
PDF and source hash; `render_path` is ignored and never enters a patch.

Each non-empty, formula-only LaTeX string becomes one deterministic
`source_language=zxx` formula `ContentUnit` and one `add_unit` operation. Empty
or duplicate formulas, duplicate issues, source/evidence drift, and ordinal or
unit-ID collisions fail closed. A `false_positive` row must be a warning with no
formulas; it produces a versioned, evidence-bound `mark_resolved` patch rather
than inventing mathematical content.

When a solution-book page is already mapped to multiple chapters, its recovered
formula units remain chapter-unassigned instead of guessing a winner; the
summary lists those issue IDs for later typed chapter assignment.

Before any output is written, the importer runs the entire patch set through a
read-only storage-layer batch validation. This uses one current ledger replay,
carries candidate effects forward in memory to detect cross-patch collisions,
checks every issue postcondition, and writes no queue, unit, mapping, ledger, or
source-status state. It may create/use the normal coordination lock file, but it
never recovers a pending ingestion transaction: that condition fails closed for
an explicit recovery run.

The output directory contains one contextually validated patch per issue,
`patch-list.json`, and `formula-audit-import-summary.json`. Drafting is
idempotent in the same directory. Review the files, claim the corresponding
issues if your workflow requires claims, then use `ingest_review.py apply-batch`
explicitly. A later source, evidence, queue, or compiled-state drift still fails
closed when the patch is applied.
