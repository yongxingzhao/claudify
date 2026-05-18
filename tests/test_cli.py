"""Tests for the CLI commands via typer.testing.CliRunner."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from claudify.cli import app
from claudify import __version__


runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_config_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for k in list(os.environ):
        if k.startswith("CLAUDIFY_"):
            monkeypatch.delenv(k, raising=False)
    result = runner.invoke(app, ["config-path"])
    assert result.exit_code == 0
    assert str(tmp_path / "claudify" / "config.toml") in result.stdout


def test_init_config_writes_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for k in list(os.environ):
        if k.startswith("CLAUDIFY_"):
            monkeypatch.delenv(k, raising=False)
    result = runner.invoke(app, ["init-config"])
    assert result.exit_code == 0
    cfg = tmp_path / "claudify" / "config.toml"
    assert cfg.exists()
    text = cfg.read_text()
    assert "backend_base" in text
    assert "api_key" in text
    assert "port" in text
    assert "host" in text


def test_init_config_refuses_overwrite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for k in list(os.environ):
        if k.startswith("CLAUDIFY_"):
            monkeypatch.delenv(k, raising=False)
    result = runner.invoke(app, ["init-config"])
    assert result.exit_code == 0
    # Second run should fail
    result = runner.invoke(app, ["init-config"])
    assert result.exit_code == 1
    assert "already exists" in result.stdout


def test_init_config_uses_settings_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for k in list(os.environ):
        if k.startswith("CLAUDIFY_"):
            monkeypatch.delenv(k, raising=False)
    result = runner.invoke(app, ["init-config"])
    assert result.exit_code == 0
    text = (tmp_path / "claudify" / "config.toml").read_text()
    assert "port = 4000" in text


def test_init_config_cli_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for k in list(os.environ):
        if k.startswith("CLAUDIFY_"):
            monkeypatch.delenv(k, raising=False)
    result = runner.invoke(
        app, ["init-config", "--backend", "http://custom:9999/v1", "--api-key", "sk-override"]
    )
    assert result.exit_code == 0
    text = (tmp_path / "claudify" / "config.toml").read_text()
    assert "http://custom:9999/v1" in text
    assert "sk-override" in text


def test_install_service_reads_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for k in list(os.environ):
        if k.startswith("CLAUDIFY_"):
            monkeypatch.delenv(k, raising=False)
    # Write config file first so install-service can read it
    cfg_dir = tmp_path / "claudify"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text(
        'backend_base = "http://from-config/v1"\napi_key = "sk-config"\nport = 5000\nhost = "0.0.0.0"\n',
        encoding="utf-8",
    )
    # Mock platform.system() inside the function and the service installers
    with patch("platform.system", return_value="Linux"):
        with patch("claudify.service.systemd.install") as mock_install:
            result = runner.invoke(app, ["install-service"])
            assert result.exit_code == 0
            mock_install.assert_called_once()


def test_uninstall_service_unsupported_platform():
    with patch("platform.system", return_value="Windows"):
        result = runner.invoke(app, ["uninstall-service"])
        assert result.exit_code == 2
        assert "unsupported" in result.stdout.lower()
