"""WordPressClient tests using httpx.MockTransport — no real site involved."""
from __future__ import annotations

import json
from typing import Any

import httpx

from wfh_pipeline.wordpress import WordPressClient


def _client(handler) -> WordPressClient:
    return WordPressClient(
        "https://example.com",
        "user",
        "app password",
        transport=httpx.MockTransport(handler),
        max_retries=1,
        backoff_base=0.0,
    )


def test_create_post_retries_without_meta_when_rejected() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if "meta" in body:
            return httpx.Response(
                400, json={"code": "rest_invalid_param", "message": "Invalid parameter(s): meta"}
            )
        return httpx.Response(
            201, json={"id": 7, "link": "https://example.com/?p=7", "meta": {}}
        )

    with _client(handler) as client:
        data = client.create_post(
            title="T", content="C", meta={"rank_math_title": "T"}
        )
    assert data["id"] == 7
    assert len(requests) == 2
    assert "meta" not in requests[-1]


def test_create_post_future_sends_utc_date() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json={"id": 1, "link": "x"})

    scheduled = datetime(2026, 6, 10, 8, 0, tzinfo=ZoneInfo("America/New_York"))  # EDT = UTC-4
    with _client(handler) as client:
        client.create_post(title="T", content="C", status="future", scheduled_for=scheduled)
    assert captured["status"] == "future"
    assert captured["date_gmt"] == "2026-06-10T12:00:00"


def test_get_or_create_term_handles_term_exists_race() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=[])  # search misses
        return httpx.Response(
            400, json={"code": "term_exists", "message": "exists", "data": {"term_id": 55}}
        )

    with _client(handler) as client:
        assert client.get_or_create_category("Customer Service") == 55


def test_get_or_create_term_matches_existing_by_name() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(
            200, json=[{"id": 9, "name": "Customer Service", "slug": "customer-service"}]
        )

    with _client(handler) as client:
        assert client.get_or_create_category("customer service") == 9


def test_retries_on_server_error_then_succeeds() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(503, text="upstream hiccup")
        return httpx.Response(201, json={"id": 3, "link": "x"})

    with _client(handler) as client:
        data = client.create_post(title="T", content="C")
    assert data["id"] == 3
    assert attempts["n"] == 2
