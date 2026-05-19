"""Tests for Anthropic <-> OpenAI protocol conversion."""

from __future__ import annotations

import json

from claudify.conversion import (
    _convert_tool_choice,
    anthropic_to_openai,
    extract_text_from_blocks,
    map_model,
    openai_to_anthropic_response,
)
from claudify.sse import sse_event, sse_ping, synthetic_stop_events


def test_map_model_direct():
    assert map_model("claude-opus-4-7", {"claude-opus-4-7": "gpt-4"}) == "gpt-4"


def test_map_model_default():
    assert map_model("unknown", {}, default="gpt-4") == "gpt-4"


def test_map_model_passthrough():
    assert map_model("unknown", {}) == "unknown"


def test_system_string():
    payload = {"model": "m", "system": "sys", "messages": [{"role": "user", "content": "hi"}]}
    out = anthropic_to_openai(payload, {})
    assert out["messages"][0]["role"] == "system"


def test_system_blocks():
    payload = {
        "model": "m",
        "system": [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": "hi"}],
    }
    out = anthropic_to_openai(payload, {})
    sys_msg = out["messages"][0]
    assert sys_msg["role"] == "system"
    assert sys_msg["content"] == "sys"


def test_assistant_tool_use():
    payload = {
        "model": "m",
        "messages": [
            {"role": "user", "content": "search"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "let me look"},
                    {"type": "tool_use", "id": "tu_1", "name": "search", "input": {"q": "x"}},
                ],
            },
        ],
    }
    out = anthropic_to_openai(payload, {})
    assistant = [m for m in out["messages"] if m["role"] == "assistant"][0]
    assert assistant["content"] == "let me look"
    assert len(assistant["tool_calls"]) == 1
    tc = assistant["tool_calls"][0]
    assert tc["function"]["name"] == "search"
    assert json.loads(tc["function"]["arguments"]) == {"q": "x"}


def test_user_tool_result():
    payload = {
        "model": "m",
        "messages": [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "tu_1", "name": "fn", "input": {}}],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu_1", "content": "result text"},
                ],
            },
        ],
    }
    out = anthropic_to_openai(payload, {})
    tool_msgs = [m for m in out["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "tu_1"
    assert tool_msgs[0]["content"] == "result text"


def test_cache_control_does_not_mutate_input():
    original_content = [
        {"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}},
        {"type": "tool_result", "tool_use_id": "t1", "content": "ok", "cache_control": {"type": "ephemeral"}},
    ]
    payload = {
        "model": "m",
        "messages": [
            {"role": "user", "content": original_content},
        ],
    }
    import copy
    snap = copy.deepcopy(original_content)
    anthropic_to_openai(payload, {})
    assert original_content == snap


def test_no_user_message_guard():
    payload = {
        "model": "m",
        "messages": [
            {"role": "assistant", "content": "hi"},
        ],
    }
    out = anthropic_to_openai(payload, {})
    assert any(m["role"] == "user" for m in out["messages"])


def test_temperature_and_max_tokens():
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "x"}],
        "temperature": 0.5,
        "max_tokens": 100,
    }
    out = anthropic_to_openai(payload, {})
    assert out["temperature"] == 0.5
    assert out["max_tokens"] == 100


def test_stop_sequences():
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "x"}],
        "stop_sequences": ["END"],
    }
    out = anthropic_to_openai(payload, {})
    assert out["stop"] == ["END"]


def test_top_k_passthrough():
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "x"}],
        "top_k": 40,
    }
    out = anthropic_to_openai(payload, {})
    assert out["top_k"] == 40


def test_tools_and_tool_choice():
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "x"}],
        "tools": [{"name": "fn", "description": "a fn", "input_schema": {"type": "object", "properties": {}}}],
        "tool_choice": {"type": "auto"},
    }
    out = anthropic_to_openai(payload, {})
    assert len(out["tools"]) == 1
    assert out["tool_choice"] == "auto"


