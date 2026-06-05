"""Tests for FastAPI app endpoints."""

from __future__ import annotations

import json

import httpx
import pytest

from claudify.settings import Settings


@pytest.mark.anyio
async def test_messages_non_stream(make_client, chat_response):
    def handler(r):
        return httpx.Response(200, json=chat_response(body="hello"))
    client, _ = make_client(handler)
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 100,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["content"][0]["text"] == "hello"
    await client.aclose()


@pytest.mark.anyio
async def test_messages_upstream_500(make_client):
    def handler(request):
        return httpx.Response(500, json={"error": {"message": "Internal"}})
    client, _ = make_client(handler)
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 500
    data = r.json()
    assert data["type"] == "error"
    assert data["error"]["type"] == "api_error"
    await client.aclose()


@pytest.mark.anyio
async def test_messages_upstream_429(make_client):
    def handler(request):
        return httpx.Response(429, json={"error": {"message": "rate limited"}})
    client, _ = make_client(handler)
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 429
    assert r.json()["error"]["type"] == "rate_limit_error"
    await client.aclose()


@pytest.mark.anyio
async def test_messages_upstream_401(make_client):
    def handler(request):
        return httpx.Response(401, json={"error": {"message": "bad key"}})
    client, _ = make_client(handler)
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 401
    assert r.json()["error"]["type"] == "authentication_error"
    await client.aclose()


@pytest.mark.anyio
async def test_messages_upstream_404(make_client):
    def handler(request):
        return httpx.Response(404, json={"error": {"message": "not found"}})
    client, _ = make_client(handler)
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 404
    assert r.json()["error"]["type"] == "not_found_error"
    await client.aclose()


@pytest.mark.anyio
async def test_messages_invalid_json(make_client):
    client, _ = make_client(lambda r: httpx.Response(200, json={}))
    r = await client.post("/v1/messages", content=b"not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "invalid_request_error"
    await client.aclose()


@pytest.mark.anyio
async def test_messages_body_too_large(make_client):
    client, _ = make_client(lambda r: httpx.Response(200, json={}), max_body_size=100)
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "x" * 200}],
    })
    assert r.status_code == 413
    assert r.json()["error"]["type"] == "invalid_request_error"
    await client.aclose()


@pytest.mark.anyio
async def test_messages_must_be_list(make_client, chat_response):
    def handler(r):
        return httpx.Response(200, json=chat_response())
    client, _ = make_client(handler)
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": "not a list",
    })
    assert r.status_code == 400
    assert "array" in r.json()["error"]["message"]
    await client.aclose()


@pytest.mark.anyio
async def test_messages_role_required(make_client, chat_response):
    def handler(r):
        return httpx.Response(200, json=chat_response())
    client, _ = make_client(handler)
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"content": "no role field"}],
    })
    assert r.status_code == 400
    assert "role" in r.json()["error"]["message"]
    await client.aclose()


@pytest.mark.anyio
async def test_empty_messages_rejected(make_client, chat_response):
    def handler(r):
        return httpx.Response(200, json=chat_response())
    client, _ = make_client(handler)
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [],
    })
    assert r.status_code == 400
    assert "empty" in r.json()["error"]["message"]
    await client.aclose()


@pytest.mark.anyio
async def test_health(make_client):
    client, _ = make_client(lambda r: httpx.Response(200, json={}))
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    await client.aclose()


@pytest.mark.anyio
async def test_health_with_upstream_check(make_client, chat_response):
    def handler(request):
        if "healthz" in str(request.url):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json=chat_response())
    client, _ = make_client(handler, upstream_health_path="healthz")
    r = await client.get("/health")
    assert r.json()["upstream"] == "ok"
    await client.aclose()


@pytest.mark.anyio
async def test_metrics(make_client, chat_response):
    def handler(r):
        return httpx.Response(200, json=chat_response(body="hi"))
    client, _ = make_client(handler)
    await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    })
    r = await client.get("/metrics")
    assert r.status_code == 200
    assert "claudify_requests_total" in r.text
    await client.aclose()


