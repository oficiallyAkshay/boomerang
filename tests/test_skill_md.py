"""SKILL.md is the thing a host loads, so it is checked like code.

The frontmatter is parsed by reading lines, not with a YAML dependency: the
block is three keys of plain text and a parser would be the only runtime
dependency the tests add. Everything else here guards the promises the body
makes. A script named in a command the model is told to run has to exist, the
two policy files both have to be pointed at, and the one standing question has
to still be in there word for word.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = REPO_ROOT / "SKILL.md"
MAX_DESCRIPTION = 200

SCRIPT_RE = re.compile(r"scripts/([A-Za-z0-9_]+)\.py")
STANDING_QUESTION = "Anything you paid for outside this inbox"


def split_frontmatter(text: str) -> tuple[list[str], str]:
    """The lines between the opening and closing fences, and the body after."""
    lines = text.splitlines()
    assert lines and lines[0].strip() == "---", "SKILL.md must open with a frontmatter fence"
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return lines[1:index], "\n".join(lines[index + 1 :])
    raise AssertionError("SKILL.md frontmatter is never closed")


def parse_frontmatter(lines: list[str]) -> dict[str, str]:
    """key: value per line, with one level of quotes taken off the value."""
    fields: dict[str, str] = {}
    for line in lines:
        if not line.strip() or line.startswith((" ", "\t", "#")):
            continue
        key, separator, value = line.partition(":")
        if not separator:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fields[key.strip()] = value
    return fields


@pytest.fixture(scope="module")
def skill() -> dict:
    text = SKILL_PATH.read_text(encoding="utf-8")
    front, body = split_frontmatter(text)
    return {"text": text, "fields": parse_frontmatter(front), "body": body}


def test_the_name_is_the_skill_name(skill: dict):
    assert skill["fields"]["name"] == "boomerang"


def test_the_description_is_present_and_short_enough(skill: dict):
    description = skill["fields"].get("description", "")
    assert description.strip()
    assert len(description) < MAX_DESCRIPTION, f"description is {len(description)} characters"


def test_a_license_is_declared(skill: dict):
    assert skill["fields"].get("license", "").strip()


def test_every_script_the_body_names_exists(skill: dict):
    named = sorted(set(SCRIPT_RE.findall(skill["body"])))
    assert named, "the body should name at least one script"
    missing = [name for name in named if not (REPO_ROOT / "scripts" / f"{name}.py").is_file()]
    assert missing == [], f"SKILL.md names scripts that do not exist: {missing}"


def test_both_policy_files_are_pointed_at(skill: dict):
    body = skill["body"]
    assert "policy.local.md" in body
    assert "policy.md" in body


def test_the_one_standing_question_is_still_there(skill: dict):
    assert STANDING_QUESTION in skill["body"]
