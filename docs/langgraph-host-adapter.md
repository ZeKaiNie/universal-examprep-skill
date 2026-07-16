# Optional LangGraph host adapter

`scripts/host_adapters/langgraph_exam.py` is opt-in/lazy-imported. Hosts supply a
durable checkpointer; default runs need no LangGraph, install, or network.

The adapter can wrap the full exam workflow without replacing its existing tools:

```text
START -> rehydrate -> [confirm] -> ingest -> validate ->
[reingest/rebuild/review/warnings/tutor/guide/visual QA/completion] ->
atomic completion snapshot -> END
```

Guards reread `study_state.json`, `.ingest/`, runtime, Guide, render, and QA
receipts on every resume. Checkpoints are routing hints, never evidence. Source,
parser, runtime, build, typed-review, warning, teaching, artifact, or dependency
drift returns to its canonical gate. The completion node takes a locked fresh
validation/progress snapshot and post-checks its dependency digest before END.
Invalid or truncated facts fail closed; validator output is bounded and a partial
warning list cannot be acknowledged.

Every invoke/resume reuses one stable `thread_id`. Interrupt nodes perform no side
effects before `interrupt`; command nodes are idempotent or protected by durable
operation receipts. Review routing only lists work: evidence inspection,
claim/patch/apply/rebuild, and validation remain in `ingest_review.py`.

## Study Guide subgraph

```text
rehydrate -> claim_create -> claim_attach -> claim_verify -> typed_validate ->
import -> preflight -> html -> pdf -> qa_render -> inspection -> accept -> validate
```

The host's read-only, local-only `command_api.study_guide_status(workspace,
chapter, artifact_mode, draft_path)` reruns the existing validators and returns
exactly `schema_version`, `chapter`, `artifact_mode`, `stage`, `pdf_sha256`,
`render_manifest_sha256`, and `pages`. Preflight and HTML publication are separate
gates; visual inspection/ready bind every ordered PNG hash.

First mount reports current truth. Later same-mode reads may stay, regress, or
advance one stage; chat goes `import` to `ready`, and v1 mounts after claim gates.
Inspection exposes every current PNG and requires one `N=pass[:notes]` verdict per
page. Only canonical `study_guide_qa.py accept` output advances to ready; any defect
requires rerendering and a fresh first-to-last inspection.

References: [interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts),
[persistence](https://docs.langchain.com/oss/python/langgraph/persistence).