@pytest.mark.anyio
async def test_list_models(make_client):
    client, _ = make_client(lambda r: httpx.Response(200, json={}), model_map={"claude-opus-4-7": "hermes-agent"})
    r = await client.get("/v1/models")
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "list"
    ids = [m["id"] for m in data["data"]]
    assert "claude-opus-4-7" in ids
    assert "created" in data["data"][0]
    assert "owned_by" in data["data"][0]
    await client.aclose()


@pytest.mark.anyio
async def test_list_models_empty(make_client):
    client, _ = make_client(lambda r: httpx.Response(200, json={}), model_map={}, default_model="")
    r = await client.get("/v1/models")
    assert r.status_code == 200
    data = r.json()
    assert len(data["data"]) >= 1
    assert data["data"][0]["id"] == "default"
    await client.aclose()


@pytest.mark.anyio
async def test_list_models_default_only(make_client):
    client, _ = make_client(lambda r: httpx.Response(200, json={}), model_map={}, default_model="gpt-4")
    r = await client.get("/v1/models")
    data = r.json()
    ids = [m["id"] for m in data["data"]]
    assert "gpt-4" in ids
    await client.aclose()


@pytest.mark.anyio
async def test_count_tokens(make_client):
    client, _ = make_client(lambda r: httpx.Response(200, json={}))
    r = await client.post("/v1/messages/count_tokens", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hello world"}],
    })
    assert r.status_code == 200
    assert "input_tokens" in r.json()
    await client.aclose()


@pytest.mark.anyio
async def test_count_tokens_invalid_json(make_client):
    client, _ = make_client(lambda r: httpx.Response(200, json={}))
    r = await client.post("/v1/messages/count_tokens", content=b"bad")
    assert r.status_code == 400
    await client.aclose()


@pytest.mark.anyio
async def test_count_tokens_empty_messages(make_client):
    client, _ = make_client(lambda r: httpx.Response(200, json={}))
    r = await client.post("/v1/messages/count_tokens", json={
        "model": "claude-opus-4-7",
        "messages": [],
    })
    assert r.status_code == 400
    assert "empty" in r.json()["error"]["message"]
    await client.aclose()


@pytest.mark.anyio
async def test_error_message_sanitization(make_client):
    def handler(request):
        return httpx.Response(500, json={"error": {"message": "key sk-abc123def leaked"}})
    client, _ = make_client(handler)
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 500
    msg = r.json()["error"]["message"]
    assert "sk-abc123def" not in msg
    assert "[REDACTED]" in msg
    await client.aclose()


@pytest.mark.anyio
async def test_cors_headers(make_client, chat_response):
    def handler(r):
        return httpx.Response(200, json=chat_response(body="hi"))
    client, _ = make_client(handler, cors_origins=["http://localhost:3000"])
    r = await client.options("/v1/messages", headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST",
    })
    assert "access-control-allow-origin" in r.headers
    await client.aclose()


@pytest.mark.anyio
async def test_anthropic_headers_forwarded(make_client, chat_response):
    captured = {}
    def handler(request):
        captured["anthropic_beta"] = request.headers.get("anthropic-beta")
        captured["anthropic_version"] = request.headers.get("anthropic-version")
        return httpx.Response(200, json=chat_response())
    client, _ = make_client(handler)
    await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    }, headers={
        "anthropic-beta": "max-tokens-3-5",
        "anthropic-version": "2023-06-01",
    })
    assert captured["anthropic_beta"] == "max-tokens-3-5"
    assert captured["anthropic_version"] == "2023-06-01"
    await client.aclose()


@pytest.mark.anyio
async def test_anthropic_version_default(make_client, chat_response):
    captured = {}
    def handler(request):
        captured["anthropic_version"] = request.headers.get("anthropic-version")
        return httpx.Response(200, json=chat_response())
    client, _ = make_client(handler)
    await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert captured["anthropic_version"] == "2023-06-01"
    await client.aclose()


