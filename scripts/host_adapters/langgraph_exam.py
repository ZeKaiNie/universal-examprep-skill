#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Optional LangGraph host around the existing command/receipt workflow.

Importing this module never imports LangGraph.  ``build_exam_graph`` performs
the lazy import only for hosts that explicitly opt in and supply a durable
checkpointer.  Graph checkpoints are routing hints; every meaningful transition
is rehydrated from the workspace's existing command receipts and validators.
"""

import hashlib
import json
import os
import re
from typing import Any, Dict, Optional, TypedDict

from . import command_core


class LangGraphAdapterError(RuntimeError):
    """The optional host adapter cannot safely build or route the workflow."""


class OptionalDependencyUnavailable(LangGraphAdapterError):
    """LangGraph was explicitly requested but is not installed."""


class ExamGraphState(TypedDict, total=False):
    workspace: str
    materials: str
    course: str
    mode: str
    time_budget: str
    language: str
    artifact_mode: str
    chapter: int
    has_structured_workspace: bool
    start_gate_receipt: Dict[str, Any]
    ingest_receipt: Dict[str, Any]
    validation_receipt: Dict[str, Any]
    completion_validation_receipt: Dict[str, Any]
    completion_snapshot_receipt: Dict[str, Any]
    review_receipt: Dict[str, Any]
    progress_receipt: Dict[str, Any]
    confirmation_response: Dict[str, Any]
    last_resume: Dict[str, Any]
    warnings_acknowledged: bool
    tutor_handoff_complete: bool
    warning_acknowledgement: Dict[str, Any]
    tutor_handoff: Dict[str, Any]
    study_guide_gate_receipt: Dict[str, Any]
    study_guide_inspection_hint: Dict[str, Any]
    terminal_status: str
    operation_error: str


def _payload(receipt):
    return receipt.get("payload") if isinstance(receipt, dict) else None


def _canonical_sha256(value):
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


_GUIDE_STAGES = "claim_create claim_attach claim_verify typed_validate import preflight html pdf qa_render inspection ready".split()
_GUIDE_GATE_FIELDS = {
    "schema_version", "chapter", "artifact_mode", "stage", "pdf_sha256",
    "render_manifest_sha256", "pages",
}
_GUIDE_PAGE_FIELDS = {"page", "png", "png_sha256"}
_GUIDE_HINT_FIELDS = {"gate_sha256", "reviewer", "reviewer_kind", "page_verdicts"}


def _hex_sha256(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def validate_study_guide_gate_receipt(receipt, state=None):
    """Validate a bounded result from the host's canonical-status callback."""

    if (not isinstance(receipt, dict) or set(receipt) != _GUIDE_GATE_FIELDS
            or receipt.get("schema_version") != 1):
        raise LangGraphAdapterError("Study Guide gate receipt schema is invalid")
    chapter, mode, stage = (
        receipt.get("chapter"), receipt.get("artifact_mode"), receipt.get("stage"))
    if (type(chapter) is not int or chapter < 1 or mode not in ("chat", "visual")
            or stage not in _GUIDE_STAGES
            or (state is not None and state.get("chapter") not in (None, chapter))
            or (mode == "chat" and stage in _GUIDE_STAGES[5:-1])):
        raise LangGraphAdapterError("Study Guide gate receipt identity is invalid")
    pages = receipt.get("pages")
    if not isinstance(pages, list) or len(pages) > 1000:
        raise LangGraphAdapterError("Study Guide gate receipt is unbounded")
    page_stage = mode == "visual" and stage in ("inspection", "ready")
    if (page_stage != bool(pages) or (page_stage and (
            not _hex_sha256(receipt.get("pdf_sha256"))
            or not _hex_sha256(receipt.get("render_manifest_sha256"))))
            or (not page_stage and (receipt.get("pdf_sha256") is not None
                                    or receipt.get("render_manifest_sha256") is not None))):
        raise LangGraphAdapterError("Study Guide render binding is incomplete")
    for number, row in enumerate(pages, 1):
        if (not isinstance(row, dict) or set(row) != _GUIDE_PAGE_FIELDS
                or row.get("page") != number
                or row.get("png") != "study_guide/qa/ch%02d_p%03d.png" % (chapter, number)
                or not _hex_sha256(row.get("png_sha256"))):
            raise LangGraphAdapterError("Study Guide page evidence is stale")
    return receipt


def validate_study_guide_gate_transition(previous, current, state=None):
    current = validate_study_guide_gate_receipt(current, state)
    if previous is None:
        return current
    # The checkpoint can legitimately carry the prior chapter/mode receipt when
    # the canonical workspace state switches identity.  Validate that old value
    # as a bounded receipt, but do not compare it to the new state's identity.
    previous = validate_study_guide_gate_receipt(previous)
    if (previous["chapter"], previous["artifact_mode"]) != (
            current["chapter"], current["artifact_mode"]):
        return current
    stages = (_GUIDE_STAGES if current["artifact_mode"] == "visual"
              else _GUIDE_STAGES[:5] + ["ready"])
    if stages.index(current["stage"]) > stages.index(previous["stage"]) + 1:
        raise LangGraphAdapterError("Study Guide stage skipped a canonical gate")
    return current


