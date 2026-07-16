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
        workspace_publication_lock,
    )
except ImportError:
    from scripts import exam_start
    from scripts.ingestion import (
        ConflictError,
        IngestionStore,
        safe_workspace_entry,
        workspace_publication_lock,
    )


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


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
        description="Official lightweight course ingestion orchestrator"
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
    if args.ingest_adapter != "core" and adapter_runner is None:
        payload["error"] = (
            "optional ingest adapters require a programmatic host-injected adapter_runner; "
            "the ordinary CLI does not provide a trusted runner registry"
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
            "mode, time_budget, language, and a matching runtime provenance receipt; "
            "run exam_start.py confirm with the intended installed skill first"
        )
        _emit(payload, args.as_json)
        return 2

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
    ] + (["--course-name", args.course_name] if args.course_name else []))
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
        with workspace_publication_lock(workspace):
            if ingest_preexisting and not os.path.lexists(ingest_path):
                raise ConflictError(".ingest changed while acquiring the publication lock")
            code, raw_input, report = builder.run(
                build_args,
                backend=backend,
                adapter_runner=adapter_runner,
                publication_workspace=workspace,
                _publication_locked=True,
                _deferred_asset_plans=asset_plans,
            )
            publish_ready = runtime_ok("material_build_publish")
            if publish_ready:
                publications = []
                pending_raw_input_sha256 = None
                if report is not None:
                    publications.append((report_path, report))
                if code == 0:
                    # Pin the deterministic byte generation about to be published.
                    # ingest.py verifies it after acquiring its own publication
                    # lock, closing the parent/child inter-lock replacement gap.
                    pending_raw_input_sha256 = hashlib.sha256(
                        builder._publication_json_bytes(raw_input)
                    ).hexdigest()
                    publications.extend((
                        (raw_path, raw_input),
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

                def publish_builder_batch():
                    builder._publish_builder_transaction(
                        publications,
                        asset_plans=asset_plans if code == 0 else (),
                    )

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
        "status": "passed" if code == 0 else "failed",
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
