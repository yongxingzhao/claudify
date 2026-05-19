# Protocol Mapping: Anthropic ↔ OpenAI

This document describes how claudify translates between the Anthropic Messages API and the OpenAI Chat Completions API.

## Request Translation (Anthropic → OpenAI)

### Top-Level Fields

| Anthropic | OpenAI | Notes |
|-----------|--------|-------|
| `model` | `model` | Mapped through `model_map` if configured, then `default_model`, else passed through |
| `messages` | `messages` | See message block mapping below |
| `system` | `messages[0].role="system"` | Extracted to a system message |
| `max_tokens` | `max_tokens` | Direct mapping |
| `temperature` | `temperature` | Direct mapping |
| `top_p` | `top_p` | Direct mapping |
| `top_k` | `top_k` | Passed through (not standard OpenAI; supported by some backends) |
| `stream` | `stream` | Direct mapping |
| `stop_sequences` | `stop` | Direct mapping |
| `tools` | `tools` | See tool mapping below |
| `tool_choice` | `tool_choice` | See tool choice mapping below |
| `metadata.user_id` | `user` | Mapped to OpenAI user field |

### Message Block Mapping

| Anthropic Block | OpenAI Format | Notes |
|-----------------|---------------|-------|
| `type: "text"` | `type: "text"` | Direct mapping |
| `type: "image"` (base64) | `type: "image_url"` with `url: "data:..."` | Base64 → data URI |
| `type: "image"` (URL) | `type: "image_url"` with `url: "..."` | Direct URL passthrough |
| `type: "tool_use"` | `tool_calls[{function:{name,arguments}}]` | Arguments: JSON string |
| `type: "tool_result"` | `role: "tool"` with `tool_call_id` | Mapped to tool message |
| `type: "thinking"` | Dropped | Stripped with debug log; not mappable to OpenAI |
| `cache_control` | Dropped | Silently stripped from all blocks |

### Tool Definition Mapping

| Anthropic | OpenAI | Notes |
|-----------|--------|-------|
| `name` | `function.name` | |
| `description` | `function.description` | |
| `input_schema` | `function.parameters` | Direct mapping (JSON Schema) |

### Tool Choice Mapping

| Anthropic | OpenAI | Notes |
|-----------|--------|-------|
| `{"type": "auto"}` | `{"type": "auto"}` | Direct mapping |
| `{"type": "none"}` | `{"type": "none"}` | Direct mapping |
| `{"type": "any"}` | `{"type": "required"}` | Best-effort mapping |
| `{"type": "tool", "name": "X"}` | `{"type": "function", "function": {"name": "X"}}` | Named tool |

## Response Translation (OpenAI → Anthropic)

### Non-Streaming

| OpenAI | Anthropic | Notes |
|--------|-----------|-------|
| `choices[0].message` | `content[]` | See content mapping below |
| `choices[0].finish_reason` | `stop_reason` | See finish reason mapping |
| `usage.prompt_tokens` | `usage.input_tokens` | |
| `usage.completion_tokens` | `usage.output_tokens` | |
| `model` | `model` | Reverse-mapped through model_map if possible |
| `id` | `id` | Prefixed with `msg_` if not already |

### Finish Reason Mapping

| OpenAI | Anthropic |
|--------|-----------|
| `stop` | `end_turn` |
| `length` | `max_tokens` |
| `tool_calls` | `tool_use` |
| Any other | `end_turn` |

### Content Mapping

| OpenAI | Anthropic | Notes |
|--------|-----------|-------|
| `content` (string) | `[{type: "text", text: ...}]` | Wrapped in text block |
| `tool_calls[]` | `[{type: "tool_use", ...}]` | Arguments: JSON string → dict |
| Empty content + tool_calls | `[{type: "text", text: ""}]` | Preserved |

### Streaming

| OpenAI SSE Event | Anthropic SSE Event | Notes |
|-------------------|---------------------|-------|
| `role: "assistant"` delta | `message_start` | First chunk with role |
| `content` delta | `content_block_delta` (text_delta) | Text streaming |
| `tool_calls` delta | `content_block_delta` (input_json_delta) | Tool call streaming |
| `finish_reason` | `message_delta` + `message_stop` | End of stream |
| `usage` (last chunk) | `message_delta` (usage) | Token counts |
| `[DONE]` | End of stream | Triggers `message_stop` |

### Error Mapping

| Upstream Status | Anthropic Error Type | Notes |
|----------------|---------------------|-------|
| 400 | `invalid_request_error` | |
| 401 | `authentication_error` | |
| 403 | `permission_error` | |
| 404 | `not_found_error` | |
| 429 | `rate_limit_error` | |
| 500 | `api_error` | |
| 502/503/504 | `upstream_unavailable` | Retried if configured |

Error messages are sanitized to remove API keys and internal URLs before being returned to the client.

## Headers

| Header | Direction | Notes |
|--------|-----------|-------|
| `x-api-key` | Inbound → `Authorization: Bearer` | Anthropic-style auth converted |
| `anthropic-version` | Forwarded to upstream | Defaults to `2023-06-01` |
| `anthropic-beta` | Forwarded to upstream | Pass-through |
| `x-request-id` | Added to response | UUID4 per request |

## Known Unsupported Features

- **Extended thinking**: `thinking` blocks are stripped (no OpenAI equivalent)
- **Prompt caching**: `cache_control` is silently removed
- **Parallel tool calls**: Anthropic `tool_choice: {"type": "any"}` maps to OpenAI `"required"` (not exact)
- **Image URL download**: Only base64 and direct URL passthrough
