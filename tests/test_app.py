"""Tests for the FastAPI app via httpx.MockTransport."""

from __future__ import annotations

import json

import httpx
import pytest

from claudify.app import create_app
from claudify.settings import Settings


def _settings(**over):
    base = dict(
        backend_base="http://upstream/v1",
        api_key="sk-test",
        host="127.0.0.1",
        port=4000,
        log_level="WARNING",
        request_timeout=10.0,
        model_map={"claude-opus-4-7": "hermes-agent"},
        default_model="",
    )
    base.update(over)
    return Settings(**base)


def _client(handler):
    transport = httpx.MockTransport(handler)
    upstream = httpx.AsyncClient(transport=transport)
    app = create_app(_settings(), http_client=upstream)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver"), app


@pytest.mark.asyncio
async def test_health():
    client, app = _client(lambda r: httpx.Response(500))
    async with client, app.router.lifespan_context(app):
        r = await client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_models_lists_known_ids():
    client, app = _client(lambda r: httpx.Response(500))
    async with client, app.router.lifespan_context(app):
        r = await client.get("/v1/models")
        ids = [m["id"] for m in r.json()["data"]]
        assert "claude-opus-4-7" in ids


@pytest.mark.asyncio
async def test_messages_non_streaming_round_trip():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "pong"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 1},
            },
        )

    client, app = _client(handler)
    async with client, app.router.lifespan_context(app):
        r = await client.post(
            "/v1/messages",
            json={
                "model": "claude-opus-4-7",
                "max_tokens": 32,
                "messages": [{"role": "user", "content": "ping"}],
            },
        )

    assert r.status_code == 200
    body = r.json()
    assert body["model"] == "claude-opus-4-7"
    assert body["content"] == [{"type": "text", "text": "pong"}]
    assert body["usage"] == {"input_tokens": 4, "output_tokens": 1}
    assert captured["url"].endswith("/v1/chat/completions")
    assert captured["auth"] == "Bearer sk-test"
    assert captured["body"]["model"] == "hermes-agent"


@pytest.mark.asyncio
async def test_messages_upstream_error_passthrough():
    upstream_err = {"error": {"type": "rate_limit_error", "message": "slow down"}}

    def handler(request):
        return httpx.Response(429, json=upstream_err)

    client, app = _client(handler)
    async with client, app.router.lifespan_context(app):
        r = await client.post(
            "/v1/messages",
            json={
                "model": "claude-opus-4-7",
                "messages": [{"role": "user", "content": "x"}],
            },
        )

    assert r.status_code == 429
    body = r.json()
    assert body["error"]["type"] == "rate_limit_error"
    assert body["error"]["message"] == "slow down"
    assert body["upstream_status"] == 429


@pytest.mark.asyncio
async def test_messages_upstream_unavailable_returns_502():
    def handler(request):
        raise httpx.ConnectError("nope", request=request)

    client, app = _client(handler)
    async with client, app.router.lifespan_context(app):
        r = await client.post(
            "/v1/messages",
            json={
                "model": "claude-opus-4-7",
                "messages": [{"role": "user", "content": "x"}],
            },
        )

    assert r.status_code == 502
    assert r.json()["error"]["type"] == "upstream_unavailable"


@pytest.mark.asyncio
async def test_messages_streaming_relays_anthropic_events():
    sse_body = (
        b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1}}\n\n'
        b"data: [DONE]\n\n"
    )

    def handler(request):
        return httpx.Response(200, content=sse_body, headers={"content-type": "text/event-stream"})

    client, app = _client(handler)
    async with app.router.lifespan_context(app):
        async with client:
            async with client.stream(
                "POST",
                "/v1/messages",
                json={
                    "model": "claude-opus-4-7",
                    "stream": True,
                    "messages": [{"role": "user", "content": "x"}],
                },
            ) as r:
                assert r.status_code == 200
                text = b"".join([c async for c in r.aiter_raw()]).decode()

    assert "event: message_start" in text
    assert '"text":"hi"' in text
    assert '"output_tokens":1' in text
    assert "event: message_stop" in text


@pytest.mark.asyncio
async def test_messages_invalid_json_400():
    client, app = _client(lambda r: httpx.Response(500))
    async with client, app.router.lifespan_context(app):
        r = await client.post(
            "/v1/messages", content=b"{not json", headers={"content-type": "application/json"}
        )
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_count_tokens_returns_estimate():
    client, app = _client(lambda r: httpx.Response(500))
    async with client, app.router.lifespan_context(app):
        r = await client.post(
            "/v1/messages/count_tokens",
            json={
                "model": "claude-opus-4-7",
                "system": "You are helpful.",
                "messages": [{"role": "user", "content": "hello world"}],
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["input_tokens"], int)
    assert body["input_tokens"] >= 1


@pytest.mark.asyncio
async def test_count_tokens_invalid_json_400():
    client, app = _client(lambda r: httpx.Response(500))
    async with client, app.router.lifespan_context(app):
        r = await client.post(
            "/v1/messages/count_tokens",
            content=b"nope",
            headers={"content-type": "application/json"},
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_streaming_request_uses_unbounded_read_timeout():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["timeout"] = request.extensions.get("timeout")
        body = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    client, app = _client(handler)
    async with app.router.lifespan_context(app):
        async with client:
            async with client.stream(
                "POST",
                "/v1/messages",
                json={
                    "model": "claude-opus-4-7",
                    "stream": True,
                    "messages": [{"role": "user", "content": "x"}],
                },
            ) as r:
                async for _ in r.aiter_raw():
                    pass

    # httpx surfaces per-request timeouts via request.extensions["timeout"];
    # streaming must disable read timeout while keeping connect/write bounded.
    assert captured["timeout"]["read"] is None
    assert captured["timeout"]["connect"] is not None
