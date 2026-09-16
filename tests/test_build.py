"""Tests for the packet builder.

Every input here comes from the synthetic fixture generator, so no real mailbox
data is ever read.
"""

from __future__ import annotations

import base64
import copy
import json
from decimal import Decimal
from pathlib import Path

import build
import check_prose
import pytest

EM_DASH = "\u2014"  # written as an escape so this file stays clean
RSEC_OPEN = '<section class="rsec"'


# ----------------------------------------------------------------- utilities


@pytest.fixture
def data(fixture_dir: Path) -> dict:
    """A fresh mutable copy of the fixture's expense data."""
    return json.loads((fixture_dir / "expense_data.json").read_text(encoding="utf-8"))


@pytest.fixture
def receipts(fixture_dir: Path) -> Path:
    return fixture_dir / "receipts"


@pytest.fixture(scope="session")
def packet(fixture_dir: Path) -> str:
    """The fixture rendered once, for the many read only assertions."""
    payload = json.loads((fixture_dir / "expense_data.json").read_text(encoding="utf-8"))
    return build.render_packet(payload, fixture_dir / "receipts")


def summary_region(html_text: str) -> str:
    """Everything before the first receipt section."""
    return html_text.split(RSEC_OPEN, 1)[0]


def first_item(payload: dict) -> dict:
    return payload["days"][0]["items"][0]


# ------------------------------------------------------------------ validate


def test_the_fixture_validates_clean(data: dict, receipts: Path) -> None:
    assert build.validate(data) == []
    assert build.validate(data, receipts) == []


def test_non_object_data_is_one_problem() -> None:
    assert build.validate(["not", "an", "object"]) == ["expense data: expected an object"]


@pytest.mark.parametrize("key", ["company", "trip", "traveler"])
def test_cover_fields_must_be_present_and_stringy(data: dict, key: str) -> None:
    missing = copy.deepcopy(data)
    del missing[key]
    assert f"{key}: missing" in build.validate(missing)

    wrong = copy.deepcopy(data)
    wrong[key] = 7
    assert f"{key}: expected a string" in build.validate(wrong)

    blank = copy.deepcopy(data)
    blank[key] = "   "
    assert f"{key}: empty" in build.validate(blank)


@pytest.mark.parametrize("key", ["company", "trip", "traveler"])
def test_cover_fields_are_checked_for_prose(data: dict, key: str) -> None:
    dashed = copy.deepcopy(data)
    dashed[key] = f"Acme {EM_DASH} West"
    assert f"{key}: contains an em dash" in build.validate(dashed)

    banned = copy.deepcopy(data)
    banned[key] = "Onsite, balance Due on arrival"
    assert f"{key}: contains a banned word" in build.validate(banned)


def test_day_labels_are_checked_for_prose(data: dict) -> None:
    dashed = copy.deepcopy(data)
    dashed["days"][0]["label"] = f"Monday {EM_DASH} travel out"
    assert "days[0]: label contains an em dash" in build.validate(dashed)

    banned = copy.deepcopy(data)
    banned["days"][0]["label"] = "Monday, balance Due"
    assert "days[0]: label contains a banned word" in build.validate(banned)


def test_receipt_titles_are_checked_for_prose(data: dict) -> None:
    dashed = copy.deepcopy(data)
    dashed["receipts"][0]["title"] = f"eTicket {EM_DASH} AUS to SEA"
    assert "receipts[0]: title contains an em dash" in build.validate(dashed)

    banned = copy.deepcopy(data)
    banned["receipts"][0]["title"] = "eTicket, balance Due"
    assert "receipts[0]: title contains a banned word" in build.validate(banned)


def test_currency_must_be_a_string(data: dict) -> None:
    data["currency"] = 840
    assert "currency: expected a string" in build.validate(data)


