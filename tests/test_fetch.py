"""Tests for the two pass windowed search and the one at a time fetch."""

from __future__ import annotations

import json
import sys
import types
from datetime import date
from pathlib import Path

import build
import pytest
from fetch import (
    GENERIC_TERMS,
    Query,
    attachment_ext,
    build_queries,
    fetch_all,
    main,
    to_gmail,
    write_message,
)

START = date(2026, 6, 8)
END = date(2026, 6, 9)

# A two day window, padded a day each side, with before: pushed one more day
# because Gmail treats it as exclusive.
GENERIC_QUERY = (
    "after:2026/06/07 before:2026/06/11 "
    '(receipt OR "your receipt" OR confirmation OR "your ride" OR "your trip" '
    'OR "your order" OR eticket OR e-ticket OR itinerary OR folio OR invoice)'
)
LYFT_QUERY = "after:2026/06/07 before:2026/06/11 (from:lyftmail.com OR from:lyft.com)"

VENDORS = {
    "lyft": {"name": "lyft", "sender_domains": ["lyftmail.com", "lyft.com"]},
    "uber": {"name": "uber", "sender_domains": ["uber.com"]},
    "nowhere": {"name": "nowhere", "sender_domains": []},
    "missing": {"name": "missing"},
}


class FakeSource:
    """A source that answers from a dict, and counts the gets."""

    def __init__(self, results: dict[str, list[str]], messages: dict[str, dict]):
        self.results = results
        self.messages = messages
        self.searched: list[str] = []
        self.fetched: list[str] = []

    def search(self, query: str) -> list[str]:
        self.searched.append(query)
        return list(self.results.get(query, []))

    def get(self, rid: str) -> dict:
        self.fetched.append(rid)
        return self.messages[rid]


def message(**over) -> dict:
    base = {
        "html": "<p>a ride</p>",
        "text": "a ride",
        "from": "receipts@lyftmail.example",
        "subject": "Your ride with a driver",
        "date": "Mon, 8 Jun 2026 20:14:00 -0700",
        "attachments": [],
    }
    base.update(over)
    return base


# ------------------------------------------------------------ build_queries


def test_pass_one_is_a_single_generic_query_with_no_domains():
    queries = build_queries(START, END, {})
    assert len(queries) == 1
    assert queries[0].terms == GENERIC_TERMS
    assert queries[0].from_domains == []


def test_window_is_padded_one_day_each_side():
    queries = build_queries(START, END, {})
    assert queries[0].after == date(2026, 6, 7)
    assert queries[0].before == date(2026, 6, 10)


def test_pass_two_is_one_query_per_vendor_that_lists_domains():
    queries = build_queries(START, END, VENDORS)
    assert len(queries) == 3
    assert [q.from_domains for q in queries[1:]] == [["lyftmail.com", "lyft.com"], ["uber.com"]]
    assert all(q.terms == [] for q in queries[1:])


def test_vendors_without_domains_produce_no_query():
    queries = build_queries(START, END, {"nowhere": VENDORS["nowhere"]})
    assert len(queries) == 1


def test_a_none_rules_entry_is_tolerated():
    queries = build_queries(START, END, {"broken": None})
    assert len(queries) == 1


# ----------------------------------------------------------------- to_gmail


def test_generic_query_string_is_exact():
    assert to_gmail(build_queries(START, END, {})[0]) == GENERIC_QUERY


def test_vendor_query_string_is_exact():
    assert to_gmail(build_queries(START, END, VENDORS)[1]) == LYFT_QUERY


def test_before_is_pushed_one_day_because_gmail_excludes_it():
    q = Query(terms=[], from_domains=[], after=date(2026, 6, 7), before=date(2026, 6, 10))
    assert q.before == date(2026, 6, 10)
    assert "before:2026/06/11" in to_gmail(q)


def test_multi_word_terms_are_quoted_and_single_words_are_not():
    q = Query(terms=["receipt", "your ride"], from_domains=[], after=START, before=END)
    assert to_gmail(q).endswith('(receipt OR "your ride")')


