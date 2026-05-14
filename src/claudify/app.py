"""FastAPI app factory."""

from __future__ import annotations

import asyncio
import json as _json
import logging
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from .conversion import (
    anthropic_to_openai,
    extract_text_from_blocks,
    openai_to_anthropic_response,
    stream_openai_to_anthropic,
)
from .settings import Settings

# Upstream HTTP status -> Anthropic error type. (#14 — full table lands in batch 3,
# but we already give 5xx a sane default here.)
_RETRYABLE_STATUS = {502, 503, 504}


# ---------- Metrics (#5) -----------------------------------------------------

# Lightweight in-process counters/histogram. Exposed via /metrics in Prometheus
# text format. Not a substitute for a real registry, but adequate for a single
# proxy process and avoids an extra dependency.
_LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)


class _Metrics:
    def __init__(self) -> None:
        self.requests_total: dict[tuple[str, str, str], int] = {}  # (method, route, status_class) -> count
        self.upstream_total: dict[tuple[str, str], int] = {}  # (route, status_class) -> count
        self.latency_buckets: dict[str, list[int]] = {}  # route -> [bucket_counts...] + [+Inf]
        self.latency_sum: dict[str, float] = {}
        self.latency_count: dict[str, int] = {}

    def observe(self, *, method: str, route: str, status: int, latency_s: float) -> None:
        cls = f"{status // 100}xx"
        key = (method, route, cls)
        self.requests_total[key] = self.requests_total.get(key, 0) + 1
        buckets = self.latency_buckets.setdefault(route, [0] * (len(_LATENCY_BUCKETS) + 1))
        for i, b in enumerate(_LATENCY_BUCKETS):
            if latency_s <= b:
                buckets[i] += 1
        buckets[-1] += 1  # +Inf
        self.latency_sum[route] = self.latency_sum.get(route, 0.0) + latency_s
        self.latency_count[route] = self.latency_count.get(route, 0) + 1

    def upstream(self, *, route: str, status: int) -> None:
        cls = f"{status // 100}xx"
        key = (route, cls)
        self.upstream_total[key] = self.upstream_total.get(key, 0) + 1

    def render(self) -> str:
        lines: list[str] = []
        lines.append("# HELP claudify_requests_total Total HTTP requests handled.")
        lines.append("# TYPE claudify_requests_total counter")
        for (method, route, cls), n in sorted(self.requests_total.items()):
            lines.append(f'claudify_requests_total{{method="{method}",route="{route}",status="{cls}"}} {n}')
        lines.append("# HELP claudify_upstream_responses_total Upstream responses by status class.")
        lines.append("# TYPE claudify_upstream_responses_total counter")
        for (route, cls), n in sorted(self.upstream_total.items()):
            lines.append(f'claudify_upstream_responses_total{{route="{route}",status="{cls}"}} {n}')
        lines.append("# HELP claudify_request_latency_seconds Request latency.")
        lines.append("# TYPE claudify_request_latency_seconds histogram")
        for route, buckets in sorted(self.latency_buckets.items()):
            cumulative = 0
            for i, b in enumerate(_LATENCY_BUCKETS):
                cumulative += buckets[i]
                lines.append(
                    f'claudify_request_latency_seconds_bucket{{route="{route}",le="{b}"}} {cumulative}'
                )
            cumulative += buckets[-1]
            lines.append(f'claudify_request_latency_seconds_bucket{{route="{route}",le="+Inf"}} {cumulative}')
            lines.append(f'claudify_request_latency_seconds_sum{{route="{route}"}} {self.latency_sum[route]}')
            lines.append(
                f'claudify_request_latency_seconds_count{{route="{route}"}} {self.latency_count[route]}'
            )
        return "\n".join(lines) + "\n"


# ---------- Error passthrough -----------------------------------------------


