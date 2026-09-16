"""Every vendor folder, checked against the sample sitting next to it.

The samples under ``vendors/`` are real vendor mail that has been scrubbed:
every name, address, card number, amount, date, identifier and tracking URL was
replaced before the file reached this repository. They are static files, not
generated, so these tests are what keeps a rules.json honest about the markup
it claims to describe.

Four things are proved here for each folder. Every strip pattern matches the
sample, unless the folder's notes say the module is absent from it. Every
amount and date pattern that is not null captures on the sample. Cleaning the
sample leaves the money alone, drops every script and leaves no inline event
handler. And a From header built from the folder's first sender domain, with
the subject line that vendor really sends, resolves back to the folder.
"""

from __future__ import annotations

import collections
import re
from pathlib import Path

import clean
import pytest

VENDORS_DIR = Path(__file__).resolve().parents[1] / "vendors"

# Every folder, and whether it ships a sample. The two search folders exist so
# the second search pass covers hotel and airline mail; they clean nothing.
SEARCH_ONLY = {"airlines", "hotels"}
FOLDERS = sorted(path.name for path in VENDORS_DIR.iterdir() if (path / "rules.json").is_file())

# Money, as a receipt prints it: a symbol in front, or a currency code behind.
# A bare 12.34 is not counted, because stylesheets are full of them.
MONEY_RE = re.compile(r"(?:[$£€₹]\s?\d[\d,]*\.\d{2})|(?:\b\d[\d,]*\.\d{2}\s?(?:USD|EUR|GBP|CHF))")
HANDLER_RE = re.compile(r"\son\w+\s*=", re.I)

# The only amounts any vendor's rules are allowed to take out of a sample, with
# the module each one sits in. Both are figures the reader does not claim: a
# loyalty credit inside a promo strip, and the two zero charges in United's
# free baggage allowance table.
REMOVED_AMOUNTS = {
    ("uber", "sample.html"): {"$1.18": 1},  # Uber One credits earned, promo strip
    ("uber-eats", "sample.html"): {"$14.55": 1},  # Uber One savings, promo strip
    ("united", "sample.html"): {"0.00 USD": 2},  # baggage allowance table, free bags
}

# A From header and a subject line per folder, in the shape that vendor sends.
# The expected name is the folder, except where two folders honestly share a
# sender domain: Uber Eats mail comes from uber.com like Uber ride mail, so the
# domain pass resolves it to uber and only the subject separates the two.
SENDERS = {
    "airlines": ("Delta <receipts@delta.com>", "Your flight receipt", "airlines"),
    "doordash": (
        "DoorDash <no-reply@doordash.com>",
        "Final receipt for Jordan from Cedar Row",
        "doordash",
    ),
    "hotels": ("Hotels.com <confirmation@hotels.com>", "Your booking confirmation", "hotels"),
    "lufthansa": (
        "Lufthansa <flight.service@information.lufthansa.com>",
        "Your baggage receipt 4783120956 for Austin - Seattle on 15. June 2026",
        "lufthansa",
    ),
    "lyft": ("Lyft <no-reply@lyftmail.com>", "Your ride with Teodoro on June 15", "lyft"),
    "marriott": (
        "Marriott <reservations@res-marriott.com>",
        "Reservation Confirmation #51882037 for Austin Congress Avenue",
        "marriott",
    ),
    "njtransit": ("NJ TRANSIT <noreply@mytix.njtransit.com>", "NJ TRANSIT - Receipt", "njtransit"),
    "stripe": (
        "<invoice+statements@stripe.com>",
        "Your receipt from Northgate Labs Inc. #4106-8823",
        "stripe",
    ),
    "uber": ("Uber Receipts <noreply@uber.com>", "Your Thursday evening trip with Uber", "uber"),
    "uber-eats": (
        "Uber Eats <noreply@uber.com>",
        "Your Friday evening order with Uber Eats",
        "uber",
    ),
    "united": (
        "United Airlines <Receipts@united.com>",
        "eTicket Itinerary and Receipt for Confirmation KQ4RTB",
        "united",
    ),
}

# The catch all has no sender domain at all, so it is reached by subject only.
PLAINTEXT_SUBJECT = "Open me to get your citizenM invoice"


def samples_for(folder: str) -> dict[str, str]:
    """Every sample file in a folder, keyed by file name."""
    path = VENDORS_DIR / folder
    return {
        item.name: item.read_text(encoding="utf-8")
        for item in sorted(path.iterdir())
        if item.name.startswith("sample") and item.suffix in {".html", ".txt"}
    }


@pytest.fixture(scope="module")
def rules() -> dict[str, dict]:
    return clean.load_vendor_rules(VENDORS_DIR)


# ----------------------------------------------------------------- the folders


def test_every_folder_loads_and_names_itself(rules: dict) -> None:
    assert sorted(rules) == FOLDERS
    for name, rule in rules.items():
        assert rule["name"] == name
        assert rule["display"].strip()
        assert rule["notes"].strip()


def test_a_folder_without_a_sample_still_loads(rules: dict) -> None:
    for name in SEARCH_ONLY:
        assert samples_for(name) == {}
        assert rules[name]["sender_domains"]
        assert rules[name]["strip_regex"] == []
        assert rules[name]["unwrap_links_matching"] == []


def test_every_folder_that_cleans_ships_a_sample() -> None:
    for folder in FOLDERS:
        if folder in SEARCH_ONLY:
            continue
        assert samples_for(folder), f"{folder} has rules but no sample"


