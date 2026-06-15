"""Small text helpers shared across modules."""
from __future__ import annotations

import re
import unicodedata


def slugify(text: str, max_length: int = 80) -> str:
    """Convert arbitrary text to a lowercase, hyphenated, URL-safe slug."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(text) > max_length:
        text = text[:max_length].rsplit("-", 1)[0]
    return text


def truncate_at_word(text: str, limit: int) -> str:
    """Collapse whitespace and trim to at most ``limit`` chars without cutting mid-word."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit + 1]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    else:
        cut = text[:limit]
    return cut.rstrip(" ,;:-–—").rstrip()


def humanize_category(slug: str) -> str:
    """Turn a lead category slug like ``customer-service`` into ``Customer Service``."""
    return slug.replace("-", " ").replace("_", " ").strip().title()


# ---------------------------------------------------------------------------
# Non-phone / phone role detection
# ---------------------------------------------------------------------------

import re as _re

_NON_PHONE_SIGNALS = (
    "non-phone",
    "non phone",
    "no phone",
    "chat support",
    "chat agent",
    "email support",
    "data entry",
    "back office",
    "no calls",
    "text-based",
    "messaging",
    "ticket",
    "ticketing",
)

_PHONE_SIGNALS = (
    "call center",
    "contact center",
    "inbound calls",
    "outbound calls",
    "inbound/outbound",
    "phone support",
    "phone-based",
    "dialer",
    "voice support",
    "phone calls",
    "make calls",
    "receive calls",
)


def is_non_phone(title: str, description: str = "") -> bool:
    """Return True if the role appears to be non-phone (chat/email/data-entry).

    Logic:
    - If any phone signal appears in title or description → False (phone role).
    - Else if any non-phone signal appears → True.
    - Else → False (unknown / assume phone).

    Both title and description are checked case-insensitively.
    """
    blob = (title + " " + description).lower()

    # Phone signals take priority — if any phone language is present, not non-phone
    if any(sig in blob for sig in _PHONE_SIGNALS):
        return False

    # Non-phone signals
    return any(sig in blob for sig in _NON_PHONE_SIGNALS)
