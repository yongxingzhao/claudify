"""FastAPI app factory."""
from __future__ import annotations

import json as _json
import logging
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .conversion import (
    anthropic_to_openai,
    openai_to_anthropic_response,
    stream_openai_to_anthropic,
)
from .settings import Settings


def _passthrough_error(body: bytes) -> dict:
    try:
        parsed = _json.loads(body)
        if isinstance(parsed, dict):
            return {"error": {"type": "upstream_error", "message": str(parsed)}}
    except Exception:
        pass
    return {"error": {"type": "upstream_error", "message": body.decode("utf-8", errors="replace")[:2000]}}


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or Settings.load()
    log = logging.getLogger("claudify")
    app = FastAPI(title="claudify", version="0.1.0")

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
        headers = {"Content-Type": "application/json",
                   "Accept": "text/event-stream" if stream else "application/json"}
        if s.api_key:
            headers["Authorization"] = f"Bearer {s.api_key}"

        client = httpx.AsyncClient(timeout=s.request_timeout)
        try:
            if stream:
                req = client.build_request("POST", url, json=openai_payload, headers=headers)
                upstream = await client.send(req, stream=True)
                if upstream.status_code >= 400:
                    body = await upstream.aread()
                    await upstream.aclose()
                    await client.aclose()
                    return JSONResponse(status_code=upstream.status_code, content=_passthrough_error(body))

                async def relay():
                    try:
                        async for chunk in stream_openai_to_anthropic(upstream.aiter_lines(), original_model):
                            yield chunk
                    finally:
                        await upstream.aclose()
                        await client.aclose()

                return StreamingResponse(relay(), media_type="text/event-stream")

            resp = await client.post(url, json=openai_payload, headers=headers)
            await client.aclose()
            if resp.status_code >= 400:
                return JSONResponse(status_code=resp.status_code, content=_passthrough_error(resp.content))
            return JSONResponse(content=openai_to_anthropic_response(resp.json(), original_model))

        except httpx.HTTPError as e:
            await client.aclose()
            log.exception("upstream error")
            return JSONResponse(
                status_code=502,
                content={"error": {"type": "upstream_unavailable", "message": f"{type(e).__name__}: {e}"}},
            )
        except Exception as e:
            await client.aclose()
            log.exception("internal error")
            return JSONResponse(
                status_code=500,
                content={"error": {"type": "internal_error", "message": str(e)}},
            )

    return app
