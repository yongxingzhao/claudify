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

## 架构

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

**请求流程：**

1. 客户端向 Claudify 发送 Anthropic Messages API 请求。
2. Claudify 验证入站认证（如已配置）并将请求转换为 OpenAI 格式。
3. 请求转发到配置的 OpenAI 兼容后端。
4. 后端响应转换回 Anthropic 格式。
5. 转换后的响应流式返回或一次性返回给客户端。

## 使用场景

### 使用 Claude Code 调用 OpenAI 模型

[Claude Code](https://docs.anthropic.com/en/docs/claude-code) 原生使用 Anthropic Messages API。运行 Claudify 后，将 Claude Code 指向代理即可使用任意 OpenAI 兼容模型：

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:4000 ANTHROPIC_API_KEY=*** claude
```

### 使用 Anthropic SDK 对接任意 OpenAI 兼容 API

任何使用 Anthropic Python SDK 的工具都能通过 Claudify 对接 OpenAI 兼容后端：

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

### 多后端负载均衡

在不同端口运行多个 Claudify 实例，每个实例指向不同后端，然后使用反向代理（如 nginx 或 HAProxy）进行负载均衡：

```toml
# 后端 1
backend_base = "http://10.0.1.10:8000/v1"
api_key = "sk-backend1"
port = 4001

# 后端 2
backend_base = "http://10.0.1.20:8000/v1"
api_key = "sk-backend2"
port = 4002
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

## 安全

### 入站认证

在配置中设置 `inbound_api_key` 以要求入站请求进行认证。客户端必须在 `x-api-key` 请求头中携带此密钥：

```bash
curl -H "x-api-key: your-secret-key" http://127.0.0.1:4000/v1/messages ...
```

> **注意：** 入站认证仅用于代理访问控制，密钥不会转发到上游。

### 上游认证

在配置中设置 `api_key` 以认证 OpenAI 兼容后端。该值会作为 `Bearer` token 通过 `Authorization` 请求头发送给上游服务。

### 配置文件权限

配置文件 `~/.config/claudify/config.toml` 创建时权限为 `0600`（仅所有者可读写），防止系统中其他用户读取你的 API 密钥：

```bash
ls -la ~/.config/claudify/config.toml
# -rw------- 1 user user ... config.toml
```

### 错误信息脱敏

Claudify 在将上游后端的错误信息转发给客户端之前会进行脱敏处理。敏感信息（API 密钥、内部 URL、堆栈跟踪）会通过正则表达式进行屏蔽，防止信息泄露。

## 性能

### 连接池设置

| 设置项 | 默认值 | 说明 |
| ------- | ------- | ----------- |
| `pool_limit` | `100` | httpx 连接池最大并发连接数 |
| `pool_timeout` | `300s` | 从连接池获取连接的最大等待时间 |
| `connect_timeout` | `300s` | 建立新连接的最大超时时间 |

对于高并发工作负载，建议增加 `pool_limit` 并确保后端能承受相应的连接数。

### 超时建议

| 工作负载类型 | `read_timeout` | 备注 |
| ------------- | -------------- | ----- |
| 流式（默认） | `300s` | 流式请求内部绕过读取超时，此值为安全兜底 |
| 非流式（短） | `60s` | 快速完成的请求 |
| 非流式（长） | `300–600s` | 大上下文或复杂提示 |

流式请求使用 `httpx` 的 `timeout(streaming=True)`，默认将 `read` 设为 `None`（无限）。`read_timeout` 仅适用于非流式响应。

### 重试策略

| 设置项 | 默认值 | 建议 |
| ------- | ------- | -------------- |
| `retry_attempts` | `0` | 生产环境建议设为 `2–3`，以处理临时性 5xx/429 错误 |
| `retry_backoff` | `0.5` | 保持 `0.5` 即可；退避时间每次翻倍，上限 30 秒 |

- 重试适用于 **5xx**（服务器错误）和 **429**（速率限制）响应。
- 429 响应会尊重 `Retry-After` 请求头。
- 重试会消耗上游额外的 token/时间，对昂贵的模型应避免过多重试次数。

## 故障排查

| 症状 | 可能原因 | 解决方案 |
| ------- | ------------ | --- |
| 调用后端时 `Connection refused` | OpenAI 兼容后端未运行 | 启动后端并确认 `backend_base` 指向正确的 URL |
| 上游后端返回 `401 Unauthorized` | 上游 API 密钥无效或缺失 | 在 `config.toml` 或通过 `CLAUDIFY_API_KEY` 设置正确的 `api_key` |
| 调用 Claudify 时 `401 Unauthorized` | 已设置 `inbound_api_key` 但客户端未提供 | 在请求中添加 `x-api-key` 头，或取消设置 `inbound_api_key` |
| 流式超时 | 读取超时对于长响应来说太短 | 在配置中增加 `read_timeout` 或 `request_timeout`（默认：300s） |
| `Model not found` 错误 | 请求的模型名不在 `model_map` 中且未设置 `default_model` | 在配置中添加 `[model_map]` 条目或设置 `default_model` |
| 响应为空或内容异常 | 上游模型不支持请求的功能（如工具调用） | 检查上游模型的能力；参见下方已知不支持的功能 |

开启调试日志以诊断问题：

```toml
log_level = "DEBUG"
log_format = "json"
```

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
