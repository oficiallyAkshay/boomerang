"""Tests for the packet PDF render.

These need a real headless browser: the Google Chrome or Microsoft Edge
already on the machine, or a Playwright Chromium if someone downloaded one.
They do not skip when none is there; they fail with the message that says what
to install.

One test here is a tampering probe. It renders a packet whose receipt carries
markup written to rewrite the summary amounts, and reads the finished PDF back
to prove the amounts are the real ones. It is the end of the chain the cleaner
starts: even if hostile markup reached a packet file, the render runs no page
script, so it cannot change a number on its way to the PDF.
"""

from __future__ import annotations

import json
import runpy
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import build
import pytest
import render_pdf
from fixtures.make_fixture import png_bytes
from playwright.sync_api import Error as PlaywrightError
from pypdf import PdfReader

REPO_ROOT = Path(__file__).resolve().parents[1]
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
        (
            "<style>body{font-family:Helvetica,Arial,sans-serif;margin:0}"
            ".rhead{font-size:15px;font-weight:700;padding:4px 0}</style>"
        ),
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
    pages, channel, page_map = render_pdf.render(html, pdf)
    return {"html": html, "pdf": pdf, "pages": pages, "channel": channel, "map": page_map}


def test_the_summary_is_one_page_and_every_receipt_has_its_own(
    rendered: dict, fixture_data: dict
) -> None:
    """One page each is no longer the rule, but a page of one's own still is."""
    page_map = rendered["map"]
    assert page_map["summary_pages"] == 1
    assert len(page_map["pages"]) == len(fixture_data["receipts"])
    assert all(count >= 1 for count in page_map["pages"])
    assert rendered["pages"] == page_map["summary_pages"] + sum(page_map["pages"])


def test_the_page_map_is_written_beside_the_pdf(rendered: dict) -> None:
    """The splice runs as its own command and reads the map off disk."""
    side = render_pdf.page_map_path(rendered["pdf"])
    assert side.name == f"{rendered['pdf'].name}.pages.json"
    assert json.loads(side.read_text(encoding="utf-8")) == rendered["map"]


def test_the_page_map_counts_the_pages_the_pdf_really_has(rendered: dict) -> None:
    """Measured against pypdf, which is the only count that matters."""
    page_map = rendered["map"]
    counted = page_map["summary_pages"] + sum(page_map["pages"])
    assert counted == render_pdf.page_count(rendered["pdf"])


def test_every_page_is_letter(rendered: dict) -> None:
    for page in PdfReader(str(rendered["pdf"])).pages:
        assert abs(float(page.mediabox.width) - LETTER_WIDTH) <= TOLERANCE
        assert abs(float(page.mediabox.height) - LETTER_HEIGHT) <= TOLERANCE


def test_an_oversized_receipt_runs_on_rather_than_shrinking(rendered: dict) -> None:
    """A 3000px body takes the pages it needs and stays the size it is.

    It used to be squeezed onto one page at a third of its size. Now it starts
    on its own page, as every receipt does, and runs on from there: the top
    mark is on the first of its pages and the bottom mark on the last.
    """
    page_map = rendered["map"]
    assert page_map["pages"][0] > 1
    assert page_map["scales"][0] >= render_pdf.MIN_SCALE

    pages = [page.extract_text() or "" for page in PdfReader(str(rendered["pdf"])).pages]
    carrying_top = [i for i, text in enumerate(pages) if TOP_MARK in text]
    carrying_bottom = [i for i, text in enumerate(pages) if BOTTOM_MARK in text]
    assert carrying_top == [1]
    assert carrying_bottom == [page_map["summary_pages"] + page_map["pages"][0] - 1]


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


