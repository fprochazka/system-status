"""Tests for the application launcher and its icon, written into a temporary ``XDG_DATA_HOME``."""

import configparser
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest
from typer.testing import CliRunner

from system_status import cli
from system_status.gui import launcher
from system_status.gui.theme import SERIES_COLORS

SVG = "{http://www.w3.org/2000/svg}"
runner = CliRunner()


def read_entry(path: Path) -> dict[str, str]:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # Desktop Entry keys are case-sensitive.
    parser.read_string(path.read_text())
    assert parser.sections() == ["Desktop Entry"]
    return dict(parser["Desktop Entry"])


def split_exec(value: str) -> list[str]:
    """Undo the string escapes and the ``Exec`` quoting, the way the Desktop Entry spec reads them.

    Written from the spec, not from the code under test, so the two can't share a mistake.
    """
    string_escapes = {"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}
    unescaped, chars = "", iter(value)
    for char in chars:
        unescaped += string_escapes[next(chars)] if char == "\\" else char

    arguments: list[str] = []
    current: str | None = None
    quoted = False
    chars = iter(unescaped)
    for char in chars:
        if quoted:
            if char == "\\":
                escaped = next(chars)
                assert escaped in '"`$\\', f"stray backslash before {escaped!r}"
                current += escaped
            elif char == '"':
                quoted = False
            else:
                current += char
        elif char == " ":
            if current is not None:
                arguments.append(current)
            current = None
        elif char == '"':
            quoted, current = True, current or ""
        else:
            current = (current or "") + char
    assert not quoted, "unterminated quote"
    if current is not None:
        arguments.append(current)
    return [argument.replace("%%", "%") for argument in arguments]


@pytest.fixture(autouse=True)
def data_home(tmp_path, monkeypatch) -> Path:
    """Keep every test away from the real ``~/.local/share`` and ``~/.config``."""
    home = tmp_path / "data home"
    monkeypatch.setenv("XDG_DATA_HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config home"))
    return home


