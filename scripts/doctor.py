#!/usr/bin/env python3
"""Say what boomerang needs, what this machine has, and the command to fix it.

Standard library only, so it runs before anything is installed, and nothing
here launches a browser or opens a socket. A machine with no browser is not
broken, it stops at ``packet.html``, so the browser line never sets the exit
code. Email comes from the host, so it is a reminder and not a check.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parent.parent / "requirements.txt"
CHANNELS = ("chrome", "msedge", "chromium")

FIX_PYTHON = "install Python 3.11 or newer, then run this check again"
FIX_PACKAGES = "pip install -r requirements.txt (or: uv sync)"
FIX_BROWSER = (
    "install Google Chrome or Microsoft Edge, or run: python -m playwright install chromium"
)
EMAIL_REMINDER = """email       host's job  the host's email tool, or the bundled Gmail fallback
                        pip install google-api-python-client google-auth-oauthlib"""

# Where a browser sits already. A path from another platform simply never exists.
APPS = {
    "chrome": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "msedge": "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
}
EXES = {
    "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "msedge": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
}
NAMES = {"chrome": ("google-chrome", "google-chrome-stable"), "msedge": ("microsoft-edge",)}


def module_present(name: str) -> bool:
    """True when the import would succeed, without running the module."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def playwright_cache() -> Path:
    """Where ``playwright install`` leaves its browsers on this platform."""
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if override:
        return Path(override)
    tails = {"Darwin": ("Library", "Caches"), "Windows": ("AppData", "Local")}
    return Path.home().joinpath(*tails.get(platform.system(), (".cache",)), "ms-playwright")


def find_browser() -> str | None:
    """The first channel this machine has, found by file rather than by launch."""
    # The renderer's own order when it imports, so a run pinned with
    # BOOMERANG_BROWSER is checked against the browser it will actually use.
    try:
        from render_pdf import wanted_channels

        channels = tuple(wanted_channels())
    except Exception:
        channels = CHANNELS
    for channel in channels:
        if channel == "chromium":
            cache = playwright_cache()
            found = cache.is_dir() and any(cache.glob("chromium*"))
        else:
            found = Path(APPS[channel]).exists() or Path(EXES[channel]).exists()
            found = found or any(shutil.which(name) for name in NAMES[channel])
        if found:
            return channel
    return None


def checks() -> list[tuple[str, bool, str, str]]:
    """One row per check: label, present, what it is for, how to fix it."""
    version = ".".join(str(part) for part in sys.version_info[:3])
    channel = find_browser()
    return [
        ("python", sys.version_info >= (3, 11), f"{version}, 3.11 or newer", FIX_PYTHON),
        ("playwright", module_present("playwright"), "drives the PDF render", FIX_PACKAGES),
        ("pypdf", module_present("pypdf"), "reads folios, splices pages", FIX_PACKAGES),
        ("browser", channel is not None, channel or "none, the packet stays HTML", FIX_BROWSER),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check what boomerang needs on this machine.")
    parser.add_argument("--install", action="store_true", help="pip install -r requirements.txt")
    parser.add_argument("--install-browser", action="store_true", help="also fetch a Chromium")
    args = parser.parse_args(argv)
    if args.install or args.install_browser:
        runs = [[sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS)]]
        if args.install_browser:
            runs.append([sys.executable, "-m", "playwright", "install", "chromium"])
        for run in runs:
            print("running: " + " ".join(run))
            if subprocess.call(run) != 0:
                return 1
    ready = True
    for label, present, detail, fix in checks():
        print(f"{label:<11} {'ok' if present else 'missing':<11} {detail}")
        if not present:
            print(f"{'':<11} fix: {fix}")
            ready = ready and label == "browser"
    print(EMAIL_REMINDER)
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