class FakeBrowser:
    """Enough of a browser for the launcher tests: it can be closed."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeChromium:
    """A ``play.chromium`` that only launches the one channel it was given.

    ``tried`` records every channel asked for, in order, so a test can say
    which browsers were looked for as well as what the failure said.
    """

    def __init__(self, working: str | None = None) -> None:
        self.working = working
        self.tried: list[str | None] = []

    def launch(self, channel: str | None = None) -> FakeBrowser:
        self.tried.append(channel)
        if channel == self.working:
            return FakeBrowser()
        raise PlaywrightError(f"Chromium distribution {channel!r} is not found")


class FakePlaywright:
    """Stands in for the ``sync_playwright()`` context manager."""

    def __init__(self, chromium: FakeChromium) -> None:
        self.chromium = chromium

    def __call__(self) -> FakePlaywright:
        return self

    def __enter__(self) -> FakePlaywright:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def test_no_browser_at_all_names_chrome_edge_and_the_install_command(
    monkeypatch, tmp_path: Path
) -> None:
    """Every channel fails, so the run stops and says what to install."""
    chromium = FakeChromium()
    monkeypatch.delenv(render_pdf.BROWSER_ENV, raising=False)
    monkeypatch.setattr(render_pdf, "sync_playwright", FakePlaywright(chromium))
    with pytest.raises(RuntimeError) as caught:
        render_pdf.render(_stub_html(tmp_path), tmp_path / "packet.pdf")
    message = str(caught.value)
    assert message == (
        "No Chrome, Edge or Chromium found. Install Google Chrome or Microsoft Edge, "
        "or run: uv run playwright install chromium"
    )
    assert chromium.tried == ["chrome", "msedge", "chromium"]


def test_edge_is_used_when_chrome_is_missing(monkeypatch) -> None:
    """The order is Chrome, then Edge, then a downloaded Chromium."""
    chromium = FakeChromium(working="msedge")
    monkeypatch.delenv(render_pdf.BROWSER_ENV, raising=False)
    browser, channel = render_pdf.launch_browser(FakePlaywright(chromium))
    assert channel == "msedge"
    assert isinstance(browser, FakeBrowser)
    assert chromium.tried == ["chrome", "msedge"]


def test_the_env_override_pins_the_run_to_one_browser(monkeypatch) -> None:
    """BOOMERANG_BROWSER skips the search and asks for that browser only."""
    chromium = FakeChromium(working="chromium")
    monkeypatch.setenv(render_pdf.BROWSER_ENV, "chromium")
    _browser, channel = render_pdf.launch_browser(FakePlaywright(chromium))
    assert channel == "chromium"
    assert chromium.tried == ["chromium"]


def test_an_unknown_env_override_is_refused(monkeypatch) -> None:
    monkeypatch.setenv(render_pdf.BROWSER_ENV, "safari")
    with pytest.raises(RuntimeError, match="BOOMERANG_BROWSER must be one of"):
        render_pdf.wanted_channels()


def test_a_render_names_the_browser_that_did_it(rendered: dict) -> None:
    """One launch per render, and the channel it came from comes back with it."""
    assert rendered["channel"] in render_pdf.CHANNELS


def test_other_playwright_errors_are_not_reworded(monkeypatch, tmp_path: Path) -> None:
    def explode() -> None:
        raise PlaywrightError("navigation failed")

    monkeypatch.setattr(render_pdf, "sync_playwright", explode)
    with pytest.raises(PlaywrightError, match="navigation failed"):
        render_pdf.render(_stub_html(tmp_path), tmp_path / "packet.pdf")


@pytest.fixture(scope="module")
def tiny_packet(tmp_path_factory) -> Path:
    """One short receipt, no title element, for the fast CLI runs."""
    out = tmp_path_factory.mktemp("tiny")
    target = out / "tiny.html"
    target.write_text(
        "<html><body>"
        '<div class="summary">Trip summary table goes here.</div>'
        '<section class="rsec"><div class="rhead">The only receipt</div>'
        '<div class="rc"><p>Receipt body for the only receipt.</p></div></section>'
        "</body></html>",
        encoding="utf-8",
    )
    return target


def test_cli_prints_the_page_count_and_names_the_browser(
    tiny_packet: Path, tmp_path: Path, capsys
) -> None:
    pdf = tmp_path / "tiny.pdf"
    assert render_pdf.main([str(tiny_packet), str(pdf), "--expect", "2"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "2"
    named = captured.err.strip().splitlines()[-1]
    assert named in [f"rendered with {name}" for name in render_pdf.CHANNELS]
    metadata = PdfReader(str(pdf)).metadata or {}
    assert "/Title" not in metadata


def test_cli_exits_two_on_a_page_count_mismatch(tiny_packet: Path, tmp_path: Path, capsys) -> None:
    pdf = tmp_path / "tiny_mismatch.pdf"
    assert render_pdf.main([str(tiny_packet), str(pdf), "--expect", "9"]) == 2
    captured = capsys.readouterr()
    assert captured.out.strip() == "2"
    assert "expected 9 pages, got 2" in captured.err


# ------------------------------------------------------------- no fetching


class CountingHandler(BaseHTTPRequestHandler):
    """Records every path asked for, so the test can prove none was."""

    # http.server builds a new handler per request, so the recording has to
    # live on the class to survive across requests within one test.
    asked: list[str] = []  # noqa: RUF012

    def do_GET(self) -> None:
        CountingHandler.asked.append(self.path)
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        return


@pytest.fixture()
def counting_server():
    """A local server that answers nothing and remembers being asked."""
    CountingHandler.asked = []
    server = HTTPServer(("127.0.0.1", 0), CountingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_the_render_never_reaches_out_over_http(counting_server, tmp_path: Path) -> None:
    """A tracking pixel in a receipt is not fetched, and the packet still renders."""
    port = counting_server.server_address[1]
    remote = f"http://127.0.0.1:{port}/pixel.png"
    packet = tmp_path / "packet.html"
    packet.write_text(
        "<html><head><title>Remote</title></head><body>"
        '<div class="summary">Summary</div>'
        '<section class="rsec"><div class="rhead">A receipt</div>'
        f'<div class="rc"><p>A receipt body</p><img src="{remote}"></div></section>'
        "</body></html>",
        encoding="utf-8",
    )
    pdf = tmp_path / "packet.pdf"
    assert render_pdf.render(packet, pdf)[0] == 2
    assert CountingHandler.asked == []
    assert "A receipt body" in (PdfReader(str(pdf)).pages[1].extract_text() or "")


# ------------------------------------------------------------ nothing cropped


def wide_table_packet(target: Path, columns: int = 25, width: int = 5000) -> Path:
    """One section holding a table far wider than any page."""
    cells = "".join(
        f'<td style="min-width:{width // columns}px;font-size:14px">Col{index}</td>'
        for index in range(columns)
    )
    target.write_text(
        '<html><head><title>Wide</title></head><body style="margin:0">'
        '<div class="summary">Summary</div>'
        '<section class="rsec"><div class="rhead">A very wide receipt</div>'
        f'<div class="rc" style="overflow:auto">'
        f'<table style="width:{width}px;table-layout:fixed"><tr>{cells}</tr></table>'
        "</div></section></body></html>",
        encoding="utf-8",
    )
    return target


def test_a_table_wider_than_the_page_keeps_its_rightmost_cell(tmp_path: Path) -> None:
    """The fit pass scales on width as well as height, so nothing is cut off."""
    packet = wide_table_packet(tmp_path / "wide.html")
    pdf = tmp_path / "wide.pdf"
    assert render_pdf.render(packet, pdf)[0] == 2
    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf)).pages)
    assert "Col0" in text
    assert "Col24" in text


def tall_image_packet(target: Path) -> Path:
    """A 1700 by 6800 receipt image, the shape a phone screenshot of a folio takes."""
    (target.parent / "tall.png").write_bytes(png_bytes(1700, 6800, (28, 92, 148)))
    target.write_text(
        "<html><head><title>Tall</title>"
        "<style>body{margin:0}.rc{overflow:auto;padding:16px}.rc img{max-width:100%}"
        ".rhead{font-size:15px;padding:12px 16px}</style></head><body>"
        '<div class="summary">Summary</div>'
        '<section class="rsec"><div class="rhead">A tall image receipt</div>'
        '<div class="rc"><img src="tall.png"></div></section>'
        "</body></html>",
        encoding="utf-8",
    )
    return target


def measure(packet: Path) -> list[dict]:
    """Open the packet the way the renderer does and run the real fit pass."""
    with render_pdf.sync_playwright() as play:
        browser, _channel = render_pdf.launch_browser(play)
        try:
            context = browser.new_context(viewport=render_pdf.VIEWPORT, java_script_enabled=False)
            page = context.new_page()
            page.route("**/*", render_pdf.block_remote_requests)
            page.goto(packet.resolve().as_uri(), wait_until="load")
            page.emulate_media(media="print")
            return page.evaluate(render_pdf.FIT_JS)
        finally:
            browser.close()


def test_a_tall_image_receipt_stays_readable_and_runs_on(tmp_path: Path) -> None:
    """An image at max-width:100% used to grow back and be cropped in half.

    It is not scaled to a fifth to reach the foot of one page either. It prints
    at the width of the page and takes the pages it takes.
    """
    packet = tall_image_packet(tmp_path / "tall.html")
    pdf = tmp_path / "tall.pdf"
    sections = measure(packet)
    assert len(sections) == 1
    assert sections[0]["scale"] >= render_pdf.MIN_SCALE

    pages, _channel, page_map = render_pdf.render(packet, pdf)
    assert page_map["pages"] == [pages - page_map["summary_pages"]]
    assert page_map["pages"][0] > 1


def small_packet(target: Path) -> Path:
    """A receipt that already fits, so the fit pass has nothing to do."""
    target.write_text(
        "<html><head><title>Small</title></head><body>"
        '<section class="rsec"><div class="rhead">A short receipt</div>'
        '<div class="rc"><p>Two lines and no more.</p></div></section>'
        "</body></html>",
        encoding="utf-8",
    )
    return target


def test_a_receipt_that_needs_no_scaling_is_left_alone(tmp_path: Path) -> None:
    sections = measure(small_packet(tmp_path / "small.html"))
    assert sections[0]["scale"] == 1


# ------------------------------------------------------- long, short and wide


def tall_table_packet(target: Path, rows: int = 200) -> Path:
    """A receipt about 6000px tall: a table of rows, the shape a folio takes."""
    body = "".join(
        f"<tr><td>Row {number}</td><td>Line item number {number}</td><td>$ {number}.00</td></tr>"
        for number in range(rows)
    )
    target.write_text(
        "<html><head><title>Long</title>"
        "<style>body{margin:0}.rhead{font-size:15px;padding:12px 16px}"
        ".rc{padding:16px;width:fit-content;max-width:100%;margin:0 auto;box-sizing:border-box}"
        "td{font-size:14px;padding:4px 8px}</style></head><body>"
        '<div class="summary">Summary</div>'
        '<section class="rsec"><div class="rhead">A very long receipt</div>'
        f'<div class="rc"><p>{TOP_MARK}</p><table>{body}</table>'
        f"<p>{BOTTOM_MARK}</p></div></section></body></html>",
        encoding="utf-8",
    )
    return target


def test_a_long_receipt_runs_on_at_a_readable_size(tmp_path: Path) -> None:
    """The receipt this whole pass exists for: long, and nowhere near legible.

    Not at the scale that would have fitted it onto one page.
    """
    packet = tall_table_packet(tmp_path / "long.html")
    pdf = tmp_path / "long.pdf"
    pages, _channel, page_map = render_pdf.render(packet, pdf)

    assert page_map["summary_pages"] == 1
    assert page_map["pages"] == [pages - 1]
    assert page_map["pages"][0] >= 2
    assert page_map["scales"][0] >= render_pdf.MIN_SCALE

    text = [page.extract_text() or "" for page in PdfReader(str(pdf)).pages]
    assert TOP_MARK in text[1]
    assert "Row 199" in text[pages - 1]
    assert BOTTOM_MARK in text[pages - 1]


def narrow_receipt_packet(target: Path, receipts_dir: Path) -> Path:
    """The real builder, over a receipt written for a phone screen.

    600px wide is what a vendor's mail is laid out at, and on Letter that used
    to print as a column up the left of the page with an inch of white either
    side of it.
    """
    receipts_dir.mkdir(parents=True, exist_ok=True)
    (receipts_dir / "narrow.html").write_text(
        '<table style="width:600px"><tr><td style="font-size:13px">'
        "A receipt written for a phone screen.</td></tr></table>",
        encoding="utf-8",
    )
    data = {
        "company": "Northwind Systems",
        "trip": "AUS to SEA onsite",
        "traveler": "A traveler",
        "days": [
            {"label": "Monday", "items": [{"desc": "Bus fare", "amt": 3.25, "rid": "narrow"}]}
        ],
        "receipts": [{"rid": "narrow", "title": "A narrow receipt", "vendor": "generic"}],
    }
    target.write_text(build.render_packet(data, receipts_dir), encoding="utf-8")
    return target


def test_a_narrow_receipt_is_grown_to_fill_the_printable_width(tmp_path: Path) -> None:
    """Measured on the card itself, against the width the page gives it."""
    packet = narrow_receipt_packet(tmp_path / "narrow.html", tmp_path / "receipts")
    with render_pdf.sync_playwright() as play:
        browser, _channel = render_pdf.launch_browser(play)
        try:
            context = browser.new_context(viewport=render_pdf.VIEWPORT, java_script_enabled=False)
            page = context.new_page()
            page.route("**/*", render_pdf.block_remote_requests)
            page.goto(packet.resolve().as_uri(), wait_until="load")
            page.emulate_media(media="print")
            sections = page.evaluate(render_pdf.FIT_JS)
            box = page.evaluate(
                """() => {
                  const sec = document.querySelector('.rsec');
                  return {
                    card: sec.querySelector('.rc').getBoundingClientRect().width,
                    page: sec.clientWidth
                  };
                }"""
            )
        finally:
            browser.close()

    assert sections[0]["scale"] > 1
    assert sections[0]["scale"] <= render_pdf.MAX_SCALE
    assert box["card"] >= box["page"] - TOLERANCE
    assert box["card"] <= box["page"] + TOLERANCE


# ------------------------------------------------------- what the run reports


def test_the_cli_names_every_receipt_that_runs_to_more_than_one_page(
    tmp_path: Path, capsys
) -> None:
    packet = tall_table_packet(tmp_path / "long.html")
    pdf = tmp_path / "long.pdf"
    assert render_pdf.main([str(packet), str(pdf)]) == 0
    captured = capsys.readouterr()
    pages = json.loads(render_pdf.page_map_path(pdf).read_text(encoding="utf-8"))
    scale = pages["scales"][0]
    assert f"receipt 1 spans {pages['pages'][0]} pages at scale {scale:.2f}" in captured.err
    assert captured.out.strip() == str(pages["summary_pages"] + sum(pages["pages"]))


def test_a_packet_with_no_receipts_at_all_maps_to_the_summary(tmp_path: Path) -> None:
    """A summary and nothing else: every page of it is the summary's."""
    packet = tmp_path / "bare.html"
    packet.write_text(
        "<html><head><title>Bare</title></head><body>"
        '<div class="summary">A trip with no receipts yet.</div></body></html>',
        encoding="utf-8",
    )
    pages, _channel, page_map = render_pdf.render(packet, tmp_path / "bare.pdf")
    assert page_map == {"summary_pages": pages, "pages": [], "scales": []}


def test_a_packet_of_short_receipts_says_nothing_about_spans(tmp_path: Path, capsys) -> None:
    assert (
        render_pdf.main([str(small_packet(tmp_path / "small.html")), str(tmp_path / "s.pdf")]) == 0
    )
    assert "spans" not in capsys.readouterr().err


def test_running_the_file_as_a_script_hits_the_main_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``python scripts/render_pdf.py`` is the documented command line, not just the function."""
    packet = small_packet(tmp_path / "small.html")
    pdf = tmp_path / "small.pdf"
    monkeypatch.setattr(sys, "argv", ["render_pdf.py", str(packet), str(pdf)])
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(REPO_ROOT / "scripts" / "render_pdf.py"), run_name="__main__")
    assert exc_info.value.code == 0
    assert pdf.exists()
