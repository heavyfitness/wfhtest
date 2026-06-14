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