def create_study_guide_inspection_hint(response, gate_receipt):
    """Bind one explicit pass verdict per page to the current render receipt."""

    gate = validate_study_guide_gate_receipt(gate_receipt)
    verdicts = response.get("page_verdicts") if isinstance(response, dict) else None
    if (gate["stage"] != "inspection" or not isinstance(response, dict)
            or set(response) != {
                "inspected_pages", "reviewer", "reviewer_kind", "page_verdicts"}
            or response.get("inspected_pages") != "all"
            or not isinstance(response.get("reviewer"), str)
            or re.fullmatch(r"[^\x00\r\n]{1,200}", response["reviewer"].strip()) is None
            or response.get("reviewer_kind") not in ("agent", "user")
            or not isinstance(verdicts, list) or len(verdicts) != len(gate["pages"])):
        raise LangGraphAdapterError("Study Guide inspection must cover every page")
    for page, verdict in zip(gate["pages"], verdicts):
        if (not isinstance(verdict, str) or re.fullmatch(
                r"%d=pass(?::[^\x00\r\n]{0,500})?" % page["page"], verdict) is None):
            raise LangGraphAdapterError("Study Guide inspection verdict is invalid")
    return {
        "gate_sha256": _canonical_sha256(gate),
        "reviewer": response["reviewer"].strip(),
        "reviewer_kind": response["reviewer_kind"],
        "page_verdicts": list(verdicts),
    }


def study_guide_inspection_hint_is_current(hint, gate_receipt):
    try:
        if not isinstance(hint, dict) or set(hint) != _GUIDE_HINT_FIELDS:
            return False
        response = {
            "inspected_pages": "all", "reviewer": hint["reviewer"],
            "reviewer_kind": hint["reviewer_kind"], "page_verdicts": hint["page_verdicts"],
        }
        return create_study_guide_inspection_hint(response, gate_receipt) == hint
    except LangGraphAdapterError:
        return False


def route_after_study_guide_gate(state):
    if state.get("operation_error"):
        return "operation_error"
    try:
        receipt = validate_study_guide_gate_receipt(
            state.get("study_guide_gate_receipt"), state)
    except LangGraphAdapterError:
        return "operation_error"
    if receipt["stage"] == "ready":
        return "validate"
    if receipt["stage"] == "inspection" and study_guide_inspection_hint_is_current(
            state.get("study_guide_inspection_hint"), receipt):
        return "guide_accept_interrupt"
    return "guide_%s_interrupt" % receipt["stage"]


_CAPABILITY_STATUSES = {
    "workspace_structural": {"ready", "blocked"},
    "teaching_ready": {"ready", "usable_with_gaps", "blocked"},
    "quiz_ready": {"ready", "usable_with_gaps", "blocked"},
    "artifact_ready": {"ready", "blocked"},
}


def _capabilities_are_well_formed(capabilities, chapter):
    if set(capabilities) != {"chapter"}.union(_CAPABILITY_STATUSES):
        return False
    for name, allowed in _CAPABILITY_STATUSES.items():
        value = capabilities.get(name)
        if not isinstance(value, dict) or set(value) != {
                "status", "ready", "reason_codes", "counts"}:
            return False
        status = value.get("status")
        if status not in allowed or value.get("ready") is not (status == "ready"):
            return False
        reasons = value.get("reason_codes")
        if (not isinstance(reasons, list) or len(reasons) != len(set(reasons))
                or any(not isinstance(reason, str) or not reason
                       for reason in reasons)):
            return False
        counts = value.get("counts")
        if not isinstance(counts, dict):
            return False
        if name != "workspace_structural" and counts.get("chapter") != chapter:
            return False
    return True


def _validation_context(state, receipt_field="validation_receipt"):
    receipt = state.get(receipt_field)
    payload = _payload(receipt)
    binding = receipt.get("binding") if isinstance(receipt, dict) else None
    if not isinstance(payload, dict) or not isinstance(binding, dict):
        return None
    if set(binding) != {
            "schema_version", "chapter", "content_sha256",
            "dependency_snapshot_sha256", "validation_sha256", "warning_sha256"}:
        return None
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, dict):
        return None
    chapter = capabilities.get("chapter")
    if type(chapter) is not int or chapter < 1 or binding.get("chapter") != chapter:
        return None
    if not _capabilities_are_well_formed(capabilities, chapter):
        return None
    if (type(payload.get("error_count")) is not int
            or type(payload.get("warning_count")) is not int
            or not isinstance(payload.get("errors"), list)
            or not isinstance(payload.get("warnings"), list)
            or payload.get("error_count") != len(payload.get("errors"))
            or payload.get("warning_count") != len(payload.get("warnings"))
            or payload.get("truncated") != {"errors": 0, "warnings": 0}):
        return None
    if state.get("chapter") is not None and state.get("chapter") != chapter:
        return None
    dependency_snapshot = payload.get("dependency_snapshot")
    if (not isinstance(dependency_snapshot, dict)
            or set(dependency_snapshot) != {
                "schema_version", "algorithm", "snapshot_sha256", "root_count",
                "content_sha256", "directory_count", "file_count", "total_bytes",
            }
            or dependency_snapshot.get("schema_version") != 1
            or dependency_snapshot.get("algorithm") != "sha256-tree-v1"
            or dependency_snapshot.get("content_sha256") != binding.get(
                "content_sha256")
            or dependency_snapshot.get("snapshot_sha256") != binding.get(
                "dependency_snapshot_sha256")
            or type(dependency_snapshot.get("root_count")) is not int
            or dependency_snapshot.get("root_count") not in (1, 2)
            or type(dependency_snapshot.get("directory_count")) is not int
            or not 0 <= dependency_snapshot.get("directory_count") <= 50000
            or type(dependency_snapshot.get("file_count")) is not int
            or not 0 <= dependency_snapshot.get("file_count") <= 20000
            or dependency_snapshot.get("directory_count")
            + dependency_snapshot.get("file_count") > 50000
            or type(dependency_snapshot.get("total_bytes")) is not int
            or not 0 <= dependency_snapshot.get("total_bytes")
            <= 50 * 1024 * 1024 * 1024):
        return None
    try:
        expected = command_core.validation_binding(
            payload, chapter, binding.get("content_sha256"))
    except Exception:
        return None
    if binding != expected:
        return None
    return {
        "payload": payload,
        "capabilities": capabilities,
        "binding": binding,
        "chapter": chapter,
    }