@pytest.fixture
def script(tmp_path, monkeypatch) -> Path:
    """A ``system-status`` command on PATH that runs this process, like ``uv tool install``."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    path = bin_dir / "system-status"
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setattr(sys, "argv", [str(path)])
    return path


@pytest.fixture
def gui_started(monkeypatch) -> list:
    started: list = []
    monkeypatch.setattr(cli, "start_gui", lambda *args: started.append(args) or 0)
    return started


class TestInstall:
    def test_writes_an_entry_that_opens_the_window(self, data_home, tmp_path):
        executable = tmp_path / "bin dir" / "system-status"

        entry_path, _ = launcher.install(executable, None)

        assert entry_path == data_home / "applications" / "system-status.desktop"
        entry = read_entry(entry_path)
        assert split_exec(entry.pop("Exec")) == [str(executable)]
        assert entry == {
            "Type": "Application",
            "Name": "System Status",
            "GenericName": "System Monitor",
            "Comment": "Graph CPU, memory, temperatures, fans, disks and GPUs",
            "Icon": "system-status",
            "Terminal": "false",
            "Categories": "System;Monitor;",
            "Keywords": "system;monitor;temperature;sensors;fan;gpu;cpu;hwmon;",
            "StartupNotify": "true",
            "StartupWMClass": "system-status",
        }

    def test_passes_the_config_as_an_absolute_path(self, data_home, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        entry_path, _ = launcher.install(Path("/opt/odd $dir/system-status"), Path("my 100%.toml"))

        assert split_exec(read_entry(entry_path)["Exec"]) == [
            "/opt/odd $dir/system-status",
            "--config",
            str(tmp_path / "my 100%.toml"),
        ]

    def test_copies_the_packaged_icon(self, data_home):
        _, icon_path = launcher.install(Path("/opt/bin/system-status"), None)

        assert icon_path == data_home / "icons/hicolor/scalable/apps/system-status.svg"
        assert icon_path.read_bytes() == launcher.icon_resource().read_bytes()

    def test_replaces_an_existing_launcher(self, data_home):
        launcher.install(Path("/old/system-status"), None)

        entry_path, _ = launcher.install(Path("/new/system-status"), None)

        assert split_exec(read_entry(entry_path)["Exec"]) == ["/new/system-status"]

    @pytest.mark.skipif(
        shutil.which("desktop-file-validate") is None, reason="desktop-file-validate missing"
    )
    def test_the_entry_passes_desktop_file_validate(self, data_home):
        entry_path, _ = launcher.install(Path("/opt/odd dir/system-status"), Path("/etc/a b.toml"))

        result = subprocess.run(
            ["desktop-file-validate", str(entry_path)], capture_output=True, text=True, check=False
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout == ""


class TestRemove:
    def test_deletes_the_launcher_and_the_icon(self, data_home):
        entry_path, icon_path = launcher.install(Path("/opt/bin/system-status"), None)

        assert launcher.remove() == (entry_path, icon_path)
        assert not entry_path.exists()
        assert not icon_path.exists()

    def test_without_a_launcher(self, data_home):
        assert launcher.remove() == (None, None)


def test_data_home_defaults_to_dot_local_share(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    for value in ("", "relative/dir"):
        monkeypatch.setenv("XDG_DATA_HOME", value)
        assert launcher.data_home() == tmp_path / ".local" / "share"


def test_executable_keeps_the_uv_tool_symlink(script, tmp_path):
    target = tmp_path / "tool env" / "system-status"
    target.parent.mkdir()
    target.write_text("#!/bin/sh\n")
    target.chmod(0o755)
    script.unlink()
    script.symlink_to(target)

    assert launcher.system_status_executable() == script


class TestPackagedIcon:
    def test_is_a_plain_square_svg(self):
        root = ElementTree.fromstring(launcher.icon_resource().read_bytes())

        assert root.tag == f"{SVG}svg"
        assert root.get("viewBox") == "0 0 128 128"
        # Only shapes: no text, no metadata, no references to other files.
        shapes = {f"{SVG}{name}" for name in ("svg", "rect", "path", "circle")}
        assert {element.tag for element in root.iter()} <= shapes
        assert not any("href" in name for element in root.iter() for name in element.attrib)

    def test_uses_the_graph_palette(self):
        root = ElementTree.fromstring(launcher.icon_resource().read_bytes())

        fills = {element.get("fill") for element in root.iter()}

        assert SERIES_COLORS[0] in fills

    def test_renders(self):
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtSvg import QSvgRenderer

        assert QSvgRenderer(str(launcher.icon_resource())).isValid()


class TestCli:
    def test_install_desktop_entry(self, data_home, script, gui_started, tmp_path):
        config = tmp_path / "settings.toml"
        config.write_text("history_seconds = 120\n")

        result = runner.invoke(cli.app, ["--install-desktop-entry", "--config", str(config)])

        assert result.exit_code == 0, result.output
        entry = data_home / "applications" / "system-status.desktop"
        icon = data_home / "icons" / "hicolor" / "scalable" / "apps" / "system-status.svg"
        exec_line = f"{script} --config {config}"
        assert f"Exec={exec_line}\n" in entry.read_text()
        assert result.output == (
            f"Wrote the launcher {entry}, which runs:\n  {exec_line}\nWrote the icon {icon}\n"
        )
        assert gui_started == []

    def test_install_without_config_runs_the_bare_command(self, data_home, script, gui_started):
        result = runner.invoke(cli.app, ["gui", "--install-desktop-entry"])

        assert result.exit_code == 0, result.output
        entry = data_home / "applications" / "system-status.desktop"
        assert f"Exec={script}\n" in entry.read_text()

    def test_install_checks_the_config(self, data_home, script, gui_started, tmp_path):
        broken = tmp_path / "broken.toml"
        broken.write_text("histroy_seconds = 1\n")

        result = runner.invoke(cli.app, ["--install-desktop-entry", "--config", str(broken)])

        assert result.exit_code == 1
        assert "histroy_seconds" in result.output
        assert not data_home.exists()

    def test_remove_desktop_entry(self, data_home, script, gui_started):
        runner.invoke(cli.app, ["--install-desktop-entry"])
        entry = data_home / "applications" / "system-status.desktop"
        icon = data_home / "icons" / "hicolor" / "scalable" / "apps" / "system-status.svg"

        removed = runner.invoke(cli.app, ["--remove-desktop-entry"])
        again = runner.invoke(cli.app, ["--remove-desktop-entry"])

        assert removed.exit_code == 0, removed.output
        assert removed.output == f"Removed the launcher {entry}\nRemoved the icon {icon}\n"
        assert not entry.exists() and not icon.exists()
        assert again.output == f"No launcher to remove at {entry}\nNo icon to remove at {icon}\n"
        assert gui_started == []

    @pytest.mark.parametrize(
        "options",
        [
            ["--install-desktop-entry", "--remove-desktop-entry"],
            ["--theme", "dark", "--install-desktop-entry"],
            ["gui", "--theme", "dark", "--remove-desktop-entry"],
        ],
    )
    def test_options_exclude_each_other(self, data_home, gui_started, options):
        result = runner.invoke(cli.app, options)

        assert result.exit_code == 2
        assert result.output == (
            "Error: use only one of --theme, --install-desktop-entry and --remove-desktop-entry\n"
        )
        assert not data_home.exists()
        assert gui_started == []

    def test_desktop_entry_options_take_no_command(self, data_home, gui_started):
        result = runner.invoke(cli.app, ["--install-desktop-entry", "sensors"])

        assert result.exit_code == 2
        assert not data_home.exists()

    def test_without_options_opens_the_window(self, data_home, gui_started, tmp_path):
        config = tmp_path / "settings.toml"

        result = runner.invoke(cli.app, ["--config", str(config), "--theme", "dark"])

        assert result.exit_code == 0, result.output
        assert gui_started == [(config, "dark")]
