# Contributing to claudify

Thanks for your interest in contributing! Here's how to get started.

## Development Setup

```bash
# Clone the repository
git clone https://github.com/yongxingzhao/claudify.git
cd claudify

# Install with dev dependencies
uv sync --dev

# Run tests
uv run pytest

# Run linter
uv run ruff check .
```

## Architecture

```
src/claudify/
├── app.py          # FastAPI app factory + middleware + lifespan
├── routes.py       # Route handlers
├── conversion.py   # Anthropic↔OpenAI protocol conversion
├── errors.py       # Error mapping + sanitization
├── metrics.py      # Prometheus metrics
├── retry.py        # Retry logic with backoff
├── sse.py          # SSE parser + helpers
├── settings.py     # Configuration (pydantic-settings)
├── cli.py          # Typer CLI
└── service/
    ├── systemd.py  # Linux systemd installer
    └── launchd.py  # macOS launchd installer
```

## Making Changes

1. Create a feature branch: `git checkout -b feat/my-feature`
2. Make your changes
3. Add tests for new functionality
4. Ensure all tests pass: `uv run pytest`
5. Ensure linter is clean: `uv run ruff check .`
6. Commit with a clear message
7. Open a Pull Request

## Code Style

- Follow PEP 8 (enforced by ruff)
- No unnecessary comments — code should be self-documenting
- Keep functions focused and small
- Prefer explicit over implicit

## Testing

- Tests use `pytest` + `pytest-asyncio`
- HTTP mocking via `httpx.MockTransport` and `ASGITransport`
- Run the full suite: `uv run pytest`
- Run a single test: `uv run pytest tests/test_conversion.py -k test_name`

## Reporting Issues

- Use GitHub Issues
- Include steps to reproduce
- Include relevant logs (redact any API keys!)
