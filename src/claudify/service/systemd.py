"""Linux systemd user service installer."""

from __future__ import annotations

import shutil
from pathlib import Path


def _unit_path() -> Path:
    base = Path.home() / ".config" / "systemd" / "user"
    base.mkdir(parents=True, exist_ok=True)
    return base / "claudify.service"


def install(host: str, port: int, backend_base: str) -> None:
    env_lines = []
    if backend_base:
        env_lines.append(f"CLAUDIFY_BACKEND_BASE={backend_base}")
    env_section = ""
    if env_lines:
        env_section = "Environment=" + " ".join(env_lines) + "\n"

    claudify_bin = shutil.which("claudify") or str(Path.home() / ".local/bin/claudify")
    unit = (
        "[Unit]\n"
        "Description=Claudify proxy\n"
        "After=network.target\n\n"
        "[Service]\n"
        f"{env_section}"
        f"ExecStart={claudify_bin} run\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    _unit_path().write_text(unit)
    print(f"wrote {_unit_path()}")


def uninstall() -> None:
    p = _unit_path()
    if p.exists():
        p.unlink()
        print("removed claudify.service")
    else:
        print("no claudify.service found")
