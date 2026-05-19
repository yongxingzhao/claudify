"""Tests for FastAPI app endpoints."""

from __future__ import annotations

import httpx
import pytest


def _chat_response(body="hi", finish="stop", model="m", usage=None):
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": body}, "finish_reason": finish}],
        "usage": usage or {"prompt_tokens": 5, "completion_tokens": 3},
    }


def _handler(body="hi", status=200, usage=None):
    """Build a mock handler returning a standard chat response."""
    resp = _chat_response(body=body, usage=usage)
    def handler(request):
        return httpx.Response(status, json=resp)
    return handler


@pytest.mark.anyio
async def test_messages_non_stream(make_client):
    client, _ = make_client(_handler("hello"))
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
async def test_health(make_client):
    client, _ = make_client(lambda r: httpx.Response(200, json={}))
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    await client.aclose()


@pytest.mark.anyio
async def test_health_with_upstream_check(make_client):
    def handler(request):
        if "healthz" in str(request.url):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json=_chat_response())
    client, _ = make_client(handler, upstream_health_path="healthz")
    r = await client.get("/health")
    assert r.json()["upstream"] == "ok"
    await client.aclose()


@pytest.mark.anyio
async def test_metrics(make_client):
    client, _ = make_client(_handler("hi"))
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
        "messages": [],
    })
    assert r.status_code == 400
    assert "empty" in r.json()["error"]["message"]
    await client.aclose()


@pytest.mark.anyio
async def test_empty_messages_rejected(make_client):
    client, _ = make_client(_handler())
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [],
    })
    assert r.status_code == 400
    assert "empty" in r.json()["error"]["message"]
    await client.aclose()


@pytest.mark.anyio
async def test_error_message_sanitization(make_client):
    def handler(request):
        return httpx.Response(500, json={"error": {"message": "Internal error at https://api.evil.com with sk-abc123def456"}})
    client, _ = make_client(handler)
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 500
    msg = r.json()["error"]["message"]
    assert "https://api.evil.com" not in msg
    assert "sk-abc123def456" not in msg
    assert "redacted" in msg
    await client.aclose()


@pytest.mark.anyio
async def test_cors_headers(make_client):
    client, _ = make_client(_handler("hi"), cors_origins=["http://localhost:3000"])
    r = await client.options("/v1/messages", headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST",
    })
    assert "access-control-allow-origin" in r.headers
    await client.aclose()


@pytest.mark.anyio
async def test_anthropic_headers_forwarded(make_client):
    captured = {}
    def handler(request):
        captured["anthropic_beta"] = request.headers.get("anthropic-beta")
        captured["anthropic_version"] = request.headers.get("anthropic-version")
        return httpx.Response(200, json=_chat_response())
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
async def test_anthropic_version_default(make_client):
    captured = {}
    def handler(request):
        captured["anthropic_version"] = request.headers.get("anthropic-version")
        return httpx.Response(200, json=_chat_response())
    client, _ = make_client(handler)
    await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert captured["anthropic_version"] == "2023-06-01"
    await client.aclose()


@pytest.mark.anyio
async def test_x_api_key_forwarded(make_client):
    captured = {}
    def handler(request):
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=_chat_response())
    client, _ = make_client(handler)
    await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    }, headers={
        "x-api-key": "sk-test-12345678",
    })
    assert captured["auth"] == "Bearer sk-test-12345678"
    await client.aclose()


@pytest.mark.anyio
async def test_request_id_header(make_client):
    client, _ = make_client(_handler("hi"))
    r = await client.post("/v1/messages", json={
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert "x-request-id" in r.headers
    await client.aclose()


@pytest.mark.anyio
async def test_retry_on_503(make_client):
    call_count = 0
    def handler(request):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return httpx.Response(503, json={"error": {"message": "overloaded"}})
        return httpx.Response(200, json=_chat_response())
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
    client, _ = make_client(_handler())
    r = await client.post("/v1/messages", json={
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "invalid_request_error"
    assert "model" in r.json()["error"]["message"]
    await client.aclose()


@pytest.mark.anyio
async def test_missing_messages_field(make_client):
    client, _ = make_client(_handler())
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
    assert data["error"]["type"] == "api_error"
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
