# Knowledge Ingestion Hardening Plan

Status: implementation complete; Draft PR delivery in progress

Branch: `codex/kb-ingestion-hardening`

Scope: local, student-focused Exam Cram Coach; no hosted multi-tenant service

Source brief: *Course Knowledge Base Project Plan* (18-page DOCX supplied by the maintainer)

## 1. Outcome

Replace the current "extract page text, concatenate Markdown, then hope an agent fixes warnings" path with a lightweight and recoverable ingestion core:

```text
immutable course files
  -> source manifest and capability routing
  -> provenance-preserving content units
  -> deterministic candidates plus confidence
  -> typed AI review tasks and replayable patches
  -> chapter/problem/concept compilation
  -> structure-aware chunks and fresh indexes
  -> completeness, leakage, citation, and retrieval gates
```

The default installation remains small and local. Existing deterministic extraction, BM25 retrieval, path safety, visual answer-leakage gates, and provenance rules stay in place. Optional high-fidelity parsers remain adapters, never silent dependencies.

## 2. Why this change is necessary

### 2.1 Repository evidence

The audit found correctness problems, not merely missing polish:

| ID | Priority | Finding | Consequence |
| --- | --- | --- | --- |
| I-01 | P0 | `build_raw_input_from_workspace.py` flattens extracted page text directly into chapter Markdown. It does not produce concepts, definitions, formulas, or stable content units. | The "knowledge base" is mostly a chapter text dump; concept and term indexes are usually empty. |
| I-02 | P0 | Phase order and actual chapter number are treated as the same integer. | A course containing only chapter 5 can produce phase 1, retrieval chapter 1, and quiz chapter 5. |
| I-03 | P0 | Warnings, skipped files, visual hand-off, and missing answers are separate free-text reports. | An agent cannot claim, resolve, validate, replay, or resume work reliably; ingestion may report success with known gaps. |
| I-04 | P0 | Visual/AI fixes mutate compiled wiki or question-bank outputs. Re-ingestion overwrites them. | Expensive review work is lost and retrieval can remain stale. |
| I-05 | P0 | Dependency preflight advertises a different PDF capability matrix from the code that actually parses and renders PDFs. | A green preflight can fail at runtime, and a working backend can be rejected. |
| I-06 | P0 | The official ingest sub-skill does not require a final workspace validation gate. | An agent can deliver a structurally invalid or materially incomplete workspace. |
| I-07 | P0 | Fully scanned or image-only documents can have no source anchor in the generated wiki. | Later visual repair may have no deterministic place to attach recovered content. |
| I-08 | P0 | Web prompts permit AI-authored quiz questions when no bank is mounted. | This violates the bank-only anti-fabrication contract. |
| I-09 | P0 | Historical manuals disagreed with the v4 neutral persisted codes (`zh`, `en`, `bilingual`). | Strict agents could load the wrong pack or reject valid state. |
| I-10 | P0 | Several sub-skills permit unsafe fallback or direct Markdown state edits on any script failure. | Parser bugs can be hidden and later rendering can discard progress records. |
| I-11 | P1 | Ingestion and visual scripts rescan the same PDFs and own overlapping backend, page, asset, and wiki-write logic. | Two facts sources drift and make failures difficult to reproduce. |
| I-12 | P1 | Roughly 462 ingestion-related tests use fake PDF backends; there are no real PDF/DOCX/PPTX fixtures. | Regex regressions are covered, but real layout, adapter, OCR, and recall behavior is not. |
| I-13 | P1 | Locale entry manuals duplicate control logic and stale documentation describes several incompatible architectures. | Runtime context is large and rule drift has already occurred. |
| I-14 | P2 | `quiz_items`, `wiki_meta.json`, the old caption-only gallery, and standalone `build_knowledge_index.py` duplicate data or have no production consumer. | Repository structure and output contracts are harder to understand than necessary. |

### 2.2 Requirements adopted from the supplied project brief

The external brief describes a production course knowledge system with a structured parser, durable revisions, element-level provenance, parent-child chunks, hybrid retrieval, backend-verified citations, restricted agent tools, and measurable evaluation. This repository adopts the parts that improve a local student's reliability:

