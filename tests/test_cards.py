"""Tests for the card fingerprint, the Who paid policy in code."""

from __future__ import annotations

from pathlib import Path

import pytest
from cards import find_last4, fingerprint, main, singletons, strip_tags
from fixtures.make_fixture import CARD_LAST4, RIDS

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
        "Order 998877 total",
        "Confirmation ending 123",
        "ending in 123456",
        "Reference xxxx98765",
        "Visa *54321",
        "•••• 123456789",
    ],
)
def test_a_longer_digit_run_is_not_a_card(text: str):
    assert find_last4(text) == set()


def test_digits_before_the_marker_do_not_confuse_the_match():
    assert find_last4("Total 42.00 on Visa ending 4321") == {"4321"}


def test_strip_tags_resolves_entities_so_bullet_runs_are_readable():
    stripped = strip_tags('<td style="color:#000">Visa &bull;&bull;&bull;&bull; 4321</td>')
    assert "•••• 4321" in stripped
    assert find_last4(stripped) == {"4321"}


def test_strip_tags_drops_script_and_style_bodies():
    stripped = strip_tags("<style>.a{content:'ending 1111'}</style><p>ending 4321</p>")
    assert find_last4(stripped) == {"4321"}


def test_fingerprint_reads_html_and_text_and_ignores_other_files(tmp_path: Path):
    (tmp_path / "one.html").write_text("<p>Visa *4321</p>", encoding="utf-8")
    (tmp_path / "two.txt").write_text("Card on file Visa ending 4321", encoding="utf-8")
    (tmp_path / "three.pdf").write_bytes(b"%PDF ending 9999")
    (tmp_path / "sub").mkdir()
    assert fingerprint(tmp_path) == {"4321": ["one", "two"]}


def test_singletons_are_the_last4_seen_once():
    assert singletons({"4321": ["a", "b"], "8802": ["c"]}) == {"8802"}


def test_no_singletons_when_every_card_repeats():
    assert singletons({"4321": ["a", "b"]}) == set()


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