def test_a_query_with_both_terms_and_domains_renders_both_groups():
    q = Query(terms=["folio"], from_domains=["hotel.example"], after=START, before=END)
    assert to_gmail(q) == ("after:2026/06/08 before:2026/06/10 (folio) (from:hotel.example)")


# ---------------------------------------------------------------- fetch_all


def test_fetch_all_writes_body_and_meta(tmp_path: Path):
    rid = "e20f48c14c0aa535"
    source = FakeSource({GENERIC_QUERY: [rid]}, {rid: message()})
    written, rejected, skipped, failed = fetch_all(source, build_queries(START, END, {}), tmp_path)

    assert (written, rejected, skipped, failed) == ([rid], [], [], [])
    assert (tmp_path / f"{rid}.html").read_text(encoding="utf-8") == "<p>a ride</p>"
    assert (tmp_path / f"{rid}.txt").read_text(encoding="utf-8") == "a ride"
    meta = json.loads((tmp_path / f"{rid}.meta.json").read_text(encoding="utf-8"))
    assert meta["subject"] == "Your ride with a driver"
    assert meta["from"] == "receipts@lyftmail.example"
    assert meta["date"].startswith("Mon, 8 Jun 2026")
    assert meta["attachments"] == []


def test_a_message_with_only_one_body_writes_only_that_file(tmp_path: Path):
    rid = "textonly"
    source = FakeSource({GENERIC_QUERY: [rid]}, {rid: message(html=None)})
    fetch_all(source, build_queries(START, END, {}), tmp_path)
    assert not (tmp_path / f"{rid}.html").exists()
    assert (tmp_path / f"{rid}.txt").exists()


def test_ids_are_deduped_across_queries_keeping_first_seen_order(tmp_path: Path):
    source = FakeSource(
        {GENERIC_QUERY: ["aaa", "bbb"], LYFT_QUERY: ["bbb", "ccc"]},
        {rid: message() for rid in ("aaa", "bbb", "ccc")},
    )
    queries = [build_queries(START, END, VENDORS)[0], build_queries(START, END, VENDORS)[1]]
    written, _, _, _ = fetch_all(source, queries, tmp_path)
    assert written == ["aaa", "bbb", "ccc"]
    assert source.fetched == ["aaa", "bbb", "ccc"]


def test_a_message_already_on_disk_is_not_fetched_again(tmp_path: Path):
    rid = "alreadyhere"
    (tmp_path / f"{rid}.meta.json").write_text("{}", encoding="utf-8")
    source = FakeSource({GENERIC_QUERY: [rid]}, {rid: message()})
    written, rejected, skipped, failed = fetch_all(source, build_queries(START, END, {}), tmp_path)
    assert (written, rejected, skipped, failed) == ([], [], [rid], [])
    assert source.fetched == []


def test_the_first_attachment_of_each_kind_takes_the_plain_name(tmp_path: Path):
    rid = "withfolio"
    source = FakeSource(
        {GENERIC_QUERY: [rid]},
        {
            rid: message(
                attachments=[
                    ("Folio.PDF", b"%PDF-1.4 folio"),
                    ("scan.JPEG", b"\xff\xd8jpeg"),
                ]
            )
        },
    )
    fetch_all(source, build_queries(START, END, {}), tmp_path)
    assert (tmp_path / f"{rid}.pdf").read_bytes() == b"%PDF-1.4 folio"
    assert (tmp_path / f"{rid}.jpg").read_bytes() == b"\xff\xd8jpeg"
    meta = json.loads((tmp_path / f"{rid}.meta.json").read_text(encoding="utf-8"))
    assert meta["attachments"] == [f"{rid}.pdf", f"{rid}.jpg"]


