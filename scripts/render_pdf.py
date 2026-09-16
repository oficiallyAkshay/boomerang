#!/usr/bin/env python3
"""Render a packet HTML file to a Letter PDF, one receipt per page.

A headless Chrome, Edge or Chromium under print media, with page script
switched off. The renderer asks Playwright for the Google Chrome already on
the machine first, then Microsoft Edge, and only then a Chromium someone
downloaded through ``playwright install``. Nothing here needs that download:
either browser most people already have will do. ``BOOMERANG_BROWSER`` forces
one of the three when a run has to be pinned to a particular browser.

Before printing, a small script walks every ``.rsec`` in the packet, forces a
page break before it, and sizes the receipt body to the page. A receipt that
is nearly the size of the page is grown or shrunk to fill it. A receipt that
would have to be shrunk past the point of being readable is left at a readable
size and runs on to the next page instead, which is the one thing a reader
cannot recover from: a folio printed at a fifth of its size is in the packet
but cannot be read. So the result is a PDF with the summary first, then every
receipt starting on a page of its own and continuing onto the next page when
it is too long for one.

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

The render writes a page map beside the PDF, ``<pdf>.pages.json``: how many
pages the summary took and how many each receipt took. ``attach_pdf`` reads it
to post each folio behind the last page of its own receipt, which it cannot
work out from the page count alone once a receipt can run to two pages.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from pathlib import Path
from string import Template
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

# The print box of a Letter page: the paper less the margins below, at the
# print media scale. Measuring here is measuring what page.pdf will lay out,
# which the old 900px viewport was not: a section measured 852px wide and
# printed 691px wide, so every width the fit pass worked from was wrong.
VIEWPORT = {"width": 739, "height": 960}
REMOTE_SCHEMES = {"http", "https"}
MARGIN = {"top": "0.5in", "bottom": "0.5in", "left": "0.4in", "right": "0.4in"}

TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

# What one printed page holds, measured rather than derived: a section 936px
# tall prints on one page and a section 937px tall prints on two. The
# arithmetic figure, ten inches at 96 dots to the inch, is 960, and a pass that
# believed it would hand a receipt's last line a page of its own.
PAGE_HEIGHT = 936
# Left under a receipt that is sized to one page, so it does not print against
# the foot of the page.
PAGE_GAP = 24

# A receipt is never shrunk below this. Below about three quarters, the body
# text of a vendor email stops being readable at arm's length, and a receipt
# nobody can read is not in the packet in any sense that matters. A receipt
# that would need less is left readable and runs onto the next page instead.
MIN_SCALE = 0.7
# And never grown beyond this. A narrow vendor email, 600px wide because it was
# written for a phone, is grown to fill the printable width; past this the page
# is a little text in a lot of white.
MAX_SCALE = 1.3

# Measures each receipt body against one Letter page and sizes it to the page.
#
# Zoom rather than transform, because a transform leaves the layout box at its
# full size: the old pass widened the box to compensate, an image at
# max-width:100% grew to fill the wider box, and the section was then clipped
# back to the height the pass had planned for. Folios lost their lower half
# that way. Zoom is layout, so the box the section reserves is the box the
# reader sees. The width cap is rewritten into the zoomed box's own coordinate
# space for the same reason: a max-width read at face value inside a box zoomed
# to 1.3 is a third wider than the paper.
#
# A receipt that would have to go below MIN_SCALE keeps a readable size and
# runs on instead: the fixed height and the clipping come off, and the section
# is allowed to break. It still starts on a page of its own.
FIT_JS = Template("""
() => {
  const PAGE = $page_height;
  const GAP = $gap;
  const MIN_SCALE = $min_scale;
  const MAX_SCALE = $max_scale;
  const out = [];
  let index = 0;
  document.querySelectorAll('.rsec').forEach(sec => {
    index += 1;
    sec.style.pageBreakBefore = 'always';
    sec.style.pageBreakAfter = 'always';
    sec.style.pageBreakInside = 'auto';
    sec.style.marginTop = '0';
    sec.style.height = 'auto';
    sec.style.overflow = 'visible';
    const headEl = sec.querySelector('.rhead');
    const heading = headEl ? headEl.textContent.trim() : '';
    const head = headEl ? headEl.getBoundingClientRect().height : 0;
    const rc = sec.querySelector('.rc');
    let scale = 1;
    if (rc) {
      const avail = sec.clientWidth;
      const room = PAGE - head - GAP;
      rc.style.zoom = '1';
      rc.style.overflow = '';
      rc.style.maxWidth = avail + 'px';
      const byWidth = avail / rc.scrollWidth;
      const onePage = Math.min(byWidth, room / rc.scrollHeight);
      const flows = onePage < MIN_SCALE;
      scale = Math.min(flows ? byWidth : onePage, MAX_SCALE);
      rc.style.zoom = String(scale);
      rc.style.maxWidth = (avail / scale) + 'px';
      if (flows) {
        rc.style.overflow = 'visible';
      } else {
        sec.style.height = (head + rc.getBoundingClientRect().height + GAP) + 'px';
        sec.style.overflow = 'hidden';
      }
    }
    out.push({index: index, heading: heading, scale: scale});
  });
  return out;
}
""").substitute(
    page_height=PAGE_HEIGHT,
    gap=PAGE_GAP,
    min_scale=MIN_SCALE,
    max_scale=MAX_SCALE,
)

# Shows one receipt at a time, so each can be printed on its own and its pages
# counted. -1 shows none of them, which is what says how many pages the summary
# took; -2 puts the packet back together.
#
# The probe is a section a pixel tall that takes one page and no more, and it
# is shown behind whatever is being counted. It is there to catch the trailing
# space of the packet, the padding under the last thing on the page, which
# otherwise lands under the receipt being counted and can push it onto a page
# it does not take in the finished packet.
#
# Counting this way rather than dividing a measured height by the page height,
# because the height is not what a browser paginates. It will not cut a picture
# or a line of text in half, so it moves one whole to the next page and leaves
# the rest of a page empty, and a receipt measured at two and a half pages
# prints on four. Guessing cost a real packet the last forty words of a hotel
# confirmation. This asks the browser, which is the only thing that knows.
COUNT_JS = """
(only) => {
  const sections = Array.from(document.querySelectorAll('.rsec'));
  if (!sections.length) { return 0; }
  let probe = document.getElementById('boomerang-page-probe');
  if (!probe) {
    probe = document.createElement('section');
    probe.id = 'boomerang-page-probe';
    probe.style.cssText = 'page-break-before:always;height:1px';
    sections[0].parentNode.appendChild(probe);
  }
  sections.forEach((sec, at) => {
    sec.style.display = (only === -2 || at === only) ? '' : 'none';
  });
  probe.style.display = only === -2 ? 'none' : '';
  return sections.length;
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


def block_remote_requests(route) -> None:
    """Abort anything the page asks for over http or https, allow the rest."""
    if urlsplit(route.request.url).scheme.lower() in REMOTE_SCHEMES:
        route.abort()
        return
    route.continue_()


def page_map_path(pdf_path: Path) -> Path:
    """Where the page map for a rendered PDF lives.

    Beside the PDF and named after it, so a packet and its map travel together
    and two packets in one directory cannot be handed each other's map.
    """
    pdf_path = Path(pdf_path)
    return pdf_path.with_name(pdf_path.name + ".pages.json")


def _page_map(pdf_path: Path, sections: list[dict], pages: list[int]) -> dict:
    """The page map for a rendered PDF: the summary's pages, then each receipt's.

    The summary's share is what the PDF has left over once every receipt's
    pages are accounted for, rather than a second measurement that could
    disagree with the file on disk.
    """
    return {
        "summary_pages": page_count(pdf_path) - sum(pages),
        "pages": list(pages),
        "scales": [round(float(section["scale"]), 3) for section in sections],
    }


def _pdf_pages(page, count: int) -> int:
    """Print the packet with only receipt ``count`` showing, and count the pages.

    -1 shows none of them and the probe, -2 shows the whole packet. The PDF is
    never written out: it is printed to memory, counted, and dropped.
    """
    page.evaluate(COUNT_JS, count)
    printed = page.pdf(format="Letter", print_background=True, margin=MARGIN)
    return len(PdfReader(io.BytesIO(printed)).pages)


def _count_pages(page, sections: list[dict]) -> list[int]:
    """How many pages each receipt takes, asked of the browser one at a time.

    Each receipt is printed on its own behind the summary, which is exactly
    where it sits in the finished packet, so the count is the count. The run
    with no receipt showing at all is the baseline, the summary and the probe,
    and the difference from it is the receipt.
    """
    if not sections:
        return []
    behind = _pdf_pages(page, -1)
    return [_pdf_pages(page, at) - behind for at in range(len(sections))]


def _print_to_pdf(play, url: str, pdf_path: Path) -> tuple[str, list[dict], list[int]]:
    """Open the packet under print media, fit each receipt, and print to PDF.

    Returns the channel the browser came from, what the fit pass measured and
    how many pages each receipt took, because this is the one launch a render
    does and asking a second time would open a second browser to be told what
    the first one already knew.

    The context runs with page script off, so nothing the packet carries can
    execute. The fit pass is a ``page.evaluate``, which the driver runs
    regardless, so the layout work is unaffected: it walks every ``.rsec``,
    forces a page break before it, and sizes the receipt body to the page.

    The route handler is installed before the first navigation, so a remote
    image in a receipt never reaches the network at all.
    """
    browser, channel = launch_browser(play)
    try:
        context = browser.new_context(viewport=VIEWPORT, java_script_enabled=False)
        page = context.new_page()
        page.route("**/*", block_remote_requests)
        page.goto(url, wait_until="load")
        page.emulate_media(media="print")
        sections = page.evaluate(FIT_JS)
        pages = _count_pages(page, sections)
        page.evaluate(COUNT_JS, -2)
        page.pdf(path=str(pdf_path), format="Letter", print_background=True, margin=MARGIN)
    finally:
        browser.close()
    return channel, sections, pages


def render(html_path: Path, pdf_path: Path) -> tuple[int, str, dict]:
    """Render the packet at html_path to pdf_path.

    Returns the page count, the channel that rendered it, chrome, msedge or
    chromium, which is what a run prints so a reader knows which browser laid
    the packet out, and the page map. The map is also written beside the PDF,
    because the splice runs as its own command and cannot ask the render
    anything.
    """
    html_path = Path(html_path).resolve()
    pdf_path = Path(pdf_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    title = html_title(html_path)
    with sync_playwright() as play:
        channel, sections, pages = _print_to_pdf(play, html_path.as_uri(), pdf_path)
    _stamp_title(pdf_path, title)
    mapping = _page_map(pdf_path, sections, pages)
    page_map_path(pdf_path).write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")
    return page_count(pdf_path), channel, mapping


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a packet HTML file to a Letter PDF.")
    parser.add_argument("html", type=Path, help="packet HTML file")
    parser.add_argument("pdf", type=Path, help="PDF file to write")
    parser.add_argument("--expect", type=int, help="fail when the page count differs")
    args = parser.parse_args(argv)

    pages, channel, mapping = render(args.html, args.pdf)
    print(f"rendered with {channel}", file=sys.stderr)
    spans = zip(mapping["pages"], mapping["scales"], strict=True)
    for number, (count, scale) in enumerate(spans, start=1):
        if count > 1:
            print(
                f"receipt {number} spans {count} pages at scale {scale:.2f}",
                file=sys.stderr,
            )
    print(pages)
    counted = mapping["summary_pages"] + sum(mapping["pages"])
    if args.expect is not None and counted != args.expect:
        print(f"expected {args.expect} pages, got {counted}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