| Brief requirement | Lightweight implementation here |
| --- | --- |
| Document revisions and immutable sources | SHA-256 source manifest plus parser/config version; compiled outputs are always rebuildable. |
| Rich document elements | Small stdlib JSON/JSONL content-unit schema with page, optional bbox, kind, text/LaTeX/asset, section path, method, confidence, and provenance. |
| Material-aware parsing | Course profiles and deterministic candidate extractors for lecture, textbook, homework, exam, and solution files. |
| Parent-child chunks | Explicit chapter/section parent IDs, source-unit IDs, and context prefixes; tables/formulas/questions are not blindly split. |
| Human/AI review for low confidence | Typed review queue, validated append-only patch log, explicit `pending/applied/blocked` lifecycle. |
| Dense plus sparse retrieval and reranking | Keep zero-dependency BM25 as the default; define an optional retriever interface and RRF extension point only. |
| Verified citations and abstention | Stable source spans, index integrity hashes, answer-leakage checks, unresolved-gap status, and retrieval abstention. |
| Evaluation and release gates | Deterministic ingestion gold set, retrieval Recall@k/MRR, page accounting, problem/answer pairing, and visual leakage tests. |

The following service-oriented parts are intentionally out of scope for the default skill: Postgres, Qdrant, Redis/Celery, S3/MinIO, Kubernetes, multi-tenant ACLs, always-on APIs, mandatory embeddings/rerankers, and cloud parsing.

## 3. Prior art and clean-room boundary

Implementation ideas are derived from public contracts and documentation, not copied source code:

- [LlamaIndex Ingestion Pipeline](https://developers.llamaindex.ai/python/framework/module_guides/loading/ingestion_pipeline/): transformation caching and `doc_id -> document_hash` duplicate/upsert semantics.
- [Haystack DocumentTypeRouter](https://docs.haystack.deepset.ai/docs/documenttyperouter): explicit MIME routing and an `unclassified` path.
- [Haystack HierarchicalDocumentSplitter](https://docs.haystack.deepset.ai/docs/hierarchicaldocumentsplitter): explicit parent-child document blocks.
- [Unstructured partitioning](https://docs.unstructured.io/open-source/core-functionality/partitioning): preserve typed elements before chunking and route between fast/high-resolution/OCR strategies.
- [Unstructured chunking](https://docs.unstructured.io/open-source/core-functionality/chunking): combine whole semantic elements, isolate tables, and text-split only oversized elements.
- [Docling document model](https://docling-project.github.io/docling/concepts/docling_document/): unified hierarchy, layout boxes, and provenance.
- [Docling chunking](https://docling-project.github.io/docling/concepts/chunking/): document-first hierarchy, token-aware refinement, same-heading merges, and repeated table headers.
- [LiteParse](https://github.com/run-llama/liteparse): a local, lightweight PDFium/OCR/screenshot adapter candidate with bounding boxes and complexity detection.
- [Microsoft GraphRAG outputs](https://microsoft.github.io/graphrag/index/outputs/): cross-linked documents, text units, entities, and relations; only the provenance pattern is relevant here.
- [ParseBench](https://github.com/run-llama/ParseBench): deterministic dimensions for content fidelity, semantic formatting, tables, charts, and visual grounding.

No external framework becomes a runtime dependency in this PR. No GPL implementation or model weight is copied. Any future direct code borrowing must record the exact source revision, license, copied scope, and modifications in `THIRD_PARTY_NOTICES.md`.

## 4. Target contracts

### 4.1 Source manifest

`.ingest/source_manifest.json` is the durable inventory for one workspace build:

```json
{
  "schema_version": 1,
  "sources": [
    {
      "source_id": "src_<stable digest>",
      "path": "lectures/ch05.pdf",
      "sha256": "...",
      "size_bytes": 1234,
      "media_type": "application/pdf",
      "status": "parsed"
    }
  ]
}
```

Paths are workspace-relative and normalized. A source is always accounted for as `discovered`, `parsed`, `review_required`, `unsupported`, `failed`, or `unrecoverable`; no source silently disappears. Pipeline identity and parser/config-dependent input hashes live in `.ingest/build_manifest.json` (`pipeline_version=ingestion-v1`) so the immutable source inventory stays minimal.

### 4.2 Content-unit IR

`.ingest/content_units.jsonl` is the smallest useful loss-resistant representation:

```json
{
  "unit_id": "unit_<stable digest>",
  "source_id": "src_<stable digest>",
  "source_file": "lectures/ch05.pdf",
  "page": 12,
  "bbox": [72.0, 144.0, 510.0, 380.0],
  "kind": "title|text|list|table|formula|figure|caption|question|answer|page_anchor",
  "text": "...",
  "html": null,
  "latex": null,
  "asset_path": null,
  "asset_role": null,
  "section_path": ["Chapter 5", "5.2 Fourier Transform"],
  "chapter_id": "ch05",
  "method": "pypdf|pymupdf|pdfium|agent_vision|manual",
  "confidence": 0.91,
  "provenance": "material|ai_recovered"
}
```

Rules:

1. IDs derive from source hash, location, normalized content, and schema version.
2. Every page has a `page_anchor`, even when it has no extracted text.
3. Raw files are immutable; patches modify the normalized view, not source files.
4. Unknown bbox or structure is `null`, never fabricated.
5. Question-side and answer-side asset roles remain separate downstream.

### 4.3 Chapter and phase identity

`chapter_id`, `phase_id`, and phase order are independent:

```json
{
  "phase_id": "phase-001",
  "phase_order": 1,
  "chapter_id": "ch05",
  "chapter_number": 5,
  "wiki_file": "references/wiki/ch05_fourier.md"
}
```

Every quiz item, teaching example, chunk, wiki file, and progress phase refers to this mapping. No component may infer one ID from another integer.

### 4.4 Typed AI review queue

`.ingest/review_queue.jsonl` replaces disconnected free-text warnings. Each exact-schema line is one issue; the abbreviated example below describes the same logical fields without pretending to be the persisted wire schema:

```json
{
  "schema_version": 1,
  "issues": [
    {
      "issue_id": "issue_<stable digest>",
      "severity": "blocking|warning|info",
      "reason_codes": ["no_text", "visual_question"],
      "source_refs": [{"source_id": "src_...", "pages": [12]}],
      "evidence_assets": [".ingest/evidence/src_.../p0012.png"],
      "target_kind": "content_unit_patch",
      "status": "pending|claimed|validated|applied|blocked",
      "attempts": 0,
      "suggested_action": "Read the rendered page and add evidence-backed units."
    }
  ]
}
```

All legacy `warnings`, `skipped`, visual hand-offs, missing answers, unmapped chapters, and unsupported formats are normalized into this queue. Compatibility reports may remain, but they are views rather than separate facts.

### 4.5 Replayable patch log

`.ingest/review_patches.jsonl` is append-only. Allowed operations are deliberately small:

- add or replace a content unit;
- assign a chapter candidate;
- pair a question and an answer candidate;
- classify an asset as question-side or answer-side;
- mark an issue unrecoverable with a user-visible reason.

Each patch includes `patch_id`, `issue_id`, operation, target, value, provenance, evidence references, reviewer, and timestamp. Validation rejects unknown paths, missing evidence, source-hash drift, answer leakage, invalid IDs, and operations outside the allow-list. Applying the same valid patch twice is idempotent.

### 4.6 Build and index integrity

`.ingest/build_manifest.json` records input hashes, applied patch IDs, derived artifact hashes, and gate results. `references/retrieval_index.json` carries its own source/wiki integrity block. Retrieval refuses a stale index instead of silently serving old content.

Build readiness has three honest states:

- `ready`: all blocking issues resolved and every mandatory gate passes;
- `usable_with_gaps`: only explicitly reported non-blocking gaps remain;
- `blocked`: a source, chapter, answer, visual dependency, freshness, or leakage gate prevents safe use.

Process exit code and readiness are separate concepts.

## 5. Implementation phases

### Phase A - contracts and regression baseline

- [x] Add `scripts/ingestion/` with stdlib models, atomic JSON/JSONL I/O, stable IDs, source manifest, review queue, and patch validation.
- [x] Preserve every input file and page in the manifest/IR, including empty and image-only pages.
- [x] Add schema versioning and explicit `chapter_id` / `phase_id` mapping.
- [x] Add unit tests for deterministic IDs, path normalization, issue lifecycle, patch replay, and invalid patch rejection.

Exit gate: contracts are independently testable and do not change legacy compiled outputs yet.

### Phase B - compatibility integration and correctness fixes

- [x] Make the existing material builder emit the source manifest, content-unit IR, and typed review queue while retaining its current CLI.
- [x] Normalize old parse warnings, skipped entries, visual review entries, and missing-answer entries into typed issues.
- [x] Fix the PDF capability matrix so preflight and runtime use one adapter registry.
- [x] Keep real chapter numbers independent from study phase order.
- [x] Assign `source_type` to lecture examples/quizzes and block unassigned chapter items from a chapter-ready result.
- [x] Run `validate_workspace.py` as the last official ingest step.
- [x] Make compiled writes crash-recoverable so a failed/interrupted build cannot mix old and new fact generations.

Exit gate: legacy commands work, non-contiguous chapters are correct, and unresolved blocking issues cannot be called ready.

### Phase C - structure-aware compilation and retrieval

- [x] Compile wiki, teaching examples, and quiz candidates from content units plus validated patches.
- [x] Extend chunk records with stable unit IDs, parent section/chapter IDs, source spans, and context prefixes.
- [x] Keep tables, formulas, figures/captions, code, and question/answer units intact unless a single unit exceeds the hard limit.
- [x] Merge concept/knowledge-point postings into retrieval index generation.
- [x] Generate lightweight terms/concepts from deterministic headings, formulas, question tags, and supplied bilingual terms; preserve AI provenance on reviewed enrichments.
- [x] Enforce index freshness during validation and retrieval using the runtime loader as the validation oracle.
- [x] Add strict gold-query evaluation with Recall@1, Recall@5, and MRR, plus formula/question/source-page and hard-negative coverage tests.

Exit gate: source-to-chunk-to-answer trace is complete and stale indexes fail closed.

### Phase D - skill and language contract repair

- [x] Route the canonical v4 state codes (`zh`, `en`, `bilingual`) correctly while accepting display strings as migration/input aliases.
- [x] Remove the web-prompt exception that invents quiz questions without a bank.
- [x] Permit manual ingest fallback only after a real Python capability probe; business failures remain visible.
- [x] Require workspace registry/path confirmation in the ingest sub-skill.
- [x] Use the official state initialization path before any Markdown fallback.
- [x] In the `<=1 day` tier, use the default walkthrough template without asking a preference question.
- [x] Clarify that source quotations keep their original language while generated teaching prose follows the target language.
- [x] Replace duplicated full locale workflows with concise compatibility indexes/messages and one control-plane truth.
- [x] Add semantic consistency, Markdown-link, template-placeholder, and runtime-context-budget tests.

Exit gate: all entry points agree on bank-only, urgency, state, workspace, language, and failure semantics.

### Phase E - repository cleanup

- [x] Remove the unused top-level `quiz_items` mirror.
- [x] Fold `wiki_meta.json` freshness into retrieval integrity and stop generating the standalone file.
- [x] Move knowledge-point indexing into the main retrieval builder, then remove `build_knowledge_index.py`.
- [x] Remove the builder's legacy caption-only wiki gallery; keep one visual compilation path.
- [x] Remove the obsolete `spike/llamaindex_rag/` now that its abstention/chunk contract is implemented by the production stdlib retriever.
- [x] Keep small `list_*` and `show_*` read-only CLIs because they reduce agent context and token use.
- [x] Keep legacy builder/ingest CLI names as compatibility entry points; the structured pipeline has one implementation owner.
- [x] Move completed `PLAN-*` and old `RELEASE-*` files out of the repository root and add lifecycle metadata.
- [x] Rewrite stale architecture/language/localization documents and fix all relative links.
- [x] Remove hard-coded dates/phase counts from templates.

Exit gate: the root contains only active entry/release files, generated outputs have one owner, and no tracked link points to a removed path.

### Phase F - realistic evaluation and release evidence

- [x] Add real stdlib-generated DOCX/PPTX packages plus valid, truncated, and rollback-triggering raster fixtures; cover formulas, tables, notes, controls, hidden content, and question/answer leakage in the deterministic gold set.
- [ ] Add a redistribution-safe real PDF layout fixture pack (text, image-only, multicolumn, and shared prompt/answer crop). Deferred because the default CI intentionally has no mandatory PDF parser/render dependency; tracked as a follow-up rather than weakening the stdlib test floor.
- [x] Test parser capability combinations against the exact registry used by preflight and runtime detection.
- [x] Add a gold manifest for page accounting, chapter assignment, concept/formula/example/question/answer recall, answer pairing, visual dependency, provenance, source/page precision, and answer leakage.
- [x] Test unchanged reruns, source changes, source-hash drift, process crashes, automatic transaction recovery, and patch idempotence.
- [x] Run quick validation for root and every executable sub-skill.
- [x] Run focused ingestion/language tests, then the complete repository suite.
- [x] Forward-test raw user scenarios with an independent agent that receives only the installed skill entry points; all five routes passed, and its wording-boundary suggestions were incorporated.

Exit gate: test evidence covers real adapters and semantic invariants, not only keyword presence and fake backends.

## 6. Dependency and adapter policy

Default core: Python standard library plus whichever already-supported PDF backend passed preflight.

Optional adapters are selected page-by-page or file-by-file after capability probing:

1. native text path (`pypdf` where appropriate);
2. local layout/render path (PyMuPDF or PDFium according to actual capability);
3. optional LiteParse adapter for local spatial text/OCR/screenshot work;
4. optional Docling adapter for high-fidelity tables, formulas, reading order, and mixed formats;
5. host-agent vision for unresolved pages;
6. cloud adapters only after explicit privacy, price, and upload-scope consent.

No adapter is installed silently. A missing optional adapter creates a specific route/consent decision, not a mid-operation crash. The project must remain useful with the core BM25 path and no vector database.

## 7. Deletion safety

Code is deleted only when all of the following are true:

1. its behavior has a new single owner;
2. repository search shows no consumer;
3. compatibility output is migrated or explicitly versioned;
4. focused and full tests pass;
5. the plan records the deletion.

Local ignored/untracked material, student workspaces, and generated reports are never treated as repository cleanup targets.

## 8. Acceptance matrix

| Scenario | Required result |
| --- | --- |
| Course contains only `ch05` | Phase 1 maps explicitly to `ch05`; wiki, quiz, teaching, retrieval, and progress agree. |
| Fully scanned PDF | Every page is accounted for; evidence images and typed review issues exist; build is not silently ready. |
| Unsupported DOCX/PPTX in minimal install | Source is inventoried and produces an actionable issue; no silent skip. |
| AI recovers a formula or question | Valid patch references source evidence, survives re-ingestion, and triggers fresh compilation/indexing. |
| AI patch targets the wrong source revision | Patch validation rejects it. |
| Question and answer share a page | Asset roles and leakage gate prevent answer-first presentation. |
| Wiki changes after indexing | Retrieval refuses stale integrity or rebuilds before use. |
| No quiz bank in web client | Teaching may continue as `covered_unverified`; no quiz is invented. |
| `<=1 day` student starts in Chinese | No opening/template preference question; canonical language route is Chinese. |
| Script fails due to invalid input | Failure is reported; manual fallback is not used unless Python itself is unavailable. |
| Unchanged rerun | Stable IDs and manifests remain deterministic; expensive review work is not repeated. |

## 9. Pull-request delivery checklist

- [x] Every implementation phase above is updated with completed/deferred status and evidence.
- [x] No user-owned unrelated change is staged.
- [x] The diff is reviewed for generated files, secrets, binary bloat, and accidental behavior expansion.
- [x] Documentation states which source-brief requirements were adapted and which were intentionally excluded.
- [x] Tests and forward scenarios are summarized in the PR body.
- [x] Compatibility and migration notes are included.
- [x] Branch is pushed to the maintainer fork.
- [x] A cross-fork Draft PR targets `ZeKaiNie/universal-examprep-skill:main` ([#23](https://github.com/ZeKaiNie/universal-examprep-skill/pull/23)).

## 10. Implementation log

This section is updated while executing the plan. A checked item without a linked test or diff is not considered complete.

| Date | Phase | Evidence | Status |
| --- | --- | --- | --- |
| 2026-07-14 | Audit | Supplied DOCX visually reviewed page-by-page; repository, skills, scripts, reports, and tests audited; official prior art verified. | complete |
| 2026-07-14 | A-B | Added exact-schema source/content/review/patch stores, strict JSON, crash journal/rollback, one-command orchestration, shared PDF capability registry, and fail-closed readiness validation. | complete |
| 2026-07-14 | C | Added structure-aware chunks, typed quiz preservation, concept postings, stale-index rejection, integrity hashes, and deterministic Recall@1/5 + MRR evaluation. | complete |
| 2026-07-14 | D-E | Repaired language/bank/state contracts; reduced locale duplication; retired duplicate indexes, caption gallery, old spike, and root-level historical plans/releases. | complete |
| 2026-07-14 | F | 1,508 repository tests passed (27 skipped); root and all 10 executable skill folders passed `quick_validate.py` under Python UTF-8 mode. | complete with real-PDF fixture follow-up |
| 2026-07-14 | Forward test | Independent cold-reader checked urgent Chinese teaching, missing-bank quiz, warning-bearing ingest, one-shot PDF in chat mode, and English missing-visual handling; no blocking ambiguity, wording boundaries tightened. | complete |
