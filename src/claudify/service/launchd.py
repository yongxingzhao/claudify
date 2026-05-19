"""macOS LaunchAgent installer."""

from __future__ import annotations

import subprocess
from pathlib import Path
from xml.sax.saxutils import escape


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.claudify.plist"


def install(host: str, port: int, backend_base: str, api_key: str) -> None:
    env_vars: dict[str, str] = {}
    if backend_base:
        env_vars["CLAUDIFY_BACKEND_BASE"] = backend_base
    # api_key read from config.toml; not exposed in plist

    env_xml = ""
    for k, v in env_vars.items():
        env_xml += f"    <key>{escape(k)}</key><string>{escape(v)}</string>\n"
    env_section = ""
    if env_xml:
        env_section = "  <key>EnvironmentVariables</key>\n  <dict>\n" + env_xml + "  </dict>\n"

    plist = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        "  <key>Label</key><string>com.claudify</string>\n"
        "  <key>ProgramArguments</key><array>\n"
        f"    <string>{escape(str(Path.home() / '.local/bin/claudify'))}</string>\n"
        "    <string>run</string>\n"
        "  </array>\n"
        f"{env_section}"
        "  <key>RunAtLoad</key><true/>\n"
        "  <key>KeepAlive</key><true/>\n"
        "  <key>StandardOutPath</key>"
        f"<string>{escape(str(Path.home() / '.claudify.log'))}</string>\n"
        "  <key>StandardErrorPath</key>"
        f"<string>{escape(str(Path.home() / '.claudify.err'))}</string>\n"
        "</dict></plist>\n"
    )
    _plist_path().write_text(plist)
    print(f"wrote {_plist_path()}")


def load_agent() -> None:
    subprocess.run(["launchctl", "load", str(_plist_path())], check=False)


def unload_agent() -> None:
    subprocess.run(["launchctl", "unload", str(_plist_path())], check=False)


def uninstall() -> None:
    unload_agent()
    p = _plist_path()
    if p.exists():
        p.unlink()
        print("removed com.claudify.plist")
    else:
        print("no com.claudify.plist found")
