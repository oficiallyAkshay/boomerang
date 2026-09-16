#!/usr/bin/env python3
"""Render a packet HTML file to a Letter PDF, one receipt per page.

A headless Chrome, Edge or Chromium under print media, with page script
switched off. The renderer asks Playwright for the Google Chrome already on
the machine first, then Microsoft Edge, and only then a Chromium someone
downloaded through ``playwright install``. Nothing here needs that download:
either browser most people already have will do. ``BOOMERANG_BROWSER`` forces
one of the three when a run has to be pinned to a particular browser. Before
printing, a small script walks every ``.rsec`` in the packet, forces a page
break either side of it, and scales the receipt body down when it is taller or
wider than the space one Letter page leaves. The result is a PDF with the
summary on page one and exactly one receipt per page after it.

The context is created with ``java_script_enabled=False``. That stops anything
the page itself carries: a handler the cleaner somehow missed cannot fire, and
markup smuggled through a vendor receipt cannot rewrite an amount on the way to
the PDF. The fit pass still runs, because ``page.evaluate`` is driven from
outside the page and is not affected by the flag.

Every http and https request the page makes is aborted before it leaves. A
packet is built from files already on disk, so a receipt that still carries a
remote image is either a tracking pixel reporting that the expense report was
opened or a picture nobody vouched for. ``file:`` and ``data:`` continue, which
is how the packet, its images and its fonts load.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright
from pypdf import PdfReader, PdfWriter

MISSING_BROWSER = (
    "No Chrome, Edge or Chromium found. Install Google Chrome or Microsoft Edge, "
    "or run: uv run playwright install chromium"
)

# Tried in this order. The first two are browsers the machine already has;
# the third is the Playwright download, which is now the last resort rather
# than the requirement.
CHANNELS = ("chrome", "msedge", "chromium")
BROWSER_ENV = "BOOMERANG_BROWSER"

VIEWPORT = {"width": 900, "height": 1200}
REMOTE_SCHEMES = {"http", "https"}
MARGIN = {"top": "0.5in", "bottom": "0.5in", "left": "0.4in", "right": "0.4in"}

TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

# Measures each receipt body against the space one Letter page leaves and zooms
# it down when it overflows, in height or in width. 880px is the printable
# height inside the 0.5in top and bottom margins at the print media scale, and
# 720px is the printable width inside the 0.4in side margins, both kept a
# little under the true figure so rounding never costs a line.
#
# The width is a constant rather than the section's own client width because
# page.pdf lays the packet out at the paper width, not at the viewport width.
# A section measures 900px wide here and prints about 740px wide, so a table
# scaled to the measured width still lost its rightmost columns off the page.
# The width only constrains content that overflows its own box: anything that
# fits reflows into the narrower print width by itself.
#
# Zoom rather than transform, because a transform leaves the layout box at its
# full size: the old pass widened the box to compensate, an image at
# max-width:100% grew to fill the wider box, and the section was then clipped
# back to the height the pass had planned for. Folios lost their lower half
# that way. Zoom is layout, so the box the section reserves is the box the
# reader sees.
#
# The measurement runs more than once. Zoom widens the box in its own
# coordinate space, which lets an image at max-width:100% grow back toward its
# natural width, so one pass can leave a tall picture twice as tall as the
# page. Each pass multiplies the scale by what is still missing, and the loop
# stops as soon as the section fits.
FIT_JS = """
() => {
  const LIMIT = 880;
  const WIDTH_LIMIT = 720;
  const PASSES = 8;
  const CLOSE_ENOUGH = 0.995;
  const out = [];
  document.querySelectorAll('.rsec').forEach(sec => {
    sec.style.pageBreakBefore = 'always';
    sec.style.pageBreakAfter = 'always';
    sec.style.marginTop = '0';
    const rc = sec.querySelector('.rc');
    if (!rc) { return; }
    const headEl = sec.querySelector('.rhead');
    const head = headEl ? headEl.getBoundingClientRect().height : 0;
    const heading = headEl ? headEl.textContent.trim() : '';
    const availHeight = LIMIT - head - 40;
    let s = 1;
    for (let pass = 0; pass < PASSES; pass++) {
      const rect = rc.getBoundingClientRect();
      const h = Math.max(rect.height, rc.scrollHeight * s);
      const wide = rc.scrollWidth > rc.clientWidth + 1;
      const w = wide ? rc.scrollWidth * s : 0;
      const step = Math.min(
        1,
        availHeight / h,
        wide ? WIDTH_LIMIT / w : 1
      );
      if (step > CLOSE_ENOUGH) { break; }
      s = s * step;
      rc.style.zoom = String(s);
    }
    if (s < 1) {
      sec.style.height = (head + rc.getBoundingClientRect().height + 20) + 'px';
      sec.style.overflow = 'hidden';
    }
    out.push({
      heading: heading,
      scale: s,
      height: rc.getBoundingClientRect().height,
      budget: availHeight
    });
  });
  return out;
}
"""

# A receipt scaled below this is printed but is hard to read, so the run says so.
MIN_SCALE = 0.5


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


def wanted_channels() -> tuple[str, ...]:
    """The channels to try, in order, honouring ``BOOMERANG_BROWSER``.

    The variable pins a run to one browser and is not a hint: an unknown value
    is refused rather than quietly falling back to the usual order, because a
    run pinned to a browser that was never tried is worse than a run that
    stops.
    """
    forced = os.environ.get(BROWSER_ENV, "").strip().lower()
    if not forced:
        return CHANNELS
    if forced not in CHANNELS:
        raise RuntimeError(f"{BROWSER_ENV} must be one of {', '.join(CHANNELS)}, not {forced!r}")
    return (forced,)


def launch_browser(play):
    """Launch the first browser of the three this machine has.

    Returns the browser and the channel it came from. Raises the missing
    browser error when none of them launches.
    """
    last: PlaywrightError | None = None
    for channel in wanted_channels():
        try:
            return play.chromium.launch(channel=channel), channel
        except PlaywrightError as exc:
            last = exc
    raise RuntimeError(MISSING_BROWSER) from last


def browser_channel() -> str:
    """The channel that renders on this machine: chrome, msedge or chromium."""
    with sync_playwright() as play:
        browser, channel = launch_browser(play)
        browser.close()
        return channel


def block_remote_requests(route) -> None:
    """Abort anything the page asks for over http or https, allow the rest."""
    if urlsplit(route.request.url).scheme.lower() in REMOTE_SCHEMES:
        route.abort()
        return
    route.continue_()


def fit_sections(page) -> list[dict]:
    """Run the fit pass on an open page and return one measurement per section.

    Each entry carries the section's heading, the scale applied to its body,
    the height that body ended up at and the height it had to fit into.
    """
    return page.evaluate(FIT_JS)


def _warn_about_small_sections(measurements: list[dict]) -> None:
    """Say which receipts came out small enough to be hard to read."""
    for section in measurements:
        if section["scale"] < MIN_SCALE:
            heading = section["heading"] or "a receipt with no heading"
            print(
                f"render_pdf: {heading} was scaled to {section['scale']:.2f} to fit the page",
                file=sys.stderr,
            )


def _print_to_pdf(play, url: str, pdf_path: Path) -> None:
    """Open the packet under print media, fit each receipt, and print to PDF.

    The context runs with page script off, so nothing the packet carries can
    execute. The fit pass below is a ``page.evaluate``, which the driver runs
    regardless, so the layout work is unaffected.

    The route handler is installed before the first navigation, so a remote
    image in a receipt never reaches the network at all.
    """
    browser, _channel = launch_browser(play)
    try:
        context = browser.new_context(viewport=VIEWPORT, java_script_enabled=False)
        page = context.new_page()
        page.route("**/*", block_remote_requests)
        page.goto(url, wait_until="load")
        page.emulate_media(media="print")
        _warn_about_small_sections(fit_sections(page))
        page.pdf(path=str(pdf_path), format="Letter", print_background=True, margin=MARGIN)
    finally:
        browser.close()


def render(html_path: Path, pdf_path: Path) -> int:
    """Render the packet at html_path to pdf_path. Returns the page count."""
    html_path = Path(html_path).resolve()
    pdf_path = Path(pdf_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    title = html_title(html_path)
    with sync_playwright() as play:
        _print_to_pdf(play, html_path.as_uri(), pdf_path)
    _stamp_title(pdf_path, title)
    return page_count(pdf_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a packet HTML file to a Letter PDF.")
    parser.add_argument("html", type=Path, help="packet HTML file")
    parser.add_argument("pdf", type=Path, help="PDF file to write")
    parser.add_argument("--expect", type=int, help="fail when the page count differs")
    args = parser.parse_args(argv)

    pages = render(args.html, args.pdf)
    print(f"rendered with {browser_channel()}", file=sys.stderr)
    print(pages)
    if args.expect is not None and pages != args.expect:
        print(f"expected {args.expect} pages, got {pages}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
