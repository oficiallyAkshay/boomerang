"""Tests for the clones badge writer and the workflow that publishes it.

Nothing here opens a socket. ``urllib.request.urlopen`` is replaced with a
stand-in that hands back a canned payload or raises the error under test, so
the suite proves what the script would do with GitHub's answer without asking
GitHub anything.

The last two tests are documentation checks rather than code checks. One pins
the owner's instruction that this workflow runs on a push and never on a timer;
the other pins the README badge to the branch the workflow actually writes,
because a badge pointed at the wrong branch fails silently as a picture that
says nothing is there.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from ci import clones_badge

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "badges.yml"
README_PATH = REPO_ROOT / "README.md"

REPO = "oficiallyAkshay/boomerang"


def payload(days: list[int], uniques: int) -> dict:
    """A clones response shaped the way the endpoint shapes it."""
    return {
        "count": sum(days),
        "uniques": uniques,
        "clones": [
            {"timestamp": f"2026-09-{index + 1:02d}T00:00:00Z", "count": count, "uniques": 1}
            for index, count in enumerate(days)
        ],
    }


class FakeResponse(io.BytesIO):
    """Enough of an http response for ``json.load`` inside a ``with``."""

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@pytest.fixture
def answers(monkeypatch: pytest.MonkeyPatch):
    """Point urlopen at whatever the test hands back, and record the request."""
    seen: list[urllib.request.Request] = []

    def install(result):
        def fake_urlopen(request, timeout=None):
            seen.append(request)
            if isinstance(result, Exception):
                raise result
            if isinstance(result, bytes):
                return FakeResponse(result)
            return FakeResponse(json.dumps(result).encode("utf-8"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        return seen

    return install


@pytest.fixture
def credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(clones_badge.TOKEN_ENV, "a-fine-grained-token")
    monkeypatch.setenv(clones_badge.REPO_ENV, REPO)


def read_badge(out_dir: Path) -> dict:
    return json.loads((out_dir / clones_badge.OUT_NAME).read_text(encoding="utf-8"))


def test_the_count_is_summed_from_the_days_and_the_uniques_are_not(answers) -> None:
    """Uniques are a window figure, so summing the rows would overstate them."""
    answers(payload([3, 0, 7, 1], uniques=5))
    assert clones_badge.totals(clones_badge.fetch_clones(REPO, "token")) == (11, 5)


def test_the_request_asks_for_daily_rows_and_carries_the_token(answers) -> None:
    seen = answers(payload([1], uniques=1))
    clones_badge.fetch_clones(REPO, "a-fine-grained-token")
    request = seen[0]
    assert request.full_url == f"https://api.github.com/repos/{REPO}/traffic/clones?per=day"
    assert request.get_header("Authorization") == "Bearer a-fine-grained-token"


def test_an_empty_window_is_zero_rather_than_an_error(answers) -> None:
    answers({"count": 0, "uniques": 0, "clones": []})
    assert clones_badge.totals(clones_badge.fetch_clones(REPO, "token")) == (0, 0)


def test_the_message_reads_as_a_total_and_a_unique_count() -> None:
    document = clones_badge.badge(11, 5)
    assert document == {
        "schemaVersion": 1,
        "label": "clones (14d)",
        "message": "11 · 5 unique",
        "color": "informational",
    }


def test_a_run_writes_the_shields_document_where_it_was_asked_to(
    answers, credentials, tmp_path: Path
) -> None:
    answers(payload([2, 4], uniques=3))
    out_dir = tmp_path / "nested" / "badge"
    assert clones_badge.main(["--out", str(out_dir)]) == 0
    assert read_badge(out_dir) == {
        "schemaVersion": 1,
        "label": "clones (14d)",
        "message": "6 · 3 unique",
        "color": "informational",
    }


def test_a_missing_token_is_one_line_and_exit_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(clones_badge.TOKEN_ENV, raising=False)
    monkeypatch.setenv(clones_badge.REPO_ENV, REPO)
    assert clones_badge.main(["--out", str(tmp_path)]) == 1
    error = capsys.readouterr().err.strip()
    assert error == f"{clones_badge.TOKEN_ENV} is not set, so the clone count cannot be read"
    assert not list(tmp_path.iterdir()), "a failed run should leave no half written badge"


def test_a_missing_repository_name_is_refused_before_any_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(clones_badge.TOKEN_ENV, "a-fine-grained-token")
    monkeypatch.delenv(clones_badge.REPO_ENV, raising=False)
    assert clones_badge.main(["--out", str(tmp_path)]) == 1
    assert clones_badge.REPO_ENV in capsys.readouterr().err


def test_a_refused_request_names_the_permission_the_token_needs(
    answers, credentials, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """403 is what a token without Administration read gets back."""
    answers(urllib.error.HTTPError(clones_badge.traffic_url(REPO), 403, "Forbidden", {}, None))
    assert clones_badge.main(["--out", str(tmp_path)]) == 1
    error = capsys.readouterr().err
    assert "403" in error
    assert "Administration read" in error
    assert not list(tmp_path.iterdir())


def test_an_unreachable_endpoint_fails_rather_than_writing_a_zero(
    answers, credentials, tmp_path: Path
) -> None:
    answers(urllib.error.URLError("name resolution failed"))
    assert clones_badge.main(["--out", str(tmp_path)]) == 1
    assert not list(tmp_path.iterdir())


def test_a_body_that_is_not_json_is_refused(answers) -> None:
    """A proxy error page comes back with a 200 and no JSON in it."""
    answers(b"<html>upstream said no</html>")
    with pytest.raises(clones_badge.BadgeError, match="did not answer with JSON"):
        clones_badge.fetch_clones(REPO, "token")


def test_an_answer_that_is_not_an_object_is_refused(answers) -> None:
    answers([{"count": 1}])
    with pytest.raises(clones_badge.BadgeError):
        clones_badge.fetch_clones(REPO, "token")


def workflow_directives() -> str:
    """The workflow with its comment lines dropped, so prose is not read as YAML."""
    lines = WORKFLOW_PATH.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def test_the_badge_workflow_runs_on_a_push_and_never_on_a_timer() -> None:
    """The owner asked for a push trigger only, so a cron has to fail here."""
    directives = workflow_directives()
    assert "schedule:" not in directives, "the badge workflow must not run on a timer"
    assert "cron" not in directives
    assert "branches: [main]" in directives


def test_the_readme_badge_reads_the_branch_the_workflow_writes() -> None:
    """One URL, two places: the branch name and the file name have to match."""
    readme = README_PATH.read_text(encoding="utf-8")
    raw_url = (
        "https://raw.githubusercontent.com/oficiallyAkshay/boomerang/badges/"
        f"{clones_badge.OUT_NAME}"
    )
    assert f"https://img.shields.io/endpoint?url={raw_url}" in readme
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "HEAD:badges" in workflow
    assert clones_badge.OUT_NAME in workflow
