import tempfile
import unittest
from unittest import mock

from scripts.host_adapters import command_core
from scripts.host_adapters import langgraph_exam as adapter


HEX_A = "a" * 64


def receipt(payload, exit_code=0, binding=None):
    result = {"payload": payload, "exit_code": exit_code}
    if binding is not None:
        result["binding"] = binding
    return result


def capabilities(structural="ready", teaching="ready", artifact="ready",
                 artifact_reasons=None, artifact_mode="chat", manifest=True,
                 chapter=1, active_review_issues=0):
    return {
        "chapter": chapter,
        "workspace_structural": {
            "status": structural, "ready": structural == "ready",
            "reason_codes": [], "counts": {},
        },
        "teaching_ready": {
            "status": teaching, "ready": teaching == "ready", "reason_codes": [],
            "counts": {"chapter": chapter,
                       "active_review_issues": active_review_issues},
        },
        "quiz_ready": {
            "status": "ready", "ready": True, "reason_codes": [],
            "counts": {"chapter": chapter},
        },
        "artifact_ready": {
            "status": artifact, "ready": artifact == "ready",
            "reason_codes": list(artifact_reasons or ()),
            "counts": {"chapter": chapter, "artifact_mode": artifact_mode,
                       "manifest": manifest},
        },
    }


def dependency_snapshot(content_sha=HEX_A, snapshot_sha=None):
    return {
        "schema_version": 1,
        "algorithm": "sha256-tree-v1",
        "snapshot_sha256": snapshot_sha or content_sha,
        "content_sha256": content_sha,
        "root_count": 1,
        "directory_count": 1,
        "file_count": 1,
        "total_bytes": 1,
    }


def validation(readiness="ready", warnings=None, errors=None, content_sha=HEX_A,
               snapshot_sha=None, **kwargs):
    payload = {
        "readiness": readiness,
        "capabilities": capabilities(**kwargs),
        "dependency_snapshot": dependency_snapshot(content_sha, snapshot_sha),
        "warning_count": len(warnings or ()),
        "warning_summary": {},
        "warnings": list(warnings or ()),
        "error_count": len(errors or ()),
        "error_summary": {},
        "errors": list(errors or ()),
        "truncated": {"errors": 0, "warnings": 0},
    }
    chapter = payload["capabilities"]["chapter"]
    return receipt(
        payload, binding=command_core.validation_binding(payload, chapter, content_sha))


def completion_snapshot(validation_receipt, progress_receipt):
    progress_receipt = dict(progress_receipt)
    progress_receipt.setdefault("state_binding", {
        "schema_version": 1, "path": "study_state.json",
        "sha256": "f" * 64, "size_bytes": 1,
    })
    snapshot = validation_receipt["payload"]["dependency_snapshot"]
    return {
        "schema_version": 1,
        "validation_receipt": validation_receipt,
        "progress_receipt": progress_receipt,
        "dependency_snapshot": snapshot,
        "binding": command_core.completion_binding(
            validation_receipt, progress_receipt, snapshot),
    }


def with_tutor_handoff(state):
    state["tutor_handoff"] = adapter.hint_receipt(state, "tutor_notebook")
    return state


def guide_gate(stage, artifact_mode="visual", chapter=1, page_hash="b" * 64):
    page_stage = artifact_mode == "visual" and stage in ("inspection", "ready")
    return adapter.validate_study_guide_gate_receipt({
        "schema_version": 1,
        "chapter": chapter,
        "artifact_mode": artifact_mode,
        "stage": stage,
        "pdf_sha256": "a" * 64 if page_stage else None,
        "render_manifest_sha256": "c" * 64 if page_stage else None,
        "pages": ([{
            "page": 1,
            "png": "study_guide/qa/ch%02d_p001.png" % chapter,
            "png_sha256": page_hash,
        }] if page_stage else []),
    })


