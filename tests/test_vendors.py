"""Every vendor folder, checked against the sample sitting next to it.

The samples under ``vendors/`` are real vendor mail that has been scrubbed:
every name, address, card number, amount, date, identifier and tracking URL was
replaced before the file reached this repository. They are static files, not
generated, so these tests are what keeps a rules.json honest about the markup
it claims to describe.

Four things are proved here for each folder. Every strip pattern matches the
sample: a pattern nothing proves is a pattern nobody can say still works, so
it does not stay. Every strip pattern takes a whole element with it and never
half of one. Cleaning the sample leaves the money alone, drops every script
and leaves no inline event handler. And a From header built from the folder's
first sender domain, with the subject line that vendor really sends, resolves
back to the folder.
"""

from __future__ import annotations

import collections
import json
import re
from pathlib import Path

import cards
import clean
import pytest

VENDORS_DIR = Path(__file__).resolve().parents[1] / "vendors"

# A street address as a receipt prints one: a number, a name, then a city and a
# two letter state. It is what a folder claiming both endpoints has to carry.
STREET_RE = re.compile(r"\d+ [A-Z][\w'.-]*(?: [A-Z][\w'.-]*)*, [A-Z][\w .'-]+, [A-Z]{2}\b")

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


# -------------------------------------------------------------- strip patterns


@pytest.mark.parametrize("folder", [name for name in FOLDERS if name not in SEARCH_ONLY])
def test_every_strip_pattern_is_proven_on_the_sample(folder: str, rules: dict) -> None:
    """Every pattern matches a sample in its own folder. No allowance.

    A folder used to be allowed to carry a pattern for a module its own sample
    does not have, as long as the notes said so, on the argument that one
    vendor sends several templates through the same shell. What that bought
    was six patterns nobody could run, describing markup nobody in this repo
    has a copy of. A pattern no sample exercises is a pattern that could have
    stopped matching a year ago and nothing here would say so, so it goes, and
    comes back with the sample that proves it.
    """
    samples = samples_for(folder)
    unmatched = [
        index
        for index, pattern in enumerate(rules[folder]["strip_regex"])
        if not any(re.search(pattern, text, re.S) for text in samples.values())
    ]
    assert unmatched == [], f"{folder} strip patterns {unmatched} match no sample in the folder"


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
def test_the_uber_total_row_reaches_the_packet_as_the_vendor_wrote_it(
    folder: str, rules: dict
) -> None:
    """The row the folders once rewrote is now carried through untouched.

    Uber lays the total out as two cells, and gives the word Total a cell at
    ``width:100%``. That cell once squeezed the amount beside it to a
    character a line, and each folder carried a replace pair to widen it. What
    actually fixed the wrap was scoping each receipt's stylesheet to its own
    card, so the pairs are gone and the cell keeps the width the vendor wrote.
    The rendered proof is in tests/test_integration.py, which measures both
    amount cells in the committed packet.
    """
    fragment = clean.clean_html(samples_for(folder)["sample.html"], rules[folder])
    row = TOTAL_ROW_RE.search(fragment)
    assert row, f"{folder} lost its total row"
    title_cell, title_text, _, amount_text = row.groups()
    assert amount_text.strip() == UBER_TOTALS[folder]
    assert title_text.strip() == "Total"
    assert "width:100%" in title_cell
    assert rules[folder].get("replace") in (None, [])


@pytest.mark.parametrize("folder", [name for name in FOLDERS if name not in SEARCH_ONLY])
def test_every_replace_pattern_compiles_and_matches_its_sample(folder: str, rules: dict) -> None:
    """A replace pair a sample never matches is a pair nothing proves."""
    samples = samples_for(folder)
    for index, (pattern, _) in enumerate(rules[folder].get("replace") or []):
        re.compile(pattern, re.S)
        assert any(re.search(pattern, text, re.S | re.I) for text in samples.values()), (
            f"{folder} replace pattern {index} matches no sample"
        )


@pytest.mark.parametrize("folder", FOLDERS)
def test_no_folder_repeats_a_generic_unwrap_pattern(folder: str, rules: dict) -> None:
    """The four shapes every vendor sends are listed once, in clean.py.

    A folder that repeated them was four lines that had to be kept in step
    with eleven other folders and with the cleaner, for a rule that was never
    the vendor's own.
    """
    repeated = set(rules[folder]["unwrap_links_matching"]) & set(clean.GENERIC_UNWRAP)
    assert repeated == set(), f"{folder} repeats {sorted(repeated)} from clean.GENERIC_UNWRAP"


