"""Hash-bound builder-to-compiler generation receipts.

The material builder and workspace compiler intentionally run as separate
processes.  This module defines the small, deterministic hand-off document that
keeps the workspace fail closed between those processes without introducing a
database or a workflow-engine dependency.
"""

import hashlib
import json
import re


MATERIAL_BUILD_PENDING_PATH = ".ingest/material_build_pending.json"
MATERIAL_BUILD_RECEIPT_PATH = ".ingest/material_build_receipt.json"
MATERIAL_BUILD_RECOVERY_DIR = ".ingest/material_build_recovery"
SOURCE_RAW_INPUT_PATH = ".ingest/source_raw_input.json"
PARSE_REPORT_PATH = ".ingest/parse_report.json"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CORE_KEYS = {
    "schema_version",
    "generation_id",
    "previous_build_manifest_sha256",
    "raw_input",
    "parse_report",
    "candidate_asset_policy_sha256",
    "asset_role_promotions_sha256",
    "asset_role_promotion_count",
}
_RECOVERY_ACTIONS = frozenset(("resume", "supersede"))
_RUNTIME_RECEIPT_PATH = "exam_runtime_receipt.json"


def canonical_json_bytes(value):
    """Return the portable JSON encoding used by generation digests."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def json_sha256(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def candidate_asset_policy(raw_input):
    """Extract the exact three candidate declaration layers from raw input."""

    if not isinstance(raw_input, dict):
        raise ValueError("material raw input must be an object")
    ingestion = raw_input.get("ingestion")
    if not isinstance(ingestion, dict):
        raise ValueError("material raw input lacks a structured ingestion envelope")
    policy = {
        "quiz_rows": raw_input.get("quiz_bank"),
        "teaching_rows": raw_input.get("teaching_examples"),
        "content_units": ingestion.get("content_units"),
    }
    for name, rows in policy.items():
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("material candidate %s must be an array of objects" % name)
    return policy


def asset_role_promotions(parse_report):
    if not isinstance(parse_report, dict):
        raise ValueError("material parse report must be an object")
    rows = parse_report.get("asset_role_promotions", [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("asset_role_promotions must be an array of objects")
    return rows


def _generation_core(
        raw_input_sha256, parse_report_sha256, raw_input, parse_report,
        previous_build_manifest_sha256, supersedes_generation_id=None,
        bind_supersession=False):
    for label, digest in (
            ("raw input", raw_input_sha256),
            ("parse report", parse_report_sha256)):
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise ValueError("%s sha256 is invalid" % label)
    if (previous_build_manifest_sha256 is not None
            and (not isinstance(previous_build_manifest_sha256, str)
                 or not _SHA256_RE.fullmatch(previous_build_manifest_sha256))):
        raise ValueError("previous build manifest sha256 is invalid")
    policy = candidate_asset_policy(raw_input)
    promotions = asset_role_promotions(parse_report)
    core = {
        "schema_version": 2 if bind_supersession else 1,
        "previous_build_manifest_sha256": previous_build_manifest_sha256,
        "raw_input": {
            "path": SOURCE_RAW_INPUT_PATH,
            "sha256": raw_input_sha256,
        },
        "parse_report": {
            "path": PARSE_REPORT_PATH,
            "sha256": parse_report_sha256,
        },
        "candidate_asset_policy_sha256": json_sha256(policy),
        "asset_role_promotions_sha256": json_sha256(promotions),
        "asset_role_promotion_count": len(promotions),
    }
    if bind_supersession:
        if (not isinstance(supersedes_generation_id, str)
                or not _SHA256_RE.fullmatch(supersedes_generation_id)):
            raise ValueError("superseded material generation ID is invalid")
        core["supersedes_generation_id"] = supersedes_generation_id
    core["generation_id"] = json_sha256(core)
    return core


def build_pending_generation(
        raw_input_sha256, parse_report_sha256, raw_input, parse_report,
        previous_build_manifest_sha256=None, supersedes_generation_id=None):
    """Create the pending hand-off published in the builder transaction."""

    document = _generation_core(
        raw_input_sha256,
        parse_report_sha256,
        raw_input,
        parse_report,
        previous_build_manifest_sha256,
        supersedes_generation_id=supersedes_generation_id,
        bind_supersession=supersedes_generation_id is not None,
    )
    document["status"] = "pending"
    return document


def _validate_completion(value):
    if (not isinstance(value, dict)
            or set(value) != {"schema_version", "recovery_logs"}
            or type(value.get("schema_version")) is not int
            or value.get("schema_version") != 1
            or not isinstance(value.get("recovery_logs"), list)
            or len(value["recovery_logs"]) > 65):
        raise ValueError("material build completion has an invalid schema")
    paths = []
    for row in value["recovery_logs"]:
        if (not isinstance(row, dict)
                or set(row) != {
                    "path", "generation_id", "outcome",
                    "replacement_generation_id",
                }
                or row.get("path") != material_recovery_path(
                    row.get("generation_id")
                )
                or row.get("outcome") not in ("completed", "abandoned")):
            raise ValueError("material build completion recovery binding is invalid")
        replacement = row.get("replacement_generation_id")
        if ((row["outcome"] == "completed" and replacement is not None)
                or (row["outcome"] == "abandoned" and (
                    not isinstance(replacement, str)
                    or not _SHA256_RE.fullmatch(replacement)))):
            raise ValueError("material build completion replacement binding is invalid")
        paths.append(row["path"])
    if paths != sorted(set(paths)):
        raise ValueError("material build completion recovery bindings are not canonical")
    return value


def complete_generation(pending, recovery_logs=()):
    """Convert an already verified pending hand-off into a durable receipt."""

    validate_generation(pending, expected_status="pending")
    receipt = dict(pending)
    receipt["status"] = "complete"
    receipt["completion"] = {
        "schema_version": 1,
        "recovery_logs": sorted(
            [dict(row) for row in recovery_logs], key=lambda row: row.get("path", "")
        ),
    }
    _validate_completion(receipt["completion"])
    return receipt


def validate_generation(value, expected_status=None):
    """Validate shape and self-authenticating generation ID."""

    schema_version = value.get("schema_version") if isinstance(value, dict) else None
    expected_keys = _CORE_KEYS | {"status"}
    if schema_version == 2:
        expected_keys = expected_keys | {"supersedes_generation_id"}
    if (isinstance(value, dict) and value.get("status") == "complete"
            and "completion" in value):
        expected_keys = expected_keys | {"completion"}
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise ValueError("material build generation has an invalid schema")
    if type(schema_version) is not int or schema_version not in (1, 2):
        raise ValueError("unsupported material build generation schema")
    if schema_version == 2:
        supersedes = value.get("supersedes_generation_id")
        if not isinstance(supersedes, str) or not _SHA256_RE.fullmatch(supersedes):
            raise ValueError("material build generation has an invalid predecessor")
        if supersedes == value.get("generation_id"):
            raise ValueError("material build generation cannot supersede itself")
    status = value.get("status")
    if status not in ("pending", "complete"):
        raise ValueError("material build generation has an invalid status")
    if expected_status is not None and status != expected_status:
        raise ValueError("material build generation is not %s" % expected_status)
    if status == "complete":
        completion = value.get("completion")
        if completion is not None:
            _validate_completion(completion)
        elif schema_version == 2:
            raise ValueError("schema2 material build receipt lacks completion audit")
    previous = value.get("previous_build_manifest_sha256")
    if previous is not None and (
            not isinstance(previous, str) or not _SHA256_RE.fullmatch(previous)):
        raise ValueError("material build generation has an invalid prior manifest hash")
    for key, expected_path in (
            ("raw_input", SOURCE_RAW_INPUT_PATH),
            ("parse_report", PARSE_REPORT_PATH)):
        row = value.get(key)
        if (not isinstance(row, dict) or set(row) != {"path", "sha256"}
                or row.get("path") != expected_path
                or not isinstance(row.get("sha256"), str)
                or not _SHA256_RE.fullmatch(row["sha256"])):
            raise ValueError("material build generation has an invalid %s binding" % key)
    for key in (
            "candidate_asset_policy_sha256",
            "asset_role_promotions_sha256"):
        if not isinstance(value.get(key), str) or not _SHA256_RE.fullmatch(value[key]):
            raise ValueError("material build generation has an invalid %s" % key)
    count = value.get("asset_role_promotion_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("material build generation has an invalid promotion count")
    generation_id = value.get("generation_id")
    if not isinstance(generation_id, str) or not _SHA256_RE.fullmatch(generation_id):
        raise ValueError("material build generation has an invalid generation ID")
    unsigned = dict(value)
    unsigned.pop("status")
    unsigned.pop("generation_id")
    unsigned.pop("completion", None)
    expected_id = json_sha256(unsigned)
    if generation_id != expected_id:
        raise ValueError("material build generation ID does not match its bindings")
    return value


def material_recovery_path(generation_id):
    """Return the fixed, generation-addressed recovery audit path."""

    if not isinstance(generation_id, str) or not _SHA256_RE.fullmatch(generation_id):
        raise ValueError("material recovery generation ID is invalid")
    return "%s/%s.json" % (MATERIAL_BUILD_RECOVERY_DIR, generation_id)


def _runtime_binding(value, label, allow_missing=False):
    if not isinstance(value, dict) or set(value) != {
            "path", "state", "sha256", "runtime_digest"}:
        raise ValueError("material recovery %s runtime binding is invalid" % label)
    if value.get("path") != _RUNTIME_RECEIPT_PATH:
        raise ValueError("material recovery %s runtime path is invalid" % label)
    states = ("valid", "invalid", "missing") if allow_missing else ("valid",)
    if value.get("state") not in states:
        raise ValueError("material recovery %s runtime state is invalid" % label)
    receipt_sha = value.get("sha256")
    runtime_digest = value.get("runtime_digest")
    if value["state"] == "missing":
        if receipt_sha is not None or runtime_digest is not None:
            raise ValueError("missing material recovery runtime must have null hashes")
    else:
        if not isinstance(receipt_sha, str) or not _SHA256_RE.fullmatch(receipt_sha):
            raise ValueError("material recovery %s receipt hash is invalid" % label)
        if value["state"] == "valid":
            if (not isinstance(runtime_digest, str)
                    or not _SHA256_RE.fullmatch(runtime_digest)):
                raise ValueError("material recovery %s runtime digest is invalid" % label)
        elif runtime_digest is not None:
            raise ValueError("invalid material recovery runtime must not claim a digest")
    return value


def build_runtime_recovery(
        pending, pending_sha256, action, previous_runtime,
        replacement_runtime, authorized_at):
    """Authorize an exact runtime rebind for one pending generation.

    The authorization never removes the pending blocker. ``resume`` compiles
    its exact input. ``supersede`` abandons it only when a fully staged
    successor is atomically published.
    """

    validate_generation(pending, expected_status="pending")
    if not isinstance(pending_sha256, str) or not _SHA256_RE.fullmatch(
            pending_sha256):
        raise ValueError("material recovery pending hash is invalid")
    return build_runtime_recovery_from_binding(
        {
            "path": MATERIAL_BUILD_PENDING_PATH,
            "sha256": pending_sha256,
            "generation_id": pending["generation_id"],
            "supersedes_generation_id": pending.get("supersedes_generation_id"),
        },
        action,
        previous_runtime,
        replacement_runtime,
        authorized_at,
    )


def build_runtime_recovery_from_binding(
        pending_binding, action, previous_runtime,
        replacement_runtime, authorized_at):
    """Re-authorize a proven predecessor when its successor publish was interrupted."""

    if (not isinstance(pending_binding, dict)
            or set(pending_binding) != {
                "path", "sha256", "generation_id", "supersedes_generation_id"
            }
            or pending_binding.get("path") != MATERIAL_BUILD_PENDING_PATH
            or not isinstance(pending_binding.get("sha256"), str)
            or not _SHA256_RE.fullmatch(pending_binding["sha256"])
            or not isinstance(pending_binding.get("generation_id"), str)
            or not _SHA256_RE.fullmatch(pending_binding["generation_id"])):
        raise ValueError("material recovery pending binding is invalid")
    predecessor = pending_binding.get("supersedes_generation_id")
    if predecessor is not None and (
            not isinstance(predecessor, str)
            or not _SHA256_RE.fullmatch(predecessor)
            or predecessor == pending_binding["generation_id"]):
        raise ValueError("material recovery predecessor binding is invalid")
    if action not in _RECOVERY_ACTIONS:
        raise ValueError("material recovery action is invalid")
    _runtime_binding(previous_runtime, "previous", allow_missing=True)
    _runtime_binding(replacement_runtime, "replacement")
    if not isinstance(authorized_at, str) or not authorized_at.endswith("Z"):
        raise ValueError("material recovery timestamp is invalid")
    authorization = {
        "schema_version": 1,
        "record_type": "material_runtime_recovery",
        "pending": dict(pending_binding),
        "action": action,
        "authorized_at": authorized_at,
        "previous_runtime_receipt": dict(previous_runtime),
        "replacement_runtime_receipt": dict(replacement_runtime),
    }
    authorization["authorization_id"] = json_sha256(authorization)
    return {"authorization": authorization, "outcome": None}


def validate_runtime_recovery(value, pending=None, pending_sha256=None):
    """Validate a recovery authorization and optional abandonment outcome."""

    if not isinstance(value, dict) or set(value) != {"authorization", "outcome"}:
        raise ValueError("material runtime recovery has an invalid schema")
    row = value.get("authorization")
    expected = {
        "schema_version", "record_type", "authorization_id", "pending",
        "action", "authorized_at", "previous_runtime_receipt",
        "replacement_runtime_receipt",
    }
    if not isinstance(row, dict) or set(row) != expected:
        raise ValueError("material runtime recovery authorization is invalid")
    if (type(row.get("schema_version")) is not int
            or row.get("schema_version") != 1
            or row.get("record_type") != "material_runtime_recovery"
            or row.get("action") not in _RECOVERY_ACTIONS
            or not isinstance(row.get("authorized_at"), str)
            or not row["authorized_at"].endswith("Z")):
        raise ValueError("material runtime recovery authorization fields are invalid")
    pending_row = row.get("pending")
    if (not isinstance(pending_row, dict)
            or set(pending_row) != {
                "path", "sha256", "generation_id", "supersedes_generation_id"
            }
            or pending_row.get("path") != MATERIAL_BUILD_PENDING_PATH
            or not isinstance(pending_row.get("sha256"), str)
            or not _SHA256_RE.fullmatch(pending_row["sha256"])
            or not isinstance(pending_row.get("generation_id"), str)
            or not _SHA256_RE.fullmatch(pending_row["generation_id"])):
        raise ValueError("material runtime recovery pending binding is invalid")
    predecessor = pending_row.get("supersedes_generation_id")
    if predecessor is not None and (
            not isinstance(predecessor, str)
            or not _SHA256_RE.fullmatch(predecessor)
            or predecessor == pending_row["generation_id"]):
        raise ValueError("material runtime recovery predecessor is invalid")
    _runtime_binding(row.get("previous_runtime_receipt"), "previous", True)
    _runtime_binding(row.get("replacement_runtime_receipt"), "replacement")
    authorization_id = row.get("authorization_id")
    unsigned = dict(row)
    unsigned.pop("authorization_id", None)
    if (not isinstance(authorization_id, str)
            or authorization_id != json_sha256(unsigned)):
        raise ValueError("material runtime recovery authorization ID is invalid")
    if pending is not None:
        validate_generation(pending, expected_status="pending")
        if pending_row["generation_id"] != pending["generation_id"]:
            raise ValueError("material runtime recovery belongs to another generation")
    if pending_sha256 is not None and pending_row["sha256"] != pending_sha256:
        raise ValueError("material runtime recovery pending hash is stale")
    outcome = value.get("outcome")
    if outcome is not None:
        if (not isinstance(outcome, dict)
                or set(outcome) != {
                    "status", "replacement_generation_id",
                    "material_build_receipt_sha256",
                }
                or outcome.get("status") not in (
                    "abandoned", "completed", "expired"
                )):
            raise ValueError("material runtime recovery outcome is invalid")
        replacement = outcome.get("replacement_generation_id")
        receipt_sha = outcome.get("material_build_receipt_sha256")
        if outcome["status"] == "abandoned":
            if (row["action"] != "supersede"
                    or not isinstance(replacement, str)
                    or not _SHA256_RE.fullmatch(replacement)
                    or replacement == pending_row["generation_id"]
                    or receipt_sha is not None):
                raise ValueError("material runtime recovery outcome is invalid")
        elif outcome["status"] == "completed":
            if (row["action"] != "resume" or replacement is not None
                    or not isinstance(receipt_sha, str)
                    or not _SHA256_RE.fullmatch(receipt_sha)):
                raise ValueError("material runtime recovery outcome is invalid")
        elif replacement is not None or receipt_sha is not None:
            raise ValueError("expired material recovery cannot bind an outcome")
    return value


def abandon_runtime_recovery(value, replacement_generation_id):
    """Bind abandonment of one generation to its atomically published successor."""

    validate_runtime_recovery(value)
    if value["authorization"]["action"] != "supersede":
        raise ValueError("only a supersede authorization can be abandoned")
    result = json.loads(json.dumps(value))
    result["outcome"] = {
        "status": "abandoned",
        "replacement_generation_id": replacement_generation_id,
        "material_build_receipt_sha256": None,
    }
    validate_runtime_recovery(result)
    return result


def expire_runtime_recovery(value):
    """Close a stale authorization before explicitly authorizing another."""

    validate_runtime_recovery(value)
    result = json.loads(json.dumps(value))
    result["outcome"] = {
        "status": "expired",
        "replacement_generation_id": None,
        "material_build_receipt_sha256": None,
    }
    validate_runtime_recovery(result)
    return result


def append_runtime_recovery(existing, recovery, limit=64):
    """Append one immutable authorization to a bounded generation audit log."""

    validate_runtime_recovery(recovery)
    generation_id = recovery["authorization"]["pending"]["generation_id"]
    if existing is None:
        log = {
            "schema_version": 1,
            "record_type": "material_runtime_recovery_log",
            "generation_id": generation_id,
            "records": [],
        }
    else:
        log = json.loads(json.dumps(existing))
        validate_runtime_recovery_log(log)
        if log["generation_id"] != generation_id:
            raise ValueError("material recovery log belongs to another generation")
    if len(log["records"]) >= limit:
        raise ValueError("material recovery log reached its bounded event limit")
    if log["records"] and log["records"][-1].get("outcome") is None:
        raise ValueError("previous material recovery authorization is still active")
    log["records"].append(json.loads(json.dumps(recovery)))
    validate_runtime_recovery_log(log)
    return log


def validate_runtime_recovery_log(value, pending=None, pending_sha256=None):
    if (not isinstance(value, dict)
            or set(value) != {
                "schema_version", "record_type", "generation_id", "records"
            }
            or type(value.get("schema_version")) is not int
            or value.get("schema_version") != 1
            or value.get("record_type") != "material_runtime_recovery_log"
            or not isinstance(value.get("generation_id"), str)
            or not _SHA256_RE.fullmatch(value["generation_id"])
            or not isinstance(value.get("records"), list)
            or not value["records"]
            or len(value["records"]) > 64):
        raise ValueError("material runtime recovery log has an invalid schema")
    seen = set()
    canonical_pending = None
    for index, recovery in enumerate(value["records"]):
        validate_runtime_recovery(
            recovery,
            pending=pending if index == len(value["records"]) - 1 else None,
            pending_sha256=(
                pending_sha256 if index == len(value["records"]) - 1 else None
            ),
        )
        authorization = recovery["authorization"]
        pending_binding = authorization["pending"]
        if pending_binding["generation_id"] != value["generation_id"]:
            raise ValueError("material runtime recovery log mixes generations")
        if canonical_pending is None:
            canonical_pending = pending_binding
        elif pending_binding != canonical_pending:
            raise ValueError("material runtime recovery log changes pending binding")
        authorization_id = authorization["authorization_id"]
        if authorization_id in seen:
            raise ValueError("material runtime recovery log duplicates an authorization")
        seen.add(authorization_id)
        if index < len(value["records"]) - 1 and recovery["outcome"] is None:
            raise ValueError("material runtime recovery log has overlapping authorizations")
    return value


def abandon_latest_runtime_recovery(log, replacement_generation_id):
    """Complete the active supersede event while retaining its prior audit chain."""

    validate_runtime_recovery_log(log)
    result = json.loads(json.dumps(log))
    result["records"][-1] = abandon_runtime_recovery(
        result["records"][-1], replacement_generation_id
    )
    validate_runtime_recovery_log(result)
    return result


def expire_latest_runtime_recovery(log):
    validate_runtime_recovery_log(log)
    result = json.loads(json.dumps(log))
    if result["records"][-1]["outcome"] is None:
        result["records"][-1] = expire_runtime_recovery(result["records"][-1])
    validate_runtime_recovery_log(result)
    return result


def complete_runtime_recovery(value, material_build_receipt_sha256):
    validate_runtime_recovery(value)
    if value["authorization"]["action"] != "resume":
        raise ValueError("only a resume authorization can complete in place")
    result = json.loads(json.dumps(value))
    result["outcome"] = {
        "status": "completed",
        "replacement_generation_id": None,
        "material_build_receipt_sha256": material_build_receipt_sha256,
    }
    validate_runtime_recovery(result)
    return result


def complete_latest_runtime_recovery(log, material_build_receipt_sha256):
    validate_runtime_recovery_log(log)
    result = json.loads(json.dumps(log))
    result["records"][-1] = complete_runtime_recovery(
        result["records"][-1], material_build_receipt_sha256
    )
    validate_runtime_recovery_log(result)
    return result


__all__ = [
    "MATERIAL_BUILD_PENDING_PATH",
    "MATERIAL_BUILD_RECEIPT_PATH",
    "MATERIAL_BUILD_RECOVERY_DIR",
    "PARSE_REPORT_PATH",
    "SOURCE_RAW_INPUT_PATH",
    "abandon_runtime_recovery",
    "abandon_latest_runtime_recovery",
    "append_runtime_recovery",
    "asset_role_promotions",
    "build_pending_generation",
    "build_runtime_recovery",
    "build_runtime_recovery_from_binding",
    "candidate_asset_policy",
    "complete_generation",
    "complete_latest_runtime_recovery",
    "complete_runtime_recovery",
    "expire_latest_runtime_recovery",
    "expire_runtime_recovery",
    "json_sha256",
    "material_recovery_path",
    "validate_generation",
    "validate_runtime_recovery",
    "validate_runtime_recovery_log",
]
