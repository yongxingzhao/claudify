# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- `inbound_api_key` setting: require API key on inbound `x-api-key` header
- `log_format` setting: `"text"` (default) or `"json"` for structured logging
- `log_format` placeholder in `init-config` template
- Streaming requests now use `httpx_timeout(streaming=True)` (read=None) to avoid timeout on long streams
- `429` (rate limit) responses are now retried alongside `5xx` errors
- Respect `Retry-After` header on 429 responses (both seconds and HTTP-date format)
- Backoff cap at 30 seconds to avoid excessively long waits
- `created_at` timestamp in both streaming and non-streaming responses
- Request duration logged on successful requests (both streaming and non-streaming)
- `--verbose`/`--quiet` CLI flags now correctly take effect (Settings passed via closure)
- Streaming requests now record metrics on completion
- `count_tokens` endpoint now requires `inbound_api_key` auth when configured
- `count_tokens` response now includes `id` and `type` fields per Anthropic spec
- `count_tokens` now requires `model` field and validates it
- Warning logged when messages with unsupported roles are dropped
- `debug_log_payloads` setting: log full request/response payloads at DEBUG level
- `ConcurrencyLimitMiddleware`: rejects requests with 503 when concurrency exceeds `pool_limit`
- Health endpoint now includes `version` field
- `x-request-id` header now included on all endpoint responses (health, models, count_tokens)
- `--completion` CLI flag: show shell completion instructions (auto-detects from `$SHELL`)

### Changed
- `inbound_api_key` comparison uses `hmac.compare_digest` to prevent timing attacks
- When `inbound_api_key` is set, the inbound key is no longer forwarded upstream — `api_key` is used instead
- CORS `allow_methods` and `allow_headers` restricted to specific values
- `install-service` uses `shutil.which("claudify")` to find binary path
- `install-service` removed dead `--host`/`--port` options (service reads from config.toml)
- `retry_attempts` semantics: `>=1` triggers retry, `attempts+1` passed to retry functions
- Assistant messages with `tool_calls` now set `content: null` (was empty string)
- `top_k` passthrough now logs a warning
- Request ID extended from 8 to 16 hex characters
- Model name validated as non-empty string (max 256 chars)
- `count_tokens` now counts system prompt, tool_result content blocks, and tool_use blocks
- `count_tokens` validates payload is a dict and requires `model` field
- Unhandled error responses include request ID
- `init-config` template expanded with missing settings
- Health endpoint now uses dedicated short timeout (5s read) and catches specific httpx exceptions
- Health endpoint distinguishes 404 (misconfigured) from other 4xx (degraded)
- Model name in response always returns the client-requested name (consistent between streaming and non-streaming)
- README config table now includes `log_level`, `log_format`, and `pool_limit`
- README dev install command changed from `uv pip install -e ".[dev]"` to `uv sync --group dev`
- Catch `httpx.TransportError` (base class) instead of listing individual transport exceptions
- Non-streaming responses are now explicitly closed to return connections to the pool
- Streaming retry failure now reads upstream error body before reporting
- Stream interruption recorded as 502 in metrics (was incorrectly 200)
- SSE parser normalizes CRLF line endings for cross-platform compatibility
- SSE parser tracks buffer length incrementally (O(1) per feed instead of O(n))
- `Settings.load()` raises `ConfigurationError` (catchable) instead of `SystemExit`
- CLI catches `ConfigurationError` and exits with a clear message
- retry.py catches `httpx.TransportError` (base class) for all transport failures during retry
- User text content placed before tool_result messages in conversion (correct semantic ordering)
- `openai_to_anthropic_response` no longer accepts unused `model_map` parameter
- `_parse_tool_arguments` returns `{}` with a warning instead of `{"_raw": ...}` for malformed JSON
- JSON log formatter now uses `json.dumps()` for proper escaping, includes `exc_info` and `stack_info`
- `count_tokens` now counts `tool_use` blocks in token estimation
- protocol-mapping.md updated: model field always returns client-requested name
- Error secret redaction: 4 regex patterns combined into a single alternation (faster, also matches uppercase variants)
- `has_user` guard tracked as boolean during message loop (eliminates redundant post-loop scan)
- `_build_finalization_events` converted to generator (avoids intermediate list allocation)
- `_close_open_blocks` extracted to DRY streaming cleanup between normal and error paths
- `_handle_tool_call` extracts `fn` dict once instead of calling `tc.get("function")` twice
- `_json_response` and `_parse_body` helpers extracted in routes.py (DRY JSON encoding and body validation)
- SSE parser `_parts` list reused via in-place mutation instead of per-feed allocation
- `passthrough_error` skips JSON extraction on upstream bodies larger than 8KB
- systemd `uninstall` no longer creates parent directories (path computation separated from `install`)