def test_the_generic_unwrap_shapes_reach_a_vendor_that_lists_none(rules: dict) -> None:
    """njtransit names no tracking shape of its own and still unwraps the four."""
    assert rules["njtransit"]["unwrap_links_matching"] == []
    raw = '<p><a href="https://example.com/x?utm_source=email">Manage trip</a></p>'
    out = clean.clean_html(raw, rules["njtransit"])
    assert out == "<p>Manage trip</p>"


@pytest.mark.parametrize("folder", [name for name in FOLDERS if name not in SEARCH_ONLY])
def test_at_least_one_strip_pattern_matches(folder: str, rules: dict) -> None:
    samples = samples_for(folder)
    patterns = rules[folder]["strip_regex"]
    assert any(
        re.search(pattern, text, re.S) for pattern in patterns for text in samples.values()
    ), f"{folder} strips nothing at all from its own sample"


# --------------------------------------------------------------- the schema


def test_no_folder_carries_a_field_nothing_reads(rules: dict) -> None:
    """amount_regex and date_regex were read by no script and are gone.

    They were written and kept in step for eleven folders, and the only thing
    that ever ran them was the test that checked they still captured. The
    amounts and dates a packet prints come from expense_data.json, which a
    person fills in from the receipt in front of them.
    """
    for folder, rule in rules.items():
        assert "amount_regex" not in rule, f"{folder} still carries amount_regex"
        assert "date_regex" not in rule, f"{folder} still carries date_regex"


def test_the_reference_page_carries_the_same_keys() -> None:
    """The schema line names every key a rules.json is allowed to hold."""
    page = (VENDORS_DIR.parent / "references" / "interfaces.md").read_text(encoding="utf-8")
    for key in (*clean.REQUIRED_KEYS, "replace"):
        assert f'"{key}"' in page, f"references/interfaces.md never names {key}"
    for gone in ("amount_regex", "date_regex"):
        assert gone not in page, f"references/interfaces.md still names {gone}"


def test_the_contributing_page_names_the_same_fields() -> None:
    """Adding a vendor is written down twice, and both lists are the schema.

    CONTRIBUTING tells a contributor what to put in a new rules.json, and
    clean.REQUIRED_KEYS is what the loader will insist on. A field that fell
    off one list and not the other is a rules.json that passes review and
    fails to load, so the two are checked against each other here.
    """
    page = (VENDORS_DIR.parent / ".github" / "CONTRIBUTING.md").read_text(encoding="utf-8")
    section = page.split("**Adding a vendor.**")[1].split("**Adding a policy rule.**")[0]
    for key in (*clean.REQUIRED_KEYS, "replace"):
        assert f"`{key}`" in section, f".github/CONTRIBUTING.md never names {key}"
    for gone in ("amount_regex", "date_regex"):
        assert gone not in section, f".github/CONTRIBUTING.md still names {gone}"


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


# --------------------------------------------------------- vendor knowledge


@pytest.mark.parametrize("folder", FOLDERS)
def test_every_folder_says_how_late_its_receipts_arrive(folder: str, rules: dict) -> None:
    """A whole number of days, and at least one, in every folder.

    The field is optional in the schema, so a folder could leave it out and
    load. None of the shipped ones does: a reader comparing two folders should
    never have to work out whether a silence means one day or means nobody
    looked.
    """
    lag = rules[folder]["arrival_lag_days"]
    assert isinstance(lag, int) and not isinstance(lag, bool)
    assert lag >= 1, f"{folder} says its receipts arrive before the event"


def test_the_folders_that_wait_for_a_folio_say_so() -> None:
    """The two day lags are the hotel shaped ones, and nothing else."""
    rules = clean.load_vendor_rules(VENDORS_DIR)
    slow = {name for name, rule in rules.items() if rule["arrival_lag_days"] > 1}
    assert slow == {"hotels", "marriott", "plaintext"}


@pytest.mark.parametrize("folder", FOLDERS)
def test_every_declared_kind_and_category_is_in_its_closed_set(folder: str, rules: dict) -> None:
    """load_vendor_rules refuses anything else, so this is the shipped proof."""
    rule = rules[folder]
    assert {row["kind"] for row in rule.get("messages", [])} <= clean.MESSAGE_KINDS
    assert {row["kind"] for row in rule.get("tenders", [])} <= clean.TENDER_KINDS
    assert {row["category"] for row in rule.get("line_categories", [])} <= clean.LINE_CATEGORIES
    assert set(rule.get("missing", [])) <= clean.MISSING_FACTS


