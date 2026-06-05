"""Edge case and missing coverage tests for claudify.

Covers:
- routes.py: model validation edge cases, streaming error paths, auth edge cases
- conversion.py: empty/malformed content, tool call edge cases, extract_text
- retry.py: backoff calculation, retry-after header, exhaustion
- sse.py: parser edge cases, bytes input, extract_usage, STOP_REASON_MAP
- errors.py: sanitize patterns, passthrough_error edge cases
- metrics.py: overflow latency, multiple routes
"""

from __future__ import annotations

import json

import httpx
import pytest

from claudify.conversion import (
    _assistant_content_to_openai,
    _convert_tool_choice,
    _convert_tools,
    _parse_tool_arguments,
    _system_to_openai,
    _user_content_to_openai,
    anthropic_to_openai,
    extract_text_from_blocks,
    openai_to_anthropic_response,
    stream_openai_to_anthropic,
)
from claudify.errors import (
    _error_type_for_status,
    _sanitize_error_message,
    passthrough_error,
)
from claudify.metrics import Metrics
from claudify.retry import MAX_BACKOFF, _backoff_time, _compute_wait
from claudify.settings import Settings
from claudify.sse import (
    STOP_REASON_MAP,
    SSEParser,
    extract_usage,
    synthetic_stop_events,
)

# ============================================================================
# routes.py: model validation edge cases
# ============================================================================