def hint_receipt(state, gate):
    """Create a chapter/content/validation-bound acknowledgement or handoff."""

    if gate not in ("warnings", "tutor_notebook"):
        raise LangGraphAdapterError("unknown hint gate %s" % gate)
    context = _validation_context(state)
    if context is None:
        raise LangGraphAdapterError("validation receipt has no trustworthy content binding")
    binding = context["binding"]
    return {
        "schema_version": 1,
        "gate": gate,
        "chapter": context["chapter"],
        "content_sha256": binding["content_sha256"],
        "validation_sha256": binding["validation_sha256"],
        "warning_sha256": binding["warning_sha256"],
        "hint_dependency_sha256": binding["content_sha256"],
    }


def _hint_is_current(state, field, gate):
    try:
        return state.get(field) == hint_receipt(state, gate)
    except LangGraphAdapterError:
        return False


def _counts(value):
    if not isinstance(value, dict):
        return None
    return value.get("counts") if isinstance(value.get("counts"), dict) else {}


def _active_typed_review(capabilities):
    teaching = capabilities.get("teaching_ready")
    artifact = capabilities.get("artifact_ready")
    teaching_counts = _counts(teaching)
    artifact_counts = _counts(artifact)
    if teaching_counts is None or artifact_counts is None:
        return None
    values = []
    for counts, fields in (
        (teaching_counts, ("active_review_issues", "blocking_review_issues")),
        (artifact_counts, (
            "chapter_high_risk_review_issues",
            "global_unbound_high_risk_review_issues",
            "unbound_active_review_issues",
        )),
    ):
        for field in fields:
            value = counts.get(field, 0)
            if type(value) is not int or value < 0:
                return None
            values.append(value)
    return any(values)


def _issue_text(payload, capabilities):
    values = []
    for key in ("error_summary", "warning_summary"):
        summary = payload.get(key)
        if isinstance(summary, dict):
            values.extend(str(item) for item in summary)
    for key in ("errors", "warnings"):
        rows = payload.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                for field in ("reason_code", "code", "msg", "message"):
                    if row.get(field) is not None:
                        values.append(str(row[field]))
                reasons = row.get("reason_codes")
                if isinstance(reasons, list):
                    values.extend(str(reason) for reason in reasons)
            else:
                values.append(str(row))
    for capability in capabilities.values():
        if not isinstance(capability, dict):
            continue
        reasons = capability.get("reason_codes")
        if isinstance(reasons, list):
            values.extend(str(reason) for reason in reasons)
    return "\n".join(values).lower()


_SOURCE_DRIFT = re.compile(
    r"source(?:_|\s).*(?:drift|hash|mismatch)|source_manifest.*(?:missing|drift)|"
    r"parser.*(?:drift|receipt|mismatch)|runtime.*(?:drift|provenance)|"
    r"原材料版本漂移|源.*漂移",
    re.I,
)
_BUILD_DRIFT = re.compile(
    r"build_manifest|build.*(?:drift|hash|mismatch)|content_units.*(?:drift|hash)|"
    r"retrieval_index.*(?:drift|hash)|compiled.*(?:drift|hash)|wiki.*hash|"
    r"dependency_snapshot",
    re.I,
)


def validate_review_receipt(receipt):
    payload = _payload(receipt)
    if not isinstance(receipt, dict) or receipt.get("exit_code") != 0:
        raise LangGraphAdapterError("review list receipt must have exit_code 0")
    if not isinstance(payload, dict):
        raise LangGraphAdapterError("review list receipt payload must be an object")
    expected = {
        "workspace", "count", "returned", "cursor", "next_cursor", "has_more",
        "summary", "details_file", "issues",
    }
    if set(payload) != expected:
        raise LangGraphAdapterError("review list receipt payload schema is invalid")
    for field in ("count", "returned", "cursor"):
        if type(payload.get(field)) is not int or payload[field] < 0:
            raise LangGraphAdapterError("review list field %s is invalid" % field)
    if (not isinstance(payload.get("workspace"), str)
            or not os.path.isabs(payload["workspace"])):
        raise LangGraphAdapterError("review list workspace must be absolute")
    summary = payload.get("summary")
    if (not isinstance(summary, dict)
            or set(summary) != {"by_status", "by_severity", "by_reason"}):
        raise LangGraphAdapterError("review list receipt omitted its summary")
    for field, must_total in (("by_status", True), ("by_severity", True),
                              ("by_reason", False)):
        counts = summary[field]
        if (not isinstance(counts, dict)
                or any(not isinstance(key, str) or not key
                       or type(value) is not int or value < 1
                       for key, value in counts.items())
                or (must_total and sum(counts.values()) != payload["count"])):
            raise LangGraphAdapterError(
                "review list summary %s is inconsistent" % field)
    if payload["count"] < 1:
        raise LangGraphAdapterError("review list reported no active typed work")
    if (payload["returned"] != 0 or payload["cursor"] != 0
            or payload["next_cursor"] != 0 or payload["has_more"] is not True
            or payload["issues"] != [] or payload["details_file"] is not None):
        raise LangGraphAdapterError(
            "review list receipt disagrees with the bounded summary-only request")
    return payload