class StartAndIngestRoutesTest(unittest.TestCase):
    def test_invalid_interrupt_resume_routes_to_halt_before_any_command(self):
        self.assertEqual(
            "operation_error", adapter.route_after_interrupt({"operation_error": "bad"}))
        self.assertEqual("continue", adapter.route_after_interrupt({"operation_error": ""}))

    def test_unconfirmed_pair_interrupts_even_when_status_exit_was_zero(self):
        state = {"start_gate_receipt": receipt({
            "process_success": True, "ready_to_ingest": False,
        })}
        self.assertEqual("confirmation_interrupt", adapter.route_after_rehydrate(state))

    def test_ready_pair_routes_by_actual_workspace_state(self):
        base = {"start_gate_receipt": receipt({
            "process_success": True, "ready_to_ingest": True,
        })}
        self.assertEqual("ingest", adapter.route_after_rehydrate(dict(
            base, has_structured_workspace=False)))
        self.assertEqual("validate", adapter.route_after_rehydrate(dict(
            base, has_structured_workspace=True)))

    def test_start_operation_failure_never_becomes_confirmation_fallback(self):
        state = {"start_gate_receipt": receipt({
            "process_success": False, "ready_to_ingest": False,
        })}
        self.assertEqual("operation_error", adapter.route_after_rehydrate(state))

    def test_confirm_requires_open_gate(self):
        self.assertEqual("ingest", adapter.route_after_confirm({
            "start_gate_receipt": receipt({"ready_to_ingest": True})}))
        self.assertEqual("operation_error", adapter.route_after_confirm({
            "start_gate_receipt": receipt({"ready_to_ingest": False})}))

    def test_ingest_exit_ten_routes_to_validator_not_error(self):
        state = {"ingest_receipt": receipt({
            "process_success": True, "readiness": "blocked",
        }, exit_code=10)}
        self.assertEqual("validate", adapter.route_after_ingest(state))

    def test_real_ingest_operation_failure_stops(self):
        state = {"ingest_receipt": receipt({
            "process_success": False, "readiness": "unknown",
        }, exit_code=2)}
        self.assertEqual("operation_error", adapter.route_after_ingest(state))


class ReadinessRoutesTest(unittest.TestCase):
    def test_only_active_typed_review_enters_typed_review(self):
        for state in (
            {"validation_receipt": validation(
                "blocked", structural="blocked", active_review_issues=1)},
            {"validation_receipt": validation(
                "blocked", teaching="blocked", active_review_issues=1)},
        ):
            self.assertEqual("review_interrupt", adapter.route_after_validation(state))

    def test_unrepairable_structural_error_is_operation_error(self):
        state = {"validation_receipt": validation(
            "blocked", structural="blocked",
            errors=[{"reason_code": "unsafe_workspace_tree", "msg": "unsafe"}])}
        self.assertEqual("operation_error", adapter.route_after_validation(state))

    def test_malformed_capability_matrix_is_operation_error_even_if_rebound(self):
        malformed = validation("ready")
        payload = malformed["payload"]
        payload["capabilities"]["teaching_ready"].pop("ready")
        malformed["binding"] = command_core.validation_binding(
            payload, 1, HEX_A)
        self.assertEqual(
            "operation_error",
            adapter.route_after_validation({"validation_receipt": malformed}),
        )

    def test_source_parser_and_build_drift_use_dedicated_recovery_gates(self):
        source = {"validation_receipt": validation(
            "blocked", structural="blocked",
            errors=[{"reason_code": "source_hash_drift", "msg": "source drift"}])}
        build = {"validation_receipt": validation(
            "blocked", structural="blocked",
            errors=[{"reason_code": "build_manifest_hash_drift", "msg": "build drift"}])}
        self.assertEqual("reingest_interrupt", adapter.route_after_validation(source))
        self.assertEqual("rebuild_interrupt", adapter.route_after_validation(build))

    def test_usable_with_gaps_must_surface_warnings_once(self):
        state = {"validation_receipt": validation("usable_with_gaps")}
        self.assertEqual("warning_interrupt", adapter.route_after_validation(state))
        state["warning_acknowledgement"] = adapter.hint_receipt(state, "warnings")
        self.assertEqual("tutor_interrupt", adapter.route_after_validation(state))

    def test_tutor_handoff_precedes_artifact_routing(self):
        state = {"validation_receipt": validation("ready")}
        self.assertEqual("tutor_interrupt", adapter.route_after_validation(state))

    def test_missing_typed_manifest_repeats_guide_gate_until_validator_passes(self):
        state = with_tutor_handoff({
            "validation_receipt": validation(
                "ready", artifact="blocked",
                artifact_reasons=["chapter_teaching_manifest_missing"]),
        })
        self.assertEqual("study_guide_rehydrate", adapter.route_after_validation(state))

    def test_visual_mode_needs_visual_qa_receipt(self):
        state = with_tutor_handoff({
            "artifact_mode": "visual",
            "validation_receipt": validation(
                "ready", artifact="blocked", artifact_mode="visual",
                artifact_reasons=["chapter_visual_qa_not_ready"]),
        })
        self.assertEqual("study_guide_rehydrate", adapter.route_after_validation(state))

    def test_artifact_ready_moves_only_to_completion_command_gate(self):
        state = with_tutor_handoff({
            "validation_receipt": validation("ready", artifact="ready"),
        })
        self.assertEqual("completion_interrupt", adapter.route_after_validation(state))

    def test_tutor_and_warning_hints_expire_on_content_or_chapter_change(self):
        state = {"validation_receipt": validation("usable_with_gaps", chapter=1)}
        state["warning_acknowledgement"] = adapter.hint_receipt(state, "warnings")
        state["tutor_handoff"] = adapter.hint_receipt(state, "tutor_notebook")
        self.assertEqual("completion_interrupt", adapter.route_after_validation(state))
        state["validation_receipt"] = validation(
            "usable_with_gaps", chapter=1, content_sha="b" * 64)
        self.assertEqual("warning_interrupt", adapter.route_after_validation(state))
        state = {"validation_receipt": validation("ready", chapter=2)}
        state["tutor_handoff"] = {
            "schema_version": 1, "gate": "tutor_notebook", "chapter": 1,
            "content_sha256": "c" * 64, "validation_sha256": "d" * 64,
            "warning_sha256": "e" * 64,
            "hint_dependency_sha256": "c" * 64,
        }
        self.assertEqual("tutor_interrupt", adapter.route_after_validation(state))


