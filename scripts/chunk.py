#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R-slice (v4-P3) — deterministic cleaning + chunking of course material, pure stdlib.

Why this exists: real ingested wikis are often DEGENERATE — the PSYC 110 chapters are single
20K–56K-char lines with scraper CSS residue at the head and zero headings, so "split on ##"
never fires. This module owns the full slicing path:

  clean_text()        strip CSS/HTML scraper residue + collapse whitespace (conservative:
                      only patterns that cannot be course prose)
  chunk_text()        headings when they exist; otherwise sentence-group rebuilding with
                      discourse-marker-preferred boundaries. Every chunk carries its
                      [start, end) offsets INTO THE CLEANED TEXT plus the cleaned text is
                      returned, so any chunk can be located back verbatim (acceptance:
                      a verbatim gold span lands inside exactly one chunk).

Ingest imports chunk_text(); retrieve.build_index() consumes the output shape directly.
No LLM, no network, no third-party deps.
"""
import hashlib
import os
import re

try:
    import strict_json
except ImportError:  # imported as ``scripts.chunk``
    from scripts import strict_json

try:
    from asset_policy import (
        audit_asset_policy,
        has_tainted_official_asset,
        iter_asset_declarations,
    )
except ImportError:  # imported as ``scripts.chunk``
    from scripts.asset_policy import (
        audit_asset_policy,
        has_tainted_official_asset,
        iter_asset_declarations,
    )

try:
    from ingestion.identifiers import (
        canonical_json,
        is_link_or_reparse,
        safe_workspace_entry,
    )
except ImportError:  # imported as ``scripts.chunk``
    from scripts.ingestion.identifiers import (
        canonical_json,
        is_link_or_reparse,
        safe_workspace_entry,
    )

TARGET = 1200          # aim chars per chunk
HARD_MAX = 2000        # plan acceptance: no chunk exceeds this
MIN_TAIL = 300         # a trailing chunk smaller than this merges backward

# --- cleaning: ONLY structures that cannot be legitimate course prose ---
# a CSS rule = selector + {prop: value; ...} — REQUIRE the css-declaration shape inside the
# braces (letters/hyphens, colon, value). Math braces ({x | x > 0}, {a, b, c}) have no
# `prop: value` pair and never match; python dicts ({'a': 1}) have quoted keys and never match.
_CSS_RULE_RE = re.compile(
    r"(?:^|\s)[\w.#][\w.#\-, >]{0,60}\{\s*(?:[a-zA-Z-]+\s*:\s*[^{};:]{1,80};?\s*){1,8}\}")
_HTML_TAG_RE = re.compile(r"</?(?:div|span|p|br|img|table|tr|td|th|ul|ol|li|a|b|i|em|strong)\b[^>]*>",
                          re.I)
_WS_RE = re.compile(r"[ \t　]+")

# sentence enders (en + zh) — offsets preserved because we split, never rewrite
_SENT_RE = re.compile(r"[^.!?。！？]*[.!?。！？]+[\"'”』」)]*\s*|[^.!?。！？]+$")
# lecture-transcript discourse markers: boundaries PREFER to start a new chunk here
_MARKER_RE = re.compile(
    r"^(?:okay|ok|so|now|all right|alright|next|today|let's|lets|first|second|finally|"
    r"好|那么|接下来|下面|现在|首先|其次|最后|我们来|让我们)\b", re.I)

_HEADING_RE = re.compile(r"(?m)^#{1,4}\s+\S.*$")


def clean_text(text):
    """Deterministic conservative cleanup. Returns cleaned text; chunk offsets refer to THIS."""
    t = text or ""
    t = _HTML_TAG_RE.sub(" ", t)
    # CSS rules only in the first 2KB (scraper residue lives at the head; course text later may
    # legitimately contain braces — e.g. set notation {x | x > 0} — so never global-strip)
    head, rest = t[:2048], t[2048:]
    head = _CSS_RULE_RE.sub(" ", head)
    t = head + rest
    t = _WS_RE.sub(" ", t)
    t = re.sub(r" ?\n ?", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _sentences(text):
    """[(start, end)] sentence spans covering the text (verbatim slices, nothing rewritten)."""
    out = []
    pos = 0
    for m in _SENT_RE.finditer(text):
        s = m.group(0)
        if s.strip():
            out.append((m.start(), m.end()))
        pos = m.end()
    if not out and text.strip():
        out = [(0, len(text))]
    return out


def _slice_by_headings(text):
    """[(title, start, end)] using markdown headings as boundaries; None if no usable headings."""
    marks = [(m.start(), m.group(0).strip()) for m in _HEADING_RE.finditer(text)]
    if len(marks) < 2:
        return None
    spans = []
    for i, (pos, raw) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        title = re.sub(r"^#{1,4}\s+", "", raw.splitlines()[0])[:80]
        spans.append((title, pos, end))
    # leading preamble before the first heading stays a chunk of its own when non-trivial
    if marks[0][0] > MIN_TAIL:
        spans.insert(0, ("", 0, marks[0][0]))
    return spans


def _pack_sentences(text, start, end, target, hard_max):
    """Greedy sentence packing inside [start, end): close a chunk near `target`, never exceed
    `hard_max`; prefer closing right BEFORE a discourse marker (new topic starts a new chunk)."""
    sents = [(s + start, e + start) for s, e in _sentences(text[start:end])]
    chunks = []
    cur_s = None
    cur_e = None
    for s, e in sents:
        if cur_s is None:
            cur_s, cur_e = s, e
            continue
        cand = e - cur_s
        marker = _MARKER_RE.match(text[s:min(e, s + 24)].lstrip())
        if cand > hard_max or (cand > target and marker) or (cand > target * 1.5):
            chunks.append((cur_s, cur_e))
            cur_s, cur_e = s, e
        else:
            cur_e = e
    if cur_s is not None:
        if chunks and (cur_e - cur_s) < MIN_TAIL and (cur_e - chunks[-1][0]) <= hard_max:
            chunks[-1] = (chunks[-1][0], cur_e)          # merge tiny tail backward
        else:
            chunks.append((cur_s, cur_e))
    # oversize single sentences (degenerate no-punctuation runs): hard-split at hard_max
    final = []
    for s, e in chunks:
        while e - s > hard_max:
            final.append((s, s + hard_max))
            s += hard_max
        final.append((s, e))
    return final


def chunk_text(raw_text, target=TARGET, hard_max=HARD_MAX):
    """→ (cleaned_text, [{"title","start","end","text"}]). Offsets index cleaned_text verbatim."""
    text = clean_text(raw_text)
    if not text:
        return text, []
    out = []
    spans = _slice_by_headings(text)
    if spans:
        for title, s, e in spans:
            if e - s <= hard_max:
                out.append({"title": title, "start": s, "end": e})
            else:
                for cs, ce in _pack_sentences(text, s, e, target, hard_max):
                    out.append({"title": title, "start": cs, "end": ce})
    else:
        for cs, ce in _pack_sentences(text, 0, len(text), target, hard_max):
            out.append({"title": "", "start": cs, "end": ce})
    for c in out:
        c["text"] = text[c["start"]:c["end"]]
    out = [c for c in out if c["text"].strip()]
    return text, out


_ATOMIC_UNIT_KINDS = frozenset(
    ("table", "formula", "question", "code", "diagram", "figure")
)
_RETRIEVABLE_UNIT_KINDS = frozenset(
    ("title", "heading", "text", "list", "table", "formula", "caption",
     "code", "question", "figure", "diagram", "other")
)


class _VerifiedAssetPolicy(frozenset):
    """Taint keys derived from a complete, validated three-layer snapshot.

    A plain ``set`` is deliberately not equivalent to this capability: it can
    describe extra taint, but it cannot prove that the caller inspected the
    quiz bank, teaching layer, and content units together.
    """

    def __new__(
            cls, values, workspace=None, unit_digests=None,
            allowed_aliases=None):
        instance = super(_VerifiedAssetPolicy, cls).__new__(cls, values)
        instance.workspace = workspace
        instance.unit_digests = dict(unit_digests or {})
        instance.allowed_aliases = {
            key: frozenset(aliases)
            for key, aliases in (allowed_aliases or {}).items()
        }
        return instance


def _policy_unit_dict(value):
    if isinstance(value, dict):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    raise TypeError("content units must be dictionaries or expose to_dict()")


def _policy_unit_digest(value):
    """Bind the full live unit while allowing retrieval-only occurrence aliases."""

    row = _policy_unit_dict(value)
    row.pop("retrieval_occurrence_unit_ids", None)
    return hashlib.sha256(canonical_json(row).encode("utf-8")).hexdigest()


def _canonical_group_aliases(canonical_groups, unit_rows, unit_digests):
    """Bind fold aliases to typed canonical-group member revision refs."""

    rows_by_id = {row["unit_id"]: row for row in unit_rows}
    aliases = {unit_id: frozenset((unit_id,)) for unit_id in rows_by_id}
    grouped_members = set()
    for position, group in enumerate(canonical_groups or ()):
        if isinstance(group, dict):
            display = group.get("display_unit_id")
            refs = group.get("member_refs")
        else:
            display = getattr(group, "display_unit_id", None)
            refs = getattr(group, "member_refs", None)
        if not isinstance(refs, (list, tuple)) or len(refs) < 2:
            raise ValueError("canonical_groups[%d] has invalid member_refs" % position)
        members = []
        for ref in refs:
            if isinstance(ref, dict):
                unit_id = ref.get("unit_id")
                source_id = ref.get("source_id")
                source_sha256 = ref.get("source_sha256")
                unit_sha256 = ref.get("unit_sha256")
            else:
                unit_id = getattr(ref, "unit_id", None)
                source_id = getattr(ref, "source_id", None)
                source_sha256 = getattr(ref, "source_sha256", None)
                unit_sha256 = getattr(ref, "unit_sha256", None)
            live = rows_by_id.get(unit_id)
            if (live is None
                    or live.get("source_id") != source_id
                    or live.get("source_sha256") != source_sha256
                    or unit_digests.get(unit_id) != unit_sha256):
                raise ValueError(
                    "canonical_groups[%d] member %r does not bind the live unit revision"
                    % (position, unit_id)
                )
            members.append(unit_id)
        member_set = frozenset(members)
        if (len(member_set) != len(members) or display not in member_set
                or grouped_members & member_set):
            raise ValueError(
                "canonical_groups[%d] has duplicate, overlapping, or invalid display members"
                % position
            )
        grouped_members.update(member_set)
        aliases[display] = member_set
    return aliases


def _verified_asset_policy_from_layers(
        *, quiz_rows=(), teaching_rows=(), content_units=(),
        canonical_groups=(), workspace=None):
    """Internal in-memory boundary for producers that own all three layers."""

    quiz_rows = list(quiz_rows or ())
    teaching_rows = list(teaching_rows or ())
    content_units = list(content_units or ())
    audit = audit_asset_policy(
        quiz_rows=quiz_rows,
        teaching_rows=teaching_rows,
        content_units=content_units,
        workspace=workspace,
    )
    problems = audit["invalid_declarations"] + audit["conflicts"]
    if problems:
        raise ValueError("complete asset policy failed: %s" % "; ".join(problems))
    unit_digests = {}
    for position, value in enumerate(content_units):
        row = _policy_unit_dict(value)
        unit_id = row.get("unit_id")
        if not isinstance(unit_id, str) or not unit_id or unit_id in unit_digests:
            raise ValueError(
                "complete asset policy content_units[%d] has a missing or duplicate unit_id"
                % position
            )
        unit_digests[unit_id] = _policy_unit_digest(row)
    allowed_aliases = _canonical_group_aliases(
        canonical_groups, [_policy_unit_dict(value) for value in content_units], unit_digests
    )
    return _VerifiedAssetPolicy(
        audit["tainted_keys"],
        os.path.normcase(os.path.realpath(str(workspace))) if workspace is not None else None,
        unit_digests,
        allowed_aliases,
    )


def _read_workspace_json_array(workspace, relative, *, optional=False):
    path = safe_workspace_entry(workspace, relative)
    raw_path = str(path)
    if not os.path.exists(raw_path):
        if optional:
            return []
        raise ValueError("%s is missing" % relative)
    if is_link_or_reparse(raw_path) or not os.path.isfile(raw_path):
        raise ValueError("%s is not a safe regular file" % relative)
    try:
        with open(raw_path, "r", encoding="utf-8") as stream:
            value = strict_json.load(stream)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("%s cannot be read safely: %s" % (relative, exc))
    if not isinstance(value, list):
        raise ValueError("%s must be an array" % relative)
    return value


def _read_workspace_content_units(workspace):
    relative = ".ingest/content_units.jsonl"
    path = safe_workspace_entry(workspace, relative)
    raw_path = str(path)
    if (not os.path.isfile(raw_path) or is_link_or_reparse(raw_path)):
        raise ValueError("%s is missing or is not a safe regular file" % relative)
    rows = []
    try:
        with open(raw_path, "r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                value = strict_json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("line %d must be an object" % line_number)
                rows.append(value)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("%s cannot be read safely: %s" % (relative, exc))
    return rows


def workspace_asset_policy(workspace):
    """Live-load and validate quiz + teaching + content-unit asset policy.

    The teaching layer is optional for legacy workspaces and then contributes
    an explicit empty layer.  Quiz and structured content-unit files are
    required because a caller cannot claim workspace-complete policy without
    seeing them.
    """

    root = os.path.abspath(str(workspace))
    quiz = _read_workspace_json_array(root, "references/quiz_bank.json")
    teaching = _read_workspace_json_array(
        root, "references/teaching_examples.json", optional=True
    )
    units = _read_workspace_content_units(root)
    canonical_groups = ()
    canonical_path = safe_workspace_entry(root, ".ingest/canonical_groups.jsonl")
    if os.path.exists(str(canonical_path)):
        if is_link_or_reparse(str(canonical_path)) or not os.path.isfile(str(canonical_path)):
            raise ValueError(
                ".ingest/canonical_groups.jsonl is not a safe regular file"
            )
        try:
            from ingestion.dedup import load_canonical_groups
        except ImportError:
            from scripts.ingestion.dedup import load_canonical_groups
        canonical_groups = load_canonical_groups(root)
    return _verified_asset_policy_from_layers(
        quiz_rows=quiz,
        teaching_rows=teaching,
        content_units=units,
        canonical_groups=canonical_groups,
        workspace=root,
    )


def _unit_dict(value):
    if isinstance(value, dict):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    raise TypeError("content units must be dictionaries or expose to_dict()")


def _unit_text(unit):
    text = str(unit.get("text") or "").strip()
    if text:
        if unit.get("kind") == "question":
            metadata = unit.get("metadata") if isinstance(unit.get("metadata"), dict) else {}
            options = metadata.get("options")
            if isinstance(options, list) and options:
                rendered = []
                for index, option in enumerate(options):
                    if isinstance(option, dict):
                        label = option.get("label") or chr(65 + index)
                        value = option.get("text") or ""
                    else:
                        label = chr(65 + index)
                        value = option
                    rendered.append("%s. %s" % (label, value))
                text += "\n" + "\n".join(rendered)
        return text
    latex = str(unit.get("latex") or "").strip()
    if latex:
        if latex.startswith("$") and latex.endswith("$"):
            return latex
        return "$$\n%s\n$$" % latex
    html = str(unit.get("html") or "").strip()
    if html:
        searchable = re.sub(r"(?i)</(?:tr|p|div|li|h[1-6])\s*>", "\n", html)
        searchable = re.sub(r"(?i)</(?:td|th)\s*>", "\t", searchable)
        return re.sub(r"<[^>]+>", " ", searchable).strip()
    asset = str(unit.get("asset_path") or "").strip()
    if asset:
        return "Visual asset: %s" % asset
    return ""


def chunk_units(
        content_units, target=TARGET, hard_max=HARD_MAX,
        tainted_keys=None, workspace=None):
    """Structure-aware chunks over versioned ingestion content units.

    Tables, formulas, questions, code, diagrams, and figures are atomic: they are
    never split mid-element.  Ordinary prose is packed only across a shared
    source/chapter/section/parent boundary.  The output retains unit IDs, pages,
    source file, chapter/phase IDs, and parent context for citation and audit.
    Answer units are intentionally excluded from the teaching retrieval corpus.
    """

    if target <= 0 or hard_max <= 0 or target > hard_max:
        raise ValueError("target/hard_max must be positive and target <= hard_max")
    units = [_unit_dict(value) for value in content_units]
    has_assets = any(iter_asset_declarations(units))
    if workspace is not None:
        if tainted_keys is not None:
            raise ValueError("workspace and tainted_keys cannot be supplied together")
        tainted_keys = workspace_asset_policy(workspace)
    elif (isinstance(tainted_keys, _VerifiedAssetPolicy)
            and tainted_keys.workspace is not None):
        raise ValueError(
            "a workspace-bound asset-policy capability cannot be passed through "
            "tainted_keys; call chunk_units(..., workspace=<that workspace>) to live-load it"
        )
    if has_assets and not isinstance(tainted_keys, _VerifiedAssetPolicy):
        raise ValueError(
            "asset-bearing content units require workspace=... or a verified complete "
            "asset-policy capability; omitted/None/raw tainted_keys are not sufficient"
        )
    verified_policy = isinstance(tainted_keys, _VerifiedAssetPolicy)
    if verified_policy:
        for position, unit in enumerate(units):
            unit_id = unit.get("unit_id")
            expected = tainted_keys.unit_digests.get(unit_id)
            if expected is None or _policy_unit_digest(unit) != expected:
                raise ValueError(
                    "content_units[%d] is not bound to the verified live "
                    "unit revision/declarations" % position
                )
            aliases = unit.get("retrieval_occurrence_unit_ids")
            if aliases is not None:
                expected_aliases = tainted_keys.allowed_aliases.get(
                    unit_id, frozenset((unit_id,))
                )
                if (not isinstance(aliases, list)
                        or not aliases
                        or len(aliases) != len(set(aliases))
                        or frozenset(aliases) != expected_aliases):
                    raise ValueError(
                        "content_units[%d] has unbound retrieval occurrence aliases"
                        % position
                    )
    else:
        # The capability-free route is retained only for ordinary legacy
        # asset-free slicing.  Occurrence aliases are provenance claims over
        # other units and therefore require exact canonical-group lineage even
        # when every unit happens to be text-only.
        for position, unit in enumerate(units):
            if unit.get("retrieval_occurrence_unit_ids") is not None:
                raise ValueError(
                    "content_units[%d] retrieval occurrence aliases require a verified "
                    "workspace/canonical-group capability" % position
                )
    policy = audit_asset_policy(content_units=units)
    problems = policy["invalid_declarations"] + policy["conflicts"]
    if problems:
        raise ValueError("content-unit asset policy failed: %s" % "; ".join(problems))
    # A verified snapshot contains workspace-global taint.  Asset-free callers
    # retain the historical pure-helper route; a raw set may add conservative
    # taint there, but it never proves completeness for asset-bearing input.
    tainted_keys = set(tainted_keys or ()) | set(policy["tainted_keys"])
    units.sort(key=lambda unit: (
        str(unit.get("source_file") or ""), int(unit.get("page") or 0),
        int(unit.get("ordinal") or 0), str(unit.get("unit_id") or ""),
    ))

    chunks = []
    pending = []
    pending_key = None

    def title_for(unit):
        section_path = unit.get("section_path") or []
        return str(section_path[-1]) if section_path else (
            str(unit.get("text") or "")[:80] if unit.get("kind") in ("title", "heading") else ""
        )

    def emit(group, atomic=False):
        if not group:
            return
        texts = [_unit_text(unit) for unit in group]
        texts = [text for text in texts if text]
        if not texts:
            return
        first = group[0]
        text = "\n\n".join(texts)
        parent_context = " / ".join(str(v) for v in first.get("section_path") or () if str(v).strip())
        if parent_context and not text.startswith(parent_context):
            retrieval_text = parent_context + "\n" + text
        else:
            retrieval_text = text
        if atomic or len(retrieval_text) <= hard_max:
            parts = [retrieval_text]
        else:
            _cleaned, sliced = chunk_text(retrieval_text, target=target, hard_max=hard_max)
            parts = [part["text"] for part in sliced]
        base_id = "%s#u%s" % (
            first.get("chapter_id") or "unassigned",
            str(first.get("unit_id") or "")[-12:],
        )
        occurrence_unit_ids = []
        for unit in group:
            aliases = unit.get("retrieval_occurrence_unit_ids")
            values = aliases if isinstance(aliases, list) else [unit.get("unit_id")]
            for value in values:
                if isinstance(value, str) and value and value not in occurrence_unit_ids:
                    occurrence_unit_ids.append(value)
        for part_number, part_text in enumerate(parts, 1):
            chunks.append({
                "id": base_id + (":p%02d" % part_number if len(parts) > 1 else ""),
                "title": title_for(first),
                "text": part_text,
                "unit_ids": occurrence_unit_ids,
                "source_file": first.get("source_file"),
                "pages": sorted(set(int(unit.get("page")) for unit in group if unit.get("page"))),
                "chapter_id": first.get("chapter_id"),
                "phase_id": first.get("phase_id"),
                "parent_unit_id": first.get("parent_unit_id"),
                "section_path": list(first.get("section_path") or ()),
                "kind": first.get("kind") if atomic else "prose",
                "asset_paths": list(dict.fromkeys(
                    unit.get("asset_path") for unit in group if unit.get("asset_path")
                )),
                "asset_roles": list(dict.fromkeys(
                    unit.get("asset_role") for unit in group if unit.get("asset_role")
                )),
                "oversize_atomic": bool(atomic and len(retrieval_text) > hard_max),
            })

    for unit in units:
        kind = unit.get("kind")
        text = _unit_text(unit)
        if unit.get("asset_role") in (
                "answer_context", "worked_solution", "student_attempt", "source_page"):
            continue
        if has_tainted_official_asset(unit, tainted_keys):
            continue
        if kind not in _RETRIEVABLE_UNIT_KINDS or not text:
            continue
        key = (
            unit.get("source_id"), unit.get("source_sha256"), unit.get("chapter_id"),
            unit.get("phase_id"),
            tuple(unit.get("section_path") or ()), unit.get("parent_unit_id"),
        )
        if kind in _ATOMIC_UNIT_KINDS:
            emit(pending)
            pending = []
            pending_key = None
            emit([unit], atomic=True)
            continue
        if pending and (key != pending_key or sum(len(_unit_text(v)) for v in pending) + len(text) > hard_max):
            emit(pending)
            pending = []
        pending_key = key
        pending.append(unit)
        if sum(len(_unit_text(v)) for v in pending) >= target:
            emit(pending)
            pending = []
            pending_key = None
    emit(pending)
    return chunks
