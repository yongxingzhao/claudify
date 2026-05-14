"""Pure functions for Anthropic ↔ OpenAI protocol conversion."""
from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator


def map_model(model: str, model_map: dict[str, str], default: str = "") -> str:
    if model in model_map:
        return model_map[model]
    if default:
        return default
    return model


def extract_text_from_blocks(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append(block.get("text", ""))
        elif btype == "tool_result":
            tc = block.get("content")
            if isinstance(tc, str):
                parts.append(tc)
            elif isinstance(tc, list):
                for sub in tc:
                    if isinstance(sub, dict) and sub.get("type") == "text":
                        parts.append(sub.get("text", ""))
        elif btype == "thinking":
            pass
        elif btype == "image":
            parts.append("[image omitted]")
    return "\n".join(p for p in parts if p)


def anthropic_to_openai(payload: dict[str, Any], model_map: dict[str, str], default_model: str = "") -> dict[str, Any]:
    out_messages: list[dict[str, Any]] = []
    system = payload.get("system")
    if isinstance(system, str) and system.strip():
        out_messages.append({"role": "system", "content": system})
    elif isinstance(system, list):
        sys_text = extract_text_from_blocks(system)
        if sys_text.strip():
            out_messages.append({"role": "system", "content": sys_text})
    for msg in payload.get("messages", []):
        role = msg.get("role")
        content = msg.get("content")
        text = extract_text_from_blocks(content) if not isinstance(content, str) else content
        if role in ("user", "assistant") and text:
            out_messages.append({"role": role, "content": text})
    has_user = any(m["role"] == "user" for m in out_messages)
    if not has_user:
        out_messages.append({"role": "user", "content": "."})
    model = map_model(payload.get("model", ""), model_map, default_model)
    openai_payload: dict[str, Any] = {
        "model": model,
        "messages": out_messages,
        "stream": bool(payload.get("stream", False)),
    }
    for k in ("temperature", "top_p", "max_tokens", "stop_sequences"):
        if k in payload:
            target = "stop" if k == "stop_sequences" else k
            openai_payload[target] = payload[k]
    return openai_payload


def openai_to_anthropic_response(openai_resp: dict[str, Any], original_model: str) -> dict[str, Any]:
    choice = (openai_resp.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    text = msg.get("content") or ""
    finish = choice.get("finish_reason") or "stop"
    stop_reason_map = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}
    usage = openai_resp.get("usage") or {}
    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": original_model,
        "content": [{"type": "text", "text": text}] if text else [],
        "stop_reason": stop_reason_map.get(finish, "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


async def stream_openai_to_anthropic(
    openai_lines: AsyncIterator[bytes],
    original_model: str,
) -> AsyncIterator[bytes]:
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    def sse(event: str, data: dict[str, Any]) -> bytes:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")
    yield sse("message_start", {
        "type": "message_start",
        "message": {
            "id": msg_id, "type": "message", "role": "assistant", "model": original_model,
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    })
    yield sse("content_block_start", {
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "text", "text": ""},
    })
    finish_reason = "stop"
    output_tokens = 0
    async for raw in openai_lines:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        body = line[5:].strip()
        if body == "[DONE]":
            break
        try:
            chunk = json.loads(body)
        except json.JSONDecodeError:
            continue
        choices = chunk.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        piece = delta.get("content")
        if piece:
            output_tokens += 1
            yield sse("content_block_delta", {
                "type": "content_block_delta", "index": 0,
                "delta": {"type": "text_delta", "text": piece},
            })
        if choices[0].get("finish_reason"):
            finish_reason = choices[0]["finish_reason"]
    stop_map = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}
    yield sse("content_block_stop", {"type": "content_block_stop", "index": 0})
    yield sse("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": stop_map.get(finish_reason, "end_turn"), "stop_sequence": None},
        "usage": {"output_tokens": output_tokens},
    })
    yield sse("message_stop", {"type": "message_stop"})
