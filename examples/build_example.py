#!/usr/bin/env python3
"""Build the worked example packet, end to end, from the vendor samples.

Everything here is synthetic. The trip, the traveller, the day labels and every
claimed amount are invented; the receipts are the scrubbed vendor samples that
live under ``vendors/``, plus the two receipts this script writes itself: a two
page hotel folio PDF and a photographed day pass. No real mailbox is read and
no personal data is written.

What this script is for is showing the pipeline in SKILL.md run once, on real
vendor markup, with the output committed so a reader can open the packet
without running anything.

The claim is built the way the skill says to build it.

- Every claimed amount is a figure its own receipt prints. Where a receipt
  prints a tip, the tip comes off and the line says so, because policy leaves
  tips out. Where something other than the traveller's card paid part of a
  bill, the claim is the card tender and the line says so.
- Every day label is the date the receipts filed under it print. The samples
  were scrubbed one vendor at a time, so their dates do not fall into a tidy
  three day trip, and the window here is the shortest one that holds every
  claimed receipt rather than a neater one invented over the top of them. Two
  claimed receipts print no date anywhere, the DoorDash final receipt and the
  photographed day pass, and each sits under the day its message header
  carries, which its line says.
- The folio is the one receipt the script writes rather than borrows, because
  no vendor sample is a folio. It is a two page PDF for the Harborview Hotel,
  it prints a room line and a tax line for each night of the stay, and its
  nights are the nights the other receipts put the traveller in town. The room
  charge is claimed on the check-out day, which is the day the folio is issued
  and the last day of the window.
- Five receipts are in the packet with no line beside them. Four show a charge
  nobody can claim: the United eTicket was paid with the value of a previous
  ticket, the Lufthansa receipt is a baggage drop-off with no amount on it, the
  Marriott confirmation is a points redemption with no cash figure, and the
  citizenM mail is a pointer to an invoice rather than the invoice. The fifth
  is the DoorDash order, which is groceries, and policy.md flags groceries
  rather than claiming them. It stays in the packet because a flagged line
  still has to be shown: the reviewer sees what was bought and sees that the
  packet is not asking to be paid for it.

Running it
----------

``python examples/build_example.py`` rebuilds the example in place: it copies
the receipts, writes the expense data, downloads any vendor images the cache is
missing, prunes the cache back under its size cap, cleans the whole directory
through ``clean_dir``, and then runs cards, build, render_pdf and attach_pdf
exactly as SKILL.md documents them. It exits 1, naming the URLs, if the packet
it just built still carries a remote image source, however that source was
quoted: an image is either inlined from the cache or printed as its alt text,
and a live ``http`` source is a packet that reaches for the network when it is
opened.

``python examples/build_example.py --check`` rebuilds into a temporary
directory with the network switched off, inlining only what the committed cache
already holds, and exits 1 if the packet HTML differs by a single byte or the
PDF page count moves.

One note on how the documented pipeline is run here.

``clean.py --fetch-images`` has no size cap, so the fetch is run once into the
cache, the cache is then pruned of anything that is not an image and anything
over 200 KB and trimmed to 1.5 MB, and the receipts are cleaned again offline
against the pruned cache. That way the committed packet matches the committed
cache exactly, which is what ``--check`` verifies. The fetch also leaves a
record of the URLs that answered with an error page rather than a picture, and
that record is committed beside the cached images, so the offline pass prints
those images' alt text exactly as the fetching pass would. That record is
merged into, never replaced, so a rebuild on a machine that cannot resolve a
vendor host keeps the entries that run learned nothing about.

Stdlib only, plus the repo's own scripts.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = REPO_ROOT / "examples"
SCRIPTS = REPO_ROOT / "scripts"
VENDORS = REPO_ROOT / "vendors"

# The receipt writers, shared with the fixture generator, and the cleaner,
# which this script drives in process for the one stage that is a directory in
# and a directory out. Everything else is run as the documented command line.
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(EXAMPLES))

import clean  # noqa: E402
from synth import money, pdf_bytes, png_bytes  # noqa: E402

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

# A cache entry smaller than this is not a picture of anything: it is a short
# error body, or a bare "OK", saved under an image URL that answered with
# something other than an image. Such an entry inlines as a broken data URI and
# carries whatever the server said, so it is dropped before the cache is
# committed.
MIN_IMAGE_BYTES = 100

# Every remote image source a packet could still be carrying, whichever way the
# vendor quoted it. A committed packet must hold none: an image is either
# inlined from the cache as a data URI or printed as its alt text, and a
# surviving http source is a picture this machine could not fetch and did not
# record, which would reach for the network from whatever opens the packet.
REMOTE_SRC_RE = re.compile(
    r"""src\s*=\s*(?:"(https?://[^"]*)"|'(https?://[^']*)'|(https?://[^\s>]+))""", re.I
)