@pytest.mark.parametrize("folder", FOLDERS)
def test_a_message_row_only_supersedes_a_subject_its_own_folder_claims(
    folder: str, rules: dict
) -> None:
    """Otherwise the row names a message this folder would never be given."""
    rule = rules[folder]
    claimed = set(rule["subject_patterns"])
    for index, row in enumerate(rule.get("messages", [])):
        assert row["subject_pattern"] in claimed, f"{folder} messages {index} claims no subject"
        if "supersedes" in row:
            assert row["supersedes"] in claimed, f"{folder} messages {index} supersedes an outsider"


# A subject line the folder can show someone, written down in one of the four
# places a subject is ever recorded in this repo.
NOTES_SUBJECT_RE = re.compile(r'"([^"\n]{4,120})"')
TXT_SUBJECT_RE = re.compile(r"^Subject:\s*(.+)$", re.M)
EXAMPLE_RECEIPTS = VENDORS_DIR.parent / "examples" / "receipts"


def proven_subjects(folder: str, rules: dict) -> list[str]:
    """Every subject line this folder can point at, from the folder and the example.

    Four sources, and each one is a message somebody really received. A
    ``.meta.json`` beside a sample carries the headers that sample arrived
    with. A ``.txt`` sample may print its own Subject header inside the body.
    A folder may quote a subject in its ``notes`` for a template it has seen
    and cannot ship. And the example packet's receipts carry their headers in
    ``examples/receipts/*.meta.json``, so a subject that resolves to this
    folder proves a template this repo renders on every build.
    """
    found: list[str] = []
    path = VENDORS_DIR / folder
    for meta in sorted(path.glob("sample*.meta.json")):
        subject = json.loads(meta.read_text(encoding="utf-8")).get("subject")
        if subject:
            found.append(str(subject))
    for name, raw in samples_for(folder).items():
        if name.endswith(".txt"):
            found.extend(TXT_SUBJECT_RE.findall(raw))
    found.extend(NOTES_SUBJECT_RE.findall(rules[folder]["notes"]))
    for meta in sorted(EXAMPLE_RECEIPTS.glob("*.meta.json")):
        headers = json.loads(meta.read_text(encoding="utf-8"))
        subject = str(headers.get("subject") or "")
        sender = str(headers.get("from") or "")
        if subject and clean.detect_vendor(sender, subject, rules) == folder:
            found.append(subject)
    return found


@pytest.mark.parametrize("folder", FOLDERS)
def test_every_message_row_is_proven_on_a_subject_the_folder_can_show(
    folder: str, rules: dict
) -> None:
    """The rule the strip patterns live under, applied to the knowledge rows.

    A ``messages`` row says what kind of document a subject line names, and a
    row is only worth that if the subject line exists. Several folders used to
    carry alternates nobody could point at: a Lyft ride receipt subject Lyft
    does not send, a DoorDash order receipt heading no sample has, an Uber
    receipt line invented to sit beside the real one. Each of them was a guess
    about a template, and a guess is what a vendor folder exists to replace.

    So every row has to match a subject somebody received, from a sample's own
    headers, from a Subject line inside a text sample, from a subject the notes
    quote, or from the example packet's receipts. A row whose pattern
    alternates are all guesses goes; a row with one provable alternate stays,
    because the pattern still names a message this folder is really given.
    """
    subjects = proven_subjects(folder, rules)
    for index, row in enumerate(rules[folder].get("messages", [])):
        pattern = row["subject_pattern"]
        assert any(re.search(pattern, subject) for subject in subjects), (
            f"{folder} messages {index} names a subject nothing in the folder "
            f"or the example proves: {pattern}"
        )


def test_the_folders_that_still_carry_message_rows_are_the_ones_with_a_subject() -> None:
    """Named here so that dropping a row cannot quietly empty a folder.

    Fourteen rows came out at once when the proof rule went in, which is enough
    removal that the remaining set should be written down rather than counted
    again by hand later.
    """
    rules = clean.load_vendor_rules(VENDORS_DIR)
    counts = {name: len(rule.get("messages", [])) for name in FOLDERS for rule in [rules[name]]}
    assert counts == {
        "airlines": 0,
        "doordash": 1,
        "hotels": 0,
        "lufthansa": 1,
        "lyft": 1,
        "marriott": 1,
        "njtransit": 1,
        "plaintext": 0,
        "stripe": 2,
        "uber": 1,
        "uber-eats": 1,
        "united": 2,
    }


def sample_text(folder: str) -> list[str]:
    """Every sample in a folder as the text a reader of values would see."""
    return [
        cards.strip_tags(raw) if name.endswith(".html") else raw
        for name, raw in samples_for(folder).items()
    ]


