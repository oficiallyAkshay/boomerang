"""Tests for the install check.

Nothing here touches the network or launches a browser. The executables are
tmp files the test creates, the PATH lookup is monkeypatched, and the two
install commands are captured rather than run, so the suite proves what
``doctor.py`` would do without doing any of it.

The first test is the one that stops a slow bug: `requirements.txt` and the
`dependencies` list in `pyproject.toml` are the same two pins written twice,
and a pip install that quietly lags a uv sync is worse than either.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import doctor
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def requirement_lines() -> list[str]:
    """The pins in requirements.txt, comments and blanks dropped."""
    text = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]


def pyproject_dependencies() -> list[str]:
    """The runtime pins declared in pyproject.toml."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return [str(item).strip() for item in data["project"]["dependencies"]]


def test_the_two_dependency_files_say_the_same_thing():
    assert requirement_lines() == pyproject_dependencies()


def test_requirements_pins_both_runtime_packages():
    names = [line.split("==")[0] for line in requirement_lines()]
    assert names == ["playwright", "pypdf"]
    assert all("==" in line for line in requirement_lines())


def test_module_present_answers_for_both_cases():
    assert doctor.module_present("json")
    assert not doctor.module_present("a_module_nobody_shipped")


def test_module_present_survives_a_broken_name():
    """A dotted name whose parent is not a package raises rather than returning None."""
    assert not doctor.module_present("json.not_a_submodule.deeper")