@pytest.mark.parametrize("folder", FOLDERS)
def test_every_pattern_compiles(folder: str, rules: dict) -> None:
    rule = rules[folder]
    for key in ("strip_regex", "subject_patterns", "unwrap_links_matching"):
        for pattern in rule[key]:
            re.compile(pattern, re.S)
    for key in ("amount_regex", "date_regex"):
        if rule[key] is not None:
            re.compile(rule[key], re.S)


# -------------------------------------------------------------- strip patterns


@pytest.mark.parametrize("folder", [name for name in FOLDERS if name not in SEARCH_ONLY])
def test_every_strip_pattern_is_proven_on_the_sample(folder: str, rules: dict) -> None:
    """A pattern either matches a sample here, or the notes say why it cannot.

    A folder is allowed to carry a pattern for a module its own sample does not
    have, because one vendor sends several templates through the same shell.
    What it is not allowed to do is carry that pattern silently.
    """
    samples = samples_for(folder)
    notes = rules[folder]["notes"]
    unmatched = [
        pattern
        for pattern in rules[folder]["strip_regex"]
        if not any(re.search(pattern, text, re.S) for text in samples.values())
    ]
    if unmatched:
        assert "present in this sample" in notes, (
            f"{folder} carries {len(unmatched)} strip patterns that match no sample "
            "and says nothing about it in notes"
        )


@pytest.mark.parametrize("folder", [name for name in FOLDERS if name not in SEARCH_ONLY])
def test_at_least_one_strip_pattern_matches(folder: str, rules: dict) -> None:
    samples = samples_for(folder)
    patterns = rules[folder]["strip_regex"]
    assert any(
        re.search(pattern, text, re.S) for pattern in patterns for text in samples.values()
    ), f"{folder} strips nothing at all from its own sample"


# ------------------------------------------------------------ amount and date


@pytest.mark.parametrize("folder", [name for name in FOLDERS if name not in SEARCH_ONLY])
def test_amount_and_date_capture_on_the_sample(folder: str, rules: dict) -> None:
    """Null means the vendor prints no such value, and the notes have to say so."""
    rule = rules[folder]
    samples = samples_for(folder)
    for key in ("amount_regex", "date_regex"):
        pattern = rule[key]
        if pattern is None:
            assert "null" in rule["notes"], f"{folder} {key} is null and unexplained"
            continue
        captures = [
            match.group(1)
            for match in (re.search(pattern, text, re.S) for text in samples.values())
            if match
        ]
        assert captures, f"{folder} {key} captures nothing on its sample"
        assert all(capture.strip() for capture in captures)


def test_the_two_search_folders_read_no_values(rules: dict) -> None:
    for name in SEARCH_ONLY:
        assert rules[name]["amount_regex"] is None
        assert rules[name]["date_regex"] is None


# -------------------------------------------------------------------- cleaning


@pytest.mark.parametrize("folder", [name for name in FOLDERS if name not in SEARCH_ONLY])
def test_cleaning_the_sample_keeps_the_money(folder: str, rules: dict) -> None:
    for name, raw in samples_for(folder).items():
        expected = collections.Counter(REMOVED_AMOUNTS.get((folder, name), {}))
        before = collections.Counter(MONEY_RE.findall(raw))
        after = collections.Counter(MONEY_RE.findall(clean.clean_html(raw, rules[folder])))
        assert before - after == expected, f"{folder}/{name} lost {before - after}"
        assert not after - before


@pytest.mark.parametrize("folder", [name for name in FOLDERS if name not in SEARCH_ONLY])
def test_cleaning_the_sample_leaves_nothing_live(folder: str, rules: dict) -> None:
    for name, raw in samples_for(folder).items():
        out = clean.clean_html(raw, rules[folder])
        assert "<script" not in out.lower(), f"{folder}/{name} kept a script"
        assert not HANDLER_RE.search(out), f"{folder}/{name} kept an event handler"
        assert "href=" not in out.lower(), f"{folder}/{name} kept a live link"


@pytest.mark.parametrize("folder", [name for name in FOLDERS if name not in SEARCH_ONLY])
def test_cleaning_the_sample_keeps_the_receipt(folder: str, rules: dict) -> None:
    """Cleaning takes modules off a receipt; it does not empty it."""
    for name, raw in samples_for(folder).items():
        out = clean.clean_html(raw, rules[folder])
        assert len(out) > len(raw) * 0.2, f"{folder}/{name} cleaned down to almost nothing"


# ------------------------------------------------------------------- detection


@pytest.mark.parametrize("folder", sorted(SENDERS))
def test_the_first_sender_domain_resolves_to_this_vendor(folder: str, rules: dict) -> None:
    from_addr, subject, expected = SENDERS[folder]
    domain = rules[folder]["sender_domains"][0]
    assert domain in from_addr
    assert clean.detect_vendor(from_addr, subject, rules) == expected


def test_uber_eats_is_told_from_an_uber_ride_by_its_subject(rules: dict) -> None:
    """Both come from uber.com, so the subject is the only separator."""
    eats = SENDERS["uber-eats"][1]
    ride = SENDERS["uber"][1]
    assert clean.detect_vendor("someone@example.org", eats, rules) == "uber-eats"
    assert clean.detect_vendor("someone@example.org", ride, rules) == "uber"


def test_the_plaintext_catch_all_wins_for_a_domainless_hotel_text(rules: dict) -> None:
    assert clean.detect_vendor("", PLAINTEXT_SUBJECT, rules) == "plaintext"
    assert clean.detect_vendor("front.desk@an-unlisted-inn.example", PLAINTEXT_SUBJECT, rules) == (
        "plaintext"
    )


def test_a_listed_hotel_domain_beats_the_catch_all(rules: dict) -> None:
    assert clean.detect_vendor("noreply@citizenm.com", PLAINTEXT_SUBJECT, rules) == "hotels"
