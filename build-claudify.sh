#!/usr/bin/env bash
set -euo pipefail

cd /home/yoxi/vsproject/claudify

# === 校验起点 ===
if [[ ! -f _legacy_anthropic_proxy.py ]]; then
    echo "ERROR: _legacy_anthropic_proxy.py not found. 先 cp 源码过来。" >&2
    exit 1
fi

mkdir -p src/claudify/service

cat >pyproject.toml <<'EOF'
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "claudify"
version = "0.1.0"
description = "Anthropic Messages API → OpenAI Chat Completions translation proxy"
readme = "README.md"
requires-python = ">=3.10,<4"
license = "MIT"
authors = [{ name = "Yongxing Zhao", email = "yongxingzhao@users.noreply.github.com" }]
keywords = ["anthropic", "claude", "openai", "proxy", "api", "translation", "claude-code"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Operating System :: POSIX :: Linux",
    "Operating System :: MacOS",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Internet :: Proxy Servers",
    "Topic :: Software Development :: Libraries :: Python Modules",
]
dependencies = [
    "fastapi>=0.110",
    "httpx>=0.27",
    "pydantic>=2.5",
    "pydantic-settings>=2.1",
    "typer>=0.12",
    "uvicorn>=0.27",
]

[project.urls]
Source = "https://github.com/yongxingzhao/claudify"
Issues = "https://github.com/yongxingzhao/claudify/issues"

[project.scripts]
claudify = "claudify.cli:app"

[tool.hatch.build.targets.wheel]
packages = ["src/claudify"]

[tool.hatch.build.targets.wheel.force-include]
"src/claudify/py.typed" = "claudify/py.typed"
EOF

cat >src/claudify/__init__.py <<'EOF'
"""Claudify — translate Anthropic Messages API to OpenAI Chat Completions."""

__version__ = "0.1.0"

from .settings import Settings
from .app import create_app
from .conversion import (
    anthropic_to_openai,
    openai_to_anthropic_response,
    stream_openai_to_anthropic,
    extract_text_from_blocks,
    map_model,
)

__all__ = [
    "__version__",
    "Settings",
    "create_app",
    "anthropic_to_openai",
    "openai_to_anthropic_response",
    "stream_openai_to_anthropic",
    "extract_text_from_blocks",
    "map_model",
]
EOF

cat >src/claudify/__main__.py <<'EOF'
from .cli import app

if __name__ == "__main__":
    app()
EOF

touch src/claudify/py.typed

