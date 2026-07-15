# Retrieval gold contract

`schema.json` is a portable description of the committed JSON shape. The
executable, fail-closed validator is `scripts/retrieval_evaluation.py`.

`sample.insufficient.json` is intentionally tiny and synthetic. It exists to
exercise the CLI and must produce `INSUFFICIENT_EVIDENCE`; it is not evidence
for adding a dense retriever, RRF, or reranker.

```powershell
python benchmark/run_retrieval_gate.py --gold benchmark/retrieval_gold/sample.insufficient.json
```

A promotion gold set must bind the exact `retrieval_index.json` SHA-256, use a
separate frozen `test` split, and provide exact source/unit/hash evidence for
every relevant unit. Unanswerable rows are near-miss probes and therefore have
no relevant units or positive evidence.
