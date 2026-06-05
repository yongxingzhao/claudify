"""FastAPI route handlers for Claudify proxy."""

from __future__ import annotations

import hmac
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import Request
from fastapi.responses import Response, StreamingResponse

from claudify.conversion import (
    _system_to_openai,
    anthropic_to_openai,
    extract_text_from_blocks,
    openai_to_anthropic_response,
    stream_openai_to_anthropic,
)
from claudify.errors import make_error_response, passthrough_error
from claudify.metrics import Metrics
from claudify.retry import post_with_retry, stream_with_retry
from claudify.settings import Settings
from claudify.sse import synthetic_stop_events

log = logging.getLogger("claudify")


def _build_headers(request: Request, settings: Settings) -> dict[str, str]:
    hdrs: dict[str, str] = {"Content-Type": "application/json"}
    # When inbound_api_key is set, x-api-key is for proxy auth only — never forward it upstream.
    # Use the configured api_key for upstream authentication instead.
    api_key = settings.api_key
    if settings.inbound_api_key:
        if api_key:
            hdrs["Authorization"] = f"Bearer {api_key}"
    else:
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


def _validate_messages_payload(body: bytes, settings: Settings) -> tuple[dict[str, Any] | None, Response | None]:
    if len(body) > settings.max_body_size:
        return None, make_error_response("invalid_request_error", "Request body too large", 413)

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None, make_error_response("invalid_request_error", "Invalid JSON", 400)

    if not isinstance(payload, dict):
        return None, make_error_response("invalid_request_error", "Request must be a JSON object", 400)
    if "model" not in payload:
        return None, make_error_response("invalid_request_error", "Missing required field: model", 400)
    model = payload.get("model")
    if not isinstance(model, str) or not model or len(model) > 256:
        return None, make_error_response("invalid_request_error", "model must be a non-empty string (max 256 chars)", 400)
    if "messages" not in payload:
        return None, make_error_response("invalid_request_error", "Missing required field: messages", 400)
    if not isinstance(payload["messages"], list):
        return None, make_error_response("invalid_request_error", "messages must be an array", 400)
    if not payload["messages"]:
        return None, make_error_response("invalid_request_error", "messages must not be empty", 400)
    for i, msg in enumerate(payload["messages"]):
        if not isinstance(msg, dict) or "role" not in msg:
            return None, make_error_response("invalid_request_error", f"messages[{i}] must have a \"role\" field", 400)

    return payload, None


def _add_request_id(response: Response, request: Request) -> Response:
    rid = getattr(request.state, "request_id", "")
    if rid:
        response.headers["x-request-id"] = rid
    return response


def _check_inbound_auth(request: Request, settings: Settings) -> Response | None:
    """Validate inbound API key if configured. Returns error response or None."""
    if settings.inbound_api_key:
        key = request.headers.get("x-api-key") or ""
        if not hmac.compare_digest(key, settings.inbound_api_key):
            return make_error_response("authentication_error", "Invalid API key", 401)
    return None


async def _stream_response(
    rid: str, r: httpx.Response, payload: dict[str, Any],
    t0: float, metrics: Metrics,
) -> AsyncIterator[bytes]:
    """Yield SSE chunks from an OpenAI streaming response, converting to Anthropic format."""
    try:
        async for chunk in stream_openai_to_anthropic(
            r.aiter_bytes(), payload.get("model", ""),
        ):
            yield chunk
    except Exception:
        log.warning("rid=%s stream interrupted", rid, exc_info=True)
        for ev in synthetic_stop_events("stop", None):
            yield ev
    finally:
        await r.aclose()
        stream_elapsed = time.monotonic() - t0
        metrics.record_request("/v1/messages", stream_elapsed, r.status_code)
        log.info("rid=%s stream completed in %.3fs status=%d", rid, stream_elapsed, r.status_code)


def _upstream_error_response(
    exc: Exception, rid: str, t0: float, metrics: Metrics,
) -> Response:
    """Convert an upstream httpx exception into an error Response with metrics."""
    if isinstance(exc, httpx.HTTPStatusError):
        elapsed = time.monotonic() - t0
        metrics.record_request("/v1/messages", elapsed, exc.response.status_code)
        log.warning("rid=%s upstream %d", rid, exc.response.status_code)
        return passthrough_error(exc.response.status_code, exc.response.content)
    if isinstance(exc, httpx.TimeoutException):
        elapsed = time.monotonic() - t0
        metrics.record_request("/v1/messages", elapsed, 504)
        log.error("rid=%s upstream timeout: %s", rid, exc)
        return passthrough_error(504)
    # ConnectError, ReadError, WriteError
    elapsed = time.monotonic() - t0
    metrics.record_request("/v1/messages", elapsed, 502)
    log.error("rid=%s upstream unavailable: %s", rid, exc)
    return passthrough_error(502)


