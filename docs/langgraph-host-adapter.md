# Optional LangGraph host adapter

`scripts/host_adapters/langgraph_exam.py` is opt-in/lazy-loaded. Defaults need no
LangGraph; the host supplies a durable checkpointer; nothing is installed.

```text
START -> rehydrate -> [confirm] -> ingest -> validate ->
[reingest/rebuild/review/warnings/tutor/guide/visual QA/completion] ->
atomic completion snapshot (fresh validate + progress + post-check) -> END
```

- Guards read strict JSON/validator state, not checkpoints: unconfirmed
  paths pause, ingestion exit 10 validates, and errors block.
- Source/parser/runtime drift re-ingests; build drift rebuilds; active issues go
  to review; visual completion needs `artifact_ready=ready`.
- Truth is `study_state.json`, `.ingest/`, `exam_runtime_receipt.json`, Guide, and
  render/QA receipts. Checkpoints are hints; every resume revalidates.
- Completion resumes acquire one locked completion snapshot: fresh validation,
  progress, and a post-read dependency check. Source, build, typed-review, warning,
  tutor, artifact, or dependency drift returns to its gate and cannot reach END.
  Validator receipts use a SHA-256 binding only when the bounded full dependency
  digest is identical before and after all validator/capability reads; this is not
  a cryptographic signature. A second content subdigest excludes only generated
  `study_state.json`/`study_progress.md`, so a valid `complete-phase` write does not
  stale teaching hints while every completion post-check still binds the full tree.
- Every invoke and resume must reuse the same stable `thread_id`. LangGraph resumes
  an interrupted node from that node's beginning, so interrupt nodes perform no
  side effects before `interrupt`; command nodes must be idempotent or protected by
  durable command receipts. A custom `command_api` is an at-least-once host boundary:
  it must deduplicate a stable operation ID or persist an equivalent idempotency
  receipt. Review only lists; evidence, `show/claim`,
  patch/apply/rebuild, and validation stay in `ingest_review.py`. QA inspects every
  page, records one pass each, and confirms hashes/readiness.
- Each interrupt publishes a one-field `resume_contract`; the resume object must set
  that exact field to `true` (for example `acknowledged`, `persisted`, or `accepted`).
  This is an explicit sequencing acknowledgement, never a substitute for the fresh
  command receipt and validator checks that follow it.
- Warning/tutor hints fingerprint the chapter/content/validation/warning/dependency
  binding; drift stales them. `{ "done": true }` proves nothing. Missing capabilities,
  invalid JSON, or drift never triggers Markdown/no-Python fallback.
- Host validation returns at most 200 errors and 200 warnings and accepts a warning
  acknowledgement only when both truncation counts are zero. Resolve larger sets
  directly with validator `--full`/`--details-file`; a partial list fails closed.
