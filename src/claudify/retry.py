"""Retry logic for upstream HTTP requests."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx

log = logging.getLogger("claudify.retry")

# Cap backoff to avoid excessively long waits
MAX_BACKOFF = 30.0


def _backoff_time(base: float, attempt: int) -> float:
    """Calculate backoff with exponential increase, capped at MAX_BACKOFF."""
    return min(base * (2 ** attempt), MAX_BACKOFF)


def _compute_wait(backoff: float, attempt: int, response: httpx.Response | None = None) -> float:
    """Compute wait time, respecting Retry-After header on 429 responses."""
    wait = _backoff_time(backoff, attempt)
    if response is not None and response.status_code == 429:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                wait = max(wait, float(retry_after))
            except ValueError:
                pass
    return wait


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
            if r.status_code < 500 and r.status_code != 429:
                return r, attempt > 0
            if attempt < attempts - 1:
                wait = _compute_wait(backoff, attempt, r)
                await r.aclose()
                log.warning("stream retry %d/%d after status %d", attempt + 1, attempts, r.status_code)
                await asyncio.sleep(wait)
        except (httpx.ConnectError, httpx.ReadError, httpx.WriteError) as exc:
            last_exc = exc
            if attempt < attempts - 1:
                log.warning("stream retry %d/%d after %s", attempt + 1, attempts, type(exc).__name__)
                await asyncio.sleep(_compute_wait(backoff, attempt))
    if r is not None:
        return r, True
    raise last_exc or httpx.ConnectError("all retry attempts exhausted")


async def _do_retry(
    fn: Callable[[httpx.Request], Awaitable[httpx.Response]],
    request: httpx.Request,
    attempts: int,
    backoff: float,
) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            r = await fn(request)
            if (r.status_code < 500 and r.status_code != 429) or attempt >= attempts - 1:
                return r
            log.warning("retry %d/%d after status %d", attempt + 1, attempts, r.status_code)
            await r.aclose()
            await asyncio.sleep(_compute_wait(backoff, attempt, r))
        except (httpx.ConnectError, httpx.ReadError, httpx.WriteError) as exc:
            last_exc = exc
            if attempt >= attempts - 1:
                raise
            log.warning("retry %d/%d after %s", attempt + 1, attempts, type(exc).__name__)
            await asyncio.sleep(_compute_wait(backoff, attempt))
    raise last_exc or httpx.ConnectError("all retry attempts exhausted")
