# Evidence-gated optional retrieval

The shipped answer-time default remains the standard-library BM25 implementation
in `scripts/retrieve.py`. Dense search, RRF, and reranking are not required
dependencies and are not silently selected when an optional backend is missing.

## Why a separate gate exists

The ingestion evaluator already provides deterministic Recall@1, Recall@5, and
MRR arithmetic, while the older benchmark trace evaluator measures whether an
agent opened the correct chapter. Neither is a reproducible BM25-versus-hybrid
experiment: its gold data does not carry the actual query, near-miss negatives,
backend identity, latency, or fusion inputs.

`scripts/retrieval_evaluation.py` supplies that missing strict contract without
adding a retrieval backend. `benchmark/run_retrieval_gate.py` invokes the
existing BM25 API and may compare it with an explicit experimental candidate
receipt.

## Gold and run receipts

The executable schema is the strict validator; unknown fields, duplicate IDs,
non-finite numbers, missing query results, source/index hash drift, and labeled
units that do not resolve to indexed chunks fail closed.

Each gold query records:

- stable query/course IDs, exact query text, language, and `answerable`;
- relevant and hard-negative source unit IDs with no overlap;
- slice tags and exact source ID/page/SHA-256 evidence for every relevant unit.

An unanswerable near-miss has no relevant units or positive evidence. It may
name topically tempting hard-negative units.

Each run binds the canonical gold hash, exact `retrieval_index.json` hash,
backend name/kind/version/config hash, query accounting, resource measurements,
and ranked results. Each hit keeps its opaque `score_kind`; BM25, dense, RRF,
and reranker scores are never compared as though they shared a scale.

RRF/hybrid receipts also bind every direct leaf input-result hash, the rank
constant, fusion window, and output `top_k`. The validator rejects nested
fusion/reranker inputs and independently recomputes every query's complete
ranked output, abstention, document/unit identities, component provenance, and
rounded score before metrics may use the receipt. The experiment helper
implements:

```text
score(document) = sum(1 / (rank_constant + component_rank))
```

Exact fused-score ties break by stable document ID. A reranker can only reorder
the supplied pool and must score every candidate; it cannot introduce or omit
document IDs.

## Reproducible CLI

The committed sample proves only that insufficient evidence is reported:

```powershell
python benchmark/run_retrieval_gate.py `
  --gold benchmark/retrieval_gold/sample.insufficient.json
```

A real frozen run uses:

```powershell
python benchmark/run_retrieval_gate.py `
  --gold path/to/frozen-test.json `
  --workspace-map path/to/workspaces.json `
  --candidate-receipt path/to/experimental-run.json `
  --baseline-out path/to/bm25-run.json `
  --out path/to/decision.json
```

`workspaces.json` is strict JSON. Its course IDs must exactly match the Gold Set,
and every course must name a distinct absolute workspace:

```json
{
  "schema_version": 1,
  "courses": [
    {"course_id": "course-a", "workspace": "D:\\absolute\\course-a-workspace"}
  ]
}
```

The runner does not import/download a dense model. Candidate generation is a
separate, maintainer-controlled experiment. Missing or malformed candidate
output is never relabeled as BM25 success.

## Three-state decision

`INSUFFICIENT_EVIDENCE` means no backend decision is justified. The frozen test
set needs at least 180 queries: 120 answerable, 40 near-miss unanswerable, three
courses with 30 answerable queries each, and 20 answerable rows in every
critical slice (`paraphrase`, `cross_language`, `formula_symbol`,
`question_or_figure`, `rare_term`). Tuning uses a separate dev set.

`NO_GO` keeps BM25 alone. Candidate investigation is unnecessary when BM25 has
Recall@5 at least 0.90 overall and at least 0.85 in every critical slice.

`GO_OPTIONAL` authorizes an opt-in hybrid only when all gates pass:

- Recall@5 improves by at least 0.05, its paired bootstrap 95% CI is positive,
  and paired Hit@5 McNemar p is below 0.05;
- candidate Recall@5 is at least 0.92, every critical slice is at least 0.85,
  no slice drops by more than 0.02, and MRR drops by no more than 0.01;
- the frozen near-miss set has zero false accepts; hard-negative intrusion is no
  worse than BM25 and at most 0.05;
- p95 query latency is at most 500 ms and 10x BM25, normalized index size is at
  most 1 GiB per 100,000 chunks and 10x BM25, and repeated top-five stability is
  at least 0.99.

A reranker is judged separately against the winning candidate: Recall@1 and MRR
must each improve by 0.05 with positive paired intervals, Hit@1 must be
significant, Recall@5 may not decline, and every safety/resource gate still
applies.

Passing this gate permits only an optional backend. Replacing the BM25 default
would require a separate end-to-end answer-quality, privacy, and cost decision.
Programmatic threshold overrides may only tighten these defaults; the evaluator
rejects any override that would reduce the required evidence or safety margin.
