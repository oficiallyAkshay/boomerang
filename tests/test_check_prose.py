"""Tests for the prose and privacy gate.

The two modes are tested apart, because they read different things. The tree
scan resolves the repo root and the denylist from the script's own path, so
the probes at the end run it from two other directories and expect the same
answer; packet mode reads one file and is the mode a host installation runs.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import check_prose
import pytest
from check_prose import EM_DASH

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE = REPO_ROOT / "scripts" / "check_prose.py"


def sha(phrase: str) -> str:
    return hashlib.sha256(phrase.encode("utf-8")).hexdigest()


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_clean_file_has_no_errors(tmp_path: Path) -> None:
    path = write(tmp_path, "clean.md", "A short line.\nAnother one.\n")
    assert check_prose.scan([path], set()) == []


def test_em_dash_is_reported_with_line_number(tmp_path: Path) -> None:
    path = write(tmp_path, "prose.md", f"fine\nnot {EM_DASH} fine\n")
    errors = check_prose.scan([path], set())
    assert errors == [f"{path.as_posix()}:2: em dash"]


def test_denylist_hit_never_prints_the_phrase(tmp_path: Path) -> None:
    denylist = {sha("acme widget corp")}
    path = write(tmp_path, "leak.md", "the Acme Widget Corp invoice\n")
    errors = check_prose.scan([path], denylist)
    assert errors == [f"{path.as_posix()}:1: denylist hit"]
    assert "acme" not in errors[0].lower()


def test_single_token_and_bigram_hits(tmp_path: Path) -> None:
    path = write(tmp_path, "tokens.md", "one Zephyrine two\nthree four five\n")
    assert check_prose.scan([path], {sha("zephyrine")})
    assert check_prose.scan([path], {sha("three four")})
    assert check_prose.scan([path], {sha("three four five")})
    assert check_prose.scan([path], {sha("three four five six")}) == []


def test_a_four_word_phrase_is_caught(tmp_path: Path) -> None:
    """Three words was short of the long names a denylist actually carries."""
    path = write(tmp_path, "long.md", "the Zephyrine Holdings Group Limited invoice\n")
    assert check_prose.MAX_NGRAM == 4
    assert check_prose.scan([path], {sha("zephyrine holdings group limited")})
    assert check_prose.scan([path], {sha("holdings group limited invoice")})
    assert check_prose.scan([path], {sha("the zephyrine holdings group limited")}) == []


def test_a_utf16_file_is_decoded_and_scanned(tmp_path: Path) -> None:
    """It is mostly NUL bytes, so the binary test used to wave it straight past."""
    for name, encoding in (("le.md", "utf-16-le"), ("be.md", "utf-16-be")):
        path = tmp_path / name
        bom = "\ufeff"
        path.write_bytes((bom + f"a line {EM_DASH} here\nand Zephyrine\n").encode(encoding))
        assert check_prose.is_scannable(path) is True
        errors = check_prose.scan([path], {sha("zephyrine")})
        assert errors == [f"{path.as_posix()}:1: em dash", f"{path.as_posix()}:2: denylist hit"]


def test_a_real_binary_file_is_still_skipped(tmp_path: Path) -> None:
    path = tmp_path / "blob.dat"
    path.write_bytes(b"\x01\x00\x02" + EM_DASH.encode())
    assert check_prose.is_scannable(path) is False


def test_empty_denylist_short_circuits() -> None:
    assert check_prose.line_hits_denylist("anything at all", set()) is False


def test_binary_and_skipped_files_are_ignored(tmp_path: Path) -> None:
    binary = tmp_path / "logo.bin"
    binary.write_bytes(b"pre\x00post " + EM_DASH.encode())
    lock = write(tmp_path, "uv.lock", f"pinned {EM_DASH}\n")
    hashes = write(tmp_path, "pii_denylist.sha256", f"{sha('zephyrine')}\n")
    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG")
    missing = tmp_path / "gone.md"
    files = [binary, lock, hashes, image, missing]
    assert check_prose.scan(files, {sha("zephyrine")}) == []


def test_unreadable_path_is_not_scannable(tmp_path: Path) -> None:
    assert check_prose.is_scannable(tmp_path) is False


def test_load_denylist_skips_comments_and_blanks(tmp_path: Path) -> None:
    path = write(tmp_path, "list.sha256", f"# note\n\n{sha('zephyrine')}\n")
    assert check_prose.load_denylist(path) == {sha("zephyrine")}
    assert check_prose.load_denylist(tmp_path / "absent.sha256") == set()


def test_packet_mode_flags_the_banned_word(tmp_path: Path, capsys) -> None:
    packet = write(tmp_path, "packet.html", "<p>Balance Due on arrival</p>\n")
    code = check_prose.main(["--packet", str(packet)])
    out = capsys.readouterr().out
    assert code == 1
    assert "banned word" in out
    assert "check_prose: 1 errors" in out


def test_packet_mode_flags_a_data_rid_attribute(tmp_path: Path, capsys) -> None:
    """A packet is printed and mailed, so a message id has no business in it."""
    packet = write(tmp_path, "packet.html", '<section data-rid="abc123">A receipt</section>\n')
    code = check_prose.main(["--packet", str(packet)])
    out = capsys.readouterr().out
    assert code == 1
    assert f"{packet.as_posix()}:1: data-rid attribute" in out


def test_packet_mode_flags_a_fragment_link(tmp_path: Path, capsys) -> None:
    packet = write(tmp_path, "packet.html", '<p>See <a href="#r3">the receipt</a>.</p>\n')
    code = check_prose.main(["--packet", str(packet)])
    out = capsys.readouterr().out
    assert code == 1
    assert f"{packet.as_posix()}:1: fragment link" in out


def test_packet_mode_reads_the_packet_and_nothing_else(tmp_path: Path, monkeypatch, capsys):
    """No git ls-files, so the mode a host installation runs needs no repo."""

    def explode(*args: object, **kwargs: object) -> list[Path]:
        raise AssertionError("packet mode must not list the tree")

    monkeypatch.setattr(check_prose, "git_files", explode)
    packet = write(tmp_path, "packet.html", "<p>A clean packet</p>\n")
    assert check_prose.main(["--packet", str(packet)]) == 0
    assert "check_prose: 0 errors" in capsys.readouterr().out


def test_the_packet_markup_rules_do_not_apply_to_the_tree(tmp_path: Path) -> None:
    """The repo is full of source that mentions both, and none of it is a packet."""
    path = write(tmp_path, "src.py", "DATA_RID = 'data-rid=\"'\nLINK = 'href=\"#top'\n")
    assert check_prose.scan([path], set()) == []


def test_the_packet_rules_come_on_together(tmp_path: Path) -> None:
    """One flag, because no caller ever wanted one of the three without the rest."""
    line = '<section data-rid="abc">Balance Due, see <a href="#r1">below</a></section>'
    assert check_prose.scan_text("p.html", line, set()) == []
    assert check_prose.scan_text("p.html", line, set(), packet=True) == [
        "p.html:1: banned word",
        "p.html:1: data-rid attribute",
        "p.html:1: fragment link",
    ]


def test_main_is_quiet_on_a_clean_tree(tmp_path: Path, monkeypatch, capsys) -> None:
    clean = write(tmp_path, "ok.md", "Short and plain.\n")
    monkeypatch.setattr(check_prose, "git_files", lambda root=None: [clean])
    code = check_prose.main([])
    assert code == 0
    assert "check_prose: 0 errors" in capsys.readouterr().out


def test_a_tree_scan_with_no_denylist_fails_loudly(monkeypatch, capsys) -> None:
    """Zero errors from a gate that was checking nothing is the worst answer."""
    monkeypatch.setattr(check_prose, "load_denylist", lambda *args: set())
    monkeypatch.setattr(check_prose, "git_files", lambda root=None: [])
    assert check_prose.main([]) == 1
    out = capsys.readouterr().out
    assert "no denylist at" in out
    assert "0 errors" not in out


def test_git_files_returns_tracked_paths() -> None:
    files = check_prose.git_files(REPO_ROOT)
    assert all(isinstance(path, Path) for path in files)
    assert Path("SKILL.md") in files


def run_gate(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """The gate as a host would run it, from some other directory."""
    return subprocess.run(
        [sys.executable, str(GATE), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("where", ["tmp", "scripts"])
def test_both_modes_run_from_any_directory(where: str, tmp_path: Path) -> None:
    """Nothing is resolved against the working directory, so nothing depends on it.

    The tree scan finds the repo and the denylist from the script's own path,
    and packet mode reads the file it was handed. A skill installed under a
    host's own folder runs from wherever the host happens to be.
    """
    cwd = tmp_path if where == "tmp" else REPO_ROOT / "scripts"
    packet = tmp_path / "packet.html"
    packet.write_text("<p>A clean packet</p>\n", encoding="utf-8")

    on_packet = run_gate(cwd, "--packet", str(packet))
    assert on_packet.returncode == 0, on_packet.stdout + on_packet.stderr
    assert "check_prose: 0 errors" in on_packet.stdout

    on_tree = run_gate(cwd)
    assert on_tree.returncode == 0, on_tree.stdout + on_tree.stderr
    assert "check_prose: 0 errors" in on_tree.stdout