def _passthrough_error(body: bytes, status_code: int) -> dict:
    """Translate an upstream error body into an Anthropic-shaped error dict."""
    text = body.decode("utf-8", errors="replace") if body else ""
    try:
        parsed = _json.loads(text)
    except Exception:
        parsed = None

    if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
        err = dict(parsed["error"])
        err.setdefault("type", "upstream_error")
        return {"error": err, "upstream_status": status_code}
    if isinstance(parsed, dict):
        return {
            "error": {"type": "upstream_error", "message": "upstream error"},
            "upstream_status": status_code,
            "upstream_body": parsed,
        }
    return {
        "error": {"type": "upstream_error", "message": text[:2000] or f"http {status_code}"},
        "upstream_status": status_code,
    }


# ---------- Synthetic SSE close (#8) ----------------------------------------


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {_json.dumps(data, ensure_ascii=False)}\n\n".encode()


def _synthetic_stop_events(reason: str = "end_turn") -> list[bytes]:
    """Emit message_delta + message_stop so Anthropic clients can exit cleanly
    when the upstream stream dies mid-flight.

    Note: callers may have already opened content blocks; we don't try to close
    them here because we don't track block index from outside the stream
    converter. Clients tolerate trailing message_delta/message_stop without a
    matching content_block_stop — and a hard close is strictly worse.
    """
    return [
        _sse(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": reason, "stop_sequence": None},
                "usage": {"output_tokens": 0},
            },
        ),
        _sse("message_stop", {"type": "message_stop"}),
    ]


# ---------- Retry (#6) ------------------------------------------------------


async def _post_with_retry(
    client: httpx.AsyncClient,
    *,
    url: str,
    json: dict,
    headers: dict,
    timeout: httpx.Timeout,
    attempts: int,
    backoff: float,
) -> httpx.Response:
    """POST with bounded exponential-backoff retry on 502/503/504 and connect/read errors.

    `attempts` is the number of retries on top of the initial request, so total
    requests = attempts + 1. attempts=0 (default) means no retry.
    """
    last_exc: Exception | None = None
    for i in range(attempts + 1):
        try:
            resp = await client.post(url, json=json, headers=headers, timeout=timeout)
            if resp.status_code in _RETRYABLE_STATUS and i < attempts:
                # Drain body so the connection can be reused.
                await resp.aread()
                await resp.aclose()
                await _sleep_backoff(backoff, i)
                continue
            return resp
        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as e:
            last_exc = e
            if i < attempts:
                await _sleep_backoff(backoff, i)
                continue
            raise
    # Unreachable: either we returned a response or re-raised.
    assert last_exc is not None
    raise last_exc


async def _sleep_backoff(base: float, attempt: int) -> None:
    delay = base * (2**attempt)
    # Decorrelated jitter to avoid thundering herd.
    delay = random.uniform(base, max(base, delay))
    await asyncio.sleep(delay)


# ---------- App factory ------------------------------------------------------