class StudyGuideSubgraphPureRoutesTest(unittest.TestCase):
    def test_each_canonical_stage_routes_to_one_explicit_interrupt(self):
        routes = {
            "claim_create": "guide_claim_create_interrupt",
            "claim_attach": "guide_claim_attach_interrupt",
            "claim_verify": "guide_claim_verify_interrupt",
            "typed_validate": "guide_typed_validate_interrupt",
            "import": "guide_import_interrupt",
            "preflight": "guide_preflight_interrupt",
            "html": "guide_html_interrupt",
            "pdf": "guide_pdf_interrupt",
            "qa_render": "guide_qa_render_interrupt",
            "inspection": "guide_inspection_interrupt",
        }
        for stage, expected in routes.items():
            with self.subTest(stage=stage):
                state = {"chapter": 1, "study_guide_gate_receipt": guide_gate(stage)}
                self.assertEqual(expected, adapter.route_after_study_guide_gate(state))

    def test_ready_receipt_returns_to_fresh_workspace_validation(self):
        state = {"chapter": 1, "study_guide_gate_receipt": guide_gate("ready")}
        self.assertEqual("validate", adapter.route_after_study_guide_gate(state))
        chat = {"chapter": 1, "study_guide_gate_receipt": guide_gate(
            "ready", artifact_mode="chat")}
        self.assertEqual("validate", adapter.route_after_study_guide_gate(chat))

    def test_checkpoint_acknowledgements_cannot_skip_a_canonical_stage(self):
        state = {
            "chapter": 1,
            "last_resume": {"imported": True, "accepted": True},
            "study_guide_gate_receipt": guide_gate("claim_create"),
        }
        self.assertEqual(
            "guide_claim_create_interrupt", adapter.route_after_study_guide_gate(state))

    def test_stage_transition_allows_one_step_or_regression_but_not_a_jump(self):
        first = guide_gate("claim_create")
        self.assertEqual(
            "claim_attach", adapter.validate_study_guide_gate_transition(
                first, guide_gate("claim_attach"))["stage"])
        self.assertEqual(
            "claim_create", adapter.validate_study_guide_gate_transition(
                guide_gate("typed_validate"), first)["stage"])
        self.assertEqual(
            "html", adapter.validate_study_guide_gate_transition(
                guide_gate("preflight"), guide_gate("html"))["stage"])
        with self.assertRaisesRegex(adapter.LangGraphAdapterError, "skipped"):
            adapter.validate_study_guide_gate_transition(first, guide_gate("pdf"))
        chat_import = guide_gate("import", artifact_mode="chat")
        self.assertEqual(
            "ready", adapter.validate_study_guide_gate_transition(
                chat_import, guide_gate("ready", artifact_mode="chat"))["stage"])
        malformed = dict(first, schema_version=2)
        with self.assertRaisesRegex(adapter.LangGraphAdapterError, "schema"):
            adapter.validate_study_guide_gate_transition(malformed, first)

    def test_stage_transition_remounts_after_chapter_or_mode_change(self):
        chapter_mount = adapter.validate_study_guide_gate_transition(
            guide_gate("ready", chapter=1),
            guide_gate("claim_create", chapter=2),
            {"chapter": 2},
        )
        self.assertEqual((2, "visual", "claim_create"), (
            chapter_mount["chapter"], chapter_mount["artifact_mode"],
            chapter_mount["stage"],
        ))

        mode_mount = adapter.validate_study_guide_gate_transition(
            guide_gate("inspection", artifact_mode="visual"),
            guide_gate("claim_create", artifact_mode="chat"),
            {"chapter": 1},
        )
        self.assertEqual((1, "chat", "claim_create"), (
            mode_mount["chapter"], mode_mount["artifact_mode"], mode_mount["stage"],
        ))

        with self.assertRaisesRegex(adapter.LangGraphAdapterError, "skipped"):
            adapter.validate_study_guide_gate_transition(
                guide_gate("claim_create", chapter=2),
                guide_gate("pdf", chapter=2),
                {"chapter": 2},
            )

    def test_malformed_or_identity_drifted_gate_receipt_fails_closed(self):
        optimistic = dict(guide_gate("claim_create"))
        optimistic["ready"] = True
        self.assertEqual("operation_error", adapter.route_after_study_guide_gate({
            "chapter": 1, "study_guide_gate_receipt": optimistic,
        }))
        drifted = guide_gate("claim_attach", chapter=2)
        self.assertEqual("operation_error", adapter.route_after_study_guide_gate({
            "chapter": 1, "study_guide_gate_receipt": drifted,
        }))
        unbounded = dict(guide_gate("claim_attach"), pdf_sha256="x" * 10000)
        with self.assertRaisesRegex(adapter.LangGraphAdapterError, "binding"):
            adapter.validate_study_guide_gate_receipt(unbounded)

    def test_all_page_inspection_hint_is_hash_bound_and_only_routes_to_accept(self):
        gate = guide_gate("inspection")
        response = {
            "inspected_pages": "all",
            "reviewer": "codex-visual-review",
            "reviewer_kind": "agent",
            "page_verdicts": ["1=pass:formula and layout checked"],
        }
        hint = adapter.create_study_guide_inspection_hint(response, gate)
        state = {
            "chapter": 1,
            "study_guide_gate_receipt": gate,
            "study_guide_inspection_hint": hint,
        }
        self.assertEqual(
            "guide_accept_interrupt", adapter.route_after_study_guide_gate(state))
        self.assertTrue(adapter.study_guide_inspection_hint_is_current(hint, gate))

        changed = guide_gate("inspection", page_hash="d" * 64)
        changed_state = dict(state, study_guide_gate_receipt=changed)
        self.assertFalse(adapter.study_guide_inspection_hint_is_current(hint, changed))
        self.assertEqual(
            "guide_inspection_interrupt",
            adapter.route_after_study_guide_gate(changed_state),
        )

    def test_missing_non_pass_or_partial_page_verdicts_are_rejected(self):
        gate = guide_gate("inspection")
        base = {
            "inspected_pages": "all", "reviewer": "reviewer",
            "reviewer_kind": "agent", "page_verdicts": [],
        }
        with self.assertRaisesRegex(adapter.LangGraphAdapterError, "every page"):
            adapter.create_study_guide_inspection_hint(base, gate)
        base["page_verdicts"] = ["1=fail:clipped"]
        with self.assertRaisesRegex(adapter.LangGraphAdapterError, "verdict is invalid"):
            adapter.create_study_guide_inspection_hint(base, gate)
        base.update(reviewer="r" * 201, page_verdicts=["1=pass"])
        with self.assertRaisesRegex(adapter.LangGraphAdapterError, "every page"):
            adapter.create_study_guide_inspection_hint(base, gate)

    def test_chat_mode_cannot_claim_a_visual_stage(self):
        with self.assertRaisesRegex(adapter.LangGraphAdapterError, "identity"):
            guide_gate("pdf", artifact_mode="chat")


