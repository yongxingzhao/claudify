"""Tests for settings loading: env vars, TOML file, XDG paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from claudify.settings import Settings, _load_toml, default_config_path


def test_default_config_path_uses_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    p = default_config_path()
    assert p == tmp_path / "claudify" / "config.toml"


def test_default_config_path_falls_back_to_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    p = default_config_path()
    assert p == tmp_path / ".config" / "claudify" / "config.toml"


def test_load_toml_missing_returns_empty(tmp_path: Path) -> None:
    assert _load_toml(tmp_path / "nope.toml") == {}


def test_load_toml_parses_file(tmp_path: Path) -> None:
    f = tmp_path / "c.toml"
    f.write_text(
        'backend_base = "http://example/v1"\nport = 4321\n[model_map]\n"a" = "b"\n',
        encoding="utf-8",
    )
    data = _load_toml(f)
    assert data["backend_base"] == "http://example/v1"
    assert data["port"] == 4321
    assert data["model_map"] == {"a": "b"}


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    # Strip any CLAUDIFY_* env that might leak from the host.
    for k in list(__import__("os").environ):
        if k.startswith("CLAUDIFY_"):
            monkeypatch.delenv(k, raising=False)
    s = Settings()
    assert s.backend_base == "http://127.0.0.1:8000/v1"
    assert s.port == 4000
    assert s.host == "127.0.0.1"
    assert s.api_key == ""
    assert s.model_map == {}
    assert s.default_model == ""


def test_settings_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDIFY_BACKEND_BASE", "http://upstream:9000/v1")
    monkeypatch.setenv("CLAUDIFY_PORT", "5555")
    monkeypatch.setenv("CLAUDIFY_API_KEY", "sk-test")
    s = Settings()
    assert s.backend_base == "http://upstream:9000/v1"
    assert s.port == 5555
    assert s.api_key == "sk-test"


def test_settings_load_uses_default_path_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Point XDG to a tmp dir that has no config — Settings.load() must still succeed.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for k in list(__import__("os").environ):
        if k.startswith("CLAUDIFY_"):
            monkeypatch.delenv(k, raising=False)
    s = Settings.load()
    assert s.port == 4000  # default


def test_settings_load_reads_toml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg_dir = tmp_path / "claudify"
    cfg_dir.mkdir()
    cfg = cfg_dir / "config.toml"
    cfg.write_text(
        'backend_base = "http://from-toml/v1"\n'
        'api_key = "from-toml"\n'
        'port = 6001\n'
        '[model_map]\n'
        '"claude-opus-4-7" = "hermes-agent"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for k in list(__import__("os").environ):
        if k.startswith("CLAUDIFY_"):
            monkeypatch.delenv(k, raising=False)
    s = Settings.load()
    assert s.backend_base == "http://from-toml/v1"
    assert s.api_key == "from-toml"
    assert s.port == 6001
    assert s.model_map == {"claude-opus-4-7": "hermes-agent"}


def test_settings_load_explicit_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "explicit.toml"
    cfg.write_text('backend_base = "http://explicit/v1"\n', encoding="utf-8")
    for k in list(__import__("os").environ):
        if k.startswith("CLAUDIFY_"):
            monkeypatch.delenv(k, raising=False)
    s = Settings.load(config_path=cfg)
    assert s.backend_base == "http://explicit/v1"