def create_app(settings: Settings | None = None, *, http_client: httpx.AsyncClient | None = None) -> FastAPI:
    s = settings or Settings.load()
    log = logging.getLogger("claudify")
    metrics = _Metrics()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if http_client is not None:
            app.state.http = http_client
            app.state.owns_http = False
        else:
            app.state.http = httpx.AsyncClient(timeout=s.httpx_timeout())
            app.state.owns_http = True
        try:
            yield
        finally:
            if app.state.owns_http:
                await app.state.http.aclose()

    app = FastAPI(title="claudify", version="0.1.0", lifespan=lifespan)
    app.state.metrics = metrics

    # ---------- Middleware: request_id + structured log + metrics (#4, #5) ----

    @app.middleware("http")
    async def observability(request: Request, call_next: Callable[[Request], Awaitable[Response]]):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = rid
        route = request.url.path
        start = time.perf_counter()
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        except Exception:
            status = 500
            raise
        finally:
            latency = time.perf_counter() - start
            metrics.observe(method=request.method, route=route, status=status, latency_s=latency)
            log.info(
                "request",
                extra={
                    "request_id": rid,
                    "method": request.method,
                    "route": route,
                    "status": status,
                    "latency_ms": round(latency * 1000, 2),
                },
            )

    @app.middleware("http")
    async def attach_request_id_header(request: Request, call_next: Callable[[Request], Awaitable[Response]]):
        response = await call_next(request)
        rid = getattr(request.state, "request_id", None)
        if rid:
            response.headers["x-request-id"] = rid
        return response

    # ---------- Routes -------------------------------------------------------

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics_endpoint() -> PlainTextResponse:
        return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")

    @app.get("/v1/models")
    async def models() -> dict:
        ids = sorted(set(list(s.model_map.keys()) + ([s.default_model] if s.default_model else [])))
        return {
            "object": "list",
            "data": [
                {"id": m, "object": "model", "created": int(time.time()), "owned_by": "claudify"} for m in ids
            ],
        }

    @app.post("/v1/messages/count_tokens")
    async def count_tokens(request: Request):
        try:
            payload = await request.json()
        except Exception as e:
            return JSONResponse(
                status_code=400,
                content={"error": {"type": "invalid_request_error", "message": f"invalid JSON: {e}"}},
            )
        chars = 0
        sys_field = payload.get("system")
        if isinstance(sys_field, str):
            chars += len(sys_field)
        elif isinstance(sys_field, list):
            chars += len(extract_text_from_blocks(sys_field))
        for msg in payload.get("messages", []) or []:
            chars += len(extract_text_from_blocks(msg.get("content")))
        return {"input_tokens": max(1, chars // 4)}

    @app.post("/v1/messages")
    async def messages(request: Request):
        rid = getattr(request.state, "request_id", "")
        try:
            payload = await request.json()
        except Exception as e:
            return JSONResponse(
                status_code=400,
                content={"error": {"type": "invalid_request_error", "message": f"invalid JSON: {e}"}},
            )

        original_model = payload.get("model", "")
        stream = bool(payload.get("stream", False))
        openai_payload = anthropic_to_openai(payload, s.model_map, s.default_model)
        url = f"{s.backend_base.rstrip('/')}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
        }
        if s.api_key:
            headers["Authorization"] = f"Bearer {s.api_key}"
        if rid:
            headers["X-Request-ID"] = rid

        client: httpx.AsyncClient = request.app.state.http
        route = "/v1/messages"

        try:
            if stream:
                stream_timeout = s.httpx_timeout(streaming=True)
                req = client.build_request(
                    "POST",
                    url,
                    json=openai_payload,
                    headers=headers,
                    timeout=stream_timeout,
                )
                upstream = await client.send(req, stream=True)
                metrics.upstream(route=route, status=upstream.status_code)
                if upstream.status_code >= 400:
                    body = await upstream.aread()
                    await upstream.aclose()
                    return JSONResponse(
                        status_code=upstream.status_code,
                        content=_passthrough_error(body, upstream.status_code),
                    )

                async def relay():
                    upstream_failed = False
                    try:
                        async for chunk in stream_openai_to_anthropic(upstream.aiter_lines(), original_model):
                            yield chunk
                    except (httpx.HTTPError, asyncio.CancelledError) as e:
                        upstream_failed = True
                        log.warning(
                            "stream interrupted",
                            extra={"request_id": rid, "error": f"{type(e).__name__}: {e}"},
                        )
                    finally:
                        if upstream_failed:
                            for ev in _synthetic_stop_events():
                                yield ev
                        await upstream.aclose()

                return StreamingResponse(relay(), media_type="text/event-stream")

            resp = await _post_with_retry(
                client,
                url=url,
                json=openai_payload,
                headers=headers,
                timeout=s.httpx_timeout(),
                attempts=s.retry_attempts,
                backoff=s.retry_backoff,
            )
            metrics.upstream(route=route, status=resp.status_code)
            if resp.status_code >= 400:
                return JSONResponse(
                    status_code=resp.status_code,
                    content=_passthrough_error(resp.content, resp.status_code),
                )
            return JSONResponse(content=openai_to_anthropic_response(resp.json(), original_model))

        except httpx.HTTPError as e:
            log.exception("upstream error", extra={"request_id": rid})
            return JSONResponse(
                status_code=502,
                content={"error": {"type": "upstream_unavailable", "message": f"{type(e).__name__}: {e}"}},
            )
        except Exception as e:
            log.exception("internal error", extra={"request_id": rid})
            return JSONResponse(
                status_code=500,
                content={"error": {"type": "internal_error", "message": str(e)}},
            )

    return app
