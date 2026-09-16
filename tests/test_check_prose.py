"""Tests for the prose and privacy gate."""

from __future__ import annotations

import hashlib
from pathlib import Path

import check_prose

EM_DASH = "\u2014"  # written as an escape so this file stays clean


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


def test_packet_mode_flags_the_banned_word(tmp_path: Path, monkeypatch, capsys) -> None:
    packet = write(tmp_path, "packet.html", "<p>Balance Due on arrival</p>\n")
    empty = write(tmp_path, "list.sha256", "")
    monkeypatch.setattr(check_prose, "git_files", lambda: [])
    code = check_prose.main(["--packet", str(packet), "--denylist", str(empty)])
    out = capsys.readouterr().out
    assert code == 1
    assert "banned word" in out
    assert "check_prose: 1 errors" in out


def test_main_is_quiet_on_a_clean_tree(tmp_path: Path, monkeypatch, capsys) -> None:
    clean = write(tmp_path, "ok.md", "Short and plain.\n")
    empty = write(tmp_path, "list.sha256", "")
    monkeypatch.setattr(check_prose, "git_files", lambda: [clean])
    code = check_prose.main(["--denylist", str(empty)])
    assert code == 0
    assert "check_prose: 0 errors" in capsys.readouterr().out


def test_git_files_returns_tracked_paths() -> None:
    root = Path(__file__).resolve().parents[1]
    files = check_prose.git_files(root)
    assert isinstance(files, list)
    assert all(isinstance(path, Path) for path in files)
