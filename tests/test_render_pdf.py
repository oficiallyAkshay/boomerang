"""Tests for the packet PDF render.

These need the real headless Chromium. They do not skip when it is missing;
they fail with the message that says how to install it.

One test here is a tampering probe. It renders a packet whose receipt carries
markup written to rewrite the summary amounts, and reads the finished PDF back
to prove the amounts are the real ones. It is the end of the chain the cleaner
starts: even if hostile markup reached a packet file, the render runs no page
script, so it cannot change a number on its way to the PDF.
"""

from __future__ import annotations

import json
from pathlib import Path

import build
import pytest
import render_pdf
from playwright.sync_api import Error as PlaywrightError
from pypdf import PdfReader

PACKET_TITLE = "Packet, AUS to SEA onsite"
TOP_MARK = "Oversized receipt top mark"
BOTTOM_MARK = "Oversized receipt bottom mark"
LETTER_WIDTH = 612.0
LETTER_HEIGHT = 792.0
TOLERANCE = 1.0


def packet_html(data: dict, receipts_dir: Path, title: str | None = PACKET_TITLE) -> str:
    """A minimal stand in for the packet builder output.

    Summary block, then one section per receipt in receipts order. A receipt
    whose file is a PDF becomes a short card, the first receipt becomes a
    deliberately oversized body that the render has to scale down.
    """
    receipts = data["receipts"]
    head = f"<title>{title}</title>" if title else ""
    parts = [
        "<html><head>",
        head,
        "<style>body{font-family:Helvetica,Arial,sans-serif;margin:0}"
        ".rhead{font-size:15px;font-weight:700;padding:4px 0}</style>",
        "</head><body>",
        '<div class="summary">Trip summary table goes here.</div>',
    ]
    for index, receipt in enumerate(receipts):
        rid = receipt["rid"]
        if (Path(receipts_dir) / f"{rid}.pdf").exists():
            body = "<p>The original attachment follows this page in the PDF.</p>"
        elif index == 0:
            body = (
                '<div style="height:3000px;font-size:15px">'
                f"<p>{TOP_MARK}</p>"
                '<div style="height:2880px"></div>'
                f"<p>{BOTTOM_MARK}</p>"
                "</div>"
            )
        else:
            body = f'<p style="font-size:15px">Receipt body for {rid}.</p>'
        parts.append(
            f'<section class="rsec" data-rid="{rid}">'
            f'<div class="rhead">{receipt.get("title", rid)}</div>'
            f'<div class="rc">{body}</div></section>'
        )
    parts.append("</body></html>")
    return "\n".join(parts)


