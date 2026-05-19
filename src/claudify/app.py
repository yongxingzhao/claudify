"""FastAPI application: Anthropic Messages API proxy to OpenAI backend."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse

from claudify.conversion import (
    _sse,
    _synthetic_stop_events,
    anthropic_to_openai,
    map_model,
    openai_to_anthropic_response,
    stream_openai_to_anthropic,
)
from claudify.settings import Settings

log = logging.getLogger("claudify")

_ERROR_TYPE_MAP: dict[int, str] = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    429: "rate_limit_error",
    500: "api_error",
    502: "api_error",
    503: "overloaded_error",
    504: "api_error",
}

_MAX_LATENCY = 10000


class _Metrics:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._latencies: deque[tuple[float, str]] = deque(maxlen=_MAX_LATENCY)
        self._upstream: dict[str, int] = {}

    def record_request(self, route: str, latency: float, upstream_status: int = 0) -> None:
        self._counts[route] = self._counts.get(route, 0) + 1
        self._latencies.append((latency, route))
        if upstream_status:
            bucket = f"{upstream_status // 100}xx"
            key = f"{route}:{bucket}"
            self._upstream[key] = self._upstream.get(key, 0) + 1

    def render(self) -> str:
        lines: list[str] = []
        for route, count in sorted(self._counts.items()):
            lines.append(f'claudify_requests_total{{route="{route}"}} {count}')
        buckets = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
        by_route: dict[str, list[float]] = {}
        for lat, route in self._latencies:
            by_route.setdefault(route, []).append(lat)
        if not by_route and self._counts:
            for route in sorted(self._counts):
                lines.append(f'claudify_request_latency_seconds_count{{route="{route}"}} 0')
        if not by_route and self._counts:
            for route in sorted(self._counts):
                lines.append(f'claudify_request_latency_seconds_count{{route="{route}"}} 0')
        for route in sorted(by_route):
            lats = by_route[route]
            for b in buckets:
                cnt = sum(1 for l in lats if l <= b)
                lines.append(f'claudify_request_latency_seconds_bucket{{le="{b}",route="{route}"}} {cnt}')
            lines.append(f'claudify_request_latency_seconds_bucket{{le="+Inf",route="{route}"}} {len(lats)}')
            total = sum(lats)
            lines.append(f'claudify_request_latency_seconds_sum{{route="{route}"}} {total:.6f}')
            lines.append(f'claudify_request_latency_seconds_count{{route="{route}"}} {len(lats)}')
        for key, count in sorted(self._upstream.items()):
            route, bucket = key.rsplit(":", 1)
            lines.append(f'claudify_upstream_responses_total{{route="{route}",status="{bucket}"}} {count}')
        return "\n".join(lines) + "\n"


def _sanitize_error_message(msg: str) -> str:
    msg = re.sub(r'\bsk-[A-Za-z0-9]{8,}\b', '[redacted-key]', msg)
    msg = re.sub(r'https?://[^\s"\'<>]+', '[redacted-url]', msg)
    return msg


def _passthrough_error(status: int, upstream_body: bytes | None = None) -> Response:
    error_type = _ERROR_TYPE_MAP.get(status, "api_error")
    message = f"Upstream returned {status}"
    if upstream_body:
        try:
            body = json.loads(upstream_body)
            err = body if isinstance(body, dict) else {}
            raw_msg = (err.get("error") or {}).get("message", "") if isinstance(err.get("error"), dict) else str(err)
            if raw_msg:
                message = _sanitize_error_message(raw_msg)
        except (json.JSONDecodeError, AttributeError):
            snippet = upstream_body[:200].decode("utf-8", errors="replace").strip()
            if snippet:
                message = _sanitize_error_message(f"Upstream {status}: {snippet}")
    return Response(
        content=json.dumps({"type": "error", "error": {"type": error_type, "message": message}}),
        status_code=status,
        media_type="application/json",
    )


def _post_with_retry(
    client: httpx.AsyncClient,
    request: httpx.Request,
    attempts: int,
    backoff: float,
) -> Any:
    return _do_retry(client.send, request, attempts, backoff)


async def _stream_with_retry(
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
                await asyncio.sleep(backoff * (2 ** attempt))
        except (httpx.ConnectError, httpx.ReadError, httpx.WriteError) as exc:
            last_exc = exc
            if attempt < attempts - 1:
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
            await asyncio.sleep(backoff * (2 ** attempt))
        except (httpx.ConnectError, httpx.ReadError, httpx.WriteError) as exc:
            last_exc = exc
            if attempt >= attempts - 1:
                raise
            await asyncio.sleep(backoff * (2 ** attempt))
    raise last_exc or httpx.ConnectError("all retry attempts exhausted")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    yield
    if hasattr(app.state, "http_client") and app.state.http_client:
        await app.state.http_client.aclose()


def create_app(settings: Settings | None = None, *, http_client: httpx.AsyncClient | None = None) -> FastAPI:
    settings = settings or Settings.load()
    metrics = _Metrics()

    app = FastAPI(lifespan=_lifespan)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next: Any) -> Response:
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        return response

    client = http_client or httpx.AsyncClient(
        base_url=settings.backend_base,
        timeout=settings.httpx_timeout(),
    )
    app.state.http_client = client

    def _build_headers(request: Request) -> dict[str, str]:
        hdrs: dict[str, str] = {"Content-Type": "application/json"}
        api_key = settings.api_key
        x_api_key = request.headers.get("x-api-key")
        auth_header = request.headers.get("authorization")
        if x_api_key:
            hdrs["Authorization"] = f"Bearer {x_api_key}"
        elif auth_header:
            hdrs["Authorization"] = auth_header
        elif api_key:
            hdrs["Authorization"] = f"Bearer {api_key}"
        for h in ("anthropic-beta", "anthropic-version"):
            v = request.headers.get(h)
            if v:
                hdrs[h] = v
        if "anthropic-version" not in hdrs:
            hdrs["anthropic-version"] = "2023-06-01"
        return hdrs

    @app.post("/v1/messages")
    async def messages(request: Request) -> Response:
        t0 = time.monotonic()
        rid = getattr(request.state, "request_id", "")

        body = await request.body()
        if len(body) > settings.max_body_size:
            return Response(
                content=json.dumps({"type": "error", "error": {"type": "invalid_request_error", "message": "Request body too large"}}),
                status_code=413,
                media_type="application/json",
            )

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return Response(
                content=json.dumps({"type": "error", "error": {"type": "invalid_request_error", "message": "Invalid JSON"}}),
                status_code=400,
                media_type="application/json",
            )

        if not isinstance(payload, dict):
            return Response(
                content=json.dumps({"type": "error", "error": {"type": "invalid_request_error", "message": "Request must be a JSON object"}}),
                status_code=400,
                media_type="application/json",
            )
        if "model" not in payload:
            return Response(
                content=json.dumps({"type": "error", "error": {"type": "invalid_request_error", "message": "Missing required field: model"}}),
                status_code=400,
                media_type="application/json",
            )
        if "messages" not in payload:
            return Response(
                content=json.dumps({"type": "error", "error": {"type": "invalid_request_error", "message": "Missing required field: messages"}}),
                status_code=400,
                media_type="application/json",
            )

        openai_payload = anthropic_to_openai(payload, settings.model_map, settings.default_model)
        is_stream = openai_payload.get("stream", False)
        upstream_path = "/chat/completions"
        headers = _build_headers(request)

        log.info("rid=%s model=%s -> %s stream=%s", rid, payload.get("model"), openai_payload.get("model"), is_stream)

        req = client.build_request("POST", upstream_path, json=openai_payload, headers=headers)

        try:
            if is_stream:
                if settings.retry_attempts > 1:
                    r, retried = await _stream_with_retry(client, req, settings.retry_attempts, settings.retry_backoff)
                else:
                    r = await client.send(req, stream=True)
                    retried = False
                r.raise_for_status()

                async def _generate() -> AsyncIterator[bytes]:
                    try:
                        async for chunk in stream_openai_to_anthropic(
                            r.aiter_bytes(), payload.get("model", "")
                        ):
                            yield chunk
                    except Exception:
                        log.warning("rid=%s stream interrupted", rid)
                        for ev in _synthetic_stop_events("stop", None):
                            yield ev
                    finally:
                        await r.aclose()

                return StreamingResponse(_generate(), media_type="text/event-stream")
            else:
                if settings.retry_attempts > 1:
                    r = await _post_with_retry(client, req, settings.retry_attempts, settings.retry_backoff)
                else:
                    r = await client.send(req)
                r.raise_for_status()
                data = r.json()

        except httpx.HTTPStatusError as exc:
            elapsed = time.monotonic() - t0
            metrics.record_request("/v1/messages", elapsed, exc.response.status_code)
            log.warning("rid=%s upstream %d", rid, exc.response.status_code)
            return _passthrough_error(exc.response.status_code, exc.response.content)
        except (httpx.ConnectError, httpx.ReadError, httpx.WriteError) as exc:
            elapsed = time.monotonic() - t0
            metrics.record_request("/v1/messages", elapsed, 502)
            log.error("rid=%s upstream unavailable: %s", rid, exc)
            return _passthrough_error(502)

        elapsed = time.monotonic() - t0
        metrics.record_request("/v1/messages", elapsed, r.status_code)
        result = openai_to_anthropic_response(data, payload.get("model", ""))
        return Response(content=json.dumps(result), media_type="application/json")

    @app.get("/v1/models")
    async def list_models() -> dict[str, Any]:
        import time as _time
        ids = list(settings.model_map.keys())
        if settings.default_model and settings.default_model not in ids:
            ids.append(settings.default_model)
        if not ids:
            ids = ["default"]
        now = int(_time.time())
        return {
            "object": "list",
            "data": [{"id": m, "object": "model", "created": now, "owned_by": "claudify"} for m in ids],
        }

    @app.get("/health")
    async def health() -> dict[str, Any]:
        result: dict[str, Any] = {"status": "ok"}
        if settings.upstream_health_path:
            try:
                r = await client.get(settings.upstream_health_path)
                result["upstream"] = "ok" if r.status_code < 400 else "degraded"
            except Exception:
                result["upstream"] = "unreachable"
        return result

    @app.get("/metrics")
    async def metrics_endpoint() -> Response:
        return Response(content=metrics.render(), media_type="text/plain")

    @app.post("/v1/messages/count_tokens")
    async def count_tokens(request: Request) -> Response:
        body = await request.body()
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return Response(
                content=json.dumps({"type": "error", "error": {"type": "invalid_request_error", "message": "Invalid JSON"}}),
                status_code=400,
                media_type="application/json",
            )
        messages = payload.get("messages", [])
        total_chars = sum(len(m.get("content", "")) if isinstance(m.get("content"), str) else len(str(m.get("content", ""))) for m in messages)
        total_words = sum(
            len(m.get("content", "").split()) if isinstance(m.get("content"), str) else 0
            for m in messages
        )
        estimated = max(total_words, total_chars // 4)
        return Response(
            content=json.dumps({"input_tokens": estimated}),
            media_type="application/json",
        )

    return app