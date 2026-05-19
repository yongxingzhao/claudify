"""Typer CLI entry point for claudify."""

from __future__ import annotations

import os
import platform
from pathlib import Path

import typer
import uvicorn

from claudify.settings import Settings, default_config_path

app = typer.Typer(help="Anthropic Messages API to OpenAI Chat Completions translation proxy")


@app.command()
def run(
    host: str = typer.Option("", help="Override host from config"),
    port: int = typer.Option(0, help="Override port from config"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Set log level to DEBUG"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Set log level to ERROR"),
) -> None:
    s = Settings.load()
    h = host or s.host
    p = port or s.port
    log_level = "DEBUG" if verbose else "ERROR" if quiet else s.log_level

    if s.model_map:
        typer.echo(f"Model map: {s.model_map}")
    if s.default_model:
        typer.echo(f"Default model: {s.default_model}")

    uvicorn.run(
        "claudify.app:create_app_from_settings",
        host=h,
        port=p,
        log_level=log_level.lower(),
        factory=True,
        timeout_graceful_shutdown=5,
    )


@app.command()
def version() -> None:
    from claudify import __version__
    typer.echo(__version__)


@app.command()
def config_path() -> None:
    typer.echo(default_config_path())


@app.command()
def init_config(
    backend: str = typer.Option("", help="Override backend_base from defaults"),
    api_key: str = typer.Option("", help="Override api_key from defaults"),
    host: str = typer.Option("", help="Override host from defaults"),
    port: int = typer.Option(0, help="Override port from defaults"),
) -> None:
    defaults = Settings()
    be = backend or defaults.backend_base
    ak = api_key or defaults.api_key
    h = host or defaults.host
    p = port or defaults.port

    cfg = default_config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    if cfg.exists():
        typer.echo(f"config already exists at {cfg}", err=True)
        raise typer.Exit(code=1)
    cfg.write_text(
        f'backend_base = "{be}"\n'
        f'api_key = "{ak}"\n'
        f'host = "{h}"\n'
        f'port = {p}\n'
        f'\n'
        f'# request_timeout = 300.0\n'
        f'# connect_timeout = 5.0\n'
        f'# read_timeout = 300.0\n'
        f'# write_timeout = 30.0\n'
        f'# pool_timeout = 10.0\n'
        f'\n'
        f'# retry_attempts = 0\n'
        f'# retry_backoff = 0.5\n'
        f'\n'
        f'# max_body_size = 10485760\n'
        f'\n'
        f'# [model_map]\n'
        f'# "claude-opus-4-7" = "hermes-agent"\n'
        f'\n'
        f'# default_model = ""\n'
        f'\n'
        f'# cors_origins = []\n'
        f'\n'
        f'# upstream_health_path = ""\n',
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

    system = platform.system()
    if system == "Linux":
        from claudify.service.systemd import install
        install(h, p)
    elif system == "Darwin":
        from claudify.service.launchd import install
        install(h, p)
    else:
        typer.echo(f"unsupported platform: {system}", err=True)
        raise typer.Exit(code=2)


@app.command("uninstall-service")
def uninstall_service() -> None:
    system = platform.system()
    if system == "Linux":
        from claudify.service.systemd import uninstall
        uninstall()
    elif system == "Darwin":
        from claudify.service.launchd import uninstall
        uninstall()
    else:
        typer.echo(f"unsupported platform: {system}", err=True)
        raise typer.Exit(code=2)
