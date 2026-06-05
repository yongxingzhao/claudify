"""Error handling utilities for Anthropic-protocol responses."""

from __future__ import annotations

import json
import re

from fastapi.responses import JSONResponse, Response

_STATUS_TO_TYPE: dict[int, str] = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    429: "rate_limit_error",
    500: "api_error",
    502: "upstream_unavailable",
    503: "overloaded_error",
    504: "timeout_error",
}

_SECRET_PATTERN = re.compile(
    r"\b(?:sk|key|token)-[A-Za-z0-9]{8,}\b|\bBearer\s+\S+",
    re.IGNORECASE,
)


def _error_type_for_status(status: int) -> str:
    return _STATUS_TO_TYPE.get(status, "api_error")


def _sanitize_error_message(message: str) -> str:
    return _SECRET_PATTERN.sub("[REDACTED]", message)


def make_error_response(error_type: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"type": "error", "error": {"type": error_type, "message": _sanitize_error_message(message)}},
    )


def passthrough_error(status: int, upstream_body: bytes | None = None) -> Response:
    error_type = _error_type_for_status(status)
    message = f"upstream returned {status}"
    if upstream_body:
        # Try to decode the full body first; fall back to truncated for safety
        try:
            decoded = upstream_body.decode("utf-8", errors="replace")
        except Exception:
            decoded = ""
        if decoded:
            try:
                parsed = json.loads(decoded)
                if isinstance(parsed, dict):
                    err = parsed.get("error") or {}
                    if isinstance(err, dict):
                        msg = err.get("message") or err.get("msg") or ""
                        if msg:
                            message = _sanitize_error_message(str(msg))
            except (json.JSONDecodeError, ValueError):
                pass
    return JSONResponse(
        status_code=status,
        content={"type": "error", "error": {"type": error_type, "message": message}},
    )