def test_rid_regex_is_enforced_on_items_and_receipts(data: dict) -> None:
    bad_item = copy.deepcopy(data)
    first_item(bad_item)["rid"] = "not a valid rid!"
    assert "days[0].items[0]: rid is not a valid id" in build.validate(bad_item)

    bad_receipt = copy.deepcopy(data)
    bad_receipt["receipts"][0]["rid"] = "x" * 65
    assert "receipts[0]: rid is not a valid id" in build.validate(bad_receipt)


def test_item_rid_must_appear_in_receipts(data: dict) -> None:
    first_item(data)["rid"] = "orphan_rid"
    assert "days[0].items[0]: rid has no receipt" in build.validate(data)


def test_duplicate_receipt_rid_is_a_problem(data: dict) -> None:
    data["receipts"][1]["rid"] = data["receipts"][0]["rid"]
    assert "receipts[1]: rid appears twice" in build.validate(data)


def test_amount_must_be_a_number_with_at_most_two_decimals(data: dict) -> None:
    text_amount = copy.deepcopy(data)
    first_item(text_amount)["amt"] = "12.00"
    assert "days[0].items[0]: amt expected a number" in build.validate(text_amount)

    boolean_amount = copy.deepcopy(data)
    first_item(boolean_amount)["amt"] = True
    assert "days[0].items[0]: amt expected a number" in build.validate(boolean_amount)

    fractional = copy.deepcopy(data)
    first_item(fractional)["amt"] = 12.345
    assert "days[0].items[0]: amt has more than 2 decimals" in build.validate(fractional)

    whole = copy.deepcopy(data)
    first_item(whole)["amt"] = 12
    assert build.validate(whole) == []


def test_desc_rules(data: dict) -> None:
    dashed = copy.deepcopy(data)
    first_item(dashed)["desc"] = f"Ride {EM_DASH} home to airport"
    assert "days[0].items[0]: desc contains an em dash" in build.validate(dashed)

    banned = copy.deepcopy(data)
    first_item(banned)["desc"] = "Balance Due on arrival"
    assert "days[0].items[0]: desc contains a banned word" in build.validate(banned)

    embedded = copy.deepcopy(data)
    first_item(embedded)["desc"] = "Overdue notice is a different word"
    assert build.validate(embedded) == []

    wrong_type = copy.deepcopy(data)
    first_item(wrong_type)["desc"] = 5
    assert "days[0].items[0]: desc expected a string" in build.validate(wrong_type)

    blank = copy.deepcopy(data)
    first_item(blank)["desc"] = ""
    assert "days[0].items[0]: desc empty" in build.validate(blank)


def test_unknown_receipt_kind_is_a_problem(data: dict) -> None:
    data["receipts"][0]["kind"] = "spreadsheet"
    problems = build.validate(data)
    assert any("kind is not one of" in problem for problem in problems)


def test_declared_kind_must_match_the_file_on_disk(data: dict, receipts: Path) -> None:
    data["receipts"][0]["kind"] = "pdf"  # the fixture flight receipt is html
    assert build.validate(data) == []  # no directory, no file check
    problems = build.validate(data, receipts)
    assert "receipts[0]: kind says pdf but the file on disk is html" in problems


def test_matching_kind_and_absent_file_are_both_fine(data: dict, receipts: Path) -> None:
    data["receipts"][0]["kind"] = "html"
    assert build.validate(data, receipts) == []

    data["receipts"][0]["rid"] = "never_fetched"
    data["days"][0]["items"][1]["rid"] = "never_fetched"
    assert build.validate(data, receipts) == []


def test_empty_days_list_is_allowed_but_an_empty_label_is_not(data: dict) -> None:
    empty = copy.deepcopy(data)
    empty["days"] = []
    assert build.validate(empty) == []

    blank_label = copy.deepcopy(data)
    blank_label["days"][0]["label"] = "  "
    assert "days[0]: label empty" in build.validate(blank_label)

    wrong_label = copy.deepcopy(data)
    wrong_label["days"][0]["label"] = None
    assert "days[0]: label expected a string" in build.validate(wrong_label)


