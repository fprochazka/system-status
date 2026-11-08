import tomllib

import pytest

from system_status.core.config import (
    Config,
    ConfigError,
    GraphConfig,
    SectionConfig,
    SensorOverride,
    dump_config,
    get_default_config_path,
    load_config,
    parse_config,
)
from system_status.core.series import Kind

EXAMPLE = """
history_seconds = 600
interval_seconds = 0.5
theme = "dark"

[sensors]
"hwmon:k10temp/Tctl"  = { label = "CPU package", color = "#e01b24" }
"hwmon:nct6687/temp6" = { hidden = true }

[[section]]
title = "CPU"
collapsed = true
  [[section.graph]]
  title = "Usage"
  series = ["cpu:total"]
  legend = false
  [[section.graph]]
  title = "Temperature"
  series = ["hwmon:k10temp/*"]
  kind = "temp"
  min = 20
  max = 100.5
  height = 160
  hide_zero = true
"""


def test_parse_example():
    config = parse_config(tomllib.loads(EXAMPLE))

    assert config.history_seconds == 600
    assert config.interval_seconds == 0.5
    assert config.theme == "dark"
    assert config.sensors["hwmon:k10temp/Tctl"] == SensorOverride("CPU package", "#e01b24")
    assert config.sensors["hwmon:nct6687/temp6"].hidden is True
    assert config.sections is not None
    cpu = config.sections[0]
    assert cpu.title == "CPU" and cpu.collapsed
    assert cpu.graphs[0] == GraphConfig("Usage", ("cpu:total",), legend=False)
    temperature = cpu.graphs[1]
    assert temperature.kind == Kind.TEMP
    assert (temperature.min, temperature.max, temperature.height) == (20, 100.5, 160)
    assert temperature.hide_zero


def test_no_sections_means_default_layout():
    assert parse_config({"sensors": {}}).sections is None
    assert parse_config({"section": []}).sections == ()


@pytest.mark.parametrize(
    ("toml", "message"),
    [
        ("histroy_seconds = 10", 'settings: unknown key "histroy_seconds"'),
        ("history_seconds = 1", "history_seconds: expected a whole number between 10 and 86400"),
        ("interval_seconds = true", "interval_seconds: expected a number"),
        ('theme = "pink"', "theme: expected one of auto, light, dark"),
        ('[sensors]\n"x" = { lable = "y" }', 'sensors."x": unknown key "lable"'),
        ('[sensors]\n"x" = { color = "red" }', 'sensors."x".color: expected "#rrggbb"'),
        ('[sensors]\n"x" = "label"', 'sensors."x": expected a table'),
        ("[[section]]\ncollapsed = true", 'section[0]: missing key "title"'),
        ('[[section]]\ntitle = "A"\nfoo = 1', 'section[0]: unknown key "foo"'),
        ('[[section]]\ntitle = "A"\n[[section.graph]]\ntitle = "B"', 'missing key "series"'),
        (
            '[[section]]\ntitle = "A"\n[[section.graph]]\nseries = ["a"]\nlegend = "no"',
            "section[0].graph[0].legend: expected bool",
        ),
        (
            '[[section]]\ntitle = "A"\n[[section.graph]]\nseries = ["a"]\nkind = "rpm"',
            "section[0].graph[0].kind: unknown kind 'rpm'",
        ),
        (
            '[[section]]\ntitle = "A"\n[[section.graph]]\nseries = ["a"]\nmin = 5\nmax = 5',
            "min must be lower than max",
        ),
        (
            '[[section]]\ntitle = "A"\n[[section.graph]]\nseries = ["a"]\nmax = true',
            "max: expected a number",
        ),
        (
            '[[section]]\ntitle = "A"\n[[section.graph]]\nseries = [1]',
            "series: expected a list of series IDs",
        ),
    ],
)
def test_invalid_config_names_the_place(toml, message):
    with pytest.raises(ConfigError) as error:
        parse_config(tomllib.loads(toml))

    assert message in str(error.value)


def test_load_missing_file_gives_defaults(tmp_path):
    assert load_config(tmp_path / "missing.toml") == Config()


def test_load_reports_invalid_toml_with_path(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text("history_seconds = ")

    with pytest.raises(ConfigError, match=r"settings\.toml: invalid TOML"):
        load_config(path)


def test_dump_round_trips():
    config = Config(
        history_seconds=120,
        interval_seconds=2.0,
        theme="light",
        sensors={"a:b/c": SensorOverride(label='Quote "x"', color="#123456", hidden=False)},
        sections=(
            SectionConfig(
                "Ünïcode",
                (
                    GraphConfig(
                        "One",
                        ("a:b/c",),
                        legend=False,
                        kind=Kind.FAN,
                        min=0,
                        max=3000,
                        right=("x:y/fan", "x:*/pwm"),
                        right_kind=Kind.PERCENT,
                        right_min=0,
                        right_max=100,
                        right_title="Fan",
                    ),
                    GraphConfig("Many", ("x", "y", "z*"), hide_zero=True, stacked=True, height=200),
                ),
                collapsed=True,
            ),
        ),
    )

    text = dump_config(config)

    assert text.startswith("# system-status settings.")
    assert parse_config(tomllib.loads(text)) == config


def test_default_path_follows_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert get_default_config_path() == tmp_path / "system-status" / "settings.toml"
