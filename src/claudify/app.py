"""FastAPI application factory: Anthropic Messages API proxy to OpenAI backend."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from claudify.metrics import Metrics
from claudify.routes import count_tokens, health, list_models, messages, metrics_endpoint
from claudify.settings import Settings


@asynccontextmanager
async def _lifespan(app: FastAPI):
    yield
    if hasattr(app.state, "http_client") and app.state.http_client:
        await app.state.http_client.aclose()


def create_app(settings: Settings | None = None, *, http_client: httpx.AsyncClient | None = None) -> FastAPI:
    settings = settings or Settings.load()
    metrics = Metrics()

    app = FastAPI(lifespan=_lifespan)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["POST", "GET", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "x-api-key", "anthropic-version", "anthropic-beta"],
        )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next: Any) -> Response:
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        return response

    client = http_client or httpx.AsyncClient(
        base_url=settings.backend_base,
        timeout=settings.httpx_timeout(streaming=True),
    )
    app.state.http_client = client
    app.state.settings = settings

    @app.post("/v1/messages")
    async def _messages(request: Request) -> Response:
        return await messages(request, client, settings, metrics)

    @app.get("/v1/models")
    async def _list_models() -> dict[str, Any]:
        return await list_models(settings)

    @app.get("/health")
    async def _health() -> dict[str, Any]:
        return await health(client, settings)

    @app.get("/metrics")
    async def _metrics() -> Response:
        return await metrics_endpoint(metrics)

    @app.post("/v1/messages/count_tokens")
    async def _count_tokens(request: Request) -> Response:
        return await count_tokens(request)

    return app
