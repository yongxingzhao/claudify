"""End-to-end SSE streaming tests."""

from __future__ import annotations

import json

import httpx
import pytest


def _stream_chunks():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body["stream"] is True

        chunks = [
            {"id": "chatcmpl-1", "choices": [{"delta": {"role": "assistant"}, "index": 0}], "model": "m"},
            {"id": "chatcmpl-1", "choices": [{"delta": {"content": "Hello"}, "index": 0}], "model": "m"},
            {"id": "chatcmpl-1", "choices": [{"delta": {"content": " world"}, "index": 0}], "model": "m"},
            {"id": "chatcmpl-1", "choices": [{"delta": {}, "finish_reason": "stop", "index": 0}], "model": "m"},
            {"id": "chatcmpl-1", "choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
        ]
        lines = []
        for c in chunks:
            lines.append(f"data: {json.dumps(c)}")
        lines.append("data: [DONE]")
        content = "\n\n".join(lines) + "\n\n"
        return httpx.Response(200, content=content.encode(), headers={"content-type": "text/event-stream"})

    return handler


def _parse_sse_events(raw: bytes) -> list[dict]:
    events = []
    text = raw.decode()
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event_type = ""
        data_str = ""
        for line in block.split("\n"):
            if line.startswith("event: "):
                event_type = line[7:]
            elif line.startswith("data: "):
                data_str = line[6:]
        if data_str and event_type:
            try:
                events.append({"event": event_type, "data": json.loads(data_str)})
            except json.JSONDecodeError:
                pass
    return events


@pytest.mark.anyio
async def test_sse_stream_basic(make_client):
    client, _ = make_client(_stream_chunks())
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    })
    assert r.status_code == 200
    events = _parse_sse_events(r.content)
    event_types = [e["event"] for e in events]
    assert "message_start" in event_types
    assert "content_block_start" in event_types
    assert "content_block_delta" in event_types
    assert "message_stop" in event_types
    await client.aclose()


@pytest.mark.anyio
async def test_sse_stream_text_content(make_client):
    client, _ = make_client(_stream_chunks())
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    })
    events = _parse_sse_events(r.content)
    deltas = [e for e in events if e["event"] == "content_block_delta"]
    text_parts = []
    for d in deltas:
        delta = d["data"].get("delta", {})
        if delta.get("type") == "text_delta":
            text_parts.append(delta["text"])
    assert "Hello" in "".join(text_parts)
    await client.aclose()


@pytest.mark.anyio
async def test_sse_stream_has_ping(make_client):
    client, _ = make_client(_stream_chunks())
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    })
    events = _parse_sse_events(r.content)
    pings = [e for e in events if e["event"] == "ping"]
    assert len(pings) >= 1
    await client.aclose()


@pytest.mark.anyio
async def test_sse_stream_upstream_error(make_client):
    def handler(request):
        return httpx.Response(500, json={"error": {"message": "boom"}})
    client, _ = make_client(handler)
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    })
    assert r.status_code == 500
    await client.aclose()


def _stream_tool_call_chunks():
    """Upstream handler that returns a streaming tool call response."""
    def handler(request: httpx.Request) -> httpx.Response:
        chunks = [
            {"id": "chatcmpl-2", "choices": [{"delta": {"role": "assistant"}, "index": 0}], "model": "m"},
            {"id": "chatcmpl-2", "choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_abc", "type": "function", "function": {"name": "get_weather", "arguments": ""}}]}, "index": 0}], "model": "m"},
            {"id": "chatcmpl-2", "choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{\"lo"}}]}, "index": 0}], "model": "m"},
            {"id": "chatcmpl-2", "choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "cation"}}]}, "index": 0}], "model": "m"},
            {"id": "chatcmpl-2", "choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "\": \"SF\"}"}}]}, "index": 0}], "model": "m"},
            {"id": "chatcmpl-2", "choices": [{"delta": {}, "finish_reason": "tool_calls", "index": 0}], "model": "m"},
            {"id": "chatcmpl-2", "choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        ]
        lines = []
        for c in chunks:
            lines.append(f"data: {json.dumps(c)}")
        lines.append("data: [DONE]")
        content = "\n\n".join(lines) + "\n\n"
        return httpx.Response(200, content=content.encode(), headers={"content-type": "text/event-stream"})

    return handler


@pytest.mark.anyio
async def test_sse_stream_tool_call(make_client):
    client, _ = make_client(_stream_tool_call_chunks())
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "weather?"}],
        "stream": True,
    })
    assert r.status_code == 200
    events = _parse_sse_events(r.content)

    # Should have tool_use content_block_start
    tool_starts = [e for e in events if e["event"] == "content_block_start"
                   and e["data"].get("content_block", {}).get("type") == "tool_use"]
    assert len(tool_starts) == 1
    assert tool_starts[0]["data"]["content_block"]["name"] == "get_weather"

    # Should have input_json_delta deltas
    json_deltas = [e for e in events if e["event"] == "content_block_delta"
                   and e["data"].get("delta", {}).get("type") == "input_json_delta"]
    assert len(json_deltas) >= 1

    # Should have tool_use stop_reason
    delta_events = [e for e in events if e["event"] == "message_delta"]
    assert delta_events[-1]["data"]["delta"]["stop_reason"] == "tool_use"

    await client.aclose()
