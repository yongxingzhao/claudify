"""Retry logic for upstream HTTP requests."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger("claudify.retry")


async def post_with_retry(
    client: httpx.AsyncClient,
    request: httpx.Request,
    attempts: int,
    backoff: float,
) -> httpx.Response:
    return await _do_retry(client.send, request, attempts, backoff)


async def stream_with_retry(
    client: httpx.AsyncClient,
    request: httpx.Request,
    attempts: int,
    backoff: float,
) -> tuple[httpx.Response, bool]:
    last_exc: Exception | None = None
    r: httpx.Response | None = None
    for attempt in range(attempts):
        try:
            r = await client.send(request, stream=True)
            if r.status_code < 500:
                return r, attempt > 0
            if attempt < attempts - 1:
                await r.aclose()
                log.warning("stream retry %d/%d after status %d", attempt + 1, attempts, r.status_code)
                await asyncio.sleep(backoff * (2 ** attempt))
        except (httpx.ConnectError, httpx.ReadError, httpx.WriteError) as exc:
            last_exc = exc
            if attempt < attempts - 1:
                log.warning("stream retry %d/%d after %s", attempt + 1, attempts, type(exc).__name__)
                await asyncio.sleep(backoff * (2 ** attempt))
    if r is not None:
        return r, True
    raise last_exc or httpx.ConnectError("all retry attempts exhausted")


async def _do_retry(
    fn: Any,
    request: httpx.Request,
    attempts: int,
    backoff: float,
) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            r = await fn(request)
            if r.status_code < 500 or attempt >= attempts - 1:
                return r
            log.warning("retry %d/%d after status %d", attempt + 1, attempts, r.status_code)
            await asyncio.sleep(backoff * (2 ** attempt))
        except (httpx.ConnectError, httpx.ReadError, httpx.WriteError) as exc:
            last_exc = exc
            if attempt >= attempts - 1:
                raise
            log.warning("retry %d/%d after %s", attempt + 1, attempts, type(exc).__name__)
            await asyncio.sleep(backoff * (2 ** attempt))
    raise last_exc or httpx.ConnectError("all retry attempts exhausted")
