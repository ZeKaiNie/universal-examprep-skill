"""Contract and policy tests for optional high-fidelity adapters."""

import os
import hashlib
import tempfile
import threading
import unittest
from unittest import mock

from scripts.ingestion import adapters as A


def _page(source_file="materials/notes.txt", page=1, text="Grounded text"):
    return {
        "file": source_file,
        "page": page,
        "text": text,
        "elements": [{
            "kind": "text",
            "text": text,
            "ordinal": 0,
            "bbox": None,
            "method": "native",
            "confidence": 1.0,
            "metadata": {"backend_locator": "p%d" % page},
        }],
        "embedded_assets": [],
        "review_signals": [],
    }


def _runner_pages(pages, discovered_page_count, warnings=None):
    return {
        "pages": pages,
        "discovered_page_count": discovered_page_count,
        "warnings": list(warnings or ()),
    }


class IngestionAdapterContractTest(unittest.TestCase):
    def test_core_text_request_and_receipt_are_content_addressed_and_deterministic(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "notes.txt")
            with open(path, "w", encoding="utf-8") as stream:
                stream.write("One local page.\n")
            request = A.ExtractionRequest.from_path(
                path, "materials/notes.txt", "text/plain",
                config={"layout": {"mode": "local"}, "threshold": 0.8},
            )

            first = A.CoreAdapter().extract(request)
            second = A.resolve_adapter("auto").extract(request)

            self.assertEqual(first, second)
            self.assertEqual(first.pages[0]["text"], "One local page.\n")
            receipt = first.receipt.to_dict()
            self.assertEqual(receipt["adapter"], "core")
            self.assertEqual(receipt["source_sha256"], request.source_sha256)
            self.assertEqual(receipt["requested_pages"], [])
            self.assertEqual(receipt["produced_pages"], [1])
            self.assertEqual(receipt["discovered_page_count"], 1)
            self.assertEqual(receipt["status"], "success")
            self.assertEqual(
                receipt["policy"], {"network": False, "upload": False, "install": False}
            )
            self.assertNotIn("source_path", first.to_dict()["receipt"])
            self.assertEqual(
                request.config_sha256,
                A.ExtractionRequest.from_path(
                    path, "materials/notes.txt", "text/plain",
                    config={"threshold": 0.8, "layout": {"mode": "local"}},
                ).config_sha256,
            )

    def test_core_backend_preserves_requested_page_accounting(self):
        class Backend:
            def page_texts(self, path):
                return ["page one", "page two", "page three"]

        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "source.pdf")
            with open(path, "wb") as stream:
                stream.write(b"synthetic backend input")
            request = A.ExtractionRequest.from_path(
                path, "materials/source.pdf", "application/pdf", pages=(3, 1),
            )
            result = A.CoreAdapter(backend=Backend()).extract(request)
            self.assertEqual([page["page"] for page in result.pages], [1, 3])
            self.assertEqual(result.receipt.requested_pages, (1, 3))

    def test_vendor_runner_is_normalized_without_importing_vendor_package(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "source.pdf")
            with open(path, "wb") as stream:
                stream.write(b"local input")
            request = A.ExtractionRequest.from_path(
                path, "materials/source.pdf", "application/pdf", pages=[2],
            )
            runner = mock.Mock(return_value={
                "pages": [_page("materials/source.pdf", 2, "layout text")],
                "discovered_page_count": 3,
                "warnings": ["formula tree retained as text"],
            })
            with mock.patch.object(A.importlib.util, "find_spec", return_value=None):
                result = A.DoclingAdapter(runner=runner).extract(request)

            runner.assert_called_once_with(request)
            self.assertEqual(result.warnings, ("formula tree retained as text",))
            self.assertEqual(result.receipt.adapter, "docling")
            self.assertIsNone(result.receipt.module)
            self.assertEqual(result.receipt.produced_pages, (2,))
            self.assertEqual(result.receipt.discovered_page_count, 3)

    def test_discovered_page_count_proves_full_contiguous_enumeration(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "source.pdf")
            with open(path, "wb") as stream:
                stream.write(b"input")
            request = A.ExtractionRequest.from_path(
                path, "source.pdf", "application/pdf")
            complete = _runner_pages(
                [_page("source.pdf", number) for number in (1, 2, 3)], 3)
            result = A.DoclingAdapter(runner=lambda unused: complete).extract(request)
            self.assertEqual((1, 2, 3), result.receipt.produced_pages)
            incomplete = _runner_pages(
                [_page("source.pdf", number) for number in (1, 3)], 3)
            with self.assertRaisesRegex(A.AdapterContractError, "contiguous"):
                A.DoclingAdapter(runner=lambda unused: incomplete).extract(request)

    def test_materialized_assets_are_regular_files_under_root_with_exact_hashes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "source.pdf")
            asset_root = os.path.join(temp, "assets")
            os.makedirs(asset_root)
            with open(path, "wb") as stream:
                stream.write(b"input")
            asset_path = os.path.join(asset_root, "figure.png")
            with open(asset_path, "wb") as stream:
                stream.write(b"local image bytes")
            digest = hashlib.sha256(b"local image bytes").hexdigest()
            page = _page("source.pdf")
            page["elements"][0].update({
                "kind": "figure", "asset": "figure.png",
                "asset_role": "figure", "asset_sha256": digest,
            })
            page["embedded_assets"] = ["figure.png"]
            request = A.ExtractionRequest.from_path(
                path, "source.pdf", "application/pdf", asset_root=asset_root)
            runner = lambda unused: _runner_pages([page], 1)
            A.DoclingAdapter(runner=runner).extract(request)
            bad = _runner_pages([dict(page)], 1)
            bad["pages"][0] = dict(page)
            bad["pages"][0]["elements"] = [dict(page["elements"][0])]
            bad["pages"][0]["elements"][0]["asset_sha256"] = "0" * 64
            with self.assertRaisesRegex(A.AdapterContractError, "hash"):
                A.DoclingAdapter(runner=lambda unused: bad).extract(request)
            with mock.patch.object(
                    A, "is_link_or_reparse",
                    side_effect=lambda value: os.path.basename(os.fspath(value)) == "figure.png"):
                with self.assertRaises(A.AdapterPolicyError):
                    A.DoclingAdapter(runner=runner).extract(request)

            nested = os.path.join(asset_root, "nested")
            os.makedirs(nested)
            nested_asset = os.path.join(nested, "figure.png")
            with open(nested_asset, "wb") as stream:
                stream.write(b"nested image bytes")
            nested_digest = hashlib.sha256(b"nested image bytes").hexdigest()
            nested_page = _page("source.pdf")
            nested_page["elements"][0].update({
                "kind": "figure", "asset": "nested/figure.png",
                "asset_role": "figure", "asset_sha256": nested_digest,
            })
            nested_page["embedded_assets"] = ["nested/figure.png"]
            nested_runner = lambda unused: _runner_pages([nested_page], 1)
            with mock.patch.object(
                    A, "is_link_or_reparse",
                    side_effect=lambda value: os.path.basename(os.fspath(value)) == "nested"):
                with self.assertRaisesRegex(A.AdapterPolicyError, "path contains"):
                    A.DoclingAdapter(runner=nested_runner).extract(request)

    def test_probe_uses_metadata_without_importing_vendor(self):
        with mock.patch.object(A.importlib.util, "find_spec", return_value=object()), mock.patch.object(
            A.importlib.metadata, "version", return_value="9.8.7"
        ) as version:
            receipt = A.MinerUAdapter().probe()
        self.assertTrue(receipt.available)
        self.assertEqual(receipt.module, "mineru")
        self.assertEqual(receipt.distribution, "mineru")
        self.assertEqual(receipt.version, "9.8.7")
        self.assertFalse(receipt.runner_configured)
        version.assert_called_once_with("mineru")

    def test_missing_vendor_and_missing_runner_fail_explicitly(self):
        with mock.patch.object(A.importlib.util, "find_spec", return_value=None):
            receipt = A.DoclingAdapter().probe()
            self.assertFalse(receipt.available)
            self.assertIn("not installed", receipt.reason)
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "source.pdf")
            with open(path, "wb") as stream:
                stream.write(b"input")
            request = A.ExtractionRequest.from_path(
                path, "source.pdf", "application/pdf",
            )
            with self.assertRaises(A.AdapterUnavailableError):
                A.DoclingAdapter().extract(request)

    def test_local_only_config_rejects_remote_install_and_urls_recursively(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "source.pdf")
            with open(path, "wb") as stream:
                stream.write(b"input")
            forbidden = (
                {"network": True},
                {"nested": {"upload": 1}},
                {"download": "weights"},
                {"endpoint": "https://example.invalid"},
                {"model": "https://example.invalid/model"},
                {"auto_install": True},
            )
            for config in forbidden:
                with self.subTest(config=config), self.assertRaises(A.AdapterPolicyError):
                    A.ExtractionRequest.from_path(
                        path, "source.pdf", "application/pdf", config=config,
                    )

    def test_source_drift_page_mismatch_and_unknown_schema_fields_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "source.pdf")
            with open(path, "wb") as stream:
                stream.write(b"first")
            request = A.ExtractionRequest.from_path(
                path, "source.pdf", "application/pdf", pages=[1],
            )
            with open(path, "wb") as stream:
                stream.write(b"second")
            with self.assertRaises(A.AdapterPolicyError):
                A.DoclingAdapter(runner=lambda unused: [_page("source.pdf")]).extract(request)

            with open(path, "wb") as stream:
                stream.write(b"stable")
            request = A.ExtractionRequest.from_path(
                path, "source.pdf", "application/pdf", pages=[2],
            )
            with self.assertRaises(A.AdapterContractError):
                A.DoclingAdapter(runner=lambda unused: [_page("source.pdf", 1)]).extract(request)

            bad = _page("source.pdf")
            bad["invented"] = "not part of the protocol"
            with self.assertRaises(A.AdapterContractError):
                A.validate_page_records([bad])

    def test_runner_cannot_modify_source_and_still_receive_success_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "source.pdf")
            with open(path, "wb") as stream:
                stream.write(b"stable source revision")
            request = A.ExtractionRequest.from_path(
                path, "source.pdf", "application/pdf",
            )

            def mutating_runner(unused):
                with open(path, "wb") as stream:
                    stream.write(b"changed during adapter execution")
                return _runner_pages([_page("source.pdf")], 1)

            with self.assertRaisesRegex(
                    A.AdapterPolicyError, "source changed while adapter runner executed"):
                A.DoclingAdapter(runner=mutating_runner).extract(request)

    def test_delayed_background_source_mutation_cannot_escape_final_fingerprint(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "source.pdf")
            with open(path, "wb") as stream:
                stream.write(b"stable source revision")
            request = A.ExtractionRequest.from_path(
                path, "source.pdf", "application/pdf",
            )
            mutate_after_post_run_check = threading.Event()
            mutation_finished = threading.Event()
            workers = []

            def delayed_mutation():
                mutate_after_post_run_check.wait(5)
                with open(path, "wb") as stream:
                    stream.write(b"background mutation after runner return")
                mutation_finished.set()

            def runner(unused):
                worker = threading.Thread(target=delayed_mutation)
                worker.start()
                workers.append(worker)
                return _runner_pages([_page("source.pdf")], 1)

            real_asset_validation = A._validate_materialized_assets

            def validate_assets_then_release_mutator(*args, **kwargs):
                real_asset_validation(*args, **kwargs)
                mutate_after_post_run_check.set()
                if not mutation_finished.wait(5):
                    raise AssertionError("background mutation did not finish")

            try:
                with mock.patch.object(
                    A,
                    "_validate_materialized_assets",
                    side_effect=validate_assets_then_release_mutator,
                ):
                    with self.assertRaisesRegex(
                        A.AdapterPolicyError,
                        "source changed before adapter success receipt",
                    ):
                        A.DoclingAdapter(runner=runner).extract(request)
            finally:
                mutate_after_post_run_check.set()
                for worker in workers:
                    worker.join(5)
                    self.assertFalse(worker.is_alive())

    def test_validator_rejects_traversal_bad_ordinals_and_duplicate_pages(self):
        labeled = _page()
        labeled["source_language"] = "en"
        labeled["elements"][0]["source_language"] = "en"
        self.assertEqual(
            "en", A.validate_page_records([labeled])[0]["source_language"])

        invalid_language = _page()
        invalid_language["source_language"] = "fr"
        with self.assertRaisesRegex(A.AdapterContractError, "source_language"):
            A.validate_page_records([invalid_language])

        bad_asset = _page()
        bad_asset["elements"][0]["asset"] = "../escape.png"
        with self.assertRaises(A.AdapterContractError):
            A.validate_page_records([bad_asset])

        bad_ordinal = _page()
        bad_ordinal["elements"][0]["ordinal"] = 4
        with self.assertRaises(A.AdapterContractError):
            A.validate_page_records([bad_ordinal])

        with self.assertRaises(A.AdapterContractError):
            A.validate_page_records([_page(page=1), _page(page=1)])

    def test_resolver_is_explicit_and_never_silently_selects_vendor(self):
        self.assertIsInstance(A.resolve_adapter("auto"), A.CoreAdapter)
        self.assertIsInstance(A.resolve_adapter("docling"), A.DoclingAdapter)
        self.assertIsInstance(A.resolve_adapter("magic-pdf"), A.MinerUAdapter)
        with self.assertRaises(A.AdapterContractError):
            A.resolve_adapter("cloud-service")

    @unittest.skipUnless(A.DoclingAdapter().probe().available, "Docling is not installed")
    def test_optional_docling_installation_has_an_auditable_probe(self):
        receipt = A.DoclingAdapter().probe()
        self.assertTrue(receipt.available)
        self.assertEqual(receipt.adapter, "docling")
        self.assertIsNotNone(receipt.module)

    @unittest.skipUnless(A.MinerUAdapter().probe().available, "MinerU is not installed")
    def test_optional_mineru_installation_has_an_auditable_probe(self):
        receipt = A.MinerUAdapter().probe()
        self.assertTrue(receipt.available)
        self.assertEqual(receipt.adapter, "mineru")
        self.assertIsNotNone(receipt.module)


if __name__ == "__main__":
    unittest.main()
