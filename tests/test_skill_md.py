"""SKILL.md is the thing a host loads, so it is checked like code.

The frontmatter is read with a regex, not with a YAML dependency: every value
is plain text on one line, inline `metadata` map included, and a parser would
be the only runtime dependency the tests add. Everything else here
guards the promises the body makes. A script named in a command the model is
told to run has to exist, every other repo path it prints in backticks has to
exist too, the two policy files both have to be pointed at, and the one
standing question has to still be in there word for word.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = REPO_ROOT / "SKILL.md"
MAX_DESCRIPTION = 200

FRONT_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n", re.S)
FIELD_RE = re.compile(r"^([A-Za-z_][\w-]*):[ \t]*(.*?)[ \t]*$", re.M)

SCRIPT_RE = re.compile(r"scripts/([A-Za-z0-9_]+)\.py")
STANDING_QUESTION = "Anything you paid for outside this inbox"

FENCE_RE = re.compile(r"```.*?```", re.S)
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
# A path has no spaces, no angle-bracket placeholder, and does not start with a
# dash, so command flags and `<rid>.html` shapes fall out here.
PATH_SHAPE_RE = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_.-]+)*/?$")

# Paths SKILL.md names on purpose that are not in the repo. `policy.local.md`
# is the user's own layer: gitignored, written on their machine, absent here.
NOT_IN_REPO = {"policy.local.md"}


def repo_paths_in_backticks(body: str) -> list[str]:
    """Relative paths the prose prints in inline code, fenced blocks aside.

    A span may be a whole command, so only its first word is considered. A word
    counts as a path when it has a directory part or a markdown extension,
    which keeps bare values like `amt` and `expense_data.json` out of it.
    """
    found: set[str] = set()
    for span in INLINE_CODE_RE.findall(FENCE_RE.sub("", body)):
        word = span.split()[0]
        if not PATH_SHAPE_RE.fullmatch(word):
            continue
        if "/" not in word.rstrip("/") and not word.endswith(".md"):
            continue
        found.add(word)
    return sorted(found)


def unquote(value: str) -> str:
    """One level of matching quotes off a frontmatter value."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def frontmatter(text: str) -> tuple[dict[str, str], str]:
    """The front block's fields, and the body that follows it.

    A key starts a line, so the indented continuation of a folded value is
    never mistaken for one, and the inline `metadata` map comes back as the
    text between its braces for the icon test to look inside.
    """
    match = FRONT_RE.match(text)
    assert match, "SKILL.md must open with a frontmatter fence and close it"
    fields = {key: unquote(value) for key, value in FIELD_RE.findall(match.group(1))}
    return fields, text[match.end() :]


@pytest.fixture(scope="module")
def skill() -> dict:
    text = SKILL_PATH.read_text(encoding="utf-8")
    fields, body = frontmatter(text)
    return {"text": text, "fields": fields, "body": body}


def test_the_name_is_the_skill_name(skill: dict):
    assert skill["fields"]["name"] == "boomerang"


def test_the_description_says_what_and_when(skill: dict):
    """Short enough for every host, and carrying the two words a user's own
    request is most likely to contain, so the skill triggers on it.
    """
    description = skill["fields"].get("description", "")
    assert description.strip()
    assert len(description) < MAX_DESCRIPTION, f"description is {len(description)} characters"
    lowered = description.lower()
    for word in ("expense", "reimburse"):
        assert word in lowered, f"description never says {word!r}"


def test_a_license_is_declared(skill: dict):
    assert skill["fields"].get("license", "").strip()


def test_the_icon_travels_under_metadata(skill: dict):
    """Anthropic's validator rejects unknown top-level keys, and `metadata` is
    the string map the specification allows, so the icon lives in there.
    """
    assert "icon" not in skill["fields"], "the icon must not be a top-level key"
    metadata = skill["fields"].get("metadata", "")
    assert "icon" in metadata, f"metadata does not carry an icon: {metadata!r}"
    assert "\U0001fa83" in metadata


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


def test_every_repo_path_the_body_names_exists(skill: dict):
    """A move that leaves SKILL.md pointing at the old place fails here."""
    named = repo_paths_in_backticks(skill["body"])
    assert named, "the body should name at least one repo path"
    missing = [
        path
        for path in named
        if path not in NOT_IN_REPO and not (REPO_ROOT / path.rstrip("/")).exists()
    ]
    assert missing == [], f"SKILL.md names paths that do not exist: {missing}"
