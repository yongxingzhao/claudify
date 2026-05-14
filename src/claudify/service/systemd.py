"""Linux user-level systemd installer."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

UNIT_NAME = "claudify.service"

UNIT_TEMPLATE = """\
[Unit]
Description=claudify — Anthropic → OpenAI proxy
After=network-online.target

[Service]
Type=simple
ExecStart={exec_path} run
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
"""


def _unit_dir() -> Path:
    p = Path.home() / ".config" / "systemd" / "user"
    p.mkdir(parents=True, exist_ok=True)
    return p


def install() -> None:
    exec_path = shutil.which("claudify") or "claudify"
    target = _unit_dir() / UNIT_NAME
    target.write_text(UNIT_TEMPLATE.format(exec_path=exec_path), encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", UNIT_NAME], check=True)
    print(f"installed {target}")


def uninstall() -> None:
    target = _unit_dir() / UNIT_NAME
    subprocess.run(["systemctl", "--user", "disable", "--now", UNIT_NAME], check=False)
    if target.exists():
        target.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    print(f"removed {target}")