cat >src/claudify/settings.py <<'EOF'
"""Configuration loaded from env vars and optional TOML file."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore


def default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "claudify" / "config.toml"


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


class Settings(BaseSettings):
    backend_base: str = Field(default="http://127.0.0.1:8000/v1")
    api_key: str = Field(default="")
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=4000)
    log_level: str = Field(default="INFO")
    request_timeout: float = Field(default=300.0)
    model_map: dict[str, str] = Field(default_factory=dict)
    default_model: str = Field(default="")

    model_config = SettingsConfigDict(
        env_prefix="CLAUDIFY_",
        env_file=None,
        extra="ignore",
    )

    @classmethod
    def load(cls, config_path: Path | None = None) -> "Settings":
        path = config_path or default_config_path()
        toml_data = _load_toml(path)
        return cls(**toml_data)
EOF

cat >src/claudify/conversion.py <<'EOF'
"""Pure functions for Anthropic ↔ OpenAI protocol conversion."""
from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator


def map_model(model: str, model_map: dict[str, str], default: str = "") -> str:
    if model in model_map:
        return model_map[model]
    if default:
        return default
    return model


def extract_text_from_blocks(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append(block.get("text", ""))
        elif btype == "tool_result":
            tc = block.get("content")
            if isinstance(tc, str):
                parts.append(tc)
            elif isinstance(tc, list):
                for sub in tc:
                    if isinstance(sub, dict) and sub.get("type") == "text":
                        parts.append(sub.get("text", ""))
        elif btype == "thinking":
            pass
        elif btype == "image":
            parts.append("[image omitted]")
    return "\n".join(p for p in parts if p)


def anthropic_to_openai(payload: dict[str, Any], model_map: dict[str, str], default_model: str = "") -> dict[str, Any]:
    out_messages: list[dict[str, Any]] = []
    system = payload.get("system")
    if isinstance(system, str) and system.strip():
        out_messages.append({"role": "system", "content": system})
    elif isinstance(system, list):
        sys_text = extract_text_from_blocks(system)
        if sys_text.strip():
            out_messages.append({"role": "system", "content": sys_text})
    for msg in payload.get("messages", []):
        role = msg.get("role")
        content = msg.get("content")
        text = extract_text_from_blocks(content) if not isinstance(content, str) else content
        if role in ("user", "assistant") and text:
            out_messages.append({"role": role, "content": text})
    has_user = any(m["role"] == "user" for m in out_messages)
    if not has_user:
        out_messages.append({"role": "user", "content": "."})
    model = map_model(payload.get("model", ""), model_map, default_model)
    openai_payload: dict[str, Any] = {
        "model": model,
        "messages": out_messages,
        "stream": bool(payload.get("stream", False)),
    }
    for k in ("temperature", "top_p", "max_tokens", "stop_sequences"):
        if k in payload:
            target = "stop" if k == "stop_sequences" else k
            openai_payload[target] = payload[k]
    return openai_payload


def openai_to_anthropic_response(openai_resp: dict[str, Any], original_model: str) -> dict[str, Any]:
    choice = (openai_resp.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    text = msg.get("content") or ""
    finish = choice.get("finish_reason") or "stop"
    stop_reason_map = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}
    usage = openai_resp.get("usage") or {}
    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": original_model,
        "content": [{"type": "text", "text": text}] if text else [],
        "stop_reason": stop_reason_map.get(finish, "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


async def stream_openai_to_anthropic(
    openai_lines: AsyncIterator[bytes],
    original_model: str,
) -> AsyncIterator[bytes]:
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    def sse(event: str, data: dict[str, Any]) -> bytes:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")
    yield sse("message_start", {
        "type": "message_start",
        "message": {
            "id": msg_id, "type": "message", "role": "assistant", "model": original_model,
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    })
    yield sse("content_block_start", {
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "text", "text": ""},
    })
    finish_reason = "stop"
    output_tokens = 0
    async for raw in openai_lines:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        body = line[5:].strip()
        if body == "[DONE]":
            break
        try:
            chunk = json.loads(body)
        except json.JSONDecodeError:
            continue
        choices = chunk.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        piece = delta.get("content")
        if piece:
            output_tokens += 1
            yield sse("content_block_delta", {
                "type": "content_block_delta", "index": 0,
                "delta": {"type": "text_delta", "text": piece},
            })
        if choices[0].get("finish_reason"):
            finish_reason = choices[0]["finish_reason"]
    stop_map = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}
    yield sse("content_block_stop", {"type": "content_block_stop", "index": 0})
    yield sse("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": stop_map.get(finish_reason, "end_turn"), "stop_sequence": None},
        "usage": {"output_tokens": output_tokens},
    })
    yield sse("message_stop", {"type": "message_stop"})
EOF

cat >src/claudify/app.py <<'EOF'
"""FastAPI app factory."""
from __future__ import annotations

import json as _json
import logging
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .conversion import (
    anthropic_to_openai,
    openai_to_anthropic_response,
    stream_openai_to_anthropic,
)
from .settings import Settings


def _passthrough_error(body: bytes) -> dict:
    try:
        parsed = _json.loads(body)
        if isinstance(parsed, dict):
            return {"error": {"type": "upstream_error", "message": str(parsed)}}
    except Exception:
        pass
    return {"error": {"type": "upstream_error", "message": body.decode("utf-8", errors="replace")[:2000]}}


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or Settings.load()
    log = logging.getLogger("claudify")
    app = FastAPI(title="claudify", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/models")
    async def models() -> dict:
        ids = sorted(set(list(s.model_map.keys()) + ([s.default_model] if s.default_model else [])))
        return {
            "object": "list",
            "data": [{"id": m, "object": "model", "created": int(time.time()), "owned_by": "claudify"} for m in ids],
        }

    @app.post("/v1/messages")
    async def messages(request: Request):
        try:
            payload = await request.json()
        except Exception as e:
            return JSONResponse(
                status_code=400,
                content={"error": {"type": "invalid_request_error", "message": f"invalid JSON: {e}"}},
            )

        original_model = payload.get("model", "")
        stream = bool(payload.get("stream", False))
        openai_payload = anthropic_to_openai(payload, s.model_map, s.default_model)
        url = f"{s.backend_base.rstrip('/')}/chat/completions"
        headers = {"Content-Type": "application/json",
                   "Accept": "text/event-stream" if stream else "application/json"}
        if s.api_key:
            headers["Authorization"] = f"Bearer {s.api_key}"

        client = httpx.AsyncClient(timeout=s.request_timeout)
        try:
            if stream:
                req = client.build_request("POST", url, json=openai_payload, headers=headers)
                upstream = await client.send(req, stream=True)
                if upstream.status_code >= 400:
                    body = await upstream.aread()
                    await upstream.aclose()
                    await client.aclose()
                    return JSONResponse(status_code=upstream.status_code, content=_passthrough_error(body))

                async def relay():
                    try:
                        async for chunk in stream_openai_to_anthropic(upstream.aiter_lines(), original_model):
                            yield chunk
                    finally:
                        await upstream.aclose()
                        await client.aclose()

                return StreamingResponse(relay(), media_type="text/event-stream")

            resp = await client.post(url, json=openai_payload, headers=headers)
            await client.aclose()
            if resp.status_code >= 400:
                return JSONResponse(status_code=resp.status_code, content=_passthrough_error(resp.content))
            return JSONResponse(content=openai_to_anthropic_response(resp.json(), original_model))

        except httpx.HTTPError as e:
            await client.aclose()
            log.exception("upstream error")
            return JSONResponse(
                status_code=502,
                content={"error": {"type": "upstream_unavailable", "message": f"{type(e).__name__}: {e}"}},
            )
        except Exception as e:
            await client.aclose()
            log.exception("internal error")
            return JSONResponse(
                status_code=500,
                content={"error": {"type": "internal_error", "message": str(e)}},
            )

    return app
EOF

cat >src/claudify/cli.py <<'EOF'
"""Typer CLI."""
from __future__ import annotations

import os
from pathlib import Path

import typer
import uvicorn

from . import __version__
from .settings import Settings, default_config_path

app = typer.Typer(help="Anthropic → OpenAI protocol translation proxy.")


@app.command()
def version() -> None:
    typer.echo(f"claudify {__version__}")


@app.command("config-path")
def config_path() -> None:
    typer.echo(str(default_config_path()))


@app.command("init-config")
def init_config(
    backend: str = typer.Option("http://127.0.0.1:8000/v1"),
    api_key: str = typer.Option(""),
    port: int = typer.Option(4000),
    host: str = typer.Option("127.0.0.1"),
) -> None:
    path = default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        typer.echo(f"refused: {path} already exists")
        raise typer.Exit(code=1)
    path.write_text(
        f'backend_base = "{backend}"\n'
        f'api_key = "{api_key}"\n'
        f'host = "{host}"\n'
        f'port = {port}\n\n'
        f'# [model_map]\n'
        f'# "claude-opus-4-7" = "hermes-agent"\n\n'
        f'# default_model = "hermes-agent"\n',
        encoding="utf-8",
    )
    os.chmod(path, 0o600)
    typer.echo(f"wrote {path}")


@app.command()
def run(
    host: str = typer.Option(""),
    port: int = typer.Option(0),
) -> None:
    s = Settings.load()
    h = host or s.host
    p = port or s.port
    typer.echo(f"claudify v{__version__} → http://{h}:{p}, forwarding to {s.backend_base}")
    from .app import create_app
    uvicorn.run(create_app(s), host=h, port=p, log_level=s.log_level.lower())


@app.command("install-service")
def install_service(
    backend: str = typer.Option(...),
    api_key: str = typer.Option(...),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(4000),
) -> None:
    import platform
    cfg = default_config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    if not cfg.exists():
        cfg.write_text(
            f'backend_base = "{backend}"\napi_key = "{api_key}"\nhost = "{host}"\nport = {port}\n',
            encoding="utf-8",
        )
        os.chmod(cfg, 0o600)
        typer.echo(f"wrote {cfg}")
    sysname = platform.system()
    if sysname == "Linux":
        from .service.systemd import install as I
        I()
    elif sysname == "Darwin":
        from .service.launchd import install as I
        I()
    else:
        typer.echo(f"unsupported platform: {sysname}")
        raise typer.Exit(code=2)


@app.command("uninstall-service")
def uninstall_service() -> None:
    import platform
    sysname = platform.system()
    if sysname == "Linux":
        from .service.systemd import uninstall as U
        U()
    elif sysname == "Darwin":
        from .service.launchd import uninstall as U
        U()
    else:
        typer.echo(f"unsupported platform: {sysname}")
        raise typer.Exit(code=2)
EOF

: >src/claudify/service/__init__.py

cat >src/claudify/service/systemd.py <<'EOF'
"""Linux user-level systemd installer."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

