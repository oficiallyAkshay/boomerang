"""Tests for the prose and privacy gate."""

from __future__ import annotations

import hashlib
from pathlib import Path

import check_prose
from check_prose import EM_DASH


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


def test_packet_mode_flags_the_banned_word(tmp_path: Path, monkeypatch, capsys) -> None:
    packet = write(tmp_path, "packet.html", "<p>Balance Due on arrival</p>\n")
    empty = write(tmp_path, "list.sha256", "")
    monkeypatch.setattr(check_prose, "git_files", lambda: [])
    code = check_prose.main(["--packet", str(packet), "--denylist", str(empty)])
    out = capsys.readouterr().out
    assert code == 1
    assert "banned word" in out
    assert "check_prose: 1 errors" in out


def test_packet_mode_flags_a_data_rid_attribute(tmp_path: Path, monkeypatch, capsys) -> None:
    """A packet is printed and mailed, so a message id has no business in it."""
    packet = write(tmp_path, "packet.html", '<section data-rid="abc123">A receipt</section>\n')
    empty = write(tmp_path, "list.sha256", "")
    monkeypatch.setattr(check_prose, "git_files", lambda: [])
    code = check_prose.main(["--packet", str(packet), "--denylist", str(empty)])
    out = capsys.readouterr().out
    assert code == 1
    assert f"{packet.as_posix()}:1: data-rid attribute" in out


def test_packet_mode_flags_a_fragment_link(tmp_path: Path, monkeypatch, capsys) -> None:
    packet = write(tmp_path, "packet.html", '<p>See <a href="#r3">the receipt</a>.</p>\n')
    empty = write(tmp_path, "list.sha256", "")
    monkeypatch.setattr(check_prose, "git_files", lambda: [])
    code = check_prose.main(["--packet", str(packet), "--denylist", str(empty)])
    out = capsys.readouterr().out
    assert code == 1
    assert f"{packet.as_posix()}:1: fragment link" in out


def test_the_packet_markup_rules_do_not_apply_to_the_tree(tmp_path: Path) -> None:
    """The repo is full of source that mentions both, and none of it is a packet."""
    path = write(tmp_path, "src.py", "DATA_RID = 'data-rid=\"'\nLINK = 'href=\"#top'\n")
    assert check_prose.scan([path], set()) == []


def test_scan_text_takes_the_two_packet_flags_apart(tmp_path: Path) -> None:
    line = '<section data-rid="abc">Balance Due</section>'
    assert check_prose.scan_text("p.html", line, set()) == []
    assert check_prose.scan_text("p.html", line, set(), flag_banned_word=True) == [
        "p.html:1: banned word"
    ]
    assert check_prose.scan_text("p.html", line, set(), flag_packet_markup=True) == [
        "p.html:1: data-rid attribute"
    ]


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
