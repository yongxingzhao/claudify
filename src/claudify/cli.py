"""Typer CLI."""
from __future__ import annotations

import os
from pathlib import Path

import typer
import uvicorn

from . import __version__
from .settings import Settings, default_config_path

app = typer.Typer(help="Anthropic → OpenAI protocol translation proxy.")


@app.command()
def version() -> None:
    typer.echo(f"claudify {__version__}")


@app.command("config-path")
def config_path() -> None:
    typer.echo(str(default_config_path()))


@app.command("init-config")
def init_config(
    backend: str = typer.Option("http://127.0.0.1:8000/v1"),
    api_key: str = typer.Option(""),
    port: int = typer.Option(4000),
    host: str = typer.Option("127.0.0.1"),
) -> None:
    path = default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        typer.echo(f"refused: {path} already exists")
        raise typer.Exit(code=1)
    path.write_text(
        f'backend_base = "{backend}"\n'
        f'api_key = "{api_key}"\n'
        f'host = "{host}"\n'
        f'port = {port}\n\n'
        f'# [model_map]\n'
        f'# "claude-opus-4-7" = "hermes-agent"\n\n'
        f'# default_model = "hermes-agent"\n',
        encoding="utf-8",
    )
    os.chmod(path, 0o600)
    typer.echo(f"wrote {path}")


@app.command()
def run(
    host: str = typer.Option(""),
    port: int = typer.Option(0),
) -> None:
    s = Settings.load()
    h = host or s.host
    p = port or s.port
    typer.echo(f"claudify v{__version__} → http://{h}:{p}, forwarding to {s.backend_base}")
    from .app import create_app
    uvicorn.run(create_app(s), host=h, port=p, log_level=s.log_level.lower())


@app.command("install-service")
def install_service(
    backend: str = typer.Option(...),
    api_key: str = typer.Option(...),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(4000),
) -> None:
    import platform
    cfg = default_config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    if not cfg.exists():
        cfg.write_text(
            f'backend_base = "{backend}"\napi_key = "{api_key}"\nhost = "{host}"\nport = {port}\n',
            encoding="utf-8",
        )
        os.chmod(cfg, 0o600)
        typer.echo(f"wrote {cfg}")
    sysname = platform.system()
    if sysname == "Linux":
        from .service.systemd import install as I
        I()
    elif sysname == "Darwin":
        from .service.launchd import install as I
        I()
    else:
        typer.echo(f"unsupported platform: {sysname}")
        raise typer.Exit(code=2)


@app.command("uninstall-service")
def uninstall_service() -> None:
    import platform
    sysname = platform.system()
    if sysname == "Linux":
        from .service.systemd import uninstall as U
        U()
    elif sysname == "Darwin":
        from .service.launchd import uninstall as U
        U()
    else:
        typer.echo(f"unsupported platform: {sysname}")
        raise typer.Exit(code=2)