_RESUME_ACK_FIELDS = {
    "confirmation": "confirmed",
    "typed_review": "review_complete",
    "source_or_parser_drift": "confirmed",
    "derived_build_drift": "rebuild_complete",
    "warnings": "acknowledged",
    "tutor_notebook": "persisted",
    "typed_guide": "imported",
    "visual_qa": "accepted",
    "study_guide_stage": "completed",
    "guide_accept": "accepted",
    "phase_completion": "progress_updated",
}


def validate_resume(value, gate):
    if not isinstance(value, dict) or not value:
        raise LangGraphAdapterError("%s resume value must be a non-empty JSON object" % gate)
    try:
        _canonical_sha256(value)
    except (TypeError, ValueError) as exc:
        raise LangGraphAdapterError(
            "%s resume value must be strict finite JSON" % gate) from exc
    acknowledgement = _RESUME_ACK_FIELDS.get(gate)
    if acknowledgement is None:
        raise LangGraphAdapterError("unknown resume gate %s" % gate)
    if value.get(acknowledgement) is not True:
        raise LangGraphAdapterError(
            "%s resume value must set %s=true" % (gate, acknowledgement))
    return value


def route_after_rehydrate(state):
    """Route from the read-only exact-pair/start-gate check."""

    if state.get("operation_error"):
        return "operation_error"
    payload = _payload(state.get("start_gate_receipt"))
    if not isinstance(payload, dict) or payload.get("process_success") is not True:
        return "operation_error"
    if payload.get("ready_to_ingest") is not True:
        return "confirmation_interrupt"
    return "validate" if state.get("has_structured_workspace") else "ingest"


def route_after_confirm(state):
    if state.get("operation_error"):
        return "operation_error"
    payload = _payload(state.get("start_gate_receipt"))
    return "ingest" if isinstance(payload, dict) and payload.get("ready_to_ingest") is True \
        else "operation_error"


def route_after_interrupt(state):
    """Never let a malformed/negative resume reach a command that clears its error."""

    return "operation_error" if state.get("operation_error") else "continue"


def route_after_ingest(state):
    """Exit 10 is successful processing with blocked content, so always validate."""

    if state.get("operation_error"):
        return "operation_error"
    receipt = state.get("ingest_receipt")
    payload = _payload(receipt)
    if not isinstance(receipt, dict) or not isinstance(payload, dict):
        return "operation_error"
    if receipt.get("exit_code") in (0, 10) and payload.get("process_success") is True:
        return "validate"
    return "operation_error"


def _validation_safety_route(context):
    """Return a mandatory recovery route, or ``None`` when content is usable."""

    payload = context["payload"]
    capabilities = context["capabilities"]
    structural = capabilities.get("workspace_structural")
    teaching = capabilities.get("teaching_ready")
    if not all(isinstance(value, dict) for value in (structural, teaching)):
        return "operation_error"
    active_review = _active_typed_review(capabilities)
    if active_review is None:
        return "operation_error"
    issue_text = _issue_text(payload, capabilities)
    if _SOURCE_DRIFT.search(issue_text):
        return "reingest_interrupt"
    if _BUILD_DRIFT.search(issue_text):
        return "rebuild_interrupt"
    blocked = (payload.get("readiness") == "blocked"
               or structural.get("status") == "blocked"
               or teaching.get("status") == "blocked")
    if active_review:
        return "review_interrupt"
    if blocked:
        return "operation_error"
    if payload.get("readiness") not in ("ready", "usable_with_gaps"):
        return "operation_error"
    return None


def _artifact_route(state, artifact):
    if not isinstance(artifact, dict):
        return "operation_error"
    if artifact.get("status") == "ready":
        return None
    reasons = set(artifact.get("reason_codes") or ())
    manifest_reasons = {
        "chapter_teaching_manifest_missing",
        "chapter_teaching_manifest_invalid",
    }
    artifact_counts = _counts(artifact)
    if artifact_counts is None:
        return "operation_error"
    if reasons.intersection(manifest_reasons) or artifact_counts.get(
            "manifest") is False:
        return "study_guide_rehydrate"
    mode = artifact_counts.get("artifact_mode") or state.get(
        "artifact_mode") or "chat"
    if mode not in ("chat", "visual"):
        return "operation_error"
    return "study_guide_rehydrate"


