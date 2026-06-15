"""Turns a Lead into validated, publish-ready post fields via an LLM backend."""
from __future__ import annotations

import html
import json
import logging
import re
from collections.abc import Sequence

from pydantic import ValidationError

from ..models import AffiliateLink, GeneratedPost, Lead
from ..qc import body_contains_apply_url
from .base import LLMBackend
from .prompts import SYSTEM_PROMPT, apply_anchor_html, build_user_prompt

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")

# Appended to every post body: FTC affiliate disclosure + due-diligence note.
DISCLOSURE_HTML = (
    "<hr />\n"
    "<p><em>Affiliate disclosure: some links on this page are affiliate links. If you "
    "sign up through them, The WFH Connect may earn a commission at no extra cost to "
    "you. We only recommend tools we genuinely rate.</em></p>\n"
    "<p><em>Job details can change at any time. We verify leads before posting, but "
    "always do your own due diligence: confirm the listing on the employer's official "
    "site, never pay to apply, and never share financial information during an "
    "application.</em></p>"
)


class ContentGenerationError(RuntimeError):
    """The LLM failed to produce a usable post for a lead."""


def extract_json(text: str) -> dict:
    """Parse a JSON object out of an LLM response, tolerating fences and stray prose."""
    candidate = _FENCE_RE.sub("", text.strip()).strip()
    attempts = [candidate]
    start, end = candidate.find("{"), candidate.rfind("}")
    if start != -1 and end > start:
        attempts.append(candidate[start : end + 1])
    for attempt in attempts:
        try:
            data = json.loads(attempt)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    raise ContentGenerationError(f"LLM response is not a JSON object: {text[:200]!r}")


def render_affiliate_section(links: Sequence[AffiliateLink]) -> str:
    """A clearly-labelled 'Recommended tools' block built from config, not the LLM."""
    items: list[str] = []
    for link in links:
        blurb = f" -- {html.escape(link.blurb)}" if link.blurb else ""
        items.append(
            f'<li><a href="{html.escape(link.url, quote=True)}" rel="sponsored noopener" '
            f'target="_blank">{html.escape(link.name)}</a>{blurb}</li>'
        )
    return (
        "<h2>Recommended tools for your remote job search</h2>\n"
        "<p>A few services we use and recommend (some are affiliate links -- see the "
        "disclosure below):</p>\n<ul>\n" + "\n".join(items) + "\n</ul>"
    )


class ContentGenerator:
    """LLM wrapper: prompt -> JSON -> GeneratedPost, plus deterministic post-processing.

    The affiliate section, FTC disclosure, and (if the model forgot it) the apply
    button are appended in code so they can never be mangled or omitted by the LLM.
    """

    def __init__(
        self,
        backend: LLMBackend,
        affiliate_links: Sequence[AffiliateLink] = (),
        *,
        max_attempts: int = 2,
    ) -> None:
        self._backend = backend
        self._affiliate_links = tuple(affiliate_links)
        self._max_attempts = max_attempts

    def generate(self, lead: Lead) -> GeneratedPost:
        prompt = build_user_prompt(lead)
        last_error: Exception | None = None
        post: GeneratedPost | None = None
        for attempt in range(1, self._max_attempts + 1):
            raw = self._backend.complete(system=SYSTEM_PROMPT, prompt=prompt)
            try:
                post = GeneratedPost.model_validate(extract_json(raw))
                break
            except (ContentGenerationError, ValidationError) as exc:
                last_error = exc
                logger.warning(
                    "Attempt %d/%d produced unusable output for %s (%s -- %s): %s",
                    attempt,
                    self._max_attempts,
                    lead.id,
                    lead.company,
                    lead.title,
                    exc,
                )
        if post is None:
            raise ContentGenerationError(
                f"Could not generate valid post JSON for lead {lead.id} "
                f"({lead.company} -- {lead.title})"
            ) from last_error
        return self._finalize(lead, post)

    def _finalize(self, lead: Lead, post: GeneratedPost) -> GeneratedPost:
        sections = [post.body_html.strip()]
        if not body_contains_apply_url(post.body_html, lead.apply_url):
            logger.warning("LLM omitted the apply link for %s; appending one", lead.id)
            sections.append(f"<h2>How to apply</h2>\n{apply_anchor_html(lead)}")
        if self._affiliate_links:
            sections.append(render_affiliate_section(self._affiliate_links))
        sections.append(DISCLOSURE_HTML)
        return post.model_copy(update={"body_html": "\n\n".join(sections)})