UNIT_NAME = "claudify.service"

UNIT_TEMPLATE = """\
[Unit]
Description=claudify — Anthropic → OpenAI proxy
After=network-online.target

[Service]
Type=simple
ExecStart={exec_path} run
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
"""


def _unit_dir() -> Path:
    p = Path.home() / ".config" / "systemd" / "user"
    p.mkdir(parents=True, exist_ok=True)
    return p


def install() -> None:
    exec_path = shutil.which("claudify") or "claudify"
    target = _unit_dir() / UNIT_NAME
    target.write_text(UNIT_TEMPLATE.format(exec_path=exec_path), encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", UNIT_NAME], check=True)
    print(f"installed {target}")


def uninstall() -> None:
    target = _unit_dir() / UNIT_NAME
    subprocess.run(["systemctl", "--user", "disable", "--now", UNIT_NAME], check=False)
    if target.exists():
        target.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    print(f"removed {target}")
EOF

cat >src/claudify/service/launchd.py <<'EOF'
"""macOS LaunchAgent installer."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

LABEL = "com.claudify.claudify"
PLIST_NAME = f"{LABEL}.plist"

PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{label}</string>
    <key>ProgramArguments</key>
    <array><string>{exec_path}</string><string>run</string></array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>{log_dir}/claudify.out.log</string>
    <key>StandardErrorPath</key><string>{log_dir}/claudify.err.log</string>
</dict>
</plist>
"""


def _agent_dir() -> Path:
    p = Path.home() / "Library" / "LaunchAgents"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _log_dir() -> Path:
    p = Path.home() / "Library" / "Logs" / "claudify"
    p.mkdir(parents=True, exist_ok=True)
    return p


def install() -> None:
    exec_path = shutil.which("claudify") or "claudify"
    target = _agent_dir() / PLIST_NAME
    target.write_text(PLIST_TEMPLATE.format(label=LABEL, exec_path=exec_path, log_dir=str(_log_dir())), encoding="utf-8")
    subprocess.run(["launchctl", "unload", str(target)], check=False)
    subprocess.run(["launchctl", "load", str(target)], check=True)
    print(f"installed {target}")


def uninstall() -> None:
    target = _agent_dir() / PLIST_NAME
    subprocess.run(["launchctl", "unload", str(target)], check=False)
    if target.exists():
        target.unlink()
    print(f"removed {target}")
EOF

cat >README.md <<'EOF'
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
EOF

cat >LICENSE <<'EOF'
MIT License

Copyright (c) 2026 Yongxing Zhao

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
EOF

cat >.gitignore <<'EOF'
pycache/
*.py[cod]
*$py.class
*.so
.Python
build/
dist/
*.egg-info/
.egg
wheels/
.eggs/
.venv/
.venv-/
venv/
env/
.pytest_cache/
.coverage
htmlcov/
.tox/
.mypy_cache/
.pyre/
.pytype/
.ruff_cache/
.vscode/
.idea/
*.swp
*.swo
.DS_Store
Thumbs.db
.env
.env.local
config.toml
*.local
EOF

cat >CLAUDE.md <<'EOF'
CLAUDE.md

Guidance for Claude Code working in this repository.

What this is

A FastAPI proxy translating Anthropic Messages API → OpenAI Chat Completions, so Anthropic-protocol clients (Claude Code, etc.) can
drive OpenAI-compatible backends.

Common commands

uv pip install -e .
claudify run
uv build
python -m claudify

Architecture

- src/claudify/settings.py — Settings (pydantic-settings); env CLAUDIFY_* + ~/.config/claudify/config.toml.
- src/claudify/conversion.py — pure functions: anthropic_to_openai(), openai_to_anthropic_response(), stream_openai_to_anthropic().
The "no user message" guard lives here.
- src/claudify/app.py — create_app(settings) factory; routes /v1/messages, /v1/models, /health. Network errors → 502 
upstream_unavailable.
- src/claudify/cli.py — Typer entry point; claudify console script.
- src/claudify/service/ — systemd.py + launchd.py for claudify install-service.

Pitfalls

- _legacy_anthropic_proxy.py is the original single-file version, kept for reference only — don't import from it; it embeds an API
key.
- ~/.config/claudify/config.toml is gitignored and chmod 0600 — never commit.
- Unknown model names fall through model_map → default_model → original.
EOF

echo
echo "=== creating venv and installing ==="
python -m venv .venv
. .venv/bin/activate
pip -q install --upgrade pip
pip -q install -e .

echo
echo "=== smoke tests ==="
claudify version
claudify config-path
python -c "import claudify; print('import OK, version =', claudify.version)"

deactivate

echo
echo "=== git init + commit ==="
git init -b main >/dev/null
git config user.name "yoxi"
git config user.email "yongxingzhao@users.noreply.github.com"
git add -A
git -c color.status=never status --short
git commit -m "Initial commit: claudify v0.1.0

Anthropic Messages API to OpenAI Chat Completions translation proxy.

- FastAPI app with /v1/messages, /v1/models, /health endpoints
- Streaming SSE bidirectional conversion
- pydantic-settings config (env + ~/.config/claudify/config.toml)
- typer CLI with run / install-service / init-config subcommands
- systemd user unit (Linux) + launchd LaunchAgent (macOS) installers
- 502 upstream_unavailable error mapping for backend network failures
- PEP 561 typed package (py.typed)" >/dev/null

git remote add origin git@github.com:yongxingzhao/claudify.git 2>/dev/null || git remote set-url origin
git@github.com:yongxingzhao/claudify.git

echo
echo "=== ssh test (expect: Hi yongxingzhao!) ==="
ssh -T git@github.com || true

echo
echo "=== git push ==="
git push -u origin main

echo
echo "=== DONE ==="
echo "Repo:  https://github.com/yongxingzhao/claudify"
echo "Local: /home/yoxi/vsproject/claudify"
OUTER_EOF

chmod +x /tmp/build-claudify.sh
echo "已写入 /tmp/build-claudify.sh"
ls -la /tmp/build-claudify.sh
