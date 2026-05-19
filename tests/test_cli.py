"""Tests for CLI commands."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from claudify.cli import app

runner = CliRunner()


def test_version():
    r = runner.invoke(app, ["version"])
    assert r.exit_code == 0
    assert "0.1.0" in r.stdout


def test_config_path():
    r = runner.invoke(app, ["config-path"])
    assert r.exit_code == 0
    assert ".config" in r.stdout


def test_init_config(tmp_path):
    cfg = tmp_path / "claudify" / "config.toml"
    with patch("claudify.cli.default_config_path", return_value=cfg):
        r = runner.invoke(app, ["init-config"])
    assert r.exit_code == 0
    assert "wrote" in r.stdout
    assert cfg.exists()
    assert os.chmod  # just check we got here


def test_init_config_already_exists(tmp_path):
    cfg = tmp_path / "claudify" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('host = "x"')
    with patch("claudify.cli.default_config_path", return_value=cfg):
        r = runner.invoke(app, ["init-config"])
    assert r.exit_code == 1
    assert "already exists" in r.output


def test_run_help():
    r = runner.invoke(app, ["run", "--help"])
    assert r.exit_code == 0
    assert "--verbose" in r.stdout
    assert "--quiet" in r.stdout
