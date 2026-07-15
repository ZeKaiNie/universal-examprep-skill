# Synthetic ingestion Gold Set

This directory contains tiny, project-authored fixtures for deterministic
ingestion tests. They include no real course material or third-party media.
All source descriptions and generated artifacts are dedicated under
`CC0-1.0`; see `LICENSE`.

`source.json` is the human-reviewable source of expected page, unit, citation,
and crop facts. `generate.py` creates the binary fixtures and `manifest.json`
using only Python's standard library. The manifest records every generated
artifact's SHA-256 digest and byte size as well as the SHA-256 of `source.json`.
It deliberately contains no timestamps or host paths.

Regenerate in place from the repository root:

```console
python tests/fixtures/ingestion_gold/generate.py
```

The pack exercises:

- a two-page PDF with deterministic multicolumn stream order and a
  vector/table/formula page;
- an image-only PDF plus its independently ingestible PNG source;
- a same-page prompt/answer PDF with declared, non-overlapping crops; and
- a minimal XLSX workbook with shared strings, a formula/cached value, and a
  defined table.

`tests/test_ingestion_gold.py` regenerates the pack in a temporary directory
and byte-compares it with the committed artifacts. Optional PDF parser checks
skip honestly when their package is absent; reproducibility itself has no
optional dependency.