@pytest.mark.anyio
async def test_x_api_key_forwarded(make_client, chat_response):
    captured = {}
    def handler(request):
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=chat_response())
    client, _ = make_client(handler)
    await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    }, headers={
        "x-api-key": "sk-test-key",
    })
    assert captured["auth"] == "Bearer sk-test-key"
    await client.aclose()


@pytest.mark.anyio
async def test_retry_on_503(make_client, chat_response):
    call_count = 0
    def handler(request):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return httpx.Response(503, json={"error": {"message": "overloaded"}})
        return httpx.Response(200, json=chat_response())
    client, _ = make_client(handler, retry_attempts=3, retry_backoff=0.01)
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 200
    assert call_count == 3
    await client.aclose()


@pytest.mark.anyio
async def test_missing_model_field(make_client):
    client, _ = make_client(lambda r: httpx.Response(200, json={}))
    r = await client.post("/v1/messages", json={
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "invalid_request_error"
    assert "model" in r.json()["error"]["message"]
    await client.aclose()


@pytest.mark.anyio
async def test_missing_messages_field(make_client):
    client, _ = make_client(lambda r: httpx.Response(200, json={}))
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
    })
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "invalid_request_error"
    assert "messages" in r.json()["error"]["message"]
    await client.aclose()


@pytest.mark.anyio
async def test_non_json_upstream_error(make_client):
    def handler(request):
        return httpx.Response(502, content=b"<html>Bad Gateway</html>", headers={"content-type": "text/html"})
    client, _ = make_client(handler)
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 502
    data = r.json()
    assert data["error"]["type"] == "upstream_unavailable"
    assert "502" in data["error"]["message"]
    await client.aclose()


@pytest.mark.anyio
async def test_upstream_error_type_override(make_client):
    def handler(request):
        return httpx.Response(429, json={"error": {"type": "service_unavailable", "message": "slow down"}})
    client, _ = make_client(handler)
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 429
    assert r.json()["error"]["type"] == "rate_limit_error"
    await client.aclose()


@pytest.mark.anyio
async def test_request_id_in_response(make_client, chat_response):
    def handler(r):
        return httpx.Response(200, json=chat_response(body="hi"))
    client, _ = make_client(handler)
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert "x-request-id" in r.headers
    await client.aclose()


@pytest.mark.anyio
async def test_inbound_api_key_valid(make_client, chat_response):
    def handler(r):
        return httpx.Response(200, json=chat_response(body="hi"))
    client, _ = make_client(handler, inbound_api_key="my-secret-key")
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    }, headers={"x-api-key": "my-secret-key"})
    assert r.status_code == 200
    await client.aclose()


@pytest.mark.anyio
async def test_inbound_api_key_invalid(make_client, chat_response):
    def handler(r):
        return httpx.Response(200, json=chat_response(body="hi"))
    client, _ = make_client(handler, inbound_api_key="my-secret-key")
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    }, headers={"x-api-key": "wrong-key"})
    assert r.status_code == 401
    assert r.json()["error"]["type"] == "authentication_error"
    await client.aclose()


@pytest.mark.anyio
async def test_inbound_api_key_missing(make_client, chat_response):
    def handler(r):
        return httpx.Response(200, json=chat_response(body="hi"))
    client, _ = make_client(handler, inbound_api_key="my-secret-key")
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 401
    await client.aclose()


@pytest.mark.anyio
async def test_count_tokens_non_dict_payload(make_client):
    client, _ = make_client(lambda r: httpx.Response(200, json={}))
    r = await client.post("/v1/messages/count_tokens", json=[1, 2, 3])
    assert r.status_code == 400
    assert "JSON object" in r.json()["error"]["message"]
    await client.aclose()


@pytest.mark.anyio
async def test_count_tokens_with_system_prompt(make_client):
    client, _ = make_client(lambda r: httpx.Response(200, json={}))
    r = await client.post("/v1/messages/count_tokens", json={
        "model": "claude-opus-4-7",
        "system": "You are a helpful assistant.",
        "messages": [{"role": "user", "content": "hello world"}],
    })
    assert r.status_code == 200
    tokens_with_system = r.json()["input_tokens"]

    r2 = await client.post("/v1/messages/count_tokens", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hello world"}],
    })
    tokens_without_system = r2.json()["input_tokens"]
    assert tokens_with_system > tokens_without_system
    await client.aclose()


