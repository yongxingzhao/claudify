"""FastAPI application: Anthropic Messages API proxy to OpenAI backend."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

from claudify.conversion import (
    _sse,
    _sse_ping,
    _synthetic_stop_events,
    anthropic_to_openai,
    map_model,
    openai_to_anthropic_response,
    stream_openai_to_anthropic,
)
from claudify.settings import Settings

log = logging.getLogger("claudify")


class _Metrics:
    def __init__(self) -> None:
        self._requests: dict[str, int] = {}
        self._latencies: list[float] = []
        self._upstream: dict[str, int] = {}

    def record_request(self, route: str, latency: float, upstream_status: int | None = None) -> None:
        self._requests[route] = self._requests.get(route, 0) + 1
        self._latencies.append(latency)
        if upstream_status is not None:
            bucket = "2xx" if 200 <= upstream_status < 300 else "4xx" if 400 <= upstream_status < 500 else "5xx"
            self._upstream[bucket] = self._upstream.get(bucket, 0) + 1

    def render(self) -> str:
        lines: list[str] = []
        for route, count in sorted(self._requests.items()):
            lines.append(f'claudify_requests_total{{route="{route}"}} {count}')
        bucket_bounds = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
        lats = sorted(self._latencies)
        for bound in bucket_bounds:
            cnt = sum(1 for l in lats if l <= bound)
            lines.append(f"claudify_request_latency_seconds_bucket{{le={bound}}} {cnt}")
        lines.append(f"claudify_request_latency_seconds_bucket{{le=+Inf}} {len(lats)}")
        lines.append(f"claudify_request_latency_seconds_count {len(lats)}")
        if lats:
            lines.append(f"claudify_request_latency_seconds_sum {sum(lats):.6f}")
        for bucket, count in sorted(self._upstream.items()):
            lines.append(f'claudify_upstream_responses_total{{status="{bucket}"}} {count}')
        return "\n".join(lines) + "\n"


_ANTHROPIC_ERROR_MAP: dict[int, str] = {
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


def _sanitize_error_message(msg: str) -> str:
    for pattern in ("http://", "https://", "api_key=", "sk-", "Bearer "):
        idx = msg.lower().find(pattern.lower())
        if idx != -1:
            msg = msg[:idx] + "...[redacted]"
    return msg.strip() or "Upstream error"


def _passthrough_error(status: int, body: dict[str, Any]) -> JSONResponse:
    error_type = _ANTHROPIC_ERROR_MAP.get(status, "api_error")
    upstream_msg = ""
    if isinstance(body.get("error"), dict):
        upstream_msg = body["error"].get("message", "")
    elif isinstance(body.get("error"), str):
        upstream_msg = body["error"]
    elif body.get("detail"):
        upstream_msg = str(body["detail"])
    message = _sanitize_error_message(upstream_msg) if upstream_msg else f"Upstream returned {status}"
    return JSONResponse(
        status_code=status,
        content={"type": "error", "error": {"type": error_type, "message": message}},
    )


async def _post_with_retry(
    client: httpx.AsyncClient,
    *,
    url: str,
    json: dict[str, Any],
    headers: dict[str, str],
    timeout: httpx.Timeout,
    attempts: int,
    backoff: float,
) -> httpx.Response:
    last: httpx.Response | None = None
    max_tries = 1 + attempts
    for i in range(max_tries):
        r = await client.post(url, json=json, headers=headers, timeout=timeout)
        if r.status_code not in (502, 503, 504) or i == max_tries - 1:
            return r
        last = r
        if i < max_tries - 1:
            import asyncio
            await asyncio.sleep(backoff * (2 ** i))
    return last  # type: ignore[return-value]


async def _stream_with_retry(
    client: httpx.AsyncClient,
    *,
    url: str,
    json: dict[str, Any],
    headers: dict[str, str],
    timeout: httpx.Timeout,
    attempts: int,
    backoff: float,
) -> httpx.Response:
    max_tries = 1 + attempts
    last_req = None
    last_resp = None
    for i in range(max_tries):
        req = client.build_request("POST", url, json=json, headers=headers, timeout=timeout)
        r = await client.send(req, stream=True)
        if r.status_code not in (502, 503, 504) or i == max_tries - 1:
            return r
        await r.aclose()
        last_req = req
        last_resp = r
        if i < max_tries - 1:
            import asyncio
            await asyncio.sleep(backoff * (2 ** i))
    # Should not reach here, but just in case
    raise httpx.HTTPStatusError("max retries exhausted", request=last_req or req, response=last_resp or r)


def create_app(settings: Settings, *, http_client: httpx.AsyncClient | None = None) -> FastAPI:
    app = FastAPI(title="claudify", docs_url=None, redoc_url=None)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    metrics = _Metrics()
    client = http_client or httpx.AsyncClient()

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next: Any) -> Any:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = rid
        t0 = time.monotonic()
        response = await call_next(request)
        elapsed = time.monotonic() - t0
        response.headers["x-request-id"] = rid
        route = request.url.path
        metrics.record_request(route, elapsed)
        return response

    @app.get("/health")
    async def health() -> dict[str, Any]:
        result: dict[str, Any] = {"status": "ok"}
        if settings.upstream_health_path:
            try:
                r = await client.get(
                    f"{settings.backend_base.rstrip('/')}/{settings.upstream_health_path.lstrip('/')}",
                    timeout=httpx.Timeout(5.0),
                )
                result["upstream"] = "ok" if r.status_code == 200 else f"error:{r.status_code}"
            except Exception as exc:
                result["upstream"] = f"unreachable:{exc}"
        return result

    @app.get("/metrics")
    async def metrics_endpoint() -> Response:
        return Response(content=metrics.render(), media_type="text/plain")

    @app.get("/v1/models")
    async def list_models() -> dict[str, Any]:
        models = list(settings.model_map.keys())
        if settings.default_model and settings.default_model not in models:
            models.append(settings.default_model)
        if not models:
            models = [settings.default_model or "default"]
        return {
            "object": "list",
            "data": [{"id": m, "object": "model", "owned_by": "claudify"} for m in models],
        }

    @app.post("/v1/messages")
    async def messages(request: Request) -> Response:
        raw = await request.body()
        if len(raw) > settings.max_body_size:
            return JSONResponse(
                status_code=413,
                content={
                    "type": "error",
                    "error": {
                        "type": "invalid_request_error",
                        "message": f"Request body too large ({len(raw)} bytes, max {settings.max_body_size})",
                    },
                },
            )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return JSONResponse(
                status_code=400,
                content={"type": "error", "error": {"type": "invalid_request_error", "message": "Invalid JSON"}},
            )

        original_model = payload.get("model", "")
        openai_payload = anthropic_to_openai(payload, settings.model_map, settings.default_model)
        is_stream = openai_payload.get("stream", False)
        timeout = settings.httpx_timeout(streaming=is_stream)

        upstream_headers: dict[str, str] = {"Content-Type": "application/json"}
        if settings.api_key:
            upstream_headers["Authorization"] = f"Bearer {settings.api_key}"
        rid = getattr(request.state, "request_id", "")
        if rid:
            upstream_headers["x-request-id"] = rid
        for hdr in ("anthropic-beta", "anthropic-version"):
            val = request.headers.get(hdr)
            if val:
                upstream_headers[hdr] = val

        url = f"{settings.backend_base.rstrip('/')}/chat/completions"

        if is_stream:
            return await _handle_stream(client, url, openai_payload, upstream_headers, timeout,
                                        settings, original_model, metrics)
        return await _handle_non_stream(client, url, openai_payload, upstream_headers, timeout,
                                        settings, original_model, metrics)

    async def _handle_stream(
        client: httpx.AsyncClient, url: str, openai_payload: dict, upstream_headers: dict,
        timeout: httpx.Timeout, settings: Settings, original_model: str, metrics: _Metrics,
    ) -> Response:
        try:
            if settings.retry_attempts > 0:
                upstream = await _stream_with_retry(
                    client, url=url, json=openai_payload, headers=upstream_headers,
                    timeout=timeout, attempts=settings.retry_attempts, backoff=settings.retry_backoff,
                )
            else:
                req = client.build_request("POST", url, json=openai_payload, headers=upstream_headers, timeout=timeout)
                upstream = await client.send(req, stream=True)
        except httpx.HTTPStatusError as exc:
            await exc.response.aclose()
            return _passthrough_error(exc.response.status_code, {})
        except httpx.RequestError as exc:
            return JSONResponse(status_code=502, content={"type": "error", "error": {"type": "api_error", "message": f"upstream unavailable: {exc}"}})

        if upstream.status_code >= 400:
            body = await upstream.aread()
            await upstream.aclose()
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = {}
            metrics.record_request("/v1/messages", 0, upstream.status_code)
            return _passthrough_error(upstream.status_code, parsed)

        async def _generate() -> AsyncIterator[bytes]:
            try:
                async for chunk in stream_openai_to_anthropic(upstream.aiter_bytes(), original_model):
                    yield chunk
            finally:
                await upstream.aclose()

        return StreamingResponse(content=_generate(), media_type="text/event-stream")

    async def _handle_non_stream(
        client: httpx.AsyncClient, url: str, openai_payload: dict, upstream_headers: dict,
        timeout: httpx.Timeout, settings: Settings, original_model: str, metrics: _Metrics,
    ) -> Response:
        try:
            if settings.retry_attempts > 0:
                upstream = await _post_with_retry(
                    client, url=url, json=openai_payload, headers=upstream_headers,
                    timeout=timeout, attempts=settings.retry_attempts, backoff=settings.retry_backoff,
                )
            else:
                upstream = await client.post(url, json=openai_payload, headers=upstream_headers, timeout=timeout)
        except httpx.RequestError as exc:
            return JSONResponse(status_code=502, content={"type": "error", "error": {"type": "api_error", "message": f"upstream unavailable: {exc}"}})

        if upstream.status_code >= 400:
            metrics.record_request("/v1/messages", 0, upstream.status_code)
            return _passthrough_error(upstream.status_code, upstream.json())

        metrics.record_request("/v1/messages", 0, upstream.status_code)
        anthropic_resp = openai_to_anthropic_response(upstream.json(), original_model)
        return JSONResponse(content=anthropic_resp)

    @app.post("/v1/messages/count_tokens")
    async def count_tokens(request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return JSONResponse(
                status_code=400,
                content={"type": "error", "error": {"type": "invalid_request_error", "message": "Invalid JSON"}},
            )
        text = payload.get("messages", [{}])
        total_chars = sum(len(str(m)) for m in text)
        return {"input_tokens": max(1, total_chars // 4)}

    @app.on_event("shutdown")
    async def shutdown() -> None:
        await client.aclose()

    return app


def create_app_from_settings() -> FastAPI:
    s = Settings.load()
    return create_app(s)
