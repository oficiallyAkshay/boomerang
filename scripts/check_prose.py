#!/usr/bin/env python3
"""Prose and privacy gate.

Scans tracked text files for two things: the em dash, which the packet style
forbids, and any phrase whose sha256 appears in the hashed denylist. Hits are
reported by file and line, never by content.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

EM_DASH = "\u2014"  # the character itself never appears in this repo
DENYLIST_PATH = Path("tests/pii_denylist.sha256")
SKIP_NAMES = {"uv.lock", "pii_denylist.sha256"}
SKIP_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2"}
TOKEN_RE = re.compile(r"[a-z0-9]+")
BANNED_WORD_RE = re.compile(r"\bdue\b", re.IGNORECASE)
MAX_NGRAM = 3


def load_denylist(path: Path = DENYLIST_PATH) -> set[str]:
    """Read one lowercase sha256 hex digest per line."""
    if not path.exists():
        return set()
    lines = path.read_text(encoding="utf-8").splitlines()
    return {ln.strip().lower() for ln in lines if ln.strip() and not ln.startswith("#")}


def git_files(root: Path | None = None) -> list[Path]:
    """Every tracked file, as paths relative to the repo root."""
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=str(root) if root else None,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [Path(name) for name in out.split("\0") if name]


def is_scannable(path: Path) -> bool:
    """True for text files the gate should read."""
    if path.name in SKIP_NAMES or path.suffix.lower() in SKIP_SUFFIXES:
        return False
    try:
        head = path.open("rb").read(8192)
    except OSError:
        return False
    return b"\0" not in head


def line_hits_denylist(line: str, denylist: set[str]) -> bool:
    """True when any 1, 2 or 3 word run of the line is on the denylist."""
    if not denylist:
        return False
    tokens = TOKEN_RE.findall(line.lower())
    for start in range(len(tokens)):
        for size in range(1, MAX_NGRAM + 1):
            if start + size > len(tokens):
                break
            phrase = " ".join(tokens[start : start + size])
            if hashlib.sha256(phrase.encode("utf-8")).hexdigest() in denylist:
                return True
    return False


def scan_text(
    name: str, text: str, denylist: set[str], flag_banned_word: bool = False
) -> list[str]:
    errors: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if EM_DASH in line:
            errors.append(f"{name}:{number}: em dash")
        if line_hits_denylist(line, denylist):
            errors.append(f"{name}:{number}: denylist hit")
        if flag_banned_word and BANNED_WORD_RE.search(line):
            errors.append(f"{name}:{number}: banned word")
    return errors


def scan(files: list[Path], denylist: set[str]) -> list[str]:
    """Check every readable text file in files. Returns error strings."""
    errors: list[str] = []
    for path in files:
        if not path.is_file() or not is_scannable(path):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        errors.extend(scan_text(path.as_posix(), text, denylist))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prose and privacy gate.")
    parser.add_argument("--packet", type=Path, help="also check a built packet file")
    parser.add_argument("--denylist", type=Path, default=DENYLIST_PATH)
    args = parser.parse_args(argv)

    denylist = load_denylist(args.denylist)
    errors = scan(git_files(), denylist)
    if args.packet:
        text = args.packet.read_text(encoding="utf-8", errors="replace")
        errors.extend(scan_text(args.packet.as_posix(), text, denylist, flag_banned_word=True))

    for error in errors:
        print(error)
    print(f"check_prose: {len(errors)} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