### Fixed
- `tool_choice: {"type": "none"}` now correctly maps to OpenAI `"none"`
- Streaming response leak on 5xx after retries exhausted
- Empty `content` array in response now falls back to `[{"type": "text", "text": ""}]` (Anthropic spec requires non-empty)
- Streaming exception handler now emits `content_block_stop` for open tool blocks
- `tool_call_id` fallback from empty string to generated ID (OpenAI rejects empty `tool_call_id`)
- `httpx.RemoteProtocolError` now returns 502 (was unhandled 500)
- Non-streaming 200 with invalid JSON from upstream now returns 502 (was unhandled 500)
- JSON log format now properly escapes special characters using `json.dumps()`
- README examples referenced non-existent CLI flags (`init-config --backend`, `install-service --api-key`)
- `--completion` CLI flag works as bare flag (`claudify --completion` auto-detects from `$SHELL`)
- Retry now catches `httpx.TransportError` (ConnectError, ReadError, WriteError) during streaming

### Removed
- Dead code: `_MAX_LATENCY` constant in metrics.py
- Dead code: `_reverse_map_model` function (model name now always uses original)
- Dead code: `args_pieces` list accumulation in streaming tool state

## [0.1.0] - 2025-05-19

### Added
- Anthropic Messages API → OpenAI Chat Completions proxy
- `claudify run` CLI with `--host`, `--port`, `--verbose`, `--quiet`, `--config` options
- `claudify version`, `config-path`, `init-config` commands
- `claudify install-service` for systemd (Linux) and launchd (macOS)
- Protocol conversion: system blocks, tool_use/tool_result, images, cache_control, thinking
- Streaming with SSE: message_start/delta/stop, content_block lifecycle, ping keep-alive
- Synthetic stop events on mid-stream interruption
- Model name mapping via `model_map` in config.toml
- Per-request structured logging with request ID
- `/metrics` Prometheus-text endpoint (request counts, latency histograms, upstream status)
- `/health` endpoint with optional upstream health check
- `/v1/models` endpoint listing mapped models
- `/v1/messages/count_tokens` with char/word-based estimation
- Retry with exponential backoff for 5xx upstream errors (configurable attempts/backoff)
- CORS support via `cors_origins` setting
- `x-api-key` header forwarding to `Authorization: Bearer`
- `anthropic-beta` and `anthropic-version` header forwarding
- Request body size limit (10MB default, 413 on overflow)
- Error message sanitization (redact API keys and URLs)
- Upstream HTTP status → Anthropic error type mapping
- Split timeout settings: `connect_timeout`, `read_timeout`, `write_timeout`, `pool_timeout`
- Config via environment variables (`CLAUDIFY_*`) and `~/.config/claudify/config.toml`
- Non-JSON upstream error handling (HTML error pages → clean JSON errors)
- GitHub Actions CI: Python 3.10–3.13 matrix, ruff lint, pytest
- `CONTRIBUTING.md` and `docs/protocol-mapping.md`

### Changed
- Refactored monolithic `app.py` into `routes.py`, `errors.py`, `metrics.py`, `retry.py`, `sse.py`
- Extracted SSE helpers from `conversion.py` into `sse.py`
- Migrated from `on_event` to `lifespan` context manager
- Metrics `_Metrics` → public `Metrics` class
- `dependency-groups` in pyproject.toml for uv compatibility

### Fixed
- CLI `install-service` now reads config.toml before requiring CLI flags
- systemd unit `Environment` line formatting (string concatenation fix)
- SSE buffer parser handles cross-chunk event boundaries
- Empty `messages` array rejected with 400
