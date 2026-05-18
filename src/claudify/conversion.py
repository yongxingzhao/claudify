"""Pure functions for Anthropic ↔ OpenAI protocol conversion."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

log = logging.getLogger("claudify.conversion")


def map_model(model: str, model_map: dict[str, str], default: str = "") -> str:
    if model in model_map:
        return model_map[model]
    if default:
        return default
    return model


def _image_block_to_openai_part(block: dict[str, Any]) -> dict[str, Any] | None:
    src = block.get("source") or {}
    stype = src.get("type")
    if stype == "base64":
        media = src.get("media_type") or "image/png"
        data = src.get("data") or ""
        if not data:
            return None
        return {"type": "image_url", "image_url": {"url": f"data:{media};base64,{data}"}}
    if stype == "url":
        url = src.get("url")
        if not url:
            return None
        return {"type": "image_url", "image_url": {"url": url}}
    return None


def _system_to_openai(system: Any) -> str:
    if isinstance(system, str):
        return system
    if not isinstance(system, list):
        return ""
    parts: list[str] = []
    for block in system:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
        # cache_control is Anthropic-specific; silently ignore.
    return "\n".join(p for p in parts if p)


def _user_content_to_openai(content: Any) -> tuple[Any, list[dict[str, Any]]]:
    """Return (openai_user_content, tool_messages).

    Anthropic puts tool_result blocks inside a user message; OpenAI expects them as
    separate {"role": "tool", ...} messages. We split them out here.
    """
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return "", []

    parts: list[dict[str, Any]] = []
    tool_msgs: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        # cache_control is Anthropic-specific; strip silently.
        block.pop("cache_control", None)
        if btype == "text":
            parts.append({"type": "text", "text": block.get("text", "")})
        elif btype == "image":
            part = _image_block_to_openai_part(block)
            if part:
                parts.append(part)
        elif btype == "tool_result":
            tc = block.get("content")
            if isinstance(tc, str):
                tool_text = tc
            elif isinstance(tc, list):
                tool_text = "\n".join(
                    sub.get("text", "") for sub in tc if isinstance(sub, dict) and sub.get("type") == "text"
                )
            else:
                tool_text = ""
            if block.get("is_error"):
                tool_text = f"[tool_error] {tool_text}".rstrip()
            tool_msgs.append(
                {
                    "role": "tool",
                    "tool_call_id": block.get("tool_use_id") or "",
                    "content": tool_text,
                }
            )

    if not parts:
        return "", tool_msgs
    if len(parts) == 1 and parts[0]["type"] == "text":
        return parts[0]["text"], tool_msgs
    return parts, tool_msgs


def _assistant_content_to_openai(content: Any) -> tuple[str, list[dict[str, Any]]]:
    """Return (text, tool_calls). Anthropic tool_use blocks → OpenAI tool_calls."""
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return "", []

    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text_parts.append(block.get("text", ""))
        elif btype == "tool_use":
            tool_calls.append(
                {
                    "id": block.get("id") or f"call_{uuid.uuid4().hex[:24]}",
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                    },
                }
            )
        elif btype == "thinking":
            # Anthropic extended thinking; OpenAI has no equivalent.
            # Drop silently — clients that request thinking will not receive it back
            # through the OpenAI backend, but the request still succeeds.
            log.debug("dropping thinking block (%d chars)", len(block.get("thinking", "")))
    return "\n".join(p for p in text_parts if p), tool_calls


def extract_text_from_blocks(content: Any) -> str:
    """Backward-compatible text extractor (kept for tests / callers)."""
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
        elif btype == "image":
            parts.append("[image omitted]")
    return "\n".join(p for p in parts if p)


def _convert_tools(tools: Any) -> list[dict[str, Any]] | None:
    if not isinstance(tools, list) or not tools:
        return None
    out: list[dict[str, Any]] = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        name = t.get("name")
        if not name:
            continue
        out.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
        )
    return out or None


def _convert_tool_choice(tc: Any) -> Any:
    if not isinstance(tc, dict):
        return None
    ttype = tc.get("type")
    if ttype == "auto":
        return "auto"
    if ttype == "any":
        return "required"
    if ttype == "tool" and tc.get("name"):
        return {"type": "function", "function": {"name": tc["name"]}}
    return None


def anthropic_to_openai(
    payload: dict[str, Any], model_map: dict[str, str], default_model: str = ""
) -> dict[str, Any]:
    out_messages: list[dict[str, Any]] = []

    sys_text = _system_to_openai(payload.get("system"))
    if sys_text.strip():
        out_messages.append({"role": "system", "content": sys_text})

    for msg in payload.get("messages", []):
        role = msg.get("role")
        content = msg.get("content")
        if role == "user":
            user_content, tool_msgs = _user_content_to_openai(content)
            out_messages.extend(tool_msgs)
            if user_content not in ("", []):
                out_messages.append({"role": "user", "content": user_content})
        elif role == "assistant":
            text, tool_calls = _assistant_content_to_openai(content)
            entry: dict[str, Any] = {"role": "assistant", "content": text or None}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            if entry["content"] is None and not tool_calls:
                continue
            out_messages.append(entry)

    has_user = any(m["role"] == "user" for m in out_messages)
    if not has_user:
        out_messages.append({"role": "user", "content": "."})

    model = map_model(payload.get("model", ""), model_map, default_model)
    openai_payload: dict[str, Any] = {
        "model": model,
        "messages": out_messages,
        "stream": bool(payload.get("stream", False)),
    }
    for k in ("temperature", "top_p", "max_tokens"):
        if k in payload:
            openai_payload[k] = payload[k]
    if "stop_sequences" in payload:
        openai_payload["stop"] = payload["stop_sequences"]

    # top_k is not part of OpenAI spec, but some backends support it via extra params.
    if "top_k" in payload:
        openai_payload["top_k"] = payload["top_k"]

    tools = _convert_tools(payload.get("tools"))
    if tools:
        openai_payload["tools"] = tools
    tc = _convert_tool_choice(payload.get("tool_choice"))
    if tc is not None:
        openai_payload["tool_choice"] = tc

    metadata = payload.get("metadata")
    if isinstance(metadata, dict) and metadata.get("user_id"):
        openai_payload["user"] = str(metadata["user_id"])

    if openai_payload["stream"]:
        openai_payload["stream_options"] = {"include_usage": True}

    return openai_payload


_STOP_REASON_MAP = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}


def _parse_tool_arguments(arguments: str) -> dict[str, Any]:
    if not arguments:
        return {}
    try:
        parsed = json.loads(arguments)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return {"_raw": arguments}


def openai_to_anthropic_response(openai_resp: dict[str, Any], original_model: str) -> dict[str, Any]:
    choice = (openai_resp.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    text = msg.get("content") or ""
    finish = choice.get("finish_reason") or "stop"
    usage = openai_resp.get("usage") or {}

    content: list[dict[str, Any]] = []
    if text:
        content.append({"type": "text", "text": text})
    for tc in msg.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        content.append(
            {
                "type": "tool_use",
                "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:24]}",
                "name": fn.get("name", ""),
                "input": _parse_tool_arguments(fn.get("arguments", "")),
            }
        )

    result: dict[str, Any] = {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": original_model,
        "content": content,
        "stop_reason": _STOP_REASON_MAP.get(finish, "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0) or 0,
            "output_tokens": usage.get("completion_tokens", 0) or 0,
        },
    }

    # Map OpenAI user field back to Anthropic metadata.user_id.
    user = openai_resp.get("user")
    if user:
        result["metadata"] = {"user_id": user}

    return result


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',',':'))}\n\n".encode()


async def stream_openai_to_anthropic(
    openai_stream: AsyncIterator[bytes],
    original_model: str,
) -> AsyncIterator[bytes]:
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"

    yield _sse(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "model": original_model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        },
    )

    finish_reason = "stop"
    upstream_usage: dict[str, Any] | None = None
    text_block_open = False
    text_block_index = 0
    next_index = 0
    tool_state: dict[int, dict[str, Any]] = {}

    # Buffer for reassembling SSE events from byte chunks.
    buf = ""

    try:
        async for raw in openai_stream:
            if isinstance(raw, bytes):
                chunk_text = raw.decode("utf-8", errors="replace")
            else:
                chunk_text = raw
            buf += chunk_text
            # SSE events are delimited by blank lines (\n\n).
            # Also handle line-by-line input (each line is its own event).
            while True:
                if "\n\n" in buf:
                    event_text, buf = buf.split("\n\n", 1)
                elif buf.startswith("data:") and buf.endswith("\n"):
                    # Single line without trailing blank line — treat as one event
                    event_text = buf.rstrip("\n")
                    buf = ""
                else:
                    break
                for line in event_text.split("\n"):
                    if not line.startswith("data:"):
                        continue
                    body = line[5:].strip()
                    if body == "[DONE]":
                        break
                    try:
                        chunk = json.loads(body)
                    except json.JSONDecodeError:
                        continue

                    if isinstance(chunk.get("usage"), dict):
                        upstream_usage = chunk["usage"]

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice0 = choices[0]
                    delta = choice0.get("delta") or {}

                    piece = delta.get("content")
                    if piece:
                        if not text_block_open:
                            yield _sse(
                                "content_block_start",
                                {
                                    "type": "content_block_start",
                                    "index": next_index,
                                    "content_block": {"type": "text", "text": ""},
                                },
                            )
                            text_block_open = True
                            text_block_index = next_index
                            next_index += 1
                        yield _sse(
                            "content_block_delta",
                            {
                                "type": "content_block_delta",
                                "index": text_block_index,
                                "delta": {"type": "text_delta", "text": piece},
                            },
                        )

                    for tc in delta.get("tool_calls") or []:
                        if not isinstance(tc, dict):
                            continue
                        up_idx = tc.get("index", 0)
                        state = tool_state.get(up_idx)
                        if state is None:
                            if text_block_open:
                                yield _sse(
                                    "content_block_stop",
                                    {
                                        "type": "content_block_stop",
                                        "index": text_block_index,
                                    },
                                )
                                text_block_open = False
                            fn = tc.get("function") or {}
                            state = {
                                "block_index": next_index,
                                "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:24]}",
                                "name": fn.get("name", ""),
                                "args": "",
                            }
                            tool_state[up_idx] = state
                            next_index += 1
                            yield _sse(
                                "content_block_start",
                                {
                                    "type": "content_block_start",
                                    "index": state["block_index"],
                                    "content_block": {
                                        "type": "tool_use",
                                        "id": state["id"],
                                        "name": state["name"],
                                        "input": {},
                                    },
                                },
                            )
                        fn = tc.get("function") or {}
                        args_piece = fn.get("arguments", "")
                        if args_piece:
                            state["args"] += args_piece
                            yield _sse(
                                "content_block_delta",
                                {
                                    "type": "content_block_delta",
                                    "index": state["block_index"],
                                    "delta": {"type": "input_json_delta", "partial_json": args_piece},
                                },
                            )

                    if choice0.get("finish_reason"):
                        finish_reason = choice0["finish_reason"]
    except Exception:
        log.exception("stream relay error")
        finish_reason = "stop"

    if text_block_open:
        yield _sse(
            "content_block_stop",
            {
                "type": "content_block_stop",
                "index": text_block_index,
            },
        )
    for state in tool_state.values():
        yield _sse(
            "content_block_stop",
            {
                "type": "content_block_stop",
                "index": state["block_index"],
            },
        )

    output_tokens = 0
    input_tokens = 0
    if upstream_usage:
        output_tokens = upstream_usage.get("completion_tokens", 0) or 0
        input_tokens = upstream_usage.get("prompt_tokens", 0) or 0

    message_delta_usage: dict[str, Any] = {"output_tokens": output_tokens}
    if input_tokens:
        message_delta_usage["input_tokens"] = input_tokens

    yield _sse(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": _STOP_REASON_MAP.get(finish_reason, "end_turn"), "stop_sequence": None},
            "usage": message_delta_usage,
        },
    )
    yield _sse("message_stop", {"type": "message_stop"})