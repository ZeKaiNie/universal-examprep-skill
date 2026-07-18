#!/usr/bin/env python3
"""Shared stable item-ID contract for teaching/notebook/Guide boundaries.

IDs are control-plane keys, but they are not required to be English.  The
typed Study Guide accepts safe Unicode identifiers, so this contract keeps
that compatibility instead of introducing an ASCII-only migration boundary.
"""

import re
import unicodedata


MAX_STABLE_ITEM_ID_LENGTH = 200
STABLE_ITEM_ID_RE = re.compile(r"^[^\s\[\]#|`/\\]+$")


def _forbidden_unicode_scalar(char):
    code = ord(char)
    category = unicodedata.category(char)
    return (
        category in ("Cc", "Cf", "Cs")
        or code == 0xFFFD
        or 0xFDD0 <= code <= 0xFDEF
        or (code & 0xFFFF) in (0xFFFE, 0xFFFF)
    )


def stable_item_id_problem(value):
    """Return a bounded validation reason, or ``None`` for a canonical ID."""

    if not isinstance(value, str) or not value:
        return "must be a non-empty string"
    if len(value) > MAX_STABLE_ITEM_ID_LENGTH:
        return "must be at most %d characters" % MAX_STABLE_ITEM_ID_LENGTH
    if any(_forbidden_unicode_scalar(char) for char in value):
        return (
            "must not contain control, format, surrogate, replacement, "
            "or Unicode noncharacter code points"
        )
    if not STABLE_ITEM_ID_RE.fullmatch(value):
        return (
            "may use Unicode but must not contain whitespace or any of "
            "[]#|`/\\"
        )
    return None


def is_stable_item_id(value):
    return stable_item_id_problem(value) is None
