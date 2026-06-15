"""Quality gate run on every generated post before it is allowed near WordPress."""
from __future__ import annotations

import html
import re

from .models import META_DESCRIPTION_MAX, SEO_TITLE_MAX, GeneratedPost, Lead

MIN_BODY_CHARS = 800
MAX_BODY_CHARS = 60_000

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Phrases that assert a direct-apply relationship; banned when the lead's
# apply URL is NOT classified as direct.
_DIRECT_APPLY_PHRASES: tuple[str, ...] = (
    "apply directly",
    "direct apply",
    "apply directly on their site",
    "apply directly with",
    "apply directly to",
)


def body_contains_apply_url(body_html: str, apply_url: str) -> bool:
    """True if the body links the apply URL (raw or attribute-escaped)."""
    return apply_url in body_html or html.escape(apply_url, quote=True) in body_html


def validate_post(lead: Lead, post: GeneratedPost) -> list[str]:
    """Return human-readable problems; an empty list means the post may publish.

    Runs on the generator output *before* the JSON-LD script is appended, so a
    <script> tag here always means the LLM emitted one.
    """
    problems: list[str] = []

    if not post.seo_title.strip():
        problems.append("empty seo_title")
    elif len(post.seo_title) > SEO_TITLE_MAX:
        problems.append(f"seo_title longer than {SEO_TITLE_MAX} chars")

    if not post.meta_description.strip():
        problems.append("empty meta_description")
    elif len(post.meta_description) > META_DESCRIPTION_MAX:
        problems.append(f"meta_description longer than {META_DESCRIPTION_MAX} chars")

    if not _SLUG_RE.match(post.slug):
        problems.append(f"invalid slug {post.slug!r}")
    if not post.focus_keyword.strip():
        problems.append("empty focus_keyword")
    if not post.excerpt.strip():
        problems.append("empty excerpt")

    body = post.body_html.strip()
    if not body:
        problems.append("empty body_html")
    elif len(body) < MIN_BODY_CHARS:
        problems.append(f"body too short ({len(body)} chars < {MIN_BODY_CHARS})")
    elif len(body) > MAX_BODY_CHARS:
        problems.append(f"body too long ({len(body)} chars > {MAX_BODY_CHARS})")

    if not body_contains_apply_url(post.body_html, lead.apply_url):
        problems.append("apply_url missing from body")
    if "<script" in body.lower():
        problems.append("body contains a <script> tag")

    # Backstop: reject "direct apply" framing when the URL is not actually direct.
    # This catches LLM drift back to direct-apply language despite the LINK FRAMING
    # prompt instruction.
    if not lead.is_direct:
        body_lower = body.lower()
        for phrase in _DIRECT_APPLY_PHRASES:
            if phrase in body_lower:
                problems.append(
                    f"body uses direct-apply framing ({phrase!r}) but "
                    f"apply_url is classified as {lead.link_type!r} "
                    "(not direct) -- update the copy or resolve the source link"
                )
                break  # one problem per lead is enough

    return problems
