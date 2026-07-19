# Incremental strict crop-receipt backfill

`scripts/backfill_crop_receipts.py` promotes an audited legacy crop or derives a
tighter audited crop from an already declared PNG in a completed ingestion-v2
workspace. It never invokes the material builder and never invents PDF-page
geometry for a legacy crop.

## Commands

```text
python scripts/backfill_crop_receipts.py validate --workspace <ws> --annotations <audit.jsonl> --json
python scripts/backfill_crop_receipts.py apply    --workspace <ws> --annotations <audit.jsonl> --json
```

`validate` is read-only. `apply` rebuilds the plan under the workspace lock,
publishes a new material generation, and invokes one compiler command:
`scripts/ingest.py --input ... --output-dir ... --expected-input-sha256 ...`.
Full processing, a current runtime receipt, ingestion-v2, and a complete current
material-build receipt are required.

## Annotation protocols

Annotation schema 1 is immutable compatibility history. It continues to accept:

- `upgrade_existing`: promote a declared legacy crop using a separate declared
  page-shaped parent;
- `create_from_parent`: derive one region from a declared full-page parent;
- `create_composite_from_parent`: derive a prompt-only vertical stack from a
  declared full-page parent.

For schema 1, the parent pixels map through `page_box_pdf_points`. A legacy
`crop_image` can act as that parent only when its declared
`source_bbox_pdf_points` equals the complete page box. Existing schema-1
annotations, composition schema 1, receipt IDs, and
`same_parent_vertical_stack_v1` remain unchanged.

New work uses annotation schema 2 and one of these explicit operations:

- `promote_legacy_crop`: copy a clean, currently declared legacy crop to a new
  immutable receipt-owned path. Parent, target, and reviewed crop are the same
  exact PNG revision. This is the safe path for an already well-isolated qcrop
  when no separately declared full-page PNG exists.
- `create_from_legacy_crop`: derive one tighter region from a declared legacy
  crop. The reviewed candidate must be a separate PNG whose RGBA bytes equal
  the exact integer crop of the parent.
- `create_composite_from_legacy_crop`: derive a prompt-only deterministic
  vertical stack of 2–32 non-overlapping regions from one declared legacy crop.

Schema 2 always carries `parent_source_bbox_pdf_points`. That rectangle is the
declared source bbox of the legacy parent; it must stay inside
`page_box_pdf_points`. Pixel coordinates map linearly through the parent source
bbox, not through the whole PDF page. This prevents a nested crop from claiming
fake page geometry.

## Schema-2 promotion record

`promote_legacy_crop` uses this exact shape:

```json
{
  "schema_version": 2,
  "record_type": "crop_receipt_backfill",
  "operation": "promote_legacy_crop",
  "item_id": "problem-1.2.3",
  "chapter_id": "ch01",
  "side": "prompt",
  "role": "question_context",
  "content_scope": "full_prompt",
  "source_id": "src_<path-derived-id>",
  "source_path": "homework.pdf",
  "source_sha256": "<source-sha256>",
  "source_page": 3,
  "page_box_pdf_points": [0.0, 0.0, 612.0, 792.0],
  "parent_source_bbox_pdf_points": [58.0, 144.0, 552.0, 356.0],
  "bbox_pdf_points": [58.0, 144.0, 552.0, 356.0],
  "parent_asset_path": "references/assets/homework_p003_qcrop.png",
  "parent_asset_sha256": "<crop-sha256>",
  "parent_width": 988,
  "parent_height": 424,
  "target_asset_path": "references/assets/homework_p003_qcrop.png",
  "target_asset_sha256": "<same-crop-sha256>",
  "crop_asset_path": "references/assets/homework_p003_qcrop.png",
  "crop_asset_sha256": "<same-crop-sha256>",
  "crop_width": 988,
  "crop_height": 424,
  "semantic_purity": {
    "schema_version": 2,
    "target_item_id": "problem-1.2.3",
    "side": "prompt",
    "crop_sha256": "<same-crop-sha256>",
    "verdict": "target_item_only",
    "unrelated_content_present": false,
    "student_attempt_present": false,
    "detected_item_ids": ["problem-1.2.3"],
    "reviewer_kind": "model_vision",
    "reviewer": "vision-model:<exact identity>",
    "reviewed_at": "2026-07-18T12:34:56Z",
    "evidence_binding_sha256": "<canonical-evidence-hash>",
    "required_context_ids": []
  }
}
```

