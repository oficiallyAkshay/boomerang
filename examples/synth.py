#!/usr/bin/env python3
"""The three synthetic writers the fixture and the worked example both use.

A solid colour PNG, a minimal multi page PDF and one money formatter, none of
them needing an image or a PDF library. They live here rather than in either
caller because both callers write the same kind of receipt: the fixture
generator writes a folio and a photographed pass for the test suite, and
``build_example.py`` writes the same two for the committed example. Two copies
of a PDF writer is two chances for the example and the fixture to disagree
about what a folio looks like.

Nothing here is read from anywhere. Every byte is generated.
"""

from __future__ import annotations

import struct
import zlib


def png_bytes(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """A solid colour PNG, built without an image library."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    row = b"\x00" + bytes(rgb) * width
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(row * height, 9))
        + chunk(b"IEND", b"")
    )


def pdf_bytes(pages: list[list[str]]) -> bytes:
    """A minimal multi page PDF with one Helvetica text block per page."""
    objects: dict[int, bytes] = {}
    kids = " ".join(f"{5 + 2 * i} 0 R" for i in range(len(pages)))
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = f"<< /Type /Pages /Count {len(pages)} /Kids [{kids}] >>".encode()
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    for index, lines in enumerate(pages):
        body = ["BT", "/F1 12 Tf", "72 720 Td", "16 TL"]
        for line in lines:
            safe = line.replace("\\", "").replace("(", "").replace(")", "")
            body.append(f"({safe}) Tj T*")
        body.append("ET")
        stream = "\n".join(body).encode()
        content_num = 4 + 2 * index
        page_num = content_num + 1
        objects[content_num] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
        )
        objects[page_num] = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            "/Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {content_num} 0 R >>"
        ).encode()

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode() + objects[num] + b"\nendobj\n"
    start = len(out)
    size = max(objects) + 1
    out += f"xref\n0 {size}\n".encode() + b"0000000000 65535 f \n"
    for num in range(1, size):
        out += f"{offsets[num]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    return bytes(out)


def money(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"
