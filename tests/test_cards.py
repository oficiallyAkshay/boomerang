"""Tests for the card fingerprint, the Who paid policy in code."""

from __future__ import annotations

from pathlib import Path

import pytest
from cards import find_last4, fingerprint, main, receipt_text, singletons, strip_tags
from fixtures.make_fixture import CARD_LAST4, RIDS, pdf_bytes

COMPANY_CARD = "8802"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Visa *4321", {"4321"}),
        ("Visa *4321 charged", {"4321"}),
        ("Visa* 4321", {"4321"}),
        ("Paid with Visa Ending in 4321", {"4321"}),
        ("card ending in 4321", {"4321"}),
        ("Visa ending 4321", {"4321"}),
        ("corporate card ending 8802", {"8802"}),
        ("Mastercard xxxx4321", {"4321"}),
        ("Mastercard XXXXXX4321", {"4321"}),
        ("Visa •••• 4321", {"4321"}),
        ("Visa ••4321", {"4321"}),
        ("Mastercard *4321", {"4321"}),
        ("VISA ****4321", {"4321"}),
        ("ending in 4321", {"4321"}),
        ("Card ending 4321", {"4321"}),
        ("card x4321", {"4321"}),
        ("Amex xxxx-4321", {"4321"}),
        ("Amex xxxx.4321", {"4321"}),
        ("**** **** **** 4321", {"4321"}),
    ],
)
def test_each_printed_shape_is_read(text: str, expected: set[str]):
    assert find_last4(text) == expected


def test_a_body_can_name_two_cards():
    text = "Ticket on the card ending 8802, checked bag on Visa ending 4321"
    assert find_last4(text) == {"8802", "4321"}


@pytest.mark.parametrize(
    "text",
    [
        "Total *4321",
        "Flight UA *1234",
        "* 1500 bonus miles",
        "Footnote: *2025 terms",
        "x4321",
        "Order 998877 total",
        "Confirmation ending 123",
        "ending in 123456",
        "Reference xxxx98765",
        "Visa *54321",
        "•••• 123456789",
    ],
)
def test_a_footnote_marker_or_a_longer_digit_run_is_not_a_card(text: str):
    assert find_last4(text) == set()


def test_one_mask_character_needs_a_card_word_close_in_front():
    """Twenty characters of room, and not one more."""
    assert find_last4("card" + " " * 15 + "*4321") == {"4321"}
    assert find_last4("card" + " " * 17 + "*4321") == set()


def test_digits_before_the_marker_do_not_confuse_the_match():
    assert find_last4("Total 42.00 on Visa ending 4321") == {"4321"}


def test_strip_tags_resolves_entities_so_bullet_runs_are_readable():
    stripped = strip_tags('<td style="color:#000">Visa &bull;&bull;&bull;&bull; 4321</td>')
    assert "•••• 4321" in stripped
    assert find_last4(stripped) == {"4321"}


def test_strip_tags_drops_script_and_style_bodies():
    stripped = strip_tags("<style>.a{content:'ending 1111'}</style><p>ending 4321</p>")
    assert find_last4(stripped) == {"4321"}


def test_fingerprint_reads_html_text_and_pdf_and_ignores_the_rest(tmp_path: Path):
    (tmp_path / "one.html").write_text("<p>Visa *4321</p>", encoding="utf-8")
    (tmp_path / "two.txt").write_text("Card on file Visa ending 4321", encoding="utf-8")
    (tmp_path / "three.pdf").write_bytes(pdf_bytes([["Charged to Visa ending 8802"]]))
    (tmp_path / "four.png").write_bytes(b"\x89PNG Visa ending 1111")
    (tmp_path / "sub").mkdir()
    assert fingerprint(tmp_path) == {"4321": ["one", "two"], "8802": ["three"]}


def test_a_folio_names_the_card_that_paid_for_the_room(tmp_path: Path):
    """The folio is a PDF, and it is the receipt most likely to name a second card."""
    (tmp_path / "stay.pdf").write_bytes(
        pdf_bytes([["Harborview folio page 1"], ["Charged to Visa ending 8802"]])
    )
    (tmp_path / "ride.html").write_text("<p>Visa *4321</p>", encoding="utf-8")
    assert fingerprint(tmp_path) == {"4321": ["ride"], "8802": ["stay"]}
    assert singletons(fingerprint(tmp_path)) == {"4321", "8802"}


