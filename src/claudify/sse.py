"""SSE (Server-Sent Events) helper functions for Anthropic streaming protocol."""

from __future__ import annotations

import json
from typing import Any


def sse_event(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',',':'))}\n\n".encode()


def sse_ping() -> bytes:
    return b"event: ping\ndata: {}\n\n"


STOP_REASON_MAP: dict[str, str] = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}


def extract_usage(upstream_usage: dict[str, Any] | None) -> dict[str, Any]:
    if upstream_usage:
        return {
            "input_tokens": upstream_usage.get("prompt_tokens", 0) or 0,
            "output_tokens": upstream_usage.get("completion_tokens", 0) or 0,
        }
    return {"input_tokens": 0, "output_tokens": 0}


def synthetic_stop_events(
    finish_reason: str, upstream_usage: dict[str, Any] | None
) -> list[bytes]:
    stop_reason = STOP_REASON_MAP.get(finish_reason, "end_turn")
    return [
        sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": extract_usage(upstream_usage),
            },
        ),
        sse_event("message_stop", {"type": "message_stop"}),
    ]
