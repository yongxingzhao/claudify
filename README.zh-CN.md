# claudify

[![PyPI](https://img.shields.io/pypi/v/claudify.svg)](https://pypi.org/project/claudify/)
[![Python](https://img.shields.io/pypi/pyversions/claudify.svg)](https://pypi.org/project/claudify/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

[English](README.md) | **中文**

Anthropic Messages API → OpenAI Chat Completions 翻译代理。让 Claude Code 等 Anthropic 协议客户端能直接驱动 OpenAI 兼容后端。

## 平台支持

**仅支持 Linux 和 macOS。** Windows 不支持且未测试。

- **Linux：** 在基于 systemd 的发行版（Arch、Ubuntu、Fedora）上测试通过。`claudify install-service` 写入用户级 systemd unit。
- **macOS：** `claudify install-service` 写入 LaunchAgent plist 并通过 `launchctl` 加载。
- **Windows：** 未测试。请使用 WSL2。

## 安装

```bash
uv tool install claudify
# 或
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
# 1. 初始化配置
claudify init-config

# 2. 编辑 ~/.config/claudify/config.toml，填入后端地址和 API Key

# 3. 启动
claudify run
```

然后将 Claude Code 的 `ANTHROPIC_BASE_URL` 指向 `http://127.0.0.1:4000`：

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:4000
```

## 功能

- 完整 Anthropic Messages API ↔ OpenAI Chat Completions 双向翻译
- 流式（SSE）和非流式请求
- 工具调用（tool_use / tool_result）双向映射
- 图片内容（base64 + URL）转换
- 可配置模型映射（model_map）
- 自动重试（502/503/504/429）+ 指数退避（上限 30 秒）
- Prometheus 指标（/metrics）
- 结构化日志 + 请求 ID 追踪
- macOS / Linux 系统服务安装
- CORS 支持

## 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/v1/messages` | Anthropic Messages API，支持流式（SSE）和工具调用 |
| `POST` | `/v1/messages/count_tokens` | 估算输入 token 数（基于字符/词启发式，不调用上游） |
| `GET`  | `/v1/models` | 列出配置中的映射模型 |
| `GET`  | `/health` | 存活检查，可选上游健康检测 |
| `GET`  | `/metrics` | Prometheus 格式指标（请求数、延迟、上游状态） |

## 配置

编辑 `~/.config/claudify/config.toml`（由 `claudify init-config` 创建，权限 0600），或通过 `CLAUDIFY_*` 环境变量覆盖：

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

| 字段 | 环境变量 | 默认值 | 说明 |
|------|----------|--------|------|
| `backend_base` | `CLAUDIFY_BACKEND_BASE` | `http://127.0.0.1:8000/v1` | OpenAI 兼容后端地址 |
| `api_key` | `CLAUDIFY_API_KEY` | _(空)_ | 发送到上游的 Bearer token |
| `inbound_api_key` | `CLAUDIFY_INBOUND_API_KEY` | _(空)_ | 设置后要求入站请求携带匹配的 `x-api-key` 头 |
| `host` | `CLAUDIFY_HOST` | `127.0.0.1` | 监听地址 |
| `port` | `CLAUDIFY_PORT` | `4000` | 监听端口 |
| `log_level` | `CLAUDIFY_LOG_LEVEL` | `INFO` | 日志级别：DEBUG, INFO, WARNING, ERROR |
| `log_format` | `CLAUDIFY_LOG_FORMAT` | `text` | `text`（默认）或 `json`（结构化日志） |
| `pool_limit` | `CLAUDIFY_POOL_LIMIT` | `100` | httpx 连接池最大连接数 |
| `connect_timeout` | `CLAUDIFY_CONNECT_TIMEOUT` | _(同 request_timeout)_ | 连接超时（秒） |
| `read_timeout` | `CLAUDIFY_READ_TIMEOUT` | _(同 request_timeout)_ | 非流式读取超时（秒），流式时自动设为 None |
| `write_timeout` | `CLAUDIFY_WRITE_TIMEOUT` | _(同 request_timeout)_ | 写入超时（秒） |
| `pool_timeout` | `CLAUDIFY_POOL_TIMEOUT` | _(同 request_timeout)_ | 连接池超时（秒） |
| `request_timeout` | `CLAUDIFY_REQUEST_TIMEOUT` | `300.0` | 未设置的超时字段的兜底值 |
| `retry_attempts` | `CLAUDIFY_RETRY_ATTEMPTS` | `0` | 5xx/429 错误最大重试次数（首次请求之后） |
| `retry_backoff` | `CLAUDIFY_RETRY_BACKOFF` | `0.5` | 初始退避时间（秒），每次翻倍（上限 30 秒） |
| `default_model` | `CLAUDIFY_DEFAULT_MODEL` | _(空)_ | 未知模型使用的默认模型 |
| `model_map` | _(仅 TOML)_ | `{}` | Anthropic 模型名 → 上游模型名映射 |
| `cors_origins` | _(仅 TOML)_ | `[]` | CORS 允许的来源列表 |
| `max_body_size` | `CLAUDIFY_MAX_BODY_SIZE` | `10485760` | 最大请求体大小（字节） |
| `upstream_health_path` | `CLAUDIFY_UPSTREAM_HEALTH_PATH` | _(空)_ | 上游健康检查路径 |

## 已知不支持

- **Thinking / extended thinking** — 请求能过，但思考内容被丢弃，响应中无 thinking 块
- **Cache control** — `cache_control` 字段被剥离，不支持提示缓存
- **Count tokens** — 返回基于字符/词的估算，非真实分词
- **Citations** — 未映射
- **PDF / 文档附件** — 不支持

完整协议映射表见 [docs/protocol-mapping.md](docs/protocol-mapping.md)。

## 系统服务

```bash
claudify install-service --backend http://127.0.0.1:8000/v1
```

- **Linux（systemd）：** 写入 `~/.config/systemd/user/claudify.service`，然后 `systemctl --user enable --now claudify`。
- **macOS（launchd）：** 写入 `~/Library/LaunchAgents/com.claudify.plist`，然后通过 `launchctl` 加载。

注意：`api_key` 不会写入服务文件。Claudify 运行时从 `config.toml` 读取。

查看 / 控制：

```bash
# Linux
systemctl --user status claudify
journalctl --user -u claudify -f

# macOS
launchctl list | grep claudify
```

## 项目结构

```
src/claudify/
├── settings.py         # pydantic-settings + config.toml 加载器
├── conversion.py       # anthropic ↔ openai 纯函数
├── sse.py              # SSE 事件工具 + 停止原因映射
├── errors.py           # 错误类型映射、脱敏、透传
├── metrics.py          # Prometheus 格式指标收集器
├── retry.py            # 带指数退避的重试
├── routes.py           # FastAPI 路由处理
├── app.py              # FastAPI 应用工厂 + 中间件
├── cli.py              # Typer CLI（claudify 命令行）
└── service/
    ├── __init__.py  # (空文件)
    ├── systemd.py      # Linux 用户级 unit 安装器
    └── launchd.py      # macOS launchd 安装器
```

## 开发

```bash
uv sync --group dev
uv run pytest
uv run ruff check src tests
```

## 许可证

MIT
