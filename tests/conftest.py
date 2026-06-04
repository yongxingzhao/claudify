"""Shared test fixtures for claudify tests."""

from __future__ import annotations

import httpx
import pytest

from claudify.app import create_app
from claudify.settings import Settings


@pytest.fixture
def make_client():
    """Return a factory that creates an httpx.AsyncClient wired to the FastAPI app,
    with the upstream OpenAI backend replaced by *handler* (httpx.MockTransport)."""
    def _factory(
        handler,
        *,
        model_map: dict[str, str] | None = None,
        default_model: str = "",
        max_body_size: int = 10 * 1024 * 1024,
        retry_attempts: int = 0,
        retry_backoff: float = 0.5,
        cors_origins: list[str] | None = None,
        upstream_health_path: str = "",
        inbound_api_key: str = "",
    ) -> tuple[httpx.AsyncClient, Settings]:
        s = Settings(
            backend_base="http://test-backend/v1",
            api_key="test-key",
            inbound_api_key=inbound_api_key,
            model_map=model_map if model_map is not None else {"claude-opus-4-7": "hermes-agent"},
            default_model=default_model,
            max_body_size=max_body_size,
            retry_attempts=retry_attempts,
            retry_backoff=retry_backoff,
            cors_origins=cors_origins or [],
            upstream_health_path=upstream_health_path,
        )
        mock_transport = httpx.MockTransport(handler)
        mock_client = httpx.AsyncClient(
            transport=mock_transport,
            base_url=s.backend_base,
            timeout=s.httpx_timeout(),
        )
        app = create_app(s, http_client=mock_client)
        asgi_transport = httpx.ASGITransport(app=app)
        client = httpx.AsyncClient(transport=asgi_transport, base_url="http://test")
        return client, s
    return _factory


@pytest.fixture
def chat_response():
    """Helper to build a standard OpenAI chat completion response."""
    def _factory(body="hi", finish="stop", model="m", usage=None):
        return {
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": body}, "finish_reason": finish}],
            "usage": usage or {"prompt_tokens": 5, "completion_tokens": 3},
        }
    return _factory
