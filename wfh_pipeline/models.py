"""Pydantic data models shared across the pipeline."""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from .link_classifier import LinkType, classify_url
from .utils import slugify, truncate_at_word

SourceTrust = Literal["direct", "aggregator", "unknown"]

SEO_TITLE_MAX = 60
META_DESCRIPTION_MAX = 155


def stable_lead_id(apply_url: str) -> str:
    """Stable 16-char hash of an apply URL, used to dedupe leads across runs."""
    normalized = apply_url.strip().lower().rstrip("/")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


class AffiliateLink(BaseModel):
    """One entry in the "Recommended tools" section, loaded from AFFILIATE_LINKS."""

    name: str
    url: str
    blurb: str = ""


class Lead(BaseModel):
    """A verified (or not) remote-job lead from any source.

    Two computed fields are derived automatically from ``apply_url``:

    * ``link_type`` -- "direct", "aggregator", or "unknown" as classified by
      wfh_pipeline.link_classifier.
    * ``is_direct`` -- True only when link_type == "direct".

    These drive the pledge-enforcement logic in the generator and QC gate.
    Never set them manually; they will be re-derived from the URL anyway.

    ``source_trust`` is set by the LeadSource that produced the lead
    ("direct" for ATS feeds, "aggregator" for RSS boards, "unknown" default).
    It is complementary to ``link_type``: a source can be "aggregator" even
    when a particular lead's URL happens to be direct (e.g. a WWR listing that
    links straight to Greenhouse).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    id: str = ""
    company: str = Field(min_length=1)
    title: str = Field(min_length=1)
    pay: str | None = None
    remote: bool = True
    employment_type: str = "FULL_TIME"
    requirements: list[str] = Field(default_factory=list)
    description: str = ""
    apply_url: str
    source: str = "unknown"
    source_trust: SourceTrust = "unknown"
    date_found: date = Field(default_factory=date.today)
    category: str = "remote-jobs"
    verified: bool = False

    # ------------------------------------------------------------------
    # Computed fields -- derived from apply_url, never stored in CSV/DB
    # ------------------------------------------------------------------

    @computed_field  # type: ignore[prop-decorator]
    @property
    def link_type(self) -> LinkType:
        """Classify the apply URL as "direct", "aggregator", or "unknown"."""
        return classify_url(self.apply_url)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_direct(self) -> bool:
        """True only when link_type is "direct"."""
        return self.link_type == "direct"

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("apply_url")
    @classmethod
    def _absolute_url(cls, value: str) -> str:
        if not value.lower().startswith(("http://", "https://")):
            raise ValueError("apply_url must be an absolute http(s) URL")
        return value

    @model_validator(mode="after")
    def _ensure_id(self) -> "Lead":
        if not self.id:
            self.id = stable_lead_id(self.apply_url)
        return self


class GeneratedPost(BaseModel):
    """Structured post fields produced by the LLM, normalized for publishing.

    seo_title and meta_description are trimmed at word boundaries to their SEO
    limits; the slug is re-sanitized so we never trust the model with URL parts.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    seo_title: str = Field(min_length=1)
    slug: str = Field(min_length=1)
    meta_description: str = Field(min_length=1)
    focus_keyword: str = Field(min_length=1)
    body_html: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)

    @field_validator("seo_title")
    @classmethod
    def _fit_title(cls, value: str) -> str:
        return truncate_at_word(value, SEO_TITLE_MAX)

    @field_validator("meta_description")
    @classmethod
    def _fit_meta_description(cls, value: str) -> str:
        return truncate_at_word(value, META_DESCRIPTION_MAX)

    @field_validator("slug")
    @classmethod
    def _sanitize_slug(cls, value: str) -> str:
        slug = slugify(value)
        if not slug:
            raise ValueError("slug is empty after sanitizing")
        return slug
