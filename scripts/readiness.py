#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Capability-specific readiness for a local exam workspace.

The legacy top-level readiness answers only whether the workspace can be opened.  This
module keeps that contract while reporting whether the current chapter can actually be
taught, graded, or published.  It is intentionally stdlib-only and side-effect free.
"""

import collections
import hashlib
import json
import os
import re

try:  # package import in tests; script-directory import in CLI entrypoints
    from . import study_guide_content, study_guide_qa
except ImportError:  # pragma: no cover - exercised by standalone script entrypoints
    import study_guide_content
    import study_guide_qa


TERMINAL_REVIEW_STATUSES = frozenset(("applied", "resolved", "unrecoverable", "superseded"))
HIGH_RISK_ARTIFACT_REASONS = frozenset((
    "formula_hint", "garbled_text", "nul_byte", "control_character", "no_text",
    "page_no_text", "unsupported_formula", "review_queue_unreadable_or_invalid",
    "content_units_unreadable_or_invalid",
))
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _load_json(path, default=None):
    if not os.path.isfile(path) or os.path.islink(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as stream:
            return json.load(stream, parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError("non-finite JSON constant: %s" % value)))
    except (OSError, UnicodeDecodeError, ValueError):
        return default


def _load_jsonl(path):
    """Return ``(valid_prefix_rows, error)`` without equating corruption to empty.

    A valid prefix is retained for diagnostics, but callers must treat any error
    as a global fail-closed condition because a truncated tail can hide pending
    review work.  Missing files remain an empty, non-error compatibility case.
    """
    if os.path.islink(path):
        return [], "symbolic-link JSONL is not trusted"
    if not os.path.exists(path):
        return [], None
    if not os.path.isfile(path):
        return [], "JSONL path is not a regular file"
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if line.strip():
                    row = json.loads(
                        line, parse_constant=lambda value: (_ for _ in ()).throw(
                            ValueError("non-finite JSON constant: %s" % value)))
                    if not isinstance(row, dict):
                        return rows, "line %d is not a JSON object" % line_number
                    rows.append(row)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return rows, str(exc)[:500]
    return rows, None


def _chapter_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 1:
        return value
    text = str(value or "")
    match = re.search(r"(?:ch(?:apter)?\s*0*|第\s*)(\d+)", text, re.I)
    if match:
        return int(match.group(1))
    return int(text) if text.isdigit() and int(text) >= 1 else None


def _current_chapter(workspace, requested=None):
    chapter = _chapter_number(requested)
    if chapter is not None:
        return chapter
    state = _load_json(os.path.join(workspace, "study_state.json"), {})
    return _chapter_number(state.get("current_chapter")) or _chapter_number(
        state.get("current_phase")) or 1


def _chapter_matches(item, chapter):
    return isinstance(item, dict) and _chapter_number(
        item.get("chapter_id") or item.get("chapter") or item.get("phase")) == chapter


def _has_answer(value):
    if value in (None, "", [], {}):
        return False
    return not (isinstance(value, str) and not value.strip())


def _unsafe_text_counts(text):
    return {
        "control_characters": sum(
            1 for char in text
            if (ord(char) < 32 and char not in "\t\n\r") or ord(char) == 0x7F
        ),
        "replacement_characters": text.count("\ufffd"),
    }


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _wiki_path(workspace, chapter):
    directory = os.path.join(workspace, "references", "wiki")
    if not os.path.isdir(directory) or os.path.islink(directory):
        return None
    pattern = re.compile(r"^ch0*%d(?:[^0-9].*)?\.md$" % chapter, re.I)
    matches = [os.path.join(directory, name) for name in os.listdir(directory)
               if pattern.match(name)]
    return matches[0] if len(matches) == 1 else None


def _unit_review_index(workspace):
    """Build the indexes needed to attribute review issues to chapters.

    Review issues created from page-level recovery signals do not always have a
    ``target_unit_ids`` binding yet.  Treating those rows as unrelated made a
    high-risk issue disappear from chapter artifact readiness.  The secondary
    indexes retain the strongest available source/page evidence without
    guessing that an entirely unbound issue belongs to chapter 1.
    """
    rows, load_error = _load_jsonl(
        os.path.join(workspace, ".ingest", "content_units.jsonl"))
    by_unit = {}
    by_source_page = collections.defaultdict(set)
    by_source = collections.defaultdict(set)
    for row in rows:
        unit_id = row.get("unit_id")
        chapter = _chapter_number(row.get("chapter_id"))
        if isinstance(unit_id, str) and chapter is not None:
            by_unit[unit_id] = chapter
        source_id = row.get("source_id")
        page = row.get("page")
        if isinstance(source_id, str) and source_id and chapter is not None:
            by_source[source_id].add(chapter)
            if type(page) is int and page >= 1:
                by_source_page[(source_id, page)].add(chapter)
    return by_unit, by_source_page, by_source, load_error


def _reason_codes(row):
    values = row.get("reason_codes") if isinstance(row, dict) else None
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        return []
    return [str(value) for value in values if isinstance(value, str) and value]


def _review_counts(rows, high_risk_only=False):
    reasons = collections.Counter()
    severities = collections.Counter()
    selected = []
    for row in rows:
        row_reasons = _reason_codes(row)
        if high_risk_only:
            row_reasons = [reason for reason in row_reasons
                           if reason in HIGH_RISK_ARTIFACT_REASONS]
            if not row_reasons:
                continue
        selected.append(row)
        severities[str(row.get("severity") or "warning")] += 1
        reasons.update(row_reasons)
    return selected, dict(sorted(reasons.items())), dict(sorted(severities.items()))


def _review_scope(workspace, chapter):
    """Classify active review issues as chapter-local, other-chapter, or global.

    Attribution is deliberately evidence ordered: bound target units first,
    then exact ``(source_id, page)`` matches, then a source-only fallback when
    that source maps to exactly one chapter.  A still-unbound active high-risk
    issue is global because publication cannot prove which chapter is safe.
    Ordinary unbound warnings remain counted but do not block artifacts.
    """
    by_unit, by_source_page, by_source, unit_load_error = _unit_review_index(workspace)
    rows, queue_load_error = _load_jsonl(
        os.path.join(workspace, ".ingest", "review_queue.jsonl"))
    load_errors = []
    if queue_load_error:
        load_errors.append("review_queue_unreadable_or_invalid")
    if unit_load_error:
        load_errors.append("content_units_unreadable_or_invalid")
    local = []
    other = []
    unbound = []
    global_unbound_high_risk = []
    attribution = collections.Counter()

    for row in rows:
        if row.get("status") in TERMINAL_REVIEW_STATUSES:
            continue

        targets = row.get("target_unit_ids") or []
        if not isinstance(targets, (list, tuple, set)):
            targets = []
        target_chapters = {
            by_unit[unit_id] for unit_id in targets
            if isinstance(unit_id, str) and unit_id in by_unit
        }
        issue_chapters = target_chapters
        method = "target_unit"

        if not issue_chapters:
            source_id = row.get("source_id")
            pages = row.get("pages") or []
            if not isinstance(pages, (list, tuple, set)):
                pages = []
            issue_chapters = set()
            if isinstance(source_id, str) and source_id:
                for page in pages:
                    if type(page) is int and page >= 1:
                        issue_chapters.update(by_source_page.get((source_id, page), ()))
            method = "source_page"

            if not issue_chapters and isinstance(source_id, str) and source_id:
                source_chapters = by_source.get(source_id, set())
                if len(source_chapters) == 1:
                    issue_chapters = set(source_chapters)
                    method = "source_unique_chapter"

        if issue_chapters:
            attribution[method] += 1
            if chapter in issue_chapters:
                local.append(row)
            else:
                other.append(row)
            continue

        attribution["unbound"] += 1
        unbound.append(row)
        if any(reason in HIGH_RISK_ARTIFACT_REASONS for reason in _reason_codes(row)):
            global_unbound_high_risk.append(row)

    # A damaged queue/index is not an empty queue.  Add an explicit global row
    # after classifying the readable prefix so both the evidence and the
    # corruption blocker remain visible to callers.
    for reason in load_errors:
        global_unbound_high_risk.append({
            "status": "pending",
            "severity": "blocking",
            "reason_codes": [reason],
            "synthetic_readiness_blocker": True,
        })
        attribution["load_error"] += 1

    unused, local_reasons, local_severities = _review_counts(local)
    local_high, local_high_reasons, unused = _review_counts(
        local, high_risk_only=True
    )
    global_high, global_high_reasons, unused = _review_counts(
        global_unbound_high_risk, high_risk_only=True
    )
    return {
        "chapter": chapter,
        "local_rows": local,
        "local_reasons": local_reasons,
        "local_severities": local_severities,
        "local_high_risk_rows": local_high,
        "local_high_risk_reasons": local_high_reasons,
        "other_chapter_active_issues": len(other),
        "unbound_active_issues": len(unbound),
        "global_unbound_high_risk_rows": global_high,
        "global_unbound_high_risk_reasons": global_high_reasons,
        "attribution_counts": dict(sorted(attribution.items())),
        "load_errors": sorted(set(load_errors)),
    }


def _chapter_review(workspace, chapter):
    scope = _review_scope(workspace, chapter)
    return (scope["local_rows"], scope["local_reasons"],
            scope["local_severities"])


def chapter_math_readiness(workspace, chapter):
    """Return the selected chapter's validated math state.

    ``standard`` means the typed guide contains at least one formula or worked
    substitution. ``none`` is allowed only after that manifest validates and no
    source-recovery signal remains. ``needs_recovery`` is deliberately distinct
    from ``none`` so dependency preflight cannot turn damaged formula evidence
    into a false "this chapter has no math" result.
    """
    workspace = os.path.abspath(workspace)
    chapter = _current_chapter(workspace, chapter)
    reasons = []
    manifest = None
    manifest_report = None
    manifest_error = None
    try:
        manifest, manifest_report = study_guide_content.load_and_validate_manifest(
            workspace, chapter
        )
    except (study_guide_content.ContentError, OSError, UnicodeError, ValueError) as exc:
        manifest_error = str(exc)[:500]
        guide_path = os.path.join(workspace, "notebook", "ch%02d.guide.json" % chapter)
        reasons.append(
            "chapter_teaching_manifest_invalid"
            if os.path.lexists(guide_path)
            else "chapter_teaching_manifest_missing"
        )

    wiki = _wiki_path(workspace, chapter)
    unsafe = {"control_characters": 0, "replacement_characters": 0}
    if wiki is None:
        reasons.append("chapter_wiki_missing_or_ambiguous")
    else:
        try:
            with open(wiki, "r", encoding="utf-8") as stream:
                unsafe = _unsafe_text_counts(stream.read())
        except (OSError, UnicodeDecodeError) as exc:
            reasons.append("chapter_wiki_unreadable")
            manifest_error = manifest_error or ("UTF-8 chapter wiki read failed: %s" % exc)[:500]
    if unsafe["control_characters"]:
        reasons.append("unsafe_control_text")
    if unsafe["replacement_characters"]:
        reasons.append("unicode_replacement_text")

    review_scope = _review_scope(workspace, chapter)
    review_rows = review_scope["local_rows"]
    recovery_review = review_scope["local_high_risk_reasons"]
    global_recovery_review = review_scope["global_unbound_high_risk_reasons"]
    if recovery_review:
        reasons.append("chapter_source_recovery_pending")
    if global_recovery_review:
        reasons.append("unbound_high_risk_review_pending")
    reasons.extend(review_scope["load_errors"])

    formula_count = 0
    substitution_count = 0
    if isinstance(manifest, dict):
        for point in manifest.get("knowledge_points") or []:
            if isinstance(point, dict):
                formula_count += sum(
                    1 for formula in (point.get("formulas") or [])
                    if isinstance(formula, dict) and str(formula.get("latex") or "").strip()
                )
        for walkthrough in manifest.get("walkthroughs") or []:
            if not isinstance(walkthrough, dict):
                continue
            substitution_count += sum(
                1 for use in (walkthrough.get("formula_uses") or [])
                if isinstance(use, dict) and str(use.get("substitution") or "").strip()
            )

    status = "needs_recovery" if reasons else (
        "standard" if formula_count or substitution_count else "none"
    )
    return {
        "status": status,
        "reason_codes": sorted(set(reasons)),
        "counts": {
            "formulas": formula_count,
            "substitutions": substitution_count,
            "control_characters": unsafe["control_characters"],
            "replacement_characters": unsafe["replacement_characters"],
            "active_review_issues": len(review_rows),
            "recovery_review_issues": len(review_scope["local_high_risk_rows"]),
            "recovery_review_reason_hits": sum(recovery_review.values()),
            "global_unbound_high_risk_review_issues": len(
                review_scope["global_unbound_high_risk_rows"]),
            "global_unbound_high_risk_review_reason_hits": sum(
                global_recovery_review.values()),
            "other_chapter_active_review_issues": review_scope[
                "other_chapter_active_issues"],
            "unbound_active_review_issues": review_scope["unbound_active_issues"],
            "review_attribution": review_scope["attribution_counts"],
        },
        "review_reasons": dict(sorted(recovery_review.items())),
        "global_review_reasons": dict(sorted(global_recovery_review.items())),
        "manifest_valid": manifest_report is not None,
        "manifest_report": manifest_report,
        "manifest_error": manifest_error,
    }


def _artifact_mode(state):
    raw = state.get("artifact_mode") if isinstance(state, dict) else None
    aliases = {
        "visual": "visual", "chat": "chat", "视觉": "visual",
        "视觉教材": "visual", "可打印教材": "visual",
    }
    return aliases.get(raw, "chat")


def _validate_visual_receipt(workspace, chapter):
    """Repeat the strict render/conversion chain plus all-page acceptance verification."""
    guide_dir = os.path.join(workspace, "study_guide")
    receipt_path = os.path.join(guide_dir, "ch%02d.receipt.json" % chapter)
    result = {
        "present": os.path.isfile(receipt_path) and not os.path.islink(receipt_path),
        "html_hash_match": False,
        "pdf_hash_match": False,
        "qa_status": None,
        "page_count": 0,
        "inspected_pages": None,
        "unresolved_defects": 0,
        "error": None,
    }
    reasons = []
    if not result["present"]:
        reasons.append("chapter_artifact_receipt_missing")
        return result, reasons
    try:
        receipt, context = study_guide_qa.validate_receipt_chain(
            workspace, chapter, require_pdf=True
        )
    except study_guide_qa.QAError as exc:
        message = str(exc)[:500]
        result["error"] = message
        reasons.append("chapter_artifact_receipt_invalid")
        lowered = message.lower()
        if "hash drifted" in lowered or "hash mismatch" in lowered:
            reasons.append("chapter_artifact_hash_mismatch")
        if "missing " in lowered:
            reasons.append("chapter_artifact_file_missing")
        return result, sorted(set(reasons))
    except (OSError, UnicodeError, ValueError) as exc:
        result["error"] = str(exc)[:500]
        reasons.extend(("chapter_artifact_receipt_invalid",
                        "chapter_artifact_file_unreadable"))
        return result, sorted(set(reasons))

    result["html_hash_match"] = True
    result["pdf_hash_match"] = True
    if receipt.get("status") != "ready":
        reasons.append("chapter_visual_qa_not_ready")

    qa = receipt.get("visual_qa")
    if not isinstance(qa, dict) or qa.get("schema_version") != study_guide_qa.SCHEMA_VERSION:
        reasons.append("chapter_visual_qa_missing")
        return result, sorted(set(reasons))
    result["qa_status"] = qa.get("status")
    result["inspected_pages"] = qa.get("inspected_pages")
    defects = qa.get("unresolved_defects")
    result["unresolved_defects"] = len(defects) if isinstance(defects, list) else 1
    if qa.get("status") != "ready" or qa.get("inspected_pages") != "all":
        reasons.append("chapter_visual_qa_not_ready")
    if (not isinstance(qa.get("pdf_sha256"), str)
            or qa.get("pdf_sha256") != receipt.get("pdf_sha256")):
        reasons.append("chapter_visual_qa_hash_mismatch")
    if defects not in ([], None):
        reasons.append("chapter_visual_defects_unresolved")
    auto_lint = qa.get("auto_lint")
    if (not isinstance(auto_lint, dict) or auto_lint.get("status") != "passed"
            or auto_lint.get("defects") not in ([], None)):
        reasons.append("chapter_visual_qa_not_ready")
    if qa.get("manual_review_checks") != list(study_guide_qa.MANUAL_REVIEW_CHECKS):
        reasons.append("chapter_visual_qa_incomplete")
    if qa.get("accepted_manual_review_checks") != list(
            study_guide_qa.MANUAL_REVIEW_CHECKS):
        reasons.append("chapter_visual_qa_incomplete")
    if qa.get("receipt_basis_sha256") != study_guide_qa._receipt_basis(receipt):
        reasons.append("chapter_visual_qa_hash_mismatch")
    render_hash = qa.get("render_manifest_sha256")
    if (not isinstance(render_hash, str) or not HASH_RE.fullmatch(render_hash)
            or render_hash != study_guide_qa._render_manifest_hash(qa)):
        reasons.append("chapter_visual_qa_hash_mismatch")
    acceptance_hash = qa.get("acceptance_manifest_sha256")
    if (not isinstance(acceptance_hash, str) or not HASH_RE.fullmatch(acceptance_hash)
            or acceptance_hash != study_guide_qa._acceptance_manifest_hash(qa)):
        reasons.append("chapter_visual_qa_hash_mismatch")
    try:
        study_guide_qa._parse_timestamp(qa.get("rendered_at"), "visual_qa.rendered_at")
        study_guide_qa._parse_timestamp(qa.get("accepted_at"), "visual_qa.accepted_at")
    except study_guide_qa.QAError:
        reasons.append("chapter_visual_qa_incomplete")
    if (not isinstance(qa.get("reviewer"), str) or not qa["reviewer"].strip()
            or qa.get("reviewer_kind") not in ("agent", "user")):
        reasons.append("chapter_visual_qa_incomplete")

    page_count = qa.get("page_count")
    pages = qa.get("pages")
    result["page_count"] = page_count if isinstance(page_count, int) else 0
    if (isinstance(page_count, bool) or not isinstance(page_count, int) or page_count < 1
            or not isinstance(pages, list) or len(pages) != page_count):
        reasons.append("chapter_visual_qa_incomplete")
        return result, sorted(set(reasons))
    verdicts = qa.get("page_verdicts")
    if not isinstance(verdicts, list) or len(verdicts) != page_count:
        reasons.append("chapter_visual_qa_incomplete")
        verdicts = []
    for number, page in enumerate(pages, 1):
        if (not isinstance(page, dict) or page.get("page") != number
                or page.get("defects") not in ([], None)):
            reasons.append("chapter_visual_qa_incomplete")
            continue
        expected_rel = "study_guide/qa/ch%02d_p%03d.png" % (chapter, number)
        png_hash = page.get("png_sha256")
        if page.get("png") != expected_rel or not (
                isinstance(png_hash, str) and HASH_RE.fullmatch(png_hash)):
            reasons.append("chapter_visual_qa_incomplete")
            continue
        png_path = os.path.join(workspace, *expected_rel.split("/"))
        if not os.path.isfile(png_path) or os.path.islink(png_path):
            reasons.append("chapter_visual_qa_incomplete")
            continue
        try:
            if _sha256_file(png_path) != png_hash:
                reasons.append("chapter_visual_qa_hash_mismatch")
        except OSError as exc:
            result["error"] = str(exc)[:500]
            reasons.append("chapter_visual_qa_incomplete")
        verdict = verdicts[number - 1] if len(verdicts) >= number else None
        if (not isinstance(verdict, dict) or verdict.get("page") != number
                or verdict.get("verdict") != "pass"
                or verdict.get("png_sha256") != png_hash
                or not isinstance(verdict.get("notes"), str)
                or any(char in verdict.get("notes", "") for char in ("\x00", "\r", "\n"))):
            reasons.append("chapter_visual_qa_incomplete")
    return result, sorted(set(reasons))


def _result(status, reasons=(), **counts):
    return {
        "status": status,
        "ready": status == "ready",
        "reason_codes": sorted(set(reasons)),
        "counts": counts,
    }


def capability_readiness(workspace, errors=(), warnings=(), stats=None, chapter=None):
    workspace = os.path.abspath(workspace)
    chapter = _current_chapter(workspace, chapter)
    stats = stats or {}
    structural_reasons = ["workspace_validation_error"] if errors else []
    structural = _result("blocked" if errors else "ready", structural_reasons,
                         errors=len(errors), warnings=len(warnings))

    wiki = _wiki_path(workspace, chapter)
    controls = 0
    replacements = 0
    if wiki:
        try:
            with open(wiki, "r", encoding="utf-8") as stream:
                unsafe = _unsafe_text_counts(stream.read())
                controls = unsafe["control_characters"]
                replacements = unsafe["replacement_characters"]
        except (OSError, UnicodeDecodeError):
            wiki = None
    review_scope = _review_scope(workspace, chapter)
    review_rows = review_scope["local_rows"]
    review_reasons = review_scope["local_reasons"]
    review_severities = review_scope["local_severities"]
    teaching_reasons = []
    if errors:
        teaching_reasons.append("workspace_validation_error")
    if wiki is None:
        teaching_reasons.append("chapter_wiki_missing_or_ambiguous")
    if controls:
        teaching_reasons.append("unsafe_control_text")
    if replacements:
        teaching_reasons.append("unicode_replacement_text")
    blocking_review = review_severities.get("blocking", 0)
    if blocking_review:
        teaching_reasons.append("chapter_blocking_review")
    teaching_status = "blocked" if teaching_reasons else (
        "usable_with_gaps" if review_rows else "ready")
    teaching = _result(
        teaching_status, teaching_reasons,
        chapter=chapter, active_review_issues=len(review_rows),
        blocking_review_issues=blocking_review, control_characters=controls,
        replacement_characters=replacements,
        review_reasons=review_reasons,
    )

    bank = _load_json(os.path.join(workspace, "references", "quiz_bank.json"), [])
    chapter_items = [row for row in (bank if isinstance(bank, list) else [])
                     if _chapter_matches(row, chapter) and row.get("gradable") is not False]
    invalid = collections.Counter()
    valid = 0
    for item in chapter_items:
        if not _has_answer(item.get("answer")):
            invalid["missing_answer"] += 1
            continue
        if item.get("type") == "subjective" and not (
                isinstance(item.get("keywords"), list)
                and any(isinstance(value, str) and value.strip()
                        for value in item.get("keywords"))):
            invalid["subjective_keywords_missing"] += 1
            continue
        valid += 1
    quiz_reasons = []
    if errors:
        quiz_reasons.append("workspace_validation_error")
    if not chapter_items:
        quiz_reasons.append("chapter_quiz_pool_empty")
    if not valid and chapter_items:
        quiz_reasons.append("no_gradable_chapter_items")
    quiz_status = "blocked" if quiz_reasons else (
        "usable_with_gaps" if invalid else "ready")
    quiz = _result(
        quiz_status, quiz_reasons,
        chapter=chapter, candidate_items=len(chapter_items), valid_items=valid,
        excluded_items=sum(invalid.values()), exclusions=dict(sorted(invalid.items())),
    )

    state = _load_json(os.path.join(workspace, "study_state.json"), {})
    language = state.get("language") if isinstance(state, dict) else None
    math = chapter_math_readiness(workspace, chapter)
    artifact_reasons = []
    if structural["status"] == "blocked":
        artifact_reasons.append("workspace_validation_error")
    if teaching["status"] == "blocked":
        artifact_reasons.append("teaching_not_ready")
    if language not in ("zh", "en", "bilingual", "中文", "English", "双语"):
        artifact_reasons.append("language_not_selected")
    if not math["manifest_valid"]:
        artifact_reasons.extend(
            reason for reason in math["reason_codes"]
            if reason.startswith("chapter_teaching_manifest_")
        )
    local_high_risk = len(review_scope["local_high_risk_rows"])
    global_unbound_high_risk = len(
        review_scope["global_unbound_high_risk_rows"])
    if local_high_risk:
        artifact_reasons.append("chapter_source_recovery_pending")
    if global_unbound_high_risk:
        artifact_reasons.append("unbound_high_risk_review_pending")
    artifact_reasons.extend(review_scope["load_errors"])
    receipt = None
    if _artifact_mode(state) == "visual":
        receipt, receipt_reasons = _validate_visual_receipt(workspace, chapter)
        artifact_reasons.extend(receipt_reasons)
    artifact = _result(
        "blocked" if artifact_reasons else "ready", artifact_reasons,
        chapter=chapter, language=language, manifest=math["manifest_valid"],
        # Keep the historical field as a chapter-local issue count.  Explicit
        # fields below prevent callers from confusing local and global risk.
        high_risk_review_issues=local_high_risk,
        chapter_high_risk_review_issues=local_high_risk,
        chapter_high_risk_review_reasons=review_scope["local_high_risk_reasons"],
        global_unbound_high_risk_review_issues=global_unbound_high_risk,
        global_unbound_high_risk_review_reasons=review_scope[
            "global_unbound_high_risk_reasons"],
        other_chapter_active_review_issues=review_scope[
            "other_chapter_active_issues"],
        unbound_active_review_issues=review_scope["unbound_active_issues"],
        review_attribution=review_scope["attribution_counts"],
        manifest_valid=math["manifest_valid"],
        manifest_error=math["manifest_error"],
        math_status=math["status"],
        math_reasons=math["reason_codes"],
        math_counts=math["counts"],
        artifact_mode=_artifact_mode(state),
        receipt=receipt,
    )
    return {
        "chapter": chapter,
        "workspace_structural": structural,
        "teaching_ready": teaching,
        "quiz_ready": quiz,
        "artifact_ready": artifact,
    }


_MESSAGE_CODES = (
    (re.compile(r"NUL|控制字节|control", re.I), "unsafe_control_text"),
    (re.compile(r"formula|公式|LaTeX|math", re.I), "formula_or_math"),
    (re.compile(r"missing.answer|缺.*答案|答案.*缺", re.I), "missing_answer"),
    (re.compile(r"keyword|关键词", re.I), "subjective_keywords_missing"),
    (re.compile(r"chapter|章节|未归属", re.I), "chapter_mapping"),
    (re.compile(r"visual|asset|图片|图像|题面图|答案图", re.I), "visual_asset"),
    (re.compile(r"review|审核|复核", re.I), "review_pending"),
)


def message_code(message):
    text = str(message or "")
    for pattern, code in _MESSAGE_CODES:
        if pattern.search(text):
            return code
    return "other"


def summarize_messages(rows):
    counts = collections.Counter(message_code(
        row.get("msg") if isinstance(row, dict) else row) for row in rows)
    return dict(sorted(counts.items()))
