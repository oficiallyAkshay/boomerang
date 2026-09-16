"""Tests for the two pass windowed search and the pooled fetch.

The concurrency tests use a source that counts how many calls are in flight at
once, under a lock, and holds each call at a barrier so a caller that ran the
work one item at a time would break the barrier rather than pass by accident.
The ordering tests do the opposite: they make the queries finish in the wrong
order on purpose and check that what comes back is still ordered by query.
"""

from __future__ import annotations

import json
import sys
import threading
import types
from datetime import date
from pathlib import Path

import build
import fetch
import pytest
from fetch import (
    BODY_WORKERS,
    GENERIC_TERMS,
    SEARCH_WORKERS,
    Query,
    attachment_ext,
    build_queries,
    fetch_all,
    main,
    search_all,
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
    # The pool decides who is fetched when; the returned order is the merge.
    assert sorted(source.fetched) == ["aaa", "bbb", "ccc"]


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


# ------------------------------------------------------------- running at once

BARRIER_TIMEOUT = 10.0


def numbered_queries(count: int) -> list[Query]:
    """Distinct queries, so a fake source can tell one from another."""
    return [
        Query(terms=[f"q{index}"], from_domains=[], after=START, before=END)
        for index in range(count)
    ]


def _barrier(width: int) -> threading.Barrier | None:
    """A rendezvous for width callers, or None when one caller is expected."""
    return threading.Barrier(width, timeout=BARRIER_TIMEOUT) if width > 1 else None


class ConcurrentSource:
    """A source that records how many of its calls overlapped.

    ``peak`` is the most calls that were ever in flight together, counted under
    a lock. Each phase can be given a width, and when it has one every call in
    that phase waits at a barrier before answering: a caller that worked
    through the list one item at a time would sit there until the barrier
    timed out, so the test fails loudly rather than quietly proving nothing.
    """

    def __init__(self, results, messages, *, search_width: int = 1, get_width: int = 1):
        self.results = results
        self.messages = messages
        self.search_barrier = _barrier(search_width)
        self.get_barrier = _barrier(get_width)
        self.searched: list[str] = []
        self.fetched: list[str] = []
        self._lock = threading.Lock()
        self.live = 0
        self.peak = 0

    def _enter(self) -> None:
        with self._lock:
            self.live += 1
            self.peak = max(self.peak, self.live)

    def _leave(self) -> None:
        with self._lock:
            self.live -= 1

    def search(self, query: str) -> list[str]:
        self._enter()
        try:
            if self.search_barrier is not None:
                self.search_barrier.wait()
            with self._lock:
                self.searched.append(query)
            return list(self.results.get(query, []))
        finally:
            self._leave()

    def get(self, rid: str) -> dict:
        self._enter()
        try:
            if self.get_barrier is not None:
                self.get_barrier.wait()
            with self._lock:
                self.fetched.append(rid)
            return self.messages[rid]
        finally:
            self._leave()


def test_the_searches_run_at_the_same_time():
    """Four queries, four callers in flight, and the merge still by query."""
    queries = numbered_queries(SEARCH_WORKERS)
    results = {to_gmail(q): [f"rid{index}"] for index, q in enumerate(queries)}
    source = ConcurrentSource(results, {}, search_width=SEARCH_WORKERS)

    assert search_all(source, queries) == [f"rid{index}" for index in range(SEARCH_WORKERS)]
    assert source.peak == SEARCH_WORKERS


def test_the_search_pool_never_grows_past_four():
    """Twelve queries still only ever have four callers in the mailbox."""
    queries = numbered_queries(3 * SEARCH_WORKERS)
    results = {to_gmail(q): [f"rid{index}"] for index, q in enumerate(queries)}
    source = ConcurrentSource(results, {}, search_width=SEARCH_WORKERS)

    assert search_all(source, queries) == [f"rid{index}" for index in range(len(queries))]
    assert source.peak == SEARCH_WORKERS


def test_no_queries_means_no_pool_at_all():
    assert search_all(ConcurrentSource({}, {}), []) == []


class OutOfOrderSource:
    """A source whose first query is the last one to answer.

    The second query releases the first, so the completion order is the
    reverse of the query order every single run. Whatever comes back has to be
    in query order regardless, which is the whole point of the merge.
    """

    def __init__(self, first: str, answers: dict[str, list[str]]):
        self.first = first
        self.answers = answers
        self.released = threading.Event()

    def search(self, query: str) -> list[str]:
        if query == self.first:
            assert self.released.wait(BARRIER_TIMEOUT), "the later query never answered"
        else:
            self.released.set()
        return list(self.answers.get(query, []))


def test_the_order_is_the_query_order_not_the_answer_order():
    queries = numbered_queries(2)
    first, second = (to_gmail(q) for q in queries)
    source = OutOfOrderSource(first, {first: ["slow", "shared"], second: ["fast", "shared"]})

    assert search_all(source, queries) == ["slow", "shared", "fast"]


def test_the_bodies_are_fetched_at_the_same_time(tmp_path: Path):
    rids = [f"body{index}" for index in range(BODY_WORKERS)]
    queries = numbered_queries(1)
    source = ConcurrentSource(
        {to_gmail(queries[0]): rids},
        {rid: message() for rid in rids},
        get_width=BODY_WORKERS,
    )

    written, rejected, skipped, failed = fetch_all(source, queries, tmp_path)
    assert (written, rejected, skipped, failed) == (rids, [], [], [])
    assert source.peak == BODY_WORKERS
    for rid in rids:
        assert (tmp_path / f"{rid}.meta.json").exists()


def test_one_worker_fetches_one_body_at_a_time(tmp_path: Path):
    rids = ["body0", "body1", "body2"]
    queries = numbered_queries(1)
    source = ConcurrentSource({to_gmail(queries[0]): rids}, {rid: message() for rid in rids})

    written, _, _, _ = fetch_all(source, queries, tmp_path, workers=1)
    assert written == rids
    assert source.fetched == rids
    assert source.peak == 1


def test_written_and_failed_keep_first_seen_order_under_the_pool(tmp_path: Path):
    """An empty message in the middle lands in failed, and the rest keep order."""
    rids = ["body0", "body1", "body2"]
    queries = numbered_queries(1)
    source = ConcurrentSource(
        {to_gmail(queries[0]): rids},
        {
            "body0": message(),
            "body1": message(html=None, text=None),
            "body2": message(),
        },
        get_width=BODY_WORKERS,
    )

    written, rejected, skipped, failed = fetch_all(source, queries, tmp_path)
    assert written == ["body0", "body2"]
    assert failed == ["body1"]
    assert (rejected, skipped) == ([], [])


class RefusingSource:
    """A source that answers four rids and raises on the fifth."""

    def __init__(self, rids: list[str], bad: str):
        self.rids = rids
        self.bad = bad

    def search(self, query: str) -> list[str]:
        return list(self.rids)

    def get(self, rid: str) -> dict:
        if rid == self.bad:
            raise TimeoutError("the mailbox did not answer")
        return message()


def test_one_message_the_source_refuses_does_not_end_the_run(tmp_path: Path, capsys):
    """Thirty receipts and one timeout is twenty nine receipts, not none.

    The rid that raised lands in failed, so nothing on disk marks it done and
    the next run asks for it again. The four lists still account for every rid
    the search returned, which is the only thing a caller can check against.
    """
    rids = [f"body{index}" for index in range(5)]
    queries = numbered_queries(1)
    source = RefusingSource(rids, "body2")

    written, rejected, skipped, failed = fetch_all(source, queries, tmp_path)

    assert written == ["body0", "body1", "body3", "body4"]
    assert failed == ["body2"]
    assert (rejected, skipped) == ([], [])
    assert sorted(written + rejected + skipped + failed) == sorted(rids)
    assert "fetch: body2 could not be fetched (TimeoutError)" in capsys.readouterr().out
    assert not (tmp_path / "body2.meta.json").exists()


def test_a_write_that_raises_is_reported_the_same_way(tmp_path: Path, capsys):
    """The failure need not be the mailbox's; a refused write reads the same."""
    queries = numbered_queries(1)
    source = ConcurrentSource({to_gmail(queries[0]): ["body0"]}, {"body0": message()})
    blocked = tmp_path / "out"
    blocked.mkdir()
    (blocked / "body0.html").mkdir()

    written, _, _, failed = fetch_all(source, queries, blocked)

    assert (written, failed) == ([], ["body0"])
    assert "fetch: body0 could not be fetched (IsADirectoryError)" in capsys.readouterr().out


def test_fetch_re_exports_the_one_rid_pattern() -> None:
    """build defines it; fetch and gmail_cli read the same object."""
    assert fetch.RID_RE is build.RID_RE


def test_the_cli_passes_its_worker_count_through(tmp_path: Path, monkeypatch, capsys):
    seen: dict[str, int] = {}

    def spy(source, queries, out_dir, workers=BODY_WORKERS):
        seen["workers"] = workers
        return [], [], [], []

    fake = types.ModuleType("gmail_cli")
    fake.GmailSource = lambda: None
    monkeypatch.setitem(sys.modules, "gmail_cli", fake)
    monkeypatch.setattr(fetch, "fetch_all", spy)

    code = main(
        [
            "--start",
            "2026-06-08",
            "--end",
            "2026-06-09",
            "--out",
            str(tmp_path / "receipts"),
            "--workers",
            "2",
        ]
    )
    assert code == 0
    assert seen == {"workers": 2}
    assert "0 written" in capsys.readouterr().out


def test_the_cli_defaults_to_three_workers(tmp_path: Path, monkeypatch, capsys):
    seen: dict[str, int] = {}

    def spy(source, queries, out_dir, workers=None):
        seen["workers"] = workers
        return [], [], [], []

    fake = types.ModuleType("gmail_cli")
    fake.GmailSource = lambda: None
    monkeypatch.setitem(sys.modules, "gmail_cli", fake)
    monkeypatch.setattr(fetch, "fetch_all", spy)

    assert main(["--start", "2026-06-08", "--end", "2026-06-09", "--out", str(tmp_path)]) == 0
    assert seen == {"workers": BODY_WORKERS}
    capsys.readouterr()
