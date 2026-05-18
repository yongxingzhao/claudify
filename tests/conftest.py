"""Shared test fixtures."""

from __future__ import annotations

import httpx
import pytest

from claudify.app import create_app
from claudify.settings import Settings


def _settings(**over) -> Settings:
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


@pytest.fixture
def make_client():
    """Factory fixture that returns (httpx.AsyncClient, FastAPI app) with a mock upstream."""

    def _make(handler, **settings_over):
        transport = httpx.MockTransport(handler)
        upstream = httpx.AsyncClient(transport=transport)
        app = create_app(_settings(**settings_over), http_client=upstream)
        return (
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver"),
            app,
        )

    return _make


@pytest.fixture
def noop_handler():
    """A handler that returns 500 — safe default for routes that don't reach upstream."""
    return lambda r: httpx.Response(500)
