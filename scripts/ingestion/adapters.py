"""Strict, local-only extraction adapters for optional document backends.

The module deliberately does not import Docling or MinerU.  Capability probes
inspect installation metadata only, and extraction requires an explicitly
provided local runner.  This keeps the default runtime stdlib-only and makes
network/upload/install policy part of every deterministic receipt.
"""

import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
from dataclasses import dataclass

from .identifiers import is_link_or_reparse, normalize_workspace_path
from .language import (
    MATERIAL_TEXT_LANGUAGE_CODES,
    SOURCE_UNIT_LANGUAGE_CODES,
    is_language_neutral_formula,
)


SCHEMA_VERSION = 1
LOCAL_ONLY_POLICY = {
    "network": False,
    "upload": False,
    "install": False,
}

_METHODS = frozenset(("native", "heuristic", "ocr", "vision", "manual", "ai_recovered"))
_QUALITY_ROUTES = frozenset(("fast", "recover", "review"))
_ELEMENT_KINDS = frozenset((
    "title", "heading", "text", "list", "table", "formula", "figure",
    "diagram", "caption", "code", "speaker_notes", "question", "answer", "other",
))
_ASSET_ROLES = frozenset((
    "question_context", "answer_context", "worked_solution", "student_attempt", "figure", "diagram",
    "table", "source_page", "other",
))
_POLICY_KEYS = frozenset((
    "allow_network", "network", "online", "remote", "cloud", "upload",
    "download", "install", "auto_install", "endpoint", "url", "api_url",
))


class AdapterError(Exception):
    """Base class for adapter contract, policy, and execution failures."""


class AdapterUnavailableError(AdapterError):
    """The selected adapter is not installed or has no configured runner."""


class AdapterContractError(AdapterError):
    """An adapter request or normalized page record violates the protocol."""


class AdapterPolicyError(AdapterError):
    """An adapter configuration requests a forbidden external side effect."""


class AdapterExecutionError(AdapterError):
    """A configured backend failed while extracting a source."""

    def __init__(self, message, receipt=None):
        super().__init__(message)
        self.receipt = receipt


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_fingerprint(path):
    """Return a stable stat identity plus digest for one regular source file."""

    filesystem_path = _safe_input_file(path)
    before = os.stat(filesystem_path, follow_symlinks=False)
    digest = _sha256(filesystem_path)
    after = os.stat(filesystem_path, follow_symlinks=False)

    def identity(value):
        return (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )

    before_identity = identity(before)
    after_identity = identity(after)
    if before_identity != after_identity:
        raise AdapterPolicyError("source changed while its fingerprint was being read")
    return after_identity, digest


