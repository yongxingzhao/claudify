# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
