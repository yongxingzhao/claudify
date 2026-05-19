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

_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9]{8,}\b"),
    re.compile(r"\bkey-[A-Za-z0-9]{8,}\b"),
    re.compile(r"\btoken-[A-Za-z0-9]{8,}\b"),
    re.compile(r"\bBearer\s+\S+", re.IGNORECASE),
]


def _error_type_for_status(status: int) -> str:
    return _STATUS_TO_TYPE.get(status, "api_error")


def _sanitize_error_message(message: str) -> str:
    for pat in _SECRET_PATTERNS:
        message = pat.sub("[REDACTED]", message)
    return message


def make_error_response(error_type: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"type": "error", "error": {"type": error_type, "message": _sanitize_error_message(message)}},
    )


def passthrough_error(status: int, upstream_body: bytes | None = None) -> Response:
    error_type = _error_type_for_status(status)
    message = f"upstream returned {status}"
    if upstream_body:
        raw = upstream_body[:200]
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                err = parsed.get("error") or {}
                if isinstance(err, dict):
                    msg = err.get("message") or err.get("msg") or ""
                    if msg:
                        message = _sanitize_error_message(str(msg))
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    return JSONResponse(
        status_code=status,
        content={"type": "error", "error": {"type": error_type, "message": message}},
    )