def _strict_json(value, label="value"):
    """Return a detached JSON value, rejecting lossy/non-finite input."""

    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise AdapterContractError("%s contains a non-finite number" % label)
        return value
    if isinstance(value, list):
        return [_strict_json(item, label) for item in value]
    if isinstance(value, tuple):
        return [_strict_json(item, label) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or "\x00" in key:
                raise AdapterContractError("%s has a non-text/empty/NUL key" % label)
            result[key] = _strict_json(item, label)
        return result
    raise AdapterContractError("%s is not strict JSON data" % label)


def _canonical_hash(value):
    payload = json.dumps(
        _strict_json(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_local_config(config):
    result = _strict_json(config or {}, "adapter config")

    def visit(value, path="config"):
        if isinstance(value, dict):
            for key, item in value.items():
                folded = key.strip().lower().replace("-", "_")
                policy_key = folded in _POLICY_KEYS or any(
                    token in folded for token in (
                        "network", "upload", "download", "install", "remote",
                        "cloud", "endpoint", "url",
                    )
                )
                if policy_key:
                    if folded in ("endpoint", "url", "api_url"):
                        if item not in (None, ""):
                            raise AdapterPolicyError(
                                "%s.%s is forbidden by the local-only policy" % (path, key)
                            )
                    elif item not in (None, False, 0, "", "off", "false", "none", "local"):
                        raise AdapterPolicyError(
                            "%s.%s is forbidden by the local-only policy" % (path, key)
                        )
                visit(item, "%s.%s" % (path, key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, "%s[%d]" % (path, index))
        elif isinstance(value, str):
            lowered = value.strip().lower()
            if "://" in lowered:
                raise AdapterPolicyError(
                    "%s contains a URI forbidden by the local-only policy" % path
                )

    visit(result)
    return result


def _normalize_pages(pages):
    if pages is None:
        return ()
    if not isinstance(pages, (list, tuple, set, frozenset)):
        raise AdapterContractError("pages must be a sequence of positive integers")
    result = []
    for page in pages:
        if type(page) is not int or page < 1:
            raise AdapterContractError("pages must contain only positive integers")
        result.append(page)
    if len(result) != len(set(result)):
        raise AdapterContractError("pages must not contain duplicates")
    return tuple(sorted(result))


def _safe_input_file(path):
    try:
        filesystem_path = os.path.abspath(os.fspath(path))
    except TypeError as exc:
        raise AdapterContractError("source_path must be a filesystem path") from exc
    if not os.path.isfile(filesystem_path):
        raise AdapterContractError("source_path is not a regular file: %s" % filesystem_path)
    if is_link_or_reparse(filesystem_path):
        raise AdapterPolicyError("source_path must not be a link/junction/reparse point")
    return filesystem_path


def _safe_asset_root(asset_root):
    if asset_root is None:
        return None
    try:
        value = os.path.abspath(os.fspath(asset_root))
    except TypeError as exc:
        raise AdapterContractError("asset_root must be a filesystem path") from exc
    if not value or "\x00" in value:
        raise AdapterContractError("asset_root must be a non-empty filesystem path")
    current = value
    while True:
        if os.path.lexists(current) and is_link_or_reparse(current):
            raise AdapterPolicyError("asset_root must not pass through a link/reparse point")
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    if os.path.lexists(value) and not os.path.isdir(value):
        raise AdapterContractError("asset_root must be a directory when it exists")
    return value


@dataclass(frozen=True)
class ExtractionRequest:
    """One immutable, content-addressed extraction request."""

    source_path: str
    source_file: str
    source_sha256: str
    media_type: str
    pages: tuple = ()
    asset_root: object = None
    config: object = None

    @classmethod
    def from_path(
        cls, source_path, source_file, media_type, pages=(), asset_root=None, config=None,
    ):
        path = _safe_input_file(source_path)
        try:
            canonical_file = normalize_workspace_path(source_file)
        except (TypeError, ValueError) as exc:
            raise AdapterContractError("source_file must be a canonical relative path") from exc
        if not isinstance(media_type, str) or not media_type.strip() or "\x00" in media_type:
            raise AdapterContractError("media_type must be a non-empty string")
        normalized_config = _validate_local_config(config or {})
        return cls(
            source_path=path,
            source_file=canonical_file,
            source_sha256=_sha256(path),
            media_type=media_type.strip().lower(),
            pages=_normalize_pages(pages),
            asset_root=_safe_asset_root(asset_root),
            config=normalized_config,
        )

    def __post_init__(self):
        if not isinstance(self.source_sha256, str) or len(self.source_sha256) != 64:
            raise AdapterContractError("source_sha256 must be a lowercase SHA-256 digest")
        if any(char not in "0123456789abcdef" for char in self.source_sha256):
            raise AdapterContractError("source_sha256 must be a lowercase SHA-256 digest")

    @property
    def config_sha256(self):
        return _canonical_hash(self.config or {})

    def to_dict(self):
        return {
            "schema_version": SCHEMA_VERSION,
            "source_file": self.source_file,
            "source_sha256": self.source_sha256,
            "media_type": self.media_type,
            "pages": list(self.pages),
            "config_sha256": self.config_sha256,
            "policy": dict(LOCAL_ONLY_POLICY),
        }


@dataclass(frozen=True)
class CapabilityReceipt:
    schema_version: int
    adapter: str
    available: bool
    module: object
    distribution: object
    version: object
    runner_configured: bool
    policy: dict
    reason: object = None

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "adapter": self.adapter,
            "available": self.available,
            "module": self.module,
            "distribution": self.distribution,
            "version": self.version,
            "runner_configured": self.runner_configured,
            "policy": dict(self.policy),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ExtractionReceipt:
    schema_version: int
    adapter: str
    adapter_version: object
    module: object
    distribution: object
    source_file: str
    source_sha256: str
    media_type: str
    requested_pages: tuple
    produced_pages: tuple
    discovered_page_count: int
    config_sha256: str
    policy: dict
    status: str

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "adapter": self.adapter,
            "adapter_version": self.adapter_version,
            "module": self.module,
            "distribution": self.distribution,
            "source_file": self.source_file,
            "source_sha256": self.source_sha256,
            "media_type": self.media_type,
            "requested_pages": list(self.requested_pages),
            "produced_pages": list(self.produced_pages),
            "discovered_page_count": self.discovered_page_count,
            "config_sha256": self.config_sha256,
            "policy": dict(self.policy),
            "status": self.status,
        }


@dataclass(frozen=True)
class ExtractionResult:
    pages: tuple
    warnings: tuple
    receipt: ExtractionReceipt

    def to_dict(self):
        return {
            "schema_version": SCHEMA_VERSION,
            "pages": [_strict_json(page, "page record") for page in self.pages],
            "warnings": list(self.warnings),
            "receipt": self.receipt.to_dict(),
        }


def _validate_bbox(value, context):
    if value is None:
        return
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise AdapterContractError("%s bbox must be null or four numbers" % context)
    numbers = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise AdapterContractError("%s bbox must contain finite numbers" % context)
        number = float(item)
        if not math.isfinite(number) or number < 0:
            raise AdapterContractError("%s bbox must contain non-negative finite numbers" % context)
        numbers.append(number)
    if numbers[2] < numbers[0] or numbers[3] < numbers[1]:
        raise AdapterContractError("%s bbox must be [x1,y1,x2,y2]" % context)


def _validate_relative_asset(value, context):
    if value is None:
        return
    if not isinstance(value, str):
        raise AdapterContractError("%s asset path must be null or text" % context)
    try:
        normalize_workspace_path(value)
    except ValueError as exc:
        raise AdapterContractError("%s asset path is unsafe" % context) from exc


def _validate_quality(value, context):
    if not isinstance(value, dict) or set(value) != {"score", "reason_codes", "route"}:
        raise AdapterContractError(
            "%s quality_signals must contain exactly score/reason_codes/route" % context
        )
    score = value["score"]
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise AdapterContractError("%s quality score must be numeric" % context)
    if not math.isfinite(float(score)) or not 0.0 <= float(score) <= 1.0:
        raise AdapterContractError("%s quality score must be in [0, 1]" % context)
    reasons = value["reason_codes"]
    if not isinstance(reasons, list) or any(
        not isinstance(reason, str) or not reason for reason in reasons
    ) or len(reasons) != len(set(reasons)):
        raise AdapterContractError("%s quality reason_codes must be unique strings" % context)
    if value["route"] not in _QUALITY_ROUTES:
        raise AdapterContractError("%s quality route is invalid" % context)


def validate_page_records(records, request=None):
    """Validate normalized page records and return detached JSON dictionaries."""

    if not isinstance(records, (list, tuple)):
        raise AdapterContractError("adapter pages must be a list or tuple")
    if not records:
        raise AdapterContractError("adapter returned no page records")
    required = {"file", "page", "text", "elements", "embedded_assets", "review_signals"}
    optional = {"quality_signals", "metadata", "source_language"}
    element_required = {"kind", "text", "ordinal", "bbox"}
    element_optional = {
        "asset", "asset_path", "asset_role", "asset_sha256", "level", "html",
        "latex", "method", "confidence", "metadata", "source_language",
    }
    result = []
    seen_pages = []
    for index, raw in enumerate(records):
        page_context = "page record %d" % index
        page = _strict_json(raw, page_context)
        if not isinstance(page, dict):
            raise AdapterContractError("%s must be an object" % page_context)
        missing = required - set(page)
        unknown = set(page) - required - optional
        if missing or unknown:
            raise AdapterContractError(
                "%s has missing=%r unknown=%r" % (page_context, sorted(missing), sorted(unknown))
            )
        if not isinstance(page["file"], str) or not page["file"]:
            raise AdapterContractError("%s file must be a non-empty string" % page_context)
        try:
            page["file"] = normalize_workspace_path(page["file"])
        except ValueError as exc:
            raise AdapterContractError("%s file must be a canonical relative path" % page_context) from exc
        if request is not None and page["file"] != request.source_file:
            raise AdapterContractError("%s file does not match the request" % page_context)
        if type(page["page"]) is not int or page["page"] < 1:
            raise AdapterContractError("%s page must be a positive integer" % page_context)
        if page["page"] in seen_pages:
            raise AdapterContractError("duplicate page record %d" % page["page"])
        seen_pages.append(page["page"])
        if not isinstance(page["text"], str):
            raise AdapterContractError("%s text must be a string" % page_context)
        if ("source_language" in page
                and page["source_language"] not in MATERIAL_TEXT_LANGUAGE_CODES):
            raise AdapterContractError(
                "%s source_language must be zh or en; zxx is unit-only" % page_context)
        if not isinstance(page["elements"], list):
            raise AdapterContractError("%s elements must be a list" % page_context)
        for ordinal, element in enumerate(page["elements"]):
            context = "%s element %d" % (page_context, ordinal)
            if not isinstance(element, dict):
                raise AdapterContractError("%s must be an object" % context)
            missing = element_required - set(element)
            unknown = set(element) - element_required - element_optional
            if missing or unknown:
                raise AdapterContractError(
                    "%s has missing=%r unknown=%r" % (context, sorted(missing), sorted(unknown))
                )
            if element["kind"] not in _ELEMENT_KINDS:
                raise AdapterContractError("%s kind is not a normalized content kind" % context)
            if not isinstance(element["text"], str):
                raise AdapterContractError("%s text must be a string" % context)
            if "source_language" in element:
                source_language = element["source_language"]
                if source_language not in SOURCE_UNIT_LANGUAGE_CODES:
                    raise AdapterContractError(
                        "%s source_language must be zh, en, or zxx" % context)
                if source_language == "zxx" and not is_language_neutral_formula(
                        element.get("text"), element.get("latex"), element.get("kind")):
                    raise AdapterContractError(
                        "%s source_language=zxx requires formula/symbol-only content"
                        % context
                    )
            if element["ordinal"] != ordinal:
                raise AdapterContractError("%s ordinal must match list order" % context)
            _validate_bbox(element["bbox"], context)
            for key in ("asset", "asset_path"):
                if key in element:
                    _validate_relative_asset(element[key], context)
            asset = element.get("asset")
            asset_path = element.get("asset_path")
            if asset is not None and asset_path is not None and asset != asset_path:
                raise AdapterContractError("%s asset and asset_path disagree" % context)
            materialized_asset = asset or asset_path
            if "asset_role" in element and element["asset_role"] is not None:
                if element["asset_role"] not in _ASSET_ROLES:
                    raise AdapterContractError("%s asset_role is invalid" % context)
                if materialized_asset is None:
                    raise AdapterContractError("%s asset_role requires a materialized asset" % context)
            if "asset_sha256" in element and element["asset_sha256"] is not None:
                digest = element["asset_sha256"]
                if not isinstance(digest, str) or len(digest) != 64 or any(
                    char not in "0123456789abcdef" for char in digest
                ):
                    raise AdapterContractError("%s asset_sha256 is invalid" % context)
                if materialized_asset is None:
                    raise AdapterContractError("%s asset_sha256 requires a materialized asset" % context)
            if "method" in element and element["method"] not in _METHODS:
                raise AdapterContractError("%s extraction method is invalid" % context)
            if "confidence" in element:
                confidence = element["confidence"]
                if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                    raise AdapterContractError("%s confidence must be numeric" % context)
                if not math.isfinite(float(confidence)) or not 0 <= float(confidence) <= 1:
                    raise AdapterContractError("%s confidence must be in [0, 1]" % context)
            if "metadata" in element:
                _strict_json(element["metadata"], "%s metadata" % context)
        assets = page["embedded_assets"]
        if not isinstance(assets, list) or len(assets) != len(set(assets)):
            raise AdapterContractError("%s embedded_assets must be a unique list" % page_context)
        for asset in assets:
            _validate_relative_asset(asset, page_context)
        referenced_assets = {
            element.get("asset") or element.get("asset_path")
            for element in page["elements"]
            if element.get("asset") or element.get("asset_path")
        }
        if set(assets) != referenced_assets:
            raise AdapterContractError(
                "%s embedded_assets must exactly match element asset references" % page_context
            )
        signals = page["review_signals"]
        if not isinstance(signals, list):
            raise AdapterContractError("%s review_signals must be a list" % page_context)
        signal_keys = set()
        for signal in signals:
            if not isinstance(signal, dict) or set(signal) != {"reason_code", "detail"}:
                raise AdapterContractError(
                    "%s review signal must contain exactly reason_code/detail" % page_context
                )
            if any(not isinstance(signal[key], str) or not signal[key] for key in signal):
                raise AdapterContractError("%s review signal values must be non-empty strings" % page_context)
            identity = (signal["reason_code"], signal["detail"])
            if identity in signal_keys:
                raise AdapterContractError("%s contains duplicate review signals" % page_context)
            signal_keys.add(identity)
        if "quality_signals" in page:
            _validate_quality(page["quality_signals"], page_context)
        if "metadata" in page:
            _strict_json(page["metadata"], "%s metadata" % page_context)
        result.append(page)
    if seen_pages != sorted(seen_pages):
        raise AdapterContractError("adapter page records must be in ascending page order")
    if request is not None and request.pages and tuple(seen_pages) != request.pages:
        raise AdapterContractError(
            "adapter returned pages %r; request required exactly %r"
            % (tuple(seen_pages), request.pages)
        )
    return result


def _validate_page_coverage(records, request, discovered_page_count):
    if type(discovered_page_count) is not int or discovered_page_count < 1:
        raise AdapterContractError("discovered_page_count must be a positive integer")
    produced = tuple(page["page"] for page in records)
    if request.pages:
        if request.pages[-1] > discovered_page_count:
            raise AdapterContractError(
                "requested page exceeds discovered_page_count")
        if produced != request.pages:
            raise AdapterContractError(
                "adapter did not return the exact requested page subset")
    else:
        expected = tuple(range(1, discovered_page_count + 1))
        if produced != expected:
            raise AdapterContractError(
                "full extraction pages must be contiguous from 1 through "
                "discovered_page_count")


def _validate_materialized_assets(records, request):
    references = {}
    for page in records:
        for element in page["elements"]:
            relative = element.get("asset") or element.get("asset_path")
            if relative is None:
                continue
            references.setdefault(relative, []).append(element.get("asset_sha256"))
    if not references:
        return
    if request.asset_root is None:
        raise AdapterContractError(
            "materialized adapter assets require an explicit asset_root")
    root = _safe_asset_root(request.asset_root)
    if not os.path.isdir(root):
        raise AdapterContractError("asset_root does not exist after adapter extraction")
    root_real = os.path.realpath(root)
    for relative, declared_hashes in sorted(references.items()):
        candidate = os.path.abspath(os.path.join(root, *relative.split("/")))
        try:
            contained = os.path.commonpath((root, candidate)) == root
        except ValueError:
            contained = False
        if not contained:
            raise AdapterPolicyError("adapter asset escapes asset_root: %s" % relative)
        current = root
        for part in relative.split("/"):
            current = os.path.join(current, part)
            if os.path.lexists(current) and is_link_or_reparse(current):
                raise AdapterPolicyError(
                    "adapter asset path contains a link/reparse entry: %s" % relative)
        if (not os.path.isfile(candidate) or os.path.islink(candidate)
                or is_link_or_reparse(candidate)):
            raise AdapterPolicyError(
                "adapter asset must be a regular non-link/reparse file: %s" % relative)
        candidate_real = os.path.realpath(candidate)
        try:
            real_contained = os.path.commonpath((root_real, candidate_real)) == root_real
        except ValueError:
            real_contained = False
        if not real_contained:
            raise AdapterPolicyError("adapter asset resolves outside asset_root: %s" % relative)
        if any(value is None for value in declared_hashes):
            raise AdapterContractError(
                "every materialized adapter asset requires asset_sha256")
        if len(set(declared_hashes)) != 1:
            raise AdapterContractError(
                "adapter asset references disagree on asset_sha256")
        try:
            actual = _sha256(candidate)
        except OSError as exc:
            raise AdapterContractError(
                "adapter asset could not be hashed: %s" % relative) from exc
        if actual != declared_hashes[0]:
            raise AdapterContractError(
                "adapter asset hash does not match asset_sha256: %s" % relative)


class _AdapterBase(object):
    adapter_id = None
    module_candidates = ()

    def __init__(self, runner=None):
        self.runner = runner

    def _probe_distribution(self):
        for module, distribution in self.module_candidates:
            try:
                present = importlib.util.find_spec(module) is not None
            except (ImportError, AttributeError, ValueError):
                present = False
            if not present:
                continue
            try:
                version = importlib.metadata.version(distribution)
            except importlib.metadata.PackageNotFoundError:
                version = None
            return module, distribution, version
        return None, None, None

    def probe(self):
        module, distribution, version = self._probe_distribution()
        installed = module is not None
        # A custom runner is a capability even in an isolated test/host where the
        # vendor package lives outside the current Python import path.
        available = installed or self.runner is not None
        if not available:
            reason = "optional adapter is not installed"
        elif self.runner is None:
            reason = "installed; configure an explicit local runner before extraction"
        else:
            reason = None
        return CapabilityReceipt(
            schema_version=SCHEMA_VERSION,
            adapter=self.adapter_id,
            available=available,
            module=module,
            distribution=distribution,
            version=version,
            runner_configured=self.runner is not None,
            policy=dict(LOCAL_ONLY_POLICY),
            reason=reason,
        )

    def _receipt(self, request, pages, status, discovered_page_count=0):
        capability = self.probe()
        return ExtractionReceipt(
            schema_version=SCHEMA_VERSION,
            adapter=self.adapter_id,
            adapter_version=capability.version,
            module=capability.module,
            distribution=capability.distribution,
            source_file=request.source_file,
            source_sha256=request.source_sha256,
            media_type=request.media_type,
            requested_pages=request.pages,
            produced_pages=tuple(pages),
            discovered_page_count=discovered_page_count,
            config_sha256=request.config_sha256,
            policy=dict(LOCAL_ONLY_POLICY),
            status=status,
        )

    def _run(self, request):
        if self.runner is None:
            raise AdapterUnavailableError(
                "%s requires an explicitly configured local runner" % self.adapter_id
            )
        if not callable(self.runner):
            raise AdapterContractError("adapter runner must be callable")
        return self.runner(request)

    def extract(self, request):
        if not isinstance(request, ExtractionRequest):
            raise AdapterContractError("extract() requires an ExtractionRequest")
        # Re-check immutable content identity immediately before invoking code
        # outside this module; source drift must fail closed.
        try:
            before_stat, current_sha256 = _source_fingerprint(request.source_path)
        except (OSError, AdapterContractError, AdapterPolicyError) as exc:
            receipt = self._receipt(request, (), "failed")
            raise AdapterExecutionError(
                "source became unreadable after ExtractionRequest was created",
                receipt=receipt,
            ) from exc
        if current_sha256 != request.source_sha256:
            raise AdapterPolicyError("source changed after ExtractionRequest was created")
        _validate_local_config(request.config or {})
        try:
            raw = self._run(request)
            try:
                after_stat, after_sha256 = _source_fingerprint(request.source_path)
            except (OSError, AdapterContractError, AdapterPolicyError) as exc:
                raise AdapterPolicyError(
                    "source changed or became unsafe while adapter runner executed"
                ) from exc
            if after_stat != before_stat or after_sha256 != current_sha256:
                raise AdapterPolicyError(
                    "source changed while adapter runner executed"
                )
            if (not isinstance(raw, dict)
                    or set(raw) - {"pages", "warnings", "discovered_page_count"}
                    or not {"pages", "discovered_page_count"}.issubset(raw)):
                raise AdapterContractError(
                    "runner object must contain pages/discovered_page_count and optional warnings"
                )
            pages = raw["pages"]
            discovered_page_count = raw["discovered_page_count"]
            warning_values = raw.get("warnings", [])
            if not isinstance(warning_values, (list, tuple)) or any(
                not isinstance(item, str) or not item for item in warning_values
            ):
                raise AdapterContractError("runner warnings must be non-empty strings")
            warnings = tuple(warning_values)
            normalized = validate_page_records(pages, request)
            _validate_page_coverage(normalized, request, discovered_page_count)
            _validate_materialized_assets(normalized, request)
        except AdapterError:
            raise
        except Exception as exc:
            receipt = self._receipt(request, (), "failed")
            raise AdapterExecutionError(
                "%s runner failed: %s" % (self.adapter_id, exc), receipt=receipt
            ) from exc
        produced = tuple(page["page"] for page in normalized)
        receipt = self._receipt(
            request, produced, "success", discovered_page_count=discovered_page_count)
        # The runner can return while a helper thread still owns the source.
        # Page/asset validation (and even receipt preparation) may then provide
        # enough time for that helper to mutate or replace the file after the
        # earlier post-run check.  Recheck the same regular, non-reparse revision
        # at the last boundary before the success receipt can leave this module.
        try:
            final_stat, final_sha256 = _source_fingerprint(request.source_path)
        except (OSError, AdapterContractError, AdapterPolicyError) as exc:
            raise AdapterPolicyError(
                "source changed or became unsafe before adapter success receipt"
            ) from exc
        if final_stat != before_stat or final_sha256 != current_sha256:
            raise AdapterPolicyError(
                "source changed before adapter success receipt"
            )
        return ExtractionResult(tuple(normalized), warnings, receipt)


class DoclingAdapter(_AdapterBase):
    adapter_id = "docling"
    module_candidates = (("docling", "docling"),)


class MinerUAdapter(_AdapterBase):
    adapter_id = "mineru"
    module_candidates = (("mineru", "mineru"), ("magic_pdf", "magic-pdf"))


class CoreAdapter(_AdapterBase):
    """Adapter for the existing local backend or strict UTF-8 text files."""

    adapter_id = "core"
    module_candidates = ()

    def __init__(self, backend=None, runner=None):
        if backend is not None and runner is not None:
            raise AdapterContractError("pass either backend or runner, not both")
        super().__init__(runner=runner)
        self.backend = backend

    def probe(self):
        return CapabilityReceipt(
            schema_version=SCHEMA_VERSION,
            adapter=self.adapter_id,
            available=True,
            module="stdlib" if self.backend is None else self.backend.__class__.__module__,
            distribution=None,
            version=None,
            runner_configured=self.runner is not None or self.backend is not None,
            policy=dict(LOCAL_ONLY_POLICY),
            reason=None,
        )

    def _run(self, request):
        if self.runner is not None:
            return super()._run(request)
        extension = os.path.splitext(request.source_path)[1].lower()
        if self.backend is None:
            if extension not in (".txt", ".md", ".markdown", ".csv", ".tsv"):
                raise AdapterUnavailableError(
                    "core has no configured backend for %s" % (extension or request.media_type)
                )
            try:
                with open(request.source_path, "r", encoding="utf-8", errors="strict") as stream:
                    text = stream.read()
            except UnicodeError as exc:
                raise AdapterExecutionError("core text source is not strict UTF-8") from exc
            records = [_text_page(request.source_file, 1, text)]
        else:
            method = getattr(self.backend, "page_texts", None)
            if not callable(method):
                raise AdapterContractError("core backend must expose page_texts(path)")
            values = method(request.source_path)
            if not isinstance(values, (list, tuple)) or any(
                not isinstance(item, str) for item in values
            ):
                raise AdapterContractError("core backend page_texts() must return strings")
            records = [
                _text_page(request.source_file, index, text)
                for index, text in enumerate(values, 1)
            ]
        discovered_page_count = len(records)
        if request.pages:
            by_page = {record["page"]: record for record in records}
            missing = [page for page in request.pages if page not in by_page]
            if missing:
                raise AdapterContractError("core backend did not enumerate requested pages %r" % missing)
            records = [by_page[page] for page in request.pages]
        return {
            "pages": records,
            "discovered_page_count": discovered_page_count,
            "warnings": [],
        }


def _text_page(source_file, page, text):
    element = {
        "kind": "text",
        "text": text,
        "ordinal": 0,
        "bbox": None,
        "method": "native",
        "confidence": 1.0,
    }
    return {
        "file": source_file,
        "page": page,
        "text": text,
        "elements": [element] if text else [],
        "embedded_assets": [],
        "review_signals": [] if text else [{
            "reason_code": "no_text",
            "detail": "the local core backend returned an empty page",
        }],
    }


def resolve_adapter(name="auto", backend=None, runner=None):
    """Resolve one explicit adapter without importing or installing vendors."""

    if not isinstance(name, str):
        raise AdapterContractError("adapter name must be text")
    normalized = name.strip().lower().replace("-", "")
    if normalized in ("auto", "core"):
        return CoreAdapter(backend=backend, runner=runner)
    if backend is not None:
        raise AdapterContractError("backend is supported only by the core adapter")
    if normalized == "docling":
        return DoclingAdapter(runner=runner)
    if normalized in ("mineru", "magicpdf"):
        return MinerUAdapter(runner=runner)
    raise AdapterContractError("unknown adapter: %s" % name)


__all__ = [
    "AdapterContractError", "AdapterError", "AdapterExecutionError",
    "AdapterPolicyError", "AdapterUnavailableError", "CapabilityReceipt",
    "CoreAdapter", "DoclingAdapter", "ExtractionReceipt", "ExtractionRequest",
    "ExtractionResult", "LOCAL_ONLY_POLICY", "MinerUAdapter", "SCHEMA_VERSION",
    "resolve_adapter", "validate_page_records",
]