def test_container_shapes_are_checked(data: dict) -> None:
    no_days = copy.deepcopy(data)
    del no_days["days"]
    assert "days: missing" in build.validate(no_days)

    days_not_list = copy.deepcopy(data)
    days_not_list["days"] = {}
    assert "days: expected a list" in build.validate(days_not_list)

    day_not_object = copy.deepcopy(data)
    day_not_object["days"][0] = "Monday"
    assert "days[0]: expected an object" in build.validate(day_not_object)

    items_not_list = copy.deepcopy(data)
    items_not_list["days"][0]["items"] = "none"
    assert "days[0]: items expected a list" in build.validate(items_not_list)

    item_not_object = copy.deepcopy(data)
    item_not_object["days"][0]["items"][0] = "a ride"
    assert "days[0].items[0]: expected an object" in build.validate(item_not_object)


def test_receipt_container_shapes_are_checked(data: dict) -> None:
    no_receipts = copy.deepcopy(data)
    del no_receipts["receipts"]
    assert "receipts: missing" in build.validate(no_receipts)

    not_a_list = copy.deepcopy(data)
    not_a_list["receipts"] = {}
    assert "receipts: expected a list" in build.validate(not_a_list)

    not_an_object = copy.deepcopy(data)
    not_an_object["receipts"][0] = "flight"
    assert "receipts[0]: expected an object" in build.validate(not_an_object)

    bad_title = copy.deepcopy(data)
    bad_title["receipts"][0]["title"] = 3
    assert "receipts[0]: title expected a string" in build.validate(bad_title)

    blank_vendor = copy.deepcopy(data)
    blank_vendor["receipts"][0]["vendor"] = " "
    assert "receipts[0]: vendor empty" in build.validate(blank_vendor)

    wrong_vendor = copy.deepcopy(data)
    wrong_vendor["receipts"][0]["vendor"] = 4
    assert "receipts[0]: vendor expected a string" in build.validate(wrong_vendor)


def test_stipend_is_optional_but_checked_when_present(data: dict) -> None:
    without = copy.deepcopy(data)
    del without["stipend"]
    assert build.validate(without) == []

    not_object = copy.deepcopy(data)
    not_object["stipend"] = 75.0
    assert "stipend: expected an object" in build.validate(not_object)

    bad = copy.deepcopy(data)
    bad["stipend"] = {"desc": f"Meal stipend {EM_DASH} one day", "amt": "75"}
    problems = build.validate(bad)
    assert "stipend: desc contains an em dash" in problems
    assert "stipend: amt expected a number" in problems

    banned = copy.deepcopy(data)
    banned["stipend"]["desc"] = "Meal stipend, balance Due"
    assert "stipend: desc contains a banned word" in build.validate(banned)


# ------------------------------------------------------------------- loading


def test_load_expense_data_returns_the_fixture(fixture_dir: Path) -> None:
    loaded = build.load_expense_data(fixture_dir / "expense_data.json")
    assert loaded["company"]
    assert len(loaded["receipts"]) == 11


def test_load_expense_data_raises_with_joined_problems(tmp_path: Path, data: dict) -> None:
    del data["company"]
    first_item(data)["rid"] = "orphan_rid"
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError) as caught:
        build.load_expense_data(path)
    message = str(caught.value)
    assert "company: missing" in message
    assert "days[0].items[0]: rid has no receipt" in message
    assert "\n" in message


def test_load_expense_data_runs_the_disk_checks_when_given_a_directory(
    tmp_path: Path, data: dict
) -> None:
    folder = tmp_path / "receipts"
    folder.mkdir()
    (folder / f"{data['receipts'][0]['rid']}.pdf").write_bytes(b"not really a pdf")
    path = tmp_path / "expense_data.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    assert build.load_expense_data(path)["company"]  # no directory, no disk check
    with pytest.raises(ValueError, match="pdf cannot be read"):
        build.load_expense_data(path, folder)


# --------------------------------------------------------------------- money