# ---------- T2: streaming retry test ---------------------------------------


@pytest.mark.anyio
async def test_stream_retry_on_503(make_client):
    """Streaming requests should retry on 503 and succeed."""
    call_count = 0

    def handler(request):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return httpx.Response(503, json={"error": {"message": "overloaded"}})
        # Return a valid streaming response
        chunks = [
            {"id": "chatcmpl-1", "choices": [{"delta": {"role": "assistant"}, "index": 0}]},
            {"id": "chatcmpl-1", "choices": [{"delta": {"content": "ok"}, "index": 0}]},
            {"id": "chatcmpl-1", "choices": [{"delta": {}, "finish_reason": "stop", "index": 0}]},
            {"id": "chatcmpl-1", "choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
        ]
        lines = []
        import json as _json
        for c in chunks:
            lines.append(f"data: {_json.dumps(c)}")
        lines.append("data: [DONE]")
        content = "\n\n".join(lines) + "\n\n"
        return httpx.Response(200, content=content.encode(), headers={"content-type": "text/event-stream"})

    client, _ = make_client(handler, retry_attempts=3, retry_backoff=0.01)
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    })
    assert r.status_code == 200
    assert call_count == 3
    await client.aclose()


# ---------- T3: connection error and timeout tests -------------------------


@pytest.mark.anyio
async def test_upstream_connect_error(make_client, chat_response):
    """ConnectError should return 502."""
    def handler(request):
        raise httpx.ConnectError("connection refused")

    # MockTransport doesn't raise exceptions, so we need a different approach.
    # Use a custom transport that raises on request.
    s = Settings(
        backend_base="http://test-backend/v1",
        api_key="test-key",
        model_map={"claude-opus-4-7": "hermes-agent"},
    )
    # We'll test the error handler directly via a transport that raises
    import httpx as _httpx

    from claudify.app import create_app

    class ConnectErrorTransport(_httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise _httpx.ConnectError("connection refused")

    mock_client = _httpx.AsyncClient(transport=ConnectErrorTransport(), base_url=s.backend_base)
    app = create_app(s, http_client=mock_client)
    asgi_transport = _httpx.ASGITransport(app=app)
    client = _httpx.AsyncClient(transport=asgi_transport, base_url="http://test")

    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 502
    assert r.json()["error"]["type"] == "upstream_unavailable"
    await client.aclose()


@pytest.mark.anyio
async def test_upstream_timeout(make_client):
    """TimeoutException should return 504."""
    s = Settings(
        backend_base="http://test-backend/v1",
        api_key="test-key",
        model_map={"claude-opus-4-7": "hermes-agent"},
    )
    import httpx as _httpx

    from claudify.app import create_app

    class TimeoutTransport(_httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise _httpx.TimeoutException("read timeout")

    mock_client = _httpx.AsyncClient(transport=TimeoutTransport(), base_url=s.backend_base)
    app = create_app(s, http_client=mock_client)
    asgi_transport = _httpx.ASGITransport(app=app)
    client = _httpx.AsyncClient(transport=asgi_transport, base_url="http://test")

    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 504
    assert r.json()["error"]["type"] == "timeout_error"
    await client.aclose()


# ---------- T7: Bearer token sanitization test -----------------------------


@pytest.mark.anyio
async def test_bearer_token_sanitization(make_client):
    def handler(request):
        return httpx.Response(500, json={"error": {"message": "Invalid Bearer sk-supersecret123 token"}})
    client, _ = make_client(handler)
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 500
    msg = r.json()["error"]["message"]
    assert "sk-supersecret123" not in msg
    assert "[REDACTED]" in msg
    await client.aclose()


# ---------- Round 3: additional tests -------------------------------------


@pytest.mark.anyio
async def test_inbound_key_not_forwarded_upstream(make_client, chat_response):
    """When inbound_api_key is set, the inbound x-api-key should NOT be forwarded upstream."""
    captured = {}

    def handler(request):
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=chat_response(body="hi"))

    client, _ = make_client(handler, inbound_api_key="my-inbound-key", api_key="upstream-key")
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    }, headers={"x-api-key": "my-inbound-key"})
    assert r.status_code == 200
    # Upstream should get the configured api_key, NOT the inbound key
    assert captured["auth"] == "Bearer upstream-key"
    await client.aclose()


