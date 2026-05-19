# Claudify Improvement Plan

This document tracks the improvement plan derived from code reviews.

---

## Batch 1 — Quick wins (chore/quick-wins)

| # | Item | Status |
|---|------|--------|
| 1 | Run `ruff format` across all files | done |
| 2 | Commit `uv.lock` for reproducible installs | done |
| 3 | Raise `settings.py` coverage above 90% (test toml load + fallbacks) | done (97%) |

---

## Batch 2 — Stability & observability (feat/observability)

| # | Item | Status |
|---|------|--------|
| 4 | Per-request structured logging with `request_id` | done |
| 5 | `/metrics` Prometheus-text endpoint | done |
| 6 | Bounded retry+backoff for upstream 502/503/504 | done |
| 8 | On mid-stream upstream failure, emit synthetic `message_delta` + `message_stop` | done |
| 9 | Split `request_timeout` into `connect_timeout` / `read_timeout` / `write_timeout` | done |

---

## Batch 3 — Protocol coverage & docs (feat/protocol-coverage)

| # | Item | Status |
|---|------|--------|
| 10 | Explicitly accept and ignore `cache_control` blocks | done |
| 12 | Forward `anthropic-beta` / `anthropic-version` headers | done |
| 13 | Handle `thinking` blocks (strip + debug log) | done |
| 14 | Map upstream HTTP status to Anthropic error types | done |
| 19 | Add `docs/protocol-mapping.md` | done |
| 20 | Add "Known unsupported" section to README | done |

---

## Batch 4 — Test matrix, CLI tests, CI (test/matrix-and-ci)

| # | Item | Status |
|---|------|--------|
| 7 | `count_tokens` improved estimation (char/word) | done |
| 11 | `top_k` passthrough | done |
| 15 | End-to-end SSE streaming tests | done |
| 16 | Error passthrough tests (401/429/400/500 mapping) | done |
| 17 | CLI tests (typer.testing.CliRunner) | done |
| 18 | GitHub Actions CI (matrix py3.10-3.13, ruff + pytest) | done |
| 21 | Add `CHANGELOG.md` | done |

---

## Additional improvements (post-plan)

| # | Item | Status |
|---|------|--------|
| | Request body size limit (413) | done |
| | `x-api-key` header forwarding | done |
| | CORS support | done |
| | `--verbose` / `--quiet` / `--config` CLI options | done |
| | Startup model list display | done |
| | `init-config` with model_map examples | done |
| | Upstream health check in `/health` | done |
| | `py.typed` marker | done |
| | `CONTRIBUTING.md` | done |
| | Stream retry support | done |
| | SSE buffer parser (cross-chunk boundaries) | done |
| | Metrics ring buffer (deque) | done |
| | Lifespan context manager (replacing deprecated on_event) | done |
| | Error message sanitization | done |
| | `anthropic-version` default (2023-06-01) | done |
| | `/v1/models` `created` + `owned_by` fields | done |
| | Empty messages validation (400) | done |
| | Retry logging | done |
| | systemd unit Environment fix | done |
| | Code splitting: app.py → routes/errors/metrics/retry/sse | done |