def test_later_attachments_of_one_kind_are_numbered(tmp_path: Path):
    rid = "twofolios"
    source = FakeSource(
        {GENERIC_QUERY: [rid]},
        {
            rid: message(
                attachments=[
                    ("first.pdf", b"%PDF one"),
                    ("second.pdf", b"%PDF two"),
                    ("third.pdf", b"%PDF three"),
                    ("shot.png", b"\x89PNG"),
                ]
            )
        },
    )
    fetch_all(source, build_queries(START, END, {}), tmp_path)
    assert (tmp_path / f"{rid}.pdf").read_bytes() == b"%PDF one"
    assert (tmp_path / f"{rid}.1.pdf").read_bytes() == b"%PDF two"
    assert (tmp_path / f"{rid}.2.pdf").read_bytes() == b"%PDF three"
    assert (tmp_path / f"{rid}.png").read_bytes() == b"\x89PNG"
    meta = json.loads((tmp_path / f"{rid}.meta.json").read_text(encoding="utf-8"))
    assert meta["attachments"] == [f"{rid}.pdf", f"{rid}.1.pdf", f"{rid}.2.pdf", f"{rid}.png"]


@pytest.mark.parametrize(
    "filename,expected_suffix",
    [("Folio.PDF", ".pdf"), ("shot.png", ".png"), ("scan.JPEG", ".jpg"), ("photo.jpg", ".jpg")],
)
def test_what_write_message_names_is_what_build_finds(
    tmp_path: Path, filename: str, expected_suffix: str
):
    """The round trip: the writer's name is the name the builder looks for."""
    rid = "roundtrip"
    attachment = message(html=None, text=None, attachments=[(filename, b"x")])
    meta = write_message(tmp_path, rid, attachment)
    assert meta["attachments"] == [f"{rid}{expected_suffix}"]
    found = build.find_receipt_file(tmp_path, rid)
    assert found is not None
    assert found.name == f"{rid}{expected_suffix}"


def test_a_second_attachment_of_a_kind_is_not_the_one_build_finds(tmp_path: Path):
    rid = "twopdfs"
    write_message(tmp_path, rid, message(attachments=[("a.pdf", b"first"), ("b.pdf", b"second")]))
    # build prefers .html, and the plain name is the only one it would ever read.
    assert build.find_receipt_file(tmp_path, rid).name == f"{rid}.html"
    assert (tmp_path / f"{rid}.1.pdf").exists()


def test_an_executable_attachment_is_skipped_and_noted(tmp_path: Path):
    rid = "withexe"
    source = FakeSource(
        {GENERIC_QUERY: [rid]},
        {rid: message(attachments=[("invoice.exe", b"MZ"), ("folio.pdf", b"%PDF")])},
    )
    fetch_all(source, build_queries(START, END, {}), tmp_path)
    assert not list(tmp_path.glob("*.exe"))
    meta = json.loads((tmp_path / f"{rid}.meta.json").read_text(encoding="utf-8"))
    assert meta["attachments"] == [f"{rid}.pdf"]
    assert meta["attachments_skipped"] == ["invoice.exe"]


# ------------------------------------------------------------------- failed


def test_a_message_with_no_body_and_no_kept_attachment_writes_nothing(tmp_path: Path):
    rid = "emptyone"
    source = FakeSource(
        {GENERIC_QUERY: [rid]},
        {rid: message(html=None, text="", attachments=[("invoice.exe", b"MZ")])},
    )
    written, rejected, skipped, failed = fetch_all(source, build_queries(START, END, {}), tmp_path)
    assert (written, rejected, skipped, failed) == ([], [], [], [rid])
    assert list(tmp_path.iterdir()) == []


def test_an_empty_message_is_asked_for_again_on_the_next_run(tmp_path: Path):
    """No meta file means no skip, which is the whole point of failing loudly."""
    rid = "emptytwo"
    source = FakeSource({GENERIC_QUERY: [rid]}, {rid: message(html=None, text=None)})
    queries = build_queries(START, END, {})
    fetch_all(source, queries, tmp_path)
    fetch_all(source, queries, tmp_path)
    assert source.fetched == [rid, rid]


def test_write_message_returns_none_for_an_empty_message(tmp_path: Path):
    assert write_message(tmp_path, "nothing", message(html=None, text=None)) is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "x" * 65, "has space", "dot.dot"])
