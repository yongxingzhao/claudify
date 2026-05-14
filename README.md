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
| `GET`  | `/v1/models` | Proxies upstream `/models`. Falls back to `[default_model]` if the upstream call fails. |
| `GET`  | `/health` | Liveness check. |

## Configuration

Edit `~/.config/claudify/config.toml` (created with mode `0600`) or override via `CLAUDIFY_*` env vars:

```toml
backend_base = "http://127.0.0.1:8000/v1"
api_key = "sk-..."
host = "127.0.0.1"
port = 4000
request_timeout = 120.0   # non-streaming HTTP timeout (seconds)
stream_timeout  = 600.0   # streaming connect timeout; read timeout is unbounded

[model_map]
"claude-opus-4-7"   = "hermes-agent"
"claude-sonnet-4-6" = "hermes-agent"

default_model = "hermes-agent"
```

| Setting | Env var | Default | Notes |
| --- | --- | --- | --- |
| `backend_base` | `CLAUDIFY_BACKEND_BASE` | — | OpenAI-compatible base URL, e.g. `http://127.0.0.1:8000/v1`. |
| `api_key` | `CLAUDIFY_API_KEY` | — | Bearer token sent upstream. |
| `host` | `CLAUDIFY_HOST` | `127.0.0.1` | Bind address. |
| `port` | `CLAUDIFY_PORT` | `4000` | Bind port. |
| `request_timeout` | `CLAUDIFY_REQUEST_TIMEOUT` | `120.0` | Timeout for non-streaming `/v1/messages`. |
| `stream_timeout` | `CLAUDIFY_STREAM_TIMEOUT` | `600.0` | Connect timeout for streaming; read timeout is `None` so long SSE streams aren't cut off. |
| `default_model` | `CLAUDIFY_DEFAULT_MODEL` | `hermes-agent` | Used when the requested model is unknown. |
| `model_map` | (TOML only) | `{}` | Map Anthropic model names to upstream model names. Unknown names fall through to `default_model`. |

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
├── settings.py         # pydantic-settings + ~/.config/claudify/config.toml loader
├── conversion.py       # anthropic ↔ openai pure functions (request, response, stream)
├── app.py              # FastAPI app: /v1/messages, /v1/messages/count_tokens, /v1/models, /health
├── cli.py              # Typer CLI (`claudify` console script)
└── service/
    ├── __init__.py     # platform dispatch
    ├── systemd.py      # Linux user-unit installer (implemented)
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
