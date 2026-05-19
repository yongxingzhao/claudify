# claudify

[![PyPI](https://img.shields.io/pypi/v/claudify.svg)](https://pypi.org/project/claudify/)
[![Python](https://img.shields.io/pypi/pyversions/claudify.svg)](https://pypi.org/project/claudify/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**English** | [中文](README.zh-CN.md)

A local proxy that translates the **Anthropic Messages API** into **OpenAI Chat Completions**, so any Anthropic-protocol client (e.g. Claude Code) can talk to an OpenAI-compatible backend.

## Platform support

**Linux and macOS only.** Windows is not supported and not tested.

- **Linux:** tested on systemd-based distros (Arch, Ubuntu, Fedora). `claudify install-service` writes a user-level systemd unit.
- **macOS:** the package runs, but `claudify install-service` is currently a **stub** — it will exit with an error. You can still run `claudify run` manually or wrap it in your own launchd plist.
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
claudify init-config --backend http://127.0.0.1:8000/v1 --api-key YOUR_KEY
claudify run
```

Default listen address: `127.0.0.1:4000`.

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
| `backend_base` | `CLAUDIFY_BACKEND_BASE` | — | OpenAI-compatible base URL. |
| `api_key` | `CLAUDIFY_API_KEY` | — | Bearer token sent upstream. |
| `host` | `CLAUDIFY_HOST` | `127.0.0.1` | Bind address. |
| `port` | `CLAUDIFY_PORT` | `4000` | Bind port. |
| `connect_timeout` | `CLAUDIFY_CONNECT_TIMEOUT` | `10.0` | Connection timeout (seconds). |
| `read_timeout` | `CLAUDIFY_READ_TIMEOUT` | `120.0` | Read timeout for non-streaming. |
| `write_timeout` | `CLAUDIFY_WRITE_TIMEOUT` | `10.0` | Write timeout. |
| `pool_timeout` | `CLAUDIFY_POOL_TIMEOUT` | `5.0` | Connection pool timeout. |
| `retry_attempts` | `CLAUDIFY_RETRY_ATTEMPTS` | `0` | Max retry attempts for 5xx errors. |
| `retry_backoff` | `CLAUDIFY_RETRY_BACKOFF` | `0.5` | Initial backoff in seconds (doubles each attempt). |
| `default_model` | `CLAUDIFY_DEFAULT_MODEL` | — | Used when the requested model is unknown. |
| `model_map` | (TOML only) | `{}` | Map Anthropic model names to upstream model names. |
| `cors_origins` | (TOML only) | `[]` | Allowed CORS origins. |

## Known unsupported features

- **Thinking/extended thinking** — blocks are dropped; no `thinking` content in responses
- **Cache control** — `cache_control` fields are stripped; no prompt caching
- **Count tokens** — returns char/word-based estimate, not real tokenization
- **Citations** — not mapped
- **PDF/document attachments** — not supported

See [docs/protocol-mapping.md](docs/protocol-mapping.md) for the full translation table.

## Run as a service

```bash
claudify install-service --backend http://127.0.0.1:8000/v1 --api-key YOUR_KEY
```

- **Linux (systemd, implemented):** writes `~/.config/systemd/user/claudify.service`, then `systemctl --user enable --now claudify`.
- **macOS (launchd, stub):** not implemented yet. The command will exit with an error message.

Inspect / control:

```bash
# Linux
systemctl --user status claudify
journalctl --user -u claudify -f
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
    └── launchd.py      # macOS launchd installer (stub)
```

## Development

```bash
uv pip install -e ".[dev]"
uv run pytest
uv run ruff check src tests
```

## License

MIT