def test_a_message_id_that_is_not_a_safe_file_name_is_refused(tmp_path: Path, bad: str):
    source = FakeSource({GENERIC_QUERY: [bad]}, {})
    written, rejected, skipped, failed = fetch_all(source, build_queries(START, END, {}), tmp_path)
    assert (written, rejected, skipped, failed) == ([], [bad], [], [])
    assert source.fetched == []
    assert list(tmp_path.iterdir()) == []


def test_the_out_directory_is_created(tmp_path: Path):
    target = tmp_path / "deep" / "receipts"
    fetch_all(FakeSource({}, {}), build_queries(START, END, {}), target)
    assert target.is_dir()


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("a.pdf", "pdf"),
        ("a.PNG", "png"),
        ("a.jpg", "jpg"),
        ("a.jpeg", "jpg"),
        ("a.exe", None),
        ("noext", None),
        ("", None),
    ],
)
def test_attachment_ext(filename: str, expected: str | None):
    assert attachment_ext(filename) == expected


def test_write_message_returns_the_meta_it_wrote(tmp_path: Path):
    meta = write_message(tmp_path, "rid1", message())
    assert meta["subject"] == "Your ride with a driver"


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "x" * 65, "has space", "dot.dot", 7, None])
def test_write_message_refuses_a_rid_that_is_not_a_safe_file_name(tmp_path: Path, bad: object):
    with pytest.raises(ValueError, match="not a valid id"):
        write_message(tmp_path, bad, message())
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------- cli


def write_vendor_rules(vendors_dir: Path, name: str, sender_domains: list[str]) -> None:
    """A complete rules.json, since the CLI reads it through clean's loader."""
    folder = vendors_dir / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "rules.json").write_text(
        json.dumps(
            {
                "name": name,
                "display": name.title(),
                "sender_domains": sender_domains,
                "subject_patterns": [],
                "strip_regex": [],
                "unwrap_links_matching": [],
                "amount_regex": None,
                "date_regex": None,
                "notes": "",
            }
        ),
        encoding="utf-8",
    )


def test_dry_run_prints_both_passes_and_stops(tmp_path: Path, capsys):
    write_vendor_rules(tmp_path / "vendors", "lyft", ["lyftmail.com", "lyft.com"])
    code = main(
        [
            "--start",
            "2026-06-08",
            "--end",
            "2026-06-09",
            "--out",
            str(tmp_path / "receipts"),
            "--vendors",
            str(tmp_path / "vendors"),
            "--dry-run",
        ]
    )
    assert code == 0
    assert capsys.readouterr().out.splitlines() == [GENERIC_QUERY, LYFT_QUERY]


def test_the_cli_fetches_through_the_gmail_source(tmp_path: Path, capsys, monkeypatch):
    rid = "cliRid"
    source = FakeSource({GENERIC_QUERY: [rid]}, {rid: message()})
    fake = types.ModuleType("gmail_cli")
    fake.GmailSource = lambda: source
    monkeypatch.setitem(sys.modules, "gmail_cli", fake)

    code = main(
        [
            "--start",
            "2026-06-08",
            "--end",
            "2026-06-09",
            "--out",
            str(tmp_path / "receipts"),
        ]
    )
    assert code == 0
    assert (tmp_path / "receipts" / f"{rid}.html").exists()
    assert "1 written" in capsys.readouterr().out


def test_the_cli_names_every_message_that_came_back_empty(tmp_path: Path, capsys, monkeypatch):
    rid = "emptyRid"
    source = FakeSource({GENERIC_QUERY: [rid]}, {rid: message(html=None, text=None)})
    fake = types.ModuleType("gmail_cli")
    fake.GmailSource = lambda: source
    monkeypatch.setitem(sys.modules, "gmail_cli", fake)

    code = main(["--start", "2026-06-08", "--end", "2026-06-09", "--out", str(tmp_path / "r")])
    out = capsys.readouterr().out
    assert code == 0
    assert f"fetch: {rid} came back empty" in out
    assert "1 empty" in out
