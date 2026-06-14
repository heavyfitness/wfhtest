"""Unit tests for LLM JSON parsing, GeneratedPost normalization, and the generator."""
from __future__ import annotations

import json

import pytest

from wfh_pipeline.generation.base import LLMBackend
from wfh_pipeline.generation.generator import (
    ContentGenerationError,
    ContentGenerator,
    extract_json,
)
from wfh_pipeline.models import AffiliateLink, GeneratedPost

VALID_PAYLOAD = {
    "seo_title": "Remote Customer Service Jobs at BrightDesk (2026)",
    "slug": "brightdesk-remote-customer-service-jobs",
    "meta_description": (
        "BrightDesk is hiring US-based remote customer service reps at $17-$19/hr. "
        "See the requirements and apply direct."
    ),
    "focus_keyword": "remote customer service jobs",
    "body_html": "<p>Intro paragraph.</p>" + "<p>Filler paragraph for length checks.</p>" * 40,
    "excerpt": "BrightDesk is hiring remote customer service reps at $17-$19/hr.",
}


class StubBackend(LLMBackend):
    """Returns canned responses in order; counts calls."""

    name = "stub"

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def complete(self, *, system: str, prompt: str, max_tokens: int = 8192) -> str:
        self.calls += 1
        return self.responses.pop(0)


# ── extract_json ─────────────────────────────────────────────────────────────


def test_extract_json_plain() -> None:
    assert extract_json(json.dumps(VALID_PAYLOAD)) == VALID_PAYLOAD


def test_extract_json_strips_markdown_fences() -> None:
    fenced = "```json\n" + json.dumps(VALID_PAYLOAD) + "\n```"
    assert extract_json(fenced) == VALID_PAYLOAD


def test_extract_json_tolerates_surrounding_prose() -> None:
    noisy = "Here is your post:\n" + json.dumps(VALID_PAYLOAD) + "\nHope that helps!"
    assert extract_json(noisy) == VALID_PAYLOAD


def test_extract_json_rejects_garbage_and_non_objects() -> None:
    with pytest.raises(ContentGenerationError):
        extract_json("this is not json at all")
    with pytest.raises(ContentGenerationError):
        extract_json("[1, 2, 3]")


# ── GeneratedPost normalization ──────────────────────────────────────────────


def test_seo_title_truncated_at_word_boundary() -> None:
    long_title = "Legit Work From Home Customer Service Jobs Hiring Right Now In 2026"
    post = GeneratedPost.model_validate({**VALID_PAYLOAD, "seo_title": long_title})
    assert len(post.seo_title) <= 60
    assert long_title.startswith(post.seo_title)  # no mid-word cut
    assert not post.seo_title.endswith(" ")


def test_meta_description_truncated() -> None:
    post = GeneratedPost.model_validate(
        {**VALID_PAYLOAD, "meta_description": "word " * 60}
    )
    assert len(post.meta_description) <= 155


def test_slug_sanitized() -> None:
    post = GeneratedPost.model_validate({**VALID_PAYLOAD, "slug": "Hello World! Jobs"})
    assert post.slug == "hello-world-jobs"


# ── ContentGenerator ─────────────────────────────────────────────────────────


def test_generator_appends_apply_link_affiliates_and_disclosures(make_lead) -> None:
    lead = make_lead()
    backend = StubBackend([json.dumps(VALID_PAYLOAD)])  # body omits the apply link
    generator = ContentGenerator(
        backend,
        affiliate_links=[
            AffiliateLink(name="FlexJobs", url="https://example.com/aff", blurb="Curated listings")
        ],
    )
    post = generator.generate(lead)
    assert lead.apply_url in post.body_html
    assert "Recommended tools" in post.body_html
    assert "FlexJobs" in post.body_html
    assert "Affiliate disclosure" in post.body_html
    assert "due diligence" in post.body_html


def test_generator_retries_once_then_raises(make_lead) -> None:
    backend = StubBackend(["garbage one", "garbage two"])
    generator = ContentGenerator(backend)
    with pytest.raises(ContentGenerationError):
        generator.generate(make_lead())
    assert backend.calls == 2


def test_generator_recovers_on_second_attempt(make_lead) -> None:
    backend = StubBackend(["garbage", json.dumps(VALID_PAYLOAD)])
    post = ContentGenerator(backend).generate(make_lead())
    assert post.seo_title == VALID_PAYLOAD["seo_title"]
    assert backend.calls == 2
