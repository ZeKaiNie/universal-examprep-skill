#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Opt-in OpenAI host adapter for isolated Study Guide explanations.

This module is deliberately outside the ordinary authoring path.  It performs one
stateless ``/v1/responses`` request per current Study Guide item, supplies no tools or
conversation identifier, imports only the strict structured result, and lets
``study_guide_explain`` create the canonical host declaration and response ledger.

The adapter never creates an API key, installs a dependency, or enables the extension.
``run`` requires an already-enabled ``answer_explanation_mode=isolated``, an exact
upload-count acknowledgement, and an explicit upload-consent flag.  API secrets are
read from ``OPENAI_API_KEY`` or the repository-local, git-ignored ``.env.local`` and
are never included in output, workspace files, receipts, or exception text.

Exit codes: 0 success; 1 provider/validation failure; 2 unsafe usage or missing consent.
"""

from __future__ import print_function

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import sys
import urllib.error
import urllib.request
from contextlib import contextmanager


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(SCRIPT_DIR)
PACKAGE_ROOT = os.path.dirname(SCRIPTS_DIR)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import study_guide_explain as explain  # noqa: E402
from ingestion import (  # noqa: E402
    UnsafePathError,
    is_link_or_reparse,
    safe_workspace_entry,
)


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_DATA_CONTROLS_URL = "https://developers.openai.com/api/docs/guides/your-data"
MAX_ENV_BYTES = 64 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 80 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{8,}")
SAFE_PROVIDER_FIELD = re.compile(r"^[A-Za-z0-9_.\[\]-]{1,128}$")
MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:/-]{1,256}$")
RESPONSE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,256}$")


class AdapterError(ValueError):
    """The local adapter contract or provider response is unsafe/invalid."""


class AdapterUsageError(AdapterError):
    """The extension was invoked without its required explicit authorization."""


def _redact(value):
    text = str(value)
    return SECRET_PATTERN.sub("[REDACTED_API_KEY]", text)


def _reject_constant(value):
    raise ValueError("non-standard JSON constant %s" % value)


def _json_loads(payload, label):
    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        return json.loads(payload, parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError) as exc:
        raise AdapterError("%s is not strict UTF-8 JSON: %s" % (label, exc)) from exc


def _json_bytes(value):
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AdapterError("provider request is not strict JSON: %s" % exc) from exc


def _valid_key(value):
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and len(value) <= 4096
        and not any(char.isspace() or ord(char) < 32 for char in value)
    )


def _dotenv_key(path):
    if not os.path.isfile(path) or os.path.islink(path):
        return None
    if os.path.getsize(path) > MAX_ENV_BYTES:
        raise AdapterError(".env.local exceeds its safety limit")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise AdapterError("cannot read .env.local safely: %s" % exc) from exc
    found = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() != "OPENAI_API_KEY":
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        found.append(value)
    if len(found) > 1:
        raise AdapterError(".env.local contains duplicate OPENAI_API_KEY entries")
    return found[0] if found else None


def load_api_key():
    """Return ``(secret, source)`` without ever printing the secret."""

    environment = os.environ.get("OPENAI_API_KEY")
    if environment is not None:
        if not _valid_key(environment):
            raise AdapterError("OPENAI_API_KEY is present but malformed")
        return environment, "environment"
    local = _dotenv_key(os.path.join(PACKAGE_ROOT, ".env.local"))
    if local is not None:
        if not _valid_key(local):
            raise AdapterError(".env.local OPENAI_API_KEY is malformed")
        return local, "repository_local_ignored_file"
    return None, "missing"


def _credential_binding_id(api_key):
    """Return a non-secret binding for the exact effective credential."""

    if api_key is None:
        return "missing"
    return "openai_credential_" + hashlib.sha256(
        b"universal-examprep/openai-study-guide/credential/v1\x00"
        + api_key.encode("utf-8")
    ).hexdigest()


def _workspace(value):
    return explain.author._workspace(value)


def _extension_status(workspace, chapter=None):
    try:
        mode = explain.author._workspace_answer_explanation_mode(workspace)
        result = {
            "answer_explanation_mode": mode,
            "extension_enabled": mode == "isolated",
        }
        if chapter is not None and mode == "isolated":
            result["explanation_status"] = explain.get_status(workspace, chapter)
        return result
    except explain.exam_start.FullProcessingRequired as exc:
        raise AdapterUsageError(str(exc)) from exc
    except (OSError, ValueError, TypeError, explain.author.AuthoringError) as exc:
        raise AdapterError("cannot resolve answer_explanation_mode: %s" % exc) from exc


def _validated_model(model):
    if not isinstance(model, str) or not MODEL_ID_PATTERN.fullmatch(model):
        raise AdapterUsageError(
            "--model must be a 1-256 character provider model ID using only "
            "letters, digits, dot, underscore, colon, slash, or hyphen"
        )
    return model


def probe(workspace=None):
    key, source = load_api_key()
    result = {
        "ok": True,
        "provider": "openai",
        "endpoint": OPENAI_RESPONSES_URL,
        "network_probe_performed": False,
        "api_key_available": key is not None,
        "api_key_source": source,
        "installs_dependencies": False,
        "creates_api_keys": False,
        "uploads_on_probe": False,
        "ordinary_mode_unchanged": True,
    }
    if workspace is not None:
        workspace = _workspace(workspace)
        result.update(_extension_status(workspace))
    return result


def _current_pending(workspace, chapter):
    status = explain.get_status(workspace, chapter)
    if status.get("status") == "disabled":
        raise AdapterUsageError(
            "isolated explanations are disabled; obtain consent and set "
            "--answer-explanation-mode isolated first"
        )
    if status.get("status") in ("not_prepared", "stale"):
        raise AdapterUsageError(
            "isolated requests are %s; run study_guide_explain.py prepare first"
            % status.get("status")
        )
    requests = explain._assert_requests_current(workspace, chapter)
    _rows, active = explain._load_ledger(workspace, chapter)
    current = explain._current_active(requests, active)
    pending = [row for row in requests if row["request_id"] not in current]
    return status, requests, pending


def _regular_asset(workspace, binding):
    try:
        path = explain.author._safe_workspace_path(
            workspace, binding["path"], "OpenAI explanation attachment"
        )
    except (explain.author.AuthoringError, ValueError) as exc:
        raise AdapterError(str(exc)) from exc
    if os.path.islink(path) or not os.path.isfile(path):
        raise AdapterError("attachment is not a regular file: %s" % binding["path"])
    size = os.path.getsize(path)
    if size <= 0 or size > MAX_IMAGE_BYTES:
        raise AdapterError(
            "attachment %s is empty or exceeds %d bytes"
            % (binding["path"], MAX_IMAGE_BYTES)
        )
    digest = hashlib.sha256()
    chunks = []
    try:
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                chunks.append(chunk)
    except OSError as exc:
        raise AdapterError("cannot read attachment %s: %s" % (binding["path"], exc)) from exc
    if digest.hexdigest() != binding["sha256"]:
        raise AdapterError("attachment revision drifted: %s" % binding["path"])
    payload = b"".join(chunks)
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif payload.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif payload.startswith((b"GIF87a", b"GIF89a")):
        mime = "image/gif"
    elif len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        raise AdapterError(
            "attachment format is unsupported by the OpenAI image-input route: %s"
            % binding["path"]
        )
    return payload, mime


def _attachment_inventory(workspace, requests):
    count = 0
    total = 0
    unique_count = 0
    unique_bytes = 0
    by_side = {"question": 0, "answer": 0}
    seen = set()
    for request in requests:
        for binding in request.get("asset_bindings") or ():
            payload, _mime = _regular_asset(workspace, binding)
            count += 1
            total += len(payload)
            by_side[binding["side"]] += 1
            identity = (binding["path"], binding["sha256"])
            if identity in seen:
                continue
            seen.add(identity)
            unique_count += 1
            unique_bytes += len(payload)
    return {
        "attachment_count": count,
        "attachment_bytes": total,
        "unique_attachment_count": unique_count,
        "unique_attachment_bytes": unique_bytes,
        "by_side": by_side,
    }


def _upload_scope(workspace, selected):
    scope = []
    for row in selected:
        attachments = []
        for binding in row.get("asset_bindings") or ():
            payload, _mime = _regular_asset(workspace, binding)
            attachments.append({
                "asset_id": binding["asset_id"],
                "side": binding["side"],
                "path": binding["path"],
                "sha256": binding["sha256"],
                "bytes": len(payload),
            })
        model_input = _json_bytes(row["model_input"])
        instruction_bytes = row["instruction"]["text"].encode("utf-8")
        output_schema = _json_bytes(row["output_schema"])
        scope.append({
            "item_id": row["item_id"],
            "request_id": row["request_id"],
            "request_sha256": row["request_sha256"],
            "instruction_sha256": row["instruction"]["sha256"],
            "instruction_utf8_bytes": len(instruction_bytes),
            "model_input_sha256": hashlib.sha256(model_input).hexdigest(),
            "model_input_utf8_bytes": len(model_input),
            "output_schema_sha256": hashlib.sha256(output_schema).hexdigest(),
            "output_schema_utf8_bytes": len(output_schema),
            "attachments": attachments,
        })
    return scope


def _plan_basis(chapter, model, detail, max_output_tokens, timeout, selected,
                credential_binding_id, api_key_source):
    """Return the exact, secret-free authorization basis for one run.

    A call-count acknowledgement alone is unsafe: a changed pending set, model,
    image detail, output cap, or attachment revision could preserve the same count
    while changing both uploaded content and cost.  Bind every such field here and
    require the resulting ID again at ``run``.
    """

    return {
        "schema_version": 1,
        "provider": "openai",
        "endpoint": OPENAI_RESPONSES_URL,
        "chapter": chapter,
        "model": model,
        "image_detail": detail,
        "store": False,
        "max_output_tokens": max_output_tokens,
        "timeout_seconds": timeout,
        "credential_binding_id": credential_binding_id,
        "api_key_source": api_key_source,
        "automatic_retries": 0,
        "maximum_http_post_attempts": len(selected),
        "selected": [
            {
                "item_id": row["item_id"],
                "request_id": row["request_id"],
                "request_sha256": row["request_sha256"],
                "asset_bindings": row.get("asset_bindings") or [],
            }
            for row in selected
        ],
    }


def _plan_id(basis):
    return "openai_study_guide_plan_" + hashlib.sha256(
        _json_bytes(basis)
    ).hexdigest()


def plan(workspace, chapter, model, limit=0, detail="high",
         max_output_tokens=12000, timeout=180):
    workspace = _workspace(workspace)
    model = _validated_model(model)
    key, source = load_api_key()
    extension = _extension_status(workspace, chapter)
    if not extension["extension_enabled"]:
        raise AdapterUsageError(
            "the isolated extension is off; the ordinary Guide remains available"
        )
    status, requests, pending = _current_pending(workspace, chapter)
    selected = pending[:limit] if limit else pending
    inventory = _attachment_inventory(workspace, selected)
    credential_binding_id = _credential_binding_id(key)
    basis = _plan_basis(
        chapter, model, detail, max_output_tokens, timeout, selected,
        credential_binding_id, source,
    )
    upload_scope = _upload_scope(workspace, selected)
    return {
        "ok": True,
        "provider": "openai",
        "endpoint": OPENAI_RESPONSES_URL,
        "model": model,
        "image_detail": detail,
        "store": False,
        "max_output_tokens": max_output_tokens,
        "timeout_seconds": timeout,
        "automatic_retries": 0,
        "maximum_http_post_attempts": len(selected),
        "chapter": chapter,
        "request_count": len(requests),
        "already_complete_count": len(requests) - len(pending),
        "pending_count": len(pending),
        "selected_call_count": len(selected),
        "selected_item_ids": [row["item_id"] for row in selected],
        "selected_request_ids": [row["request_id"] for row in selected],
        "plan_id": _plan_id(basis),
        "api_key_available": key is not None,
        "api_key_source": source,
        "credential_binding_id": credential_binding_id,
        "data_controls": OPENAI_DATA_CONTROLS_URL,
        "host_receipt_is_declaration": True,
        "tools_supplied": False,
        "conversation_state_supplied": False,
        "status_before": status.get("status"),
        "selected_upload_scope": upload_scope,
        "inspect_request_command": (
            "python scripts/study_guide_explain.py --workspace <ws> show "
            "--chapter %d --request-id <request_id> --json" % chapter
        ),
        **inventory
    }


def _provider_payload(workspace, request, model, detail, max_output_tokens):
    user_content = [{
        "type": "input_text",
        "text": "MODEL_INPUT_JSON\n" + json.dumps(
            request["model_input"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
    }]
    total = 0
    for binding in request.get("asset_bindings") or ():
        raw, mime = _regular_asset(workspace, binding)
        total += len(raw)
        if total > MAX_TOTAL_IMAGE_BYTES:
            raise AdapterError("one request exceeds the attachment safety limit")
        user_content.append({
            "type": "input_text",
            "text": "%s_IMAGE %s" % (binding["side"].upper(), binding["asset_id"]),
        })
        user_content.append({
            "type": "input_image",
            "image_url": "data:%s;base64,%s" % (
                mime, base64.b64encode(raw).decode("ascii")
            ),
            "detail": detail,
        })
    return {
        "model": model,
        "store": False,
        "max_output_tokens": max_output_tokens,
        "input": [
            {"role": "system", "content": request["instruction"]["text"]},
            {"role": "user", "content": user_content},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "answer_explanation",
                "strict": True,
                "schema": request["output_schema"],
            }
        },
    }


def _provider_error(payload, status):
    """Return only allow-listed machine metadata, never Provider prose."""

    details = []
    try:
        value = _json_loads(payload, "OpenAI error response")
        error = value.get("error") or {}
        for label in ("type", "code", "param"):
            field = error.get(label)
            if isinstance(field, str) and SAFE_PROVIDER_FIELD.fullmatch(field):
                details.append("%s=%s" % (label, field))
    except AdapterError:
        pass
    suffix = " " + " ".join(details) if details else ""
    return "HTTP %s%s" % (status, suffix)


def _call_openai(api_key, payload, timeout):
    """Make exactly one HTTP POST attempt.

    Automatic retries are deliberately disabled because the Responses API call may
    have reached the Provider before a timeout/disconnect.  A retry without an
    official idempotency contract could duplicate processing or billing.  The user
    can inspect a fresh plan and explicitly resume only still-pending items.
    """

    body = _json_bytes(payload)
    request = urllib.request.Request(
        OPENAI_RESPONSES_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "User-Agent": "universal-examprep-skill/openai-study-guide",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise AdapterError("OpenAI response exceeds its safety limit")
            return _json_loads(raw, "OpenAI response")
    except urllib.error.HTTPError as exc:
        raw = exc.read(64 * 1024)
        raise AdapterError(
            "OpenAI request failed: %s" % _provider_error(raw, exc.code)
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AdapterError(
            "OpenAI request failed before a response was accepted; delivery may "
            "be ambiguous, so no automatic retry was attempted (%s)"
            % _redact(type(exc).__name__)
        ) from exc


def _model_result(response):
    if not isinstance(response, dict):
        raise AdapterError("OpenAI response must be an object")
    status = response.get("status")
    if status == "incomplete":
        reason = (response.get("incomplete_details") or {}).get("reason") or "unknown"
        safe_reason = reason if reason in ("max_output_tokens", "content_filter") else "unknown"
        raise AdapterError("OpenAI response is incomplete: %s" % safe_reason)
    if status != "completed":
        raise AdapterError("OpenAI response status must be completed")
    texts = []
    refusals = []
    for output in response.get("output") or ():
        if not isinstance(output, dict) or output.get("type") != "message":
            continue
        for content in output.get("content") or ():
            if not isinstance(content, dict):
                continue
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                texts.append(content["text"])
            elif content.get("type") == "refusal":
                refusals.append(str(content.get("refusal") or "provider refusal"))
    if refusals:
        raise AdapterError("OpenAI refused the request; refusal text was not logged")
    if len(texts) != 1:
        raise AdapterError("OpenAI response must contain exactly one structured output_text")
    value = _json_loads(texts[0], "OpenAI structured output")
    if not isinstance(value, dict):
        raise AdapterError("OpenAI structured output must be an object")
    return value


@contextmanager
def _adapter_run_lock(workspace, chapter):
    """Prevent concurrent replay of one chapter plan without blocking state writes."""

    relative = ".ingest/openai-study-guide-ch%02d.lock" % chapter
    try:
        lock_path = str(safe_workspace_entry(workspace, relative))
    except (UnsafePathError, OSError, ValueError) as exc:
        raise AdapterUsageError("the OpenAI adapter run lock path is unsafe") from exc
    parent = os.path.dirname(lock_path)
    if is_link_or_reparse(parent) or not os.path.isdir(parent):
        raise AdapterUsageError("the ingestion directory is unavailable for a run lock")
    if os.path.lexists(lock_path) and (
        is_link_or_reparse(lock_path) or not os.path.isfile(lock_path)
    ):
        raise AdapterUsageError("the OpenAI adapter run lock path is unsafe")
    flags = (
        os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    fd = None
    try:
        fd = os.open(lock_path, flags, 0o600)
        # Recheck every parent and the opened leaf before writing a sentinel.
        safe_workspace_entry(workspace, relative)
        opened = os.fstat(fd)
        live = os.lstat(lock_path)
        if (
            is_link_or_reparse(lock_path)
            or not stat.S_ISREG(live.st_mode)
            or (opened.st_dev, opened.st_ino) != (live.st_dev, live.st_ino)
        ):
            raise AdapterUsageError("the OpenAI adapter run lock changed during open")
        stream = os.fdopen(fd, "r+b")
        fd = None
    except OSError as exc:
        if fd is not None:
            os.close(fd)
        raise AdapterUsageError("cannot open the OpenAI adapter run lock") from exc
    except (UnsafePathError, AdapterUsageError):
        if fd is not None:
            os.close(fd)
        raise
    locked = False
    try:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except (OSError, IOError) as exc:
            raise AdapterUsageError(
                "another OpenAI Study Guide run is already active for this chapter"
            ) from exc
        yield
    finally:
        if locked:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            except (OSError, IOError):
                pass
        stream.close()


def _assert_run_step_current(workspace, chapter, selected, index,
                             credential_binding_id, api_key_source):
    """Recheck revocable state immediately before each Provider POST.

    Do not hold the workspace publication lock across a network call.  Instead,
    rehydrate the mode, request set, ledger, exact remaining order, and credential
    before every item.  This prevents a long run from continuing after the user
    switches back to ordinary mode or another process changes/completes a request.
    """

    current_key, current_source = load_api_key()
    if (
        current_key is None
        or current_source != api_key_source
        or _credential_binding_id(current_key) != credential_binding_id
    ):
        raise AdapterUsageError(
            "the effective OpenAI credential changed after planning; stop and "
            "review a new exact plan before any further upload"
        )
    extension = _extension_status(workspace, chapter)
    if not extension["extension_enabled"]:
        raise AdapterUsageError(
            "isolated mode was revoked during the run; no further upload was attempted"
        )
    _status, _requests, live_pending = _current_pending(workspace, chapter)
    expected = selected[index:]
    live_prefix = live_pending[:len(expected)]
    expected_identity = [
        (row["request_id"], row["request_sha256"]) for row in expected
    ]
    live_identity = [
        (row["request_id"], row["request_sha256"]) for row in live_prefix
    ]
    if live_identity != expected_identity:
        raise AdapterUsageError(
            "the pending request set/order changed during the run; no further "
            "upload was attempted. Create and review a new plan"
        )
    return current_key


def _run_calls_locked(workspace, chapter, model, consent_upload, confirm_call_count,
                      confirm_plan_id, limit=0, detail="high",
                      max_output_tokens=12000, timeout=180, finalize=True):
    if consent_upload is not True:
        raise AdapterUsageError(
            "run requires --consent-upload after provider/upload/cost/retention/privacy disclosure"
        )
    workspace = _workspace(workspace)
    model = _validated_model(model)
    key, key_source = load_api_key()
    if key is None:
        raise AdapterUsageError(
            "no OpenAI API key is available; keep the ordinary route or configure one safely"
        )
    extension = _extension_status(workspace, chapter)
    if not extension["extension_enabled"]:
        raise AdapterUsageError(
            "answer_explanation_mode is ordinary; explicit isolated opt-in is required"
        )
    _status, requests, pending = _current_pending(workspace, chapter)
    selected = pending[:limit] if limit else pending
    if confirm_call_count != len(selected):
        raise AdapterUsageError(
            "--confirm-call-count must exactly equal the selected pending count (%d)"
            % len(selected)
        )
    inventory = _attachment_inventory(workspace, selected)
    basis = _plan_basis(
        chapter, model, detail, max_output_tokens, timeout, selected,
        _credential_binding_id(key), key_source,
    )
    current_plan_id = _plan_id(basis)
    if confirm_plan_id != current_plan_id:
        raise AdapterUsageError(
            "--confirm-plan-id does not match the current exact request/model/asset plan; "
            "run plan again and review the new scope before upload"
        )
    credential_binding_id = _credential_binding_id(key)
    imported = []
    for index, request in enumerate(selected):
        key = _assert_run_step_current(
            workspace, chapter, selected, index,
            credential_binding_id, key_source,
        )
        provider_payload = _provider_payload(
            workspace, request, model, detail, max_output_tokens
        )
        # Asset encoding can take time. Recheck the revocable mode, exact pending
        # request order, and credential again immediately before the only POST.
        key = _assert_run_step_current(
            workspace, chapter, selected, index,
            credential_binding_id, key_source,
        )
        response = _call_openai(key, provider_payload, timeout)
        result = _model_result(response)
        response_id = response.get("id")
        if (
            not isinstance(response_id, str)
            or not RESPONSE_ID_PATTERN.fullmatch(response_id)
        ):
            raise AdapterError(
                "OpenAI response is missing a valid provider response id"
            )
        invocation_id = "openai-responses:" + response_id
        host_receipt = explain.make_host_receipt(
            workspace,
            chapter,
            request["request_id"],
            invocation_id,
            "stateless_api",
            provider="openai",
            model=model,
        )
        imported_result = explain.import_result(
            workspace, chapter, request["request_id"], result, host_receipt
        )
        imported.append({
            "item_id": request["item_id"],
            "request_id": request["request_id"],
            "provider_response_id": response_id,
            "status": imported_result["status"],
        })
    after = explain.get_status(workspace, chapter)
    finalized = None
    if finalize and after.get("pending_item_ids") == [] and after.get("status") != "finalized":
        finalized = explain.finalize_receipt(workspace, chapter)
        after = explain.get_status(workspace, chapter)
    return {
        "ok": True,
        "provider": "openai",
        "model": model,
        "store": False,
        "tools_supplied": False,
        "conversation_state_supplied": False,
        "chapter": chapter,
        "api_key_source": key_source,
        "credential_binding_id": credential_binding_id,
        "selected_call_count": len(selected),
        "plan_id": current_plan_id,
        "maximum_http_post_attempts": len(selected),
        "automatic_retries": 0,
        "imported": imported,
        "attachment_count": inventory["attachment_count"],
        "attachment_bytes": inventory["attachment_bytes"],
        "unique_attachment_count": inventory["unique_attachment_count"],
        "unique_attachment_bytes": inventory["unique_attachment_bytes"],
        "status_after": after.get("status"),
        "pending_after": len(after.get("pending_item_ids") or ()),
        "receipt_id": (
            finalized.get("receipt_id") if finalized is not None
            else after.get("receipt_id")
        ),
        "host_receipt_is_declaration": True,
    }


def run_calls(workspace, chapter, model, consent_upload, confirm_call_count,
              confirm_plan_id, limit=0, detail="high", max_output_tokens=12000,
              timeout=180, finalize=True):
    if consent_upload is not True:
        raise AdapterUsageError(
            "run requires --consent-upload after provider/upload/cost/retention/privacy disclosure"
        )
    workspace = _workspace(workspace)
    with _adapter_run_lock(workspace, chapter):
        return _run_calls_locked(
            workspace, chapter, model, consent_upload, confirm_call_count,
            confirm_plan_id, limit=limit, detail=detail,
            max_output_tokens=max_output_tokens, timeout=timeout,
            finalize=finalize,
        )


def _print(value, as_json):
    if as_json:
        print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
        return
    for key in sorted(value):
        if key == "imported":
            print("imported: %d" % len(value[key]))
        else:
            print("%s: %s" % (key, value[key]))


def build_parser():
    parser = argparse.ArgumentParser(
        description="Opt-in OpenAI Responses host adapter for isolated Study Guide explanations."
    )
    parser.add_argument("--workspace")
    parser.add_argument("--json", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("probe", help="local capability/key check; never contacts OpenAI")

    planner = commands.add_parser("plan", help="report exact pending upload/call scope")
    planner.add_argument("--chapter", required=True, type=int)
    planner.add_argument("--model", required=True)
    planner.add_argument("--limit", type=int, default=0)
    planner.add_argument("--detail", choices=("low", "high", "original", "auto"), default="high")
    planner.add_argument("--max-output-tokens", type=int, default=12000)
    planner.add_argument("--timeout", type=int, default=180)

    runner = commands.add_parser("run", help="perform and import one stateless call per item")
    runner.add_argument("--chapter", required=True, type=int)
    runner.add_argument("--model", required=True)
    runner.add_argument("--limit", type=int, default=0)
    runner.add_argument("--detail", choices=("low", "high", "original", "auto"), default="high")
    runner.add_argument("--max-output-tokens", type=int, default=12000)
    runner.add_argument("--timeout", type=int, default=180)
    runner.add_argument("--consent-upload", action="store_true")
    runner.add_argument("--confirm-call-count", required=True, type=int)
    runner.add_argument("--confirm-plan-id", required=True)
    runner.add_argument("--no-finalize", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "probe":
            result = probe(args.workspace)
        else:
            if not args.workspace:
                raise AdapterUsageError("--workspace is required for %s" % args.command)
            if args.chapter < 1:
                raise AdapterUsageError("--chapter must be >= 1")
            if args.limit < 0:
                raise AdapterUsageError("--limit must be >= 0")
            if args.command == "plan":
                if args.max_output_tokens < 256 or args.max_output_tokens > 128000:
                    raise AdapterUsageError("--max-output-tokens must be between 256 and 128000")
                if args.timeout < 10 or args.timeout > 600:
                    raise AdapterUsageError("--timeout must be between 10 and 600 seconds")
                result = plan(
                    args.workspace, args.chapter, args.model,
                    limit=args.limit, detail=args.detail,
                    max_output_tokens=args.max_output_tokens,
                    timeout=args.timeout,
                )
            else:
                if args.max_output_tokens < 256 or args.max_output_tokens > 128000:
                    raise AdapterUsageError("--max-output-tokens must be between 256 and 128000")
                if args.timeout < 10 or args.timeout > 600:
                    raise AdapterUsageError("--timeout must be between 10 and 600 seconds")
                result = run_calls(
                    args.workspace,
                    args.chapter,
                    args.model,
                    args.consent_upload,
                    args.confirm_call_count,
                    args.confirm_plan_id,
                    limit=args.limit,
                    detail=args.detail,
                    max_output_tokens=args.max_output_tokens,
                    timeout=args.timeout,
                    finalize=not args.no_finalize,
                )
        _print(result, args.json)
        return 0
    except AdapterUsageError as exc:
        sys.stderr.write("openai_study_guide: %s\n" % _redact(exc))
        return 2
    except explain.exam_start.FullProcessingRequired as exc:
        sys.stderr.write("openai_study_guide: %s\n" % _redact(exc))
        return 2
    except (
        AdapterError,
        explain.ExplainError,
        explain.author.AuthoringError,
        OSError,
    ) as exc:
        sys.stderr.write("openai_study_guide: %s\n" % _redact(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
