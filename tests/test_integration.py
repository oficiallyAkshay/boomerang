"""The whole pipeline, once through, on the session fixture.

Every other test file proves one script against its own stubs. These two prove
the scripts against each other: the fixture receipts are cleaned with the real
vendor rules, fingerprinted, validated, built, rendered, spliced, and read back
out of the finished PDF. The first test drives the Python interfaces, the
second drives the same flow through the documented command lines, so a CLI
whose flags drift away from references/interfaces.md fails here rather than in
someone's shell.

Both of them clean the whole directory in one call, which is the stage the
workflow describes: ``clean_dir`` in the first, ``clean.py --dir`` in the
second. The receipts are staged with the ``.meta.json`` sidecars fetch.py
writes, because reading the vendor off those is how a directory clean decides
which rules a receipt gets.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import attach_pdf
import build
import cards
import check_prose
import clean
import pytest
import render_pdf
from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDORS_DIR = REPO_ROOT / "vendors"
DENYLIST = Path(__file__).resolve().parent / "pii_denylist.sha256"

# The company card. It pays for the ticket and nothing else, so it is the one
# last-4 that shows up on exactly one receipt.
COMPANY_CARD = "8802"
FOLIO_PAGES = 2
FOLIO_FIRST_PAGE_MARK = "folio page 1 of 2"

# An address that belongs to no vendor in vendors/, for a receipt whose vendor
# is not one the rules know.
UNKNOWN_SENDER = "billing@an-unlisted-vendor.example"

# The committed example, and the two rows in it that have cost a correction:
# the Uber and Uber Eats totals, each set at 32px over a 40px line. They are
# the widest figures on any receipt, and the ride sits two receipts below a
# sender whose stylesheet wraps long words, so they are the first things to
# break when a vendor's CSS escapes its own receipt. Both folders once carried
# a replace pair widening the title cell beside the figure; scoping each
# receipt's stylesheet to its own card is what actually holds the row
# together, and the pairs are gone. These bounds are the rendered box at the
# render viewport in print media: one line tall, and wide enough to be holding
# every character rather than one per line.
EXAMPLE_PACKET = REPO_ROOT / "examples" / "packet.html"
EXAMPLE_DATA = REPO_ROOT / "examples" / "expense_data.json"
UBER_TOTAL_TESTID = "total_fare_amount"
UBER_TOTALS = {"Ride, home to airport": "$34.86", "Team lunch order": "$88.60"}
ONE_LINE = 48
MIN_TOTAL_WIDTH = 60


def sender_for(vendor: str, rules: dict[str, dict]) -> str:
    """A synthetic From header that detect_vendor should resolve to vendor."""
    if vendor not in rules:
        return UNKNOWN_SENDER
    domains = rules[vendor].get("sender_domains") or []
    if not domains:
        return UNKNOWN_SENDER
    return f"receipts@{domains[0]}"


def expected_vendor(title: str, rules: dict[str, dict]) -> str | None:
    """The rules key for a receipt's vendor label, or None when there is none."""
    name = title.strip().lower()
    return name if name in rules else None


@pytest.fixture(scope="module")
def fixture_data(fixture_dir: Path) -> dict:
    return json.loads((fixture_dir / "expense_data.json").read_text(encoding="utf-8"))


