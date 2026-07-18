# Agent Portability

Behavior lives in `skills/`; `AGENTS.md` is the compact fallback. Prefer references over copies; any host copy must stay aligned. Install the whole runtime, including `scripts/`, `locales/`, `docs/`, and `prompts/`; confusion capture is [`skills/confusion-tracker/SKILL.md`](../skills/confusion-tracker/SKILL.md).

| Host | Entry/boundary |
| --- | --- |
| Claude Code | root `SKILL.md` or `skills/*` |
| Codex | `AGENTS.md` or `skills/*` |
| Cursor / Windsurf / generic | aligned `AGENTS.md` fallback |
| ChatGPT / Claude Web | `prompts/web_prompt.md`; English: [`prompts/web_prompt.en.md`](../prompts/web_prompt.en.md); mounted data only, no local-write claims |

Root `SKILL.md` is language-neutral; locale compatibility indices include [`locales/en/SKILL.md`](../locales/en/SKILL.md). Skill-aware hosts may enter at `skills/exam-cram/SKILL.md`.

## 文件型 host

Only after the student confirms exact, separate materials/workspace paths and the three learning choices:

```bash
python scripts/exam_start.py status --materials <dir> --workspace <ws> --json
python scripts/exam_start.py confirm --course <name> --materials <dir> --workspace <ws> --mode <mode> --time-budget <tier> --language <zh|en|bilingual> --json
python scripts/ingest_course.py --materials <dir> --workspace <ws> --json
```

`confirm` atomically writes pair confirmation, state, and runtime receipt; later gates revalidate them. Core covers PDF/DOCX/PPTX/XLSX/raster/txt/Markdown with honest PDF-page, PPTX-slide, XLSX-worksheet, DOCX-logical-segment, and raster page-equivalent anchors.

If ingestion is interrupted after `.ingest/material_build_pending.json` is published and the runtime receipt is then missing or drifted, do not rerun ordinary `confirm` and do not delete the blocker. Choose explicitly:

```bash
python scripts/exam_start.py recover-material-build --materials <dir> --workspace <ws> --action resume --json
# If the builder now produces different bytes and resume refuses with zero publication:
python scripts/exam_start.py recover-material-build --materials <dir> --workspace <ws> --action supersede --json
python scripts/ingest_course.py --materials <dir> --workspace <ws> --json
```

`resume` is exact-generation-only; complete bound sources bypass reparsing, while incomplete blocker-first state may be rebuilt only if it reproduces the same generation. `supersede` creates an audited schema-2 successor and closes every predecessor to its direct child. Recovery logs are bounded (64 events per generation, 64 ancestor edges, and at most 65 receipt rows including one current completion) and are transactionally bound by the receipt and manifest.

Ingestion-v2 requires one parser receipt per source, binding revision/config/location accounting and `network/upload/install=false`; its exact schema is in [`file-format.md`](file-format.md). Docling/MinerU require an explicitly selected host callable runner; probe/flag alone is insufficient. The adapter itself does not install, network, or upload. Policy values are validated declarations, not runner sandbox/attestation; the host constrains and audits runner internals.

Exit `0` means `ready` or disclosed `usable_with_gaps`; `10` means completed process but blocked content, so teaching/quiz/completion remain forbidden; other nonzero means operation failure, never “no Python.” Typed takeover uses only `ingest_review.py list/show/claim/validate-patch/apply/apply-batch/mark-unrecoverable/rebuild`; batch apply keeps one validated patch per issue. Never hand-edit ledgers, facts, wiki, or bank. Rebuild and validate after source/patch changes.

## 教材与宿主扩展

Missing/unknown `artifact_mode` is `chat`. Explicit standing `visual` or a one-shot request may enter the linked [`PDF capability routes`](pdf-capability-adapters.md); no mode permits silent installation. Structured completion always requires the current `profile=full` typed guide. Visual delivery additionally requires matching hashes, every-page QA, no unresolved defect, and `artifact_ready=ready`; language changes stale prior artifacts.

Ingestion-v2 Guide claims bind exact same-unit refs and current guide/source/content/fact/parser-receipt hashes; the receipt proves authored-text membership plus location/revision, not semantic support. Legacy/v1 does not claim this gate.

Existing LangGraph hosts may use the optional [`LangGraph adapter`](langgraph-host-adapter.md). Checkpoints/interrupts contain bounded routing contracts, never course truth; resume rehydrates current state and receipts. Web hosts cannot claim local commands, `.ingest/`, or writes. Host-specific rule copies must be generated from or tested against `AGENTS.md`.
