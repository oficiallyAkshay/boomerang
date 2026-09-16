#!/usr/bin/env python3
"""Build the worked example packet, end to end, from the vendor samples.

Everything here is synthetic. The trip, the traveller, the day labels and every
claimed amount are invented; the receipts are the scrubbed vendor samples that
live under ``vendors/``, plus the two receipts the fixture generator makes: a
two page hotel folio PDF and a photographed day pass. No real mailbox is read
and no personal data is written.

What this script is for is showing the pipeline in SKILL.md run once, on real
vendor markup, with the output committed so a reader can open the packet
without running anything.

The claim is built the way the skill says to build it.

- Every claimed amount is a figure its own receipt prints. Where a receipt
  prints a tip, the tip comes off and the line says so, because policy leaves
  tips out. Where something other than the traveller's card paid part of a
  bill, the claim is the card tender and the line says so.
- Four receipts are in the packet with no line beside them, because they show a
  charge nobody can claim: the United eTicket was paid with the value of a
  previous ticket, the Lufthansa receipt is a baggage drop-off with no amount
  on it, the Marriott confirmation is a points redemption with no cash figure,
  and the citizenM mail is a pointer to a folio rather than the folio. The
  folio itself is the two page PDF, and that is where the room charge is
  claimed from.

Running it
----------

``python examples/build_example.py`` rebuilds the example in place: it copies
the receipts, writes the expense data, downloads any vendor images the cache is
missing, prunes the cache back under its size cap, and then runs clean, cards,
build, render_pdf and attach_pdf exactly as SKILL.md documents them.

``python examples/build_example.py --check`` rebuilds into a temporary
directory with the network switched off, inlining only what the committed cache
already holds, and exits 1 if the packet HTML differs by a single byte or the
PDF page count moves.

Two notes on how the documented pipeline is run here.

SKILL.md step 7 shows ``build.py ... --receipts receipts``. This script passes
the cleaned directory instead, because ``build.py`` inserts an HTML receipt
into the packet as is: handing it the raw mail would put the vendors' scripts
and tracking pixels straight into the packet. The cleaned directory holds the
cleaned HTML plus the text, PDF and image receipts copied across untouched.

``clean.py --fetch-images`` has no size cap, so the fetch is run once into the
cache, the cache is then pruned of anything over 200 KB and trimmed to 1.5 MB,
and the receipts are cleaned again offline against the pruned cache. That way
the committed packet matches the committed cache exactly, which is what
``--check`` verifies.

Stdlib only, plus the repo's own scripts.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = REPO_ROOT / "examples"
SCRIPTS = REPO_ROOT / "scripts"
VENDORS = REPO_ROOT / "vendors"

RECEIPTS = EXAMPLES / "receipts"
CLEAN = EXAMPLES / "clean"
IMAGE_CACHE = EXAMPLES / "image-cache"
DATA_PATH = EXAMPLES / "expense_data.json"
PACKET_HTML = EXAMPLES / "packet.html"
PACKET_PDF = EXAMPLES / "packet.pdf"

# One image over this size is a hero photograph, not a logo, and a cache over
# the total is not something to commit. Both are enforced after the fetch.
MAX_IMAGE_BYTES = 200 * 1024
MAX_CACHE_BYTES = 1_500 * 1024

COMPANY = "Northwind Labs, Inc."
TRAVELER = "Jordan Rivera"
TRIP = "AUS to SEA onsite, June 8 to 10, 2026"

# Receipt ids, one per receipt. Generated once with secrets.token_hex(8) and
# frozen here, so a rebuild writes the same file names and the same packet.
RIDS = {
    "uber_ride": "f7e925ca92e2d2d2",
    "united_wifi": "31b5223f2a62e2ce",
    "doordash": "d2e61faed5861571",
    "uber_eats": "654c53dc3924c879",
    "njtransit": "1c4f3b50684e94b5",
    "stripe": "5c359f66433854d8",
    "folio": "10a0d07da121a647",
    "transit": "a59320850b63be2c",
    "lyft": "5b59a786336f4ceb",
    "united_eticket": "de89b6711d085c37",
    "lufthansa": "647603912df707b3",
    "marriott": "19768ef7c5e3471c",
    "citizenm": "2d00ffde43b1575f",
}

# Where each receipt's file comes from, and the synthetic headers that make
# vendor detection work. The From and Subject lines are invented, in the shape
# each vendor really sends, so detect_vendor resolves the same rules a fetched
# message would.
SOURCES = [
    (
        "united_eticket",
        VENDORS / "united" / "sample.html",
        "United Airlines <Receipts@united.com>",
        "eTicket Itinerary and Receipt for Confirmation KRVDTM",
        "Wed, 10 Jun 2026 09:14:00 -0500",
    ),
    (
        "uber_ride",
        VENDORS / "uber" / "sample.html",
        "Uber Receipts <noreply@uber.com>",
        "Your Monday evening trip with Uber",
        "Mon, 8 Jun 2026 21:12:00 -0500",
    ),
    (
        "united_wifi",
        VENDORS / "united" / "sample-wifi.html",
        "United Airlines <Receipts@united.com>",
        "Thanks for your purchase with United",
        "Mon, 8 Jun 2026 23:41:00 -0500",
    ),
    (
        "doordash",
        VENDORS / "doordash" / "sample.html",
        "DoorDash <no-reply@doordash.com>",
        "Final receipt for Jordan from Northgate Market",
        "Mon, 8 Jun 2026 20:02:00 -0700",
    ),
    (
        "marriott",
        VENDORS / "marriott" / "sample.html",
        "Marriott <reservations@res-marriott.com>",
        "Reservation Confirmation #51882037 for Austin Congress Avenue",
        "Thu, 4 Jun 2026 08:30:00 -0500",
    ),
    (
        "citizenm",
        VENDORS / "plaintext" / "sample.txt",
        "citizenM <noreply@an-unlisted-inn.example>",
        "Open me to get your citizenM invoice",
        "Wed, 10 Jun 2026 11:05:00 -0500",
    ),
    (
        "folio",
        None,
        "Front desk <folio@an-unlisted-inn.example>",
        "Your folio for confirmation 51882037",
        "Wed, 10 Jun 2026 11:20:00 -0500",
    ),
    (
        "uber_eats",
        VENDORS / "uber-eats" / "sample.html",
        "Uber Eats <noreply@uber.com>",
        "Your Tuesday morning order with Uber Eats",
        "Tue, 9 Jun 2026 13:35:00 -0500",
    ),
    (
        "njtransit",
        VENDORS / "njtransit" / "sample.html",
        "NJ TRANSIT <noreply@mytix.njtransit.com>",
        "NJ TRANSIT - Receipt",
        "Tue, 9 Jun 2026 08:44:00 -0500",
    ),
    (
        "transit",
        None,
        "Jordan Rivera <traveller@an-unlisted-inbox.example>",
        "Photo of the day pass",
        "Tue, 9 Jun 2026 09:02:00 -0500",
    ),
    (
        "stripe",
        VENDORS / "stripe" / "sample.html",
        "Northgate Labs <invoice+statements@stripe.com>",
        "Your receipt from Northgate Labs Inc. #4106-8823",
        "Tue, 9 Jun 2026 14:20:00 -0500",
    ),
    (
        "lufthansa",
        VENDORS / "lufthansa" / "sample.html",
        "Lufthansa <flight.service@information.lufthansa.com>",
        "Your baggage receipt 4783120956 for Austin - Seattle on 8. June 2026",
        "Mon, 8 Jun 2026 18:05:00 -0500",
    ),
    (
        "lyft",
        VENDORS / "lyft" / "sample.html",
        "Lyft <no-reply@lyftmail.com>",
        "Your ride with Nadia on June 10",
        "Wed, 10 Jun 2026 19:22:00 -0500",
    ),
]

# Receipt titles, in packet order. The order of this list is the order the
# receipts appear in the packet.
TITLES = {
    "united_eticket": "eTicket, AUS to SEA, paid with previous ticket value",
    "uber_ride": "Ride, home to airport",
    "united_wifi": "Inflight Wi-Fi",
    "doordash": "Groceries delivered to the hotel",
    "marriott": "Stay confirmation, points redemption",
    "citizenm": "Invoice notice from the property",
    "folio": "Folio, 2 nights",
    "uber_eats": "Team breakfast order",
    "njtransit": "Bus fare, one way",
    "transit": "Day pass, photo receipt",
    "stripe": "Software licence for the onsite",
    "lufthansa": "Baggage receipt, checked bag",
    "lyft": "Ride, hotel to airport",
}

VENDOR_LABELS = {
    "united_eticket": "United",
    "uber_ride": "Uber",
    "united_wifi": "United",
    "doordash": "DoorDash",
    "marriott": "Marriott",
    "citizenm": "citizenM",
    "folio": "Austin Congress Avenue",
    "uber_eats": "Uber Eats",
    "njtransit": "NJ TRANSIT",
    "transit": "City transit",
    "stripe": "Northgate Labs",
    "lufthansa": "Lufthansa",
    "lyft": "Lyft",
}

# Every claimed amount below is printed on the receipt it points at.
#   uber_ride    34.86  the charged total
#   united_wifi  10.99  the charged total
#   doordash     50.91  54.91 charged, less the 4.00 Dasher tip, tips out
#   uber_eats    73.60  88.60 ordered, less the 15.00 voucher, card tender
#   njtransit     4.75  the charged total
#   transit       6.71  the figure on the photographed pass
#   stripe       54.11  the amount paid
#   folio       500.95  room and tax for two nights, from the folio total
#   lyft         22.27  40.67 charged, less 18.40 of Lyft Cash, card tender
DAYS = [
    {
        "label": "Monday, June 8, travel out",
        "items": [
            ("Ride, home to airport", 34.86, "uber_ride"),
            ("Inflight Wi-Fi", 10.99, "united_wifi"),
            ("Groceries to the hotel, tip out", 50.91, "doordash"),
        ],
    },
    {
        "label": "Tuesday, June 9, working day",
        "items": [
            ("Team breakfast, voucher netted out", 73.60, "uber_eats"),
            ("Bus fare, hotel to office", 4.75, "njtransit"),
            ("Transit day pass", 6.71, "transit"),
            ("Software licence for the onsite", 54.11, "stripe"),
            ("Room and tax, 2 nights", 500.95, "folio"),
        ],
    },
    {
        "label": "Wednesday, June 10, personal day, airport leg kept",
        "items": [
            ("Ride, hotel to airport, Lyft Cash netted out", 22.27, "lyft"),
        ],
    },
]

STIPEND = {"desc": "Meal stipend, 1 day worked", "amt": 75.00}

TEXT_SUFFIXES = {".html", ".txt"}


# ------------------------------------------------------------------- receipts


def fixture_receipts(target: Path) -> dict[str, Path]:
    """The folio PDF and the photographed pass, from the fixture generator."""
    sys.path.insert(0, str(REPO_ROOT / "tests"))
    from fixtures import make_fixture

    with tempfile.TemporaryDirectory() as raw:
        made = Path(raw)
        make_fixture.make(made)
        source = made / "receipts"
        out = {}
        for key, suffix in (("folio", ".pdf"), ("transit", ".png")):
            found = source / f"{make_fixture.RIDS[key]}{suffix}"
            landing = target / f"{RIDS[key]}{suffix}"
            shutil.copyfile(found, landing)
            out[key] = landing
        return out


def write_receipts(target: Path) -> dict[str, Path]:
    """Copy every receipt into target, with a meta file beside each one."""
    target.mkdir(parents=True, exist_ok=True)
    made = fixture_receipts(target)
    paths: dict[str, Path] = {}
    for key, source, sender, subject, when in SOURCES:
        if source is None:
            landing = made[key]
        else:
            landing = target / f"{RIDS[key]}{source.suffix}"
            shutil.copyfile(source, landing)
        paths[key] = landing
        meta = {
            "from": sender,
            "subject": subject,
            "date": when,
            "attachments": [],
            "attachments_skipped": [],
        }
        (target / f"{RIDS[key]}.meta.json").write_text(
            json.dumps(meta, indent=2) + "\n", encoding="utf-8"
        )
    return paths


def expense_data() -> dict:
    """The packet input, in the schema docs/interfaces.md sets out."""
    return {
        "company": COMPANY,
        "trip": TRIP,
        "traveler": TRAVELER,
        "currency": "USD",
        "days": [
            {
                "label": day["label"],
                "items": [
                    {"desc": desc, "amt": amt, "rid": RIDS[key]} for desc, amt, key in day["items"]
                ],
            }
            for day in DAYS
        ],
        "receipts": [
            {"rid": RIDS[key], "title": TITLES[key], "vendor": VENDOR_LABELS[key]}
            for key, *_ in SOURCES
        ],
        "stipend": dict(STIPEND),
    }


# ---------------------------------------------------------------------- cache


def prune_cache(cache: Path) -> tuple[int, int]:
    """Drop oversized cache entries, then the largest until the cache fits.

    Returns the number of files kept and the total bytes. An image that is
    dropped is not lost from the packet: its ``src`` stays as the vendor wrote
    it, so the receipt still renders, just without that one picture offline.
    """
    if not cache.is_dir():
        return 0, 0
    files = [path for path in sorted(cache.iterdir()) if path.is_file()]
    for path in files:
        if path.stat().st_size > MAX_IMAGE_BYTES:
            path.unlink()
    files = [path for path in sorted(cache.iterdir()) if path.is_file()]
    files.sort(key=lambda path: path.stat().st_size, reverse=True)
    total = sum(path.stat().st_size for path in files)
    while total > MAX_CACHE_BYTES and files:
        biggest = files.pop(0)
        total -= biggest.stat().st_size
        biggest.unlink()
    kept = [path for path in cache.iterdir() if path.is_file()]
    return len(kept), sum(path.stat().st_size for path in kept)


# ------------------------------------------------------------------- pipeline


def run(name: str, *args: str) -> str:
    """One documented command line, run from the repo root."""
    done = subprocess.run(
        [sys.executable, str(SCRIPTS / f"{name}.py"), *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise SystemExit(f"{name} exited {done.returncode}: {done.stderr.strip()}")
    return done.stdout


def detect(receipts: Path, key: str) -> str | None:
    """The vendor a receipt's own headers resolve to.

    Nothing is overridden here. ``detect_vendor`` reads the saved From and
    Subject the same way the cleaner does when it is given no ``--vendor``,
    including the one case that used to need a hand: Uber and Uber Eats send
    from the same domain, and the detection prefers the folder whose subject
    patterns fit as well as its domain.
    """
    sys.path.insert(0, str(SCRIPTS))
    import clean

    rules = clean.load_vendor_rules(VENDORS)
    meta = json.loads((receipts / f"{RIDS[key]}.meta.json").read_text(encoding="utf-8"))
    return clean.detect_vendor(meta["from"], meta["subject"], rules)


def clean_receipts(receipts: Path, cleaned: Path, cache: Path, fetch: bool) -> list[str]:
    """Clean every HTML receipt, carry the rest across untouched."""
    if cleaned.exists():
        shutil.rmtree(cleaned)
    cleaned.mkdir(parents=True)
    lines = []
    for key, *_ in SOURCES:
        source = next(
            path
            for path in sorted(receipts.glob(f"{RIDS[key]}.*"))
            if path.suffix != ".json" and not path.name.endswith(".meta.json")
        )
        if source.suffix != ".html":
            shutil.copyfile(source, cleaned / source.name)
            continue
        vendor = detect(receipts, key)
        args = [
            str(source),
            "--out",
            str(cleaned / source.name),
            "--vendors",
            str(VENDORS),
            "--images",
            str(cache),
        ]
        if vendor:
            args += ["--vendor", vendor]
        if fetch:
            args.append("--fetch-images")
        run("clean", *args)
        lines.append(f"{key} -> {vendor}")
    return lines


def build_packet(data_path: Path, cleaned: Path, out_html: Path, out_pdf: Path) -> int:
    """build, render_pdf, attach_pdf. Returns the final page count."""
    run("build", str(data_path), "--receipts", str(cleaned), "--out", str(out_html))
    expected = 1 + len(SOURCES)
    with tempfile.TemporaryDirectory() as raw:
        rendered = Path(raw) / "rendered.pdf"
        run("render_pdf", str(out_html), str(rendered), "--expect", str(expected))
        pages = run(
            "attach_pdf",
            str(rendered),
            str(data_path),
            "--receipts",
            str(cleaned),
            "--out",
            str(out_pdf),
        )
    return int(pages.strip())


def pdf_pages(path: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(path)).pages)


# ----------------------------------------------------------------------- main


def build() -> int:
    """Rebuild the committed example in place."""
    print("[example] copying receipts")
    write_receipts(RECEIPTS)
    DATA_PATH.write_text(json.dumps(expense_data(), indent=2) + "\n", encoding="utf-8")

    print("[example] fetching vendor images into the cache")
    with tempfile.TemporaryDirectory() as raw:
        clean_receipts(RECEIPTS, Path(raw) / "clean", IMAGE_CACHE, fetch=True)
    count, size = prune_cache(IMAGE_CACHE)
    print(f"[example] image cache: {count} files, {size / 1024:.0f} KB")

    print("[example] cleaning receipts against the pruned cache")
    for line in clean_receipts(RECEIPTS, CLEAN, IMAGE_CACHE, fetch=False):
        print(f"[example]   {line}")

    print("[example] card fingerprints")
    print(run("cards", str(CLEAN)).rstrip())

    pages = build_packet(DATA_PATH, CLEAN, PACKET_HTML, PACKET_PDF)
    print(f"[example] packet.pdf is {pages} pages")
    return 0


def check() -> int:
    """Rebuild offline into a temporary directory and compare, byte for byte."""
    with tempfile.TemporaryDirectory() as raw:
        scratch = Path(raw)
        receipts = scratch / "receipts"
        write_receipts(receipts)
        data_path = scratch / "expense_data.json"
        data_path.write_text(json.dumps(expense_data(), indent=2) + "\n", encoding="utf-8")

        committed_data = DATA_PATH.read_bytes()
        if data_path.read_bytes() != committed_data:
            print("check: examples/expense_data.json differs from what this script writes")
            return 1

        clean_receipts(receipts, scratch / "clean", IMAGE_CACHE, fetch=False)
        html = scratch / "packet.html"
        pdf = scratch / "packet.pdf"
        pages = build_packet(data_path, scratch / "clean", html, pdf)

        problems = []
        rebuilt = html.read_bytes()
        if rebuilt != PACKET_HTML.read_bytes():
            problems.append(
                f"packet.html differs: {len(rebuilt)} bytes rebuilt, "
                f"{len(PACKET_HTML.read_bytes())} bytes committed"
            )
        committed_pages = pdf_pages(PACKET_PDF)
        if pages != committed_pages:
            problems.append(f"packet.pdf is {committed_pages} pages, rebuild is {pages}")

        for problem in problems:
            print(f"check: {problem}")
        if problems:
            return 1
        print(f"check: packet.html matches byte for byte, packet.pdf is {pages} pages")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or check the example packet.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="rebuild offline into a temporary directory and compare",
    )
    args = parser.parse_args(argv)
    return check() if args.check else build()


if __name__ == "__main__":
    sys.exit(main())
