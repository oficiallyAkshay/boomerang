#!/usr/bin/env python3
"""Card fingerprints across a receipts directory.

The Who paid policy in one script: a card last-4 that shows up on exactly
one receipt in the whole mailbox is almost never the traveler's own card,
it is the company card that paid for one booking. A last-4 on many receipts
is the card the traveler carries.

PDF receipts are read too. Hotel folios are the receipt most likely to name
a different card from the rest of the trip, and they are the receipt that
arrives as a PDF, so leaving them out left the one answer that mattered off
the table.

A bare asterisk in front of four digits is a footnote at least as often as
it is a card, so one mask character only counts when a card word stands
close in front of it. Two or more mask characters count on their own.

Some vendors print a card line none of those shapes reads. Given a vendors
directory, each receipt is matched to the folder that sent it, through the
headers ``fetch.py`` saved beside it, and read with that folder's own
``last4_pattern`` where it has one. Without the directory nothing changes,
and a receipt whose headers name no folder is read the built-in way.

Nothing here is a payment detail. A last-4 is the only fragment receipts
print, and it is read, counted and thrown away.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

import attach_pdf
import clean

# A run of mask characters, then the four digits, with at most one space, dot
# or hyphen between them. "Amex xxxx-4321" and "Visa •••• 4321" both land here.
MASK_RE = re.compile(r"(\*+|x+|•+)[ .-]?(\d{4})", re.IGNORECASE)

# "ending 4321" and "ending in 4321", which need no mask at all.
ENDING_RE = re.compile(r"ending\s+(?:in\s+)?(\d{4})", re.IGNORECASE)

# What has to stand within CONTEXT characters in front of a single mask
# character for it to read as a card rather than as a footnote marker.
CARD_WORD_RE = re.compile(
    r"\b(card|visa|mastercard|amex|discover|ending|debit|credit)", re.IGNORECASE
)
CONTEXT = 20
LONE_MASK = 1

SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
TEXT_SUFFIXES = {".html", ".txt"}
PDF_SUFFIX = ".pdf"


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


def _has_card_word(text: str, before: int) -> bool:
    """True when a card word stands in the CONTEXT characters before before."""
    return CARD_WORD_RE.search(text[max(0, before - CONTEXT) : before]) is not None


def _from_vendor_pattern(text: str, pattern: str) -> set[str]:
    """The last-4 values a vendor's own card line prints, group one each time.

    Four digits and no more, and never a slice of a longer run, so a pattern
    written loosely still cannot turn a reference number into a card.
    """
    found: set[str] = set()
    for match in re.finditer(pattern, text, re.IGNORECASE):
        digits = match.group(1) or ""
        if len(digits) == 4 and digits.isdigit() and _isolated(text, match.start(1), match.end(1)):
            found.add(digits)
    return found


def find_last4(text: str, rules: dict | None = None) -> set[str]:
    """Every card last-4 the text prints, in any of the known shapes.

    A single mask character is only read as a card when a card word stands
    close in front of it, so "Visa *4321" is a card and "Flight UA *1234",
    "Total *4321" and "Footnote: *2025 terms" are not. Two or more mask
    characters are a card on their own, because nothing else prints them.

    A vendor whose card line fits none of those shapes says where its digits
    are in its own ``last4_pattern``, and passing that vendor's rules here
    reads the line that way instead. Stripe is the case that needs it: the
    card brand is an image and the digits follow a bare dash, which no mask
    and no "ending in" appears anywhere near. The pattern replaces the
    built-in shapes rather than adding to them, because a folder that has to
    describe its card line is a folder the defaults were wrong about. With no
    rules, or with rules that name no pattern, nothing here changes.
    """
    vendor_pattern = (rules or {}).get("last4_pattern")
    if isinstance(vendor_pattern, str) and vendor_pattern:
        return _from_vendor_pattern(text, vendor_pattern)
    found: set[str] = set()
    for match in ENDING_RE.finditer(text):
        if _isolated(text, match.start(1), match.end(1)):
            found.add(match.group(1))
    for match in MASK_RE.finditer(text):
        if not _isolated(text, match.start(2), match.end(2)):
            continue
        if len(match.group(1)) <= LONE_MASK and not _has_card_word(text, match.start(1)):
            continue
        found.add(match.group(2))
    return found


def _pdf_text(path: Path) -> str:
    """The text of a PDF receipt, or nothing at all when it will not open.

    A folio that pypdf cannot read is reported by build.validate and by
    attach_pdf. Here it simply names no card, because refusing to count the
    other thirty receipts over one torn attachment helps nobody.
    """
    try:
        return attach_pdf.extract_text(path)
    except Exception:
        return ""


def receipt_text(path: Path) -> str | None:
    """The readable text of one receipt file, or None when it is not one."""
    suffix = path.suffix.lower()
    if suffix == PDF_SUFFIX:
        return _pdf_text(path)
    if suffix not in TEXT_SUFFIXES:
        return None
    raw = path.read_text(encoding="utf-8", errors="replace")
    return strip_tags(raw) if suffix == ".html" else raw


def rules_for(path: Path, vendor_rules: dict | None) -> dict | None:
    """The rules of the folder that sent this receipt, or None for the defaults.

    The headers ``fetch.py`` saved beside the file name the vendor, and
    ``clean.detect_vendor`` picks the folder from them, which is the same
    answer the cleaner reached for the same receipt. A receipt with no meta
    file beside it, or headers naming a folder these rules do not hold, falls
    back to the built-in card shapes rather than to some other vendor's.
    """
    if not vendor_rules:
        return None
    meta = clean.read_meta(path)
    if not meta:
        return None
    name = clean.detect_vendor(
        str(meta.get("from") or ""), str(meta.get("subject") or ""), vendor_rules
    )
    return vendor_rules.get(name) if name else None


def fingerprint(receipts_dir: Path, rules: dict | None = None) -> dict[str, list[str]]:
    """last4 -> sorted rids, over every html, text and pdf receipt found.

    With vendor rules, each receipt is read with its own folder's card line
    where that folder describes one, so a card no general shape reaches is
    counted along with the rest and can be the singleton that answers who
    paid. Without them the reading is what it has always been.
    """
    hits: dict[str, set[str]] = {}
    for path in sorted(Path(receipts_dir).iterdir()):
        if not path.is_file():
            continue
        text = receipt_text(path)
        if text is None:
            continue
        for last4 in find_last4(text, rules_for(path, rules)):
            hits.setdefault(last4, set()).add(path.stem)
    return {last4: sorted(rids) for last4, rids in sorted(hits.items())}


def singletons(fp: dict[str, list[str]]) -> set[str]:
    """The last-4 values seen on exactly one receipt."""
    return {last4 for last4, rids in fp.items() if len(rids) == 1}


def main(argv: list[str] | None = None) -> int:
    """Card fingerprints across a receipts directory."""
    parser = argparse.ArgumentParser(description="Card fingerprints across a receipts directory.")
    parser.add_argument("receipts_dir", type=Path, help="directory of receipt files")
    parser.add_argument(
        "--vendors",
        type=Path,
        help="vendors directory, so a vendor's own card line is read too",
    )
    args = parser.parse_args(argv)

    rules = clean.load_vendor_rules(args.vendors) if args.vendors else None
    fp = fingerprint(args.receipts_dir, rules)
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
