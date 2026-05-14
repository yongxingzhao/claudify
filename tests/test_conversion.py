"""Tests for claudify.conversion."""
from __future__ import annotations

import json

import pytest

from claudify.conversion import (
    anthropic_to_openai,
    extract_text_from_blocks,
    map_model,
    openai_to_anthropic_response,
    stream_openai_to_anthropic,
)


def test_map_model_explicit_match():
    assert map_model("claude-opus-4-7", {"claude-opus-4-7": "x"}) == "x"


def test_map_model_default_fallback():
    assert map_model("unknown", {}, default="fallback") == "fallback"


def test_map_model_passthrough_when_no_default():
    assert map_model("unknown", {}) == "unknown"


def test_extract_text_from_blocks_handles_tool_result_list():
    blocks = [
        {"type": "text", "text": "hello"},
        {"type": "tool_result", "content": [{"type": "text", "text": "world"}]},
        {"type": "image", "source": {"type": "base64", "data": "x", "media_type": "image/png"}},
    ]
    assert extract_text_from_blocks(blocks) == "hello\nworld\n[image omitted]"


def test_anthropic_to_openai_basic_user_message():
    payload = {
        "model": "claude-opus-4-7",
        "system": "You are helpful.",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 100,
        "temperature": 0.5,
        "stream": False,
    }
    out = anthropic_to_openai(payload, {"claude-opus-4-7": "hermes-agent"})
    assert out["model"] == "hermes-agent"
    assert out["messages"] == [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "hi"},
    ]
    assert out["max_tokens"] == 100
    assert out["temperature"] == 0.5
    assert out["stream"] is False
    assert "stream_options" not in out


def test_anthropic_to_openai_no_user_guard():
    out = anthropic_to_openai({"model": "x", "system": "sys"}, {})
    assert out["messages"][-1] == {"role": "user", "content": "."}


def test_anthropic_to_openai_stop_sequences_become_stop():
    out = anthropic_to_openai({"model": "x", "messages": [{"role": "user", "content": "hi"}], "stop_sequences": ["END"]}, {})
    assert out["stop"] == ["END"]


def test_anthropic_to_openai_stream_options():
    out = anthropic_to_openai({"model": "x", "messages": [{"role": "user", "content": "hi"}], "stream": True}, {})
    assert out["stream"] is True
    assert out["stream_options"] == {"include_usage": True}


def test_anthropic_to_openai_tools_passthrough():
    payload = {
        "model": "x",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "lookup", "description": "lookup", "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}}}],
        "tool_choice": {"type": "tool", "name": "lookup"},
    }
    out = anthropic_to_openai(payload, {})
    assert out["tools"] == [{
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "lookup",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        },
    }]
    assert out["tool_choice"] == {"type": "function", "function": {"name": "lookup"}}


def test_anthropic_to_openai_assistant_tool_use_to_tool_calls():
    payload = {
        "model": "x",
        "messages": [
            {"role": "user", "content": "find the weather"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "calling..."},
                {"type": "tool_use", "id": "toolu_1", "name": "weather", "input": {"city": "SF"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": "sunny"},
            ]},
        ],
    }
    out = anthropic_to_openai(payload, {})
    assert out["messages"] == [
        {"role": "user", "content": "find the weather"},
        {
            "role": "assistant",
            "content": "calling...",
            "tool_calls": [{
                "id": "toolu_1",
                "type": "function",
                "function": {"name": "weather", "arguments": json.dumps({"city": "SF"})},
            }],
        },
        {"role": "tool", "tool_call_id": "toolu_1", "content": "sunny"},
    ]


def test_anthropic_to_openai_tool_result_error_marker():
    payload = {
        "model": "x",
        "messages": [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "boom", "is_error": True},
            ]},
        ],
    }
    out = anthropic_to_openai(payload, {})
    tool_msg = [m for m in out["messages"] if m["role"] == "tool"][0]
    assert tool_msg["content"] == "[tool_error] boom"


def test_anthropic_to_openai_image_block_to_data_url():
    payload = {
        "model": "x",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "what's this"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
        ]}],
    }
    out = anthropic_to_openai(payload, {})
    user_msg = [m for m in out["messages"] if m["role"] == "user"][0]
    assert user_msg["content"][1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}


def test_openai_to_anthropic_response_text_only():
    resp = {
        "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    }
    out = openai_to_anthropic_response(resp, "claude-opus-4-7")
    assert out["model"] == "claude-opus-4-7"
    assert out["content"] == [{"type": "text", "text": "hi"}]
    assert out["stop_reason"] == "end_turn"
    assert out["usage"] == {"input_tokens": 5, "output_tokens": 2}


def test_openai_to_anthropic_response_tool_calls():
    resp = {
        "choices": [{
            "message": {
                "content": None,
                "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "weather", "arguments": json.dumps({"city": "SF"})},
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    out = openai_to_anthropic_response(resp, "x")
    assert out["stop_reason"] == "tool_use"
    assert out["content"] == [{
        "type": "tool_use", "id": "call_1", "name": "weather", "input": {"city": "SF"},
    }]


async def _gather(agen):
    return [chunk async for chunk in agen]


async def _async_lines(lines):
    for line in lines:
        yield line.encode("utf-8") if isinstance(line, str) else line


@pytest.mark.asyncio
async def test_stream_text_relay_emits_full_sequence():
    upstream = [
        'data: {"choices":[{"delta":{"content":"he"}}]}\n',
        'data: {"choices":[{"delta":{"content":"llo"}}]}\n',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":7}}\n',
        "data: [DONE]\n",
    ]
    chunks = await _gather(stream_openai_to_anthropic(_async_lines(upstream), "claude-opus-4-7"))
    text = b"".join(chunks).decode()
    assert "event: message_start" in text
    assert "event: content_block_start" in text
    assert '"text":"he"' in text and '"text":"llo"' in text
    assert "event: content_block_stop" in text
    assert "event: message_delta" in text
    assert '"output_tokens":7' in text
    assert "event: message_stop" in text


@pytest.mark.asyncio
async def test_stream_tool_call_relay():
    upstream = [
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_x","function":{"name":"f","arguments":"{\\"a\\":"}}]}}]}\n',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"1}"}}]}}]}\n',
        'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":2,"completion_tokens":3}}\n',
        "data: [DONE]\n",
    ]
    chunks = await _gather(stream_openai_to_anthropic(_async_lines(upstream), "x"))
    text = b"".join(chunks).decode()
    assert '"type":"tool_use"' in text
    assert '"name":"f"' in text
    assert '"partial_json":"{\\"a\\":"' in text
    assert '"partial_json":"1}"' in text
    assert '"stop_reason":"tool_use"' in text


@pytest.mark.asyncio
async def test_stream_handles_upstream_exception_gracefully():
    async def bad_lines():
        yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n'
        raise RuntimeError("upstream died")

    chunks = await _gather(stream_openai_to_anthropic(bad_lines(), "x"))
    text = b"".join(chunks).decode()
    # Must still close blocks and emit message_stop
    assert "event: content_block_stop" in text
    assert "event: message_delta" in text
    assert "event: message_stop" in text
