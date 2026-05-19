"""Linux systemd user service installer."""

from __future__ import annotations

from pathlib import Path


def _unit_path() -> Path:
    base = Path.home() / ".config" / "systemd" / "user"
    base.mkdir(parents=True, exist_ok=True)
    return base / "claudify.service"


def install(host: str, port: int, backend_base: str, api_key: str) -> None:
    env_lines = []
    if backend_base:
        env_lines.append(f"CLAUDIFY_BACKEND_BASE={backend_base}")
    if api_key:
        env_lines.append("CLAUDIFY_API_KEY=" + api_key)
    env_section = ""
    if env_lines:
        env_section = "Environment=" + " ".join(env_lines) + "\n"

    unit = (
        "[Unit]\n"
        "Description=Claudify proxy\n"
        "After=network.target\n\n"
        "[Service]\n"
        f"{env_section}"
        "ExecStart=%h/.local/bin/claudify run\n\n"
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
