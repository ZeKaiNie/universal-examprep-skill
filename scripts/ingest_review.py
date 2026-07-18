#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Inspect, claim, validate, apply, and rebuild typed ingestion review work."""

import argparse
import collections
import copy
import hashlib
import json
import os
import sys

try:
    import exam_start
    from asset_crops import (
        CropContractError,
        compact_asset_from_receipt,
        load_crop_receipt_report,
        verify_crop_asset_live_binding,
    )
    from ingestion import (
        EvidenceRef,
        ContentUnit,
        IngestionStore,
        ReviewIssue,
        ReviewPatch,
        atomic_write_json,
        canonical_json,
        file_sha256,
        read_json,
        safe_workspace_entry,
        workspace_state_lock,
    )
    from ingestion.pipeline import (
        BUILD_MANIFEST_PATH,
        compile_review_outputs,
        refresh_build_manifest,
    )
except ImportError:
    from scripts import exam_start
    from scripts.asset_crops import (
        CropContractError,
        compact_asset_from_receipt,
        load_crop_receipt_report,
        verify_crop_asset_live_binding,
    )
    from scripts.ingestion import (
        EvidenceRef,
        ContentUnit,
        IngestionStore,
        ReviewIssue,
        ReviewPatch,
        atomic_write_json,
        canonical_json,
        file_sha256,
        read_json,
        safe_workspace_entry,
        workspace_state_lock,
    )
    from scripts.ingestion.pipeline import (
        BUILD_MANIFEST_PATH,
        compile_review_outputs,
        refresh_build_manifest,
    )


def _die(message, code=2):
    sys.stderr.write("ingest_review: %s\n" % message)
    raise SystemExit(code)


def _store(workspace):
    root = os.path.abspath(workspace)
    if not os.path.isdir(root):
        _die("workspace does not exist: %s" % root)
    manifest_path = os.path.join(root, *BUILD_MANIFEST_PATH.split("/"))
    try:
        manifest = read_json(manifest_path)
    except Exception as exc:
        _die("cannot read .ingest/build_manifest.json: %s" % exc)
    source_root = manifest.get("source_root") if isinstance(manifest, dict) else None
    if not isinstance(source_root, str) or not os.path.isdir(source_root):
        _die("source_root is missing or no longer exists")
    return root, IngestionStore(root, source_root=source_root)


def _issue_payload(issue):
    return issue.to_dict()


def _template(issue):
    return {
        "instructions": [
            "Use ReviewPatch.create(...) to generate stable patch_id after filling operations.",
            "Keep status=validated only after checking every evidence hash and source page.",
            "Allowed operations never mutate arbitrary workspace paths.",
        ],
        "issue": issue.to_dict(),
        "allowed_operation_shapes": [
            {"op": "add_unit", "unit": "<full ContentUnit object>"},
            {
                "op": "replace_unit", "unit_id": "<unit_id>",
                "expected_unit_sha256": "<required canonical current-unit sha256>",
                "unit": "<full ContentUnit object>",
            },
            {
                "op": "assign_chapter", "unit_id": "<unit_id>",
                "chapter": "<label>", "phase": "<label>",
                "chapter_id": "chNN", "phase_id": "phaseNN",
            },
            {
                "op": "pair_qa",
                "question_unit_id": "<unit_id>",
                "answer_unit_id": "<unit_id>",
            },
            {"op": "classify_asset", "unit_id": "<unit_id>", "asset_role": "<role>"},
            {"op": "mark_resolved", "reason": "<what the evidence confirms>"},
            {"op": "mark_unrecoverable", "reason": "<why evidence cannot be recovered>"},
        ],
    }


def _bounded_details_path(workspace, value):
    """Keep potentially huge detail exports inside the confirmed workspace."""
    if value is None:
        return None
    candidate = os.path.abspath(os.path.join(workspace, value))
    try:
        contained = os.path.commonpath((workspace, candidate)) == workspace
    except ValueError:
        contained = False
    if not contained:
        _die("--details-file must stay inside the workspace")
    parent = os.path.dirname(candidate)
    if not os.path.isdir(parent):
        _die("--details-file parent does not exist: %s" % parent)
    if os.path.lexists(candidate) and os.path.islink(candidate):
        _die("--details-file must not be a symbolic link")
    return candidate


def _issue_summary(issues):
    by_status = collections.Counter()
    by_severity = collections.Counter()
    by_reason = collections.Counter()
    for issue in issues:
        by_status[issue.status] += 1
        by_severity[issue.severity] += 1
        for reason in issue.reason_codes:
            by_reason[reason] += 1
    return {
        "by_status": dict(sorted(by_status.items())),
        "by_severity": dict(sorted(by_severity.items())),
        "by_reason": dict(sorted(by_reason.items())),
    }


def _load_patch(path):
    try:
        return ReviewPatch.from_dict(read_json(path))
    except Exception as exc:
        _die("patch validation failed for %s: %s" % (path, exc))