@pytest.mark.anyio
async def test_model_empty_string(make_client):
    client, _ = make_client(lambda r: httpx.Response(200, json={}))
    r = await client.post(
        "/v1/messages",
        json={"model": "", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 400
    assert "non-empty" in r.json()["error"]["message"]
    await client.aclose()


@pytest.mark.anyio
async def test_model_too_long(make_client):
    client, _ = make_client(lambda r: httpx.Response(200, json={}))
    r = await client.post(
        "/v1/messages",
        json={"model": "x" * 300, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 400
    assert "256" in r.json()["error"]["message"]
    await client.aclose()


@pytest.mark.anyio
async def test_model_not_string(make_client):
    client, _ = make_client(lambda r: httpx.Response(200, json={}))
    r = await client.post(
        "/v1/messages",
        json={"model": 123, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 400
    await client.aclose()


@pytest.mark.anyio
async def test_payload_not_dict(make_client):
    client, _ = make_client(lambda r: httpx.Response(200, json={}))
    r = await client.post("/v1/messages", json="just a string")
    # "just a string" is a JSON string, not a JSON object
    assert r.status_code == 400
    assert "JSON object" in r.json()["error"]["message"]
    await client.aclose()


@pytest.mark.anyio
async def test_empty_body(make_client):
    client, _ = make_client(lambda r: httpx.Response(200, json={}))
    r = await client.post(
        "/v1/messages",
        content=b"",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400
    assert "Invalid JSON" in r.json()["error"]["message"]
    await client.aclose()


# ============================================================================
# routes.py: streaming error paths
# ============================================================================


@pytest.mark.anyio
async def test_streaming_upstream_500(make_client):
    def handler(request):
        return httpx.Response(500, json={"error": {"message": "boom"}})

    client, _ = make_client(handler)
    r = await client.post(
        "/v1/messages",
        json={
            "model": "claude-opus-4-7",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert r.status_code == 500
    assert r.json()["error"]["type"] == "api_error"
    await client.aclose()


@pytest.mark.anyio
async def test_streaming_upstream_429(make_client):
    def handler(request):
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    client, _ = make_client(handler)
    r = await client.post(
        "/v1/messages",
        json={
            "model": "claude-opus-4-7",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert r.status_code == 429
    assert r.json()["error"]["type"] == "rate_limit_error"
    await client.aclose()


@pytest.mark.anyio
async def test_streaming_connect_error(make_client):
    """ConnectError during streaming should return 502."""
    s = Settings(
        backend_base="http://test-backend/v1",
        api_key="test-key",
        model_map={"claude-opus-4-7": "hermes-agent"},
    )
    from claudify.app import create_app

    class ConnectErrorTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("connection refused")

    mock_client = httpx.AsyncClient(
        transport=ConnectErrorTransport(), base_url=s.backend_base
    )
    app = create_app(s, http_client=mock_client)
    asgi_transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=asgi_transport, base_url="http://test")

    r = await client.post(
        "/v1/messages",
        json={
            "model": "claude-opus-4-7",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert r.status_code == 502
    assert r.json()["error"]["type"] == "upstream_unavailable"
    await client.aclose()


@pytest.mark.anyio
async def test_streaming_timeout_error(make_client):
    """TimeoutException during streaming should return 504."""
    s = Settings(
        backend_base="http://test-backend/v1",
        api_key="test-key",
        model_map={"claude-opus-4-7": "hermes-agent"},
    )
    from claudify.app import create_app

    class TimeoutTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.TimeoutException("read timeout")

    mock_client = httpx.AsyncClient(
        transport=TimeoutTransport(), base_url=s.backend_base
    )
    app = create_app(s, http_client=mock_client)
    asgi_transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=asgi_transport, base_url="http://test")

    r = await client.post(
        "/v1/messages",
        json={
            "model": "claude-opus-4-7",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert r.status_code == 504
    assert r.json()["error"]["type"] == "timeout_error"
    await client.aclose()


# ============================================================================
# routes.py: non-streaming error paths
# ============================================================================


@pytest.mark.anyio
async def test_nonstreaming_connect_error(make_client):
    """ConnectError during non-streaming should return 502."""
    s = Settings(
        backend_base="http://test-backend/v1",
        api_key="test-key",
        model_map={"claude-opus-4-7": "hermes-agent"},
    )
    from claudify.app import create_app

    class ConnectErrorTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("connection refused")

    mock_client = httpx.AsyncClient(
        transport=ConnectErrorTransport(), base_url=s.backend_base
    )
    app = create_app(s, http_client=mock_client)
    asgi_transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=asgi_transport, base_url="http://test")

    r = await client.post(
        "/v1/messages",
        json={
            "model": "claude-opus-4-7",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 502
    assert r.json()["error"]["type"] == "upstream_unavailable"
    await client.aclose()


@pytest.mark.anyio
async def test_nonstreaming_timeout_error(make_client):
    """TimeoutException during non-streaming should return 504."""
    s = Settings(
        backend_base="http://test-backend/v1",
        api_key="test-key",
        model_map={"claude-opus-4-7": "hermes-agent"},
    )
    from claudify.app import create_app

    class TimeoutTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.TimeoutException("read timeout")

    mock_client = httpx.AsyncClient(
        transport=TimeoutTransport(), base_url=s.backend_base
    )
    app = create_app(s, http_client=mock_client)
    asgi_transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=asgi_transport, base_url="http://test")

    r = await client.post(
        "/v1/messages",
        json={
            "model": "claude-opus-4-7",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 504
    assert r.json()["error"]["type"] == "timeout_error"
    await client.aclose()


# ============================================================================
# routes.py: auth edge cases
# ============================================================================


@pytest.mark.anyio
async def test_auth_header_forwarded(make_client, chat_response):
    """When no x-api-key, Authorization header should be forwarded."""
    captured = {}

    def handler(request):
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=chat_response(body="hi"))

    client, _ = make_client(handler)
    await client.post(
        "/v1/messages",
        json={"model": "claude-opus-4-7", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer my-token"},
    )
    assert captured["auth"] == "Bearer my-token"
    await client.aclose()


@pytest.mark.anyio
async def test_no_auth_no_key_fallback(make_client, chat_response):
    """When no inbound key, no x-api-key, no auth header, use configured api_key."""
    captured = {}

    def handler(request):
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=chat_response(body="hi"))

    client, _ = make_client(handler, api_key="fallback-key")
    await client.post(
        "/v1/messages",
        json={"model": "claude-opus-4-7", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert captured["auth"] == "Bearer fallback-key"
    await client.aclose()


@pytest.mark.anyio
async def test_health_upstream_timeout(make_client):
    """Health endpoint should return 'unreachable' on upstream timeout."""

    def handler(request):
        if "health" in str(request.url):
            raise httpx.TimeoutException("timeout")
        return httpx.Response(200, json={})

    client, _ = make_client(handler, upstream_health_path="/healthz")
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["upstream"] == "unreachable"
    await client.aclose()


@pytest.mark.anyio
async def test_health_upstream_degraded(make_client):
    """Health endpoint should return 'degraded' for 5xx upstream."""

    def handler(request):
        if "healthz" in str(request.url):
            return httpx.Response(503, json={})
        return httpx.Response(200, json={})

    client, _ = make_client(handler, upstream_health_path="healthz")
    r = await client.get("/health")
    assert r.json()["upstream"] == "degraded"
    await client.aclose()


# ============================================================================
# routes.py: count_tokens edge cases
# ============================================================================


@pytest.mark.anyio
async def test_count_tokens_no_messages_key(make_client):
    client, _ = make_client(lambda r: httpx.Response(200, json={}))
    r = await client.post("/v1/messages/count_tokens", json={"model": "claude-opus-4-7"})
    assert r.status_code == 400
    assert "messages" in r.json()["error"]["message"]
    await client.aclose()


@pytest.mark.anyio
async def test_count_tokens_messages_not_list(make_client):
    client, _ = make_client(lambda r: httpx.Response(200, json={}))
    r = await client.post(
        "/v1/messages/count_tokens",
        json={"model": "claude-opus-4-7", "messages": "not a list"},
    )
    assert r.status_code == 400
    await client.aclose()


@pytest.mark.anyio
async def test_count_tokens_system_list(make_client):
    client, _ = make_client(lambda r: httpx.Response(200, json={}))
    r = await client.post(
        "/v1/messages/count_tokens",
        json={
            "model": "claude-opus-4-7",
            "system": [{"type": "text", "text": "sys prompt"}],
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 200
    assert r.json()["input_tokens"] > 0
    await client.aclose()


# ============================================================================
# conversion.py: _system_to_openai edge cases
# ============================================================================


def test_system_to_openai_none():
    assert _system_to_openai(None) == ""


def test_system_to_openai_int():
    assert _system_to_openai(42) == ""


def test_system_to_openai_empty_list():
    assert _system_to_openai([]) == ""


def test_system_to_openai_list_with_non_dict():
    assert _system_to_openai([1, "text", None]) == ""


def test_system_to_openai_list_with_non_text_types():
    assert _system_to_openai([{"type": "image"}, {"type": "code"}]) == ""


def test_system_to_openai_multiple_text_blocks():
    result = _system_to_openai([
        {"type": "text", "text": "first"},
        {"type": "text", "text": "second"},
    ])
    assert result == "first\nsecond"


# ============================================================================
# conversion.py: _user_content_to_openai edge cases
# ============================================================================


def test_user_content_none():
    content, tools = _user_content_to_openai(None)
    assert content == ""
    assert tools == []


def test_user_content_int():
    content, tools = _user_content_to_openai(42)
    assert content == ""
    assert tools == []


def test_user_content_empty_list():
    content, tools = _user_content_to_openai([])
    assert content == ""
    assert tools == []


def test_user_content_non_dict_items():
    content, tools = _user_content_to_openai([1, "text", None])
    assert content == ""
    assert tools == []


def test_user_content_only_tool_result_no_text():
    """User content with only tool_result blocks (no text) should produce empty content."""
    content, tools = _user_content_to_openai([
        {"type": "tool_result", "tool_use_id": "t1", "content": "result"},
    ])
    assert content == ""  # no text parts
    assert len(tools) == 1
    assert tools[0]["content"] == "result"


def test_user_content_tool_result_list_content():
    """tool_result with list-type content containing text blocks."""
    content, tools = _user_content_to_openai([
        {
            "type": "tool_result",
            "tool_use_id": "t1",
            "content": [
                {"type": "text", "text": "part1"},
                {"type": "text", "text": "part2"},
            ],
        },
    ])
    assert tools[0]["content"] == "part1\npart2"


def test_user_content_tool_result_list_with_image():
    """tool_result with list content containing images (should log warning and drop)."""
    content, tools = _user_content_to_openai([
        {
            "type": "tool_result",
            "tool_use_id": "t1",
            "content": [
                {"type": "image", "source": {"type": "url", "url": "http://x"}},
            ],
        },
    ])
    assert tools[0]["content"] == ""


def test_user_content_tool_result_empty_content():
    """tool_result with non-str, non-list content → empty tool_text."""
    content, tools = _user_content_to_openai([
        {
            "type": "tool_result",
            "tool_use_id": "t1",
            "content": 42,
        },
    ])
    assert tools[0]["content"] == ""


def test_user_content_tool_result_is_error_prefix():
    """is_error=True should prefix tool_text with [tool_error]."""
    content, tools = _user_content_to_openai([
        {
            "type": "tool_result",
            "tool_use_id": "t1",
            "content": "bad stuff",
            "is_error": True,
        },
    ])
    assert tools[0]["content"] == "[tool_error] bad stuff"


def test_user_content_tool_result_no_tool_use_id():
    """Missing tool_use_id should generate a fallback id."""
    content, tools = _user_content_to_openai([
        {
            "type": "tool_result",
            "content": "ok",
        },
    ])
    assert tools[0]["tool_call_id"].startswith("call_")


def test_user_content_mixed_text_image_tool():
    """Mixed content: text + image + tool_result."""
    content, tools = _user_content_to_openai([
        {"type": "text", "text": "hello"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "abc"}},
        {"type": "tool_result", "tool_use_id": "t1", "content": "result"},
    ])
    # Should have text and image_url parts (multimodal), plus tool result
    assert isinstance(content, list)
    assert len(tools) == 1


def test_user_content_single_text_returns_string():
    """Single text block should return string, not list."""
    content, tools = _user_content_to_openai([
        {"type": "text", "text": "hello"},
    ])
    assert content == "hello"
    assert isinstance(content, str)


def test_user_content_multiple_parts_returns_list():
    """Multiple text blocks should return list."""
    content, tools = _user_content_to_openai([
        {"type": "text", "text": "hello"},
        {"type": "text", "text": "world"},
    ])
    assert isinstance(content, list)
    assert len(content) == 2


# ============================================================================
# conversion.py: _assistant_content_to_openai edge cases
# ============================================================================


def test_assistant_content_none():
    text, tools = _assistant_content_to_openai(None)
    assert text == ""
    assert tools == []


def test_assistant_content_int():
    text, tools = _assistant_content_to_openai(42)
    assert text == ""
    assert tools == []


def test_assistant_content_empty_list():
    text, tools = _assistant_content_to_openai([])
    assert text == ""
    assert tools == []


def test_assistant_content_non_dict_blocks():
    text, tools = _assistant_content_to_openai([1, "text", None])
    assert text == ""
    assert tools == []


def test_assistant_content_thinking_only():
    """Only thinking blocks, no text or tool_use."""
    text, tools = _assistant_content_to_openai([
        {"type": "thinking", "thinking": "hmm..."},
    ])
    assert text == ""
    assert tools == []


def test_assistant_content_tool_use_missing_id():
    """tool_use without id should generate fallback."""
    text, tools = _assistant_content_to_openai([
        {"type": "tool_use", "name": "fn", "input": {"a": 1}},
    ])
    assert len(tools) == 1
    assert tools[0]["id"].startswith("call_")


def test_assistant_content_tool_use_empty_input():
    """tool_use with None input → empty dict."""
    text, tools = _assistant_content_to_openai([
        {"type": "tool_use", "id": "t1", "name": "fn", "input": None},
    ])
    assert tools[0]["function"]["arguments"] == "{}"


def test_assistant_content_multiple_text_blocks():
    text, tools = _assistant_content_to_openai([
        {"type": "text", "text": "hello"},
        {"type": "text", "text": "world"},
    ])
    assert text == "hello\nworld"


def test_assistant_content_empty_text_blocks():
    """Empty text blocks should be filtered out."""
    text, tools = _assistant_content_to_openai([
        {"type": "text", "text": ""},
        {"type": "text", "text": "real"},
    ])
    assert text == "real"


# ============================================================================
# conversion.py: _convert_tools edge cases
# ============================================================================


def test_convert_tools_empty_list():
    assert _convert_tools([]) is None


def test_convert_tools_non_list():
    assert _convert_tools("not a list") is None


def test_convert_tools_none():
    assert _convert_tools(None) is None


def test_convert_tools_non_dict_items():
    assert _convert_tools([1, 2, 3]) is None


def test_convert_tools_missing_name():
    """Tools without name should be skipped."""
    result = _convert_tools([{"description": "no name"}])
    assert result is None


def test_convert_tools_empty_name():
    result = _convert_tools([{"name": "", "description": "empty"}])
    assert result is None


def test_convert_tools_missing_input_schema():
    """Missing input_schema should use default empty schema."""
    result = _convert_tools([{"name": "fn"}])
    assert result is not None
    assert result[0]["function"]["parameters"] == {"type": "object", "properties": {}}


# ============================================================================
# conversion.py: _convert_tool_choice edge cases
# ============================================================================


def test_tool_choice_tool_no_name():
    """tool type with no name → None."""
    assert _convert_tool_choice({"type": "tool"}) is None


def test_tool_choice_empty_dict():
    assert _convert_tool_choice({}) is None


def test_tool_choice_unknown_type():
    assert _convert_tool_choice({"type": "unknown"}) is None


# ============================================================================
# conversion.py: _parse_tool_arguments edge cases
# ============================================================================


def test_parse_tool_arguments_empty():
    assert _parse_tool_arguments("") == {}


def test_parse_tool_arguments_none():
    assert _parse_tool_arguments(None) == {}


def test_parse_tool_arguments_valid_dict():
    assert _parse_tool_arguments('{"a": 1}') == {"a": 1}


def test_parse_tool_arguments_non_dict_json():
    """Valid JSON but not a dict (e.g. list) → empty dict with warning."""
    assert _parse_tool_arguments("[1, 2]") == {}

    assert _parse_tool_arguments("not json") == {}


def test_parse_tool_arguments_unicode():
    assert _parse_tool_arguments('{"key": "日本語"}') == {"key": "日本語"}


# ============================================================================
# conversion.py: openai_to_anthropic_response edge cases
# ============================================================================


def test_response_empty_choices():
    """Empty choices list should fallback gracefully."""
    resp = {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    out = openai_to_anthropic_response(resp, "m")
    assert len(out["content"]) >= 1
    assert out["content"][0]["type"] == "text"


def test_response_missing_choices():
    resp = {"usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    out = openai_to_anthropic_response(resp, "m")
    assert out["content"][0]["type"] == "text"


def test_response_missing_usage():
    resp = {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]}
    out = openai_to_anthropic_response(resp, "m")
    assert out["usage"]["input_tokens"] == 0
    assert out["usage"]["output_tokens"] == 0


def test_response_none_content():
    resp = {
        "choices": [{"message": {"content": None}, "finish_reason": "stop"}],
        "usage": {},
    }
    out = openai_to_anthropic_response(resp, "m")
    assert len(out["content"]) >= 1


def test_response_non_dict_tool_calls():
    """Non-dict tool_calls should be skipped."""
    resp = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": ["not-a-dict", 123],
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {},
    }
    out = openai_to_anthropic_response(resp, "m")
    assert len(out["content"]) >= 1


def test_response_missing_message():
    resp = {"choices": [{}], "usage": {}}
    out = openai_to_anthropic_response(resp, "m")
    assert len(out["content"]) >= 1


def test_response_tool_calls_missing_function():
    """tool_calls with no function field should still produce tool_use."""
    resp = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [{"id": "c1"}],
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {},
    }
    out = openai_to_anthropic_response(resp, "m")
    tc = [c for c in out["content"] if c["type"] == "tool_use"]
    assert len(tc) == 1
    assert tc[0]["id"] == "c1"


def test_response_tool_calls_missing_id():
    """tool_calls with no id should generate fallback."""
    resp = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [{"function": {"name": "fn", "arguments": "{}"}}],
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {},
    }
    out = openai_to_anthropic_response(resp, "m")
    tc = out["content"][0]
    assert tc["id"].startswith("toolu_")


def test_finish_reason_map():
    """All STOP_REASON_MAP entries should map correctly."""
    for openai_reason, expected in STOP_REASON_MAP.items():
        resp = {
            "choices": [{"message": {"content": "x"}, "finish_reason": openai_reason}],
            "usage": {},
        }
        out = openai_to_anthropic_response(resp, "m")
        assert out["stop_reason"] == expected


def test_unknown_finish_reason():
    resp = {
        "choices": [{"message": {"content": "x"}, "finish_reason": "something_new"}],
        "usage": {},
    }
    out = openai_to_anthropic_response(resp, "m")
    assert out["stop_reason"] == "end_turn"


# ============================================================================
# conversion.py: extract_text_from_blocks edge cases
# ============================================================================


def test_extract_text_none():
    assert extract_text_from_blocks(None) == ""


def test_extract_text_int():
    assert extract_text_from_blocks(42) == ""


def test_extract_text_empty_list():
    assert extract_text_from_blocks([]) == ""


def test_extract_text_non_dict_items():
    assert extract_text_from_blocks([1, "text", None]) == ""


def test_extract_text_image_block():
    blocks = [{"type": "image", "source": {"type": "url", "url": "http://x"}}]
    assert extract_text_from_blocks(blocks) == "[image omitted]"


def test_extract_text_tool_result_list():
    blocks = [
        {
            "type": "tool_result",
            "content": [
                {"type": "text", "text": "a"},
                {"type": "text", "text": "b"},
            ],
        }
    ]
    assert extract_text_from_blocks(blocks) == "a\nb"


def test_extract_text_tool_result_string():
    blocks = [{"type": "tool_result", "content": "result"}]
    assert extract_text_from_blocks(blocks) == "result"


def test_extract_text_tool_result_list_non_dict():
    """Non-dict items in tool_result list content should be skipped."""
    blocks = [
        {
            "type": "tool_result",
            "content": [1, None, {"type": "text", "text": "ok"}],
        }
    ]
    assert extract_text_from_blocks(blocks) == "ok"


def test_extract_text_mixed_blocks():
    blocks = [
        {"type": "text", "text": "a"},
        {"type": "image"},
        {"type": "tool_result", "content": "b"},
        {"type": "code"},
    ]
    result = extract_text_from_blocks(blocks)
    assert "a" in result
    assert "[image omitted]" in result
    assert "b" in result


def test_extract_text_empty_text_blocks():
    blocks = [
        {"type": "text", "text": ""},
        {"type": "text", "text": "real"},
    ]
    assert extract_text_from_blocks(blocks) == "real"


# ============================================================================
# conversion.py: anthropic_to_openai additional edge cases
# ============================================================================


def test_anthropic_to_openai_all_optional_params():
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.7,
        "top_p": 0.9,
        "max_tokens": 1024,
        "stop_sequences": ["END"],
        "top_k": 50,
        "stream": True,
        "metadata": {"user_id": "u-1"},
        "tools": [{"name": "fn", "description": "a fn", "input_schema": {}}],
        "tool_choice": {"type": "auto"},
    }
    out = anthropic_to_openai(payload, {"m": "gpt-4"})
    assert out["model"] == "gpt-4"
    assert out["temperature"] == 0.7
    assert out["top_p"] == 0.9
    assert out["max_tokens"] == 1024
    assert out["stop"] == ["END"]
    assert out["top_k"] == 50
    assert out["stream"] is True
    assert out["stream_options"] == {"include_usage": True}
    assert out["user"] == "u-1"
    assert len(out["tools"]) == 1
    assert out["tool_choice"] == "auto"


def test_anthropic_to_openai_empty_metadata():
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "metadata": {},
    }
    out = anthropic_to_openai(payload, {})
    assert "user" not in out


def test_anthropic_to_openai_metadata_no_user_id():
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "metadata": {"other": "data"},
    }
    out = anthropic_to_openai(payload, {})
    assert "user" not in out


def test_anthropic_to_openai_system_whitespace_only():
    """System that is all whitespace should not produce a system message."""
    payload = {
        "model": "m",
        "system": "   \n\t  ",
        "messages": [{"role": "user", "content": "hi"}],
    }
    out = anthropic_to_openai(payload, {})
    assert out["messages"][0]["role"] == "user"


def test_anthropic_to_openai_unsupported_role():
    """Messages with unsupported roles should be dropped."""
    payload = {
        "model": "m",
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "sys"},
            {"role": "custom", "content": "x"},
        ],
    }
    out = anthropic_to_openai(payload, {})
    roles = [m["role"] for m in out["messages"]]
    assert "system" not in roles  # unsupported roles are dropped
    assert "custom" not in roles


def test_anthropic_to_openai_tool_choice_named_no_name():
    """tool_choice with type=tool but no name → None (skipped)."""
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "tool_choice": {"type": "tool"},
    }
    out = anthropic_to_openai(payload, {})
    assert "tool_choice" not in out


# ============================================================================
# sse.py: SSEParser edge cases
# ============================================================================


def test_sse_parser_empty_input():
    parser = SSEParser()
    events = parser.feed("")
    assert events == []
    assert not parser.done


def test_sse_parser_bytes_input():
    parser = SSEParser()
    events = parser.feed(b'data: {"id":"1"}\n\n')
    assert len(events) == 1
    assert events[0]["id"] == "1"


def test_sse_parser_bytes_invalid_utf8():
    p = SSEParser()
    events = p.feed(b"\xff\xfe data: {\"ok\":true}\n\n")
    # Should not crash; uses errors="replace"
    assert isinstance(events, list)


def test_sse_parser_trailing_newline():
    """The branch for data ending with \\n but no \\n\\n."""
    parser = SSEParser()
    events = parser.feed('data: {"x":1}\n')
    assert len(events) == 1
    assert events[0]["x"] == 1


def test_sse_parser_done_mixed_with_events():
    """[DONE] mixed with valid events in the same chunk."""
    parser = SSEParser()
    events = parser.feed('data: {"a":1}\n\ndata: [DONE]\n\n')
    assert len(events) == 1
    assert parser.done


def test_sse_parser_done_mid_chunk():
    """[DONE] in the middle, remaining data ignored."""
    parser = SSEParser()
    events = parser.feed('data: [DONE]\n\ndata: {"a":1}\n\n')
    assert parser.done
    assert len(events) == 0


def test_sse_parser_multiple_data_lines():
    """Multiple data: lines in one event block (only first parsed)."""
    parser = SSEParser()
    events = parser.feed('data: {"a":1}\ndata: {"b":2}\n\n')
    # The second data line is in the same event block
    assert len(events) >= 1


def test_sse_parser_empty_data_line():
    """data: with empty body should be skipped (json.loads fails)."""
    parser = SSEParser()
    events = parser.feed("data:\n\n")
    assert events == []


def test_sse_parser_invalid_json():
    """Non-JSON data lines are silently skipped."""
    parser = SSEParser()
    events = parser.feed("data: {invalid\n\n")
    assert events == []


def test_sse_parser_event_prefix_ignored():
    """Lines that don't start with 'data:' are ignored."""
    parser = SSEParser()
    events = parser.feed("event: message\ndata: {\"ok\":true}\nid: 1\n\n")
    assert len(events) == 1


def test_sse_parser_mixed_bytes_and_str():
    """Alternating bytes and str inputs should parse correctly."""
    parser = SSEParser()
    events: list[dict] = []
    events.extend(parser.feed(b'data: {"a":1}\n\n'))
    events.extend(parser.feed('data: {"b":2}\n\n'))
    assert len(events) == 2
    assert events[0] == {"a": 1}
    assert events[1] == {"b": 2}


# ============================================================================
# sse.py: extract_usage edge cases
# ============================================================================


def test_extract_usage_none():
    assert extract_usage(None) == {"input_tokens": 0, "output_tokens": 0}


def test_extract_usage_empty():
    assert extract_usage({}) == {"input_tokens": 0, "output_tokens": 0}


def test_extract_usage_none_values():
    assert extract_usage({"prompt_tokens": None, "completion_tokens": None}) == {
        "input_tokens": 0,
        "output_tokens": 0,
    }


def test_extract_usage_zero_values():
    assert extract_usage({"prompt_tokens": 0, "completion_tokens": 0}) == {
        "input_tokens": 0,
        "output_tokens": 0,
    }


def test_extract_usage_normal():
    assert extract_usage({"prompt_tokens": 10, "completion_tokens": 20}) == {
        "input_tokens": 10,
        "output_tokens": 20,
    }


def test_extract_usage_missing_keys():
    assert extract_usage({"other": "data"}) == {
        "input_tokens": 0,
        "output_tokens": 0,
    }


# ============================================================================
# sse.py: synthetic_stop_events edge cases
# ============================================================================


def test_synthetic_stop_length():
    events = synthetic_stop_events("length", None)
    delta = events[0]
    assert b"max_tokens" in delta


def test_synthetic_stop_tool_calls():
    events = synthetic_stop_events("tool_calls", None)
    assert b"tool_use" in events[0]


def test_synthetic_stop_unknown():
    """Unknown finish reason should default to 'end_turn'."""
    events = synthetic_stop_events("something_new", None)
    assert b"end_turn" in events[0]


def test_synthetic_stop_none_usage():
    events = synthetic_stop_events("stop", None)
    assert len(events) == 2


def test_synthetic_stop_with_usage():
    events = synthetic_stop_events("stop", {"prompt_tokens": 5, "completion_tokens": 10})
    assert len(events) == 2


# ============================================================================
# retry.py: _backoff_time edge cases
# ============================================================================


def test_backoff_time_attempt_0():
    assert _backoff_time(0.5, 0) == 0.5


def test_backoff_time_attempt_1():
    assert _backoff_time(0.5, 1) == 1.0


def test_backoff_time_attempt_2():
    assert _backoff_time(0.5, 2) == 2.0


def test_backoff_time_capped():
    assert _backoff_time(1.0, 100) == MAX_BACKOFF


def test_backoff_time_large_base():
    assert _backoff_time(60.0, 1) == MAX_BACKOFF


def test_backoff_time_zero_base():
    assert _backoff_time(0.0, 5) == 0.0


# ============================================================================
# retry.py: _compute_wait edge cases
# ============================================================================


def test_compute_wait_no_response():
    assert _compute_wait(0.5, 0) == 0.5


def test_compute_wait_429_no_retry_after():
    resp = httpx.Response(429, headers={})
    wait = _compute_wait(0.5, 0, resp)
    assert wait == 0.5


def test_compute_wait_429_with_retry_after():
    resp = httpx.Response(429, headers={"retry-after": "10"})
    wait = _compute_wait(0.5, 0, resp)
    assert wait >= 10.0


def test_compute_wait_429_retry_after_less_than_backoff():
    """Retry-After less than backoff should use backoff."""
    resp = httpx.Response(429, headers={"retry-after": "0.1"})
    wait = _compute_wait(0.5, 0, resp)
    assert wait == 0.5  # backoff is larger


def test_compute_wait_429_retry_after_invalid():
    resp = httpx.Response(429, headers={"retry-after": "not-a-number"})
    wait = _compute_wait(0.5, 0, resp)
    assert wait == 0.5  # falls back to backoff


def test_compute_wait_non_429():
    resp = httpx.Response(500, headers={"retry-after": "10"})
    wait = _compute_wait(0.5, 0, resp)
    assert wait == 0.5  # retry-after ignored for non-429


# ============================================================================
# retry.py: post_with_retry integration
# ============================================================================


@pytest.mark.anyio
async def test_post_retry_all_exhausted():
    """All retries exhausted with ConnectError should raise."""
    from claudify.retry import post_with_retry

    s = Settings(
        backend_base="http://test-backend/v1",
        api_key="test-key",
        model_map={},
    )

    class FailTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("refused")

    client = httpx.AsyncClient(transport=FailTransport(), base_url=s.backend_base)
    req = client.build_request("POST", "/chat/completions", json={"model": "m"})
    with pytest.raises(httpx.ConnectError):
        await post_with_retry(client, req, attempts=2, backoff=0.01)
    await client.aclose()


@pytest.mark.anyio
async def test_post_retry_success_first_attempt():
    """First attempt succeeds → no retry."""
    from claudify.retry import post_with_retry

    def handler(request):
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://test/v1")
    req = client.build_request("POST", "/chat/completions", json={"model": "m"})
    r = await post_with_retry(client, req, attempts=3, backoff=0.01)
    assert r.status_code == 200
    await client.aclose()


@pytest.mark.anyio
async def test_post_retry_429_with_retry_after():
    """429 with Retry-After should respect it."""
    from claudify.retry import post_with_retry

    call_count = 0

    def handler(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, headers={"retry-after": "0.01"})
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://test/v1")
    req = client.build_request("POST", "/chat/completions", json={"model": "m"})
    r = await post_with_retry(client, req, attempts=3, backoff=0.01)
    assert r.status_code == 200
    assert call_count == 2
    await client.aclose()


@pytest.mark.anyio
async def test_post_retry_429_all_exhausted():
    """All retries exhausted with 429 → returns last 429 response."""
    from claudify.retry import post_with_retry

    def handler(request):
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://test/v1")
    req = client.build_request("POST", "/chat/completions", json={"model": "m"})
    r = await post_with_retry(client, req, attempts=2, backoff=0.01)
    assert r.status_code == 429
    await client.aclose()


# ============================================================================
# retry.py: stream_with_retry integration
# ============================================================================


@pytest.mark.anyio
async def test_stream_retry_success_first_attempt():
    """First stream attempt succeeds → retried=False."""
    from claudify.retry import stream_with_retry

    def handler(request):
        return httpx.Response(200, content=b"data: [DONE]\n\n", headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://test/v1")
    req = client.build_request("POST", "/chat/completions", json={"model": "m"})
    r, retried = await stream_with_retry(client, req, attempts=3, backoff=0.01)
    assert not retried
    assert r.status_code == 200
    await r.aclose()
    await client.aclose()


@pytest.mark.anyio
async def test_stream_retry_all_connect_errors():
    """All stream retries exhausted with ConnectError → raises."""
    from claudify.retry import stream_with_retry

    class FailTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("refused")

    client = httpx.AsyncClient(transport=FailTransport(), base_url="http://test/v1")
    req = client.build_request("POST", "/chat/completions", json={"model": "m"})
    with pytest.raises(httpx.ConnectError):
        await stream_with_retry(client, req, attempts=2, backoff=0.01)
    await client.aclose()


@pytest.mark.anyio
async def test_stream_retry_connect_error_then_success():
    """ConnectError on first attempt, success on second."""
    from claudify.retry import stream_with_retry

    call_count = 0

    class RetryTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("refused")
            return httpx.Response(200, content=b"data: [DONE]\n\n", headers={"content-type": "text/event-stream"})

    client = httpx.AsyncClient(transport=RetryTransport(), base_url="http://test/v1")
    req = client.build_request("POST", "/chat/completions", json={"model": "m"})
    r, retried = await stream_with_retry(client, req, attempts=3, backoff=0.01)
    assert retried
    assert r.status_code == 200
    await r.aclose()
    await client.aclose()


@pytest.mark.anyio
async def test_stream_retry_429():
    """429 on first stream attempt, success on second."""
    from claudify.retry import stream_with_retry

    call_count = 0

    def handler(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, json={"error": {"message": "rate limited"}})
        return httpx.Response(200, content=b"data: [DONE]\n\n", headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://test/v1")
    req = client.build_request("POST", "/chat/completions", json={"model": "m"})
    r, retried = await stream_with_retry(client, req, attempts=3, backoff=0.01)
    assert retried
    assert r.status_code == 200
    await r.aclose()
    await client.aclose()


# ============================================================================
# errors.py: edge cases
# ============================================================================


def test_error_type_for_status_unknown():
    assert _error_type_for_status(999) == "api_error"


def test_error_type_for_status_418():
    assert _error_type_for_status(418) == "api_error"


def test_sanitize_key_pattern():
    msg = "Use key-abcdef1234567890 to authenticate"
    result = _sanitize_error_message(msg)
    assert "key-abcdef1234567890" not in result
    assert "[REDACTED]" in result


def test_sanitize_token_pattern():
    msg = "token-xyz12345678901234 is invalid"
    result = _sanitize_error_message(msg)
    assert "token-xyz12345678901234" not in result
    assert "[REDACTED]" in result


def test_sanitize_bearer_pattern():
    msg = "Authorization: Bearer abc123def456 is invalid"
    result = _sanitize_error_message(msg)
    assert "Bearer abc123def456" not in result
    assert "[REDACTED]" in result


def test_sanitize_no_secrets():
    msg = "No secrets here"
    assert _sanitize_error_message(msg) == msg


def test_sanitize_multiple_secrets():
    msg = "sk-1234567890abcdef key-abcdef1234567890"
    result = _sanitize_error_message(msg)
    assert "sk-" not in result
    assert "key-" not in result
    assert result.count("[REDACTED]") == 2


def test_passthrough_error_no_body():
    resp = passthrough_error(502)
    assert resp.status_code == 502
    data = resp.body
    assert b"upstream_unavailable" in data


def test_passthrough_error_non_json_body():
    resp = passthrough_error(500, b"<html>Error</html>")
    assert resp.status_code == 500


def test_passthrough_error_json_no_error_key():
    resp = passthrough_error(500, json.dumps({"message": "something"}).encode())
    assert resp.status_code == 500


def test_passthrough_error_json_error_not_dict():
    resp = passthrough_error(500, json.dumps({"error": "string"}).encode())
    assert resp.status_code == 500


def test_passthrough_error_json_error_has_msg_field():
    """When error has 'msg' instead of 'message', use 'msg'."""
    resp = passthrough_error(
        500, json.dumps({"error": {"msg": "custom error"}}).encode()
    )
    assert b"custom error" in resp.body


def test_passthrough_error_json_error_no_message_or_msg():
    """When error dict has neither message nor msg."""
    resp = passthrough_error(
        500, json.dumps({"error": {"type": "custom"}}).encode()
    )
    assert b"upstream returned 500" in resp.body


def test_passthrough_error_binary_body():
    resp = passthrough_error(500, b"\xff\xfe\x00\x01")
    assert resp.status_code == 500


def test_passthrough_error_empty_string_body():
    resp = passthrough_error(500, b"")
    assert resp.status_code == 500


def test_passthrough_error_upstream_body_sanitized():
    """Secrets in upstream body should be redacted."""
    body = json.dumps({"error": {"message": "sk-abcdef1234567890 is bad"}}).encode()
    resp = passthrough_error(500, body)
    assert b"sk-" not in resp.body
    assert b"[REDACTED]" in resp.body


# ============================================================================
# metrics.py: edge cases
# ============================================================================


def test_metrics_very_high_latency():
    """Latency exceeding all buckets should only appear in +Inf."""
    m = Metrics()
    m.record_request("/test", 100.0, 200)
    text = m.render()
    # Should have +Inf bucket with count 1
    assert 'le="+Inf"' in text
    # All specific buckets should be 0
    assert 'le="0.005"} 1' not in text.split("\n")[1] if len(text.split("\n")) > 1 else True


def test_metrics_multiple_routes_independent():
    m = Metrics()
    m.record_request("/a", 0.1, 200)
    m.record_request("/b", 0.2, 500)
    text = m.render()
    assert 'route="/a"' in text
    assert 'route="/b"' in text


def test_metrics_render_format():
    """Render should produce valid prometheus text format."""
    m = Metrics()
    m.record_request("/test", 0.5, 200)
    text = m.render()
    lines = text.strip().split("\n")
    for line in lines:
        if line.startswith("claudify_"):
            assert " " in line  # metric_name value format


# ============================================================================
# conversion.py: anthropic_to_openai user_content empty → guard
# ============================================================================


def test_anthropic_to_openai_user_content_empty_list_skipped():
    """User message with empty content list should be skipped, guard adds '.' user."""
    payload = {
        "model": "m",
        "messages": [
            {"role": "user", "content": []},
        ],
    }
    out = anthropic_to_openai(payload, {})
    # The empty user content is skipped, but guard adds a '.' user message
    user_msgs = [m for m in out["messages"] if m["role"] == "user"]
    assert len(user_msgs) == 1
    assert user_msgs[0]["content"] == "."


# ============================================================================
# conversion.py: stream_openai_to_anthropic - text only stream
# ============================================================================


@pytest.mark.anyio
async def test_stream_text_only():
    """Test stream_openai_to_anthropic with text-only response."""
    async def mock_stream():
        yield b'data: {"id":"c","choices":[{"delta":{"content":"Hello"},"index":0}],"model":"m"}\n\n'
        yield b'data: {"id":"c","choices":[{"delta":{},"finish_reason":"stop","index":0}],"model":"m"}\n\n'
        yield b'data: {"id":"c","choices":[],"usage":{"prompt_tokens":5,"completion_tokens":2}}\n\n'
        yield b"data: [DONE]\n\n"

    events = []
    async for chunk in stream_openai_to_anthropic(mock_stream(), "test-model"):
        events.append(chunk)

    # Should have message_start, content_block_start, content_block_delta(s), content_block_stop, message_delta, message_stop
    event_types = []
    for ev in events:
        text = ev.decode()
        for line in text.split("\n"):
            if line.startswith("event: "):
                event_types.append(line[7:])

    assert "message_start" in event_types
    assert "content_block_start" in event_types
    assert "content_block_delta" in event_types
    assert "message_delta" in event_types
    assert "message_stop" in event_types


# ============================================================================
# conversion.py: stream_openai_to_anthropic - error during stream
# ============================================================================


@pytest.mark.anyio
async def test_stream_error_mid_stream():
    """Error during stream should emit synthetic stop events."""

    async def failing_stream():
        yield b'data: {"id":"c","choices":[{"delta":{"content":"Hello"},"index":0}],"model":"m"}\n\n'
        raise RuntimeError("stream broken")

    events = []
    async for chunk in stream_openai_to_anthropic(failing_stream(), "test-model"):
        events.append(chunk)

    # Should have at least message_start and synthetic stop events
    all_text = b"".join(events).decode()
    assert "message_start" in all_text
    assert "message_stop" in all_text


# ============================================================================
# conversion.py: stream_openai_to_anthropic - tool call stream
# ============================================================================


@pytest.mark.anyio
async def test_stream_tool_call_only():
    """Stream with only tool calls (no text)."""

    async def tool_stream():
        yield b'data: {"id":"c","choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"search","arguments":""}}]},"index":0}],"model":"m"}\n\n'
        yield b'data: {"id":"c","choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\"q\":\"test\"}"}}]},"index":0}],"model":"m"}\n\n'
        yield b'data: {"id":"c","choices":[{"delta":{},"finish_reason":"tool_calls","index":0}],"model":"m"}\n\n'
        yield b'data: [DONE]\n\n'

    events = []
    async for chunk in stream_openai_to_anthropic(tool_stream(), "test-model"):
        events.append(chunk)

    all_text = b"".join(events).decode()
    assert "tool_use" in all_text
    assert '"tool_use"' in all_text