def test_a_pdf_that_will_not_open_names_no_card_and_stops_nothing(tmp_path: Path):
    (tmp_path / "torn.pdf").write_bytes(b"%PDF-1.4 and then nothing at all")
    (tmp_path / "ride.txt").write_text("Visa ending 4321", encoding="utf-8")
    assert fingerprint(tmp_path) == {"4321": ["ride"]}


def test_receipt_text_refuses_a_file_that_is_not_a_receipt(tmp_path: Path):
    other = tmp_path / "notes.meta.json"
    other.write_text("{}", encoding="utf-8")
    assert receipt_text(other) is None


def test_singletons_are_the_last4_seen_once():
    assert singletons({"4321": ["a", "b"], "8802": ["c"]}) == {"8802"}


def test_no_singletons_when_every_card_repeats():
    assert singletons({"4321": ["a", "b"]}) == set()


# --------------------------------------------------- a vendor's own card line

# Stripe's shape, and the reason the field exists: the brand is an image, so
# nothing survives the tag strip except a dash and four digits.
STRIPE_LINE = "Payment method\n   \n    - 4321\n"
STRIPE_RULES = {"last4_pattern": r"Payment method\s*-\s*(\d{4})"}


def test_a_card_line_no_default_shape_reads_is_found_by_the_vendor_pattern():
    """The line the built-in shapes miss, and the rules that find it."""
    assert find_last4(STRIPE_LINE) == set()
    assert find_last4(STRIPE_LINE, STRIPE_RULES) == {"4321"}


def test_the_vendor_pattern_replaces_the_built_in_shapes():
    """A folder that describes its card line is a folder the defaults got wrong.

    So the pattern is not one more shape to try. A receipt carrying both its
    own line and something the defaults would have read answers with the
    vendor's line alone.
    """
    both = STRIPE_LINE + "Visa ending in 8802"
    assert find_last4(both) == {"8802"}
    assert find_last4(both, STRIPE_RULES) == {"4321"}


def test_rules_that_name_no_pattern_change_nothing():
    for rules in ({}, {"last4_pattern": ""}, {"last4_pattern": None}, {"name": "lyft"}):
        assert find_last4("Visa *4321", rules) == {"4321"}


@pytest.mark.parametrize(
    "text",
    [
        "Payment method - 43215",
        "Payment method - 921",
        "Payment method - 4321",
    ],
)
def test_a_loose_vendor_pattern_still_cannot_take_a_longer_run(text: str):
    """Four digits, standing alone, however loosely the folder wrote it.

    The first two are a reference number and a short code; only the third is
    a card, and it is the only one that comes back.
    """
    found = find_last4(text, {"last4_pattern": r"-\s*(\d+)"})
    assert found == ({"4321"} if text.endswith("- 4321") else set())


# ------------------------------------------------------- against the fixture


def test_the_fixture_traveller_card_is_on_many_receipts(fixture_dir: Path):
    fp = fingerprint(fixture_dir / "receipts")
    assert len(fp[CARD_LAST4]) > 1


def test_the_company_card_is_the_one_seen_once(fixture_dir: Path):
    fp = fingerprint(fixture_dir / "receipts")
    assert singletons(fp) == {COMPANY_CARD}
    assert fp[COMPANY_CARD] == [RIDS["flight"]]


# --------------------------------------------------------------------- cli


def test_the_cli_prints_the_table_and_names_the_singleton(fixture_dir: Path, capsys):
    assert main([str(fixture_dir / "receipts")]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0] == "last4  count  rids"
    assert any(line.startswith(f"{COMPANY_CARD}  1  ") for line in lines)
    assert lines[-1] == f"{COMPANY_CARD}: seen once, probably someone else's card"


def test_the_cli_says_so_when_no_card_stands_alone(tmp_path: Path, capsys):
    (tmp_path / "a.txt").write_text("Visa ending 4321", encoding="utf-8")
    (tmp_path / "b.txt").write_text("Visa ending 4321", encoding="utf-8")
    assert main([str(tmp_path)]) == 0
    assert capsys.readouterr().out.splitlines()[-1].startswith("No last-4 seen once")
