"""Minimal WordPress REST API client (Application Password / Basic Auth)."""
from __future__ import annotations

import logging
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

import httpx

from . import __version__
from .utils import slugify

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
VALID_STATUSES = ("draft", "publish", "future")


class WordPressError(RuntimeError):
    """A WordPress REST call failed after retries."""


class WordPressClient:
    """Wraps {WP_URL}/wp-json/wp/v2 with Basic Auth and retry/backoff."""

    def __init__(
        self,
        base_url: str,
        username: str,
        app_password: str,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_root = base_url.rstrip("/") + "/wp-json/wp/v2"
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._client = httpx.Client(
            base_url=self.api_root,
            auth=httpx.BasicAuth(username, app_password),  # base64 Basic header
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": f"wfh-content-pipeline/{__version__}"},
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "WordPressClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Issue a request, retrying transport errors and retryable statuses."""
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                last_error = exc
                logger.warning(
                    "WP %s %s transport error (attempt %d/%d): %s",
                    method, path, attempt + 1, self._max_retries + 1, exc,
                )
            else:
                if response.status_code not in RETRYABLE_STATUS:
                    return response
                last_error = WordPressError(
                    f"HTTP {response.status_code}: {response.text[:200]}"
                )
                logger.warning(
                    "WP %s %s returned %d (attempt %d/%d)",
                    method, path, response.status_code, attempt + 1, self._max_retries + 1,
                )
            if attempt < self._max_retries:
                time.sleep(self._backoff_base * 2**attempt)
        raise WordPressError(
            f"{method} {path} failed after {self._max_retries + 1} attempts"
        ) from last_error

    # ── taxonomy ─────────────────────────────────────────────────────────────

    def get_or_create_category(self, name: str) -> int:
        return self._get_or_create_term("categories", name)

    def get_or_create_tag(self, name: str) -> int:
        return self._get_or_create_term("tags", name)

    def _get_or_create_term(self, endpoint: str, name: str) -> int:
        response = self._request(
            "GET", f"/{endpoint}", params={"search": name, "per_page": 100}
        )
        if response.status_code == 200:
            wanted_slug = slugify(name)
            for term in response.json():
                if (
                    str(term.get("name", "")).strip().lower() == name.strip().lower()
                    or term.get("slug") == wanted_slug
                ):
                    return int(term["id"])

        response = self._request("POST", f"/{endpoint}", json={"name": name})
        if response.status_code in (200, 201):
            return int(response.json()["id"])
        if response.status_code == 400:
            try:
                body = response.json()
            except ValueError:
                body = {}
            # Race-safe: another run created it between our GET and POST.
            if body.get("code") == "term_exists":
                return int(body["data"]["term_id"])
        raise WordPressError(
            f"Could not create {endpoint[:-1]} {name!r}: "
            f"HTTP {response.status_code} {response.text[:200]}"
        )

    # ── posts ────────────────────────────────────────────────────────────────

    def create_post(
        self,
        *,
        title: str,
        content: str,
        excerpt: str = "",
        status: str = "draft",
        slug: str | None = None,
        categories: Sequence[int] | None = None,
        tags: Sequence[int] | None = None,
        meta: Mapping[str, str] | None = None,
        scheduled_for: datetime | None = None,
    ) -> dict[str, Any]:
        """Create a post; ``status='future'`` requires a tz-aware ``scheduled_for``.

        If WordPress rejects the request because the RankMath meta keys are not
        registered for REST (mu-plugin missing), the post is retried — and
        published — without meta, with a warning.
        """
        if status not in VALID_STATUSES:
            raise ValueError(f"status must be one of {VALID_STATUSES}, got {status!r}")

        payload: dict[str, Any] = {"title": title, "content": content, "status": status}
        if excerpt:
            payload["excerpt"] = excerpt
        if slug:
            payload["slug"] = slug
        if categories:
            payload["categories"] = list(categories)
        if tags:
            payload["tags"] = list(tags)
        if meta:
            payload["meta"] = dict(meta)
        if status == "future":
            if scheduled_for is None:
                raise ValueError("status='future' requires scheduled_for")
            payload["date_gmt"] = scheduled_for.astimezone(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S"
            )

        response = self._request("POST", "/posts", json=payload)
        if response.status_code == 400 and "meta" in payload:
            logger.warning(
                "WordPress rejected the post meta — are the RankMath keys registered "
                "for REST? Install mu-plugins/wfh-rest-meta.php. Publishing without "
                "SEO meta. Response: %s",
                response.text[:300],
            )
            payload.pop("meta")
            response = self._request("POST", "/posts", json=payload)
        if response.status_code not in (200, 201):
            raise WordPressError(
                f"create_post failed: HTTP {response.status_code} {response.text[:300]}"
            )

        data: dict[str, Any] = response.json()
        returned_meta = data.get("meta")
        if meta and isinstance(returned_meta, dict):
            missing = [key for key in meta if not returned_meta.get(key)]
            if missing:
                logger.warning(
                    "RankMath meta not persisted (%s) — check the mu-plugin install",
                    ", ".join(missing),
                )
        return data
