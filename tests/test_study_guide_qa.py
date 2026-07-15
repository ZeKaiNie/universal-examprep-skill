# -*- coding: utf-8 -*-
import hashlib
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import study_guide_qa as qa  # noqa: E402


def png_payload(width, height, marker=b""):
    return (qa.PNG_SIGNATURE + b"\x00\x00\x00\x0dIHDR"
            + struct.pack(">II", width, height) + marker)


class FakeBackend(object):
    name = "fake-renderer"

    def __init__(self, pages=None, error=None):
        self.pages = pages or []
        self.error = error
        self.last_input = None

    def render_pages(self, pdf_input):
        self.last_input = pdf_input
        if self.error:
            raise self.error
        return [dict(page) for page in self.pages]


def clean_page(number, total=2):
    return {
        "png": png_payload(1200, 1800, str(number).encode("ascii")),
        "text": "Readable chapter content.\nPage %d of %d" % (number, total),
        "width": 1200,
        "height": 1800,
        "white_ratio": 0.93,
    }


class StudyGuideQATest(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="study-guide-qa-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        self.guide = os.path.join(self.ws, "study_guide")
        os.makedirs(self.guide)
        os.makedirs(os.path.join(self.ws, "notebook"))
        with open(os.path.join(self.ws, "study_state.json"), "w", encoding="utf-8") as stream:
            json.dump({"language": "bilingual", "artifact_mode": "visual"}, stream)
        self.manifest_path = os.path.join(self.ws, "notebook", "ch01.guide.json")
        with open(self.manifest_path, "w", encoding="utf-8") as stream:
            json.dump({"schema_version": 1, "chapter": 1}, stream)
        self.html = os.path.join(self.guide, "ch01.html")
        with open(self.html, "w", encoding="utf-8", newline="\n") as stream:
            stream.write('<html><body><details class="quiz-answer">Answer</details></body></html>')
        self.pdf = os.path.join(self.guide, "ch01.pdf")
        with open(self.pdf, "wb") as stream:
            stream.write(b"%PDF-1.4\nsynthetic test PDF\n%%EOF\n")
        self.receipt_path = os.path.join(self.guide, "ch01.receipt.json")
        self.gate = {
            "ready_to_use": True,
            "workspace": self.ws,
            "materials": os.path.join(self.ws, "materials"),
            "registered_course": "fixture-course",
            "runtime_provenance": {"receipt": {
                "runtime_digest": "a" * 64,
                "runtime_file_count": 12,
                "skill_version": "5.0.0-test",
                "git_commit": "b" * 40,
                "git_branch": "codex/test",
                "git_dirty": False,
                "python_executable": sys.executable,
            }},
        }
        self.manifest_report = {
            "chapter": 1,
            "language": "bilingual",
            "profile": "full",
            "expected_item_ids": ["item-1"],
            "walkthrough_item_ids": ["item-1"],
            "omitted_item_ids": [],
            "input_path": self.manifest_path,
        }
        gate_snapshot = qa._start_gate_snapshot(self.gate)
        html_hash = qa._sha256_file(self.html)
        pdf_hash = qa._sha256_file(self.pdf)
        input_hash = qa._conversion_input_hash(self.html)
        converter = os.path.abspath("C:/browser/msedge.exe")
        started = "2026-07-14T11:00:00Z"
        completed = "2026-07-14T11:00:01Z"
        self.write_receipt({
            "schema_version": 2,
            "artifact_type": "study_guide",
            "chapter": 1,
            "profile": "full",
            "language": "bilingual",
            "content_manifest": "notebook/ch01.guide.json",
            "content_manifest_sha256": qa._sha256_file(self.manifest_path),
            "expected_item_ids": ["item-1"],
            "rendered_item_ids": ["item-1"],
            "omitted_item_ids": [],
            "html_file": "study_guide/ch01.html",
            "html_sha256": html_hash,
            "pdf_file": "study_guide/ch01.pdf",
            "pdf_sha256": pdf_hash,
            "pdf_backend": "browser",
            "converter": converter,
            "conversion_input_html_sha256": input_hash,
            "conversion_started_at": started,
            "conversion_completed_at": completed,
            "conversion_run_sha256": qa._conversion_run_hash(
                1, "full", "bilingual", html_hash, pdf_hash, input_hash,
                converter, started, completed, gate_snapshot,
            ),
            "preflight": {
                "status": "injected-test-converter", "pdf_backend": "browser",
                "missing_needed": [], "probe_error": None,
            },
            "start_gate": gate_snapshot,
            "generated_at": "2026-07-14T11:00:02Z",
            "status": "qa_pending",
            "visual_qa": {"schema_version": 1, "status": "pending"},
        })
        self.backend = FakeBackend([clean_page(1), clean_page(2)])
        manifest_patch = mock.patch.object(
            qa.study_guide_content, "load_and_validate_manifest",
            side_effect=lambda unused_ws, unused_chapter: ({}, dict(self.manifest_report)),
        )
        gate_patch = mock.patch.object(
            qa.exam_start, "check_registered_workspace_gate",
            side_effect=lambda unused_ws: self.gate,
        )
        manifest_patch.start()
        gate_patch.start()
        self.addCleanup(manifest_patch.stop)
        self.addCleanup(gate_patch.stop)

    def write_receipt(self, receipt):
        with open(self.receipt_path, "w", encoding="utf-8") as stream:
            json.dump(receipt, stream, ensure_ascii=False, indent=2)

    def read_receipt(self):
        with open(self.receipt_path, encoding="utf-8") as stream:
            return json.load(stream)

    def invoke(self, command, *extra, backend=None, now="2026-07-14T12:00:00Z"):
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ["--workspace", self.ws, "--chapter", "1", "--json", command]
        argv.extend(extra)
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = qa.main(argv, backend=self.backend if backend is None else backend,
                           now=now)
        return code, stdout.getvalue(), stderr.getvalue()

    def render_clean(self):
        code, unused_out, error = self.invoke("render")
        self.assertEqual(0, code, error)
        return self.read_receipt()

    def accept_clean(self, **kwargs):
        args = ["--inspected-pages", kwargs.get("inspected", "all"),
                "--reviewer", kwargs.get("reviewer", "codex"),
                "--reviewer-kind", kwargs.get("kind", "agent")]
        verdicts = kwargs.get("verdicts")
        if verdicts is None:
            page_count = len(self.backend.pages)
            verdicts = ["%d=pass" % number for number in range(1, page_count + 1)]
        for verdict in verdicts:
            args += ["--page-verdict", verdict]
        if kwargs.get("defect"):
            args += ["--unresolved-defect", kwargs["defect"]]
        return self.invoke("accept", *args, now="2026-07-14T12:30:00Z")

    def test_render_uses_prebound_pdf_and_records_every_page_digest(self):
        with open(self.pdf, "rb") as stream:
            expected_pdf_bytes = stream.read()
        receipt = self.render_clean()
        self.assertIsInstance(self.backend.last_input, bytes)
        self.assertEqual(expected_pdf_bytes, self.backend.last_input)
        with open(self.pdf, "rb") as stream:
            expected_pdf_hash = hashlib.sha256(stream.read()).hexdigest()
        self.assertEqual(expected_pdf_hash, receipt["pdf_sha256"])
        visual = receipt["visual_qa"]
        self.assertEqual("rendered", visual["status"])
        self.assertEqual("fake-renderer", visual["renderer"])
        self.assertEqual(2, visual["page_count"])
        self.assertEqual("passed", visual["auto_lint"]["status"])
        self.assertEqual([], visual["unresolved_defects"])
        self.assertRegex(visual["render_manifest_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual([1, 2], [page["page"] for page in visual["pages"]])
        for number, page in enumerate(visual["pages"], 1):
            expected = os.path.join(self.guide, "qa", "ch01_p%03d.png" % number)
            self.assertTrue(os.path.isfile(expected))
            with open(expected, "rb") as stream:
                actual_hash = hashlib.sha256(stream.read()).hexdigest()
            self.assertEqual(actual_hash, page["png_sha256"])
            self.assertTrue(page["metrics"]["page_number_text_visible"])

    def test_transient_pdf_swap_restore_cannot_publish_attacker_pages(self):
        with open(self.pdf, "rb") as stream:
            trusted = stream.read()
        pdf_path = self.pdf

        class SwapRestoreBackend(object):
            name = "swap-restore-renderer"

            def __init__(self):
                self.received = None

            def render_pages(self, pdf_input):
                self.received = pdf_input
                backup = pdf_path + ".trusted"
                moved = False
                try:
                    # Windows rejects this while QA retains the verified handle.  POSIX may
                    # allow it, in which case the guide-directory generation check detects
                    # the complete replace/render/restore cycle after this method returns.
                    os.replace(pdf_path, backup)
                    moved = True
                    with open(pdf_path, "wb") as stream:
                        stream.write(b"%PDF-1.4\nattacker PDF\n%%EOF\n")
                    return [clean_page(1, total=1)]
                finally:
                    if moved:
                        if os.path.exists(pdf_path):
                            os.remove(pdf_path)
                        os.replace(backup, pdf_path)

        attacker = SwapRestoreBackend()
        code, unused_out, error = self.invoke("render", backend=attacker)
        self.assertEqual(1, code, error)
        self.assertIsInstance(attacker.received, bytes)
        self.assertEqual(trusted, attacker.received)
        with open(self.pdf, "rb") as stream:
            self.assertEqual(trusted, stream.read())
        receipt = self.read_receipt()
        self.assertEqual("qa_pending", receipt["status"])
        self.assertEqual("blocked", receipt["visual_qa"]["status"])
        self.assertEqual(
            "render_failed", receipt["visual_qa"]["unresolved_defects"][0]["code"]
        )
        self.assertFalse(os.path.exists(
            os.path.join(self.guide, "qa", "ch01_p001.png")
        ))
        code, unused_out, error = self.accept_clean(verdicts=["1=pass"])
        self.assertEqual(1, code)
        self.assertNotEqual("ready", self.read_receipt()["status"])

    def test_accept_requires_and_records_explicit_all_page_inspection(self):
        self.render_clean()
        code, unused_out, error = self.accept_clean()
        self.assertEqual(0, code, error)
        receipt = self.read_receipt()
        visual = receipt["visual_qa"]
        self.assertEqual("ready", receipt["status"])
        self.assertEqual("ready", visual["status"])
        self.assertEqual("all", visual["inspected_pages"])
        self.assertEqual("codex", visual["reviewer"])
        self.assertEqual("agent", visual["reviewer_kind"])
        self.assertEqual("2026-07-14T12:30:00Z", visual["accepted_at"])
        self.assertEqual([1, 2], [row["page"] for row in visual["page_verdicts"]])
        self.assertTrue(all(row["verdict"] == "pass" for row in visual["page_verdicts"]))
        self.assertEqual(list(qa.MANUAL_REVIEW_CHECKS),
                         visual["accepted_manual_review_checks"])
        self.assertEqual(qa._acceptance_manifest_hash(visual),
                         visual["acceptance_manifest_sha256"])

    def test_accept_rejects_partial_inspection_or_declared_defect(self):
        self.render_clean()
        code, unused_out, error = self.accept_clean(inspected="1")
        self.assertEqual(2, code)
        self.assertIn("inspected-pages all", error)
        self.assertEqual("rendered", self.read_receipt()["visual_qa"]["status"])
        code, unused_out, error = self.accept_clean(defect="formula clipped")
        self.assertEqual(1, code)
        self.assertIn("unresolved defects", error)
        self.assertEqual("rendered", self.read_receipt()["visual_qa"]["status"])

    def test_auto_lint_is_blocking_for_blank_raw_tex_bad_text_and_missing_page_number(self):
        broken = FakeBackend([
            {"png": png_payload(100, 100, b"blank"), "text": "", "width": 100, "height": 100,
             "white_ratio": 1.0},
            {"png": png_payload(100, 100, b"bad"), "text": "formula $x$ \x00 \ufffd",
             "width": 100, "height": 100, "white_ratio": 0.90},
        ])
        code, unused_out, error = self.invoke("render", backend=broken)
        self.assertEqual(10, code, error)
        visual = self.read_receipt()["visual_qa"]
        self.assertEqual("blocked", visual["status"])
        self.assertEqual("failed", visual["auto_lint"]["status"])
        codes = {item["code"] for item in visual["unresolved_defects"]}
        self.assertTrue({"blank_page", "raw_tex_visible", "nul_or_replacement_text",
                         "page_number_not_visible"} <= codes)
        self.assertTrue(os.path.isfile(os.path.join(self.guide, "qa", "ch01_p001.png")))
        code, unused_out, error = self.accept_clean()
        self.assertEqual(1, code)
        self.assertIn("automatic lint", error)

    def test_accept_requires_complete_unique_pass_page_verdicts(self):
        self.render_clean()
        code, unused_out, error = self.accept_clean(verdicts=[])
        self.assertEqual(2, code)
        self.assertIn("page-verdict", error)
        code, unused_out, error = self.accept_clean(verdicts=["1=pass", "1=pass"])
        self.assertEqual(2, code)
        self.assertIn("duplicate", error)
        code, unused_out, error = self.accept_clean(verdicts=["1=pass", "2=fail:clipped"])
        self.assertEqual(1, code)
        self.assertIn("non-pass verdict", error)
        self.assertEqual("qa_pending", self.read_receipt()["status"])

    def test_bilingual_css_page_footer_is_recognized(self):
        self.assertTrue(qa._page_number_visible(
            "第 / Page 2 / 6\n正文 / Body", 2, 6))

    def test_pdf_hash_drift_refuses_acceptance(self):
        self.render_clean()
        with open(self.pdf, "ab") as stream:
            stream.write(b"changed")
        code, unused_out, error = self.accept_clean()
        self.assertEqual(1, code)
        self.assertIn("hash drifted", error)
        self.assertEqual("rendered", self.read_receipt()["visual_qa"]["status"])

    def test_base_receipt_drift_refuses_acceptance(self):
        receipt = self.render_clean()
        receipt["html_sha256"] = "b" * 64
        self.write_receipt(receipt)
        code, unused_out, error = self.accept_clean()
        self.assertEqual(1, code)
        self.assertIn("HTML hash drifted", error)

    def test_missing_or_changed_rendered_page_refuses_acceptance(self):
        self.render_clean()
        first = os.path.join(self.guide, "qa", "ch01_p001.png")
        os.remove(first)
        code, unused_out, error = self.accept_clean()
        self.assertEqual(1, code)
        self.assertIn("missing rendered QA page", error)

        self.invoke("render")
        with open(first, "ab") as stream:
            stream.write(b"tampered")
        code, unused_out, error = self.accept_clean()
        self.assertEqual(1, code)
        self.assertIn("hash drifted", error)

    def test_unexpected_page_path_is_rejected_even_with_recomputed_manifest(self):
        receipt = self.render_clean()
        receipt["visual_qa"]["pages"][0]["png"] = "study_guide/qa/../outside.png"
        receipt["visual_qa"]["render_manifest_sha256"] = qa._render_manifest_hash(
            receipt["visual_qa"]
        )
        self.write_receipt(receipt)
        code, unused_out, error = self.accept_clean()
        self.assertEqual(1, code)
        self.assertIn("unexpected PNG path", error)

    def test_renderer_failure_invalidates_prior_ready_receipt(self):
        self.render_clean()
        self.assertEqual(0, self.accept_clean()[0])
        failing = FakeBackend(error=RuntimeError("render exploded"))
        code, unused_out, error = self.invoke("render", backend=failing)
        self.assertEqual(1, code)
        self.assertIn("render exploded", error)
        visual = self.read_receipt()["visual_qa"]
        self.assertEqual("blocked", visual["status"])
        self.assertEqual("render_failed", visual["unresolved_defects"][0]["code"])
        self.assertNotIn("reviewer", visual)

    def test_existing_pdf_hash_mismatch_is_rejected_before_render(self):
        receipt = self.read_receipt()
        receipt["pdf_sha256"] = "0" * 64
        self.write_receipt(receipt)
        code, unused_out, error = self.invoke("render")
        self.assertEqual(1, code)
        self.assertIn("hash drifted", error)
        self.assertEqual("pending", self.read_receipt()["visual_qa"]["status"])

    def test_extra_receipt_field_is_fail_closed(self):
        receipt = self.read_receipt()
        receipt["page_count"] = 9
        self.write_receipt(receipt)
        code, unused_out, error = self.invoke("render")
        self.assertEqual(1, code)
        self.assertIn("artifact receipt fields are invalid", error)
        self.assertEqual("pending", self.read_receipt()["visual_qa"]["status"])

    def test_abnormal_text_whitespace_is_blocking(self):
        whitespace = FakeBackend([{
            "png": png_payload(100, 100, b"space"), "text": (" \n" * 100) + "Page 1 of 1",
            "width": 100, "height": 100, "white_ratio": 0.95,
        }])
        code, unused_out, error = self.invoke("render", backend=whitespace)
        self.assertEqual(10, code, error)
        codes = {item["code"] for item in
                 self.read_receipt()["visual_qa"]["unresolved_defects"]}
        self.assertIn("abnormal_text_whitespace", codes)

    def test_receipt_pdf_path_traversal_is_rejected(self):
        receipt = self.read_receipt()
        receipt["pdf_file"] = "../outside.pdf"
        self.write_receipt(receipt)
        code, unused_out, error = self.invoke("render")
        self.assertEqual(1, code)
        self.assertIn("canonical chapter PDF", error)
        self.assertEqual("pending", self.read_receipt()["visual_qa"]["status"])

    def test_minimal_receipt_is_rejected(self):
        self.write_receipt({"schema_version": 2, "chapter": 1})
        code, unused_out, error = self.invoke("render")
        self.assertEqual(1, code)
        self.assertIn("receipt fields are invalid", error)

    def test_native_external_pdf_binding_is_forbidden(self):
        receipt = self.read_receipt()
        for field in (
                "pdf_file", "pdf_sha256", "converter", "conversion_input_html_sha256",
                "conversion_started_at", "conversion_completed_at", "conversion_run_sha256"):
            receipt[field] = None
        receipt["pdf_backend"] = "native"
        receipt["preflight"]["pdf_backend"] = "native"
        receipt["status"] = "awaiting_native_pdf"
        self.write_receipt(receipt)
        code, unused_out, error = self.invoke("render")
        self.assertEqual(1, code)
        self.assertIn("external/native PDF binding is forbidden", error)

    def test_conversion_and_current_state_drift_are_rejected(self):
        receipt = self.read_receipt()
        receipt["conversion_input_html_sha256"] = "0" * 64
        self.write_receipt(receipt)
        code, unused_out, error = self.invoke("render")
        self.assertEqual(1, code)
        self.assertIn("conversion input drifted", error)

        # Restore the strict fixture by repairing the conversion fields and then drift state.
        receipt["conversion_input_html_sha256"] = qa._conversion_input_hash(self.html)
        receipt["conversion_run_sha256"] = qa._conversion_run_hash(
            1, receipt["profile"], receipt["language"], receipt["html_sha256"],
            receipt["pdf_sha256"], receipt["conversion_input_html_sha256"],
            receipt["converter"], receipt["conversion_started_at"],
            receipt["conversion_completed_at"], receipt["start_gate"],
        )
        self.write_receipt(receipt)
        with open(os.path.join(self.ws, "study_state.json"), "w", encoding="utf-8") as stream:
            json.dump({"language": "en", "artifact_mode": "visual"}, stream)
        code, unused_out, error = self.invoke("render")
        self.assertEqual(1, code)
        self.assertIn("language drifted", error)

    def test_symlinked_pdf_or_qa_directory_is_rejected(self):
        outside = os.path.join(self.ws, "outside.pdf")
        with open(outside, "wb") as stream:
            stream.write(b"%PDF-1.4\noutside\n")
        os.remove(self.pdf)
        try:
            os.symlink(outside, self.pdf)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("platform does not permit symlinks")
        code, unused_out, error = self.invoke("render")
        self.assertEqual(1, code)
        self.assertIn("symlink", error)

    def test_backend_discovery_is_lazy_and_missing_backend_is_exit_3(self):
        # Call main without an injected backend to exercise lazy discovery.
        stderr = io.StringIO()
        with mock.patch.object(qa, "detect_backend", return_value=None), redirect_stderr(stderr):
            code = qa.main(["--workspace", self.ws, "--chapter", "1", "render"])
        self.assertEqual(3, code)
        self.assertIn("no PDF rendering backend", stderr.getvalue())
        self.assertEqual("pending", self.read_receipt()["visual_qa"]["status"])

    def test_receipt_and_qa_page_symlinks_are_rejected(self):
        self.render_clean()
        page = os.path.join(self.guide, "qa", "ch01_p001.png")
        outside = os.path.join(self.ws, "outside.png")
        with open(outside, "wb") as stream:
            stream.write(png_payload(1200, 1800))
        os.remove(page)
        try:
            os.symlink(outside, page)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("platform does not permit symlinks")
        code, unused_out, error = self.accept_clean()
        self.assertEqual(1, code)
        self.assertIn("symlink", error)


if __name__ == "__main__":
    unittest.main()