class CompletionAndDependencyBoundaryTest(unittest.TestCase):
    def test_completion_uses_persisted_phase_evidence(self):
        payload = {
            "current_phase": 2,
            "phase_evidence": {"2": {"status": "verified"}},
        }
        self.assertEqual("verified", adapter.completed_phase_status(payload))
        fresh_validation = validation("ready", chapter=2)
        progress = receipt(payload)
        state = with_tutor_handoff({
            "chapter": 2,
            "validation_receipt": fresh_validation,
            "completion_snapshot_receipt": completion_snapshot(
                fresh_validation, progress),
        })
        self.assertEqual("end", adapter.route_after_completion_check(state))

    def test_resume_flag_cannot_substitute_for_phase_evidence(self):
        fresh_validation = validation("ready", chapter=2)
        progress = receipt({
            "current_phase": 2, "phase_evidence": {"2": {}}
        })
        state = with_tutor_handoff({
            "chapter": 2,
            "last_resume": {"done": True},
            "validation_receipt": fresh_validation,
            "completion_snapshot_receipt": completion_snapshot(
                fresh_validation, progress),
        })
        self.assertEqual("completion_interrupt", adapter.route_after_completion_check(state))

    def test_completion_rechecks_tutor_and_warning_hints_against_every_fresh_field(self):
        progress = receipt({
            "current_phase": 1,
            "phase_evidence": {"1": {"status": "verified"}},
        })
        old_validation = validation("ready", content_sha="a" * 64)
        tutor_state = with_tutor_handoff({
            "chapter": 1, "validation_receipt": old_validation,
        })
        fresh_validation = validation("ready", content_sha="b" * 64)
        tutor_state["completion_snapshot_receipt"] = completion_snapshot(
            fresh_validation, progress)
        self.assertEqual(
            "tutor_interrupt", adapter.route_after_completion_check(tutor_state))

        validation_only_state = with_tutor_handoff({
            "chapter": 1, "validation_receipt": old_validation,
        })
        validation_only = validation("ready", content_sha="a" * 64)
        validation_only["payload"]["capabilities"]["teaching_ready"][
            "counts"]["receipt_revision"] = 2
        validation_only["binding"] = command_core.validation_binding(
            validation_only["payload"], 1, "a" * 64)
        validation_only_state["completion_snapshot_receipt"] = completion_snapshot(
            validation_only, progress)
        self.assertEqual(
            "tutor_interrupt",
            adapter.route_after_completion_check(validation_only_state))

        old_warning = validation(
            "usable_with_gaps", warnings=[{"msg": "old"}], content_sha="c" * 64)
        warning_state = {
            "chapter": 1, "validation_receipt": old_warning,
        }
        warning_state["warning_acknowledgement"] = adapter.hint_receipt(
            warning_state, "warnings")
        warning_state["tutor_handoff"] = adapter.hint_receipt(
            warning_state, "tutor_notebook")
        fresh_warning = validation(
            "usable_with_gaps", warnings=[{"msg": "new"}], content_sha="c" * 64)
        warning_state["completion_snapshot_receipt"] = completion_snapshot(
            fresh_warning, progress)
        self.assertEqual(
            "warning_interrupt", adapter.route_after_completion_check(warning_state))

        chapter_state = with_tutor_handoff({
            "chapter": 1, "validation_receipt": old_validation,
        })
        chapter_validation = validation("ready", chapter=2, content_sha="e" * 64)
        chapter_state["completion_snapshot_receipt"] = completion_snapshot(
            chapter_validation, progress)
        self.assertEqual(
            "operation_error", adapter.route_after_completion_check(chapter_state))

    def test_complete_phase_progress_write_does_not_livelock_current_hints(self):
        old_validation = validation(
            "ready", content_sha="a" * 64, snapshot_sha="b" * 64)
        state = with_tutor_handoff({
            "chapter": 1, "validation_receipt": old_validation,
        })
        fresh_validation = validation(
            "ready", content_sha="a" * 64, snapshot_sha="c" * 64)
        progress = receipt({
            "current_phase": 1,
            "phase_evidence": {"1": {"status": "verified"}},
        })
        state["completion_snapshot_receipt"] = completion_snapshot(
            fresh_validation, progress)
        self.assertEqual("end", adapter.route_after_completion_check(state))

    def test_completion_fresh_validation_blocks_source_build_and_review_drift(self):
        progress = receipt({
            "current_phase": 1,
            "phase_evidence": {"1": {"status": "verified"}},
        })
        cases = (
            (validation(
                "blocked", structural="blocked", content_sha="b" * 64,
                errors=[{"reason_code": "source_hash_drift", "msg": "source drift"}]),
             "reingest_interrupt"),
            (validation(
                "blocked", structural="blocked", content_sha="c" * 64,
                errors=[{"reason_code": "build_manifest_hash_drift",
                         "msg": "build drift"}]),
             "rebuild_interrupt"),
            (validation(
                "blocked", teaching="blocked", active_review_issues=1,
                content_sha="d" * 64),
             "review_interrupt"),
        )
        for fresh_validation, expected in cases:
            with self.subTest(route=expected):
                state = with_tutor_handoff({
                    "workspace": "/course", "chapter": 1,
                    "validation_receipt": validation("ready"),
                })
                state["completion_snapshot_receipt"] = completion_snapshot(
                    fresh_validation, progress)
                self.assertEqual(
                    expected, adapter.route_after_completion_check(state))

    def test_completion_check_consumes_one_atomic_host_snapshot(self):
        class FakeApi:
            def __init__(self, snapshot):
                self.snapshot = snapshot
                self.calls = []

            def completion_snapshot(self, workspace, chapter):
                self.calls.append(("completion_snapshot", workspace, chapter))
                return self.snapshot

        base = with_tutor_handoff({
            "workspace": "/course", "chapter": 1,
            "validation_receipt": validation("ready"),
        })
        progress = receipt({
            "current_phase": 1,
            "phase_evidence": {"1": {"status": "verified"}},
        })
        ready_validation = validation("ready")
        ready_api = FakeApi(completion_snapshot(ready_validation, progress))
        ready_update = adapter.completion_check_update(ready_api, base)
        self.assertEqual(
            [("completion_snapshot", "/course", 1)],
            ready_api.calls)
        self.assertIsNotNone(ready_update["progress_receipt"])
        self.assertEqual(
            ready_api.snapshot, ready_update["completion_snapshot_receipt"])

    def test_operation_error_wins_over_optimistic_checkpoint_fields(self):
        state = {
            "operation_error": "runtime drift",
            "tutor_handoff_complete": True,
            "validation_receipt": validation("ready"),
        }
        self.assertEqual("operation_error", adapter.route_after_validation(state))

    def test_graph_factory_requires_host_checkpointer_before_optional_import(self):
        with self.assertRaisesRegex(adapter.LangGraphAdapterError, "checkpointer"):
            adapter.build_exam_graph(None)

    def test_graph_factory_reports_optional_dependency_when_langgraph_is_absent(self):
        original_import = __import__

        def without_langgraph(name, *args, **kwargs):
            if name == "langgraph" or name.startswith("langgraph."):
                raise ImportError("simulated optional dependency absence")
            return original_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=without_langgraph):
            with self.assertRaises(adapter.OptionalDependencyUnavailable):
                adapter.build_exam_graph(object())

    def test_graph_really_compiles_when_optional_langgraph_is_installed(self):
        try:
            from langgraph.checkpoint.memory import MemorySaver
        except ImportError:
            self.skipTest("optional LangGraph is not installed")
        graph = adapter.build_exam_graph(MemorySaver())
        self.assertIsNotNone(graph)

    def test_real_graph_stops_at_confirmation_interrupt_before_ingest(self):
        try:
            from langgraph.checkpoint.memory import MemorySaver
        except ImportError:
            self.skipTest("optional LangGraph is not installed")

        class StartOnlyApi:
            def __init__(self):
                self.calls = []

            def exam_start_status(self, workspace, materials):
                self.calls.append(("status", workspace, materials))
                return receipt({"process_success": True, "ready_to_ingest": False})

        api = StartOnlyApi()
        graph = adapter.build_exam_graph(MemorySaver(), api)
        config = {"configurable": {"thread_id": "confirmation-smoke"}}
        graph.invoke({
            "workspace": adapter.os.path.abspath("course"),
            "materials": adapter.os.path.abspath("materials"),
            "course": "course", "mode": "from_scratch", "time_budget": "le1d",
            "language": "bilingual", "artifact_mode": "visual", "chapter": 1,
        }, config)
        snapshot = graph.get_state(config)
        self.assertEqual(("confirmation_interrupt",), snapshot.next)
        self.assertEqual(1, len(api.calls))
        self.assertEqual(
            {"confirmed": True}, snapshot.tasks[0].interrupts[0].value["resume_contract"])

    def test_real_graph_invalid_source_drift_resume_never_calls_ingest(self):
        try:
            from langgraph.checkpoint.memory import MemorySaver
            from langgraph.types import Command
        except ImportError:
            self.skipTest("optional LangGraph is not installed")

        class DriftApi:
            def __init__(self):
                self.ingest_calls = 0

            def exam_start_status(self, workspace, materials):
                return receipt({"process_success": True, "ready_to_ingest": True})

            def validate_workspace(self, workspace, chapter):
                return validation(
                    "blocked", structural="blocked",
                    errors=[{"reason_code": "source_hash_drift", "msg": "source drift"}],
                )

            def ingest_course(self, workspace, materials):
                self.ingest_calls += 1
                return receipt({"process_success": True, "readiness": "ready"})

        with tempfile.TemporaryDirectory() as workspace:
            adapter.os.makedirs(adapter.os.path.join(workspace, ".ingest"))
            api = DriftApi()
            graph = adapter.build_exam_graph(MemorySaver(), api)
            config = {"configurable": {"thread_id": "invalid-drift-resume"}}
            graph.invoke({
                "workspace": workspace, "materials": adapter.os.path.abspath("materials"),
                "course": "course", "mode": "from_scratch", "time_budget": "le1d",
                "language": "bilingual", "artifact_mode": "visual", "chapter": 1,
            }, config)
            self.assertEqual(("reingest_interrupt",), graph.get_state(config).next)
            graph.invoke(Command(resume={"done": True}), config)
            final = graph.get_state(config)
            self.assertEqual(0, api.ingest_calls)
            self.assertEqual("blocked_operation", final.values["terminal_status"])

    def test_review_list_and_resume_contracts_fail_closed(self):
        with self.assertRaisesRegex(adapter.LangGraphAdapterError, "review list"):
            adapter.validate_review_receipt(receipt({
                "count": 0, "returned": 0, "cursor": 0, "summary": {},
            }))
        valid_review = {
            "workspace": adapter.os.path.abspath("course"), "count": 1, "returned": 0,
            "cursor": 0, "next_cursor": 0, "has_more": True,
            "summary": {
                "by_status": {"pending": 1},
                "by_severity": {"blocking": 1},
                "by_reason": {"missing_answer": 1},
            },
            "details_file": None, "issues": [],
        }
        self.assertEqual(
            valid_review, adapter.validate_review_receipt(receipt(valid_review)))
        with self.assertRaisesRegex(adapter.LangGraphAdapterError, "resume"):
            adapter.validate_resume({}, "typed_review")
        with self.assertRaisesRegex(adapter.LangGraphAdapterError, "finite JSON"):
            adapter.validate_resume({"score": float("nan")}, "typed_review")

    def test_each_resume_gate_requires_its_explicit_acknowledgement(self):
        contracts = {
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
        for gate, field in contracts.items():
            with self.subTest(gate=gate):
                with self.assertRaisesRegex(
                        adapter.LangGraphAdapterError, field + "=true"):
                    adapter.validate_resume({"done": True}, gate)
                self.assertEqual(
                    {field: True}, adapter.validate_resume({field: True}, gate))
        with self.assertRaisesRegex(adapter.LangGraphAdapterError, "unknown resume gate"):
            adapter.validate_resume({"done": True}, "unknown")


if __name__ == "__main__":
    unittest.main(verbosity=2)