async def _handle_streaming(
    request: Request, client: httpx.AsyncClient, payload: dict[str, Any],
    openai_payload: dict[str, Any], headers: dict[str, str],
    settings: Settings, metrics: Metrics, rid: str, t0: float,
) -> Response:
    """Handle a streaming messages request."""
    req = client.build_request(
        "POST", "/chat/completions", json=openai_payload, headers=headers,
        timeout=settings.httpx_timeout(streaming=True),
    )
    try:
        if settings.retry_attempts >= 1:
            r, retried = await stream_with_retry(
                client, req, settings.retry_attempts + 1, settings.retry_backoff,
            )
            if retried:
                log.info("rid=%s stream succeeded after retry", rid)
        else:
            r = await client.send(req, stream=True)
        r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        resp = _upstream_error_response(exc, rid, t0, metrics)
        await exc.response.aclose()
        return _add_request_id(resp, request)
    except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, httpx.WriteError) as exc:
        return _add_request_id(_upstream_error_response(exc, rid, t0, metrics), request)

    resp = StreamingResponse(
        _stream_response(rid, r, payload, t0, metrics),
        media_type="text/event-stream",
    )
    return _add_request_id(resp, request)


async def _handle_nonstreaming(
    request: Request, client: httpx.AsyncClient, payload: dict[str, Any],
    openai_payload: dict[str, Any], headers: dict[str, str],
    settings: Settings, metrics: Metrics, rid: str, t0: float,
) -> Response:
    """Handle a non-streaming messages request."""
    req = client.build_request("POST", "/chat/completions", json=openai_payload, headers=headers)
    try:
        if settings.retry_attempts >= 1:
            r = await post_with_retry(
                client, req, settings.retry_attempts + 1, settings.retry_backoff,
            )
        else:
            r = await client.send(req)
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPStatusError, httpx.TimeoutException,
            httpx.ConnectError, httpx.ReadError, httpx.WriteError) as exc:
        return _add_request_id(_upstream_error_response(exc, rid, t0, metrics), request)

    elapsed = time.monotonic() - t0
    metrics.record_request("/v1/messages", elapsed, r.status_code)
    log.info("rid=%s completed in %.3fs status=%d", rid, elapsed, r.status_code)
    result = openai_to_anthropic_response(data, payload.get("model", ""), settings.model_map)
    resp = Response(content=json.dumps(result), media_type="application/json")
    return _add_request_id(resp, request)


async def messages(request: Request, client: httpx.AsyncClient, settings: Settings, metrics: Metrics) -> Response:
    t0 = time.monotonic()
    rid = getattr(request.state, "request_id", "")

    auth_err = _check_inbound_auth(request, settings)
    if auth_err:
        return _add_request_id(auth_err, request)

    body = await request.body()
    payload, err = _validate_messages_payload(body, settings)
    body = None
    if err:
        return _add_request_id(err, request)

    openai_payload = anthropic_to_openai(payload, settings.model_map, settings.default_model)
    is_stream = openai_payload.get("stream", False)
    headers = _build_headers(request, settings)

    log.info("rid=%s model=%s -> %s stream=%s", rid, payload.get("model"), openai_payload.get("model"), is_stream)

    if is_stream:
        return await _handle_streaming(request, client, payload, openai_payload, headers, settings, metrics, rid, t0)
    return await _handle_nonstreaming(request, client, payload, openai_payload, headers, settings, metrics, rid, t0)


async def list_models(settings: Settings) -> dict[str, Any]:
    ids = list(settings.model_map.keys())
    if settings.default_model and settings.default_model not in ids:
        ids.append(settings.default_model)
    if not ids:
        ids = ["default"]
    now = int(time.time())
    return {
        "object": "list",
        "data": [{"id": m, "object": "model", "created": now, "owned_by": "claudify"} for m in ids],
    }


async def health(client: httpx.AsyncClient, settings: Settings) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "ok"}
    if settings.upstream_health_path:
        try:
            r = await client.get(
                settings.upstream_health_path,
                timeout=httpx.Timeout(connect=3.0, read=5.0, write=3.0, pool=3.0),
            )
            result["upstream"] = "ok" if r.status_code < 400 else "degraded"
        except (httpx.TimeoutException, httpx.ConnectError):
            result["upstream"] = "unreachable"
    return result


async def metrics_endpoint(metrics: Metrics) -> Response:
    return Response(content=metrics.render(), media_type="text/plain")


async def count_tokens(request: Request, settings: Settings) -> Response:
    auth_err = _check_inbound_auth(request, settings)
    if auth_err:
        return auth_err

    body = await request.body()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return make_error_response("invalid_request_error", "Invalid JSON", 400)
    finally:
        body = None
    if not isinstance(payload, dict):
        return make_error_response("invalid_request_error", "Request must be a JSON object", 400)
    msgs = payload.get("messages", [])
    if not isinstance(msgs, list) or not msgs:
        return make_error_response("invalid_request_error", "messages must be a non-empty array", 400)

    total_chars = 0
    total_words = 0

    def _count_text(text: str) -> None:
        nonlocal total_chars, total_words
        if text:
            total_chars += len(text)
            total_words += len(text.split())

    # Count system prompt using shared extraction
    system = payload.get("system")
    if isinstance(system, str) and system:
        _count_text(system)
    elif isinstance(system, list):
        _count_text(_system_to_openai(system))

    # Count messages using shared extraction
    for m in msgs:
        if isinstance(m, dict):
            _count_text(extract_text_from_blocks(m.get("content")))

    estimated = max(total_words, total_chars // 4)
    return Response(
        content=json.dumps({
            "id": f"ctkn_{uuid.uuid4().hex[:24]}",
            "type": "token_count",
            "input_tokens": estimated,
        }),
        media_type="application/json",
    )
