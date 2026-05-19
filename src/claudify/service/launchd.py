"""macOS LaunchAgent installer."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.claudify.plist"


def install(host: str, port: int) -> None:
    plist = _plist_path()
    content = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" "
        "\"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n"
        "<plist version=\"1.0\">\n"
        "<dict>\n"
        "  <key>Label</key><string>com.claudify</string>\n"
        "  <key>ProgramArguments</key>\n"
        "  <array>\n"
        "    <string>%h/.local/bin/claudify</string>\n"
        "    <string>run</string>\n"
        "  </array>\n"
        "  <key>EnvironmentVariables</key>\n"
        "  <dict>\n"
        "    <key>CLAUDIFY_API_KEY</key>\n"
        "    <string>${CLAUDIFY_API_KEY}</string>\n"
        "  </dict>\n"
        "  <key>RunAtLoad</key><true/>\n"
        "  <key>KeepAlive</key><true/>\n"
        "</dict>\n"
        "</plist>\n"
    )
    plist.write_text(content, encoding="utf-8")
    print(f"wrote {plist}")
    print("run: launchctl load " + str(plist))


def uninstall() -> None:
    plist = _plist_path()
    if plist.exists():
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        plist.unlink()
        print(f"removed {plist}")
