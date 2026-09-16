"""Every vendor folder, checked against the sample sitting next to it.

The samples under ``vendors/`` are real vendor mail that has been scrubbed:
every name, address, card number, amount, date, identifier and tracking URL was
replaced before the file reached this repository. They are static files, not
generated, so these tests are what keeps a rules.json honest about the markup
it claims to describe.

Five things are proved here for each folder. Every strip pattern matches the
sample, unless the folder's notes say the module is absent from it. Every
strip pattern takes a whole element with it and never half of one. Every
amount and date pattern that is not null captures on the sample. Cleaning the
sample leaves the money alone, drops every script and leaves no inline event
handler. And a From header built from the folder's first sender domain, with
the subject line that vendor really sends, resolves back to the folder.
"""

from __future__ import annotations

import collections
import json
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

# Every tag in a fragment, open or close, with the element name in group two.
TAG_RE = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9]*)\b[^>]*?(/?)>", re.S)
# Elements that carry no closing tag, so counting them would never balance.
VOID_ELEMENTS = frozenset("img br hr meta link input source track wbr col".split())

# A From header and a subject line per folder, in the shape that vendor sends.
# Every one of them resolves to its own folder, including the two that share a
# sender domain: Uber Eats mail comes from uber.com like Uber ride mail, and
# the subject line is what tells the detection which of the two it is.
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
        "Reservation Confirmation #51882037 for The Westin Balcones Park",
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
        "uber-eats",
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


def tag_balance(fragment: str) -> dict[str, int]:
    """Opens minus closes per element name, for every name that does not balance.

    Void elements are skipped, and so is a tag that closes itself, because
    neither of them is ever waiting for a closing tag.
    """
    counts: collections.Counter[str] = collections.Counter()
    for closing, name, self_closing in TAG_RE.findall(fragment):
        element = name.lower()
        if element in VOID_ELEMENTS or self_closing:
            continue
        counts[element] += -1 if closing else 1
    return {element: count for element, count in counts.items() if count}


@pytest.mark.parametrize("folder", [name for name in FOLDERS if name not in SEARCH_ONLY])
def test_every_strip_pattern_removes_a_whole_element(folder: str, rules: dict) -> None:
    """Whatever a strip pattern takes out, it takes out entire.

    A pattern written as an opening tag and a lazy run to the first closing
    tag stops at the first nested close, not at its own, and what it leaves
    behind is an opening tag with nothing to close it. The receipt after that
    is markup a browser has to guess at, and a packet built from a guess puts
    a vendor's table around the next vendor's receipt.

    So every match is counted: opens minus closes, per element name. A match
    that balances took a whole element, or a run of whole elements. A match
    that does not is a half element and the pattern has to be rewritten.

    The one allowance is for a tag the sample itself never closed. Lyft's
    safety callout opens a second table row without closing the first, so the
    module around it cannot be taken out except with that orphan opening tag
    inside the match, and what is left behind is better balanced than what was
    there before. A match may therefore carry an unclosed tag only where the
    whole sample carries the same one, and never more of them than it has.

    The counting runs on the sample with its comments taken out, which is the
    text ``clean.clean_html`` hands the patterns. Conditional comments are the
    reason: a Lufthansa or DoorDash template opens a table inside
    ``<!--[if mso]>`` and closes it inside a second comment further down, and
    tags that only Outlook ever sees are not tags this has any business
    counting.
    """
    for name, raw in samples_for(folder).items():
        text = clean.COMMENT_RE.sub("", raw)
        orphans = tag_balance(text)
        for index, pattern in enumerate(rules[folder]["strip_regex"]):
            for match in re.finditer(pattern, text, re.S | re.I):
                halves = {
                    element: count
                    for element, count in tag_balance(match.group(0)).items()
                    if count * orphans.get(element, 0) <= 0 or abs(count) > abs(orphans[element])
                }
                assert not halves, (
                    f"{folder}/{name} strip pattern {index} matched a half element, "
                    f"leaving {halves} unclosed"
                )


