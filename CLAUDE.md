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
