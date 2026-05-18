"""Configuration loaded from env vars and optional TOML file."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import httpx
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore


def default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "claudify" / "config.toml"


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


class Settings(BaseSettings):
    backend_base: str = Field(default="http://127.0.0.1:8000/v1")
    api_key: str = Field(default="")
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=4000)
    log_level: str = Field(default="INFO")

    request_timeout: float = Field(default=300.0)
    connect_timeout: float | None = Field(default=None)
    read_timeout: float | None = Field(default=None)
    write_timeout: float | None = Field(default=None)
    pool_timeout: float | None = Field(default=None)

    retry_attempts: int = Field(default=0, ge=0, le=10)
    retry_backoff: float = Field(default=0.5, ge=0.0)

    # Max request body size in bytes. Requests exceeding this are rejected with 413.
    max_body_size: int = Field(default=10 * 1024 * 1024, ge=0)  # 10 MB

    model_map: dict[str, str] = Field(default_factory=dict)
    default_model: str = Field(default="")

    model_config = SettingsConfigDict(
        env_prefix="CLAUDIFY_",
        env_file=None,
        extra="ignore",
    )

    def httpx_timeout(self, *, streaming: bool = False) -> httpx.Timeout:
        connect = self.connect_timeout if self.connect_timeout is not None else self.request_timeout
        read = self.read_timeout if self.read_timeout is not None else self.request_timeout
        write = self.write_timeout if self.write_timeout is not None else self.request_timeout
        pool = self.pool_timeout if self.pool_timeout is not None else self.request_timeout
        if streaming:
            read = None
        return httpx.Timeout(connect=connect, read=read, write=write, pool=pool)

    @classmethod
    def load(cls, config_path: Path | None = None) -> Settings:
        path = config_path or default_config_path()
        toml_data = _load_toml(path)
        return cls(**toml_data)
