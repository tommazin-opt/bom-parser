"""Unit tests for the async Bright Data SERP client.

No real network: a fake aiohttp session feeds canned responses. Covers link
extraction + top_n capping, the retry-then-succeed path on a 429, and graceful
``[]`` after the retry budget is exhausted.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp
import pytest

from bom_parser.models.serp import SerpConfig
from bom_parser.services.serp_client import (
    SerpAuthError,
    _extract_links,
    fetch_serp,
)


def _payload(n: int) -> str:
    return json.dumps(
        {"organic": [{"link": f"https://r{i}.example"} for i in range(n)]}
    )


class _FakeResponse:
    def __init__(
        self, status: int, body: str, headers: dict[str, str] | None = None
    ) -> None:
        self.status = status
        self._body = body
        self.headers = headers or {}

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                request_info=None,  # type: ignore[arg-type]
                history=(),
                status=self.status,
            )

    async def text(self) -> str:
        return self._body


class _FakeSession:
    """Yields queued responses in order; records how many posts were made."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses
        self.calls = 0

    def post(self, *args: Any, **kwargs: Any) -> _FakeResponse:
        resp = self._responses[self.calls]
        self.calls += 1
        return resp


def _cfg(**overrides: Any) -> SerpConfig:
    base: dict[str, Any] = {
        "api_token": "TEST_TOKEN",
        "zone": "test_zone",
        "max_retries": 2,
        "backoff_base_s": 0.001,  # keep retry sleeps negligible
        "top_n": 10,
    }
    base.update(overrides)
    return SerpConfig(**base)


def test_extract_links_caps_at_top_n() -> None:
    payload = json.loads(_payload(25))
    assert _extract_links(payload, 10) == [f"https://r{i}.example" for i in range(10)]


def test_extract_links_handles_missing_organic() -> None:
    assert _extract_links({}, 10) == []
    assert _extract_links({"organic": "nope"}, 10) == []


def test_fetch_returns_top_urls() -> None:
    session = _FakeSession([_FakeResponse(200, _payload(12))])
    urls = asyncio.run(fetch_serp(session, "some query", _cfg()))  # type: ignore[arg-type]
    assert len(urls) == 10
    assert session.calls == 1


def test_retry_then_success_on_429() -> None:
    session = _FakeSession(
        [_FakeResponse(429, ""), _FakeResponse(200, _payload(3))]
    )
    urls = asyncio.run(fetch_serp(session, "q", _cfg()))  # type: ignore[arg-type]
    assert urls == ["https://r0.example", "https://r1.example", "https://r2.example"]
    assert session.calls == 2  # one retry


def test_returns_empty_after_budget_exhausted() -> None:
    # max_retries=1 → 2 attempts total, both 503.
    session = _FakeSession([_FakeResponse(503, ""), _FakeResponse(503, "")])
    urls = asyncio.run(fetch_serp(session, "q", _cfg(max_retries=1)))  # type: ignore[arg-type]
    assert urls == []
    assert session.calls == 2


def test_invalid_json_resolves_to_empty() -> None:
    session = _FakeSession([_FakeResponse(200, "not json")])
    urls = asyncio.run(fetch_serp(session, "q", _cfg(max_retries=0)))  # type: ignore[arg-type]
    assert urls == []


def test_brd_auth_error_raises_and_does_not_retry() -> None:
    # Bright Data wraps auth failures in an outer 200 with x-brd-* headers.
    session = _FakeSession(
        [
            _FakeResponse(
                200,
                "",
                headers={
                    "x-brd-err-code": "client_10000",
                    "x-brd-err-msg": "Invalid authentication: check credentials",
                    "x-brd-status-code": "407",
                },
            )
        ]
    )
    with pytest.raises(SerpAuthError, match="Invalid authentication"):
        asyncio.run(fetch_serp(session, "q", _cfg()))  # type: ignore[arg-type]
    assert session.calls == 1  # fatal — not retried


def test_brd_non_auth_error_resolves_to_empty() -> None:
    session = _FakeSession(
        [_FakeResponse(200, "", headers={"x-brd-err-code": "client_20000"})]
    )
    urls = asyncio.run(fetch_serp(session, "q", _cfg(max_retries=0)))  # type: ignore[arg-type]
    assert urls == []
    assert session.calls == 1