def write_packet(
    target: Path, data: dict, receipts_dir: Path, title: str | None = PACKET_TITLE
) -> Path:
    """Write the helper packet HTML to target and return the path."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(packet_html(data, receipts_dir, title), encoding="utf-8")
    return target


@pytest.fixture(scope="module")
def fixture_data(fixture_dir: Path) -> dict:
    return json.loads((fixture_dir / "expense_data.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rendered(fixture_dir: Path, fixture_data: dict, tmp_path_factory) -> dict:
    """The helper packet, rendered once and shared by the tests below."""
    out = tmp_path_factory.mktemp("rendered")
    html = write_packet(out / "packet.html", fixture_data, fixture_dir / "receipts")
    pdf = out / "packet.pdf"
    pages = render_pdf.render(html, pdf)
    return {"html": html, "pdf": pdf, "pages": pages}


def test_one_page_per_receipt_plus_summary(rendered: dict, fixture_data: dict) -> None:
    assert rendered["pages"] == 1 + len(fixture_data["receipts"])


def test_every_page_is_letter(rendered: dict) -> None:
    for page in PdfReader(str(rendered["pdf"])).pages:
        assert abs(float(page.mediabox.width) - LETTER_WIDTH) <= TOLERANCE
        assert abs(float(page.mediabox.height) - LETTER_HEIGHT) <= TOLERANCE


def test_oversized_receipt_is_scaled_onto_one_page(rendered: dict, fixture_data: dict) -> None:
    """A 3000px body still takes exactly one page and keeps its text."""
    assert rendered["pages"] == 1 + len(fixture_data["receipts"])
    reader = PdfReader(str(rendered["pdf"]))
    pages = [page.extract_text() or "" for page in reader.pages]
    carrying_top = [i for i, text in enumerate(pages) if TOP_MARK in text]
    carrying_bottom = [i for i, text in enumerate(pages) if BOTTOM_MARK in text]
    assert carrying_top == [1]
    assert carrying_bottom == [1]


def test_title_comes_from_the_html_and_there_is_no_author(rendered: dict) -> None:
    metadata = PdfReader(str(rendered["pdf"])).metadata or {}
    assert metadata.get("/Title") == PACKET_TITLE
    assert "/Author" not in metadata


def test_html_title_reads_and_collapses_whitespace(tmp_path: Path) -> None:
    target = tmp_path / "titled.html"
    target.write_text("<html><head><title>A packet\n  title</title></head></html>", "utf-8")
    assert render_pdf.html_title(target) == "A packet title"


def test_html_title_is_empty_when_absent(tmp_path: Path) -> None:
    target = tmp_path / "bare.html"
    target.write_text("<html><body>no title element</body></html>", encoding="utf-8")
    assert render_pdf.html_title(target) == ""


# The classic tampering shape: an image that cannot load, and a handler that
# rewrites every amount cell in the summary the moment it fails.
TAMPER_MARKUP = (
    "<img src=x onerror=\"document.querySelectorAll('table.sum td.amt')"
    ".forEach(e=>e.textContent='$9,999.00')\">"
)


def test_a_receipt_cannot_rewrite_the_summary_amounts(tmp_path: Path) -> None:
    """The probe, rendered through the real builder and the real renderer."""
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    (receipts / "tamper.html").write_text(
        f"<p>A receipt with a payload</p>{TAMPER_MARKUP}", encoding="utf-8"
    )
    data = {
        "company": "Northwind Systems",
        "trip": "AUS to SEA onsite",
        "traveler": "A traveler",
        "days": [
            {
                "label": "Monday, travel out",
                "items": [{"desc": "Ride, home to airport", "amt": 41.25, "rid": "tamper"}],
            }
        ],
        "receipts": [{"rid": "tamper", "title": "A receipt", "vendor": "lyft"}],
    }
    # The probe is raw markup on purpose, and the validator now says so. What
    # this test is about is what the renderer does with it regardless.
    assert build.validate(data, receipts) == ["receipt tamper: looks uncleaned, run clean.py first"]

    packet = tmp_path / "packet.html"
    packet.write_text(build.render_packet(data, receipts), encoding="utf-8")
    pdf = tmp_path / "packet.pdf"
    render_pdf.render(packet, pdf)

    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf)).pages)
    assert "$41.25" in text
    assert "9,999" not in text
    assert "9999" not in text


def _stub_html(tmp_path: Path) -> Path:
    target = tmp_path / "packet.html"
    target.write_text("<html><head><title>Stub</title></head><body></body></html>", "utf-8")
    return target


def test_missing_chromium_raises_with_the_install_command(monkeypatch, tmp_path: Path) -> None:
    def explode() -> None:
        raise PlaywrightError("Executable doesn't exist at /nowhere/headless_shell")

    monkeypatch.setattr(render_pdf, "sync_playwright", explode)
    with pytest.raises(RuntimeError, match=r"uv run playwright install chromium"):
        render_pdf.render(_stub_html(tmp_path), tmp_path / "packet.pdf")


def test_other_playwright_errors_are_not_reworded(monkeypatch, tmp_path: Path) -> None:
    def explode() -> None:
        raise PlaywrightError("navigation failed")

    monkeypatch.setattr(render_pdf, "sync_playwright", explode)
    with pytest.raises(PlaywrightError, match="navigation failed"):
        render_pdf.render(_stub_html(tmp_path), tmp_path / "packet.pdf")


@pytest.fixture(scope="module")
def tiny_packet(tmp_path_factory) -> Path:
    """One receipt, no title element, for the fast CLI runs."""
    out = tmp_path_factory.mktemp("tiny")
    data = {"receipts": [{"rid": "only", "title": "The only receipt"}]}
    return write_packet(out / "tiny.html", data, out / "receipts", title=None)


def test_cli_prints_the_page_count(tiny_packet: Path, tmp_path: Path, capsys) -> None:
    pdf = tmp_path / "tiny.pdf"
    assert render_pdf.main([str(tiny_packet), str(pdf), "--expect", "2"]) == 0
    assert capsys.readouterr().out.strip() == "2"
    metadata = PdfReader(str(pdf)).metadata or {}
    assert "/Title" not in metadata


def test_cli_exits_two_on_a_page_count_mismatch(tiny_packet: Path, tmp_path: Path, capsys) -> None:
    pdf = tmp_path / "tiny_mismatch.pdf"
    assert render_pdf.main([str(tiny_packet), str(pdf), "--expect", "9"]) == 2
    captured = capsys.readouterr()
    assert captured.out.strip() == "2"
    assert "expected 9 pages, got 2" in captured.err