def route_after_validation(state):
    """Use capability readiness, never a checkpoint's optimistic completion flag."""

    if state.get("operation_error"):
        return "operation_error"
    context = _validation_context(state)
    if context is None:
        return "operation_error"
    safety_route = _validation_safety_route(context)
    if safety_route is not None:
        return safety_route
    payload = context["payload"]
    capabilities = context["capabilities"]
    if (payload.get("readiness") == "usable_with_gaps"
            and not _hint_is_current(
                state, "warning_acknowledgement", "warnings")):
        return "warning_interrupt"
    if not _hint_is_current(state, "tutor_handoff", "tutor_notebook"):
        return "tutor_interrupt"
    artifact_route = _artifact_route(state, capabilities.get("artifact_ready"))
    if artifact_route is not None:
        return artifact_route
    return "completion_interrupt"


def completed_phase_status(state_payload, chapter=None):
    """Read the current phase evidence status without duplicating its validator."""

    if not isinstance(state_payload, dict):
        return None
    phase = chapter or state_payload.get("current_phase") or state_payload.get("current_chapter")
    if isinstance(phase, bool):
        return None
    try:
        phase = int(phase)
    except (TypeError, ValueError):
        return None
    evidence = state_payload.get("phase_evidence")
    if not isinstance(evidence, dict):
        return None
    record = evidence.get(str(phase))
    if record is None:
        record = evidence.get(phase)
    return record.get("status") if isinstance(record, dict) else None


def _completion_snapshot_context(state):
    receipt = state.get("completion_snapshot_receipt")
    if (not isinstance(receipt, dict)
            or set(receipt) != {
                "schema_version", "validation_receipt", "progress_receipt",
                "dependency_snapshot", "binding",
            }
            or receipt.get("schema_version") != 1):
        return None
    validation_receipt = receipt.get("validation_receipt")
    progress_receipt = receipt.get("progress_receipt")
    dependency_snapshot = receipt.get("dependency_snapshot")
    binding = receipt.get("binding")
    state_binding = progress_receipt.get("state_binding") \
        if isinstance(progress_receipt, dict) else None
    if (not isinstance(state_binding, dict)
            or set(state_binding) != {
                "schema_version", "path", "sha256", "size_bytes"}
            or state_binding.get("schema_version") != 1
            or state_binding.get("path") != "study_state.json"
            or not isinstance(state_binding.get("sha256"), str)
            or len(state_binding["sha256"]) != 64
            or any(char not in "0123456789abcdef"
                   for char in state_binding["sha256"])
            or type(state_binding.get("size_bytes")) is not int
            or state_binding["size_bytes"] < 1):
        return None
    try:
        expected_binding = command_core.completion_binding(
            validation_receipt, progress_receipt, dependency_snapshot)
    except Exception:
        return None
    if binding != expected_binding:
        return None
    fresh_state = dict(state)
    fresh_state["validation_receipt"] = validation_receipt
    context = _validation_context(fresh_state)
    if (context is None
            or context["payload"].get("dependency_snapshot") != dependency_snapshot):
        return None
    return {
        "validation_receipt": validation_receipt,
        "progress_receipt": progress_receipt,
        "dependency_snapshot": dependency_snapshot,
        "validation_context": context,
    }


def _route_after_completion_validation(state):
    """Route only from the atomic completion snapshot's fresh validation."""

    snapshot = _completion_snapshot_context(state)
    if snapshot is None:
        return "operation_error"
    fresh_state = dict(state)
    fresh_state["validation_receipt"] = snapshot["validation_receipt"]
    route = route_after_validation(fresh_state)
    return "progress" if route == "completion_interrupt" else route


def completion_check_update(command_api, state):
    """Acquire the host core's one locked, post-checked completion snapshot."""

    snapshot_receipt = command_api.completion_snapshot(
        state["workspace"], state.get("chapter"))
    update = {
        "completion_snapshot_receipt": snapshot_receipt,
        "completion_validation_receipt": snapshot_receipt.get(
            "validation_receipt") if isinstance(snapshot_receipt, dict) else None,
        "progress_receipt": snapshot_receipt.get(
            "progress_receipt") if isinstance(snapshot_receipt, dict) else None,
    }
    refreshed = dict(state)
    refreshed.update(update)
    if _completion_snapshot_context(refreshed) is None:
        raise LangGraphAdapterError("completion snapshot receipt is invalid")
    return update


def route_after_completion_check(state):
    if state.get("operation_error"):
        return "operation_error"
    validation_route = _route_after_completion_validation(state)
    if validation_route != "progress":
        return validation_route
    snapshot = _completion_snapshot_context(state)
    if snapshot is None:
        return "operation_error"
    payload = _payload(snapshot["progress_receipt"])
    status = completed_phase_status(payload, state.get("chapter"))
    return "end" if status in ("verified", "covered_unverified") \
        else "completion_interrupt"


def _resume_object(value, gate):
    return validate_resume(value, gate)


