"""SSE (Server-Sent Events) helpers for Anthropic streaming protocol."""

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


class SSEParser:
    """Incremental SSE stream parser that handles chunk boundaries correctly."""

    def __init__(self) -> None:
        self._buf = ""
        self._done = False

    @property
    def done(self) -> bool:
        return self._done

    def feed(self, raw: bytes | str) -> list[dict[str, Any]]:
        """Feed raw bytes/string, return list of parsed SSE data dicts."""
        if isinstance(raw, bytes):
            chunk_text = raw.decode("utf-8", errors="replace")
        else:
            chunk_text = raw
        self._buf += chunk_text
        events: list[dict[str, Any]] = []
        while True:
            if "\n\n" in self._buf:
                event_text, self._buf = self._buf.split("\n\n", 1)
            elif self._buf.startswith("data:") and self._buf.endswith("\n"):
                event_text = self._buf.rstrip("\n")
                self._buf = ""
            else:
                break
            for line in event_text.split("\n"):
                if not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if body == "[DONE]":
                    self._done = True
                    break
                try:
                    events.append(json.loads(body))
                except json.JSONDecodeError:
                    continue
            if self._done:
                break
        return events


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
