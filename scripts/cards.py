#!/usr/bin/env python3
"""Card fingerprints across a receipts directory.

The Who paid policy in one script: a card last-4 that shows up on exactly
one receipt in the whole mailbox is almost never the traveler's own card,
it is the company card that paid for one booking. A last-4 on many receipts
is the card the traveler carries.

Nothing here is a payment detail. A last-4 is the only fragment receipts
print, and it is read, counted and thrown away.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

# The five shapes vendor receipts actually print. Case insensitive, since
# the same vendor writes "Ending in" in one template and "ending" in another.
PATTERNS = [
    re.compile(r"\*\s?(\d{4})\b"),
    re.compile(r"ending in (\d{4})", re.IGNORECASE),
    re.compile(r"ending (\d{4})", re.IGNORECASE),
    re.compile(r"x{2,}(\d{4})", re.IGNORECASE),
    re.compile(r"•{2,}\s?(\d{4})"),
]

SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
TEXT_SUFFIXES = {".html", ".txt"}


def strip_tags(raw: str) -> str:
    """Plain text from receipt markup, entities resolved.

    The entity pass matters: the dot separated card lines are written as
    &bull; runs in mail templates, never as the character itself.
    """
    text = SCRIPT_RE.sub(" ", raw)
    text = TAG_RE.sub(" ", text)
    return html.unescape(text)


def _isolated(text: str, start: int, end: int) -> bool:
    """True when the four digits are not a slice of a longer digit run."""
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    return not before.isdigit() and not after.isdigit()


def find_last4(text: str) -> set[str]:
    """Every card last-4 the text prints, in any of the known shapes."""
    found: set[str] = set()
    for pattern in PATTERNS:
        for match in pattern.finditer(text):
            if _isolated(text, match.start(1), match.end(1)):
                found.add(match.group(1))
    return found


def fingerprint(receipts_dir: Path) -> dict[str, list[str]]:
    """last4 -> sorted rids, over every html and text receipt in the directory."""
    hits: dict[str, set[str]] = {}
    for path in sorted(Path(receipts_dir).iterdir()):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        text = strip_tags(raw) if path.suffix.lower() == ".html" else raw
        for last4 in find_last4(text):
            hits.setdefault(last4, set()).add(path.stem)
    return {last4: sorted(rids) for last4, rids in sorted(hits.items())}


def singletons(fp: dict[str, list[str]]) -> set[str]:
    """The last-4 values seen on exactly one receipt."""
    return {last4 for last4, rids in fp.items() if len(rids) == 1}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Card fingerprints across a receipts directory.")
    parser.add_argument("receipts_dir", type=Path, help="directory of receipt files")
    args = parser.parse_args(argv)

    fp = fingerprint(args.receipts_dir)
    print("last4  count  rids")
    for last4, rids in fp.items():
        print(f"{last4}  {len(rids)}  {' '.join(rids)}")

    singles = sorted(singletons(fp))
    if singles:
        print(f"{', '.join(singles)}: seen once, probably someone else's card")
    else:
        print("No last-4 seen once, so every card here paid for more than one receipt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