COMPANY = "Northwind Labs, Inc."
TRAVELER = "Jordan Rivera"
TRIP = "AUS to SEA onsite, June 11 to 16, 2026"

# The folio the script writes. The nights are the nights the other receipts
# put the traveller in town, the first being the evening the ride to the
# airport is dated and the last the night before the flight home, so the stay
# is the shortest one that covers the trip the rest of the packet describes.
HOTEL = "Harborview Hotel"
FOLIO_CONF = "HV-2048841"
FOLIO_CARD_LAST4 = "4321"
FOLIO_NIGHTS = [
    "Thursday June 11, 2026",
    "Friday June 12, 2026",
    "Saturday June 13, 2026",
    "Sunday June 14, 2026",
    "Monday June 15, 2026",
]
FOLIO_CHECKOUT = "Tuesday June 16, 2026"
FOLIO_RATE = 154.00
FOLIO_NIGHTLY_TAX = 23.10
FOLIO_ROOM = round(FOLIO_RATE * len(FOLIO_NIGHTS), 2)
FOLIO_TAX = round(FOLIO_NIGHTLY_TAX * len(FOLIO_NIGHTS), 2)
FOLIO_TOTAL = round(FOLIO_ROOM + FOLIO_TAX, 2)

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
        "Your Thursday evening trip with Uber",
        "Thu, 11 Jun 2026 21:12:00 -0500",
    ),
    (
        "united_wifi",
        VENDORS / "united" / "sample-wifi.html",
        "United Airlines <Receipts@united.com>",
        "Thanks for your purchase with United",
        "Tue, 16 Jun 2026 23:41:00 -0500",
    ),
    (
        "doordash",
        VENDORS / "doordash" / "sample.html",
        "DoorDash <no-reply@doordash.com>",
        "Final receipt for Jordan from Northgate Market",
        "Thu, 11 Jun 2026 20:02:00 -0700",
    ),
    (
        "marriott",
        VENDORS / "marriott" / "sample.html",
        "Marriott <reservations@res-marriott.com>",
        "Reservation Confirmation #51882037 for The Westin Balcones Park",
        "Thu, 4 Jun 2026 08:30:00 -0500",
    ),
    (
        "citizenm",
        VENDORS / "plaintext" / "sample.txt",
        "citizenM <noreply@an-unlisted-inn.example>",
        "Open me to get your citizenM invoice",
        "Tue, 16 Jun 2026 11:05:00 -0500",
    ),
    (
        "folio",
        None,
        "Front desk <folio@an-unlisted-inn.example>",
        f"Your {HOTEL} folio for confirmation {FOLIO_CONF}",
        "Tue, 16 Jun 2026 11:20:00 -0500",
    ),
    (
        "uber_eats",
        VENDORS / "uber-eats" / "sample.html",
        "Uber Eats <noreply@uber.com>",
        "Your Friday afternoon order with Uber Eats",
        "Fri, 12 Jun 2026 13:35:00 -0500",
    ),
    (
        "njtransit",
        VENDORS / "njtransit" / "sample.html",
        "NJ TRANSIT <noreply@mytix.njtransit.com>",
        "NJ TRANSIT - Receipt",
        "Mon, 15 Jun 2026 08:44:00 -0500",
    ),
    (
        "transit",
        None,
        "Jordan Rivera <traveller@an-unlisted-inbox.example>",
        "Photo of the day pass",
        "Fri, 12 Jun 2026 09:02:00 -0500",
    ),
    (
        "stripe",
        VENDORS / "stripe" / "sample.html",
        "Northgate Labs <invoice+statements@stripe.com>",
        "Your receipt from Northgate Labs Inc. #4106-8823",
        "Thu, 11 Jun 2026 14:20:00 -0500",
    ),
    (
        "lufthansa",
        VENDORS / "lufthansa" / "sample.html",
        "Lufthansa <flight.service@information.lufthansa.com>",
        "Your baggage receipt 4783120956 for Austin - Seattle on 15. June 2026",
        "Mon, 15 Jun 2026 17:05:00 -0500",
    ),
    (
        "lyft",
        VENDORS / "lyft" / "sample.html",
        "Lyft <no-reply@lyftmail.com>",
        "Your ride with Nadia on June 15",
        "Mon, 15 Jun 2026 18:45:00 -0500",
    ),
]

