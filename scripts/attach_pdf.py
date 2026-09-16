#!/usr/bin/env python3
"""Splice PDF receipt attachments into a rendered packet.

The packet renders one page per receipt. A receipt that arrived as a PDF, such
as a hotel folio, renders as a short card saying the attachment follows. This
script inserts the attachment's own pages right after that card page, so the
folio reads in place instead of living outside the packet.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pypdf import PdfReader, PdfWriter


def extract_text(pdf_path: Path) -> str:
    """All text in the PDF, pages joined with form feeds."""
    reader = PdfReader(str(pdf_path))
    return "\f".join(page.extract_text() or "" for page in reader.pages)


def splice(packet_pdf: Path, data: dict, receipts_dir: Path, out_pdf: Path) -> int:
    """Insert each PDF receipt after its card page. Returns the final page count."""
    receipts_dir = Path(receipts_dir)
    reader = PdfReader(str(packet_pdf))
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
        source = receipts_dir / f"{receipt['rid']}.pdf"
        if not source.exists():
            continue
        attachment = PdfReader(str(source))
        card_index = summary_pages + index + offset
        for step, page in enumerate(attachment.pages, start=1):
            writer.insert_page(page, card_index + step)
        offset += len(attachment.pages)

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
    print(splice(args.packet, data, args.receipts, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
