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
# The same name render_pdf reads, so a pin holds whether or not it imports.
BROWSER_ENV = "BOOMERANG_BROWSER"

FIX_PYTHON = "install Python 3.11 or newer, then run this check again"
FIX_PACKAGES = "pip install -r requirements.txt (or: uv sync)"
FIX_BROWSER = (
    "install Google Chrome or Microsoft Edge, or run: python -m playwright install chromium"
)
EMAIL_REMINDER = """email       host's job  the host's email tool, or the bundled Gmail fallback
                        pip install google-api-python-client google-auth-oauthlib"""

# Where an installed browser sits, one entry per channel the renderer drives:
# the paths to try, then the names to look up on PATH. A path from another
# platform simply never exists, so all of them are tried everywhere.
#
# Playwright is asked for nothing here. Its own lookup, ``chromium
# .executable_path``, answers for the browser it downloaded rather than for a
# channel, and reaching it means starting the driver, which is a subprocess on
# a machine that may not have playwright installed at all. There is no
# non-launching channel lookup to call, so this stays a table, kept as short
# as the two channels allow.
BROWSERS = {
    "chrome": (
        (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        ),
        ("google-chrome", "google-chrome-stable"),
    ),
    "msedge": (
        (
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ),
        ("microsoft-edge",),
    ),
}


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


def channels_to_check() -> tuple[str, ...]:
    """The channels to look for, in order, honouring ``BOOMERANG_BROWSER``.

    Where ``render_pdf`` imports, its own order is asked for, so a pinned run
    is checked against the browser it will really use and an unknown pin is
    refused there.

    Where it does not import, which is every machine that has not installed
    playwright yet and so the machine most likely to be running this, the pin
    is read here instead. The variable names a browser whether or not the
    renderer is present to be asked about it, and reporting chrome as the
    browser to a run pinned to msedge would be a wrong answer, not a missing
    one. A value that names no channel is not a pin and falls back to the
    shipped order, which is what ``render_pdf`` refusing it comes to as well.
    """
    try:
        from render_pdf import wanted_channels

        return tuple(wanted_channels())
    except Exception:  # noqa: S110
        # playwright not being importable yet is exactly the case this
        # function exists to run before, on the machine most likely to hit
        # it; any other import failure falls back to the same shipped order,
        # which is what an unrecognised pin already resolves to below.
        pass
    forced = os.environ.get(BROWSER_ENV, "").strip().lower()
    return (forced,) if forced in CHANNELS else CHANNELS


def _installed(channel: str) -> bool:
    """True when this channel is on the machine, found by file, never by launch."""
    if channel == "chromium":
        cache = playwright_cache()
        return cache.is_dir() and any(cache.glob("chromium*"))
    places, names = BROWSERS[channel]
    if any(Path(place).exists() for place in places):
        return True
    return any(shutil.which(name) for name in names)


def find_browser() -> str | None:
    """The first channel this machine has, in the order the renderer wants them."""
    return next((channel for channel in channels_to_check() if _installed(channel)), None)


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
    """Check what boomerang needs on this machine."""
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
            # Every entry in runs is built above from sys.executable and
            # literal flags; nothing here comes from argv or the network.
            if subprocess.call(run) != 0:  # noqa: S603
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