def stage_mailbox(source_dir: Path, target_dir: Path, rules: dict[str, dict], data: dict) -> None:
    """The receipts as fetch.py would leave them, sidecar headers included.

    The fixture ships the bodies but not the headers, so the From line each
    receipt's own vendor would have sent is written out beside it. That is
    what the cleaner reads when it is pointed at a directory.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(source_dir.iterdir()):
        if path.is_file():
            shutil.copy2(path, target_dir / path.name)

    vendor_by_rid = {r["rid"]: r["vendor"] for r in data["receipts"]}
    for path in sorted(source_dir.glob("*.html")):
        want = expected_vendor(vendor_by_rid[path.stem], rules)
        (target_dir / f"{path.stem}.meta.json").write_text(
            json.dumps({"from": sender_for(want or "", rules), "subject": ""}),
            encoding="utf-8",
        )


def expected_listing(source_dir: Path, rules: dict[str, dict], data: dict) -> list[tuple[str, str]]:
    """What clean_dir should report: every HTML receipt, in filename order."""
    vendor_by_rid = {r["rid"]: r["vendor"] for r in data["receipts"]}
    return [
        (path.name, expected_vendor(vendor_by_rid[path.stem], rules) or "generic")
        for path in sorted(source_dir.glob("*.html"))
    ]


def test_the_pipeline_runs_end_to_end(fixture_dir: Path, fixture_data: dict, tmp_path: Path):
    """Clean, fingerprint, validate, build, render, splice, read back."""
    rules = clean.load_vendor_rules(VENDORS_DIR)
    source = fixture_dir / "receipts"
    mailbox = tmp_path / "mailbox"
    stage_mailbox(source, mailbox, rules, fixture_data)

    # Stage 5, one call: every HTML receipt cleaned at once with the rules its
    # own saved sender resolves to, everything else carried across untouched.
    receipts = tmp_path / "clean"
    listed = clean.clean_dir(mailbox, receipts, VENDORS_DIR)
    assert listed == expected_listing(source, rules, fixture_data)
    assert len(listed) == 8
    for name, _ in listed:
        fragment = (receipts / name).read_text(encoding="utf-8")
        assert "<script" not in fragment.lower()
        assert "href=" not in fragment.lower()
    assert len(list(receipts.iterdir())) == len(list(source.iterdir()))

    # Who paid, read off the cleaned receipts rather than the raw mail.
    fingerprints = cards.fingerprint(receipts)
    assert cards.singletons(fingerprints) == {COMPANY_CARD}
    assert len(fingerprints[COMPANY_CARD]) == 1

    # The data still describes what is on disk.
    assert build.validate(fixture_data, receipts) == []

    packet_html = tmp_path / "packet.html"
    packet_html.write_text(build.render_packet(fixture_data, receipts), encoding="utf-8")
    assert check_prose.scan([packet_html], check_prose.load_denylist(DENYLIST)) == []

    # One summary page, then every receipt starting on a page of its own and
    # running on to the next when it is too long for one.
    receipt_count = len(fixture_data["receipts"])
    packet_pdf = tmp_path / "packet.pdf"
    pages, channel, page_map = render_pdf.render(packet_html, packet_pdf)
    assert page_map["summary_pages"] == 1
    assert len(page_map["pages"]) == receipt_count
    assert pages == 1 + sum(page_map["pages"])
    assert min(page_map["scales"]) >= render_pdf.MIN_SCALE
    assert channel in render_pdf.CHANNELS
    assert render_pdf.page_count(packet_pdf) == pages

    # The folio's own pages land behind the last page of its own receipt.
    final_pdf = tmp_path / "final.pdf"
    final_pages = attach_pdf.splice(packet_pdf, fixture_data, receipts, final_pdf)
    assert final_pages == pages + FOLIO_PAGES

    text = attach_pdf.extract_text(final_pdf)
    for day in fixture_data["days"]:
        assert day["label"] in text
    assert "Total" in text

    per_page = text.split("\f")
    assert len(per_page) == final_pages
    folio_index = next(
        index
        for index, receipt in enumerate(fixture_data["receipts"])
        if (receipts / f"{receipt['rid']}.pdf").is_file()
    )
    card_page = page_map["summary_pages"] + sum(page_map["pages"][: folio_index + 1]) - 1
    assert "PDF attachment" in per_page[card_page]
    assert FOLIO_FIRST_PAGE_MARK in per_page[card_page + 1]


def run_script(name: str, *args: str) -> subprocess.CompletedProcess[str]:
    """One documented command line, run from the repo root."""
    done = subprocess.run(
        [sys.executable, f"scripts/{name}.py", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, f"{name} exited {done.returncode}: {done.stderr}"
    return done


def test_the_documented_clis_run_the_same_pipeline(
    fixture_dir: Path, fixture_data: dict, tmp_path: Path
):
    """Every CLI shape in references/interfaces.md, driven end to end."""
    rules = clean.load_vendor_rules(VENDORS_DIR)
    source = fixture_dir / "receipts"
    mailbox = tmp_path / "mailbox"
    stage_mailbox(source, mailbox, rules, fixture_data)

    receipts = tmp_path / "clean"
    cleaned = run_script(
        "clean",
        "--dir",
        str(mailbox),
        "--out",
        str(receipts),
        "--vendors",
        str(VENDORS_DIR),
    ).stdout
    assert f"clean: wrote 8 receipts into {receipts.as_posix()}" in cleaned

    listing = run_script("cards", str(receipts)).stdout
    assert f"{COMPANY_CARD}  1  " in listing
    assert f"{COMPANY_CARD}: seen once" in listing

    data_path = fixture_dir / "expense_data.json"
    packet_html = tmp_path / "packet.html"
    built = run_script(
        "build", str(data_path), "--receipts", str(receipts), "--out", str(packet_html)
    ).stdout
    assert "total $" in built

    receipt_count = len(fixture_data["receipts"])
    packet_pdf = tmp_path / "packet.pdf"
    rendered = int(run_script("render_pdf", str(packet_html), str(packet_pdf)).stdout)
    page_map = json.loads(
        (tmp_path / "packet.pdf.pages.json").read_text(encoding="utf-8"),
    )
    assert len(page_map["pages"]) == receipt_count
    assert rendered == page_map["summary_pages"] + sum(page_map["pages"])
    # And the count the render printed is the count --expect takes.
    run_script("render_pdf", str(packet_html), str(packet_pdf), "--expect", str(rendered))

    final_pdf = tmp_path / "final.pdf"
    spliced = run_script(
        "attach_pdf",
        str(packet_pdf),
        str(data_path),
        "--receipts",
        str(receipts),
        "--out",
        str(final_pdf),
    ).stdout
    assert spliced.strip() == str(rendered + FOLIO_PAGES)

    # The gate now flags data-rid in packet mode. build.py still stamps one on
    # every receipt section, and that attribute is on its way out of the
    # builder, so the run is allowed to report those lines and nothing else.
    gate = subprocess.run(
        [sys.executable, "scripts/check_prose.py", "--packet", str(packet_html)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    reported = [line for line in gate.splitlines() if not line.startswith("check_prose:")]
    assert all(line.endswith(": data-rid attribute") for line in reported), gate


def measure(url: str, selectors: dict[str, str]) -> dict[str, dict]:
    """The bounding box of each named element, at the viewport the render uses.

    One browser for the whole set, in print media, because that is the media
    render_pdf prints the packet in and a rule that only applies on screen
    would otherwise be measured instead of the one that reaches the PDF.
    """
    found: dict[str, dict] = {}
    with sync_playwright() as play:
        browser, _ = render_pdf.launch_browser(play)
        try:
            page = browser.new_page(viewport=render_pdf.VIEWPORT)
            page.route("**/*", render_pdf.block_remote_requests)
            page.emulate_media(media="print")
            page.goto(url, wait_until="load")
            for name, selector in selectors.items():
                located = page.locator(selector)
                assert located.count() == 1, f"{located.count()} elements match {selector}"
                found[name] = {
                    "box": located.first.bounding_box(),
                    "text": located.first.inner_text(),
                }
        finally:
            browser.close()
    return found


def test_the_examples_uber_totals_print_on_one_line():
    """The committed packet, measured rather than eyeballed.

    The selector is half the assertion. A receipt card carries ``rc`` and its
    own ordinal, and a fragment's stylesheet is rewritten to match that pair,
    so finding the cell under ``.rc.rN`` says the scoping reached the packet
    that is committed. The box says the rule from two receipts up did not, and
    that the amount is sitting on one line with no vendor rule widening the
    cell beside it.
    """
    data = json.loads(EXAMPLE_DATA.read_text(encoding="utf-8"))
    ordinals = {
        receipt["title"]: index
        for index, receipt in enumerate(data["receipts"], start=1)
        if receipt["title"] in UBER_TOTALS
    }
    assert sorted(ordinals) == sorted(UBER_TOTALS), ordinals
    selectors = {
        title: f'.rc.r{ordinal} [data-testid="{UBER_TOTAL_TESTID}"]'
        for title, ordinal in ordinals.items()
    }
    found = measure(EXAMPLE_PACKET.resolve().as_uri(), selectors)

    for title, amount in UBER_TOTALS.items():
        box = found[title]["box"]
        assert found[title]["text"] == amount
        assert box["width"] > MIN_TOTAL_WIDTH, (title, box)
        assert box["height"] <= ONE_LINE, (title, box)
