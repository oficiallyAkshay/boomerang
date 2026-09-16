#!/usr/bin/env python3
"""Find candidate receipts in a mailbox and write each one to disk.

Two passes, as the Discovery policy asks for. Pass one sweeps the padded
window with generic terms. Pass two asks each vendor's sender domains
directly, which catches the mail whose subject line says nothing useful.
Both passes pad the window a day on each side, because ride receipts land
six to twenty hours after the ride and the airport legs go missing without
the padding.

Messages are fetched one at a time and written straight to disk. Nothing
holds more than a single body in memory, and a message whose meta file is
already on disk is never fetched twice.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from clean import load_vendor_rules

GENERIC_TERMS = [
    "receipt",
    "your receipt",
    "confirmation",
    "your ride",
    "your trip",
    "your order",
    "eticket",
    "e-ticket",
    "itinerary",
    "folio",
    "invoice",
]

RID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ATTACHMENT_EXTS = {"pdf", "png", "jpg", "jpeg"}


@dataclass
class Query:
    """One Gmail search. after and before are the padded window ends."""

    terms: list[str] = field(default_factory=list)
    from_domains: list[str] = field(default_factory=list)
    after: date = date(1970, 1, 1)
    before: date = date(1970, 1, 1)


class FetchReport(list):
    """The rids written this run.

    A plain list of the rids, so it matches the interface, with the refused
    and the already-on-disk rids carried alongside for the caller to print.
    """

    def __init__(
        self,
        written: list[str] | None = None,
        rejected: list[str] | None = None,
        skipped: list[str] | None = None,
    ) -> None:
        super().__init__(written or [])
        self.rejected = list(rejected or [])
        self.skipped = list(skipped or [])


def build_queries(window_start: date, window_end: date, vendor_rules: dict) -> list[Query]:
    """Pass one generic terms, then one pass two query per vendor with domains.

    Vendors are visited in sorted name order so the query list is stable.
    A vendor whose rules list no sender domains contributes no query.
    """
    after = window_start - timedelta(days=1)
    before = window_end + timedelta(days=1)
    queries = [Query(terms=list(GENERIC_TERMS), from_domains=[], after=after, before=before)]
    for name in sorted(vendor_rules):
        rules = vendor_rules[name] or {}
        domains = [d for d in (rules.get("sender_domains") or []) if d]
        if not domains:
            continue
        queries.append(Query(terms=[], from_domains=domains, after=after, before=before))
    return queries


def _quote(term: str) -> str:
    """Gmail needs a multi word phrase in quotes to stay one phrase."""
    return f'"{term}"' if any(ch.isspace() for ch in term) else term


def to_gmail(q: Query) -> str:
    """Render one Query as a Gmail search string.

    Gmail reads before: as exclusive, so a message sent on the padded end
    date is only returned when before: names the day after it. after: is
    inclusive and takes the padded start unchanged.
    """
    parts = [
        f"after:{q.after.strftime('%Y/%m/%d')}",
        f"before:{(q.before + timedelta(days=1)).strftime('%Y/%m/%d')}",
    ]
    if q.terms:
        parts.append("(" + " OR ".join(_quote(t) for t in q.terms) + ")")
    if q.from_domains:
        parts.append("(" + " OR ".join(f"from:{d}" for d in q.from_domains) + ")")
    return " ".join(parts)


def attachment_ext(filename: str) -> str | None:
    """The lowercase extension, when it is one of the four we keep."""
    ext = Path(filename or "").suffix.lstrip(".").lower()
    return ext if ext in ATTACHMENT_EXTS else None


def write_message(out_dir: Path, rid: str, message: dict) -> dict:
    """Write one message body, its attachments and its meta file.

    The meta file is written last. A run cut short halfway leaves no meta
    file, so the next run fetches that message again instead of trusting a
    half written body.
    """
    out_dir = Path(out_dir)
    html = message.get("html")
    if html:
        (out_dir / f"{rid}.html").write_text(html, encoding="utf-8")
    text = message.get("text")
    if text:
        (out_dir / f"{rid}.txt").write_text(text, encoding="utf-8")

    written: list[str] = []
    refused: list[str] = []
    for index, (filename, blob) in enumerate(message.get("attachments") or []):
        ext = attachment_ext(filename)
        if ext is None:
            refused.append(filename)
            continue
        name = f"{rid}.{index}.{ext}"
        (out_dir / name).write_bytes(blob)
        written.append(name)

    meta = {
        "from": message.get("from", ""),
        "subject": message.get("subject", ""),
        "date": message.get("date", ""),
        "attachments": written,
        "attachments_skipped": refused,
    }
    (out_dir / f"{rid}.meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


def fetch_all(source, queries: list[Query], out_dir: Path) -> list[str]:
    """Run every query, then fetch each new message once, in first seen order."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ordered: list[str] = []
    seen: set[str] = set()
    for q in queries:
        for rid in source.search(to_gmail(q)):
            if rid not in seen:
                seen.add(rid)
                ordered.append(rid)

    written: list[str] = []
    rejected: list[str] = []
    skipped: list[str] = []
    for rid in ordered:
        if not RID_RE.match(rid):
            rejected.append(rid)
            continue
        if (out_dir / f"{rid}.meta.json").exists():
            skipped.append(rid)
            continue
        write_message(out_dir, rid, source.get(rid))
        written.append(rid)
    return FetchReport(written, rejected, skipped)


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Windowed two pass receipt search.")
    parser.add_argument("--start", type=_parse_date, required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", type=_parse_date, required=True, help="YYYY-MM-DD")
    parser.add_argument("--out", type=Path, required=True, help="receipts directory")
    parser.add_argument("--vendors", type=Path, help="vendors directory for pass two")
    parser.add_argument("--source", choices=["gmail"], default="gmail")
    parser.add_argument("--dry-run", action="store_true", help="print the queries and stop")
    args = parser.parse_args(argv)

    rules = load_vendor_rules(args.vendors) if args.vendors else {}
    queries = build_queries(args.start, args.end, rules)

    if args.dry_run:
        for q in queries:
            print(to_gmail(q))
        return 0

    from gmail_cli import GmailSource

    report = fetch_all(GmailSource(), queries, args.out)
    print(
        f"fetch: {len(report)} written, "
        f"{len(getattr(report, 'skipped', []))} already on disk, "
        f"{len(getattr(report, 'rejected', []))} refused"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
