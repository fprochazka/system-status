"""The CLI's option handling, on a fake machine so the commands don't read real hardware."""

import json

import pytest
from typer.testing import CliRunner

from system_status import cli
from system_status.core.sampler import build_sources
from tests.sysfs import FakeSys, build_machine

runner = CliRunner()


@pytest.fixture(autouse=True)
def machine(tmp_path, monkeypatch):
    fake = FakeSys(tmp_path / "sys")
    build_machine(fake)
    monkeypatch.setattr(cli, "build_sources", lambda: build_sources(fake.root))
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config home"))


@pytest.fixture
def gui_started(monkeypatch) -> list:
    started: list = []
    monkeypatch.setattr(cli, "start_gui", lambda *args: started.append(args) or 0)
    return started


def _settings(tmp_path, name: str, text: str):
    path = tmp_path / name
    path.write_text(text)
    return path


def _sensors_json(arguments: list[str]) -> dict:
    result = runner.invoke(cli.app, [*arguments, "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _label(report: dict, series_id: str) -> str:
    return next(row["label"] for row in report["series"] if row["id"] == series_id)


class TestGlobalOptions:
    def test_config_before_the_command_name_applies_to_it(self, tmp_path):
        path = _settings(tmp_path, "a.toml", '[sensors]\n"hwmon:k10temp/Tctl" = { label = "A" }\n')

        report = _sensors_json(["--config", str(path), "sensors"])

        assert _label(report, "hwmon:k10temp/Tctl") == "A"

    def test_the_commands_own_option_wins(self, tmp_path):
        first = _settings(tmp_path, "a.toml", '[sensors]\n"hwmon:k10temp/Tctl" = { label = "A" }\n')
        second = _settings(
            tmp_path, "b.toml", '[sensors]\n"hwmon:k10temp/Tctl" = { label = "B" }\n'
        )

        report = _sensors_json(["--config", str(first), "sensors", "--config", str(second)])

        assert _label(report, "hwmon:k10temp/Tctl") == "B"

    def test_dump_config_reads_the_config_given_before_it(self, tmp_path):
        path = _settings(tmp_path, "a.toml", "history_seconds = 120\n")

        result = runner.invoke(cli.app, ["-c", str(path), "dump-config"])

        assert result.exit_code == 0, result.output
        assert "history_seconds = 120\n" in result.stdout

    def test_theme_before_gui_applies_to_it(self, tmp_path, gui_started):
        path = tmp_path / "settings.toml"

        result = runner.invoke(cli.app, ["--theme", "dark", "--config", str(path), "gui"])

        assert result.exit_code == 0, result.output
        assert gui_started == [(path, "dark")]

    @pytest.mark.parametrize(
        ("arguments", "message"),
        [
            (["--theme", "dark", "sensors"], "Error: sensors doesn't take --theme\n"),
            (["--theme", "dark", "dump-config"], "Error: dump-config doesn't take --theme\n"),
            (["--config", "x.toml", "version"], "Error: version doesn't take --config\n"),
        ],
    )
    def test_an_option_the_command_doesnt_take_is_an_error(self, arguments, message):
        result = runner.invoke(cli.app, arguments)

        assert result.exit_code == 2
        assert result.output == message


class TestUnknownOverrides:
    def test_sensors_reports_override_ids_that_no_series_has(self, tmp_path):
        path = _settings(
            tmp_path,
            "a.toml",
            '[sensors]\n"hwmon:k10temp/Tctl" = { label = "A" }\n'
            '"disk:Unplugged_Disk/temp" = { label = "Gone" }\n'
            '"hwmon:k10temp/*" = { hidden = true }\n',
        )

        report = _sensors_json(["sensors", "--config", str(path)])
        text = runner.invoke(cli.app, ["sensors", "--config", str(path)])

        # Overrides are exact IDs: a glob there is just an ID that no series has.
        assert report["unmatched_sensors"] == ["disk:Unplugged_Disk/temp", "hwmon:k10temp/*"]
        assert text.exit_code == 0
        assert "[sensors] overrides in" in text.stderr
        assert "  disk:Unplugged_Disk/temp\n" in text.stderr
