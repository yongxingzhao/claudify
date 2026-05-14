"""Configuration loaded from env vars and optional TOML file."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

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
    model_map: dict[str, str] = Field(default_factory=dict)
    default_model: str = Field(default="")

    model_config = SettingsConfigDict(
        env_prefix="CLAUDIFY_",
        env_file=None,
        extra="ignore",
    )

    @classmethod
    def load(cls, config_path: Path | None = None) -> "Settings":
        path = config_path or default_config_path()
        toml_data = _load_toml(path)
        return cls(**toml_data)
