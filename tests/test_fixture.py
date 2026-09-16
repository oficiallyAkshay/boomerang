"""Tests for the synthetic fixture generator."""

from __future__ import annotations

import json
import re
from pathlib import Path

import check_prose
from fixtures.make_fixture import make, make_vendor_samples
from pypdf import PdfReader

RID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
EM_DASH = "\u2014"  # written as an escape so this file stays clean
BANNED_WORD = re.compile(r"\bdue\b", re.IGNORECASE)
KINDS = {".html", ".txt", ".pdf", ".png", ".jpg"}


def validate(data: dict, receipts_dir: Path) -> list[str]:
    """The schema check from docs/interfaces.md, written out inline."""
    problems: list[str] = []
    for key in ("company", "trip", "traveler"):
        if not isinstance(data.get(key), str) or not data[key]:
            problems.append(f"{key} must be a non empty string")
    known = set()
    for receipt in data.get("receipts", []):
        rid = receipt.get("rid", "")
        if not RID_RE.match(rid):
            problems.append(f"bad rid {rid!r}")
        if not receipt.get("title") or not receipt.get("vendor"):
            problems.append(f"receipt {rid} needs a title and a vendor")
        matches = [p for p in receipts_dir.glob(f"{rid}.*") if p.suffix in KINDS]
        if len(matches) != 1:
            problems.append(f"receipt {rid} needs exactly one file on disk")
        known.add(rid)
    for day in data.get("days", []):
        if not day.get("label"):
            problems.append("every day needs a label")
        for item in day.get("items", []):
            desc, amt, rid = item.get("desc", ""), item.get("amt"), item.get("rid", "")
            if rid not in known:
                problems.append(f"item rid {rid!r} is not in receipts")
            if not isinstance(amt, int | float) or round(float(amt), 2) != float(amt):
                problems.append(f"amount {amt!r} needs at most 2 decimals")
            if EM_DASH in desc or BANNED_WORD.search(desc):
                problems.append(f"description {desc!r} breaks the prose rules")
    return problems


def test_fixture_is_deterministic(tmp_path: Path) -> None:
    first = make(tmp_path / "a")
    second = make(tmp_path / "b")
    assert first.read_bytes() == second.read_bytes()
    left = sorted((tmp_path / "a" / "receipts").iterdir())
    right = sorted((tmp_path / "b" / "receipts").iterdir())
    assert [p.name for p in left] == [p.name for p in right]
    for one, other in zip(left, right, strict=True):
        assert one.read_bytes() == other.read_bytes()


def test_a_different_seed_changes_the_amounts(tmp_path: Path) -> None:
    one = json.loads(make(tmp_path / "a", seed=1).read_text())
    two = json.loads(make(tmp_path / "b", seed=2).read_text())
    assert one != two
    assert [d["label"] for d in one["days"]] == [d["label"] for d in two["days"]]


def test_fixture_matches_the_schema(fixture_dir: Path) -> None:
    data = json.loads((fixture_dir / "expense_data.json").read_text())
    assert validate(data, fixture_dir / "receipts") == []


def test_totals_add_up(fixture_dir: Path) -> None:
    data = json.loads((fixture_dir / "expense_data.json").read_text())
    subtotals = [round(sum(i["amt"] for i in d["items"]), 2) for d in data["days"]]
    expenses = round(sum(subtotals), 2)
    total = round(expenses + data["stipend"]["amt"], 2)
    assert expenses > 0
    assert total == round(expenses + 75.0, 2)
    assert len(data["receipts"]) == 11


def test_the_trip_shape_is_the_documented_one(fixture_dir: Path) -> None:
    data = json.loads((fixture_dir / "expense_data.json").read_text())
    personal = [d for d in data["days"] if "personal day" in d["label"]]
    assert len(personal) == 1
    assert len(personal[0]["items"]) == 1
    assert "airport" in personal[0]["items"][0]["desc"]
    netted = [i for d in data["days"] for i in d["items"] if "refund netted" in i["desc"]]
    assert len(netted) == 1


def test_receipt_files_cover_every_kind(fixture_dir: Path) -> None:
    receipts = fixture_dir / "receipts"
    assert len(list(receipts.glob("*.html"))) == 8
    assert len(list(receipts.glob("*.txt"))) == 1
    assert len(list(receipts.glob("*.png"))) == 1
    folio = next(receipts.glob("*.pdf"))
    reader = PdfReader(str(folio))
    assert len(reader.pages) == 2
    assert "folio page 1 of 2" in reader.pages[0].extract_text()


def test_receipts_look_like_vendor_mail(fixture_dir: Path) -> None:
    receipts = fixture_dir / "receipts"
    blobs = [p.read_text(encoding="utf-8") for p in sorted(receipts.glob("*.html"))]
    joined = "\n".join(blobs)
    assert "Thanks for riding with" in joined
    assert "Paid with Visa Ending in 4321" in joined
    assert "Track your order" in joined
    assert "eTicket confirmation" in joined
    assert joined.count("https://example.com/track?u=") >= 10
    assert "data:image/png;base64," in joined
    plaintext = next(receipts.glob("*.txt")).read_text(encoding="utf-8")
    assert plaintext.startswith("From: ")
    assert "Subject: " in plaintext


def test_generated_files_pass_the_privacy_gate(fixture_dir: Path) -> None:
    denylist = check_prose.load_denylist(Path(__file__).parent / "pii_denylist.sha256")
    assert denylist
    files = [fixture_dir / "expense_data.json", *sorted((fixture_dir / "receipts").iterdir())]
    assert check_prose.scan(files, denylist) == []


def test_vendor_samples_are_written(tmp_path: Path) -> None:
    make_vendor_samples(tmp_path / "templates")
    names = sorted(p.name for p in (tmp_path / "templates").iterdir())
    assert names == ["doordash.html", "lyft.html", "plaintext.txt", "uber.html", "united.html"]
