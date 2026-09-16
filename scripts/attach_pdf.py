#!/usr/bin/env python3
"""Splice PDF receipt attachments into a rendered packet.

The packet renders one page per receipt. A receipt that arrived as a PDF, such
as a hotel folio, renders as a short card saying the attachment follows. This
script inserts the attachment's own pages right after that card page, so the
folio reads in place instead of living outside the packet.

Every rid is checked against ``build.RID_RE`` and the file it names has to
resolve to somewhere inside the resolved receipts directory. A rid arrives here
from a JSON file, which is the same trust as any other input, so a traversal or
a symlink out of the folder raises rather than splicing a stranger's PDF into
someone's packet.

The output carries a ``/BoomerangSpliced`` mark, and a packet that already
carries it is refused. Splicing a spliced packet would count the attachment
pages as receipt card pages and post every later folio to the wrong place, so
the second run stops instead of quietly shuffling the packet.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import build
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


def is_spliced(pdf_path: Path) -> bool:
    """True when this PDF already carries the splice mark."""
    return SPLICED_KEY in (PdfReader(str(pdf_path)).metadata or {})


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
    summary_pages = len(reader.pages) - len(receipts)
    if summary_pages < 1:
        raise ValueError(
            f"packet has {len(reader.pages)} pages for {len(receipts)} receipts, "
            "leaving no summary page"
        )

    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    offset = 0
    for index, receipt in enumerate(receipts):
        source = _receipt_pdf(receipts_dir, receipt.get("rid"))
        if not source.exists():
            continue
        attachment = _read_receipt(source, receipt["rid"])
        card_index = summary_pages + index + offset
        for step, page in enumerate(attachment.pages, start=1):
            writer.insert_page(page, card_index + step)
        offset += len(attachment.pages)

    writer.add_metadata({SPLICED_KEY: "1"})
    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    with open(out_pdf, "wb") as handle:
        writer.write(handle)
    return len(writer.pages)


def main(argv: list[str] | None = None) -> int:
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
