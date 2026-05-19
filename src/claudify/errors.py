"""Error handling: upstream passthrough, sanitization, type mapping."""

from __future__ import annotations

import json
import re

from fastapi.responses import Response

ERROR_TYPE_MAP: dict[int, str] = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    429: "rate_limit_error",
    500: "api_error",
    502: "api_error",
    503: "overloaded_error",
    504: "api_error",
}


def sanitize_error_message(msg: str) -> str:
    msg = re.sub(r'\bsk-[A-Za-z0-9]{8,}\b', '[redacted-key]', msg)
    msg = re.sub(r'https?://[^\s"\'<>]+', '[redacted-url]', msg)
    return msg


def make_error_response(
    error_type: str,
    message: str,
    status_code: int = 400,
) -> Response:
    return Response(
        content=json.dumps({"type": "error", "error": {"type": error_type, "message": message}}),
        status_code=status_code,
        media_type="application/json",
    )


def passthrough_error(status: int, upstream_body: bytes | None = None) -> Response:
    error_type = ERROR_TYPE_MAP.get(status, "api_error")
    message = f"Upstream returned {status}"
    if upstream_body:
        try:
            body = json.loads(upstream_body)
            err = body if isinstance(body, dict) else {}
            raw_msg = (err.get("error") or {}).get("message", "") if isinstance(err.get("error"), dict) else str(err)
            if raw_msg:
                message = sanitize_error_message(raw_msg)
        except (json.JSONDecodeError, AttributeError):
            snippet = upstream_body[:200].decode("utf-8", errors="replace").strip()
            if snippet:
                message = sanitize_error_message(f"Upstream {status}: {snippet}")
    return make_error_response(error_type, message, status)
