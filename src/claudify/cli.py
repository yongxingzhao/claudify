"""Typer CLI."""

from __future__ import annotations

import os

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
    backend: str = typer.Option("", help="Backend base URL (default from Settings)"),
    api_key: str = typer.Option("", help="API key (default from Settings)"),
    port: int = typer.Option(0, help="Listen port (default from Settings)"),
    host: str = typer.Option("", help="Listen host (default from Settings)"),
) -> None:
    # Derive defaults from Settings so they stay in sync.
    defaults = Settings()
    be = backend or defaults.backend_base
    ak = api_key or defaults.api_key
    h = host or defaults.host
    p = port or defaults.port

    path = default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        typer.echo(f"refused: {path} already exists")
        raise typer.Exit(code=1)
    path.write_text(
        f'backend_base = "{be}"\n'
        f'api_key = "{ak}"\n'
        f'host = "{h}"\n'
        f"port = {p}\n\n"
        f"# [model_map]\n"
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

    uvicorn.run(
        create_app(s),
        host=h,
        port=p,
        log_level=s.log_level.lower(),
        timeout_graceful_shutdown=5,
    )


@app.command("install-service")
def install_service(
    backend: str = typer.Option("", help="Override backend_base from config"),
    api_key: str = typer.Option("", help="Override api_key from config"),
    host: str = typer.Option("", help="Override host from config"),
    port: int = typer.Option(0, help="Override port from config"),
) -> None:
    import platform

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