@pytest.mark.parametrize("folder", [name for name in FOLDERS if name not in SEARCH_ONLY])
def test_every_label_a_folder_names_is_one_its_sample_prints(folder: str, rules: dict) -> None:
    """The same rule the strip patterns live under, for the knowledge labels.

    A tender or a line category describing a label no sample carries is a
    guess about the vendor, and a guess is what this whole folder exists to
    replace. Matching is on the text a reader sees, tags gone and entities
    resolved, because that is what the label is by the time anyone reads it.
    """
    texts = sample_text(folder)
    for key in ("tenders", "line_categories"):
        for index, row in enumerate(rules[folder].get(key, [])):
            pattern = row["label_pattern"]
            assert any(re.search(pattern, text) for text in texts), (
                f"{folder} {key} {index} names a label no sample prints: {pattern}"
            )


@pytest.mark.parametrize("folder", [name for name in FOLDERS if name not in SEARCH_ONLY])
def test_a_folder_with_its_own_card_shape_finds_a_card_the_defaults_miss(
    folder: str, rules: dict
) -> None:
    """The only reason to carry last4_pattern is that nothing else works."""
    pattern = rules[folder].get("last4_pattern")
    if pattern is None:
        return
    for text in sample_text(folder):
        assert cards.find_last4(text) == set(), f"{folder} needs no last4_pattern"
        assert cards.find_last4(text, rules[folder]), f"{folder} last4_pattern finds nothing"


def test_stripe_is_the_folder_that_needs_its_own_card_shape(rules: dict) -> None:
    """Named here so that a second one has to be a deliberate addition."""
    carried = {name for name in FOLDERS if rules[name].get("last4_pattern")}
    assert carried == {"stripe"}


@pytest.mark.parametrize("folder", [name for name in FOLDERS if name not in SEARCH_ONLY])
def test_a_folder_claiming_both_endpoints_prints_both_addresses(folder: str, rules: dict) -> None:
    """endpoints says a ride can be classified from the receipt alone.

    Two street addresses in the body is what that takes, so the claim is
    checked against the sample rather than trusted.
    """
    if not rules[folder].get("endpoints"):
        return
    for text in sample_text(folder):
        assert len(STREET_RE.findall(text)) >= 2, f"{folder} claims endpoints it does not print"


def test_the_ride_and_delivery_folders_are_the_ones_with_endpoints(rules: dict) -> None:
    named = {name for name in FOLDERS if rules[name].get("endpoints")}
    assert named == {"lyft", "uber", "uber-eats"}


# ------------------------------------------------------------- the reference


def test_the_reference_page_covers_every_folder() -> None:
    """references/vendors.md and the notes fields describe the same set."""
    page = (VENDORS_DIR.parent / "references" / "vendors.md").read_text(encoding="utf-8")
    headings = {line[3:].strip() for line in page.splitlines() if line.startswith("## ")}
    for folder in FOLDERS:
        rule = json.loads((VENDORS_DIR / folder / "rules.json").read_text(encoding="utf-8"))
        assert rule["display"] in headings, f"references/vendors.md has no section for {folder}"


def test_the_reference_page_names_every_folder_that_ships_no_sample() -> None:
    """A search folder cleans nothing, and the page has to say which they are."""
    page = (VENDORS_DIR.parent / "references" / "vendors.md").read_text(encoding="utf-8")
    sections = {}
    current = None
    for line in page.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current:
            sections[current].append(line)
    for folder in sorted(SEARCH_ONLY):
        rule = json.loads((VENDORS_DIR / folder / "rules.json").read_text(encoding="utf-8"))
        body = " ".join(sections[rule["display"]])
        assert "no sample" in body.lower(), f"references/vendors.md hides the gap in {folder}"


# ------------------------------------------------------------------ the badge


README_BADGE_RE = re.compile(r"vendors-(\d+)-")


def test_the_readme_vendor_badge_matches_the_folders() -> None:
    """The README badge and the tree it counts are checked against each other.

    CI runs the whole suite on every pull request, so a vendor folder added
    or dropped without touching the badge fails here before it reaches main.
    """
    with_sample = sum(1 for folder in FOLDERS if samples_for(folder))
    readme = (VENDORS_DIR.parent / "README.md").read_text(encoding="utf-8")
    match = README_BADGE_RE.search(readme)
    assert match, "README.md has no vendors badge matching vendors-<N>-"
    badge_count = int(match.group(1))
    assert badge_count == with_sample, (
        f"README vendor badge says {badge_count}, but {with_sample} vendor folders "
        f"carry a sample.html or sample.txt; change the badge in README.md to "
        f"vendors-{with_sample}-"
    )