# Everything the three Lyft promo modules are made of: the safety panel with
# its shield, the ride safety summary with its illustration and its thumbs,
# and the help cluster, whose rows are an icon, a label and a chevron.
LYFT_MODULE_PARTS = (
    "How Lyft prioritizes your safety",
    "Every Lyft ride has built-in safety",
    "ShieldCross_Circle.png",
    'bill-callout-box"',
    "Ride safety summary",
    "safe-ride-summary-module",
    "safe-rides-summary-header.png",
    "thumb-up_XS.png",
    "Get help and more",
    "Help_Circle.png",
    "MoneyBag_Circle.png",
    "Help center",
    "Dispute ride charges",
    "button-aarow.png",
)

# What a ride receipt is for, all of it still in the fragment afterwards.
LYFT_RECEIPT_PARTS = (
    "YOUR RIDE TO 815 CYPRESS ROW BLVD ON JUNE 15, 2026",
    "Thanks for riding with",
    "Lyft Cash",
    "Visa *4321",
    "$18.40",
    "$22.27",
    "Lyft fare",
    "Toll: AT : Airport Toll Plaza",
    "Your trip",
    "1150 Congress Ave, Austin, TX",
    "815 Cypress Row Blvd, Austin, TX",
    "Receipt #2264013875290446311",
)


def test_the_lyft_promo_modules_go_whole_and_the_receipt_stays(rules: dict) -> None:
    """The three modules Lyft hangs off a receipt go, picture and label together.

    Each of them is an icon or an illustration over a line of marketing copy,
    so a pattern anchored on the copy leaves the picture sitting on the page
    with nothing to explain it. The help cluster was the loudest case: five
    rows of an icon, a label and a chevron, with only the labels stripped.
    """
    fragment = clean.clean_html(samples_for("lyft")["sample.html"], rules["lyft"])
    for part in LYFT_MODULE_PARTS:
        assert part not in fragment, f"lyft kept {part!r} from a promo module"
    for part in LYFT_RECEIPT_PARTS:
        assert part in fragment, f"lyft lost {part!r} from the receipt itself"


# -------------------------------------------------------------------- replace


# The total row of an Uber receipt, as it is after cleaning: the title cell, the
# amount cell, and the text of each. Both folders share the row shape.
TOTAL_ROW_RE = re.compile(
    r'(<td[^>]*class="total-fare-title"[^>]*>)(.*?)</td>\s*'
    r'(<td[^>]*class="total-fare-amount"[^>]*>)(.*?)</td>',
    re.S,
)

UBER_TOTALS = {"uber": "$34.86", "uber-eats": "$88.60"}


@pytest.mark.parametrize("folder", sorted(UBER_TOTALS))
def test_the_uber_total_row_gives_the_amount_room_to_sit_on_one_line(
    folder: str, rules: dict
) -> None:
    """The title cell at width:100% squeezed the amount to a character a line.

    Uber lays the total out as two cells, and gives the word Total a cell at
    ``width:100%``. A mail client is wide enough that the amount beside it
    still fits; a packet's column is not, and the amount wrapped one character
    per line. The folder's replace pair turns that one cell's width into
    ``auto``. Nothing else in the row moves, and the amount is the amount the
    vendor printed.
    """
    fragment = clean.clean_html(samples_for(folder)["sample.html"], rules[folder])
    row = TOTAL_ROW_RE.search(fragment)
    assert row, f"{folder} lost its total row"
    title_cell, title_text, _, amount_text = row.groups()
    assert amount_text.strip() == UBER_TOTALS[folder]
    assert title_text.strip() == "Total"
    assert "width:100%" not in title_cell
    assert "width:auto" in title_cell


@pytest.mark.parametrize("folder", [name for name in FOLDERS if name not in SEARCH_ONLY])
def test_every_replace_pattern_compiles_and_matches_its_sample(folder: str, rules: dict) -> None:
    """A replace pair a sample never matches is a pair nothing proves."""
    samples = samples_for(folder)
    for index, (pattern, _) in enumerate(rules[folder].get("replace") or []):
        re.compile(pattern, re.S)
        assert any(re.search(pattern, text, re.S | re.I) for text in samples.values()), (
            f"{folder} replace pattern {index} matches no sample"
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
    """Every money string in a sample is still there after the clean.

    No vendor gets an allowance. Where a module a rules.json would rather
    remove prints an amount of its own, the amount guard skips that pattern
    and the module stays: Uber's Uber One credit, Uber Eats' Uber One saving
    and United's two free bags at 0.00 USD are all still in the fragment,
    and each folder's notes say so.
    """
    for name, raw in samples_for(folder).items():
        before = collections.Counter(MONEY_RE.findall(raw))
        after = collections.Counter(MONEY_RE.findall(clean.clean_html(raw, rules[folder])))
        assert not before - after, f"{folder}/{name} lost {before - after}"
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


# A sample whose <title> is the subject line the vendor sent. Where a folder is
# on this list, the two have to name the same thing: a subject naming one hotel
# over a sample naming another reads as two stays and there is only one.
TITLE_IS_THE_SUBJECT = ("marriott",)
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S | re.I)


