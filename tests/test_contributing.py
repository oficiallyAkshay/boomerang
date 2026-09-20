"""The contributing guide describes CI, so it is checked against CI.

A table is prose until something proves it, and the row a reader trusts most
is the one nobody updated. These tests pin the two places the guide restates
what lives elsewhere: the job names in ``.github/workflows/ci.yml``, and the
four local gate commands the pull request template asks for as a checklist.

The workflow is read with PyYAML when the environment has it, and with a small
regex over the two-space keys under ``jobs:`` when it does not, so the tests
add no dependency of their own.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
CONTRIBUTING_PATH = REPO_ROOT / ".github" / "CONTRIBUTING.md"
TEMPLATE_PATH = REPO_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"

CI_TABLE_LEAD = "**What CI runs.**"
PLAN_TABLE_LEAD = "**Test plan a change must satisfy.**"

JOB_RE = re.compile(r"^  ([A-Za-z0-9_-]+):", re.MULTILINE)
COMMAND_RE = re.compile(r"^(uv run .+?)(?:\s{2,}#.*)?$", re.MULTILINE)


def job_names(text: str) -> list[str]:
    """The job names the workflow declares, parser or no parser."""
    try:
        import yaml
    except ImportError:
        jobs_block = text.split("\njobs:\n", 1)[1]
        return sorted(set(JOB_RE.findall(jobs_block)))
    return sorted(yaml.safe_load(text)["jobs"])


def table_after(text: str, lead: str) -> list[str]:
    """The rows of the first markdown table following a bold lead-in."""
    assert lead in text, f"the guide no longer carries {lead!r}"
    rows: list[str] = []
    for line in text.split(lead, 1)[1].splitlines():
        if line.startswith("|"):
            rows.append(line)
        elif rows:
            break
    assert rows, f"no table follows {lead!r}"
    return rows


def local_gate_commands(text: str) -> list[str]:
    """The commands the guide tells a contributor to run before pushing."""
    block = text.split("**The gates, run locally.**", 1)[1].split("```", 2)[1]
    return [command.strip() for command in COMMAND_RE.findall(block)]


@pytest.fixture(scope="module")
def guide() -> str:
    return CONTRIBUTING_PATH.read_text(encoding="utf-8")


def test_every_ci_job_is_named_in_the_table(guide: str) -> None:
    """A new job, or a renamed one, has to reach the guide to pass here."""
    names = job_names(CI_PATH.read_text(encoding="utf-8"))
    assert names, "the workflow should declare at least one job"
    table = "\n".join(table_after(guide, CI_TABLE_LEAD))
    missing = [name for name in names if f"`{name}`" not in table]
    assert missing == [], f"the What CI runs table never names these jobs: {missing}"


def test_the_ci_table_says_what_blocks_a_merge(guide: str) -> None:
    """Three columns, and every body row answers the third one."""
    header, _, *body = table_after(guide, CI_TABLE_LEAD)
    assert [cell.strip() for cell in header.strip("|").split("|")] == [
        "Check",
        "Runs on",
        "Blocks merge",
    ]
    for row in body:
        assert row.strip("|").split("|")[-1].strip() in {"yes", "no"}, row


def test_the_test_plan_covers_every_area(guide: str) -> None:
    table = "\n".join(table_after(guide, PLAN_TABLE_LEAD)).lower()
    for area in ("vendor rules", "cleaner", "packet builder", "renderer", "fetch", "docs"):
        assert f"| {area} |" in table, f"the test plan says nothing about {area}"


def test_the_template_checklist_is_the_local_gates(guide: str) -> None:
    """One list of commands, written down twice, kept identical by this test."""
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    commands = local_gate_commands(guide)
    assert len(commands) == 4, commands
    missing = [command for command in commands if f"- [ ] `{command}`" not in template]
    assert missing == [], f"the pull request template is missing: {missing}"
