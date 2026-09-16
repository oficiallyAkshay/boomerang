"""Tests for the synthetic fixture generator."""

from __future__ import annotations

import json
from pathlib import Path

import build
import check_prose
from fixtures.make_fixture import make
from pypdf import PdfReader


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


def test_the_fixture_validates_against_the_real_validator(fixture_dir: Path) -> None:
    """One check, and it is the one the builder runs, not a copy of it."""
    data = json.loads((fixture_dir / "expense_data.json").read_text())
    problems = build.validate(data, fixture_dir / "receipts")
    # These receipts are raw vendor mail, which is what clean.py takes, so the
    # validator says exactly that, once per HTML receipt, and nothing else.
    assert all(p.endswith("looks uncleaned, run clean.py first") for p in problems)
    assert len(problems) == len(list((fixture_dir / "receipts").glob("*.html")))


def test_receipt_files_cover_every_kind(fixture_dir: Path) -> None:
    receipts = fixture_dir / "receipts"
    assert len(list(receipts.glob("*.html"))) == 8
    assert len(list(receipts.glob("*.txt"))) == 1
    assert len(list(receipts.glob("*.png"))) == 1
    folio = next(receipts.glob("*.pdf"))
    reader = PdfReader(str(folio))
    assert len(reader.pages) == 2
    assert "folio page 1 of 2" in reader.pages[0].extract_text()


def test_generated_files_pass_the_privacy_gate(fixture_dir: Path) -> None:
    denylist = check_prose.load_denylist(Path(__file__).parent / "pii_denylist.sha256")
    assert denylist
    files = [fixture_dir / "expense_data.json", *sorted((fixture_dir / "receipts").iterdir())]
    assert check_prose.scan(files, denylist) == []
