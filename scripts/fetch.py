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

Gmail is the only source, so the CLI has no flag for choosing one. A source is
anything with ``search(query_str) -> list[str]`` and ``get(rid) -> dict``,
where the dict carries ``html``, ``text``, ``from``, ``subject``, ``date`` and
``attachments`` as a list of ``(filename, bytes)`` pairs.

Each message is written out as ``<rid>.html``, ``<rid>.txt`` and
``<rid>.meta.json``. The first kept attachment of each kind takes the plain
name ``<rid>.pdf``, ``<rid>.png`` or ``<rid>.jpg``, which is the name
``build.find_receipt_file`` and ``attach_pdf.splice`` look for, so a folio that
arrived as an attachment reaches the packet. A second attachment of the same
kind takes ``<rid>.<n>.<ext>``, and ``meta.json`` lists every name written.

A message with no body and no attachment worth keeping writes nothing at all,
meta file included. Writing an empty meta file would mark the message done and
skip it on every later run, so instead it is reported as failed and tried
again next time.
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

# Attachment suffix on the way in, mapped to the suffix we store it under.
# jpeg becomes jpg because jpg is the one the rest of the pipeline looks for.
ATTACHMENT_EXTS = {"pdf": "pdf", "png": "png", "jpg": "jpg", "jpeg": "jpg"}


@dataclass
class Query:
    """One Gmail search. after and before are the padded window ends."""

    terms: list[str] = field(default_factory=list)
    from_domains: list[str] = field(default_factory=list)
    after: date = date(1970, 1, 1)
    before: date = date(1970, 1, 1)


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
    """The suffix we would store this attachment under, or None to refuse it.

    Four suffixes are kept and a .jpeg is stored as .jpg, so every image
    attachment lands under a name the rest of the pipeline already knows.
    """
    ext = Path(filename or "").suffix.lstrip(".").lower()
    return ATTACHMENT_EXTS.get(ext)


def _attachment_names(rid: str, message: dict) -> tuple[list[tuple[str, bytes]], list[str]]:
    """The (name, bytes) pairs to write, and the file names refused.

    The first attachment of each kind takes the plain ``<rid>.<ext>``, which is
    the name ``build.find_receipt_file`` and ``attach_pdf.splice`` look for. A
    second attachment of that kind takes ``<rid>.1.<ext>``, a third
    ``<rid>.2.<ext>``, so nothing overwrites anything.
    """
    kept: list[tuple[str, bytes]] = []
    refused: list[str] = []
    counts: dict[str, int] = {}
    for filename, blob in message.get("attachments") or []:
        ext = attachment_ext(filename)
        if ext is None:
            refused.append(filename)
            continue
        seen = counts.get(ext, 0)
        counts[ext] = seen + 1
        kept.append((f"{rid}.{ext}" if seen == 0 else f"{rid}.{seen}.{ext}", blob))
    return kept, refused


def write_message(out_dir: Path, rid: str, message: dict) -> dict | None:
    """Write one message body, its attachments and its meta file.

    The rid is checked first. It becomes a file name several times over, so a
    rid that is not a safe file name raises here rather than writing anywhere.
    ``fetch_all`` already refuses such rids, and the check is repeated because
    this function is callable on its own.

    Attachment names come from ``_attachment_names``: the first of each kind
    is ``<rid>.pdf``, ``<rid>.png`` or ``<rid>.jpg``, later ones carry a
    number. ``meta.json`` lists the names written.

    Returns the meta it wrote, or None for a message that has neither body nor
    a single attachment worth keeping. Such a message leaves nothing on disk,
    meta file included, because a meta file is the mark that says this message
    is done and it would otherwise be skipped on every run from here on.

    The meta file is written last. A run cut short halfway leaves no meta
    file, so the next run fetches that message again instead of trusting a
    half written body.
    """
    if not isinstance(rid, str) or not RID_RE.match(rid):
        raise ValueError(f"message id is not a valid id: {rid!r}")
    out_dir = Path(out_dir)
    html = message.get("html")
    text = message.get("text")
    kept, refused = _attachment_names(rid, message)
    if not html and not text and not kept:
        return None

    if html:
        (out_dir / f"{rid}.html").write_text(html, encoding="utf-8")
    if text:
        (out_dir / f"{rid}.txt").write_text(text, encoding="utf-8")
    for name, blob in kept:
        (out_dir / name).write_bytes(blob)

    meta = {
        "from": message.get("from", ""),
        "subject": message.get("subject", ""),
        "date": message.get("date", ""),
        "attachments": [name for name, _ in kept],
        "attachments_skipped": refused,
    }
    (out_dir / f"{rid}.meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


def fetch_all(
    source, queries: list[Query], out_dir: Path
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Run every query, then fetch each new message once, in first seen order.

    Returns ``(written, rejected, skipped, failed)``: the rids fetched this
    run, the rids refused by the id pattern, the rids whose meta file was
    already on disk, and the rids that came back with no body and no
    attachment worth keeping. A failed rid leaves nothing on disk, so the next
    run asks for it again.
    """
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
    failed: list[str] = []
    for rid in ordered:
        if not RID_RE.match(rid):
            rejected.append(rid)
            continue
        if (out_dir / f"{rid}.meta.json").exists():
            skipped.append(rid)
            continue
        if write_message(out_dir, rid, source.get(rid)) is None:
            failed.append(rid)
            continue
        written.append(rid)
    return written, rejected, skipped, failed


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Windowed two pass receipt search.")
    parser.add_argument("--start", type=_parse_date, required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", type=_parse_date, required=True, help="YYYY-MM-DD")
    parser.add_argument("--out", type=Path, required=True, help="receipts directory")
    parser.add_argument("--vendors", type=Path, help="vendors directory for pass two")
    parser.add_argument("--dry-run", action="store_true", help="print the queries and stop")
    args = parser.parse_args(argv)

    rules = load_vendor_rules(args.vendors) if args.vendors else {}
    queries = build_queries(args.start, args.end, rules)

    if args.dry_run:
        for q in queries:
            print(to_gmail(q))
        return 0

    from gmail_cli import GmailSource

    written, rejected, skipped, failed = fetch_all(GmailSource(), queries, args.out)
    for rid in failed:
        print(f"fetch: {rid} came back empty, nothing written, it will be asked for again")
    print(
        f"fetch: {len(written)} written, "
        f"{len(skipped)} already on disk, "
        f"{len(rejected)} refused, "
        f"{len(failed)} empty"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
