"""FastAPI app factory."""
from __future__ import annotations

import json as _json
import logging
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .conversion import (
    anthropic_to_openai,
    openai_to_anthropic_response,
    stream_openai_to_anthropic,
)
from .settings import Settings


def _passthrough_error(body: bytes, status_code: int) -> dict:
    """Translate an upstream error body into an Anthropic-shaped error dict.

    Preserve upstream JSON structure when possible so clients can introspect.
    """
    text = body.decode("utf-8", errors="replace") if body else ""
    try:
        parsed = _json.loads(text)
    except Exception:
        parsed = None

    if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
        err = dict(parsed["error"])
        err.setdefault("type", "upstream_error")
        return {"error": err, "upstream_status": status_code}
    if isinstance(parsed, dict):
        return {
            "error": {"type": "upstream_error", "message": "upstream error"},
            "upstream_status": status_code,
            "upstream_body": parsed,
        }
    return {
        "error": {"type": "upstream_error", "message": text[:2000] or f"http {status_code}"},
        "upstream_status": status_code,
    }


def create_app(settings: Settings | None = None, *, http_client: httpx.AsyncClient | None = None) -> FastAPI:
    s = settings or Settings.load()
    log = logging.getLogger("claudify")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if http_client is not None:
            app.state.http = http_client
            app.state.owns_http = False
        else:
            app.state.http = httpx.AsyncClient(timeout=s.request_timeout)
            app.state.owns_http = True
        try:
            yield
        finally:
            if app.state.owns_http:
                await app.state.http.aclose()

    app = FastAPI(title="claudify", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/models")
    async def models() -> dict:
        ids = sorted(set(list(s.model_map.keys()) + ([s.default_model] if s.default_model else [])))
        return {
            "object": "list",
            "data": [{"id": m, "object": "model", "created": int(time.time()), "owned_by": "claudify"} for m in ids],
        }

    @app.post("/v1/messages")
    async def messages(request: Request):
        try:
            payload = await request.json()
        except Exception as e:
            return JSONResponse(
                status_code=400,
                content={"error": {"type": "invalid_request_error", "message": f"invalid JSON: {e}"}},
            )

        original_model = payload.get("model", "")
        stream = bool(payload.get("stream", False))
        openai_payload = anthropic_to_openai(payload, s.model_map, s.default_model)
        url = f"{s.backend_base.rstrip('/')}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
        }
        if s.api_key:
            headers["Authorization"] = f"Bearer {s.api_key}"

        client: httpx.AsyncClient = request.app.state.http

        try:
            if stream:
                req = client.build_request(
                    "POST", url, json=openai_payload, headers=headers,
                    timeout=s.request_timeout,
                )
                upstream = await client.send(req, stream=True)
                if upstream.status_code >= 400:
                    body = await upstream.aread()
                    await upstream.aclose()
                    return JSONResponse(
                        status_code=upstream.status_code,
                        content=_passthrough_error(body, upstream.status_code),
                    )

                async def relay():
                    try:
                        async for chunk in stream_openai_to_anthropic(upstream.aiter_lines(), original_model):
                            yield chunk
                    finally:
                        await upstream.aclose()

                return StreamingResponse(relay(), media_type="text/event-stream")

            resp = await client.post(url, json=openai_payload, headers=headers, timeout=s.request_timeout)
            if resp.status_code >= 400:
                return JSONResponse(
                    status_code=resp.status_code,
                    content=_passthrough_error(resp.content, resp.status_code),
                )
            return JSONResponse(content=openai_to_anthropic_response(resp.json(), original_model))

        except httpx.HTTPError as e:
            log.exception("upstream error")
            return JSONResponse(
                status_code=502,
                content={"error": {"type": "upstream_unavailable", "message": f"{type(e).__name__}: {e}"}},
            )
        except Exception as e:
            log.exception("internal error")
            return JSONResponse(
                status_code=500,
                content={"error": {"type": "internal_error", "message": str(e)}},
            )

    return app