def test_money_formats_usd_with_a_symbol() -> None:
    assert build.money(1234.5) == "$1,234.50"
    assert build.money(0) == "$0.00"
    assert build.money(-12.3) == "-$12.30"


def test_money_formats_other_currencies_with_a_code() -> None:
    assert build.money(1234.5, "EUR") == "1,234.50 EUR"
    assert build.money(-8, "JPY") == "-8.00 JPY"
    assert build.money(Decimal("19.99"), "GBP") == "19.99 GBP"


def test_an_amount_that_is_not_a_number_raises() -> None:
    """validate refuses junk before anything renders, so this is a bug, not a total."""
    with pytest.raises(ValueError, match="amount is not a number"):
        build._decimal("not a number")
    with pytest.raises(ValueError, match="amount is not a number"):
        build.money("not a number")


# -------------------------------------------------------------------- totals


def test_totals_match_a_hand_computation(data: dict) -> None:
    expected_days = []
    expected_expenses = Decimal("0.00")
    for day in data["days"]:
        subtotal = sum(
            (Decimal(str(item["amt"])) for item in day["items"]), start=Decimal("0")
        ).quantize(Decimal("0.01"))
        expected_days.append((day["label"], subtotal))
        expected_expenses += subtotal
    expected_stipend = Decimal(str(data["stipend"]["amt"])).quantize(Decimal("0.01"))

    figures = build.totals(data)
    assert figures["days"] == expected_days
    assert figures["expenses"] == expected_expenses
    assert figures["stipend"] == expected_stipend
    assert figures["total"] == expected_expenses + expected_stipend
    assert all(isinstance(value, Decimal) for _, value in figures["days"])


def test_stipend_is_zero_when_absent(data: dict) -> None:
    del data["stipend"]
    figures = build.totals(data)
    assert figures["stipend"] == Decimal("0.00")
    assert figures["total"] == figures["expenses"]


def test_pennies_never_drift(data: dict) -> None:
    data["days"] = [
        {
            "label": "One day of small change",
            "items": [
                {"desc": f"Small item {n}", "amt": 0.10, "rid": data["receipts"][0]["rid"]}
                for n in range(3)
            ],
        }
    ]
    del data["stipend"]

    assert 0.10 + 0.10 + 0.10 != 0.30  # the float sum this replaces
    figures = build.totals(data)
    assert figures["days"][0][1] == Decimal("0.30")
    assert figures["expenses"] == Decimal("0.30")
    assert figures["total"] == Decimal("0.30")
    assert build.money(figures["total"]) == "$0.30"


# -------------------------------------------------------------------- render


def test_the_packet_has_one_summary_table(packet: str) -> None:
    # Receipt bodies carry the vendors' own tables, so the count is taken over
    # the summary region, plus a whole document check on the summary class.
    assert summary_region(packet).count("<table") == 1
    assert packet.count('<table class="sum"') == 1


def test_the_packet_omits_the_removed_sections(packet: str) -> None:
    for banned in ("Notes", "Attn", "Submitted by"):
        assert banned not in packet
    assert 'href="#' not in packet


def test_the_summary_table_shows_no_rids(packet: str, data: dict) -> None:
    region = summary_region(packet)
    for receipt in data["receipts"]:
        assert receipt["rid"] not in region


def test_the_cover_block_names_the_trip(packet: str, data: dict) -> None:
    assert f"<title>Expense reimbursement packet, {data['company']}</title>" in packet
    assert "<h1>Expense reimbursement packet</h1>" in packet
    assert data["trip"] in packet
    assert data["traveler"] in packet
    assert '<meta name="author"' not in packet.lower()


def test_the_summary_table_rows_are_in_order(packet: str, data: dict) -> None:
    region = summary_region(packet)
    for day in data["days"]:
        assert f'<tr class="day"><td colspan="2">{day["label"]}</td></tr>' in region
    assert region.count("<td>Day subtotal</td>") == len(data["days"])
    assert region.index("<td>Expenses</td>") < region.index('<td colspan="2">Stipend</td>')
    assert region.index('<td colspan="2">Stipend</td>') < region.index("<td>Total</td>")
    assert build.money(build.totals(data)["total"]) in region