The declared asset must be an unreceipted `crop_image` with the exact path,
PNG hash, role, source file, current source hash, source page, item, chapter, and
source bbox. `bbox_pdf_points` must equal `parent_source_bbox_pdf_points`.

## Schema-2 nested single crop

`create_from_legacy_crop` adds this geometry to the promotion fields:

```json
{
  "operation": "create_from_legacy_crop",
  "parent_source_bbox_pdf_points": [58.0, 144.0, 552.0, 356.0],
  "bbox_parent_pixels": [40, 50, 940, 350],
  "bbox_pdf_points": [78.0, 169.0, 528.0, 319.0],
  "target_asset_path": "references/assets/homework_p003_qcrop.png",
  "target_asset_sha256": "<parent-sha256>",
  "crop_asset_path": ".ingest/crop-review/problem-1.2.3-tight.png",
  "crop_asset_sha256": "<candidate-sha256>",
  "crop_width": 900,
  "crop_height": 300
}
```

The complete JSONL row still contains every common field and the semantic-v2
object. Target equals parent; candidate is separate and must not already be a
raw declaration. The candidate must equal the exact unscaled parent pixel
selection. A whole-parent selection is rejected because it should use
`promote_legacy_crop` instead.

## Schema-2 nested composite

`create_composite_from_legacy_crop` replaces `bbox_parent_pixels` and
`bbox_pdf_points` with `regions` and `stack`:

```json
{
  "schema_version": 2,
  "operation": "create_composite_from_legacy_crop",
  "parent_source_bbox_pdf_points": [40.0, 90.0, 570.0, 700.0],
  "regions": [
    {
      "region_id": "required-context",
      "content_ids": ["theorem-1.1.1"],
      "bbox_parent_pixels": [80, 40, 1080, 310],
      "bbox_pdf_points": [75.33333333333333, 105.25, 517.0, 208.1875]
    },
    {
      "region_id": "target-problem",
      "content_ids": ["problem-1.1.2"],
      "bbox_parent_pixels": [80, 620, 1080, 1180],
      "bbox_pdf_points": [75.33333333333333, 326.375, 517.0, 539.875]
    }
  ],
  "stack": {
    "schema_version": 2,
    "layout": "vertical_stack",
    "region_order": ["required-context", "target-problem"],
    "gap_pixels": 24,
    "background_rgba": [255, 255, 255, 255],
    "horizontal_alignment": "left"
  }
}
```

The example coordinates are illustrative; production annotations must use the
exact linear mapping from the declared parent dimensions and source bbox.
Region content IDs may name only the target and sorted required contexts. Their
union must cover exactly those IDs. Output width is the widest region; output
height is the sum of region heights plus the declared gaps. There is no OCR,
rescaling, arbitrary editing, or whole-page fallback. The resulting receipt
uses the compatible `same_source_crop_vertical_stack_v2` variant and preserves
the complete composition.

## Semantic evidence binding

Every new operation requires semantic-purity schema 2. For no prerequisite,
use `required_context_ids=[]`, `verdict=target_item_only`, and detected IDs
`[target_item_id]`. A dependent prompt uses sorted unique contexts,
`verdict=target_with_required_context`, and detected IDs exactly
`[target_item_id] + required_context_ids`. Answer assets cannot carry required
contexts.

Hash the repository canonical JSON encoding of the operation-specific evidence
object. Schema-2 evidence always includes the exact source revision and
`parent_source_bbox_pdf_points`.

Promotion evidence:

```json
{
  "schema_version": 2,
  "evidence_kind": "backfill_declared_legacy_crop_promotion",
  "operation": "promote_legacy_crop",
  "target_item_id": "problem-1.2.3",
  "side": "prompt",
  "source_id": "src_<id>",
  "source_file": "homework.pdf",
  "source_sha256": "<source-sha256>",
  "source_page": 3,
  "page_box_pdf_points": [0.0, 0.0, 612.0, 792.0],
  "parent_source_bbox_pdf_points": [58.0, 144.0, 552.0, 356.0],
  "bbox_pdf_points": [58.0, 144.0, 552.0, 356.0],
  "parent_asset_path": "references/assets/homework_p003_qcrop.png",
  "parent_asset_sha256": "<crop-sha256>",
  "parent_width": 988,
  "parent_height": 424,
  "target_asset_path": "references/assets/homework_p003_qcrop.png",
  "target_asset_sha256": "<crop-sha256>",
  "crop_asset_path": "references/assets/homework_p003_qcrop.png",
  "crop_asset_sha256": "<crop-sha256>",
  "crop_width": 988,
  "crop_height": 424,
  "required_context_ids": []
}
```

