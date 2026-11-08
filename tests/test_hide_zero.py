import math
import tomllib

import pytest

from system_status.core.config import (
    Config,
    ConfigError,
    GraphConfig,
    SectionConfig,
    dump_config,
    parse_config,
)
from system_status.core.layout import default_sections, zero_device, zero_hidden
from system_status.core.series import Kind, SeriesInfo
from system_status.core.snapshot import Catalog

NAN = math.nan

VALUES = {
    "disk:WD/read": (0.0, 0.0, 0.0),
    "disk:WD/write": (0.0, 4096.0, 0.0),
    "disk:HDD/read": (0.0, NAN, 0.0),
    "disk:HDD/write": (NAN, 0.0, 0.0),
    "net:total/rx": (0.0, 0.0, 0.0),
    "cpu:total": (0.0, 0.0, 0.0),
}


def test_zero_device_is_the_id_up_to_the_last_slash():
    assert zero_device("disk:WD_Red_SN700_2000GB/read") == "disk:WD_Red_SN700_2000GB"
    assert zero_device("hwmon:k10temp@0000_00_18.3/Tctl") == "hwmon:k10temp@0000_00_18.3"
    assert zero_device("cpu:total") == "cpu:total"


def test_per_series_hides_every_zero_series():
    assert zero_hidden(VALUES, True) == {
        "disk:WD/read",
        "disk:HDD/read",
        "disk:HDD/write",
        "net:total/rx",
        "cpu:total",
    }


def test_per_device_keeps_an_active_devices_zero_series():
    hidden = zero_hidden(VALUES, "device")

    # The WD wrote once, so its flat read line stays; the idle HDD goes entirely.
    assert "disk:WD/read" not in hidden
    assert "disk:WD/write" not in hidden
    assert {"disk:HDD/read", "disk:HDD/write"} <= hidden
    assert {"net:total/rx", "cpu:total"} <= hidden


def test_one_sample_of_activity_anywhere_in_the_window_counts():
    values = {"disk:A/read": (5.0,) + (0.0,) * 299, "disk:A/write": (0.0,) * 300}

    assert zero_hidden(values, "device") == set()
    assert zero_hidden(values, True) == {"disk:A/write"}
    assert zero_hidden(values, False) == set()


GRAPH = '[[section]]\ntitle = "D"\n[[section.graph]]\nseries = ["disk:*"]\n'


def test_config_accepts_device():
    graph = parse_config(tomllib.loads(GRAPH + 'hide_zero = "device"')).sections[0].graphs[0]

    assert graph.hide_zero == "device"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ('"disk"', "hide_zero: expected true, false or \"device\", got 'disk'"),
        ("1", "hide_zero: expected bool or str"),
    ],
)
def test_config_rejects_other_values(value, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(tomllib.loads(GRAPH + f"hide_zero = {value}"))


def test_dump_round_trips_device():
    config = Config(
        sections=(SectionConfig("D", (GraphConfig("T", ("disk:*",), hide_zero="device"),)),)
    )

    text = dump_config(config)

    assert 'hide_zero = "device"' in text
    assert parse_config(tomllib.loads(text)) == config


def test_default_disk_throughput_hides_by_device():
    catalog = Catalog(
        (
            SeriesInfo("disk:WD/read", "WD read", Kind.RATE, "disk-read"),
            SeriesInfo("disk:WD/write", "WD write", Kind.RATE, "disk-write"),
        )
    )
    disks = next(s for s in default_sections(catalog) if s.title == "Disks")

    assert disks.graphs[0].hide_zero == "device"
