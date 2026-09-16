"""Tests for splicing PDF receipt attachments into a rendered packet.

The packet under test comes from the same local helper the render tests use, so
these exercise the real page geometry rather than a hand built PDF.
"""

from __future__ import annotations

import json
from pathlib import Path

import attach_pdf
import pytest
import render_pdf
from fixtures.make_fixture import pdf_bytes
from test_render_pdf import write_packet

FOLIO_MARK_ONE = "folio page 1 of 2"
FOLIO_MARK_TWO = "folio page 2 of 2"


@pytest.fixture(scope="module")
def fixture_data(fixture_dir: Path) -> dict:
    return json.loads((fixture_dir / "expense_data.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def packet(fixture_dir: Path, fixture_data: dict, tmp_path_factory) -> dict:
    out = tmp_path_factory.mktemp("attach")
    html = write_packet(out / "packet.html", fixture_data, fixture_dir / "receipts")
    pdf = out / "packet.pdf"
    pages = render_pdf.render(html, pdf)
    return {"dir": out, "pdf": pdf, "pages": pages}


def _pdf_receipt_indexes(data: dict, receipts_dir: Path) -> list[int]:
    return [
        index
        for index, receipt in enumerate(data["receipts"])
        if (receipts_dir / f"{receipt['rid']}.pdf").exists()
    ]


def test_extract_text_returns_the_folio_text(fixture_dir: Path, fixture_data: dict) -> None:
    receipts_dir = fixture_dir / "receipts"
    index = _pdf_receipt_indexes(fixture_data, receipts_dir)[0]
    rid = fixture_data["receipts"][index]["rid"]
    text = attach_pdf.extract_text(receipts_dir / f"{rid}.pdf")
    assert FOLIO_MARK_ONE in text
    assert FOLIO_MARK_TWO in text
    assert text.count("\f") == 1


def test_splice_adds_the_folio_pages_right_after_its_card(
    packet: dict, fixture_dir: Path, fixture_data: dict, tmp_path: Path
) -> None:
    receipts_dir = fixture_dir / "receipts"
    out = tmp_path / "final.pdf"
    final = attach_pdf.splice(packet["pdf"], fixture_data, receipts_dir, out)
    assert final == packet["pages"] + 2

    summary_pages = packet["pages"] - len(fixture_data["receipts"])
    assert summary_pages == 1
    card_index = summary_pages + _pdf_receipt_indexes(fixture_data, receipts_dir)[0]

    pages = attach_pdf.extract_text(out).split("\f")
    assert FOLIO_MARK_ONE in pages[card_index + 1]
    assert FOLIO_MARK_TWO in pages[card_index + 2]
    assert FOLIO_MARK_ONE not in pages[card_index]


def test_the_output_is_stamped_and_a_second_splice_is_refused(
    packet: dict, fixture_dir: Path, fixture_data: dict, tmp_path: Path
) -> None:
    """Splicing a spliced packet would post every folio to the wrong page."""
    receipts_dir = fixture_dir / "receipts"
    once = tmp_path / "once.pdf"
    attach_pdf.splice(packet["pdf"], fixture_data, receipts_dir, once)

    assert attach_pdf.is_spliced(once) is True
    assert attach_pdf.is_spliced(packet["pdf"]) is False

    twice = tmp_path / "twice.pdf"
    with pytest.raises(ValueError, match="spliced already"):
        attach_pdf.splice(once, fixture_data, receipts_dir, twice)
    assert not twice.exists()


def test_a_receipt_pdf_that_will_not_open_names_the_receipt(packet: dict, tmp_path: Path) -> None:
    receipts_dir = tmp_path / "receipts"
    receipts_dir.mkdir()
    (receipts_dir / "torn.pdf").write_bytes(b"%PDF-1.4 and then nothing at all")
    data = {"receipts": [{"rid": "torn", "title": "A folio"}]}
    with pytest.raises(ValueError, match="receipt torn: pdf cannot be read"):
        attach_pdf.splice(packet["pdf"], data, receipts_dir, tmp_path / "final.pdf")


def test_splice_keeps_two_attachments_in_place(tmp_path: Path) -> None:
    """Two PDF receipts, so the second insertion has to allow for the first."""
    receipts_dir = tmp_path / "receipts"
    receipts_dir.mkdir()
    (receipts_dir / "alpha.pdf").write_bytes(
        pdf_bytes([["Alpha folio sheet one"], ["Alpha folio sheet two"]])
    )
    (receipts_dir / "omega.pdf").write_bytes(pdf_bytes([["Omega folio single sheet"]]))
    (receipts_dir / "middle.html").write_text("<p>a card receipt</p>", encoding="utf-8")
    data = {
        "receipts": [
            {"rid": "alpha", "title": "Folio alpha"},
            {"rid": "middle", "title": "Card in between"},
            {"rid": "omega", "title": "Folio omega"},
        ]
    }

    html = write_packet(tmp_path / "packet.html", data, receipts_dir)
    before = render_pdf.render(html, tmp_path / "packet.pdf")
    assert before == 4

    out = tmp_path / "final.pdf"
    assert attach_pdf.splice(tmp_path / "packet.pdf", data, receipts_dir, out) == before + 3

    pages = attach_pdf.extract_text(out).split("\f")
    assert "Folio alpha" in pages[1]
    assert "Alpha folio sheet one" in pages[2]
    assert "Alpha folio sheet two" in pages[3]
    assert "Card in between" in pages[4]
    assert "Folio omega" in pages[5]
    assert "Omega folio single sheet" in pages[6]


def test_splice_rejects_a_packet_with_no_summary_page(packet: dict, tmp_path: Path) -> None:
    crowded = {"receipts": [{"rid": f"r{n}"} for n in range(packet["pages"] + 3)]}
    with pytest.raises(ValueError, match="no summary page"):
        attach_pdf.splice(packet["pdf"], crowded, tmp_path, tmp_path / "final.pdf")


@pytest.mark.parametrize("rid", ["../x", "a/b", "", "x" * 65, "has space", 7, None])
def test_splice_refuses_a_rid_that_is_not_an_id(packet: dict, tmp_path: Path, rid: object) -> None:
    data = {"receipts": [{"rid": rid, "title": "Folio"}]}
    with pytest.raises(ValueError, match="not a valid id"):
        attach_pdf.splice(packet["pdf"], data, tmp_path, tmp_path / "final.pdf")
    assert not (tmp_path / "final.pdf").exists()


def test_splice_refuses_an_absolute_rid(packet: dict, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere" / "secret"
    outside.parent.mkdir()
    data = {"receipts": [{"rid": str(outside), "title": "Folio"}]}
    with pytest.raises(ValueError, match="not a valid id"):
        attach_pdf.splice(packet["pdf"], data, tmp_path / "receipts", tmp_path / "final.pdf")


def test_splice_refuses_a_symlink_that_points_outside(packet: dict, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "secret.pdf").write_bytes(pdf_bytes([["Somebody else's folio"]]))
    receipts_dir = tmp_path / "receipts"
    receipts_dir.mkdir()
    (receipts_dir / "leak.pdf").symlink_to(outside / "secret.pdf")

    data = {"receipts": [{"rid": "leak", "title": "Folio"}]}
    with pytest.raises(ValueError, match="resolves outside the receipts directory"):
        attach_pdf.splice(packet["pdf"], data, receipts_dir, tmp_path / "final.pdf")


def test_cli_prints_the_final_page_count(
    packet: dict, fixture_dir: Path, fixture_data: dict, tmp_path: Path, capsys
) -> None:
    data_path = tmp_path / "expense_data.json"
    data_path.write_text(json.dumps(fixture_data), encoding="utf-8")
    out = tmp_path / "cli_final.pdf"
    code = attach_pdf.main(
        [
            str(packet["pdf"]),
            str(data_path),
            "--receipts",
            str(fixture_dir / "receipts"),
            "--out",
            str(out),
        ]
    )
    assert code == 0
    assert capsys.readouterr().out.strip() == str(packet["pages"] + 2)
    assert out.exists()


def test_the_cli_validates_before_it_touches_the_packet(
    packet: dict, fixture_dir: Path, fixture_data: dict, tmp_path: Path, capsys
) -> None:
    """A packet is only spliced when the data still describes what is on disk."""
    broken = json.loads(json.dumps(fixture_data))
    broken["receipts"][0]["title"] = ""
    data_path = tmp_path / "broken.json"
    data_path.write_text(json.dumps(broken), encoding="utf-8")
    out = tmp_path / "never.pdf"
    code = attach_pdf.main(
        [
            str(packet["pdf"]),
            str(data_path),
            "--receipts",
            str(fixture_dir / "receipts"),
            "--out",
            str(out),
        ]
    )
    assert code == 2
    assert "title" in capsys.readouterr().err
    assert not out.exists()
