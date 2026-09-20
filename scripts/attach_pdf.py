#!/usr/bin/env python3
"""Splice PDF receipt attachments into a rendered packet.

Every receipt in the packet starts on a page of its own, and a long one runs
on to the next page. A receipt that arrived as a PDF, such as a hotel folio,
renders as a short card saying the attachment follows. This script inserts the
attachment's own pages right after the last page of that card's receipt, so the
folio reads in place instead of living outside the packet.

Which page that is comes from the page map ``render_pdf`` writes beside the
packet, ``<packet>.pages.json``. Counting it out instead, one page per receipt,
is what the splice used to do, and it is wrong the moment any receipt runs to
two pages: every folio after it lands a page early. Without a map the splice
still counts, but it refuses rather than guesses when the count does not
describe the file it was handed.

Every rid is checked against ``build.RID_RE`` and the file it names has to
resolve to somewhere inside the resolved receipts directory. A rid arrives here
from a JSON file, which is the same trust as any other input, so a traversal or
a symlink out of the folder raises rather than splicing a stranger's PDF into
someone's packet.

The output carries a ``/BoomerangSpliced`` mark, and a packet that already
carries it is refused. Splicing a spliced packet would count the attachment
pages as receipt card pages and post every later folio to the wrong place, so
the second run stops instead of quietly shuffling the packet.

The output is a new document, so it starts with no metadata at all. The one
entry carried across from the packet is ``/Title``, which ``render_pdf`` put
there from the packet's own ``<title>`` and which is what a reader sees in a
viewer's window and in a file listing. Nothing else is copied, and no author
is ever written: the traveller's name belongs on the page, where they put it,
and not in a document property that follows the file around.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import build
import render_pdf
from build import RID_RE
from pypdf import PdfReader, PdfWriter

SPLICED_KEY = "/BoomerangSpliced"


def _receipt_pdf(receipts_dir: Path, rid: object) -> Path:
    """The PDF path for a rid, checked for shape and for containment."""
    if not isinstance(rid, str) or not RID_RE.match(rid):
        raise ValueError(f"receipt id is not a valid id: {rid!r}")
    root = Path(receipts_dir).resolve()
    resolved = (root / f"{rid}.pdf").resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"receipt {rid} resolves outside the receipts directory")
    return resolved


def extract_text(pdf_path: Path) -> str:
    """All text in the PDF, pages joined with form feeds."""
    reader = PdfReader(str(pdf_path))
    return "\f".join(page.extract_text() or "" for page in reader.pages)


def _read_receipt(source: Path, rid: str) -> PdfReader:
    """Open one receipt PDF, saying which receipt it is when it will not open.

    pypdf raises its own errors with nothing but a byte offset in them, which
    tells the reader nothing about which of thirty receipts is the bad one.
    """
    try:
        return PdfReader(str(source))
    except Exception as exc:
        raise ValueError(f"receipt {rid}: pdf cannot be read") from exc


def _page_map(packet_pdf: Path, total: int, receipts: list) -> tuple[int, list[int]]:
    """The summary's pages and each receipt's, from the map or from counting.

    The map beside the packet is the render's own record of where each receipt
    went. Without one, every receipt is taken to be one page, which is what the
    packet was before a long receipt was allowed to run on; a packet whose page
    count does not fit that is refused rather than spliced into the wrong
    order.
    """
    side = render_pdf.page_map_path(packet_pdf)
    if side.is_file():
        mapping = json.loads(side.read_text(encoding="utf-8"))
        pages = [int(count) for count in mapping["pages"]]
        summary_pages = int(mapping["summary_pages"])
        if len(pages) != len(receipts) or total != summary_pages + sum(pages):
            raise ValueError(
                f"{side.name} describes {len(pages)} receipts over "
                f"{summary_pages + sum(pages)} pages, not {len(receipts)} over {total}"
            )
    else:
        pages = [1] * len(receipts)
        summary_pages = total - len(receipts)
    if summary_pages < 1:
        raise ValueError(
            f"packet has {total} pages for {len(receipts)} receipts, leaving no summary page"
        )
    return summary_pages, pages


def splice(packet_pdf: Path, data: dict, receipts_dir: Path, out_pdf: Path) -> int:
    """Insert each PDF receipt after its card page. Returns the final page count.

    The result is stamped ``/BoomerangSpliced``, and a packet that already
    carries that stamp is refused, so running this twice over the same file
    raises rather than inserting every folio a second time in the wrong place.
    """
    receipts_dir = Path(receipts_dir)
    reader = PdfReader(str(packet_pdf))
    if SPLICED_KEY in (reader.metadata or {}):
        raise ValueError(f"{Path(packet_pdf).name} has been spliced already")
    receipts = data.get("receipts", [])
    # Every rid is checked before anything is read or counted, so a packet is
    # never half spliced against a receipt list that names a file it should not.
    sources = [_receipt_pdf(receipts_dir, receipt.get("rid")) for receipt in receipts]
    summary_pages, per_receipt = _page_map(packet_pdf, len(reader.pages), receipts)

    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    offset = 0
    last_page = summary_pages - 1
    for index, receipt in enumerate(receipts):
        last_page += per_receipt[index]
        source = sources[index]
        if not source.exists():
            continue
        attachment = _read_receipt(source, receipt["rid"])
        for step, page in enumerate(attachment.pages, start=1):
            writer.insert_page(page, last_page + offset + step)
        offset += len(attachment.pages)

    metadata = {SPLICED_KEY: "1"}
    title = (reader.metadata or {}).get("/Title")
    if title:
        metadata["/Title"] = str(title)
    writer.add_metadata(metadata)
    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    with out_pdf.open("wb") as handle:
        writer.write(handle)
    return len(writer.pages)


def main(argv: list[str] | None = None) -> int:
    """Splice PDF receipts into a rendered packet."""
    parser = argparse.ArgumentParser(description="Splice PDF receipts into a rendered packet.")
    parser.add_argument("packet", type=Path, help="rendered packet PDF")
    parser.add_argument("data", type=Path, help="expense_data.json")
    parser.add_argument("--receipts", type=Path, required=True, help="receipts directory")
    parser.add_argument("--out", type=Path, required=True, help="PDF file to write")
    args = parser.parse_args(argv)

    data = json.loads(args.data.read_text(encoding="utf-8"))
    problems = build.validate(data, args.receipts)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 2
    print(splice(args.packet, data, args.receipts, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
