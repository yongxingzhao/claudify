"""macOS LaunchAgent installer."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

LABEL = "com.claudify.claudify"
PLIST_NAME = f"{LABEL}.plist"

PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{label}</string>
    <key>ProgramArguments</key>
    <array><string>{exec_path}</string><string>run</string></array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>{log_dir}/claudify.out.log</string>
    <key>StandardErrorPath</key><string>{log_dir}/claudify.err.log</string>
</dict>
</plist>
"""


def _agent_dir() -> Path:
    p = Path.home() / "Library" / "LaunchAgents"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _log_dir() -> Path:
    p = Path.home() / "Library" / "Logs" / "claudify"
    p.mkdir(parents=True, exist_ok=True)
    return p


def install() -> None:
    exec_path = shutil.which("claudify") or "claudify"
    target = _agent_dir() / PLIST_NAME
    target.write_text(PLIST_TEMPLATE.format(label=LABEL, exec_path=exec_path, log_dir=str(_log_dir())), encoding="utf-8")
    subprocess.run(["launchctl", "unload", str(target)], check=False)
    subprocess.run(["launchctl", "load", str(target)], check=True)
    print(f"installed {target}")


def uninstall() -> None:
    target = _agent_dir() / PLIST_NAME
    subprocess.run(["launchctl", "unload", str(target)], check=False)
    if target.exists():
        target.unlink()
    print(f"removed {target}")
