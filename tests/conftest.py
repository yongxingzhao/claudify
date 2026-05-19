"""Shared test fixtures for claudify tests."""

from __future__ import annotations

import httpx
import pytest

from claudify.app import create_app
from claudify.settings import Settings


@pytest.fixture
def noop_handler():
    return lambda req: httpx.Response(200, json={"status": "ok"})


@pytest.fixture
def make_client():
    def _make(
        handler,
        *,
        retry_attempts: int = 0,
        retry_backoff: float = 0.5,
        model_map: dict | None = None,
        default_model: str = "",
        max_body_size: int = 10 * 1024 * 1024,
        cors_origins: list | None = None,
        upstream_health_path: str = "",
        **settings_overrides,
    ):
        base = dict(
            backend_base="http://upstream/v1",
            api_key="sk-test",
            host="127.0.0.1",
            port=4000,
            log_level="WARNING",
            request_timeout=10.0,
            retry_attempts=retry_attempts,
            retry_backoff=retry_backoff,
            model_map=model_map if model_map is not None else {"claude-opus-4-7": "hermes-agent"},
            default_model=default_model,
            max_body_size=max_body_size,
            cors_origins=cors_origins or [],
            upstream_health_path=upstream_health_path,
        )
        base.update(settings_overrides)
        s = Settings(**base)
        transport = httpx.MockTransport(handler)
        upstream = httpx.AsyncClient(transport=transport)
        app = create_app(s, http_client=upstream)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
        return client, app

    return _make