def test_the_stipend_rows_vanish_without_a_stipend(data: dict, receipts: Path) -> None:
    del data["stipend"]
    region = summary_region(build.render_packet(data, receipts))
    assert "Stipend" not in region
    assert "<td>Total</td>" in region


def test_every_receipt_gets_a_section_in_order(packet: str, data: dict) -> None:
    order = [
        line.split('data-rid="', 1)[1].split('"', 1)[0]
        for line in packet.split("\n")
        if line.startswith(RSEC_OPEN)
    ]
    assert order == [receipt["rid"] for receipt in data["receipts"]]
    for number, receipt in enumerate(data["receipts"], start=1):
        assert f'<div class="rhead">Receipt {number}: {receipt["title"]}</div>' in packet


def test_html_receipts_are_inserted_as_is(packet: str, data: dict, receipts: Path) -> None:
    rid = data["receipts"][0]["rid"]
    raw = (receipts / f"{rid}.html").read_text(encoding="utf-8")
    assert "eTicket confirmation" in raw
    assert raw.strip() in packet


def test_the_pdf_receipt_renders_a_card_with_its_page_count(packet: str) -> None:
    assert "PDF attachment, 2 pages, embedded in the PDF packet" in packet


def test_an_unreadable_pdf_is_reported_and_says_so_on_its_card(data: dict, tmp_path: Path) -> None:
    folder = tmp_path / "receipts"
    folder.mkdir()
    rid = data["receipts"][0]["rid"]
    (folder / f"{rid}.pdf").write_bytes(b"not really a pdf")

    assert f"receipt {rid}: pdf cannot be read" in build.validate(data, folder)

    rendered = build.render_packet(data, folder)
    assert '<div class="card">PDF attachment could not be read</div>' in rendered
    assert "0 pages" not in rendered


def test_the_image_receipt_renders_a_data_uri(packet: str, receipts: Path) -> None:
    raw = (receipts / "de472d8a39854951.png").read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    assert f'<img src="data:image/png;base64,{encoded}" alt="Receipt image">' in packet


def test_jpg_receipts_get_the_jpeg_media_type(tmp_path: Path, receipts: Path) -> None:
    shot = tmp_path / "photo.jpg"
    shot.write_bytes((receipts / "de472d8a39854951.png").read_bytes())
    assert build._receipt_body(shot).startswith('<img src="data:image/jpeg;base64,')


def test_text_receipts_are_escaped(tmp_path: Path, data: dict) -> None:
    rid = data["receipts"][0]["rid"]
    folder = tmp_path / "receipts"
    folder.mkdir()
    (folder / f"{rid}.txt").write_text(
        "Front desk note\n<script>alert('x')</script>\n", encoding="utf-8"
    )
    data["receipts"] = [data["receipts"][0]]
    data["days"] = []

    rendered = build.render_packet(data, folder)
    assert '<pre class="plain">' in rendered
    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in rendered
    assert "<script>" not in rendered


def test_a_missing_receipt_file_renders_a_placeholder(data: dict, tmp_path: Path) -> None:
    rendered = build.render_packet(data, tmp_path / "nothing_here")
    assert rendered.count('<p class="missing">Receipt file not found</p>') == len(data["receipts"])


def test_find_receipt_file_prefers_html(receipts: Path, data: dict) -> None:
    rid = data["receipts"][0]["rid"]
    assert build.find_receipt_file(receipts, rid).suffix == ".html"
    assert build.find_receipt_file(receipts, "no_such_rid") is None