@pytest.mark.anyio
async def test_count_tokens_inbound_auth(make_client):
    """count_tokens endpoint should require inbound auth when configured."""
    client, _ = make_client(lambda r: httpx.Response(200, json={}), inbound_api_key="secret")
    r = await client.post("/v1/messages/count_tokens", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hello"}],
    })
    assert r.status_code == 401
    # Now with valid key
    r2 = await client.post("/v1/messages/count_tokens", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hello"}],
    }, headers={"x-api-key": "secret"})
    assert r2.status_code == 200
    await client.aclose()


@pytest.mark.anyio
async def test_count_tokens_response_format(make_client):
    """count_tokens should return id and type fields per Anthropic spec."""
    client, _ = make_client(lambda r: httpx.Response(200, json={}))
    r = await client.post("/v1/messages/count_tokens", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hello"}],
    })
    assert r.status_code == 200
    data = r.json()
    assert "id" in data
    assert data["type"] == "token_count"
    assert "input_tokens" in data
    await client.aclose()


@pytest.mark.anyio
async def test_empty_content_in_response(make_client):
    """When upstream returns empty content, response should still have a content array."""
    def handler(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 0},
        })
    client, _ = make_client(handler)
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 200
    data = r.json()
    assert len(data["content"]) > 0  # Anthropic requires non-empty content
    await client.aclose()


@pytest.mark.anyio
async def test_count_tokens_missing_model(make_client):
    """count_tokens should require model field."""
    client, _ = make_client(lambda r: httpx.Response(200, json={}))
    r = await client.post("/v1/messages/count_tokens", json={
        "messages": [{"role": "user", "content": "hello"}],
    })
    assert r.status_code == 400
    assert "model" in r.json()["error"]["message"]
    await client.aclose()


@pytest.mark.anyio
async def test_health_unreachable_upstream(make_client):
    """Health endpoint should report unreachable when upstream is down."""
    def handler(request):
        raise httpx.ConnectError("connection refused")
    client, _ = make_client(handler, upstream_health_path="/health")
    r = await client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["upstream"] == "unreachable"
    await client.aclose()


@pytest.mark.anyio
async def test_remote_protocol_error(make_client):
    """RemoteProtocolError should return 502, not 500."""
    def handler(request):
        raise httpx.RemoteProtocolError("connection lost")
    client, _ = make_client(handler)
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 502
    await client.aclose()


@pytest.mark.anyio
async def test_upstream_invalid_json(make_client):
    """200 response with invalid JSON should return 502."""
    def handler(request):
        return httpx.Response(200, text="not json", headers={"content-type": "application/json"})
    client, _ = make_client(handler)
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 502
    await client.aclose()


@pytest.mark.anyio
async def test_unhandled_exception_returns_api_error(make_client, chat_response):
    """Unhandled exception handler returns Anthropic error format with request ID."""
    from claudify.errors import make_error_response

    def handler(r):
        return httpx.Response(200, json=chat_response(body="hi"))
    client, _ = make_client(handler)
    # Test the error handler output format directly (the handler is registered
    # via @app.exception_handler(Exception) and uses make_error_response)
    resp = make_error_response("api_error", "internal error (rid=abc123)", 500)
    assert resp.status_code == 500
    body = json.loads(resp.body)
    assert body["type"] == "error"
    assert body["error"]["type"] == "api_error"
    assert "abc123" in body["error"]["message"]
    # Without rid
    resp2 = make_error_response("api_error", "internal error", 500)
    body2 = json.loads(resp2.body)
    assert "internal error" in body2["error"]["message"]
    await client.aclose()
