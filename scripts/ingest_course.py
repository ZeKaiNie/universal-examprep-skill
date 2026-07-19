#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""One command: preflight -> build -> ingest -> visual index -> validate.

No dependency is installed here.  Exit 10 means the engineering pipeline
completed but content readiness is blocked by explicit review/validation work.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys

try:
    import exam_start
    from ingestion import (
        ConflictError,
        IngestionStore,
        safe_workspace_entry,
        stable_read_bytes,
        workspace_publication_lock,
    )
    from material_generation import (
        MATERIAL_BUILD_PENDING_PATH,
        abandon_latest_runtime_recovery,
        build_pending_generation,
        material_recovery_path,
        validate_generation,
        validate_runtime_recovery_log,
    )
except ImportError:
    from scripts import exam_start
    from scripts.ingestion import (
        ConflictError,
        IngestionStore,
        safe_workspace_entry,
        stable_read_bytes,
        workspace_publication_lock,
    )
    from scripts.material_generation import (
        MATERIAL_BUILD_PENDING_PATH,
        abandon_latest_runtime_recovery,
        build_pending_generation,
        material_recovery_path,
        validate_generation,
        validate_runtime_recovery_log,
    )


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _strict_json_payload(payload, label):
    def no_duplicates(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON key in %s" % label)
            value[key] = item
        return value

    def reject_constant(value):
        raise ValueError("non-finite JSON constant in %s: %s" % (label, value))

    return json.loads(
        payload.decode("utf-8"),
        object_pairs_hook=no_duplicates,
        parse_constant=reject_constant,
    )


def _load_recovery_log(
        workspace, generation_id, pending=None, pending_sha256=None):
    relative = material_recovery_path(generation_id)
    path = safe_workspace_entry(workspace, relative)
    if not os.path.lexists(str(path)):
        return None, relative
    payload, _snapshot = stable_read_bytes(path)
    value = _strict_json_payload(payload, "material recovery log")
    validate_runtime_recovery_log(
        value, pending=pending, pending_sha256=pending_sha256
    )
    return value, relative


def _verify_recovery_runtime(workspace, recovery):
    authorization = recovery["authorization"]
    replacement = authorization["replacement_runtime_receipt"]
    runtime_path = safe_workspace_entry(workspace, replacement["path"])
    runtime_payload, _runtime_snapshot = stable_read_bytes(runtime_path)
    if hashlib.sha256(runtime_payload).hexdigest() != replacement["sha256"]:
        raise ValueError("material recovery runtime receipt hash drifted")
    runtime_receipt = exam_start._validate_runtime_receipt_schema(
        _strict_json_payload(runtime_payload, "runtime receipt")
    )
    if runtime_receipt["runtime_digest"] != replacement["runtime_digest"]:
        raise ValueError("material recovery runtime digest drifted")


def _recovery_ancestor_chain(workspace, pending, limit=64):
    chain = []
    seen = {pending["generation_id"]}
    child_generation = pending["generation_id"]
    generation_id = pending.get("supersedes_generation_id")
    while generation_id is not None:
        if generation_id in seen or len(chain) >= limit:
            raise ValueError("material recovery ancestor chain is cyclic or too deep")
        seen.add(generation_id)
        recovery_log, recovery_path = _load_recovery_log(
            workspace, generation_id
        )
        if recovery_log is None:
            raise ValueError("successor pending lacks an ancestor recovery audit")
        latest = recovery_log["records"][-1]
        authorization = latest["authorization"]
        outcome = latest["outcome"]
        binding = authorization["pending"]
        if (authorization["action"] != "supersede"
                or binding["generation_id"] != generation_id
                or (outcome is not None and (
                    outcome.get("status") != "abandoned"
                    or outcome.get("replacement_generation_id")
                    != child_generation))):
            raise ValueError("material recovery ancestor audit is invalid")
        chain.append({
            "generation_id": generation_id,
            "child_generation_id": child_generation,
            "path": recovery_path,
            "log": recovery_log,
        })
        child_generation = generation_id
        generation_id = binding.get("supersedes_generation_id")
    return chain


def _pending_material_state(workspace):
    """Load one exact pending generation and its active recovery decision."""

    pending_path = safe_workspace_entry(workspace, MATERIAL_BUILD_PENDING_PATH)
    if not os.path.lexists(str(pending_path)):
        return None
    pending_payload, _snapshot = stable_read_bytes(pending_path)
    pending = _strict_json_payload(pending_payload, "material pending marker")
    validate_generation(pending, expected_status="pending")
    pending_sha = hashlib.sha256(pending_payload).hexdigest()
    documents = {}
    source_error = None
    try:
        for name, binding in (
                ("raw_input", pending["raw_input"]),
                ("parse_report", pending["parse_report"])):
            source = safe_workspace_entry(workspace, binding["path"])
            source_payload, _source_snapshot = stable_read_bytes(source)
            if hashlib.sha256(source_payload).hexdigest() != binding["sha256"]:
                raise ValueError("pending material %s hash drifted" % name)
            documents[name] = _strict_json_payload(
                source_payload, "pending %s" % name
            )
    except (OSError, UnicodeDecodeError, TypeError, ValueError) as exc:
        source_error = exc

    recovery_log, recovery_relative = _load_recovery_log(
        workspace,
        pending["generation_id"],
        pending=pending,
        pending_sha256=pending_sha,
    )
    action = "resume"
    supersede_source_generation_id = pending["generation_id"]
    ancestor_recoveries = _recovery_ancestor_chain(workspace, pending)
    if recovery_log is not None:
        active = recovery_log["records"][-1]
        if active["outcome"] is not None:
            raise ValueError(
                "pending generation has no active runtime recovery authorization"
            )
        _verify_recovery_runtime(workspace, active)
        action = active["authorization"]["action"]

    if source_error is not None and recovery_log is None:
        # A blocker-first hard crash is routed back through the builder.  The
        # rebuilt candidate must reproduce this exact self-authenticating
        # generation or publication fails and requires explicit supersede.
        action = "repair"

    return {
        "document": pending,
        "sha256": pending_sha,
        "raw_input": documents.get("raw_input"),
        "parse_report": documents.get("parse_report"),
        "action": action,
        "recovery_log": recovery_log,
        "recovery_path": recovery_relative,
        "supersede_source_generation_id": supersede_source_generation_id,
        "sources_exact": source_error is None,
        "ancestor_recoveries": ancestor_recoveries,
    }


def _run(command):
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _emit(payload, as_json):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("process_success=%s readiness=%s" % (
            str(payload.get("process_success")).lower(),
            payload.get("readiness") or "unknown",
        ))
        for step in payload.get("steps", []):
            suffix = ""
            if step.get("name") == "workspace_validation":
                suffix = " errors=%s warnings=%s" % (
                    step.get("errors", 0), step.get("warnings", 0))
            print("[%s] %s%s" % (step.get("status"), step.get("name"), suffix))
        if payload.get("workspace"):
            print("workspace=%s" % payload["workspace"])


def _validated_workspace_payload(result):
    """Parse the validator protocol, distinguishing content blocks from crashes."""
    try:
        payload = json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise ValueError("validator did not return valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("validator JSON must be an object")
    readiness = payload.get("readiness")
    if readiness not in ("ready", "usable_with_gaps", "blocked"):
        raise ValueError("validator returned an invalid readiness")
    declared_exit = payload.get("exit_code")
    if isinstance(declared_exit, bool) or not isinstance(declared_exit, int):
        raise ValueError("validator JSON omitted its integer exit_code")
    if declared_exit != result.returncode:
        raise ValueError("validator process/JSON exit codes disagree")
    error_count = payload.get("error_count")
    if isinstance(error_count, bool) or not isinstance(error_count, int) or error_count < 0:
        raise ValueError("validator returned an invalid error_count")
    if readiness == "blocked":
        if result.returncode not in (1, 2) or error_count < 1:
            raise ValueError("blocked validator result is internally inconsistent")
    elif result.returncode != 0 or error_count != 0:
        raise ValueError("non-blocked validator result is internally inconsistent")
    return payload


def run(argv=None, backend=None, adapter_runner=None):
    parser = argparse.ArgumentParser(
        description="Explicit full structured course-ingestion orchestrator"
    )
    parser.add_argument("--materials", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--course-name")
    parser.add_argument("--lang", choices=("zh", "en"))
    parser.add_argument(
        "--artifact-mode", choices=("chat", "visual"), default=None,
        help="explicit standing preference; omitted means keep existing/default chat",
    )
    parser.add_argument("--render-pages", choices=("never", "auto", "required"), default="auto")
    parser.add_argument("--visual-index", choices=("never", "auto", "required"), default="auto")
    parser.add_argument(
        "--crop-annotations",
        default=None,
        help=(
            "optional UTF-8 JSONL of source/preview-bound target-item crop "
            "annotations passed unchanged to the material builder"
        ),
    )
    parser.add_argument(
        "--ingest-adapter", choices=("core", "docling", "mineru"), default="core",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    materials = os.path.abspath(args.materials)
    workspace = os.path.abspath(args.workspace)
    steps = []
    payload = {
        "process_success": False,
        "readiness": "unknown",
        "materials": materials,
        "workspace": workspace,
        "steps": steps,
    }
    if args.ingest_adapter != "core":
        payload["error"] = (
            "local Docling/MinerU execution is disabled. A learner must explicitly "
            "request the named parser and use a separately configured remote/cloud "
            "host integration with disclosed upload/privacy terms; ingest_course.py "
            "never downloads, installs, imports, or invokes that heavy parser locally"
        )
        _emit(payload, args.as_json)
        return 2
    if not os.path.isdir(materials):
        payload["error"] = "materials directory does not exist"
        _emit(payload, args.as_json)
        return 2
    if exam_start._path_has_link_or_reparse(materials):
        payload["error"] = "materials path contains a symbolic link, junction, or reparse point"
        _emit(payload, args.as_json)
        return 2
    materials_real = os.path.realpath(materials)
    workspace_real = os.path.realpath(workspace)
    try:
        workspace_inside_materials = (
            os.path.commonpath((materials_real, workspace_real)) == materials_real
        )
    except ValueError:
        workspace_inside_materials = False
    if workspace_inside_materials:
        payload["error"] = (
            "workspace must not equal or live inside materials; reruns would ingest generated files"
        )
        _emit(payload, args.as_json)
        return 2
    if exam_start._path_has_link_or_reparse(workspace):
        payload["error"] = "workspace path contains a symbolic link, junction, or reparse point"
        _emit(payload, args.as_json)
        return 2
    start_gate = exam_start.check_start_gate(workspace, materials)
    payload["start_gate"] = start_gate
    steps.append({
        "name": "exam_start_gate",
        "status": "passed" if start_gate.get("ready_to_ingest") else "blocked",
        "ready_to_ingest": bool(start_gate.get("ready_to_ingest")),
        "blockers": list(
            (start_gate.get("ingestion_permission") or {}).get("blockers") or []
        ),
    })
    if not start_gate.get("ready_to_ingest"):
        payload["error"] = (
            "ingestion requires an exact workspace/materials confirmation and "
            "mode, time_budget, language, an explicit processing_mode=full, and a "
            "matching runtime provenance receipt; run exam_start.py confirm with "
            "--processing-mode full and the intended installed skill first. "
            "If material_build_pending.json exists, ordinary confirm must not replace "
            "its provenance: run exam_start.py recover-material-build with an explicit "
            "--action resume or --action supersede instead"
        )
        _emit(payload, args.as_json)
        return 2

    try:
        # A compiler hard crash can leave pending_ingest with partially written
        # recovery/pending files.  This authorized mutation command must trigger
        # rollback before it attempts to route on those bytes.
        with workspace_publication_lock(
                workspace, allow_material_generation=True):
            pending_state = _pending_material_state(workspace)
    except ConflictError as exc:
        payload["error"] = "pending material recovery lock conflict: %s" % exc
        steps.append({
            "name": "material_generation_recovery",
            "status": "failed",
            "operation_error": "publication_conflict",
        })
        _emit(payload, args.as_json)
        return 1
    except (OSError, UnicodeDecodeError, TypeError, ValueError) as exc:
        payload["error"] = "pending material recovery is invalid: %s" % exc
        steps.append({
            "name": "material_generation_recovery",
            "status": "failed",
            "operation_error": "invalid_pending_generation",
        })
        _emit(payload, args.as_json)
        return 2
    if pending_state is not None:
        payload["material_recovery"] = {
            "generation_id": pending_state["document"]["generation_id"],
            "pending_sha256": pending_state["sha256"],
            "action": pending_state["action"],
            "authorization": (
                pending_state["recovery_path"]
                if pending_state["recovery_log"] is not None else None
            ),
        }

    runtime_rechecks = []
    payload["runtime_rechecks"] = runtime_rechecks

    def runtime_ok(stage):
        gate = exam_start.check_start_gate(workspace, materials)
        ok = bool(gate.get("ready_to_ingest"))
        runtime_rechecks.append({
            "stage": stage,
            "status": "passed" if ok else "failed",
            "runtime_reason": (gate.get("runtime_provenance") or {}).get("reason"),
            "blockers": list(
                (gate.get("ingestion_permission") or {}).get("blockers") or []
            ),
        })
        if not ok:
            payload["error"] = (
                "runtime/start provenance drifted before %s; stop and rerun exam_start confirm"
                % stage
            )
        return ok

    if not runtime_ok("dependency_preflight"):
        _emit(payload, args.as_json)
        return 2
    os.makedirs(workspace, exist_ok=True)
    try:
        # Validate every parent before the builder gets its first output path.
        # This prevents a pre-existing .ingest/references junction from turning
        # an apparently local build into an external write.
        safe_workspace_entry(workspace, ".ingest")
        safe_workspace_entry(workspace, "references/assets")
    except Exception as exc:
        payload["error"] = "unsafe workspace output tree: %s" % exc
        _emit(payload, args.as_json)
        return 2

    preflight = _run([
        sys.executable,
        os.path.join(SCRIPT_DIR, "check_deps.py"),
        "--materials", materials,
        "--artifact-mode", args.artifact_mode or "chat",
    ])
    steps.append({
        "name": "dependency_preflight",
        "status": "passed" if preflight.returncode == 0 else "failed",
        "exit_code": preflight.returncode,
    })
    if preflight.returncode != 0:
        payload["error"] = (preflight.stderr or preflight.stdout).strip()
        _emit(payload, args.as_json)
        return preflight.returncode

    if not runtime_ok("material_build"):
        _emit(payload, args.as_json)
        return 2
    try:
        import build_raw_input_from_workspace as builder
    except ImportError:
        from scripts import build_raw_input_from_workspace as builder

    raw_path = os.path.join(workspace, ".ingest", "source_raw_input.json")
    report_path = os.path.join(workspace, ".ingest", "parse_report.json")
    asset_root = os.path.join(workspace, "references", "assets")
    build_args = builder.build_arg_parser().parse_args([
        "--materials", materials,
        "--out", raw_path,
        "--report", report_path,
        "--asset-root", asset_root,
        "--render-pages", args.render_pages,
        "--extract-lecture-questions", "auto",
        "--extract-homework", "auto",
        "--ingest-adapter", args.ingest_adapter,
    ] + (["--course-name", args.course_name] if args.course_name else [])
      + (["--crop-annotations", args.crop_annotations]
         if args.crop_annotations else []))
    publish_ready = False
    code = raw_input = report = None
    raw_input_sha256 = None
    asset_plans = []
    ingest_path = os.path.join(workspace, ".ingest")
    ingest_preexisting = os.path.lexists(ingest_path)
    try:
        # Build immutable asset plans, recheck the runtime gate, then commit the
        # exact JSON+asset set under one state->ingestion lock.  No asset becomes
        # public before every JSON document has serialized and staged safely.
        with workspace_publication_lock(
                workspace, allow_material_generation=True):
            if ingest_preexisting and not os.path.lexists(ingest_path):
                raise ConflictError(".ingest changed while acquiring the publication lock")
            locked_pending = _pending_material_state(workspace)
            if pending_state is None:
                if locked_pending is not None:
                    raise ConflictError(
                        "a pending material generation appeared before publication"
                    )
            elif (locked_pending is None
                    or locked_pending["sha256"] != pending_state["sha256"]
                    or locked_pending["document"] != pending_state["document"]
                    or locked_pending["action"] != pending_state["action"]
                    or locked_pending["supersede_source_generation_id"]
                    != pending_state["supersede_source_generation_id"]):
                raise ConflictError(
                    "pending material recovery changed before publication"
                )
            if (locked_pending is not None
                    and locked_pending["action"] == "resume"
                    and locked_pending["sources_exact"]):
                if args.crop_annotations:
                    raise ValueError(
                        "--crop-annotations cannot modify an exact pending resume; "
                        "authorize recover-material-build --action supersede first"
                    )
                # A same-runtime retry, or an explicit resume authorization,
                # compiles the exact generation already on disk.  It must not
                # spend another parser pass or silently replace that generation.
                code = 0
                raw_input = locked_pending["raw_input"]
                report = locked_pending["parse_report"]
            else:
                code, raw_input, report = builder.run(
                    build_args,
                    backend=backend,
                    adapter_runner=adapter_runner,
                    publication_workspace=workspace,
                    _publication_locked=True,
                    _deferred_asset_plans=asset_plans,
                )
            no_publish_marker = object()
            no_publish_value = (
                report.get("_no_publish_on_failure", no_publish_marker)
                if isinstance(report, dict) else no_publish_marker
            )
            if (no_publish_value is not no_publish_marker
                    and not isinstance(no_publish_value, bool)):
                raise ValueError(
                    "material builder returned a non-boolean "
                    "_no_publish_on_failure sentinel"
                )
            suppress_failure_report = no_publish_value is True
            if suppress_failure_report and code == 0:
                raise ValueError(
                    "material builder returned success with "
                    "_no_publish_on_failure=true"
                )
            publish_ready = runtime_ok("material_build_publish")
            resuming_existing = (
                locked_pending is not None
                and locked_pending["action"] == "resume"
                and locked_pending["sources_exact"]
            )
            if publish_ready and resuming_existing:
                # Authorization and all source hashes were rechecked while the
                # lock was held.  Do not republish even identical builder
                # candidates: the compiler must consume the already-bound bytes.
                raw_input_sha256 = locked_pending["document"]["raw_input"][
                    "sha256"
                ]
            if publish_ready and not resuming_existing:
                publications = []
                pending_raw_input_sha256 = None
                # A builder-side ownership/policy rejection promises that no
                # workspace file changed.  Preserve that contract in the
                # orchestrated path too; the diagnostic remains in the
                # top-level command payload/stderr instead of replacing the
                # last known-good parse report.
                if code == 0:
                    publications.append((report_path, report))
                    # Pin the deterministic byte generation about to be published.
                    # ingest.py verifies it after acquiring its own publication
                    # lock, closing the parent/child inter-lock replacement gap.
                    pending_raw_input_sha256 = hashlib.sha256(
                        builder._publication_json_bytes(raw_input)
                    ).hexdigest()
                    report_sha256 = hashlib.sha256(
                        builder._publication_json_bytes(report)
                    ).hexdigest()
                    previous_manifest_path = safe_workspace_entry(
                        workspace, ".ingest/build_manifest.json"
                    )
                    if os.path.lexists(str(previous_manifest_path)):
                        previous_manifest_bytes, _snapshot = stable_read_bytes(
                            previous_manifest_path
                        )
                        previous_manifest_sha256 = hashlib.sha256(
                            previous_manifest_bytes
                        ).hexdigest()
                    else:
                        previous_manifest_sha256 = None
                    predecessor = None
                    if locked_pending is not None:
                        predecessor = (
                            locked_pending["document"]["generation_id"]
                            if locked_pending["action"] == "supersede"
                            else locked_pending["document"].get(
                                "supersedes_generation_id"
                            )
                        )
                    pending_generation = build_pending_generation(
                        pending_raw_input_sha256,
                        report_sha256,
                        raw_input,
                        report,
                        previous_manifest_sha256,
                        supersedes_generation_id=predecessor,
                    )
                    if (locked_pending is not None
                            and locked_pending["action"] != "supersede"
                            and pending_generation != locked_pending["document"]):
                        raise ValueError(
                            "rebuilt candidate does not reproduce the pending generation; "
                            "authorize an explicit supersede instead"
                        )
                    publications.extend((
                        (raw_path, raw_input),
                        (
                            os.path.join(
                                workspace,
                                *MATERIAL_BUILD_PENDING_PATH.split("/"),
                            ),
                            pending_generation,
                        ),
                        (
                            os.path.join(
                                workspace, ".ingest", "ai_review_manifest.json"
                            ),
                            {
                                "note": (
                                    "Legacy view only; canonical lifecycle is "
                                    ".ingest/review_queue.jsonl."
                                ),
                                "entries": (report or {}).get("ai_review", []),
                            },
                        ),
                    ))
                    if (locked_pending is not None
                            and locked_pending["action"] == "supersede"):
                        if locked_pending["recovery_log"] is None:
                            raise ValueError(
                                "supersede requires an explicit recovery authorization"
                            )
                        abandoned = abandon_latest_runtime_recovery(
                            locked_pending["recovery_log"],
                            pending_generation["generation_id"],
                        )
                        publications.append((
                            os.path.join(
                                workspace,
                                *locked_pending["recovery_path"].split("/"),
                            ),
                            abandoned,
                        ))
                    if locked_pending is not None:
                        for ancestor in locked_pending["ancestor_recoveries"]:
                            ancestor_log = ancestor["log"]
                            if ancestor_log["records"][-1]["outcome"] is None:
                                ancestor_log = abandon_latest_runtime_recovery(
                                    ancestor_log,
                                    ancestor["child_generation_id"],
                                )
                                publications.append((
                                    os.path.join(
                                        workspace, *ancestor["path"].split("/")
                                    ),
                                    ancestor_log,
                                ))

                def publish_builder_batch():
                    builder._publish_builder_transaction(
                        publications,
                        asset_plans=asset_plans if code == 0 else (),
                        blocker_paths=(
                            os.path.join(
                                workspace,
                                *MATERIAL_BUILD_PENDING_PATH.split("/"),
                            ),
                        ) if code == 0 else (),
                    )

                if publications or (code == 0 and asset_plans):
                    if ingest_preexisting:
                        publish_builder_batch()
                    else:
                        # publication_lock owns state, but a brand-new .ingest had
                        # no mutation lock yet.  Create and hold it before writing.
                        with IngestionStore(workspace).mutation_lock():
                            publish_builder_batch()
                raw_input_sha256 = pending_raw_input_sha256
    except ConflictError as exc:
        steps.append({
            "name": "material_build",
            "status": "failed",
            "exit_code": 1,
            "operation_error": "publication_conflict",
        })
        payload["error"] = "material build JSON publication conflict: %s" % exc
        _emit(payload, args.as_json)
        return 1
    except (OSError, ValueError) as exc:
        steps.append({
            "name": "material_build",
            "status": "failed",
            "exit_code": 2,
            "operation_error": "publication_failed",
        })
        payload["error"] = "material build publication failed: %s" % exc
        _emit(payload, args.as_json)
        return 2
    if not publish_ready:
        _emit(payload, args.as_json)
        return 2
    steps.append({
        "name": "material_build",
        "status": (
            "resumed" if code == 0 and pending_state is not None
            and pending_state["action"] == "resume"
            else "passed" if code == 0 else "failed"
        ),
        "exit_code": code,
        "warnings": len((report or {}).get("warnings", [])),
        "review_entries": len((report or {}).get("ai_review", [])),
    })
    if code != 0:
        payload["error"] = (raw_input or {}).get("error", "material build failed")
        _emit(payload, args.as_json)
        return code

    ingest_command = [
        sys.executable,
        os.path.join(SCRIPT_DIR, "ingest.py"),
        "--input", raw_path,
        "--output-dir", workspace,
        "--expected-input-sha256", raw_input_sha256,
    ]
    if args.lang:
        ingest_command.extend(("--lang", args.lang))
    # Do not hold a parent lock across a subprocess.  ingest.py owns the full
    # state->ingestion publication lock; an outer state lock would make its
    # non-reentrant acquisition fail.  Bound the child with gate checks on both
    # sides and trust only its explicit exit status.
    if not runtime_ok("workspace_compile"):
        _emit(payload, args.as_json)
        return 2
    ingested = _run(ingest_command)
    steps.append({
        "name": "workspace_compile",
        "status": "passed" if ingested.returncode == 0 else "failed",
        "exit_code": ingested.returncode,
    })
    if ingested.returncode != 0:
        payload["error"] = (ingested.stderr or ingested.stdout).strip()
        _emit(payload, args.as_json)
        return ingested.returncode
    if not runtime_ok("workspace_compile_exit"):
        _emit(payload, args.as_json)
        return 2

    state_path = os.path.join(workspace, "study_state.json")
    if not os.path.isfile(state_path):
        if not runtime_ok("study_state_init"):
            _emit(payload, args.as_json)
            return 2
        initialized = _run([
            sys.executable,
            os.path.join(SCRIPT_DIR, "update_progress.py"),
            "--workspace", workspace,
            "init",
        ])
        steps.append({
            "name": "study_state_init",
            "status": "passed" if initialized.returncode == 0 else "failed",
            "exit_code": initialized.returncode,
        })
        if initialized.returncode != 0:
            payload["error"] = (initialized.stderr or initialized.stdout).strip()
            _emit(payload, args.as_json)
            return initialized.returncode
    if args.artifact_mode is not None:
        if not runtime_ok("artifact_preference"):
            _emit(payload, args.as_json)
            return 2
        preference = _run([
            sys.executable,
            os.path.join(SCRIPT_DIR, "update_progress.py"),
            "--workspace", workspace,
            "set", "--artifact-mode", args.artifact_mode,
        ])
        steps.append({
            "name": "artifact_preference",
            "status": "passed" if preference.returncode == 0 else "failed",
            "exit_code": preference.returncode,
        })
        if preference.returncode != 0:
            payload["error"] = (preference.stderr or preference.stdout).strip()
            _emit(payload, args.as_json)
            return preference.returncode

    if args.visual_index != "never":
        if not runtime_ok("visual_index"):
            _emit(payload, args.as_json)
            return 2
        visual = _run([
            sys.executable,
            os.path.join(SCRIPT_DIR, "build_visual_index.py"),
            "--workspace", workspace,
            "--materials", materials,
            "--apply",
            "--apply-wiki",
        ])
        visual_ok = visual.returncode == 0
        steps.append({
            "name": "visual_index",
            "status": "passed" if visual_ok else (
                "warning" if args.visual_index == "auto" else "failed"
            ),
            "exit_code": visual.returncode,
        })
        if not runtime_ok("post_visual_recompile"):
            _emit(payload, args.as_json)
            return 2
        recompiled = _run([
            sys.executable,
            os.path.join(SCRIPT_DIR, "ingest_review.py"),
            "--workspace", workspace,
            "rebuild",
        ])
        if not runtime_ok("post_visual_recompile_exit"):
            _emit(payload, args.as_json)
            return 2
        steps.append({
            "name": "post_visual_recompile",
            "status": "passed" if recompiled.returncode == 0 else "failed",
            "exit_code": recompiled.returncode,
        })
        if recompiled.returncode != 0:
            payload["error"] = (recompiled.stderr or recompiled.stdout).strip()
            _emit(payload, args.as_json)
            return recompiled.returncode
        if not visual_ok and args.visual_index == "required":
            payload["error"] = (visual.stderr or visual.stdout).strip()
            _emit(payload, args.as_json)
            return visual.returncode or 1

    if not runtime_ok("workspace_validation"):
        _emit(payload, args.as_json)
        return 2

    validated = _run([
        sys.executable,
        os.path.join(SCRIPT_DIR, "validate_workspace.py"),
        workspace,
        "--json",
    ])
    try:
        validation = _validated_workspace_payload(validated)
    except ValueError as exc:
        steps.append({
            "name": "workspace_validation",
            "status": "failed",
            "exit_code": validated.returncode,
            "operation_error": str(exc),
        })
        detail = (validated.stderr or validated.stdout or "").strip()
        payload["error"] = "workspace validator operation failed: %s%s" % (
            exc, ("; " + detail[:500]) if detail else "")
        _emit(payload, args.as_json)
        return validated.returncode if validated.returncode not in (0, 10) else 1
    readiness = validation.get("readiness") or "blocked"
    steps.append({
        "name": "workspace_validation",
        "status": {"ready": "passed", "usable_with_gaps": "warning",
                   "blocked": "blocked"}.get(readiness, "blocked"),
        "exit_code": validated.returncode,
        "readiness": readiness,
        "errors": validation.get("error_count", len(validation.get("errors") or [])),
        "warnings": validation.get("warning_count", len(validation.get("warnings") or [])),
        "error_summary": validation.get("error_summary") or {},
        "warning_summary": validation.get("warning_summary") or {},
        "truncated": validation.get("truncated") or {},
    })
    payload["process_success"] = True
    payload["readiness"] = readiness
    payload["validation"] = validation
    payload["capabilities"] = validation.get("capabilities") or {}
    payload["next_action"] = {
        "ready": "resume_exam_coach",
        "usable_with_gaps": "name_warnings_then_resume_or_review",
        "blocked": "ingest_review",
    }.get(readiness, "inspect_validation")
    if not runtime_ok("final_receipt"):
        payload["process_success"] = False
        _emit(payload, args.as_json)
        return 2
    _emit(payload, args.as_json)
    return 10 if readiness == "blocked" else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(run())