@pytest.mark.parametrize("rid", ["../secret", "a/b", "", "x" * 65, "has space", "dot.dot", 7])
def test_find_receipt_file_refuses_a_rid_that_is_not_an_id(
    receipts: Path, rid: object, tmp_path: Path
) -> None:
    (tmp_path / "secret.html").write_text("<p>not yours</p>", encoding="utf-8")
    folder = tmp_path / "receipts"
    folder.mkdir()
    assert build.find_receipt_file(folder, rid) is None
    assert build.find_receipt_file(receipts, rid) is None


def test_find_receipt_file_refuses_an_absolute_rid(tmp_path: Path) -> None:
    outside = tmp_path / "secret.html"
    outside.write_text("<p>not yours</p>", encoding="utf-8")
    folder = tmp_path / "receipts"
    folder.mkdir()
    assert build.find_receipt_file(folder, str(outside.with_suffix(""))) is None


def test_find_receipt_file_refuses_a_symlink_that_points_outside(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "secret.html").write_text("<p>not yours</p>", encoding="utf-8")
    folder = tmp_path / "receipts"
    folder.mkdir()
    (folder / "leak.html").symlink_to(outside / "secret.html")

    assert (folder / "leak.html").is_file()
    assert build.find_receipt_file(folder, "leak") is None


def test_find_receipt_file_follows_a_symlink_that_stays_inside(tmp_path: Path) -> None:
    folder = tmp_path / "receipts"
    folder.mkdir()
    (folder / "real.html").write_text("<p>a receipt</p>", encoding="utf-8")
    (folder / "alias.html").symlink_to(folder / "real.html")
    assert build.find_receipt_file(folder, "alias") == (folder / "real.html").resolve()


def test_find_receipt_file_on_a_directory_that_is_not_there(tmp_path: Path) -> None:
    assert build.find_receipt_file(tmp_path / "nothing_here", "anything") is None


def test_a_missing_currency_defaults_to_usd(data: dict, receipts: Path) -> None:
    del data["currency"]
    assert "$" in summary_region(build.render_packet(data, receipts))


def test_a_non_usd_currency_reaches_every_row(data: dict, receipts: Path) -> None:
    data["currency"] = "EUR"
    region = summary_region(build.render_packet(data, receipts))
    assert "EUR" in region
    assert "$" not in region


# ---------------------------------------------------------------- prose gate


def test_the_rendered_packet_passes_the_prose_gate(packet: str, tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    denylist = check_prose.load_denylist(root / check_prose.DENYLIST_PATH)
    path = tmp_path / "packet.html"
    path.write_text(packet, encoding="utf-8")

    assert check_prose.scan([path], denylist) == []
    assert check_prose.scan_text("packet.html", packet, denylist, flag_banned_word=True) == []


# ----------------------------------------------------------------------- cli


def test_the_cli_writes_the_packet_and_prints_totals(
    fixture_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "nested" / "packet.html"
    code = build.main(
        [
            str(fixture_dir / "expense_data.json"),
            "--receipts",
            str(fixture_dir / "receipts"),
            "--out",
            str(out),
        ]
    )
    assert code == 0
    assert out.is_file()

    payload = json.loads((fixture_dir / "expense_data.json").read_text(encoding="utf-8"))
    figures = build.totals(payload)
    printed = capsys.readouterr().out.strip()
    assert printed == (
        f"expenses {build.money(figures['expenses'])}, "
        f"stipend {build.money(figures['stipend'])}, "
        f"total {build.money(figures['total'])}"
    )
    assert out.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_the_cli_runs_the_disk_checks_on_the_receipts_it_was_given(
    tmp_path: Path, data: dict
) -> None:
    """A junk pdf in the receipts directory stops the build rather than reaching a card."""
    folder = tmp_path / "receipts"
    folder.mkdir()
    rid = data["receipts"][0]["rid"]
    (folder / f"{rid}.pdf").write_bytes(b"not really a pdf")
    path = tmp_path / "expense_data.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "packet.html"

    with pytest.raises(ValueError, match=f"receipt {rid}: pdf cannot be read"):
        build.main([str(path), "--receipts", str(folder), "--out", str(out)])
    assert not out.exists()
