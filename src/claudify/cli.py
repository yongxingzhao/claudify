"""Typer CLI entry point for claudify."""

from __future__ import annotations

import functools
import logging
import os
import platform
import textwrap
from pathlib import Path

import typer
import uvicorn

from claudify.settings import Settings, default_config_path

app = typer.Typer(help="Claudify: Anthropic-to-OpenAI translation proxy", no_args_is_help=True)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_INIT_CONFIG_TEMPLATE = textwrap.dedent("""\
    # Claudify configuration
    # See https://github.com/yongxingzhao/claudify for docs

    backend_base = "http://127.0.0.1:8000/v1"
    # api_key = ""  # Set your API key here
    # inbound_api_key = ""  # Require this key in inbound x-api-key header
    host = "127.0.0.1"
    port = 4000
    log_level = "INFO"
    # log_format = "text"  # "text" or "json" for structured logging

    # Timeout settings (seconds)
    # request_timeout = 300.0
    # connect_timeout = 10.0
    # read_timeout = 300.0
    # write_timeout = 10.0
    # pool_timeout = 5.0

    # Retry settings (number of retries after the initial attempt)
    # retry_attempts = 0
    # retry_backoff = 0.5

    # Maximum request body size (bytes)
    # max_body_size = 10485760

    # Model mapping: Anthropic name -> OpenAI name
    # Uncomment and edit to enable model routing
    # [model_map]
    # "claude-opus-4-7" = "gpt-4"
    # "claude-sonnet-4-6" = "gpt-4o"

    # Default model when no mapping matches
    # default_model = "gpt-4o"

    # CORS origins (for browser access)
    # cors_origins = ["http://localhost:3000"]

    # Upstream health check path (appended to backend_base)
    # upstream_health_path = "healthz"
""")


def _is_darwin() -> bool:
    """Return True when running on macOS (Darwin)."""
    return platform.system() == "Darwin"


def _setup_logging(settings: Settings) -> None:
    """Configure the root logger according to *settings*."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    if settings.log_format == "json":
        formatter = logging.Formatter(
            '{"time":"%(asctime)s","level":"%(name)s","msg":"%(message)s"}'
        )
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        logging.basicConfig(level=level, handlers=[handler])
    else:
        logging.basicConfig(level=level)


def _print_startup_banner(settings: Settings) -> None:
    """Print a one-line summary of the effective runtime configuration."""
    parts: list[str] = [f"forwarding to {settings.backend_base}"]
    if settings.model_map:
        parts.append(f"model map: {settings.model_map}")
    if settings.default_model:
        parts.append(f"default model: {settings.default_model}")
    if settings.retry_attempts:
        parts.append(f"retry: {settings.retry_attempts} attempts, {settings.retry_backoff}s backoff")
    if settings.inbound_api_key:
        parts.append("inbound auth: enabled")
    typer.echo(" | ".join(parts))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

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
    cfg.write_text(_INIT_CONFIG_TEMPLATE, encoding="utf-8")
    os.chmod(cfg, 0o600)
    typer.echo(f"wrote {cfg}")


@app.command("install-service")
def install_service(
    backend: str = typer.Option("", help="Override backend_base from config"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be installed without doing it"),
) -> None:
    s = Settings.load()
    be = backend or s.backend_base

    cfg = default_config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    if not cfg.exists():
        cfg.write_text(
            f'backend_base = "{be}"\n',
            encoding="utf-8",
        )
        os.chmod(cfg, 0o600)
        typer.echo(f"wrote {cfg}")

    if _is_darwin():
        if dry_run:
            typer.echo("Would install LaunchAgent (Darwin/macOS).")
            return
        from claudify.service.launchd import install as ld_install
        from claudify.service.launchd import load_agent
        ld_install(s.host, s.port, be)
        load_agent()
        typer.echo("LaunchAgent installed and loaded.")
    else:
        if dry_run:
            typer.echo("Would install systemd unit (Linux).")
            return
        from claudify.service.systemd import install as sd_install
        sd_install(s.host, s.port, be)
        typer.echo("systemd unit installed. Run: systemctl --user enable --now claudify")


@app.command("uninstall-service")
def uninstall_service() -> None:
    if _is_darwin():
        from claudify.service.launchd import uninstall as ld_uninstall
        ld_uninstall()
        typer.echo("LaunchAgent uninstalled.")
    else:
        from claudify.service.systemd import uninstall as sd_uninstall
        sd_uninstall()
        typer.echo("systemd unit uninstalled.")


@app.command("show-config")
def show_config(
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Display the current effective settings (useful for debugging)."""
    s = Settings.load(config_path=config)
    lines = [
        f"backend_base   = {s.backend_base!r}",
        f"api_key        = {'***' if s.api_key else '(not set)'}",
        f"inbound_api_key= {'***' if s.inbound_api_key else '(not set)'}",
        f"host           = {s.host!r}",
        f"port           = {s.port}",
        f"log_level      = {s.log_level!r}",
        f"log_format     = {s.log_format!r}",
        f"request_timeout= {s.request_timeout}",
        f"connect_timeout= {s.connect_timeout}",
        f"read_timeout   = {s.read_timeout}",
        f"write_timeout  = {s.write_timeout}",
        f"pool_timeout   = {s.pool_timeout}",
        f"retry_attempts = {s.retry_attempts}",
        f"retry_backoff  = {s.retry_backoff}",
        f"max_body_size  = {s.max_body_size}",
        f"pool_limit     = {s.pool_limit}",
        f"model_map      = {s.model_map!r}" if s.model_map else "model_map      = {}",
        f"default_model  = {s.default_model!r}" if s.default_model else "default_model  = (not set)",
        f"cors_origins   = {s.cors_origins!r}",
        f"upstream_health= {s.upstream_health_path!r}" if s.upstream_health_path else "upstream_health= (not set)",
        f"config_file    = {(config or default_config_path())}",
    ]
    typer.echo("\n".join(lines))


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

    _setup_logging(s)
    _print_startup_banner(s)

    # Pass the already-loaded Settings to create_app via closure,
    # so --verbose/--quiet flags and config overrides take effect.
    from claudify.app import create_app
    app_factory = functools.partial(create_app, s)

    uvicorn.run(
        app_factory,
        host=h,
        port=p,
        factory=True,
        log_level=s.log_level.lower(),
        timeout_graceful_shutdown=5,
    )