# Receipt titles, in packet order. The order of this list is the order the
# receipts appear in the packet.
TITLES = {
    "united_eticket": "eTicket, AUS to SEA, paid with previous ticket value",
    "uber_ride": "Ride, home to airport",
    "united_wifi": "Inflight Wi-Fi",
    "doordash": "Groceries delivered to the hotel, flagged not claimed",
    "marriott": "Stay confirmation, points redemption",
    "citizenm": "Invoice notice from the property",
    "folio": f"Folio, {len(FOLIO_NIGHTS)} nights",
    "uber_eats": "Team lunch order",
    "njtransit": "Bus fare, one way",
    "transit": "Day pass, photo receipt",
    "stripe": "Software licence for the onsite",
    "lufthansa": "Baggage receipt, checked bag",
    "lyft": "Ride, office to hotel",
}

VENDOR_LABELS = {
    "united_eticket": "United",
    "uber_ride": "Uber",
    "united_wifi": "United",
    "doordash": "DoorDash",
    "marriott": "Marriott",
    "citizenm": "citizenM",
    "folio": HOTEL,
    "uber_eats": "Uber Eats",
    "njtransit": "NJ TRANSIT",
    "transit": "City transit",
    "stripe": "Northgate Labs",
    "lufthansa": "Lufthansa",
    "lyft": "Lyft",
}

# Every claimed amount below is printed on the receipt it points at, and every
# day here is the date that receipt carries. The right hand column is where the
# date was read: the body of the receipt itself, or the message header for the
# one receipt that prints no date at all.
#   uber_ride    34.86  the charged total          Jun 11, on the receipt
#   stripe       54.11  the amount paid            Jun 11, on the receipt
#   uber_eats    73.60  88.60 ordered, less the 15.00 voucher, card tender
#                                                  Jun 12, on the receipt
#   transit       6.71  the figure on the photographed pass
#                                                  Jun 12, message header
#   njtransit     4.75  the charged total          Jun 15, on the receipt
#   lyft         22.27  40.67 charged, less 18.40 of Lyft Cash, card tender
#                                                  Jun 15, on the receipt
#   united_wifi  10.99  the charged total          Jun 16, on the receipt
#   folio       885.50  room and tax for five nights, from the folio total
#                                                  Jun 16, the check-out day
#                                                  printed on page 2
DAYS = [
    {
        "label": "Thursday, June 11, travel out",
        "items": [
            ("Ride, home to airport", 34.86, "uber_ride"),
            ("Software licence for the onsite", 54.11, "stripe"),
        ],
    },
    {
        "label": "Friday, June 12, working day",
        "items": [
            ("Team lunch, voucher netted out", 73.60, "uber_eats"),
            ("Transit day pass, dated by the message header", 6.71, "transit"),
        ],
    },
    {
        "label": "Monday, June 15, working day",
        "items": [
            ("Bus fare, hotel to office", 4.75, "njtransit"),
            ("Ride, office to hotel, Lyft Cash netted out", 22.27, "lyft"),
        ],
    },
    {
        "label": "Tuesday, June 16, check out, personal day, flight home kept",
        "items": [
            (f"Room and tax, {len(FOLIO_NIGHTS)} nights", FOLIO_TOTAL, "folio"),
            ("Inflight Wi-Fi", 10.99, "united_wifi"),
        ],
    },
]

STIPEND = {"desc": "Meal stipend, 2 days worked", "amt": 75.00}

# The summary table fits on one Letter page, and the receipts that follow are
# one per page, which is the count render_pdf is asked to hold to. The number
# is read back off a render rather than guessed: build the example and the
# page count it prints is SUMMARY_PAGES plus one page per receipt.
SUMMARY_PAGES = 1

TEXT_SUFFIXES = {".html", ".txt"}


# ------------------------------------------------------------------- receipts


def folio_pages() -> list[list[str]]:
    """The two pages of the folio, a room line and a tax line per night.

    Three nights on page 1 and the rest on page 2, so the stay is split the
    way a front desk splits it and the totals land under the nights they add
    up. The total on the last page is the figure the packet claims.
    """
    head = [
        f"{HOTEL} folio page 1 of 2",
        f"Guest {TRAVELER}",
        f"Confirmation {FOLIO_CONF}",
    ]
    tail = [f"{HOTEL} folio page 2 of 2", f"Guest {TRAVELER}"]
    for number, night in enumerate(FOLIO_NIGHTS, start=1):
        target = head if number <= 3 else tail
        target.append(f"Night {number}, {night}")
        target.append(f"  Room {money(FOLIO_RATE)}")
        target.append(f"  Room tax {money(FOLIO_NIGHTLY_TAX)}")
    tail += [
        f"Check out {FOLIO_CHECKOUT}",
        f"Room, {len(FOLIO_NIGHTS)} nights {money(FOLIO_ROOM)}",
        f"Room tax {money(FOLIO_TAX)}",
        f"Room and tax total {money(FOLIO_TOTAL)}",
        f"Charged to Visa ending {FOLIO_CARD_LAST4}",
    ]
    return [head, tail]


