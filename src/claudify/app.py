"""FastAPI application factory for Claudify proxy."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from claudify.errors import make_error_response
from claudify.metrics import Metrics
from claudify.routes import count_tokens, health, list_models, messages, metrics_endpoint
from claudify.settings import Settings

log = logging.getLogger("claudify")


class RequestIdMiddleware:
    """ASGI middleware that assigns a unique request ID."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] in ("http", "websocket"):
            rid = str(uuid.uuid4())[:8]
            scope.setdefault("state", {})
            scope["state"]["request_id"] = rid
        await self.app(scope, receive, send)


def create_app(settings: Settings | None = None, *, http_client: httpx.AsyncClient | None = None) -> FastAPI:
    settings = settings or Settings.load()
    metrics = Metrics()
    own_client = http_client is None

    client = http_client or httpx.AsyncClient(
        base_url=settings.backend_base,
        timeout=settings.httpx_timeout(),
    )

    async def lifespan(app_instance: FastAPI) -> AsyncIterator[None]:
        app_instance.state.http_client = client
        app_instance.state.settings = settings
        app_instance.state.metrics = metrics
        yield
        if own_client:
            await client.aclose()

    app = FastAPI(title="claudify", docs_url=None, redoc_url=None, lifespan=lifespan)

    # Eagerly set state for non-lifespan scenarios (e.g. ASGITransport in tests)
    app.state.http_client = client
    app.state.settings = settings
    app.state.metrics = metrics

    app.add_middleware(RequestIdMiddleware)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> Response:
        log.exception("unhandled error")
        return make_error_response("api_error", "internal error", 500)

    @app.post("/v1/messages")
    async def _messages(request: Request) -> Response:
        return await messages(
            request,
            client=app.state.http_client,
            settings=app.state.settings,
            metrics=app.state.metrics,
        )

    @app.get("/v1/models")
    async def _list_models() -> dict[str, Any]:
        return await list_models(settings=app.state.settings)

    @app.get("/health")
    async def _health() -> dict[str, Any]:
        return await health(client=app.state.http_client, settings=app.state.settings)

    @app.get("/metrics")
    async def _metrics() -> Response:
        return await metrics_endpoint(metrics=app.state.metrics)

    @app.post("/v1/messages/count_tokens")
    async def _count_tokens(request: Request) -> Response:
        return await count_tokens(request)

    return app
