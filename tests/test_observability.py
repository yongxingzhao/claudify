"""Tests for batch-2 stability/observability features:

- structured logging + request_id passthrough
- /metrics endpoint
- bounded retry on 502/503/504
- synthetic message_stop on mid-stream upstream failure
- per-phase httpx.Timeout from settings
"""

from __future__ import annotations

import json

import httpx
import pytest

from claudify.app import _post_with_retry, _synthetic_stop_events, create_app
from claudify.settings import Settings


# ---------- request_id (#4) -------------------------------------------------


@pytest.mark.asyncio
async def test_request_id_generated_when_missing(make_client, noop_handler):
    client, app = make_client(noop_handler)
    async with client, app.router.lifespan_context(app):
        r = await client.get("/health")
        assert r.status_code == 200
        rid = r.headers.get("x-request-id")
        assert rid and len(rid) >= 16


@pytest.mark.asyncio
async def test_request_id_preserved_from_header(make_client, noop_handler):
    client, app = make_client(noop_handler)
    async with client, app.router.lifespan_context(app):
        r = await client.get("/health", headers={"x-request-id": "abc-123"})
        assert r.headers["x-request-id"] == "abc-123"


@pytest.mark.asyncio
async def test_request_id_forwarded_upstream(make_client):
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["rid"] = req.headers.get("x-request-id", "")
        return httpx.Response(
            200,
            json={
                "id": "x",
                "model": "hermes-agent",
                "choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    client, app = make_client(handler)
    async with client, app.router.lifespan_context(app):
        r = await client.post(
            "/v1/messages",
            headers={"x-request-id": "trace-xyz"},
            json={"model": "claude-opus-4-7", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200
        assert seen["rid"] == "trace-xyz"


# ---------- /metrics (#5) ---------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_endpoint_renders_prometheus(make_client, noop_handler):
    client, app = make_client(noop_handler)
    async with client, app.router.lifespan_context(app):
        await client.get("/health")
        await client.get("/health")
        r = await client.get("/metrics")
        assert r.status_code == 200
        assert "text/plain" in r.headers["content-type"]
        body = r.text
        assert "claudify_requests_total" in body
        assert 'route="/health"' in body
        assert "claudify_request_latency_seconds_bucket" in body
        assert "claudify_request_latency_seconds_count" in body


@pytest.mark.asyncio
async def test_metrics_records_upstream_status(make_client):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"type": "service_unavailable"}})

    client, app = make_client(handler)
    async with client, app.router.lifespan_context(app):
        r = await client.post(
            "/v1/messages",
            json={"model": "claude-opus-4-7", "messages": [{"role": "user", "content": "x"}]},
        )
        assert r.status_code == 503
        m = await client.get("/metrics")
        assert "claudify_upstream_responses_total" in m.text
        assert 'status="5xx"' in m.text


# ---------- retry (#6) ------------------------------------------------------


@pytest.mark.asyncio
async def test_post_with_retry_succeeds_after_transient_503():
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, json={"error": "transient"})
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as c:
        r = await _post_with_retry(
            c,
            url="http://upstream/v1/chat/completions",
            json={},
            headers={},
            timeout=httpx.Timeout(5.0),
            attempts=3,
            backoff=0.001,
        )
        assert r.status_code == 200
        assert calls["n"] == 3


@pytest.mark.asyncio
async def test_post_with_retry_gives_up_after_attempts():
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(502, json={"error": "always"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as c:
        r = await _post_with_retry(
            c,
            url="http://upstream/v1/chat/completions",
            json={},
            headers={},
            timeout=httpx.Timeout(5.0),
            attempts=2,
            backoff=0.001,
        )
        assert r.status_code == 502
        assert calls["n"] == 3  # initial + 2 retries


@pytest.mark.asyncio
async def test_retry_disabled_by_default(make_client):
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={"error": "x"})

    client, app = make_client(handler)  # retry_attempts defaults to 0
    async with client, app.router.lifespan_context(app):
        r = await client.post(
            "/v1/messages",
            json={"model": "claude-opus-4-7", "messages": [{"role": "user", "content": "x"}]},
        )
        assert r.status_code == 503
        assert calls["n"] == 1


@pytest.mark.asyncio
async def test_retry_engaged_via_settings(make_client):
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(502, json={"error": "x"})
        return httpx.Response(
            200,
            json={
                "id": "x",
                "model": "hermes-agent",
                "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    client, app = make_client(handler, retry_attempts=2, retry_backoff=0.001)
    async with client, app.router.lifespan_context(app):
        r = await client.post(
            "/v1/messages",
            json={"model": "claude-opus-4-7", "messages": [{"role": "user", "content": "x"}]},
        )
        assert r.status_code == 200
        assert calls["n"] == 2


# ---------- synthetic stream stop (#8) --------------------------------------


def test_synthetic_stop_events_well_formed():
    events = _synthetic_stop_events()
    assert len(events) == 2
    for ev in events:
        assert ev.startswith(b"event: ")
        assert b"data: " in ev
        assert ev.endswith(b"\n\n")
    delta_line = [line for line in events[0].split(b"\n") if line.startswith(b"data: ")][0]
    payload = json.loads(delta_line[len(b"data: "):])
    assert payload["delta"]["stop_reason"] == "end_turn"


# ---------- timeout (#9) ----------------------------------------------------


def test_settings_httpx_timeout_uses_request_timeout_fallback():
    s = Settings(
        backend_base="http://upstream/v1",
        api_key="sk-test",
        request_timeout=12.0,
    )
    t = s.httpx_timeout()
    assert t.connect == 12.0
    assert t.read == 12.0
    assert t.write == 12.0


def test_settings_httpx_timeout_streaming_disables_read():
    s = Settings(
        backend_base="http://upstream/v1",
        api_key="sk-test",
        request_timeout=12.0,
        read_timeout=5.0,
    )
    t = s.httpx_timeout(streaming=True)
    assert t.connect == 12.0
    assert t.read is None


def test_settings_httpx_timeout_per_phase_overrides():
    s = Settings(
        backend_base="http://upstream/v1",
        api_key="sk-test",
        connect_timeout=2.0,
        read_timeout=20.0,
        write_timeout=3.0,
        pool_timeout=4.0,
    )
    t = s.httpx_timeout()
    assert t.connect == 2.0
    assert t.read == 20.0
    assert t.write == 3.0
    assert t.pool == 4.0


# ---------- body size limit -------------------------------------------------


@pytest.mark.asyncio
async def test_body_size_limit_rejects_oversized():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    transport = httpx.MockTransport(handler)
    upstream = httpx.AsyncClient(transport=transport)
    s = Settings(
        backend_base="http://upstream/v1",
        api_key="sk-test",
        max_body_size=100,
    )
    app = create_app(s, http_client=upstream)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")

    async with client, app.router.lifespan_context(app):
        big_body = {"model": "x", "messages": [{"role": "user", "content": "x" * 200}]}
        r = await client.post("/v1/messages", json=big_body)
        assert r.status_code == 413
        assert r.json()["error"]["type"] == "invalid_request_error"
        assert "too large" in r.json()["error"]["message"]
