CLAUDE.md

Guidance for Claude Code working in this repository.

What this is

A FastAPI proxy translating Anthropic Messages API → OpenAI Chat Completions, so Anthropic-protocol clients (Claude Code, etc.) can drive OpenAI-compatible backends.

Common commands

```
uv pip install -e .
uv run pytest
uv run ruff check src tests
claudify run
uv build
python -m claudify
```

Architecture

- src/claudify/settings.py — Settings (pydantic-settings); env CLAUDIFY_* + ~/.config/claudify/config.toml.
- src/claudify/conversion.py — pure functions: anthropic_to_openai(), openai_to_anthropic_response(), stream_openai_to_anthropic(). The "no user message" guard lives here.
- src/claudify/routes.py — FastAPI route handlers: /v1/messages, /v1/models, /health, /metrics, /v1/messages/count_tokens. Inbound auth check, request validation, streaming timeout handling.
- src/claudify/app.py — create_app(settings) factory; ASGI middleware (RequestIdMiddleware), CORS, global exception handler.
- src/claudify/retry.py — post_with_retry(), stream_with_retry() with exponential backoff (capped at 30s). Retries 5xx AND 429. Respects Retry-After header on 429.
- src/claudify/sse.py — SSEParser (incremental chunk parser), sse_event(), sse_ping(), synthetic_stop_events(), STOP_REASON_MAP.
- src/claudify/errors.py — Error type mapping, _sanitize_error_message (regex redaction), passthrough_error (upstream body extraction).
- src/claudify/metrics.py — Prometheus-text Metrics collector with histogram buckets.
- src/claudify/cli.py — Typer entry point; claudify console script. Uses closure to pass Settings to uvicorn (so --verbose/--quiet work).
- src/claudify/service/ — systemd.py + launchd.py for claudify install-service. Uses shutil.which() to find binary.

Pitfalls

- ~/.config/claudify/config.toml is gitignored and chmod 0600 — never commit.
- Unknown model names fall through model_map → default_model → original.
- Response model name always returns the original client-requested name (not the upstream model).
- When inbound_api_key is set, the inbound key is for proxy auth only — never forwarded upstream.
- inbound_api_key comparison MUST use hmac.compare_digest (timing attack prevention).
- Catch httpx.TransportError (base class) for all transport failures — not individual subclasses.
- Non-streaming responses must be explicitly closed with `await r.aclose()` to return connections to the pool.
- Streaming retry failures: must `await exc.response.aread()` before accessing `.content` for error body.
- Streaming requests use httpx_timeout(streaming=True) via build_request(timeout=...), which sets read=None.
- retry_attempts semantics: value = number of retries AFTER the initial attempt. Internally, attempts+1 is passed to retry functions.
- 429 responses are retried alongside 5xx; the Retry-After header is respected.
- Backoff is capped at MAX_BACKOFF=30s to avoid excessively long waits.
- OpenAI's tool_choice "none" maps from Anthropic's {"type": "none"}.
- assistant messages with tool_calls should have content=None (not empty string).
- Anthropic requires content array to be non-empty — fallback to [{"type": "text", "text": ""}].
- top_k is passed through but most OpenAI backends ignore it — a warning is logged.
- SSE parser normalizes CRLF → LF; buffer length tracked incrementally.
