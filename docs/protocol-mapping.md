# Protocol Mapping: Anthropic ↔ OpenAI

This document describes how Claudify translates between the Anthropic Messages API and the OpenAI Chat Completions API.

## Message Roles

| Anthropic | OpenAI | Notes |
|-----------|--------|-------|
| `system` (string) | `role: "system"` | Direct mapping |
| `system` (content blocks) | `role: "system"` | Blocks merged into single string; `cache_control` stripped |
| `user` (string) | `role: "user"` | Direct mapping |
| `user` (content blocks) | `role: "user"` | Text → text parts; Image → image_url; tool_result → separate `role: "tool"` messages |
| `assistant` (string) | `role: "assistant"` | Direct mapping |
| `assistant` (content blocks) | `role: "assistant"` | text → content; tool_use → tool_calls; thinking → dropped |

## Content Block Types

| Anthropic Block | OpenAI Equivalent | Notes |
|-----------------|-------------------|-------|
| `text` | `text` part | Direct |
| `image` (base64) | `image_url` with data URI | `data:{media};base64,{data}` |
| `image` (url) | `image_url` with URL | Direct |
| `tool_use` | `tool_calls[]` entry | `input` → JSON `arguments` string |
| `tool_result` | Separate `role: "tool"` message | Content extracted as text; `is_error` → `[tool_error]` prefix |
| `thinking` | Dropped | Logged at debug level |
| `cache_control` | Dropped | Stripped from all blocks |

## Parameters

| Anthropic | OpenAI | Notes |
|-----------|--------|-------|
| `model` | `model` | Mapped via `model_map` or `default_model` |
| `max_tokens` | `max_tokens` | Direct |
| `temperature` | `temperature` | Direct |
| `top_p` | `top_p` | Direct |
| `top_k` | `top_k` | Passthrough (not all OpenAI backends support) |
| `stop_sequences` | `stop` | Renamed |
| `stream` | `stream` | Direct; `stream_options: {include_usage: true}` added |
| `tools` | `tools` | `input_schema` → `parameters` |
| `tool_choice` | `tool_choice` | `auto`→`auto`, `any`→`required`, named→`{type:"function",...}` |
| `metadata.user_id` | `user` | Flattened |

## Stop Reasons

| OpenAI `finish_reason` | Anthropic `stop_reason` |
|------------------------|------------------------|
| `stop` | `end_turn` |
| `length` | `max_tokens` |
| `tool_calls` | `tool_use` |

## Error Mapping

| HTTP Status | Anthropic Error Type |
|-------------|---------------------|
| 400 | `invalid_request_error` |
| 401 | `authentication_error` |
| 403 | `permission_error` |
| 404 | `not_found_error` |
| 429 | `rate_limit_error` |
| 500 | `api_error` |
| 502 | `api_error` |
| 503 | `overloaded_error` |
| 504 | `api_error` |

Upstream error messages are sanitized: API keys (`sk-...`) and URLs are redacted.

## Headers

| Header | Behavior |
|--------|----------|
| `x-api-key` | Converted to `Authorization: Bearer ...` for upstream |
| `Authorization` | Passed through as-is |
| `anthropic-beta` | Forwarded to upstream |
| `anthropic-version` | Forwarded; defaults to `2023-06-01` if absent |

## Streaming Protocol

Claudify translates OpenAI SSE chunks into Anthropic SSE events:

1. `message_start` — sent at beginning with empty message scaffold
2. `content_block_start` — when a new text or tool_use block begins
3. `content_block_delta` — incremental text (`text_delta`) or tool arguments (`input_json_delta`)
4. `content_block_stop` — when a block ends
5. `message_delta` — final stop reason + usage
6. `message_stop` — end of message
7. `ping` — sent after each upstream chunk batch for keep-alive

If the upstream stream is interrupted, Claudify emits synthetic `message_delta` + `message_stop` events to ensure the client receives a complete message.

## Known Unsupported Features

- **Thinking/extended thinking**: Blocks are dropped (no `thinking` content in response)
- **Cache control**: `cache_control` fields are stripped; no prompt caching
- **Vision with multiple images**: Supported but may be slow on some backends
- **Count tokens**: Returns char/word-based estimate, not real tokenization
- **Citations**: Not mapped
- **PDF/document attachments**: Not supported
