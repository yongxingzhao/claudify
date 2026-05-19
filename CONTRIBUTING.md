# Contributing to Claudify

## Development Setup

1. Clone the repository
2. Install with dev dependencies: `uv sync --group dev`
3. Run tests: `uv run pytest`
4. Lint: `uv run ruff check src/ tests/`

## Making Changes

- Keep changes focused and small
- Add tests for new functionality
- Ensure all existing tests pass before submitting
- Follow the existing code style (ruff enforces this)

## Commit Messages

Use conventional commit format:
- `feat(scope): description`
- `fix(scope): description`
- `test(scope): description`
- `docs(scope): description`
- `refactor(scope): description`

## Pull Requests

- One logical change per PR
- Include test coverage for new features
- CI must pass (Python 3.10-3.13, ruff lint)

## Architecture

- `src/claudify/settings.py` - Configuration (pydantic-settings)
- `src/claudify/conversion.py` - Pure protocol conversion functions
- `src/claudify/app.py` - FastAPI application and routes
- `src/claudify/cli.py` - Typer CLI entry point
- `src/claudify/service/` - Service installers (systemd, launchd)
