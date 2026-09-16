"""The whole pipeline, once through, on the session fixture.

Every other test file proves one script against its own stubs. These two prove
the scripts against each other: the fixture receipts are cleaned with the real
vendor rules, fingerprinted, validated, built, rendered, spliced, and read back
out of the finished PDF. The first test drives the Python interfaces, the
second drives the same flow through the documented command lines, so a CLI
whose flags drift away from docs/interfaces.md fails here rather than in
someone's shell.
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


def copy_non_html(source_dir: Path, target_dir: Path) -> None:
    """Carry the text, PDF and image receipts across untouched."""
    for path in sorted(source_dir.iterdir()):
        if path.is_file() and path.suffix.lower() != ".html":
            shutil.copy2(path, target_dir / path.name)


def test_the_pipeline_runs_end_to_end(fixture_dir: Path, fixture_data: dict, tmp_path: Path):
    """Clean, fingerprint, validate, build, render, splice, read back."""
    rules = clean.load_vendor_rules(VENDORS_DIR)
    source = fixture_dir / "receipts"
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    copy_non_html(source, receipts)

    # Clean every HTML receipt with the rules its own sender resolves to.
    vendor_by_rid = {r["rid"]: r["vendor"] for r in fixture_data["receipts"]}
    cleaned = 0
    for path in sorted(source.glob("*.html")):
        want = expected_vendor(vendor_by_rid[path.stem], rules)
        found = clean.detect_vendor(sender_for(want or "", rules), "", rules)
        assert found == want, f"{path.name} resolved to {found}, expected {want}"
        vendor_rules = rules[found] if found else None
        fragment = clean.clean_html(path.read_text(encoding="utf-8"), vendor_rules)
        assert "<script" not in fragment.lower()
        assert "href=" not in fragment.lower()
        (receipts / path.name).write_text(fragment, encoding="utf-8")
        cleaned += 1
    assert cleaned == 8
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

    # One summary page, then one page per receipt.
    receipt_count = len(fixture_data["receipts"])
    packet_pdf = tmp_path / "packet.pdf"
    pages = render_pdf.render(packet_html, packet_pdf)
    assert pages == 1 + receipt_count
    assert render_pdf.page_count(packet_pdf) == pages

    # The folio's own pages land behind its card.
    final_pdf = tmp_path / "final.pdf"
    final_pages = attach_pdf.splice(packet_pdf, fixture_data, receipts, final_pdf)
    assert final_pages == 1 + receipt_count + FOLIO_PAGES

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
    summary_pages = pages - receipt_count
    card_page = summary_pages + folio_index
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
    """Every CLI shape in docs/interfaces.md, driven end to end."""
    rules = clean.load_vendor_rules(VENDORS_DIR)
    source = fixture_dir / "receipts"
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    copy_non_html(source, receipts)

    vendor_by_rid = {r["rid"]: r["vendor"] for r in fixture_data["receipts"]}
    for path in sorted(source.glob("*.html")):
        vendor = expected_vendor(vendor_by_rid[path.stem], rules)
        assert vendor is not None
        run_script(
            "clean",
            str(path),
            "--out",
            str(receipts / path.name),
            "--vendor",
            vendor,
            "--vendors",
            str(VENDORS_DIR),
        )

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
    rendered = run_script(
        "render_pdf", str(packet_html), str(packet_pdf), "--expect", str(1 + receipt_count)
    ).stdout
    assert rendered.strip() == str(1 + receipt_count)

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
    assert spliced.strip() == str(1 + receipt_count + FOLIO_PAGES)

    gate = run_script("check_prose", "--packet", str(packet_html)).stdout
    assert gate.strip().endswith("0 errors")
