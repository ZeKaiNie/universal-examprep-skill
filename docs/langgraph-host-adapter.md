# Remote-only LangGraph host contract

LangGraph is not part of the local student runtime. The package never downloads,
installs, imports, or executes it. `scripts/host_adapters/langgraph_exam.py` keeps
bounded receipt validators and routing helpers only so a separately operated
remote/cloud host can implement the same gates. Calling `build_exam_graph()`
locally fails explicitly.

A remote host may be proposed only when the learner explicitly asks for
LangGraph. Before any upload, that host must disclose its service, data boundary,
retention/privacy terms, and receive separate consent. If no configured remote
integration exists, report it unavailable and use the normal local command/state
machine; do not install a local fallback.

The remote graph may coordinate the full workflow, but never becomes its truth:

```text
START -> rehydrate -> [confirm] -> ingest -> validate ->
[reingest/rebuild/review/warnings/tutor/guide/visual QA/completion] ->
atomic completion snapshot -> END
```

Every transition must reread `study_state.json`, `.ingest/`, runtime, Guide,
render, and QA receipts. Checkpoints are bounded routing hints, not course or
learning evidence. Source, parser, runtime, build, review, warning, teaching,
artifact, or dependency drift returns to its canonical local gate. Interrupt
nodes perform no side effects before interrupting; mutation commands remain
idempotent or receipt-protected.

The default `processing_mode=lightweight` never enters this route. Even under
`processing_mode=full`, LangGraph remains explicit opt-in and remote-only.

## Study Guide subgraph contract

```text
rehydrate -> claim_create -> claim_attach -> claim_verify -> typed_validate ->
import -> preflight -> html -> pdf -> qa_render -> inspection -> accept -> validate
```

The host must expose the same bounded Study Guide status fields and preserve the
existing progression gates. A remote checkpoint cannot skip claim verification,
typed import, rendering receipts, or one passing visual verdict per rendered page.
