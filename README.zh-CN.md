# claudify

[![PyPI](https://img.shields.io/pypi/v/claudify.svg)](https://pypi.org/project/claudify/)
[![Python](https://img.shields.io/pypi/pyversions/claudify.svg)](https://pypi.org/project/claudify/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

[English](README.md) | **中文**

一个本地代理，把 **Anthropic Messages API** 翻译成 **OpenAI Chat Completions**，让任意走 Anthropic 协议的客户端（例如 Claude Code）都能对接 OpenAI 兼容后端。

## 平台支持

**仅支持 Linux 和 macOS**，不支持 Windows，也未做测试。

- **Linux：** 在使用 systemd 的发行版上测试通过（Arch、Ubuntu、Fedora）。`claudify install-service` 会写入一个用户级 systemd unit。
- **macOS：** 程序本身可正常运行，但 `claudify install-service` 目前是 **stub**，执行后会直接报错退出。你仍然可以手动 `claudify run`，或者自己写一个 launchd plist 包起来。
- **Windows：** 未测试，请使用 WSL2。

## 安装

```bash
uv tool install claudify
# 或者
pipx install claudify
```

从源码安装：

```bash
git clone https://github.com/yongxingzhao/claudify.git
cd claudify
uv tool install .
```

## 快速开始

```bash
claudify init-config --backend http://127.0.0.1:8000/v1 --api-key YOUR_KEY
claudify run
```

默认监听地址：`127.0.0.1:4000`。

## 端点

| 方法 | 路径 | 说明 |
| ---- | ---- | ---- |
| `POST` | `/v1/messages` | Anthropic Messages API，支持流式（SSE）与工具调用。 |
| `POST` | `/v1/messages/count_tokens` | 输入 token 估算（按字符的近似算法，不会发起上游请求）。 |
| `GET`  | `/v1/models` | 代理上游 `/models`；上游失败时回退到 `[default_model]`。 |
| `GET`  | `/health` | 存活检查。 |

## 配置

编辑 `~/.config/claudify/config.toml`（创建时权限为 `0600`），或通过 `CLAUDIFY_*` 环境变量覆盖：

```toml
backend_base = "http://127.0.0.1:8000/v1"
api_key = "sk-..."
host = "127.0.0.1"
port = 4000
request_timeout = 120.0   # 非流式 HTTP 超时（秒）
stream_timeout  = 600.0   # 流式连接超时；读超时不限，避免长 SSE 被截断

[model_map]
"claude-opus-4-7"   = "hermes-agent"
"claude-sonnet-4-6" = "hermes-agent"

default_model = "hermes-agent"
```

| 配置项 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `backend_base` | `CLAUDIFY_BACKEND_BASE` | — | OpenAI 兼容后端 base URL，例如 `http://127.0.0.1:8000/v1`。 |
| `api_key` | `CLAUDIFY_API_KEY` | — | 转发到上游的 Bearer token。 |
| `host` | `CLAUDIFY_HOST` | `127.0.0.1` | 监听地址。 |
| `port` | `CLAUDIFY_PORT` | `4000` | 监听端口。 |
| `request_timeout` | `CLAUDIFY_REQUEST_TIMEOUT` | `120.0` | 非流式 `/v1/messages` 的超时时间。 |
| `stream_timeout` | `CLAUDIFY_STREAM_TIMEOUT` | `600.0` | 流式请求的连接超时；读超时为 `None`，长 SSE 不会被截断。 |
| `default_model` | `CLAUDIFY_DEFAULT_MODEL` | `hermes-agent` | 当请求的模型名未在 `model_map` 中时使用此值。 |
| `model_map` | （仅 TOML） | `{}` | Anthropic 模型名到上游模型名的映射，未命中则 fallback 到 `default_model`。 |

## 作为服务运行

```bash
claudify install-service --backend http://127.0.0.1:8000/v1 --api-key YOUR_KEY
```

- **Linux（systemd，已实现）：** 写入 `~/.config/systemd/user/claudify.service`，然后 `systemctl --user enable --now claudify`。
- **macOS（launchd，stub）：** 暂未实现，命令会直接报错。

查看 / 控制：

```bash
# Linux
systemctl --user status claudify
journalctl --user -u claudify -f
```

## 项目结构

```
src/claudify/
├── settings.py         # pydantic-settings + ~/.config/claudify/config.toml 加载
├── conversion.py       # anthropic ↔ openai 纯函数（请求、响应、流）
├── app.py              # FastAPI 应用：/v1/messages、/v1/messages/count_tokens、/v1/models、/health
├── cli.py              # Typer CLI（`claudify` 控制台命令）
└── service/
    ├── __init__.py     # 按平台分发
    ├── systemd.py      # Linux 用户 unit 安装器（已实现）
    └── launchd.py      # macOS launchd 安装器（stub）
```

## 开发

```bash
uv pip install -e ".[dev]"
uv run pytest
uv run ruff check src tests
```

## 许可证

MIT