@pytest.mark.parametrize("folder", TITLE_IS_THE_SUBJECT)
def test_the_sample_title_and_the_subject_name_the_same_thing(folder: str) -> None:
    """The scrubbing renamed the property, and the subject kept the old name.

    Marriott puts the subject line in the sample's ``<title>``, so the two are
    one string in two places, and a reader who saw a confirmation for one hotel
    carrying a page titled for another would be right to distrust the packet.
    """
    for name, raw in samples_for(folder).items():
        title = TITLE_RE.search(raw)
        assert title, f"{folder}/{name} has no title"
        assert title.group(1).strip() == SENDERS[folder][1]


def test_uber_eats_is_told_from_an_uber_ride_by_its_subject(rules: dict) -> None:
    """Both come from uber.com, so the subject is the only separator."""
    eats = SENDERS["uber-eats"][1]
    ride = SENDERS["uber"][1]
    assert clean.detect_vendor("someone@example.org", eats, rules) == "uber-eats"
    assert clean.detect_vendor("someone@example.org", ride, rules) == "uber"


def test_the_shared_uber_domain_is_split_by_the_subject(rules: dict) -> None:
    """A vendor the domain and the subject both name beats one named by domain alone."""
    sender = "Uber <noreply@uber.com>"
    assert clean.detect_vendor(sender, SENDERS["uber-eats"][1], rules) == "uber-eats"
    assert clean.detect_vendor(sender, SENDERS["uber"][1], rules) == "uber"


def test_a_shared_domain_with_a_subject_neither_claims_falls_to_the_first(rules: dict) -> None:
    """With nothing in the subject to go on, the domain still answers."""
    assert clean.detect_vendor("noreply@uber.com", "A message about nothing", rules) == "uber"


def test_the_plaintext_catch_all_wins_for_a_domainless_hotel_text(rules: dict) -> None:
    assert clean.detect_vendor("", PLAINTEXT_SUBJECT, rules) == "plaintext"
    assert clean.detect_vendor("front.desk@an-unlisted-inn.example", PLAINTEXT_SUBJECT, rules) == (
        "plaintext"
    )


def test_a_listed_hotel_domain_beats_the_catch_all(rules: dict) -> None:
    assert clean.detect_vendor("noreply@citizenm.com", PLAINTEXT_SUBJECT, rules) == "hotels"


# ------------------------------------------------------------- the reference


def test_the_reference_page_covers_every_folder() -> None:
    """references/vendors.md and the notes fields describe the same set."""
    page = (VENDORS_DIR.parent / "references" / "vendors.md").read_text(encoding="utf-8")
    headings = {line[3:].strip() for line in page.splitlines() if line.startswith("## ")}
    for folder in FOLDERS:
        rule = json.loads((VENDORS_DIR / folder / "rules.json").read_text(encoding="utf-8"))
        assert rule["display"] in headings, f"references/vendors.md has no section for {folder}"


def test_the_reference_page_names_every_known_gap() -> None:
    """A folder with a null pattern is a gap, and the page has to say so."""
    page = (VENDORS_DIR.parent / "references" / "vendors.md").read_text(encoding="utf-8")
    sections = {}
    current = None
    for line in page.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current:
            sections[current].append(line)
    for folder in FOLDERS:
        if folder in SEARCH_ONLY:
            continue
        rule = json.loads((VENDORS_DIR / folder / "rules.json").read_text(encoding="utf-8"))
        if rule["amount_regex"] is not None and rule["date_regex"] is not None:
            continue
        body = " ".join(sections[rule["display"]])
        assert "Known gap" in body, f"references/vendors.md hides a gap in {folder}"
