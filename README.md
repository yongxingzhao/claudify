# claudify

[![PyPI](https://img.shields.io/pypi/v/claudify.svg)](https://pypi.org/project/claudify/)
[![Python](https://img.shields.io/pypi/pyversions/claudify.svg)](https://pypi.org/project/claudify/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A local proxy that translates the **Anthropic Messages API** into **OpenAI Chat Completions**, so any Anthropic-protocol client (e.g.
 Claude Code) can talk to an OpenAI-compatible backend.

## Install

```bash
uv tool install claudify
# or
pipx install claudify

From source:

git clone https://github.com/yongxingzhao/claudify.git
cd claudify
uv tool install .

Quick start

claudify init-config --backend http://127.0.0.1:8000/v1 --api-key YOUR_KEY
claudify run

Default listen address: 127.0.0.1:4000. Endpoints:

- POST /v1/messages
- GET /v1/models
- GET /health

Configuration

Edit ~/.config/claudify/config.toml (or override via CLAUDIFY_* env vars):

backend_base = "http://127.0.0.1:8000/v1"
api_key = "sk-..."
host = "127.0.0.1"
port = 4000

[model_map]
"claude-opus-4-7" = "hermes-agent"
"claude-sonnet-4-6" = "hermes-agent"

default_model = "hermes-agent"

Run as a service

claudify install-service --backend http://127.0.0.1:8000/v1 --api-key YOUR_KEY
# Linux:  systemctl --user status claudify
# macOS:  launchctl list | grep claudify

License

MIT