def _report_issue(workspace, store, args):
    """Persist an evidence-bound issue discovered during AI/human source review."""

    reasons = sorted(set(args.reason or ()))
    pages = sorted(set(args.page or ()))
    targets = sorted(set(args.target_unit or ()))
    if not reasons:
        _die("report needs at least one --reason")
    if not pages and not targets:
        _die("report needs at least one --page or --target-unit")
    if any(page < 1 for page in pages):
        _die("--page values must be >= 1")

    with store.mutation_lock():
        source = store.manifest.get(args.source_id)
        if source is None:
            _die("report source is absent from the source manifest: %s" % args.source_id)
        store.manifest.verify_current(source.source_id, source.sha256)
        units, _unused_mappings = store._expected_compiled_state()
        valid_pages = {
            unit.page for unit in units.values()
            if unit.source_id == source.source_id
            and unit.source_sha256 == source.sha256
        }
        receipt_path = safe_workspace_entry(workspace, ".ingest/parser_receipts.json")
        receipt_document = read_json(receipt_path, default={})
        receipts = (
            receipt_document.get("receipts", ())
            if isinstance(receipt_document, dict) else ()
        )
        for receipt in (receipts if isinstance(receipts, list) else ()):
            if (isinstance(receipt, dict)
                    and receipt.get("source_file") == source.path
                    and receipt.get("source_sha256") == source.sha256):
                produced = receipt.get("produced_pages")
                if isinstance(produced, list):
                    valid_pages.update(
                        page for page in produced
                        if isinstance(page, int) and not isinstance(page, bool) and page >= 1
                    )
        unknown_pages = sorted(set(pages) - valid_pages)
        if unknown_pages:
            _die(
                "report pages are absent from this source revision's units/parser receipt: %s"
                % ", ".join(str(page) for page in unknown_pages)
            )
        for unit_id in targets:
            unit = units.get(unit_id)
            if unit is None:
                _die("report target unit does not exist: %s" % unit_id)
            if unit.source_id != source.source_id or unit.source_sha256 != source.sha256:
                _die("report target unit does not belong to the selected source revision: %s"
                     % unit_id)

        stable = {
            "kind": "reviewer_discovered",
            "reason_codes": reasons,
            "source_file": source.path,
            "pages": pages,
            "target_unit_ids": targets,
            "description": args.description.strip(),
            "suggested_action": args.suggested_action.strip(),
            "severity": args.severity,
        }
        evidence_payload = {
            "schema_version": 1,
            "source_id": source.source_id,
            "source_file": source.path,
            "source_sha256": source.sha256,
            "candidate": stable,
        }
        encoded = (
            json.dumps(evidence_payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        path_digest = hashlib.sha256(
            canonical_json(evidence_payload).encode("utf-8")
        ).hexdigest()
        evidence_rel = ".ingest/evidence/%s/%s.json" % (source.source_id, path_digest)
        evidence = EvidenceRef(evidence_rel, hashlib.sha256(encoded).hexdigest())
        issue = ReviewIssue.create(
            source.source_id,
            source.sha256,
            reasons,
            (evidence,),
            stable["description"],
            stable["suggested_action"],
            pages=pages,
            target_unit_ids=targets,
            severity=args.severity,
        )
        current = store.review_queue.get(issue.issue_id)
        created = current is None
        if current is not None:
            expected_current = issue.to_dict()
            expected_current["status"] = current.status
            if current.to_dict() != expected_current:
                _die("existing review issue disagrees with the deterministic report payload")

        # Evidence, queue, source status, and the two selected manifest hashes
        # form one control-plane mutation.  A later exception must not leave a
        # partial issue that an idempotent rerun can no longer repair.
        with store.ingest_transaction((
                evidence_rel,
                ".ingest/review_queue.jsonl",
                ".ingest/source_manifest.json",
                ".ingest/build_manifest.json",
        )):
            atomic_write_json(
                safe_workspace_entry(workspace, evidence_rel), evidence_payload
            )
            if created:
                store.review_queue.append(issue)
            store.refresh_source_statuses()
            refresh_build_manifest(
                workspace,
                rehash_artifacts=False,
                rehash_artifact_names=("review_queue", "source_manifest"),
            )
        if current is not None:
            issue = current
    return {"created": created, "issue": issue.to_dict()}


def _canonical_sha256(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _inline_title_prefix(text, title):
    normalized_text = " ".join(str(text or "").split()).casefold()
    normalized_title = " ".join(str(title or "").split()).casefold()
    return bool(
        normalized_title
        and normalized_text.startswith(normalized_title)
        and len(normalized_text) > len(normalized_title)
        and (
            normalized_text[len(normalized_title)].isspace()
            or normalized_text[len(normalized_title)] in (":.-" + "\u2013\u2014")
        )
    )


def _verified_inline_issue(workspace, store, question_id, material_id, crop_id):
    """Return an already registered exact issue without reopening applied work."""

    for issue in store.review_queue.issues():
        if (
            "inline_worked_answer_candidate" not in issue.reason_codes
            or tuple(issue.target_unit_ids) != (question_id,)
            or len(issue.evidence) != 1
        ):
            continue
        evidence = issue.evidence[0]
        evidence_path = safe_workspace_entry(workspace, evidence.path)
        if (
            not evidence_path.is_file()
            or evidence_path.is_symlink()
            or file_sha256(evidence_path) != evidence.sha256
        ):
            _die("registered inline-worked evidence is missing or drifted")
        payload = read_json(evidence_path)
        if not isinstance(payload, dict) or payload.get("kind") != "inline_worked_answer_review":
            continue
        if (
            payload.get("question_unit_id") == question_id
            and payload.get("material_unit_id") == material_id
            and payload.get("crop_receipt_id") == crop_id
        ):
            return issue
    return None


def _inline_worked_context(
        workspace, store, question_unit_id, material_unit_id, crop_receipt_id):
    """Rehydrate and strictly validate one proposed same-page worked example."""

    build_manifest = read_json(
        safe_workspace_entry(workspace, BUILD_MANIFEST_PATH)
    )
    if (
        not isinstance(build_manifest, dict)
        or build_manifest.get("pipeline_version") != "ingestion-v2"
    ):
        _die("inline-worked ledger migration requires ingestion-v2")
    units, _unused_mappings = store._expected_compiled_state()
    question = units.get(question_unit_id)
    if question is None or question.kind != "question":
        _die("--question-unit must name one current question ContentUnit")
    source = store.manifest.get(question.source_id)
    if source is None:
        _die("question source is absent from the current manifest")
    store.manifest.verify_current(source.source_id, source.sha256)
    if (
        question.source_sha256 != source.sha256
        or question.source_file != source.path
        or question.provenance != "material"
        or not question.external_id
        or question.paired_unit_id is not None
    ):
        _die(
            "inline-worked registration needs an unpaired material question on the "
            "current source revision"
        )
    if question.chapter_id is None:
        _die("inline-worked question needs a canonical chapter assignment")
    source_pages = question.metadata.get("source_pages") or [question.page]
    if source_pages != [question.page]:
        _die("inline-worked question must bind exactly one source page")

    quiz_path = safe_workspace_entry(workspace, "references/quiz_bank.json")
    quiz_rows = read_json(quiz_path, default=[])
    if not isinstance(quiz_rows, list):
        _die("references/quiz_bank.json must be a list")
    if any(
        isinstance(row, dict) and str(row.get("id")) == question.external_id
        for row in quiz_rows
    ):
        _die("inline worked examples are teaching-only and must not exist in quiz_bank")

    teaching_path = safe_workspace_entry(
        workspace, "references/teaching_examples.json"
    )
    teaching_rows = read_json(teaching_path, default=[])
    if not isinstance(teaching_rows, list):
        _die("references/teaching_examples.json must be a list")
    matches = [
        row for row in teaching_rows
        if isinstance(row, dict) and str(row.get("id")) == question.external_id
    ]
    if len(matches) != 1:
        _die("inline-worked question needs exactly one teaching_examples row")
    teaching = matches[0]
    title = teaching.get("title")
    if (
        teaching.get("teaching_role") != "worked_example"
        or teaching.get("gradable") is not False
        or teaching.get("source") != "material"
        or teaching.get("source_file") != question.source_file
        or teaching.get("source_pages") != [question.page]
        or teaching.get("answer") not in (None, "", [], {})
        or teaching.get("answer_origin") is not None
        or not isinstance(title, str)
        or not title.strip()
        or title != title.strip()
        or any(char in title for char in ("\x00", "\n", "\r"))
    ):
        _die(
            "teaching row must be an unanswered, non-gradable material worked_example "
            "on the question's exact source page"
        )

    material = units.get(material_unit_id)
    if material is None:
        _die("--material-unit does not exist")
    if (
        material.kind != "text"
        or material.method != "native"
        or material.provenance != "material"
        or material.external_id is not None
        or material.paired_unit_id is not None
        or material.source_id != question.source_id
        or material.source_sha256 != question.source_sha256
        or material.source_file != question.source_file
        or material.page != question.page
        or material.metadata.get("source_language") not in ("zh", "en")
        or not _inline_title_prefix(material.text, title)
    ):
        _die(
            "--material-unit must be the same-page native zh/en material text "
            "beginning with the exact teaching title"
        )
    native_candidates = [
        unit for unit in units.values()
        if unit.kind == "text"
        and unit.method == "native"
        and unit.provenance == "material"
        and unit.external_id is None
        and unit.source_id == question.source_id
        and unit.source_sha256 == question.source_sha256
        and unit.source_file == question.source_file
        and unit.page == question.page
        and unit.metadata.get("source_language") in ("zh", "en")
        and _inline_title_prefix(unit.text, title)
    ]
    if len(native_candidates) != 1 or native_candidates[0].unit_id != material.unit_id:
        _die(
            "same-page title match is ambiguous; inline material needs exactly one "
            "native text unit"
        )

    try:
        _unused_report, receipt_index = load_crop_receipt_report(workspace)
        receipt = receipt_index.get(crop_receipt_id)
        if receipt is None:
            raise CropContractError("crop receipt ID is absent from parse_report")
        compact_asset = compact_asset_from_receipt(receipt)
        receipt = verify_crop_asset_live_binding(
            workspace,
            store.source_root,
            compact_asset,
            receipt_index=receipt_index,
            expected_item_id=question.external_id,
            expected_chapter_id=question.chapter_id,
            require_current_semantic=True,
        )
    except CropContractError as exc:
        _die("inline-worked crop validation failed: %s" % exc)
    if (
        receipt.schema_version != 2
        or receipt.semantic_purity.schema_version != 2
        or receipt.side != "prompt"
        or receipt.role != "question_context"
        or receipt.content_scope != "full_prompt"
        or receipt.isolation not in (
            "target_item_only", "target_with_required_context"
        )
        or receipt.source_id != question.source_id
        or receipt.source_sha256 != question.source_sha256
        or receipt.source_file != question.source_file
        or receipt.source_page != question.page
    ):
        _die(
            "inline-worked migration needs a current full-prompt semantic-v2 crop "
            "for the exact item/source/page"
        )
    compact_asset = compact_asset_from_receipt(receipt)
    question_assets = question.metadata.get("assets") or []
    if (
        not isinstance(question_assets, list)
        or sum(
            1 for asset in question_assets
            if canonical_json(asset) == canonical_json(compact_asset)
        ) != 1
    ):
        _die(
            "question unit must already carry the exact live crop receipt declaration "
            "in metadata.assets"
        )
    # ``metadata.assets`` is the receipt-bound authority.  A material-build
    # crop overlay can intentionally leave the legacy top-level mirror empty
    # so historical replace-unit CAS digests remain replayable.  Accept that
    # exact absence, or an already-promoted exact mirror; reject every partial
    # or unrelated top-level declaration.  The constrained patch below safely
    # promotes the verified crop to the top-level mirror.
    top_level_asset = (question.asset_path, question.asset_role)
    if top_level_asset not in (
        (None, None),
        (compact_asset["path"], "question_context"),
    ):
        _die(
            "question unit top-level asset must be absent or exactly mirror the "
            "live prompt crop"
        )
    return {
        "source": source,
        "question": question,
        "material": material,
        "teaching": copy.deepcopy(teaching),
        "receipt": receipt,
        "compact_asset": compact_asset,
        "units": units,
    }


def _register_inline_worked(workspace, store, args):
    reviewer = args.reviewer.strip()
    review_note = args.review_note.strip()
    if not reviewer:
        _die("--reviewer must be non-empty")
    if not review_note:
        _die("--review-note must explain why the page is one complete worked example")

    with store.mutation_lock():
        current = _verified_inline_issue(
            workspace,
            store,
            args.question_unit,
            args.material_unit,
            args.crop_receipt_id,
        )
        if current is not None:
            return {"created": False, "issue": current.to_dict()}
        context = _inline_worked_context(
            workspace,
            store,
            args.question_unit,
            args.material_unit,
            args.crop_receipt_id,
        )
        question = context["question"]
        material = context["material"]
        teaching = context["teaching"]
        receipt = context["receipt"]
        source = context["source"]
        evidence_payload = {
            "schema_version": 1,
            "kind": "inline_worked_answer_review",
            "source_id": source.source_id,
            "source_file": source.path,
            "source_sha256": source.sha256,
            "source_page": question.page,
            "question_unit_id": question.unit_id,
            "question_unit": question.to_dict(),
            "question_unit_sha256": _canonical_sha256(question.to_dict()),
            "material_unit_id": material.unit_id,
            "material_unit": material.to_dict(),
            "material_unit_sha256": _canonical_sha256(material.to_dict()),
            "teaching_row": teaching,
            "teaching_row_sha256": _canonical_sha256(teaching),
            "crop_receipt_id": receipt.crop_receipt_id,
            "crop_receipt": receipt.to_dict(),
            "crop_receipt_sha256": _canonical_sha256(receipt.to_dict()),
            "review_verdict": "complete_inline_worked_demonstration",
            "reviewer": reviewer,
            "review_note": review_note,
        }
        encoded = (
            json.dumps(evidence_payload, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n"
        ).encode("utf-8")
        path_digest = _canonical_sha256(evidence_payload)
        evidence_rel = ".ingest/evidence/%s/%s.json" % (
            source.source_id, path_digest
        )
        evidence = EvidenceRef(evidence_rel, hashlib.sha256(encoded).hexdigest())
        issue = ReviewIssue.create(
            source.source_id,
            source.sha256,
            ("inline_worked_answer_candidate",),
            (evidence,),
            (
                "A reviewer identified one complete same-page worked Example, but its "
                "material answer has not yet been represented as a typed teaching pair."
            ),
            (
                "Claim this issue, generate the constrained inline-worked patch, then "
                "validate and apply it through the normal review ledger."
            ),
            pages=(question.page,),
            target_unit_ids=(question.unit_id,),
            severity="blocking",
        )
        superseded = []
        for old in store.review_queue.issues():
            if (
                old.issue_id == issue.issue_id
                or "inline_worked_answer_candidate" not in old.reason_codes
                or question.unit_id not in old.target_unit_ids
                or old.source_id != source.source_id
                or old.source_sha256 != source.sha256
                or question.page not in old.pages
            ):
                continue
            if old.status in ("claimed", "validated", "blocked"):
                _die(
                    "an existing inline-worked candidate is already active; finish or "
                    "release it before registering exact evidence"
                )
            if old.status == "pending":
                superseded.append(old)

        with store.ingest_transaction((
                evidence_rel,
                ".ingest/review_queue.jsonl",
                ".ingest/source_manifest.json",
                ".ingest/build_manifest.json",
        )):
            atomic_write_json(
                safe_workspace_entry(workspace, evidence_rel), evidence_payload
            )
            for old in superseded:
                store.review_queue.replace(old.with_status("superseded"))
            store.review_queue.append(issue)
            store.refresh_source_statuses()
            refresh_build_manifest(
                workspace,
                rehash_artifacts=False,
                rehash_artifact_names=("review_queue", "source_manifest"),
            )
    return {
        "created": True,
        "superseded_issue_ids": [old.issue_id for old in superseded],
        "issue": issue.to_dict(),
    }


def _load_inline_evidence(workspace, issue):
    if len(issue.evidence) != 1:
        _die("inline-worked issue must carry exactly one evidence document")
    evidence = issue.evidence[0]
    path = safe_workspace_entry(workspace, evidence.path)
    if not path.is_file() or path.is_symlink() or file_sha256(path) != evidence.sha256:
        _die("inline-worked evidence is missing or drifted")
    payload = read_json(path)
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or payload.get("kind") != "inline_worked_answer_review"
        or payload.get("review_verdict") != "complete_inline_worked_demonstration"
    ):
        _die("inline-worked evidence has an unsupported schema or verdict")
    return payload


def _draft_inline_worked(workspace, store, args):
    reviewer = args.reviewer.strip()
    if not reviewer:
        _die("--reviewer must be non-empty")
    issue = store.review_queue.get(args.issue_id)
    if issue is None:
        _die("unknown issue_id: %s" % args.issue_id)
    if tuple(issue.reason_codes) != ("inline_worked_answer_candidate",):
        _die("draft-inline-worked only accepts its dedicated typed issue")
    if issue.status != "claimed":
        _die("draft-inline-worked requires a claimed issue; current status: %s" % issue.status)
    evidence = _load_inline_evidence(workspace, issue)
    context = _inline_worked_context(
        workspace,
        store,
        evidence.get("question_unit_id"),
        evidence.get("material_unit_id"),
        evidence.get("crop_receipt_id"),
    )
    question = context["question"]
    material = context["material"]
    teaching = context["teaching"]
    receipt = context["receipt"]
    compact_asset = context["compact_asset"]
    live_bindings = (
        ("question_unit", question.to_dict()),
        ("material_unit", material.to_dict()),
        ("teaching_row", teaching),
        ("crop_receipt", receipt.to_dict()),
    )
    for label, value in live_bindings:
        if evidence.get(label) != value or evidence.get(label + "_sha256") != _canonical_sha256(value):
            _die("inline-worked evidence drifted from live %s" % label)

    title = teaching["title"]
    source_language = material.metadata["source_language"]
    question_data = question.to_dict()
    question_metadata = copy.deepcopy(question.metadata)
    for field in (
        "answer_source_file", "answer_source_pages", "answer_origin",
        "inline_material_source_unit_id", "answer_value",
    ):
        question_metadata.pop(field, None)
    question_metadata.update({
        "quiz_type": question_metadata.get("quiz_type")
            or teaching.get("type") or "subjective",
        "source_type": "example",
        "source": "material",
        "source_pages": [question.page],
        "gradable": False,
        "teaching_role": "worked_example",
        "teaching_title": title,
        # The verified crop is already present exactly once.  Preserve every
        # other typed component instead of collapsing a legitimate multi-asset
        # prompt to one image (or silently dropping tainted evidence).
        "assets": copy.deepcopy(question.metadata.get("assets") or []),
        "requires_assets": True,
        "question_text_status": "full",
        "source_language": source_language,
    })
    question_metadata.pop("maybe_requires_assets", None)
    question_metadata.pop("asset_sha256", None)
    question_data.update({
        "text": material.text,
        "html": material.html,
        "latex": material.latex,
        # Receipt-bearing crops live only in metadata.assets.  Repeating the
        # same path through the legacy top-level mirror would create a second,
        # receipt-less declaration when Study Guide authoring inventories the
        # unit.  Preserve one canonical declaration instead.
        "asset_path": None,
        "asset_role": None,
        "metadata": question_metadata,
        "method": material.method,
        "confidence": material.confidence,
        "provenance": "material",
    })
    proposed_question = ContentUnit.from_dict(question_data)
    answer_metadata = {
        "quiz_type": question_metadata["quiz_type"],
        "source_type": "example",
        "source": "material",
        "answer_source_file": question.source_file,
        "answer_source_pages": [question.page],
        "answer_origin": "inline_material",
        "inline_material_source_unit_id": material.unit_id,
        "teaching_role": "worked_example",
        "teaching_title": title,
        "gradable": False,
        "requires_assets": True,
        "answer_value": material.text,
        "source_language": source_language,
    }
    for field in ("knowledge_point", "knowledge_points"):
        if field in question_metadata:
            answer_metadata[field] = copy.deepcopy(question_metadata[field])
    proposed_answer = ContentUnit.create(
        question.source_id,
        question.source_sha256,
        question.source_file,
        "answer",
        material.text,
        question.page,
        ordinal=question.ordinal + 1,
        external_id=question.external_id,
        section_path=question.section_path,
        chapter_id=question.chapter_id,
        phase_id=question.phase_id,
        metadata=answer_metadata,
        method=material.method,
        confidence=material.confidence,
        provenance="material",
    )
    collision = context["units"].get(proposed_answer.unit_id)
    if collision is not None:
        _die("proposed inline answer unit_id collides with an existing unit")
    operations = [
        {
            "op": "replace_unit",
            "unit_id": question.unit_id,
            "expected_unit_sha256": _canonical_sha256(question.to_dict()),
            "unit": proposed_question.to_dict(),
        },
        {"op": "add_unit", "unit": proposed_answer.to_dict()},
        {
            "op": "pair_qa",
            "question_unit_id": question.unit_id,
            "answer_unit_id": proposed_answer.unit_id,
            "source_revisions": [{
                "source_id": question.source_id,
                "source_sha256": question.source_sha256,
            }],
        },
    ]
    patch = ReviewPatch.create(
        issue.issue_id,
        issue.source_id,
        issue.source_sha256,
        operations,
        list(issue.evidence),
        reviewer=reviewer,
        status="validated",
    )
    output_rel = args.output or (
        ".ingest/review_drafts/%s.inline-worked.patch.json" % issue.issue_id
    )
    output_path = safe_workspace_entry(workspace, output_rel)
    atomic_write_json(output_path, patch.to_dict())
    return {
        "patch_file": str(output_path),
        "patch": patch.to_dict(),
        "next": [
            "validate-patch %s" % str(output_path),
            "apply %s" % str(output_path),
        ],
    }


def _confirm_full_prompt(workspace, store, args):
    """Persist an operator's visual finding on every target prompt asset."""

    issue = store.review_queue.get(args.issue_id)
    if issue is None:
        _die("unknown issue_id: %s" % args.issue_id)
    allowed_reasons = {"full_prompt_asset_confirmed", "full_prompt_metadata_missing"}
    reason_codes = set(issue.reason_codes)
    if not reason_codes or not reason_codes.issubset(allowed_reasons):
        _die(
            "full-prompt confirmation only accepts a dedicated full-prompt review issue"
        )
    if issue.status not in ("claimed", "applied"):
        _die(
            "full-prompt confirmation requires a claimed issue or recovery of its applied patch; "
            "current status: %s"
            % issue.status
        )
    if not issue.target_unit_ids:
        _die("full-prompt confirmation requires issue-bound target units")

    units, _unused_mappings = store._expected_compiled_state()
    recovery_only = issue.status == "applied"
    operations = []
    confirmed = []
    for unit_id in issue.target_unit_ids:
        unit = units.get(unit_id)
        if unit is None:
            _die("full-prompt target unit does not exist: %s" % unit_id)
        if unit.kind != "question":
            _die("full-prompt target is not a question unit: %s" % unit_id)
        metadata = copy.deepcopy(unit.metadata)
        assets = metadata.get("assets")
        if not isinstance(assets, list) or not assets:
            _die("full-prompt target has no typed assets: %s" % unit_id)
        question_indices = [
            index for index, asset in enumerate(assets)
            if isinstance(asset, dict) and asset.get("role") == "question_context"
        ]
        primary = [
            index for index in question_indices
            if unit.asset_path is not None and assets[index].get("path") == unit.asset_path
        ]
        if len(primary) != 1:
            if unit.asset_path is None and len(question_indices) == 1:
                primary = question_indices
            else:
                _die(
                    "full-prompt target needs one unambiguous primary question asset: %s"
                    % unit_id
                )
        asset = assets[primary[0]]
        expected_sha256 = asset.get("sha256")
        if not isinstance(expected_sha256, str):
            _die("full-prompt asset needs an exact sha256: %s" % unit_id)
        absolute = safe_workspace_entry(workspace, asset["path"])
        if not absolute.is_file() or absolute.is_symlink():
            _die("full-prompt asset is missing or unsafe: %s" % asset["path"])
        if file_sha256(absolute) != expected_sha256:
            _die("full-prompt asset drifted: %s" % asset["path"])
        if recovery_only and asset.get("contains_full_prompt") is not True:
            _die(
                "applied full-prompt issue disagrees with the live compiled unit: %s"
                % unit_id
            )
        asset["contains_full_prompt"] = True
        metadata["assets"] = assets
        proposed = unit.to_dict()
        proposed["metadata"] = metadata
        ContentUnit.from_dict(proposed)
        if not recovery_only:
            operations.append({
                "op": "replace_unit",
                "unit_id": unit_id,
                "expected_unit_sha256": hashlib.sha256(
                    canonical_json(unit.to_dict()).encode("utf-8")
                ).hexdigest(),
                "unit": proposed,
            })
        confirmed.append({
            "unit_id": unit_id,
            "asset_path": asset["path"],
            "asset_sha256": expected_sha256,
        })

    patch = None
    try:
        if recovery_only:
            ledger_entries = store.ledger_entries()
            if not any(
                    isinstance(entry, dict)
                    and isinstance(entry.get("patch"), dict)
                    and entry["patch"].get("issue_id") == issue.issue_id
                    and entry["patch"].get("status") == "validated"
                    for entry in ledger_entries):
                _die("applied full-prompt issue has no matching validated ledger patch")
            issue_status = issue.status
        else:
            patch = ReviewPatch.create(
                issue.issue_id,
                issue.source_id,
                issue.source_sha256,
                operations,
                list(issue.evidence),
                reviewer=args.reviewer,
                status="validated",
            )
            result = store.apply_patch(patch)
            issue_status = result.issue_status
        compiled = compile_review_outputs(workspace)
    except Exception as exc:
        _die("full-prompt confirmation/rebuild failed: %s" % exc, code=1)
    return {
        "patch": patch.to_dict() if patch is not None else None,
        "recovered_compile": recovery_only,
        "confirmed": confirmed,
        "issue_status": issue_status,
        "compiled": compiled,
    }


def _batch_patch_paths(args):
    positional = list(args.patch_files or ())
    if args.patch_list and positional:
        _die("choose patch files or --patch-list, not both")
    if args.patch_list:
        try:
            listed = read_json(args.patch_list)
        except Exception as exc:
            _die("invalid --patch-list: %s" % exc)
        if (not isinstance(listed, list) or not listed
                or any(not isinstance(path, str) or not path.strip() for path in listed)):
            _die("--patch-list must be a non-empty JSON string list")
        positional = listed
    if not positional:
        _die("apply-batch needs at least one patch")
    if len(set(positional)) != len(positional):
        _die("patch paths must be unique")
    return positional


def _apply_patch_batch(workspace, store, patch_paths):
    patches = []
    patch_ids = set()
    issue_ids = set()
    for path in patch_paths:
        patch = _load_patch(path)
        if patch.patch_id in patch_ids:
            _die("duplicate patch_id: %s" % patch.patch_id)
        if patch.issue_id in issue_ids:
            _die("duplicate issue_id: %s" % patch.issue_id)
        patch_ids.add(patch.patch_id)
        issue_ids.add(patch.issue_id)
        patches.append((path, patch))

    applied_rows = None
    batch_apply = getattr(store, "apply_patches", None)
    if callable(batch_apply):
        try:
            applied_rows = batch_apply([patch for _path, patch in patches])
        except Exception as exc:
            message = "batch apply failed; earlier ledger entries remain durable: %s" % exc
            try:
                compile_review_outputs(workspace)
            except Exception as rebuild_exc:
                message += "; rebuild failed: %s" % rebuild_exc
            _die(message, code=1)

    results = []
    for index, (path, patch) in enumerate(patches):
        if applied_rows is not None:
            result = applied_rows[index]
        else:
            try:
                result = store.apply_patch(patch)
            except Exception as exc:
                message = "patch %d/%d failed after %d ledger entries: %s" % (
                    index + 1, len(patches), len(results), exc)
                try:
                    compile_review_outputs(workspace)
                except Exception as rebuild_exc:
                    message += "; rebuild failed: %s" % rebuild_exc
                _die(message, code=1)
        results.append({
            "patch_file": path,
            "patch_id": patch.patch_id,
            "issue_id": patch.issue_id,
            "applied": result.applied,
            "replayed": result.replayed,
            "changed_operations": result.changed_operations,
            "issue_status": result.issue_status,
        })
    compiled = compile_review_outputs(workspace)
    return {
        "patch_count": len(results),
        "applied_count": sum(1 for result in results if result["applied"]),
        "replayed_count": sum(1 for result in results if result["replayed"]),
        "results": results,
        "compiled": compiled,
    }


def run(argv=None):
    parser = argparse.ArgumentParser(
        description="Typed AI/human review queue for .ingest workspaces"
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--json", action="store_true", dest="as_json")
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="list review issues")
    list_parser.add_argument(
        "--status", action="append",
        help="filter by status (repeatable; default all)",
    )
    list_parser.add_argument(
        "--reason", action="append",
        help="filter by reason code (repeatable; any matching reason is included)",
    )
    list_parser.add_argument(
        "--cursor", type=int, default=0,
        help="zero-based resume cursor into the stable filtered queue",
    )
    list_parser.add_argument(
        "--limit", type=int, default=50,
        help="maximum issues returned in chat-safe JSON (1-200; default 50)",
    )
    list_parser.add_argument(
        "--summary-only", action="store_true",
        help="return counts without issue bodies",
    )
    list_parser.add_argument(
        "--details-file",
        help="optional workspace-relative JSON file receiving the complete filtered issue list",
    )
    report_parser = sub.add_parser(
        "report",
        help="append a source-revision-bound issue discovered during visual/AI review",
    )
    report_parser.add_argument("--source-id", required=True)
    report_parser.add_argument(
        "--reason", action="append", required=True,
        help="lowercase machine reason code (repeatable)",
    )
    report_parser.add_argument(
        "--page", action="append", type=int,
        help="one-based source location reviewed (repeatable)",
    )
    report_parser.add_argument(
        "--target-unit", action="append",
        help="affected content-unit id from this exact source revision (repeatable)",
    )
    report_parser.add_argument(
        "--severity", choices=("blocking", "warning", "info"), default="warning",
    )
    report_parser.add_argument("--description", required=True)
    report_parser.add_argument("--suggested-action", required=True)
    inline_register = sub.add_parser(
        "register-inline-worked",
        help=(
            "register one visually reviewed same-page worked Example as a typed "
            "ledger issue"
        ),
    )
    inline_register.add_argument("--question-unit", required=True)
    inline_register.add_argument("--material-unit", required=True)
    inline_register.add_argument("--crop-receipt-id", required=True)
    inline_register.add_argument("--reviewer", default="ai-visual-review")
    inline_register.add_argument(
        "--review-note",
        required=True,
        help="why the exact crop/text is one complete worked demonstration",
    )
    inline_draft = sub.add_parser(
        "draft-inline-worked",
        help="generate the constrained replace/add/pair patch for a claimed issue",
    )
    inline_draft.add_argument("issue_id")
    inline_draft.add_argument("--reviewer", default="ai-visual-review")
    inline_draft.add_argument(
        "--output",
        help="workspace-relative patch path (default .ingest/review_drafts/...)",
    )
    show_parser = sub.add_parser("show", help="show one issue and patch operation shapes")
    show_parser.add_argument("issue_id")
    claim_parser = sub.add_parser("claim", help="atomically claim one pending issue")
    claim_parser.add_argument("issue_id")
    prompt_parser = sub.add_parser(
        "confirm-full-prompt",
        help="record that every target's primary question asset visibly contains its full prompt",
    )
    prompt_parser.add_argument("issue_id")
    prompt_parser.add_argument("--reviewer", default="ai-visual-review")
    validate_parser = sub.add_parser("validate-patch", help="strictly validate a patch JSON")
    validate_parser.add_argument("patch_file")
    apply_parser = sub.add_parser("apply", help="apply a validated patch and rebuild derivatives")
    apply_parser.add_argument("patch_file")
    batch_parser = sub.add_parser(
        "apply-batch",
        help="apply distinct patches and rebuild once",
    )
    batch_parser.add_argument(
        "patch_files", nargs="*",
        help="patch JSON files for distinct issues",
    )
    batch_parser.add_argument(
        "--patch-list",
        help="JSON list of patch-file paths",
    )
    mark_parser = sub.add_parser(
        "mark-unrecoverable", help="close one issue with an evidence-bound terminal patch"
    )
    mark_parser.add_argument("issue_id")
    mark_parser.add_argument("--reason", required=True)
    mark_parser.add_argument(
        "--evidence-note",
        help="issue-specific pages/files/search scope reviewed before declaring recovery impossible",
    )
    mark_parser.add_argument("--reviewer", default="ai")
    resolved_parser = sub.add_parser(
        "mark-resolved", help="confirm from evidence that extraction is already complete"
    )
    resolved_parser.add_argument("issue_id")
    resolved_parser.add_argument("--reason", required=True)
    resolved_parser.add_argument("--reviewer", default="ai")
    sub.add_parser("pending", help="inspect an interrupted review-patch intent")
    sub.add_parser(
        "recover-pending",
        help="idempotently resume the exact interrupted review patch and rebuild",
    )
    sub.add_parser("rebuild", help="recompile wiki/quiz/index from current applied IR")

    args = parser.parse_args(argv)
    workspace_root = os.path.abspath(args.workspace)
    with workspace_state_lock(workspace_root):
        try:
            exam_start.require_full_processing(
                workspace_root, purpose="structured ingestion review"
            )
        except exam_start.FullProcessingRequired as exc:
            _die(str(exc))
        workspace, store = _store(workspace_root)
        return _dispatch(args, workspace, store)


def _dispatch(args, workspace, store):

    if args.command == "register-inline-worked":
        try:
            payload = _register_inline_worked(workspace, store, args)
        except SystemExit:
            raise
        except Exception as exc:
            _die("inline-worked registration failed: %s" % exc, code=1)
    elif args.command == "draft-inline-worked":
        try:
            payload = _draft_inline_worked(workspace, store, args)
        except SystemExit:
            raise
        except Exception as exc:
            _die("inline-worked draft failed: %s" % exc, code=1)
    elif args.command == "report":
        try:
            payload = _report_issue(workspace, store, args)
        except SystemExit:
            raise
        except Exception as exc:
            _die("report failed: %s" % exc, code=1)
    elif args.command == "list":
        if args.cursor < 0:
            _die("--cursor must be >= 0")
        if args.limit < 1 or args.limit > 200:
            _die("--limit must be between 1 and 200")
        statuses = set(args.status or ())
        reasons = set(args.reason or ())
        issues = [
            issue for issue in store.review_queue.issues()
            if (not statuses or issue.status in statuses)
            and (not reasons or reasons.intersection(issue.reason_codes))
        ]
        total = len(issues)
        if args.cursor > total:
            _die("--cursor %d exceeds filtered issue count %d" % (args.cursor, total))
        details_path = _bounded_details_path(workspace, args.details_file)
        if details_path is not None:
            atomic_write_json(details_path, {
                "workspace": workspace,
                "count": total,
                "summary": _issue_summary(issues),
                "issues": [_issue_payload(issue) for issue in issues],
            })
        page = [] if args.summary_only else issues[args.cursor:args.cursor + args.limit]
        next_cursor = args.cursor + len(page)
        payload = {
            "workspace": workspace,
            "count": total,
            "returned": len(page),
            "cursor": args.cursor,
            "next_cursor": next_cursor if next_cursor < total else None,
            "has_more": next_cursor < total,
            "summary": _issue_summary(issues),
            "details_file": details_path,
            "issues": [_issue_payload(issue) for issue in page],
        }
    elif args.command == "show":
        issue = store.review_queue.get(args.issue_id)
        if issue is None:
            _die("unknown issue_id: %s" % args.issue_id)
        payload = _template(issue)
    elif args.command == "claim":
        try:
            issue = store.claim_issue(args.issue_id)
            refresh_build_manifest(workspace)
        except Exception as exc:
            _die("claim failed: %s" % exc)
        payload = {"claimed": True, "issue": issue.to_dict()}
    elif args.command == "confirm-full-prompt":
        payload = _confirm_full_prompt(workspace, store, args)
    elif args.command in ("validate-patch", "apply"):
        patch = _load_patch(args.patch_file)
        if args.command == "validate-patch":
            try:
                store.validate_patch(patch)
            except Exception as exc:
                _die("patch contextual validation failed: %s" % exc)
            payload = {"valid": True, "patch": patch.to_dict()}
        else:
            try:
                result = store.apply_patch(patch)
                compiled = compile_review_outputs(workspace)
            except Exception as exc:
                _die("patch application/rebuild failed: %s" % exc, code=1)
            payload = {
                "applied": result.applied,
                "replayed": result.replayed,
                "changed_operations": result.changed_operations,
                "issue_status": result.issue_status,
                "compiled": compiled,
            }
    elif args.command == "apply-batch":
        payload = _apply_patch_batch(
            workspace, store, _batch_patch_paths(args)
        )
    elif args.command in ("mark-resolved", "mark-unrecoverable"):
        issue = store.review_queue.get(args.issue_id)
        if issue is None:
            _die("unknown issue_id: %s" % args.issue_id)
        evidence_note = getattr(args, "evidence_note", None)
        if (args.command == "mark-unrecoverable"
                and "missing_answer" in issue.reason_codes
                and not (evidence_note or "").strip()):
            _die(
                "missing_answer cannot be marked unrecoverable with a generic reason; "
                "provide --evidence-note naming the candidate files/pages and search scope"
            )
        try:
            operation = (
                "mark_resolved" if args.command == "mark-resolved"
                else "mark_unrecoverable"
            )
            reason = args.reason.strip()
            if args.command == "mark-unrecoverable" and evidence_note:
                reason += "\nEvidence review: " + evidence_note.strip()
            patch = ReviewPatch.create(
                issue.issue_id,
                issue.source_id,
                issue.source_sha256,
                [{"op": operation, "reason": reason}],
                list(issue.evidence),
                reviewer=args.reviewer,
                status="validated",
            )
            result = store.apply_patch(patch)
            compiled = compile_review_outputs(workspace)
        except Exception as exc:
            _die("%s failed: %s" % (args.command, exc), code=1)
        payload = {
            "patch": patch.to_dict(),
            "issue_status": result.issue_status,
            "compiled": compiled,
        }
    elif args.command == "pending":
        try:
            pending = read_json(store.pending_patch_path, default=None)
        except Exception as exc:
            _die("cannot inspect pending patch: %s" % exc)
        payload = {"pending": pending is not None, "intent": pending}
    elif args.command == "recover-pending":
        try:
            pending = read_json(store.pending_patch_path, default=None)
            if pending is None:
                payload = {"recovered": False, "reason": "no_pending_patch"}
            else:
                expected = {"schema_version", "patch_id", "fingerprint", "patch"}
                if not isinstance(pending, dict) or set(pending) != expected:
                    raise ValueError("pending patch intent has an invalid schema")
                patch = ReviewPatch.from_dict(pending["patch"])
                if patch.patch_id != pending["patch_id"]:
                    raise ValueError("pending patch_id disagrees with embedded patch")
                result = store.apply_patch(patch)
                compiled = compile_review_outputs(workspace)
                payload = {
                    "recovered": True,
                    "applied": result.applied,
                    "replayed": result.replayed,
                    "issue_status": result.issue_status,
                    "compiled": compiled,
                }
        except Exception as exc:
            _die("pending patch recovery failed: %s" % exc, code=1)
    elif args.command == "rebuild":
        try:
            payload = compile_review_outputs(workspace)
        except Exception as exc:
            _die("rebuild failed: %s" % exc, code=1)
    else:
        _die("unknown command")

    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(run())