def build_exam_graph(checkpointer, command_api=None):
    """Build the optional state graph around the unchanged command core.

    ``checkpointer`` is required because every human/agent gate uses a dynamic
    interrupt.  Production hosts should provide their own durable saver and a
    stable ``thread_id`` when invoking the compiled graph.
    """

    if checkpointer is None:
        raise LangGraphAdapterError("a host-supplied durable checkpointer is required")
    try:
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import interrupt
    except ImportError as exc:
        raise OptionalDependencyUnavailable(
            "LangGraph is optional and is not installed; install it in the host environment "
            "only after explicit consent") from exc

    api = command_api or command_core

    def safe_call(function, state, *args, **kwargs):
        try:
            return function(*args, **kwargs), None
        except Exception as exc:  # command_core already bounds subprocess details
            label = getattr(function, "__name__", function.__class__.__name__)
            return None, "%s: %s" % (label, str(exc)[:1000])

    def resume_update(response, gate, **values):
        try:
            values["last_resume"] = validate_resume(response, gate)
            values["operation_error"] = ""
        except LangGraphAdapterError as exc:
            return {"operation_error": str(exc)}
        return values

    def rehydrate(state):
        receipt, error = safe_call(
            api.exam_start_status, state, state["workspace"], state["materials"])
        structured = os.path.isdir(os.path.join(state["workspace"], ".ingest"))
        update = {"has_structured_workspace": structured}
        if error:
            update["operation_error"] = error
        else:
            update["start_gate_receipt"] = receipt
            update["operation_error"] = ""
        return update

    def confirmation_interrupt(state):
        response = interrupt({
            "gate": "workspace_confirmation",
            "workspace": state.get("workspace"),
            "materials": state.get("materials"),
            "course": state.get("course"),
            "mode": state.get("mode"),
            "time_budget": state.get("time_budget"),
            "language": state.get("language"),
            "resume_contract": {"confirmed": True},
            "instruction": "Confirm the exact pair and all three learning choices.",
        })
        try:
            return {"confirmation_response": validate_resume(response, "confirmation"),
                    "operation_error": ""}
        except LangGraphAdapterError as exc:
            return {"operation_error": str(exc)}

    def confirm(state):
        response = state.get("confirmation_response") or {}
        if response.get("confirmed") is not True:
            return {"operation_error": "exact workspace/materials confirmation was not granted"}
        receipt, error = safe_call(
            api.exam_start_confirm, state,
            state["workspace"], state["materials"], state["course"], state["mode"],
            state["time_budget"], state["language"], state.get("artifact_mode"), False)
        return {"operation_error": error or "", "start_gate_receipt": receipt} if receipt \
            else {"operation_error": error}

    def ingest(state):
        # Rehydrate first so a crash after a completed prior ingest can route by
        # current receipts on the next graph invocation.
        receipt, error = safe_call(
            api.ingest_course, state, state["workspace"], state["materials"])
        return {"operation_error": error or "", "ingest_receipt": receipt} if receipt \
            else {"operation_error": error}

    def validate(state):
        receipt, error = safe_call(
            api.validate_workspace, state, state["workspace"], state.get("chapter"))
        return {"operation_error": error or "", "validation_receipt": receipt} if receipt \
            else {"operation_error": error}

    def review_interrupt(state):
        # This read-only list call is safe to replay. Claim/apply/rebuild remain
        # outside the interrupting node because claim is not idempotent.
        receipt, error = safe_call(
            api.review_list, state, state["workspace"],
            statuses=("pending", "claimed", "validated", "blocked"),
            summary_only=True)
        if error:
            return {"operation_error": error}
        try:
            review_payload = validate_review_receipt(receipt)
        except LangGraphAdapterError as exc:
            return {"operation_error": str(exc)}
        response = interrupt({
            "gate": "typed_review",
            "workspace": state["workspace"],
            "summary": review_payload["summary"],
            "resume_contract": {"review_complete": True},
            "instruction": (
                "Use ingest_review list/show/claim, inspect cited evidence, then validate/apply "
                "a typed patch or mark the issue unrecoverable with evidence. Rebuild and resume."
            ),
        })
        return resume_update(
            response, "typed_review", review_receipt=receipt)

    def reingest_interrupt(state):
        response = interrupt({
            "gate": "source_or_parser_drift",
            "workspace": state["workspace"],
            "materials": state.get("materials"),
            "resume_contract": {"confirmed": True},
            "instruction": (
                "The validator reports source/parser/runtime drift. Confirm the exact current "
                "materials pair, then resume so the canonical ingestion command reruns."
            ),
        })
        return resume_update(response, "source_or_parser_drift")

    def rebuild_interrupt(state):
        response = interrupt({
            "gate": "derived_build_drift",
            "workspace": state["workspace"],
            "resume_contract": {"rebuild_complete": True},
            "instruction": (
                "Rebuild canonical derived ingestion artifacts with the existing rebuild command, "
                "then resume for a fresh validator receipt."
            ),
        })
        return resume_update(response, "derived_build_drift")

    def warning_interrupt(state):
        payload = _payload(state.get("validation_receipt")) or {}
        response = interrupt({
            "gate": "usable_with_gaps",
            "warning_count": payload.get("warning_count"),
            "warning_summary": payload.get("warning_summary") or {},
            "warnings": payload.get("warnings") or [],
            "resume_contract": {"acknowledged": True},
            "instruction": "Surface every remaining warning before continuing.",
        })
        try:
            acknowledgement = hint_receipt(state, "warnings")
        except LangGraphAdapterError as exc:
            return {"operation_error": str(exc)}
        return resume_update(
            response, "warnings", warning_acknowledgement=acknowledgement)

    def tutor_interrupt(state):
        response = interrupt({
            "gate": "tutor_notebook",
            "workspace": state["workspace"],
            "chapter": state.get("chapter"),
            "resume_contract": {"persisted": True},
            "instruction": (
                "Run the current-chapter tutor workflow and persist substantive teaching, "
                "feedback, and evidence through notebook/update_progress commands."
            ),
        })
        try:
            handoff = hint_receipt(state, "tutor_notebook")
        except LangGraphAdapterError as exc:
            return {"operation_error": str(exc)}
        return resume_update(response, "tutor_notebook", tutor_handoff=handoff)

    def study_guide_rehydrate(state):
        context = _validation_context(state)
        if context is None:
            return {"operation_error": "Study Guide routing requires a current validator receipt"}
        artifact = context["capabilities"].get("artifact_ready")
        counts = _counts(artifact)
        mode = counts.get("artifact_mode") if isinstance(counts, dict) else None
        if mode not in ("chat", "visual"):
            return {"operation_error": "validator omitted the canonical artifact_mode"}
        status_function = getattr(api, "study_guide_status", None)
        if not callable(status_function):
            return {"operation_error": (
                "the host must supply a read-only study_guide_status callback that rereads "
                "canonical claim/content/render/QA receipts")}
        draft = "notebook/ch%02d.guide.claim-draft.json" % context["chapter"]
        receipt, error = safe_call(
            status_function, state, state["workspace"], context["chapter"], mode,
            draft)
        if error:
            return {"operation_error": error}
        try:
            validate_study_guide_gate_transition(
                state.get("study_guide_gate_receipt"), receipt, state)
            if receipt["artifact_mode"] != mode:
                raise LangGraphAdapterError(
                    "Study Guide gate receipt disagrees with canonical artifact_mode")
        except LangGraphAdapterError as exc:
            return {"operation_error": str(exc)}
        return {"study_guide_gate_receipt": receipt, "operation_error": ""}

    def current_guide_receipt(state, stage):
        try:
            receipt = validate_study_guide_gate_receipt(
                state.get("study_guide_gate_receipt"), state)
            if receipt["stage"] != stage:
                raise LangGraphAdapterError("Study Guide stage drifted before interrupt")
            return receipt, None
        except LangGraphAdapterError as exc:
            return None, str(exc)

    def make_guide_interrupt(stage):
        def node(state):
            receipt, error = current_guide_receipt(state, stage)
            if error:
                return {"operation_error": error}
            response = interrupt({
                "gate": "study_guide_%s" % stage, "workspace": state["workspace"],
                "chapter": receipt["chapter"], "draft_path":
                    "notebook/ch%02d.guide.claim-draft.json" % receipt["chapter"],
                "resume_contract": {"completed": True},
                "instruction": "Complete this canonical stage using the documented command.",
                "truth_boundary": "Resume is only a hint; canonical validators rerun next.",
            })
            return resume_update(response, "study_guide_stage")

        return node

    guide_command_nodes = {
        "guide_%s_interrupt" % stage: make_guide_interrupt(stage)
        for stage in _GUIDE_STAGES[:-2]
    }
    def guide_inspection_interrupt(state):
        receipt, error = current_guide_receipt(state, "inspection")
        if error:
            return {"operation_error": error}
        response = interrupt({
            "gate": "study_guide_all_pages_inspection",
            "workspace": state["workspace"],
            "chapter": receipt["chapter"],
            "pdf_sha256": receipt["pdf_sha256"],
            "render_manifest_sha256": receipt["render_manifest_sha256"],
            "pages": receipt["pages"],
            "resume_contract": {
                "inspected_pages": "all",
                "reviewer": "<non-empty name>",
                "reviewer_kind": "agent|user",
                "page_verdicts": ["1=pass:<notes>"],
            },
            "instruction": (
                "Actually open and visually inspect every listed current PNG in order. Check math, "
                "glyphs, images, prompt/answer order, clipping, tables, margins, page breaks, page "
                "numbers, orphan headings, and blank space. Return one hash-bound pass verdict per "
                "page; any defect requires a fix, rerender, and a fresh inspection from page 1."
            ),
        })
        try:
            hint = create_study_guide_inspection_hint(response, receipt)
        except LangGraphAdapterError as exc:
            return {"operation_error": str(exc)}
        return {"study_guide_inspection_hint": hint, "operation_error": ""}

    def guide_accept_interrupt(state):
        receipt, error = current_guide_receipt(state, "inspection")
        if error:
            return {"operation_error": error}
        hint = state.get("study_guide_inspection_hint")
        if not study_guide_inspection_hint_is_current(hint, receipt):
            return {"operation_error": "Study Guide inspection evidence is missing or stale"}
        response = interrupt({
            "gate": "study_guide_qa_accept",
            "workspace": state["workspace"],
            "chapter": receipt["chapter"],
            "reviewer": hint["reviewer"],
            "reviewer_kind": hint["reviewer_kind"],
            "page_verdicts": hint["page_verdicts"],
            "resume_contract": {"accepted": True},
            "instruction": (
                "Run study_guide_qa.py accept with --inspected-pages all and exactly these "
                "hash-bound per-page pass verdicts. Resume only after the canonical receipt is "
                "ready; acknowledgement alone cannot satisfy artifact readiness."
            ),
        })
        return resume_update(response, "guide_accept")

    def completion_interrupt(state):
        response = interrupt({
            "gate": "phase_completion",
            "workspace": state["workspace"],
            "chapter": state.get("chapter"),
            "resume_contract": {"progress_updated": True},
            "instruction": (
                "Run update_progress complete-phase with the intended status. The command's "
                "existing evidence and artifact gates remain authoritative."
            ),
        })
        return resume_update(response, "phase_completion")

    def completion_check(state):
        update, error = safe_call(completion_check_update, state, api, state)
        if error:
            return {"operation_error": error}
        update["operation_error"] = ""
        return update

    def operation_error(state):
        return {"terminal_status": "blocked_operation"}

    builder = StateGraph(ExamGraphState)
    for name, node in (
        ("rehydrate", rehydrate),
        ("confirmation_interrupt", confirmation_interrupt),
        ("confirm", confirm),
        ("ingest", ingest),
        ("validate", validate),
        ("review_interrupt", review_interrupt),
        ("reingest_interrupt", reingest_interrupt),
        ("rebuild_interrupt", rebuild_interrupt),
        ("warning_interrupt", warning_interrupt),
        ("tutor_interrupt", tutor_interrupt),
        ("study_guide_rehydrate", study_guide_rehydrate),
        ("guide_inspection_interrupt", guide_inspection_interrupt),
        ("guide_accept_interrupt", guide_accept_interrupt),
        ("completion_interrupt", completion_interrupt),
        ("completion_check", completion_check),
        ("halt_operation", operation_error),
    ):
        builder.add_node(name, node)
    for name, node in guide_command_nodes.items():
        builder.add_node(name, node)
    builder.add_edge(START, "rehydrate")
    builder.add_conditional_edges("rehydrate", route_after_rehydrate, {
        "confirmation_interrupt": "confirmation_interrupt",
        "ingest": "ingest", "validate": "validate", "operation_error": "halt_operation",
    })
    builder.add_conditional_edges("confirmation_interrupt", route_after_interrupt, {
        "continue": "confirm", "operation_error": "halt_operation",
    })
    builder.add_conditional_edges("confirm", route_after_confirm, {
        "ingest": "ingest", "operation_error": "halt_operation",
    })
    builder.add_conditional_edges("ingest", route_after_ingest, {
        "validate": "validate", "operation_error": "halt_operation",
    })
    builder.add_conditional_edges("validate", route_after_validation, {
        "review_interrupt": "review_interrupt",
        "reingest_interrupt": "reingest_interrupt",
        "rebuild_interrupt": "rebuild_interrupt",
        "warning_interrupt": "warning_interrupt",
        "tutor_interrupt": "tutor_interrupt",
        "study_guide_rehydrate": "study_guide_rehydrate",
        "completion_interrupt": "completion_interrupt",
        "operation_error": "halt_operation",
    })
    builder.add_conditional_edges("reingest_interrupt", route_after_interrupt, {
        "continue": "ingest", "operation_error": "halt_operation",
    })
    builder.add_conditional_edges("rebuild_interrupt", route_after_interrupt, {
        "continue": "validate", "operation_error": "halt_operation",
    })
    for name in ("review_interrupt", "warning_interrupt", "tutor_interrupt"):
        builder.add_conditional_edges(name, route_after_interrupt, {
            "continue": "validate", "operation_error": "halt_operation",
        })
    guide_routes = dict((name, name) for name in guide_command_nodes)
    guide_routes.update({
        "guide_inspection_interrupt": "guide_inspection_interrupt",
        "guide_accept_interrupt": "guide_accept_interrupt",
        "validate": "validate", "operation_error": "halt_operation",
    })
    builder.add_conditional_edges(
        "study_guide_rehydrate", route_after_study_guide_gate, guide_routes)
    for name in tuple(guide_command_nodes) + (
            "guide_inspection_interrupt", "guide_accept_interrupt"):
        builder.add_conditional_edges(name, route_after_interrupt, {
            "continue": "study_guide_rehydrate", "operation_error": "halt_operation",
        })
    builder.add_conditional_edges("completion_interrupt", route_after_interrupt, {
        "continue": "completion_check", "operation_error": "halt_operation",
    })
    builder.add_conditional_edges("completion_check", route_after_completion_check, {
        "end": END,
        "completion_interrupt": "completion_interrupt",
        "review_interrupt": "review_interrupt",
        "reingest_interrupt": "reingest_interrupt",
        "rebuild_interrupt": "rebuild_interrupt",
        "warning_interrupt": "warning_interrupt",
        "tutor_interrupt": "tutor_interrupt",
        "study_guide_rehydrate": "study_guide_rehydrate",
        "operation_error": "halt_operation",
    })
    builder.add_edge("halt_operation", END)
    return builder.compile(checkpointer=checkpointer)


__all__ = [
    "ExamGraphState", "LangGraphAdapterError", "OptionalDependencyUnavailable",
    "build_exam_graph", "completed_phase_status", "completion_check_update",
    "create_study_guide_inspection_hint",
    "route_after_study_guide_gate", "study_guide_inspection_hint_is_current",
    "validate_study_guide_gate_receipt", "validate_study_guide_gate_transition",
    "route_after_completion_check",
    "hint_receipt", "validate_resume", "validate_review_receipt",
    "route_after_confirm", "route_after_ingest", "route_after_interrupt",
    "route_after_rehydrate",
    "route_after_validation",
]
