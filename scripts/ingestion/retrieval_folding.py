"""Display/retrieval folding over already-verified canonical groups.

Immutable content units remain authoritative.  This helper only removes repeated
members from the transient chunking input and carries every occurrence unit ID
onto the selected display unit so citations can still expand through the fact
sidecar.
"""

import copy

from .facts import CanonicalGroup


class RetrievalFoldingError(ValueError):
    pass


def _unit_dict(value):
    if isinstance(value, dict):
        return copy.deepcopy(value)
    method = getattr(value, "to_dict", None)
    if callable(method):
        return method()
    raise RetrievalFoldingError("content unit must be an object or expose to_dict()")


def fold_units_for_retrieval(units, canonical_groups):
    """Return a folded transient unit list without mutating persisted facts."""

    rows = [_unit_dict(value) for value in units]
    by_id = {}
    for row in rows:
        unit_id = row.get("unit_id")
        if not isinstance(unit_id, str) or not unit_id or unit_id in by_id:
            raise RetrievalFoldingError("content units need unique non-empty unit_id values")
        by_id[unit_id] = row

    aliases = {}
    excluded = set()
    seen_members = set()
    groups = [
        value if isinstance(value, CanonicalGroup) else CanonicalGroup.from_dict(value)
        for value in canonical_groups
    ]
    for group in sorted(groups, key=lambda item: item.canonical_group_id):
        member_ids = sorted(ref.unit_id for ref in group.member_refs)
        missing = sorted(set(member_ids) - set(by_id))
        overlap = sorted(set(member_ids) & seen_members)
        if missing or overlap:
            raise RetrievalFoldingError(
                "canonical group %s has missing=%r overlap=%r"
                % (group.canonical_group_id, missing, overlap)
            )
        display = group.display_unit_id
        seen_members.update(member_ids)
        aliases[display] = member_ids
        excluded.update(set(member_ids) - {display})

    output = []
    for unit_id in sorted(by_id, key=lambda value: (
            str(by_id[value].get("source_file") or ""),
            int(by_id[value].get("page") or 0),
            int(by_id[value].get("ordinal") or 0),
            value)):
        if unit_id in excluded:
            continue
        row = by_id[unit_id]
        if unit_id in aliases:
            row["retrieval_occurrence_unit_ids"] = aliases[unit_id]
        output.append(row)
    return output


__all__ = ["RetrievalFoldingError", "fold_units_for_retrieval"]
