"""Shared test fixtures."""

from __future__ import annotations

import shutil
from pathlib import Path

import clean
import pytest
from fixtures.make_fixture import make


@pytest.fixture(scope="session")
def fixture_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A generated packet input: expense_data.json plus receipts/."""
    target = tmp_path_factory.mktemp("fixture")
    make(target)
    return target


@pytest.fixture(scope="session")
def cleaned_receipts(fixture_dir: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The receipts as step 5 leaves them, which is what a packet is built from."""
    target = tmp_path_factory.mktemp("cleaned")
    for path in sorted((fixture_dir / "receipts").iterdir()):
        if path.suffix.lower() == ".html":
            fragment = clean.clean_html(path.read_text(encoding="utf-8"))
            (target / path.name).write_text(fragment, encoding="utf-8")
        else:
            shutil.copy2(path, target / path.name)
    return target
