"""End-to-end SSE streaming tests: mid-stream close, multiple tool calls, interleaved."""

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


def _sse_line(obj: dict) -> str:
    return "data: " + json.dumps(obj) + "\n\n"


def _tc(idx, tid=None, name=None, args=None):
    """Build a tool_call delta dict for SSE chunk construction."""
    tc = {"index": idx}
    if tid is not None:
        tc["id"] = tid
    func = {}
    if name is not None:
        func["name"] = name
    if args is not None:
        func["arguments"] = args
    if func:
        tc["function"] = func
    return tc


def _delta(tool_calls=None, content=None):
    """Build a delta dict."""
    d = {}
    if tool_calls is not None:
        d["tool_calls"] = tool_calls
    if content is not None:
        d["content"] = content
    return d


@pytest.mark.asyncio
async def test_sse_mid_stream_close_gets_synthetic_stop():
    """If upstream closes mid-stream, client receives synthetic message_delta + message_stop."""

    content = _sse_line({"choices": [{"delta": {"content": "partial"}}]}).encode()

    def handler(request):
        return httpx.Response(200, content=content, headers={"content-type": "text/event-stream"})

    client, app = _client(handler)
    async with app.router.lifespan_context(app):
        async with client:
            async with client.stream(
                "POST",
                "/v1/messages",
                json={"model": "claude-opus-4-7", "stream": True, "messages": [{"role": "user", "content": "x"}]},
            ) as r:
                text = b"".join([c async for c in r.aiter_raw()]).decode()

    assert '"text":"partial"' in text
    assert "event: content_block_stop" in text
    assert "event: message_delta" in text
    assert "event: message_stop" in text


@pytest.mark.asyncio
async def test_sse_multiple_tool_calls_interleaved():
    """Multiple tool calls arriving in sequence should produce proper content blocks."""

    chunks = [
        {"choices": [{"delta": _delta(tool_calls=[_tc(0, "call_1", "tool_a", '{"a')])}]},
        {"choices": [{"delta": _delta(tool_calls=[_tc(0, args=":1}")])}]},
        {"choices": [{"delta": _delta(tool_calls=[_tc(1, "call_2", "tool_b", '{"b')])}]},
        {"choices": [{"delta": _delta(tool_calls=[_tc(1, args=":2}")])}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}],
         "usage": {"prompt_tokens": 5, "completion_tokens": 10}},
    ]
    sse_body = "".join(_sse_line(c) for c in chunks).encode() + b"data: [DONE]\n\n"

    def handler(request):
        return httpx.Response(200, content=sse_body, headers={"content-type": "text/event-stream"})

    client, app = _client(handler)
    async with app.router.lifespan_context(app):
        async with client:
            async with client.stream(
                "POST",
                "/v1/messages",
                json={"model": "claude-opus-4-7", "stream": True, "messages": [{"role": "user", "content": "x"}]},
            ) as r:
                text = b"".join([c async for c in r.aiter_raw()]).decode()

    assert '"name":"tool_a"' in text
    assert '"name":"tool_b"' in text
    assert text.count("event: content_block_stop") >= 2
    assert "event: message_stop" in text
    assert '"stop_reason":"tool_use"' in text


@pytest.mark.asyncio
async def test_sse_text_then_tool_call():
    """Text content followed by tool call in the same stream."""

    chunks = [
        {"choices": [{"delta": _delta(content="Let me check")}]},
        {"choices": [{"delta": _delta(tool_calls=[_tc(0, "call_1", "search", "{}")])}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}],
         "usage": {"prompt_tokens": 3, "completion_tokens": 5}},
    ]
    sse_body = "".join(_sse_line(c) for c in chunks).encode() + b"data: [DONE]\n\n"

    def handler(request):
        return httpx.Response(200, content=sse_body, headers={"content-type": "text/event-stream"})

    client, app = _client(handler)
    async with app.router.lifespan_context(app):
        async with client:
            async with client.stream(
                "POST",
                "/v1/messages",
                json={"model": "claude-opus-4-7", "stream": True, "messages": [{"role": "user", "content": "x"}]},
            ) as r:
                text = b"".join([c async for c in r.aiter_raw()]).decode()

    assert '"text":"Let me check"' in text
    assert "event: content_block_stop" in text
    assert '"name":"search"' in text


@pytest.mark.asyncio
async def test_sse_empty_stream():
    """Stream with only [DONE] should produce valid start/stop events."""

    sse_body = b"data: [DONE]\n\n"

    def handler(request):
        return httpx.Response(200, content=sse_body, headers={"content-type": "text/event-stream"})

    client, app = _client(handler)
    async with app.router.lifespan_context(app):
        async with client:
            async with client.stream(
                "POST",
                "/v1/messages",
                json={"model": "claude-opus-4-7", "stream": True, "messages": [{"role": "user", "content": "x"}]},
            ) as r:
                text = b"".join([c async for c in r.aiter_raw()]).decode()

    assert "event: message_start" in text
    assert "event: message_delta" in text
    assert "event: message_stop" in text


@pytest.mark.asyncio
async def test_sse_upstream_error_during_stream():
    """If upstream returns an error (non-200) for a stream request, convert to JSON error."""

    def handler(request):
        return httpx.Response(503, json={"error": {"type": "service_unavailable", "message": "overloaded"}})

    client, app = _client(handler)
    async with client, app.router.lifespan_context(app):
        r = await client.post(
            "/v1/messages",
            json={"model": "claude-opus-4-7", "stream": True, "messages": [{"role": "user", "content": "x"}]},
        )
        assert r.status_code == 503
        body = r.json()
        # _passthrough_error preserves upstream error type if present
        assert body["error"]["type"] == "service_unavailable"
        assert "overloaded" in body["error"]["message"]
