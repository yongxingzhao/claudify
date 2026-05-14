# Claudify Improvement Plan

This document tracks the 21-item improvement plan derived from a code review on 2026-05-15.

Goal mix: **stability + observability** (for self-hosted use) and **protocol coverage + docs + test matrix** (for public GitHub users).

Items are grouped by PR batch. Each batch lands as a single feature branch.

---

## Batch 1 — Quick wins (chore/quick-wins)

| # | Item | Status |
|---|------|--------|
| 1 | Run `ruff format` across all files | pending |
| 2 | Commit `uv.lock` for reproducible installs | pending |
| 3 | Raise `settings.py` coverage above 90% (test toml load + fallbacks) | pending |

Acceptance: `ruff format --check` passes, `uv.lock` tracked, `coverage report` shows settings ≥ 90%.

---

## Batch 2 — Stability & observability (feat/observability)

| # | Item | Status |
|---|------|--------|
| 4 | Per-request structured logging with `request_id` (in / out / status / latency / upstream_status / tokens) | pending |
| 5 | `/metrics` Prometheus-text endpoint (request count, latency histogram, upstream errors) | pending |
| 6 | Bounded retry+backoff for upstream 502/503/504 (configurable, off by default) | pending |
| 8 | On mid-stream upstream failure, emit synthetic `message_delta` + `message_stop` so clients exit cleanly | pending |
| 9 | Split `request_timeout` into `connect_timeout` / `read_timeout` / `write_timeout` | pending |

Acceptance: new tests cover synthetic stream-stop; metrics endpoint scrapeable; logs include request_id.

---

## Batch 3 — Protocol coverage & docs (feat/protocol-coverage)

| # | Item | Status |
|---|------|--------|
| 10 | Explicitly accept and ignore `cache_control` blocks (document as no-op) | pending |
| 12 | Document `anthropic-beta` / `service_tier` headers as ignored | pending |
| 13 | Review `thinking` block handling; map to OpenAI reasoning where possible | pending |
| 14 | Map upstream HTTP status → Anthropic error types (401→authentication_error, 429→rate_limit_error, 400→invalid_request_error, 5xx→api_error) | pending |
| 19 | Add `docs/protocol-mapping.md` (Anthropic ↔ OpenAI field table) | pending |
| 20 | Add "Known unsupported" section to README (cache_control, citations, PDFs, computer-use, etc.) | pending |

Acceptance: error mapping table tested; protocol-mapping doc renders on GitHub.

---

## Batch 4 — Test matrix, CLI tests, CI (test/matrix-and-ci)

| # | Item | Status |
|---|------|--------|
| 7 | Add `tiktoken` optional extra for accurate `count_tokens`; document char/4 fallback | pending |
| 11 | Optional `top_k` passthrough via `extra_body` (configurable per-backend) | pending |
| 15 | End-to-end SSE streaming tests (mocked httpx upstream, mid-stream close) | pending |
| 16 | Error passthrough tests (401/429/400/500 mapping) | pending |
| 17 | CLI tests (typer.testing.CliRunner: version, config-path, init-config) | pending |
| 18 | GitHub Actions CI (matrix py3.10–3.13, ruff + pytest + coverage upload) | pending |
| 21 | Add `CHANGELOG.md` (Keep-a-Changelog format) | pending |

Acceptance: CI green on all supported Python versions; coverage ≥ 90% overall.

---

## Workflow

- One PR per batch.
- Each PR: tests pass locally + ruff format + ruff check.
- PRs merged into `main` after review.