def test_tool_choice_any():
    out = _convert_tool_choice({"type": "any"})
    assert out == "required"


def test_tool_choice_named():
    out = _convert_tool_choice({"type": "tool", "name": "fn"})
    assert out == {"type": "function", "function": {"name": "fn"}}


def test_tool_choice_none():
    assert _convert_tool_choice(None) is None
    assert _convert_tool_choice("auto") is None


def test_metadata_user_id():
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "x"}],
        "metadata": {"user_id": "u-123"},
    }
    out = anthropic_to_openai(payload, {})
    assert out["user"] == "u-123"


def test_stream_options_included():
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "x"}],
        "stream": True,
    }
    out = anthropic_to_openai(payload, {})
    assert out["stream_options"] == {"include_usage": True}


def test_thinking_block_dropped():
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "hmm"},
                    {"type": "text", "text": "answer"},
                ],
            },
            {"role": "user", "content": "ok"},
        ],
    }
    out = anthropic_to_openai(payload, {})
    assistant = [m for m in out["messages"] if m["role"] == "assistant"][0]
    assert assistant["content"] == "answer"
    assert "tool_calls" not in assistant


def test_empty_string_content_preserved():
    payload = {
        "model": "m",
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": "ok"},
        ],
    }
    out = anthropic_to_openai(payload, {})
    assistant_msgs = [m for m in out["messages"] if m["role"] == "assistant"]
    assert len(assistant_msgs) == 0


# ---------- openai_to_anthropic_response ------------------------------------


def test_basic_response():
    resp = {
        "id": "chatcmpl-1",
        "choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    out = openai_to_anthropic_response(resp, "claude-opus-4-7")
    assert out["model"] == "claude-opus-4-7"
    assert out["content"][0]["type"] == "text"
    assert out["content"][0]["text"] == "hi"
    assert out["stop_reason"] == "end_turn"
    assert out["usage"]["input_tokens"] == 10


def test_tool_calls_response():
    resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "fn", "arguments": '{"a":1}'},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 10},
    }
    out = openai_to_anthropic_response(resp, "m")
    assert out["stop_reason"] == "tool_use"
    assert out["content"][0]["type"] == "tool_use"
    assert out["content"][0]["name"] == "fn"
    assert out["content"][0]["input"] == {"a": 1}


def test_malformed_tool_arguments():
    resp = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {"id": "c", "type": "function", "function": {"name": "f", "arguments": "not-json"}},
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }
    out = openai_to_anthropic_response(resp, "m")
    assert out["content"][0]["input"] == {"_raw": "not-json"}


def test_user_field_to_metadata():
    resp = {
        "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
        "usage": {},
        "user": "u-999",
    }
    out = openai_to_anthropic_response(resp, "m")
    assert out["metadata"]["user_id"] == "u-999"


# ---------- extract_text_from_blocks ----------------------------------------


def test_extract_text_string():
    assert extract_text_from_blocks("hello") == "hello"


def test_extract_text_blocks():
    blocks = [
        {"type": "text", "text": "a"},
        {"type": "tool_result", "content": "b"},
    ]
    assert extract_text_from_blocks(blocks) == "a\nb"


# ---------- sse helpers ---------------------------------------------------


def test_sse_format():
    result = sse_event("ping", {"type": "ping"})
    assert result.startswith(b"event: ping\n")
    assert b"data: " in result
    assert result.endswith(b"\n\n")


def test_sse_ping():
    result = sse_ping()
    assert result == b"event: ping\ndata: {}\n\n"


def test_synthetic_stop_events():
    events = synthetic_stop_events("stop", {"prompt_tokens": 3, "completion_tokens": 5})
    assert len(events) == 2
    for ev in events:
        assert ev.startswith(b"event: ")
        assert ev.endswith(b"\n\n")
    delta_line = [line for line in events[0].split(b"\n") if line.startswith(b"data: ")][0]
    payload = json.loads(delta_line[len(b"data: "):])
    assert payload["delta"]["stop_reason"] == "end_turn"