Nested single-crop evidence changes `evidence_kind` to
`backfill_nested_parent_pixel_crop` and also carries
`bbox_parent_pixels`. Nested composite evidence uses
`backfill_nested_parent_vertical_stack`, the normalized composition (including
its parent source bbox), the union `bbox_pdf_points`, candidate hash, and
required contexts. Schema-1 evidence objects remain exactly as previously
documented by their annotation protocol; never recalculate them as schema 2.

## Declaration reconciliation

One physical asset can appear in a quiz/teaching row, a ContentUnit top-level
`asset_path`, and both units of a reciprocal question/answer pair. Before
selection, backfill reconciles only declarations with the same canonical
physical path, exact PNG hash, role, source file/page/revision, logical item,
and chapter. The sole missing-PNG-hash compatibility case is a ContentUnit
top-level `asset_path` whose own `metadata.asset_sha256` is absent: it may bind
to one unambiguous exact revision exposed by the same unit's nested
`metadata.assets` declaration when path, role, item, chapter, source/page, and
source revision all agree. Quiz/teaching rows and declarations in another unit
never inherit an asset hash through this exception. Missing `type` or
`source_bbox_pdf_points` may inherit only when all exact mirrors expose one
unambiguous value.

At least one declaration mirror or ContentUnit owner in an otherwise exact
group must already carry the current source revision. Null-only source revision
groups are rejected rather than being relabelled with the annotation's current
hash, and any stale or conflicting declared revision fails closed.

A prompt asset mirrored inside an answer unit is owned by the paired question's
source/page unless the asset itself supplies explicit source metadata. The
equivalent rule assigns an answer mirror inside a question unit to the paired
answer. Thus a storage mirror never becomes a foreign answer-page owner.

Publication replaces every exact mirror, clears a matching ContentUnit
top-level pair, and writes one compact receipt asset per affected container.
Duplicate compact assets are folded; conflicting duplicate captions fail
closed. Duplicate folding is deferred until every annotation in the batch has
finished replacing its frozen declaration indexes, so one fold cannot shift a
later prompt/answer target onto the wrong list entry. A crop path shared by
another item/source/revision cannot be promoted or used as a nested parent.

## Safety and provenance boundaries

- New receipts require semantic-purity schema 2. Historical semantic v1 stays
  readable but cannot be minted by backfill.
- Prompt candidates may derive from a student-attempt-tainted parent only when
  the separate reviewed candidate is clean. A promoted crop itself cannot be
  tainted. Every answer parent, target, candidate, and output remains
  official-only.
- Existing receipt-schema-v2 single crops and receipt-schema-v1 history retain
  their IDs. New nested composites add a compatible variant; they do not
  reinterpret old receipts.
- Automatic layout crops remain usable as legacy teaching/quiz assets. A
  filename such as `qcrop` never implies semantic purity.
- Backfill copies verified bytes to a new digest-named path and records the old
  target under `supersedes`. It never attaches a receipt to the old path.
- Reapplying a successful audit fails because the target is no longer a current
  unreceipted declaration.

## Apply execution receipt

The `apply` result contains this separately hashed receipt:

```json
{
  "schema_version": 1,
  "record_type": "crop_receipt_backfill_compiler_execution",
  "invoked_compiler_command": ["<python>", "<scripts/ingest.py>", "..."],
  "compiler_exit_code": 0,
  "before": {
    "parser_receipts_sha256": "<sha256>",
    "source_manifest_sha256": "<sha256>"
  },
  "after": {
    "parser_receipts_sha256": "<sha256>",
    "source_manifest_sha256": "<sha256>"
  }
}
```

This receipt records the one subprocess command invoked by the backfill host and
its integer exit code plus the exact control-file bytes observed immediately
before and after it. All four facts participate in the receipt hash for both
successful and failed compiler attempts. It is not a sandbox, remote
attestation, or proof about commands that `ingest.py` could internally invoke.
The implementation's compiler-only boundary is a declared workflow property;
the receipt deliberately does not claim
`parser_invoked=false` or `pdfs_reparsed=0` as independently attested facts.

Compiler failure keeps `material_build_pending.json` fail-closed for the normal
resume/supersede recovery path and still reports the observed execution receipt.
The changed asset/receipt bindings also change the Study Guide authoring packet;
use the separate annotation-rebind workflow instead of rewriting authored prose.
