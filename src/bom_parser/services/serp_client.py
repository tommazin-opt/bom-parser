"""Async Bright Data Google SERP API client.

Sends each search query to Bright Data's "Direct API" request endpoint and
returns the top-N organic result URLs. One query == one billable request, so
callers should pass the *deduplicated* query list
([serp_flattener.unique_queries]); the returned ``dict`` keyed by query string
doubles as the cache the exporter reads back.

Transport details:

* ``POST {endpoint}`` with ``Authorization: Bearer {api_token}`` and JSON body
  ``{"zone": ..., "url": "https://www.google.com/search?q=...&brd_json=1",
  "format": "raw"}``. The ``brd_json=1`` suffix makes Bright Data return parsed
  SERP JSON whose ``organic`` array holds ``{"link": ...}`` objects.
* Google-only by construction — the request URL is always a google.com/search
  URL.
* Each request retries up to ``max_retries`` with exponential backoff on
  timeouts, 429, and 5xx. On final failure the query resolves to ``[]`` (logged)
  so one bad query never aborts the whole file.
* Bright Data wraps upstream errors in an outer HTTP 200 with the real status in
  ``x-brd-status-code``; we inspect those headers. A credential/zone rejection
  (``client_10000`` / proxy ``407``) is not transient, so it raises
  ``SerpAuthError`` to abort the whole run with the upstream message rather than
  retrying every query into a silent empty result.

TLS note: many corporate networks / HTTPS-scanning antivirus re-sign traffic
with a CA that strict OpenSSL rejects. We verify via ``truststore`` (the OS
trust store, e.g. the Windows certificate store) so those environments work the
same way the user's browser already does.
"""

from __future__ import annotations

import asyncio
import json
import ssl
from collections.abc import Mapping
from typing import Any, cast
from urllib.parse import quote_plus

import aiohttp
import structlog
import truststore

from bom_parser.models.serp import SerpConfig
from bom_parser.utils.consts import (
    BRD_AUTH_ERR_CODE,
    BRD_ERR_CODE_HEADER,
    BRD_ERR_MSG_HEADER,
    BRD_PROXY_AUTH_STATUS,
    BRD_STATUS_CODE_HEADER,
    GOOGLE_SEARCH_URL_TEMPLATE,
    SERP_ORGANIC_KEY,
    SERP_RESULT_LINK_KEY,
    SERP_RETRYABLE_STATUS,
)

log = structlog.get_logger(__name__)


class SerpAuthError(Exception):
    """Bright Data rejected the credentials/zone — fatal, abort the whole run."""


class _RetryableStatusError(Exception):
    """Raised internally to route a retryable HTTP status into the backoff loop."""

    def __init__(self, status: int) -> None:
        super().__init__(f"retryable status {status}")
        self.status = status


def _check_brd_error(headers: Mapping[str, str], query: str) -> bool:
    """Inspect Bright Data's ``x-brd-*`` error headers on an outer-200 response.

    Returns ``True`` if the body should be parsed normally (no upstream error).
    Raises ``SerpAuthError`` for a credential/zone rejection. For any other
    upstream error, logs it and returns ``False`` (caller yields no URLs).
    """
    err_code = headers.get(BRD_ERR_CODE_HEADER)
    if not err_code:
        return True
    err_msg = headers.get(BRD_ERR_MSG_HEADER, err_code)
    brd_status = headers.get(BRD_STATUS_CODE_HEADER)
    if err_code == BRD_AUTH_ERR_CODE or brd_status == BRD_PROXY_AUTH_STATUS:
        raise SerpAuthError(err_msg)
    log.warning(
        "serp_brd_error", query=query, code=err_code, msg=err_msg, brd_status=brd_status
    )
    return False


def _extract_links(payload: Any, top_n: int) -> list[str]:
    """Pull the first ``top_n`` organic result URLs from Bright Data's JSON."""
    if not isinstance(payload, dict):
        return []
    organic = cast("dict[str, Any]", payload).get(SERP_ORGANIC_KEY)
    if not isinstance(organic, list):
        return []
    links: list[str] = []
    for entry in cast("list[Any]", organic):
        if not isinstance(entry, dict):
            continue
        link = cast("dict[str, Any]", entry).get(SERP_RESULT_LINK_KEY)
        if isinstance(link, str) and link:
            links.append(link)
        if len(links) >= top_n:
            break
    return links


async def fetch_serp(
    session: aiohttp.ClientSession,
    query: str,
    cfg: SerpConfig,
) -> list[str]:
    """Run one query through Bright Data, returning up to ``cfg.top_n`` URLs.

    Never raises for transport/API errors — exhausted retries resolve to ``[]``
    so the caller's ``gather`` always completes. The one exception is
    ``SerpAuthError`` (bad credentials/zone): it propagates so the run aborts
    instead of retrying every query into the same failure.
    """
    search_url = GOOGLE_SEARCH_URL_TEMPLATE.format(q=quote_plus(query))
    body = {"zone": cfg.zone, "url": search_url, "format": "raw"}
    headers = {
        "Authorization": f"Bearer {cfg.api_token}",
        "Content-Type": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=cfg.timeout_s)

    for attempt in range(cfg.max_retries + 1):
        try:
            async with session.post(
                cfg.endpoint, json=body, headers=headers, timeout=timeout
            ) as response:
                if response.status in SERP_RETRYABLE_STATUS:
                    raise _RetryableStatusError(response.status)
                response.raise_for_status()
                # Bright Data wraps upstream errors in an outer 200; bail (or
                # abort, for auth) before trying to parse an empty/error body.
                if not _check_brd_error(response.headers, query):
                    return []
                text = await response.text()
            payload = json.loads(text)
            return _extract_links(payload, cfg.top_n)
        except (
            _RetryableStatusError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
            json.JSONDecodeError,
        ) as exc:
            if attempt >= cfg.max_retries:
                log.warning(
                    "serp_query_failed",
                    query=query,
                    attempts=attempt + 1,
                    error=str(exc),
                )
                return []
            await asyncio.sleep(cfg.backoff_base_s * (2**attempt))
    return []


async def run_searches(queries: list[str], cfg: SerpConfig) -> dict[str, list[str]]:
    """Run every (already-deduplicated) query concurrently under a semaphore.

    Returns a ``{query: [url, ...]}`` map. Concurrency is bounded by
    ``cfg.max_concurrency`` to respect the Bright Data plan's request limit.
    """
    if not queries:
        return {}

    semaphore = asyncio.Semaphore(cfg.max_concurrency)

    # Verify TLS against the OS trust store so HTTPS-inspecting proxies / AV
    # (common on corporate Windows) don't break the run with cert errors.
    ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    connector = aiohttp.TCPConnector(ssl=ssl_context)

    async with aiohttp.ClientSession(connector=connector) as session:

        async def bounded(query: str) -> tuple[str, list[str]]:
            async with semaphore:
                return query, await fetch_serp(session, query, cfg)

        results = await asyncio.gather(*(bounded(q) for q in queries))

    return dict(results)
