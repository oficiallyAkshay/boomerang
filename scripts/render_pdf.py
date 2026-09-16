#!/usr/bin/env python3
"""Render a packet HTML file to a Letter PDF, one receipt per page.

Headless Chromium under print media, with page script switched off. Before
printing, a small script walks every ``.rsec`` in the packet, forces a page
break either side of it, and scales the receipt body down when it is taller
than the space one Letter page leaves. The result is a PDF with the summary on
page one and exactly one receipt per page after it.

The context is created with ``java_script_enabled=False``. That stops anything
the page itself carries: a handler the cleaner somehow missed cannot fire, and
markup smuggled through a vendor receipt cannot rewrite an amount on the way to
the PDF. The fit pass still runs, because ``page.evaluate`` is driven from
outside the page and is not affected by the flag.
"""

from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright
from pypdf import PdfReader, PdfWriter

MISSING_BROWSER = (
    "Chromium is not installed for playwright. Run: uv run playwright install chromium"
)

VIEWPORT = {"width": 900, "height": 1200}
MARGIN = {"top": "0.5in", "bottom": "0.5in", "left": "0.4in", "right": "0.4in"}

TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

# Measures each receipt body against the space one Letter page leaves and scales
# it down from the top left corner when it overflows. 880px is the printable
# height inside the 0.5in top and bottom margins at the print media scale.
FIT_JS = """
() => {
  const LIMIT = 880;
  document.querySelectorAll('.rsec').forEach(sec => {
    sec.style.pageBreakBefore = 'always';
    sec.style.pageBreakAfter = 'always';
    sec.style.marginTop = '0';
    const rc = sec.querySelector('.rc');
    if (!rc) { return; }
    const headEl = sec.querySelector('.rhead');
    const head = headEl ? headEl.getBoundingClientRect().height : 0;
    const h = rc.scrollHeight;
    const avail = LIMIT - head - 40;
    if (h > avail) {
      const s = avail / h;
      rc.style.transform = 'scale(' + s + ')';
      rc.style.transformOrigin = 'top left';
      rc.style.width = (100 / s) + '%';
      rc.style.height = h + 'px';
      sec.style.height = (head + h * s + 20) + 'px';
      sec.style.overflow = 'hidden';
    }
  });
}
"""


def html_title(html_path: Path) -> str:
    """The text of the first <title> element, or an empty string."""
    text = Path(html_path).read_text(encoding="utf-8", errors="replace")
    match = TITLE_RE.search(text)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def page_count(pdf_path: Path) -> int:
    """How many pages the PDF holds."""
    return len(PdfReader(str(pdf_path)).pages)


def _stamp_title(pdf_path: Path, title: str) -> None:
    """Rewrite the PDF with the given title and no author."""
    raw = Path(pdf_path).read_bytes()
    reader = PdfReader(io.BytesIO(raw))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    if title:
        writer.add_metadata({"/Title": title})
    with open(pdf_path, "wb") as handle:
        writer.write(handle)


def _print_to_pdf(play, url: str, pdf_path: Path) -> None:
    """Open the packet under print media, fit each receipt, and print to PDF.

    The context runs with page script off, so nothing the packet carries can
    execute. The fit pass below is a ``page.evaluate``, which the driver runs
    regardless, so the layout work is unaffected.
    """
    browser = play.chromium.launch()
    try:
        context = browser.new_context(viewport=VIEWPORT, java_script_enabled=False)
        page = context.new_page()
        page.goto(url, wait_until="load")
        page.emulate_media(media="print")
        page.evaluate(FIT_JS)
        page.pdf(path=str(pdf_path), format="Letter", print_background=True, margin=MARGIN)
    finally:
        browser.close()


def render(html_path: Path, pdf_path: Path) -> int:
    """Render the packet at html_path to pdf_path. Returns the page count."""
    html_path = Path(html_path).resolve()
    pdf_path = Path(pdf_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    title = html_title(html_path)
    try:
        with sync_playwright() as play:
            _print_to_pdf(play, html_path.as_uri(), pdf_path)
    except PlaywrightError as exc:
        message = str(exc).lower()
        if "executable doesn't exist" in message or "playwright install" in message:
            raise RuntimeError(MISSING_BROWSER) from exc
        raise
    _stamp_title(pdf_path, title)
    return page_count(pdf_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a packet HTML file to a Letter PDF.")
    parser.add_argument("html", type=Path, help="packet HTML file")
    parser.add_argument("pdf", type=Path, help="PDF file to write")
    parser.add_argument("--expect", type=int, help="fail when the page count differs")
    args = parser.parse_args(argv)

    pages = render(args.html, args.pdf)
    print(pages)
    if args.expect is not None and pages != args.expect:
        print(f"expected {args.expect} pages, got {pages}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