def written_receipts(target: Path) -> dict[str, Path]:
    """The two receipts no vendor sample supplies, written here.

    The folio is a two page PDF built the way the fixture generator builds
    one, and the day pass is a solid colour PNG standing in for a photograph.
    Neither writer needs an image or a PDF library.
    """
    folio = target / f"{RIDS['folio']}.pdf"
    folio.write_bytes(pdf_bytes(folio_pages()))
    transit = target / f"{RIDS['transit']}.png"
    transit.write_bytes(png_bytes(200, 80, (28, 92, 148)))
    return {"folio": folio, "transit": transit}


def write_receipts(target: Path) -> dict[str, Path]:
    """Copy every receipt into target, with a meta file beside each one."""
    target.mkdir(parents=True, exist_ok=True)
    made = written_receipts(target)
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
    """The packet input, in the schema references/interfaces.md sets out."""
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
    """Drop what is not an image, then oversized entries, then the largest.

    Returns the number of files kept and the total bytes. An image that is
    dropped is not lost from the packet: its ``src`` stays as the vendor wrote
    it, so the receipt still renders, just without that one picture offline.

    The first pass is the privacy one. A URL that answers with a short error
    body rather than a picture still lands in the cache, and a cached body that
    is not an image is a stray fragment of someone's session, so an entry under
    ``MIN_IMAGE_BYTES`` or without one of the four signatures is unlinked here
    and never reaches a commit.

    The one file no pass touches is the fetch's failure record, which is not a
    cache entry at all: it is the list of URLs that answered with an error page
    rather than a picture, and it is what tells an offline clean to print those
    images' alt text instead of leaving a broken image in the packet.
    """
    if not cache.is_dir():
        return 0, 0

    def entries() -> list[Path]:
        return [
            path
            for path in sorted(cache.iterdir())
            if path.is_file() and path.name != clean.FAILED_RECORD
        ]

    for path in entries():
        with path.open("rb") as handle:
            head = handle.read(12)
        if path.stat().st_size < MIN_IMAGE_BYTES or not clean._looks_like_an_image(head):
            path.unlink()
    for path in entries():
        if path.stat().st_size > MAX_IMAGE_BYTES:
            path.unlink()
    files = entries()
    files.sort(key=lambda path: path.stat().st_size, reverse=True)
    total = sum(path.stat().st_size for path in files)
    while total > MAX_CACHE_BYTES and files:
        biggest = files.pop(0)
        total -= biggest.stat().st_size
        biggest.unlink()
    kept = entries()
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


def clean_receipts(receipts: Path, cleaned: Path, cache: Path, fetch: bool) -> list[str]:
    """Stage 5, in one call: every receipt in, every receipt out.

    ``clean_dir`` is what SKILL.md documents and what the cleaner's own tests
    cover, so the example runs it rather than a loop of its own. It reads each
    receipt's vendor from the ``.meta.json`` saved beside it, which is the one
    case that used to need a hand here: Uber and Uber Eats send from the same
    domain, and the detection prefers the folder whose subject patterns fit as
    well as its domain. The ``.txt``, ``.pdf`` and ``.png`` receipts are
    carried across untouched.
    """
    if cleaned.exists():
        shutil.rmtree(cleaned)
    listed = clean.clean_dir(receipts, cleaned, VENDORS, image_cache=cache, fetch_images=fetch)
    return [f"{name} -> {vendor}" for name, vendor in listed]


def build_packet(data_path: Path, cleaned: Path, out_html: Path, out_pdf: Path) -> int:
    """build, render_pdf, attach_pdf. Returns the final page count."""
    run("build", str(data_path), "--receipts", str(cleaned), "--out", str(out_html))
    expected = SUMMARY_PAGES + len(SOURCES)
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


def remote_sources(html: str) -> list[str]:
    """Every remote image URL still live in a packet, in the order they appear.

    Each match names one image the packet would reach out for when it is
    opened, which is the one thing a committed packet must never do. The URLs
    come back rather than a count, because the useful thing to print is which
    picture went unfetched and unrecorded.
    """
    found: list[str] = []
    for match in REMOTE_SRC_RE.finditer(html):
        url = next(group for group in match.groups() if group is not None)
        if url not in found:
            found.append(url)
    return found


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

    live = remote_sources(PACKET_HTML.read_text(encoding="utf-8"))
    if live:
        print("[example] the rebuilt packet still reaches for these images:")
        for url in live:
            print(f"[example]   {url}")
        print(
            "[example] each one was neither fetched into the cache nor recorded as failed. "
            "Rerun the fetch with those hosts reachable, or add them to "
            f"{(IMAGE_CACHE / clean.FAILED_RECORD).name} so the packet prints their alt text."
        )
        return 1
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
