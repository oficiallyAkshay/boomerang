"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
from fixtures.make_fixture import make


@pytest.fixture(scope="session")
def fixture_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A generated packet input: expense_data.json plus receipts/."""
    target = tmp_path_factory.mktemp("fixture")
    make(target)
    return target
