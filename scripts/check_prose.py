#!/usr/bin/env python3
"""Prose and privacy gate.

Scans tracked text files for two things: the em dash, which the packet style
forbids, and any phrase whose sha256 appears in the hashed denylist. Hits are
reported by file and line, never by content.

A file that opens with a UTF-16 byte order mark is decoded as UTF-16 before
anything else looks at it. Such a file is full of NUL bytes, so the binary
test used to wave it through unread, which is a hole in a gate whose job is
to find a name nobody meant to ship.

``--packet`` adds the checks that only make sense on a built packet: the
standalone banned word, the data-rid attribute, and a link to a fragment of
the packet itself. A packet is printed and mailed, so an attribute carrying a
message id and a link that goes nowhere on paper are both noise a reviewer
should never have to see.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

# The two prose rules, defined once for the whole repo. build.py imports them
# so a packet and the gate can never disagree about what they forbid.
EM_DASH = "\u2014"  # the character itself never appears in this repo
DENYLIST_PATH = Path("tests/pii_denylist.sha256")
SKIP_NAMES = {"uv.lock", "pii_denylist.sha256"}
SKIP_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2"}
TOKEN_RE = re.compile(r"[a-z0-9]+")
BANNED_WORD_RE = re.compile(r"\bdue\b", re.IGNORECASE)
MAX_NGRAM = 4

# Markup that has no business in a finished packet.
DATA_RID_RE = re.compile(r"data-rid=")
FRAGMENT_LINK = 'href="#'

# Both UTF-16 orders. A UTF-32 file opens with the first of these too, and
# reads as mojibake rather than as nothing at all, which is the safer failure.
UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")


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
    """True for text files the gate should read.

    A UTF-16 file is scannable even though it is mostly NUL bytes, because
    ``read_text`` knows how to decode it.
    """
    if path.name in SKIP_NAMES or path.suffix.lower() in SKIP_SUFFIXES:
        return False
    try:
        with path.open("rb") as handle:
            head = handle.read(8192)
    except OSError:
        return False
    if head.startswith(UTF16_BOMS):
        return True
    return b"\0" not in head


def read_text(path: Path) -> str:
    """The file's text, decoded as UTF-16 when it opens with a UTF-16 mark."""
    raw = path.read_bytes()
    if raw.startswith(UTF16_BOMS):
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8", errors="replace")


def line_hits_denylist(line: str, denylist: set[str]) -> bool:
    """True when any run of up to MAX_NGRAM words in the line is on the denylist."""
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
    name: str,
    text: str,
    denylist: set[str],
    flag_banned_word: bool = False,
    flag_packet_markup: bool = False,
) -> list[str]:
    """Every rule that applies, line by line. Returns error strings.

    The two flags are separate because a caller that only wants the prose
    rules checked on packet text should not have to take the markup rules
    with them.
    """
    errors: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if EM_DASH in line:
            errors.append(f"{name}:{number}: em dash")
        if line_hits_denylist(line, denylist):
            errors.append(f"{name}:{number}: denylist hit")
        if flag_banned_word and BANNED_WORD_RE.search(line):
            errors.append(f"{name}:{number}: banned word")
        if flag_packet_markup:
            if DATA_RID_RE.search(line):
                errors.append(f"{name}:{number}: data-rid attribute")
            if FRAGMENT_LINK in line:
                errors.append(f"{name}:{number}: fragment link")
    return errors


def scan(files: list[Path], denylist: set[str]) -> list[str]:
    """Check every readable text file in files. Returns error strings."""
    errors: list[str] = []
    for path in files:
        if not path.is_file() or not is_scannable(path):
            continue
        errors.extend(scan_text(path.as_posix(), read_text(path), denylist))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prose and privacy gate.")
    parser.add_argument("--packet", type=Path, help="also check a built packet file")
    parser.add_argument("--denylist", type=Path, default=DENYLIST_PATH)
    args = parser.parse_args(argv)

    denylist = load_denylist(args.denylist)
    errors = scan(git_files(), denylist)
    if args.packet:
        errors.extend(
            scan_text(
                args.packet.as_posix(),
                read_text(args.packet),
                denylist,
                flag_banned_word=True,
                flag_packet_markup=True,
            )
        )

    for error in errors:
        print(error)
    print(f"check_prose: {len(errors)} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
