"""Linux systemd user service installer."""

from __future__ import annotations

from pathlib import Path


def _unit_path() -> Path:
    base = Path.home() / ".config" / "systemd" / "user"
    base.mkdir(parents=True, exist_ok=True)
    return base / "claudify.service"


def install(host: str, port: int) -> None:
    unit = _unit_path()
    content = (
        "[Unit]\n"
        "Description=Claudify proxy\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        "ExecStart=%h/.local/bin/claudify run\n"
        "Environment=CLAUDIFY_API_KEY=${CLAUDIFY_API_KEY}\n"
        "Restart=on-failure\n"
        "RestartSec=3\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    unit.write_text(content, encoding="utf-8")
    print(f"wrote {unit}")
    print("run: systemctl --user daemon-reload && systemctl --user enable --now claudify")


def uninstall() -> None:
    unit = _unit_path()
    if unit.exists():
        unit.unlink()
        print(f"removed {unit}")
    print("run: systemctl --user daemon-reload")