@pytest.fixture
def barren(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A machine with no browser anywhere: no app, no executable, no PATH hit.

    Every path points inside tmp_path at a name that was never created, so a
    test can make one of them real and watch that one be found.
    """
    for table in ("APPS", "EXES"):
        places = {channel: str(tmp_path / f"{table}-{channel}") for channel in ("chrome", "msedge")}
        monkeypatch.setattr(doctor, table, places)
    monkeypatch.setattr(doctor, "NAMES", {"chrome": (), "msedge": ()})
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    empty = tmp_path / "ms-playwright"
    empty.mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(empty))
    monkeypatch.delenv("BOOMERANG_BROWSER", raising=False)
    return tmp_path


def test_no_browser_anywhere_is_reported_as_none(barren: Path):
    assert doctor.find_browser() is None


def test_a_mac_app_is_found(barren: Path, monkeypatch: pytest.MonkeyPatch):
    app = barren / "Google Chrome"
    app.write_text("", encoding="utf-8")
    monkeypatch.setitem(doctor.APPS, "chrome", str(app))
    assert doctor.find_browser() == "chrome"


def test_a_windows_executable_is_found(barren: Path, monkeypatch: pytest.MonkeyPatch):
    exe = barren / "msedge.exe"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setitem(doctor.EXES, "msedge", str(exe))
    assert doctor.find_browser() == "msedge"


def test_a_browser_on_the_path_is_found(barren: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(doctor.NAMES, "msedge", ("microsoft-edge",))
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/usr/bin/microsoft-edge")
    assert doctor.find_browser() == "msedge"


def test_a_downloaded_chromium_is_found(barren: Path, monkeypatch: pytest.MonkeyPatch):
    (barren / "ms-playwright" / "chromium-1148").mkdir()
    assert doctor.find_browser() == "chromium"


def test_the_pinned_browser_is_the_only_one_checked(barren: Path, monkeypatch: pytest.MonkeyPatch):
    """BOOMERANG_BROWSER reaches the check through render_pdf's own order."""
    app = barren / "Google Chrome"
    app.write_text("", encoding="utf-8")
    monkeypatch.setitem(doctor.APPS, "chrome", str(app))
    monkeypatch.setenv("BOOMERANG_BROWSER", "msedge")
    assert doctor.find_browser() is None


def test_an_unusable_pin_falls_back_to_the_shipped_order(barren: Path, monkeypatch):
    """render_pdf refuses an unknown BOOMERANG_BROWSER, so the check uses its own order."""
    app = barren / "APPS-chrome"
    app.write_text("", encoding="utf-8")
    monkeypatch.setenv("BOOMERANG_BROWSER", "safari")
    assert doctor.find_browser() == "chrome"


@pytest.fixture
def no_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    """A machine where ``from render_pdf import ...`` cannot work.

    ``None`` in ``sys.modules`` is what the import machinery treats as an
    import that has been halted, so the import raises rather than finding the
    renderer sitting beside doctor on the path. It stands for the machine this
    check exists for: playwright is not installed, so render_pdf cannot import
    playwright either.
    """
    monkeypatch.setitem(sys.modules, "render_pdf", None)
    with pytest.raises(ImportError):
        from render_pdf import wanted_channels  # noqa: F401


def test_the_pin_is_honoured_with_no_renderer_to_ask(
    barren: Path, no_renderer: None, monkeypatch: pytest.MonkeyPatch
):
    """The machine most likely to run doctor is the one with no renderer on it.

    A run pinned to msedge is checked for msedge and nothing else, even though
    render_pdf is not there to be asked for its order, because naming the
    chrome beside it would answer a question nobody asked.
    """
    app = barren / "Google Chrome"
    app.write_text("", encoding="utf-8")
    monkeypatch.setitem(doctor.APPS, "chrome", str(app))
    monkeypatch.setenv("BOOMERANG_BROWSER", "msedge")
    assert doctor.channels_to_check() == ("msedge",)
    assert doctor.find_browser() is None


def test_the_pinned_browser_is_found_with_no_renderer_to_ask(
    barren: Path, no_renderer: None, monkeypatch: pytest.MonkeyPatch
):
    edge = barren / "Microsoft Edge"
    edge.write_text("", encoding="utf-8")
    monkeypatch.setitem(doctor.APPS, "msedge", str(edge))
    monkeypatch.setenv("BOOMERANG_BROWSER", "msedge")
    assert doctor.find_browser() == "msedge"


@pytest.mark.parametrize("pinned", ["MSEdge", "  msedge  "])
def test_the_pin_is_read_the_way_the_renderer_reads_it(
    pinned: str, barren: Path, no_renderer: None, monkeypatch: pytest.MonkeyPatch
):
    """Case and surrounding space are stripped here exactly as render_pdf strips them."""
    monkeypatch.setenv("BOOMERANG_BROWSER", pinned)
    assert doctor.channels_to_check() == ("msedge",)


def test_no_pin_and_no_renderer_is_the_shipped_order(barren: Path, no_renderer: None):
    assert doctor.channels_to_check() == doctor.CHANNELS


@pytest.mark.parametrize("pinned", ["safari", ""])
def test_a_pin_that_names_no_channel_is_the_shipped_order(
    pinned: str, barren: Path, no_renderer: None, monkeypatch: pytest.MonkeyPatch
):
    """An unknown value is not a pin, which is where render_pdf refusing it lands too."""
    monkeypatch.setenv("BOOMERANG_BROWSER", pinned)
    assert doctor.channels_to_check() == doctor.CHANNELS


def test_the_renderer_order_wins_when_the_renderer_imports(
    barren: Path, monkeypatch: pytest.MonkeyPatch
):
    """With render_pdf present it is the one asked, and doctor does not second guess it."""
    monkeypatch.setenv("BOOMERANG_BROWSER", "chromium")
    assert doctor.channels_to_check() == ("chromium",)


def test_the_cache_path_follows_the_platform(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.setattr(doctor.platform, "system", lambda: "Darwin")
    assert doctor.playwright_cache().parts[-3:] == ("Library", "Caches", "ms-playwright")
    monkeypatch.setattr(doctor.platform, "system", lambda: "Linux")
    assert doctor.playwright_cache().parts[-2:] == (".cache", "ms-playwright")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    assert doctor.playwright_cache() == tmp_path


def test_every_check_carries_a_fix(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(doctor, "find_browser", lambda: "chrome")
    labels = [row[0] for row in doctor.checks()]
    assert labels == ["python", "playwright", "pypdf", "browser"]
    assert all(row[3].strip() for row in doctor.checks())


def test_a_complete_machine_passes(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    monkeypatch.setattr(doctor, "find_browser", lambda: "chrome")
    monkeypatch.setattr(doctor, "module_present", lambda name: True)
    assert doctor.main([]) == 0
    out = capsys.readouterr().out
    assert "missing" not in out
    assert "host's job" in out


def test_a_missing_browser_still_exits_zero(monkeypatch, capsys: pytest.CaptureFixture):
    """The HTML packet is the deliverable without a browser, so this is not a failure."""
    monkeypatch.setattr(doctor, "find_browser", lambda: None)
    monkeypatch.setattr(doctor, "module_present", lambda name: True)
    assert doctor.main([]) == 0
    out = capsys.readouterr().out
    assert "browser     missing" in out
    assert "python -m playwright install chromium" in out
    assert "Google Chrome or Microsoft Edge" in out


def test_a_missing_package_fails_and_names_the_command(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "find_browser", lambda: "chrome")
    monkeypatch.setattr(doctor, "module_present", lambda name: name != "pypdf")
    assert doctor.main([]) == 1
    out = capsys.readouterr().out
    assert "pypdf       missing" in out
    assert "pip install -r requirements.txt (or: uv sync)" in out


@pytest.fixture
def captured_runs(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Every command the install path would run, captured instead of run."""
    runs: list[list[str]] = []

    def fake_call(command, *args, **kwargs):
        runs.append(list(command))
        return 0

    monkeypatch.setattr(subprocess, "call", fake_call)
    monkeypatch.setattr(doctor, "find_browser", lambda: "chrome")
    monkeypatch.setattr(doctor, "module_present", lambda name: True)
    return runs


def test_install_uses_this_interpreter_and_no_browser_download(captured_runs, capsys):
    assert doctor.main(["--install"]) == 0
    assert captured_runs == [
        [sys.executable, "-m", "pip", "install", "-r", str(doctor.REQUIREMENTS)]
    ]
    assert "running: " in capsys.readouterr().out


def test_the_browser_download_is_opt_in(captured_runs, capsys):
    assert doctor.main(["--install", "--install-browser"]) == 0
    assert captured_runs[-1] == [sys.executable, "-m", "playwright", "install", "chromium"]


def test_a_failed_install_stops_there(monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.setattr(subprocess, "call", lambda command, *a, **k: 1)
    assert doctor.main(["--install"]) == 1
    assert "ok" not in capsys.readouterr().out
