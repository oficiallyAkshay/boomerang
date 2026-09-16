#!/usr/bin/env python3
"""Write the shields endpoint JSON for the last 14 days of clones.

GitHub keeps a rolling 14 day traffic window per repository and serves it at
``GET /repos/{owner}/{repo}/traffic/clones``. That endpoint sits under the
"Administration" permission at read access, which a workflow ``GITHUB_TOKEN``
cannot be granted, so this script reads a repository secret named
``TRAFFIC_TOKEN`` instead: a fine-grained token the owner creates with
Administration read on this repository.

The window is the endpoint's own. Nothing here counts anything on anyone's
machine, and no identity reaches the file: the output is two integers, the
total and the uniques, for the repository as a whole.

Standard library only, so it runs on a bare runner with no project sync. The
output is the shields endpoint shape, ``{"schemaVersion": 1, "label", "message",
"color"}``, written as ``clones-14d.json`` into ``--out``. A missing token or an
endpoint that refuses the request is one plain line on stderr and exit 1, never
a half written badge.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API_ROOT = "https://api.github.com"
TOKEN_ENV = "TRAFFIC_TOKEN"
REPO_ENV = "GITHUB_REPOSITORY"

OUT_NAME = "clones-14d.json"
LABEL = "clones (14d)"
COLOR = "informational"

# Long enough for a slow API, short enough that a hung socket does not hold a
# runner for the job's whole timeout.
TIMEOUT_SECONDS = 30


class BadgeError(Exception):
    """A failure the caller prints in one line and exits on."""


def traffic_url(repo: str) -> str:
    """The clones endpoint for one repository, one row per day."""
    return f"{API_ROOT}/repos/{repo}/traffic/clones?per=day"


def fetch_clones(repo: str, token: str) -> dict:
    """The decoded clones payload, or a BadgeError saying what went wrong."""
    request = urllib.request.Request(
        traffic_url(repo),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "boomerang-clones-badge",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        raise BadgeError(
            f"the clones endpoint answered HTTP {error.code} for {repo}. "
            f"{TOKEN_ENV} needs Administration read on this repository."
        ) from error
    except urllib.error.URLError as error:
        raise BadgeError(f"the clones endpoint could not be reached: {error.reason}") from error
    except ValueError as error:
        raise BadgeError(f"the clones endpoint did not answer with JSON: {error}") from error
    if not isinstance(payload, dict):
        raise BadgeError("the clones endpoint answered with something other than an object")
    return payload


def totals(payload: dict) -> tuple[int, int]:
    """The clone total summed from the daily rows, and the uniques as reported.

    The uniques are not a sum. A person who clones on Monday and again on
    Friday is one unique over the window and two across the rows, so the
    window's own figure is the only honest one.
    """
    days = payload.get("clones") or []
    count = sum(int(day.get("count", 0)) for day in days)
    return count, int(payload.get("uniques", 0))


def badge(count: int, uniques: int) -> dict:
    """The shields endpoint document for one pair of figures."""
    return {
        "schemaVersion": 1,
        "label": LABEL,
        "message": f"{count} · {uniques} unique",
        "color": COLOR,
    }


def write_badge(document: dict, out_dir: Path) -> Path:
    """Write the badge JSON into out_dir, creating it if it is not there."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / OUT_NAME
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the clones badge JSON.")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("."),
        help=f"directory to write {OUT_NAME} into (default: the working directory)",
    )
    args = parser.parse_args(argv)

    token = os.environ.get(TOKEN_ENV, "").strip()
    repo = os.environ.get(REPO_ENV, "").strip()
    try:
        if not token:
            raise BadgeError(f"{TOKEN_ENV} is not set, so the clone count cannot be read")
        if not repo:
            raise BadgeError(f"{REPO_ENV} is not set, so there is no repository to ask about")
        count, uniques = totals(fetch_clones(repo, token))
    except BadgeError as error:
        print(error, file=sys.stderr)
        return 1

    path = write_badge(badge(count, uniques), args.out)
    print(f"wrote {path}: {count} clones, {uniques} unique, last 14 days")
    return 0


if __name__ == "__main__":
    sys.exit(main())
