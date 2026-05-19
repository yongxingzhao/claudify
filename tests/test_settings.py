"""Tests for Settings configuration."""

from __future__ import annotations

from pathlib import Path

from claudify.settings import Settings, _load_toml


def test_defaults():
    s = Settings()
    assert s.host == "127.0.0.1"
    assert s.port == 4000
    assert s.retry_attempts == 0
    assert s.max_body_size == 10 * 1024 * 1024


def test_load_from_toml(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('host = "0.0.0.0"\nport = 8080\napi_key = "sk-test"\n')
    s = Settings.load(cfg)
    assert s.host == "0.0.0.0"
    assert s.port == 8080
    assert s.api_key == "sk-test"


def test_load_missing_toml():
    s = Settings.load(Path("/nonexistent/config.toml"))
    assert s.host == "127.0.0.1"


def test_env_override(monkeypatch):
    monkeypatch.setenv("CLAUDIFY_HOST", "0.0.0.0")
    monkeypatch.setenv("CLAUDIFY_PORT", "9999")
    s = Settings()
    assert s.host == "0.0.0.0"
    assert s.port == 9999


def test_httpx_timeout_default():
    s = Settings()
    t = s.httpx_timeout()
    assert t.connect is not None
    assert t.read is not None


def test_httpx_timeout_streaming():
    s = Settings()
    t = s.httpx_timeout(streaming=True)
    assert t.read is None  # No read timeout for streaming


def test_custom_timeouts():
    s = Settings(connect_timeout=5.0, read_timeout=60.0, write_timeout=10.0, pool_timeout=5.0)
    t = s.httpx_timeout()
    assert t.connect == 5.0
    assert t.read == 60.0


def test_cors_origins_default():
    s = Settings()
    assert s.cors_origins == []


def test_upstream_health_path_default():
    s = Settings()
    assert s.upstream_health_path == ""


def test_load_toml_empty(tmp_path):
    p = tmp_path / "empty.toml"
    p.write_text("")
    assert _load_toml(p) == {}


def test_model_map_from_toml(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[model_map]\n"claude-opus-4-7" = "hermes-agent"\n'
    )
    s = Settings.load(cfg)
    assert s.model_map == {"claude-opus-4-7": "hermes-agent"}
