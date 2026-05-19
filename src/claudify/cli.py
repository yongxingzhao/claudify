"""Typer CLI entry point for claudify."""

from __future__ import annotations

import os
import platform
from pathlib import Path

import typer
import uvicorn

from claudify.settings import Settings, default_config_path

app = typer.Typer(help="Claudify: Anthropic-to-OpenAI translation proxy")


@app.command()
def version() -> None:
    from claudify import __version__
    typer.echo(__version__)


@app.command()
def config_path() -> None:
    typer.echo(default_config_path())


@app.command("init-config")
def init_config() -> None:
    cfg = default_config_path()
    if cfg.exists():
        typer.echo(f"{cfg} already exists", err=True)
        raise typer.Exit(1)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    s = Settings()
    cfg.write_text(
        f'# Claudify configuration\n'
        f'# See https://github.com/yongxingzhao/claudify for docs\n\n'
        f'backend_base = "{s.backend_base}"\n'
        f'api_key = "{s.api_key}"\n'
        f'host = "{s.host}"\n'
        f'port = {s.port}\n'
        f'log_level = "{s.log_level}"\n\n'
        f'# Timeout settings (seconds)\n'
        f'# request_timeout = 300.0\n'
        f'# connect_timeout = 10.0\n'
        f'# read_timeout = 300.0\n\n'
        f'# Retry settings\n'
        f'# retry_attempts = 0\n'
        f'# retry_backoff = 0.5\n\n'
        f'# Model mapping: Anthropic name -> OpenAI name\n'
        f'# [model_map]\n'
        f'# "claude-opus-4-7" = "gpt-4"\n\n'
        f'# CORS origins (for browser access)\n'
        f'# cors_origins = ["http://localhost:3000"]\n',
        encoding="utf-8",
    )
    os.chmod(cfg, 0o600)
    typer.echo(f"wrote {cfg}")


@app.command("install-service")
def install_service(
    backend: str = typer.Option("", help="Override backend_base from config"),
    api_key: str = typer.Option("", help="Override api_key from config"),
    host: str = typer.Option("", help="Override host from config"),
    port: int = typer.Option(0, help="Override port from config"),
) -> None:
    s = Settings.load()
    be = backend or s.backend_base
    ak = api_key or s.api_key
    h = host or s.host
    p = port or s.port

    cfg = default_config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    if not cfg.exists():
        cfg.write_text(
            f'backend_base = "{be}"\napi_key = "{ak}"\nhost = "{h}"\nport = {p}\n',
            encoding="utf-8",
        )
        os.chmod(cfg, 0o600)
        typer.echo(f"wrote {cfg}")

    if platform.system() == "Darwin":
        from claudify.service.launchd import install as ld_install
        from claudify.service.launchd import load_agent
        ld_install(h, p, be, ak)
        load_agent()
        typer.echo("LaunchAgent installed and loaded.")
    else:
        from claudify.service.systemd import install as sd_install
        sd_install(h, p, be, ak)
        typer.echo("systemd unit installed. Run: systemctl --user enable --now claudify")


@app.command("uninstall-service")
def uninstall_service() -> None:
    if platform.system() == "Darwin":
        from claudify.service.launchd import uninstall as ld_uninstall
        ld_uninstall()
        typer.echo("LaunchAgent uninstalled.")
    else:
        from claudify.service.systemd import uninstall as sd_uninstall
        sd_uninstall()
        typer.echo("systemd unit uninstalled.")


@app.command()
def run(
    host: str = typer.Option("", help="Override host from config"),
    port: int = typer.Option(0, help="Override port from config"),
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Set log level to DEBUG"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Set log level to WARNING"),
) -> None:
    s = Settings.load(config_path=config)
    h = host or s.host
    p = port or s.port
    if verbose:
        s.log_level = "DEBUG"
    elif quiet:
        s.log_level = "WARNING"

    import logging
    logging.basicConfig(level=getattr(logging, s.log_level.upper(), logging.INFO))

    # Print model map on startup
    if s.model_map:
        typer.echo(f"Model map: {s.model_map}")
    if s.default_model:
        typer.echo(f"Default model: {s.default_model}")

    typer.echo(f"forwarding to {s.backend_base}")
    uvicorn.run(
        "claudify.app:create_app",
        host=h,
        port=p,
        factory=True,
        log_level=s.log_level.lower(),
        timeout_graceful_shutdown=5,
    )
