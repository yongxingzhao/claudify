# claudify

[![PyPI](https://img.shields.io/pypi/v/claudify.svg)](https://pypi.org/pypi/claudify/)
[![Python](https://img.shields.io/pypi/pyversions/claudify.svg)](https://pypi.org/pypi/claudify/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Anthropic Messages API → OpenAI Chat Completions 翻译代理。让 Claude Code 等 Anthropic 协议客户端能直接驱动 OpenAI 兼容后端。

## 安装

```bash
pip install claudify
# 或
uv tool install claudify
```

## 快速开始

```bash
# 1. 初始化配置
claudify init-config

# 2. 编辑 ~/.config/claudify/config.toml，填入你的后端地址和 API Key

# 3. 启动
claudify run
```

然后将 Claude Code 的 `ANTHROPIC_BASE_URL` 指向 `http://localhost:8000`：

```bash
export ANTHROPIC_BASE_URL=http://localhost:8000
```

## 功能

- 完整 Anthropic Messages API ↔ OpenAI Chat Completions 双向翻译
- 流式（SSE）和非流式请求
- 工具调用（tool_use / tool_result）双向映射
- 图片内容（base64 + URL）转换
- 可配置模型映射（model_map）
- 自动重试（502/503/504）+ 指数退避
- Prometheus 指标（/metrics）
- 结构化日志 + 请求 ID 追踪
- macOS / Linux 系统服务安装
- CORS 支持

## 端点

| 路径 | 方法 | 说明 |
|------|------|------|
| `/v1/messages` | POST | 代理 Anthropic Messages API |
| `/v1/models` | GET | 列出配置中的映射模型 |
| `/v1/messages/count_tokens` | POST | 估算 token 数量 |
| `/health` | GET | 健康检查 |
| `/metrics` | GET | Prometheus 指标 |

## 配置

配置文件：`~/.config/claudify/config.toml`（可通过 `claudify config-path` 查看）

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `backend_base` | `http://127.0.0.1:8000/v1` | OpenAI 兼容后端地址 |
| `api_key` | — | 后端 API Key |
| `host` | `127.0.0.1` | 监听地址 |
| `port` | `8000` | 监听端口 |
| `connect_timeout` | `5.0` | 连接超时（秒） |
| `read_timeout` | `30.0` | 读取超时（秒），流式时自动设为 None |
| `write_timeout` | `5.0` | 写入超时（秒） |
| `pool_timeout` | `5.0` | 连接池超时（秒） |
| `retry_attempts` | `0` | 重试次数（仅 502/503/504） |
| `retry_backoff` | `1.0` | 重试退避倍数 |
| `cors_origins` | `[]` | CORS 允许的来源列表 |
| `model_map` | `{}` | Anthropic→OpenAI 模型名映射 |
| `default_model` | `""` | 未知模型的默认映射 |

示例 `config.toml`：

```toml
backend_base = "https://api.openai.com/v1"
api_key = "sk-your-key-here"
host = "127.0.0.1"
port = 8000

connect_timeout = 5.0
read_timeout = 30.0
write_timeout = 5.0
pool_timeout = 5.0

retry_attempts = 2
retry_backoff = 1.0

cors_origins = ["*"]

[model_map]
"claude-opus-4-7" = "gpt-5-4"
"claude-sonnet-4-6" = "gpt-5-3-codex"
```

## 系统服务

```bash
# 安装（从 config.toml 读取配置）
claudify install-service

# 卸载
claudify uninstall-service
```

- **Linux（systemd）：** 安装用户级 systemd unit + 环境变量，自动 `systemctl daemon-reload`。
- **macOS（launchd）：** 安装 LaunchAgent plist，自动 `launchctl load`。

## 项目结构

```
src/claudify/
├── app.py          # FastAPI 应用工厂 + 中间件
├── routes.py       # 路由定义
├── conversion.py   # Anthropic↔OpenAI 协议转换
├── errors.py       # 错误映射 + 脱敏
├── metrics.py      # Prometheus 指标
├── retry.py        # 重试逻辑
├── sse.py          # SSE 解析器 + 工具函数
├── settings.py     # 配置管理
├── cli.py          # Typer CLI
└── service/
    ├── systemd.py  # Linux systemd 安装器
    └── launchd.py  # macOS launchd 安装器
```

## 已知不支持

- Extended thinking / reasoning 块（请求能过，但思考内容被丢弃）
- Prompt caching（`cache_control` 被静默剥离）
- 图片 URL 下载（仅支持 base64 和直接 URL 透传）
- 并行工具调用（Anthropic 的 `tool_choice: {"type": "any"}` 映射为 OpenAI 的 `"required"`）

## 许可证

MIT
