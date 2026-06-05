# claudify

[![PyPI](https://img.shields.io/pypi/v/claudify.svg)](https://pypi.org/project/claudify/)
[![Python](https://img.shields.io/pypi/pyversions/claudify.svg)](https://pypi.org/project/claudify/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**English** | [中文](README.zh-CN.md)

A local proxy that translates the **Anthropic Messages API** into **OpenAI Chat Completions**, so any Anthropic-protocol client (e.g. Claude Code) can talk to an OpenAI-compatible backend.

## Platform support

**Linux and macOS only.** Windows is not supported and not tested.

- **Linux:** tested on systemd-based distros (Arch, Ubuntu, Fedora). `claudify install-service` writes a user-level systemd unit.
- **macOS:** `claudify install-service` writes a LaunchAgent plist and loads it via `launchctl`.
- **Windows:** untested. Use WSL2.

## Install

```bash
uv tool install claudify
# or
pipx install claudify
```

From source:

```bash
git clone https://github.com/yongxingzhao/claudify.git
cd claudify
uv tool install .
```

## Quick start

```bash
claudify init-config
# Edit ~/.config/claudify/config.toml to set backend_base and api_key
claudify run
```

Default listen address: `127.0.0.1:4000`.

## Architecture

```
┌──────────────────────┐      ┌───────────────────────────────────────────────┐      ┌──────────────────────┐
│   Anthropic Client   │      │              Claudify Proxy                   │      │   OpenAI Backend     │
│                      │      │                                               │      │                      │
│  - Claude Code       │─────▶│  FastAPI Server (routes.py, app.py)          │─────▶│  - vLLM              │
│  - Python SDK        │  1   │  ┌───────────────────────────────────────┐   │  4   │  - OpenAI API        │
│  - curl / HTTP       │◀─────│  │  Auth Check (inbound_api_key)         │   │◀─────│  - Any compatible    │
│                      │  5   │  ├───────────────────────────────────────┤   │      │    endpoint          │
└──────────────────────┘      │  │  Conversion Layer (conversion.py)     │   │      └──────────────────────┘
                              │  │  - anthropic_to_openai()              │   │
                              │  │  - openai_to_anthropic_response()     │   │
                              │  ├───────────────────────────────────────┤   │
                              │  │  SSE Parser (sse.py)                 │   │
                              │  │  - Incremental chunk parsing          │   │
                              │  │  - Stop reason mapping                │   │
                              │  ├───────────────────────────────────────┤   │
                              │  │  Retry Logic (retry.py)              │   │
                              │  │  - Exponential backoff (cap 30s)      │   │
                              │  │  - Retry-After header support         │   │
                              │  ├───────────────────────────────────────┤   │
                              │  │  Model Map (settings.py)             │   │
                              │  │  - Anthropic → OpenAI name mapping    │   │
                              │  └───────────────────────────────────────┘   │
                              └───────────────────────────────────────────────┘
```

**Request flow:**

1. Client sends an Anthropic Messages API request to Claudify.
2. Claudify validates inbound auth (if configured) and converts the request to OpenAI format.
3. The request is forwarded to the configured OpenAI-compatible backend.
4. The backend response is converted back to Anthropic format.
5. The converted response is streamed or returned to the client.

## Use Cases

### Using Claude Code with OpenAI models

[Claude Code](https://docs.anthropic.com/en/docs/claude-code) natively speaks the Anthropic Messages API. With Claudify running, point Claude Code at the proxy and use any OpenAI-compatible model:

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:4000 ANTHROPIC_API_KEY=any-value claude
```

### Using Anthropic SDK with any OpenAI-compatible API

Any tool that uses the Anthropic Python SDK can talk to an OpenAI-compatible backend:

```python
from anthropic import Anthropic

client = Anthropic(
    base_url="http://127.0.0.1:4000",
    api_key="any-value",
)

message = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello!"}],
)
print(message.content[0].text)
```

### Load balancing across multiple backends

Run multiple Claudify instances on different ports, each pointing to a different backend, and use a reverse proxy (e.g., nginx or HAProxy) for load balancing:

```toml
# Backend 1
backend_base = "http://10.0.1.10:8000/v1"
api_key = "sk-backend1"
port = 4001

# Backend 2
backend_base = "http://10.0.1.20:8000/v1"
api_key = "sk-backend2"
port = 4002
```

## Endpoints

| Method | Path | Description |
| ------ | ---- | ----------- |
| `POST` | `/v1/messages` | Anthropic Messages API. Supports streaming (SSE) and tool use. |
| `POST` | `/v1/messages/count_tokens` | Approximate input-token count (character-based heuristic; no upstream call). |
| `GET`  | `/v1/models` | Lists mapped models from config. |
| `GET`  | `/health` | Liveness check with optional upstream health. |
| `GET`  | `/metrics` | Prometheus-text metrics (request counts, latency, upstream status). |

## Configuration

Edit `~/.config/claudify/config.toml` (created with mode `0600`) or override via `CLAUDIFY_*` env vars:

```toml
backend_base = "http://127.0.0.1:8000/v1"
api_key = "sk-..."
host = "127.0.0.1"
port = 4000

connect_timeout = 10.0
read_timeout = 120.0
write_timeout = 10.0
pool_timeout = 5.0

retry_attempts = 3
retry_backoff = 0.5

cors_origins = ["http://localhost:3000"]

[model_map]
"claude-opus-4-7"   = "hermes-agent"
"claude-sonnet-4-6" = "hermes-agent"

default_model = "hermes-agent"
```

| Setting | Env var | Default | Notes |
| --- | --- | --- | --- |
| `backend_base` | `CLAUDIFY_BACKEND_BASE` | `http://127.0.0.1:8000/v1` | OpenAI-compatible base URL. |
| `api_key` | `CLAUDIFY_API_KEY` | _(empty)_ | Bearer token sent upstream. |
| `inbound_api_key` | `CLAUDIFY_INBOUND_API_KEY` | _(empty)_ | If set, require this key in inbound `x-api-key` header. |
| `host` | `CLAUDIFY_HOST` | `127.0.0.1` | Bind address. |
| `port` | `CLAUDIFY_PORT` | `4000` | Bind port. |
| `log_level` | `CLAUDIFY_LOG_LEVEL` | `INFO` | Logging level: DEBUG, INFO, WARNING, ERROR. |
| `log_format` | `CLAUDIFY_LOG_FORMAT` | `text` | `text` (default) or `json` for structured logging. |
| `pool_limit` | `CLAUDIFY_POOL_LIMIT` | `100` | Max connections in httpx pool. |
| `connect_timeout` | `CLAUDIFY_CONNECT_TIMEOUT` | _(same as request_timeout)_ | Connection timeout (seconds). |
| `read_timeout` | `CLAUDIFY_READ_TIMEOUT` | _(same as request_timeout)_ | Read timeout for non-streaming. |
| `write_timeout` | `CLAUDIFY_WRITE_TIMEOUT` | _(same as request_timeout)_ | Write timeout. |
| `pool_timeout` | `CLAUDIFY_POOL_TIMEOUT` | _(same as request_timeout)_ | Connection pool timeout. |
| `request_timeout` | `CLAUDIFY_REQUEST_TIMEOUT` | `300.0` | Fallback timeout for any unset timeout. |
| `retry_attempts` | `CLAUDIFY_RETRY_ATTEMPTS` | `0` | Max retry attempts for 5xx/429 errors (after initial attempt). |
| `retry_backoff` | `CLAUDIFY_RETRY_BACKOFF` | `0.5` | Initial backoff in seconds (doubles each attempt, capped at 30s). |
| `default_model` | `CLAUDIFY_DEFAULT_MODEL` | _(empty)_ | Used when the requested model is unknown. |
| `model_map` | (TOML only) | `{}` | Map Anthropic model names to upstream model names. |
| `cors_origins` | (TOML only) | `[]` | Allowed CORS origins. |
| `max_body_size` | `CLAUDIFY_MAX_BODY_SIZE` | `10485760` | Max request body size in bytes. |
| `upstream_health_path` | `CLAUDIFY_UPSTREAM_HEALTH_PATH` | _(empty)_ | Upstream health check path. |

## Security

### Inbound authentication

Set `inbound_api_key` in your config to require authentication on incoming requests. Clients must include this key in the `x-api-key` header:

```bash
curl -H "x-api-key: your-secret-key" http://127.0.0.1:4000/v1/messages ...
```

> **Note:** Inbound auth is for proxy access control only. The key is never forwarded upstream.

### Upstream authentication

Set `api_key` in your config to authenticate with the OpenAI-compatible backend. This value is sent as a `Bearer` token in the `Authorization` header to the upstream service.

### Config file permissions

The config file `~/.config/claudify/config.toml` is created with mode `0600` (owner read/write only). This prevents other users on the system from reading your API keys:

```bash
ls -la ~/.config/claudify/config.toml
# -rw------- 1 user user ... config.toml
```

### Error message sanitization

Claudify sanitizes error messages from upstream backends before forwarding them to clients. Sensitive details (API keys, internal URLs, stack traces) are redacted using regex patterns to prevent information leakage.

## Performance

### Connection pool settings

| Setting | Default | Description |
| ------- | ------- | ----------- |
| `pool_limit` | `100` | Max concurrent connections in the httpx pool |
| `pool_timeout` | `300s` | Max seconds to wait for a connection from the pool |
| `connect_timeout` | `300s` | Max seconds to establish a new connection |

For high-concurrency workloads, consider increasing `pool_limit` and ensuring your backend can handle the connection count.

### Timeout recommendations

| Workload type | `read_timeout` | Notes |
| ------------- | -------------- | ----- |
| Streaming (default) | `300s` | Streaming requests bypass read timeout internally; this is the safety net |
| Non-streaming (short) | `60s` | Quick completion requests |
| Non-streaming (long) | `300–600s` | Large context or complex prompts |

Streaming requests use `httpx`'s `timeout(streaming=True)`, which sets `read=None` (infinite) by default. The `read_timeout` only applies to non-streaming responses.

### Retry strategy

| Setting | Default | Recommendation |
| ------- | ------- | -------------- |
| `retry_attempts` | `0` | Set to `2–3` for production to handle transient 5xx/429 errors |
| `retry_backoff` | `0.5` | Keep at `0.5`; backoff doubles each attempt, capped at 30s |

- Retries apply to **5xx** (server errors) and **429** (rate limit) responses.
- The `Retry-After` header is respected for 429 responses.
- Retries consume additional tokens/time from the upstream; avoid excessive retry counts for expensive models.

## Troubleshooting

| Symptom | Likely cause | Fix |
| ------- | ------------ | --- |
| `Connection refused` when calling the backend | The OpenAI-compatible backend is not running | Start your backend and verify `backend_base` points to the correct URL |
| `401 Unauthorized` from the upstream backend | The upstream API key is invalid or missing | Set the correct `api_key` in `config.toml` or via `CLAUDIFY_API_KEY` |
| `401 Unauthorized` when calling Claudify | `inbound_api_key` is set but the client didn't provide it | Include `x-api-key` header in your requests, or unset `inbound_api_key` |
| Streaming timeout | Read timeout too short for long responses | Increase `read_timeout` or `request_timeout` in config (default: 300s) |
| `Model not found` error | The requested model name isn't in `model_map` and no `default_model` is set | Add a `[model_map]` entry or set `default_model` in config |
| Empty response or unexpected content | Upstream model doesn't support the requested feature (e.g., tool use) | Check upstream model capabilities; see known unsupported features below |

Enable debug logging to diagnose issues:

```toml
log_level = "DEBUG"
log_format = "json"
```

## Known unsupported features

- **Thinking/extended thinking** — blocks are dropped; no `thinking` content in responses
- **Cache control** — `cache_control` fields are stripped; no prompt caching
- **Count tokens** — returns char/word-based estimate, not real tokenization
- **Citations** — not mapped
- **PDF/document attachments** — not supported

See [docs/protocol-mapping.md](docs/protocol-mapping.md) for the full translation table.

## Run as a service

```bash
claudify install-service --backend http://127.0.0.1:8000/v1
```

- **Linux (systemd):** writes `~/.config/systemd/user/claudify.service`, then `systemctl --user enable --now claudify`.
- **macOS (launchd):** writes `~/Library/LaunchAgents/com.claudify.plist`, then loads via `launchctl`.

Note: `api_key` is not written to the service file. Claudify reads it from `config.toml` at runtime.

Inspect / control:

```bash
# Linux
systemctl --user status claudify
journalctl --user -u claudify -f

# macOS
launchctl list | grep claudify
```

## Project layout

```
src/claudify/
├── settings.py         # pydantic-settings + config.toml loader
├── conversion.py       # anthropic ↔ openai pure functions
├── sse.py              # SSE event helpers + stop reason map
├── errors.py           # error type mapping, sanitization, passthrough
├── metrics.py          # Prometheus-text metrics collector
├── retry.py            # retry with exponential backoff
├── routes.py           # FastAPI route handlers
├── app.py              # FastAPI app factory + middleware
├── cli.py              # Typer CLI (claudify console script)
└── service/
    ├── __init__.py     # platform dispatch
    ├── systemd.py      # Linux user-unit installer
    └── launchd.py      # macOS launchd installer
```

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check src tests
```

## License

MIT
