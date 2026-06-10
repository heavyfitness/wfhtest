"""Pydantic data models shared across the pipeline."""
from __future__ import annotations

import hashlib
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .utils import slugify, truncate_at_word

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
    """A verified (or not) remote-job lead from any source."""

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
    date_found: date = Field(default_factory=date.today)
    category: str = "remote-jobs"
    verified: bool = False

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
