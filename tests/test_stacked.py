import math
import tomllib

import pytest

from system_status.core.axis import stack_values
from system_status.core.config import ConfigError, GraphConfig, parse_config
from system_status.core.layout import assign_colors, default_sections, resolve_graph
from system_status.core.series import Kind, SeriesInfo
from system_status.core.snapshot import Catalog

NAN = math.nan

DISKS = Catalog(
    (
        SeriesInfo("disk:WD/temp", "WD", Kind.TEMP, "disk-temp"),
        SeriesInfo("disk:Samsung/Composite", "Samsung", Kind.TEMP, "disk-temp"),
        SeriesInfo("disk:Samsung/Sensor_1", "Samsung Sensor 1", Kind.TEMP, "disk-temp"),
        SeriesInfo("disk:HDD/temp", "HDD", Kind.TEMP, "disk-temp"),
        SeriesInfo("disk:WD/read", "WD read", Kind.RATE, "disk-read"),
        SeriesInfo("disk:WD/write", "WD write", Kind.RATE, "disk-write"),
        SeriesInfo("disk:Samsung/read", "Samsung read", Kind.RATE, "disk-read"),
        SeriesInfo("disk:Samsung/write", "Samsung write", Kind.RATE, "disk-write"),
        SeriesInfo("disk:HDD/read", "HDD read", Kind.RATE, "disk-read"),
        SeriesInfo("disk:HDD/write", "HDD write", Kind.RATE, "disk-write"),
        SeriesInfo("cpu:total", "CPU", Kind.PERCENT, "cpu"),
    )
)


def _same(actual, expected) -> bool:
    return len(actual) == len(expected) and all(
        (math.isnan(a) and math.isnan(e)) or a == e for a, e in zip(actual, expected, strict=True)
    )


def test_stack_values_adds_layers_up():
    layers = stack_values([(1, 2, 3), (10, 20, 30), (100, 0, 0)])

    assert layers == [(1, 2, 3), (11, 22, 33), (111, 22, 33)]


def test_stack_values_missing_counts_as_zero_unless_all_missing():
    layers = stack_values([(1, NAN, NAN, 4), (NAN, 2, NAN, 5)])

    assert _same(layers[0], (1, 0, NAN, 4))
    assert _same(layers[1], (1, 2, NAN, 9))


def test_stack_values_uses_the_shortest_series_and_handles_nothing():
    assert stack_values([(1, 2, 3), (1,)]) == [(1,), (2,)]
    assert stack_values([]) == []


def test_stacked_config_option():
    toml = '[[section]]\ntitle = "D"\n[[section.graph]]\nseries = ["disk:*"]\nstacked = true'
    graph = parse_config(tomllib.loads(toml)).sections[0].graphs[0]

    assert graph.stacked


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ('stacked = "yes"', "stacked: expected bool"),
        ('stacked = true\nkind = "temp"', "stacked: temp values can't be added up"),
    ],
)
def test_stacked_config_errors(extra, message):
    toml = f'[[section]]\ntitle = "D"\n[[section.graph]]\nseries = ["disk:*"]\n{extra}'

    with pytest.raises(ConfigError, match=message):
        parse_config(tomllib.loads(toml))


def test_stacked_graph_refuses_mixed_kinds():
    resolved = resolve_graph(GraphConfig("Disks", ("disk:WD/*",), stacked=True), DISKS, {})

    assert resolved.series == ()
    assert "several kinds (rate, temp)" in resolved.error


def test_stacked_graph_with_explicit_kind_keeps_that_kind():
    graph = GraphConfig("Disks", ("disk:*",), stacked=True, kind=Kind.RATE)
    resolved = resolve_graph(graph, DISKS, {})

    assert resolved.error is None
    assert [s.info.id for s in resolved.series][:2] == ["disk:WD/read", "disk:WD/write"]


def test_stacked_temperatures_are_refused():
    resolved = resolve_graph(GraphConfig("T", ("disk:*/temp",), stacked=True), DISKS, {})

    assert resolved.error == 'graph "T": temp values can\'t be stacked'


def test_default_layout_has_one_stacked_disk_throughput_graph():
    disks = next(s for s in default_sections(DISKS) if s.title == "Disks")

    assert [g.title for g in disks.graphs] == ["Temperature", "Throughput"]
    throughput = disks.graphs[1]
    assert throughput.stacked and throughput.hide_zero == "device"
    assert throughput.series == (
        "disk:WD/read",
        "disk:WD/write",
        "disk:Samsung/read",
        "disk:Samsung/write",
        "disk:HDD/read",
        "disk:HDD/write",
    )


def test_disk_colours_follow_the_device_in_every_graph():
    temperatures = assign_colors(
        ["disk:WD/temp", "disk:Samsung/Composite", "disk:Samsung/Sensor_1", "disk:HDD/temp"],
        DISKS,
    )
    # A graph with the WD disk left out (hide_zero) keeps the others' colours.
    throughput = assign_colors(
        ["disk:Samsung/read", "disk:Samsung/write", "disk:HDD/read", "disk:HDD/write"], DISKS
    )

    assert temperatures == [(0, 0), (1, 0), (1, 1), (2, 0)]
    assert throughput == [(1, 0), (1, 1), (2, 0), (2, 1)]


def test_other_series_are_coloured_by_position():
    assert assign_colors(["cpu:total", "hwmon:x/a", "disk:HDD/temp", "hwmon:x/b"], DISKS) == [
        (0, 0),
        (1, 0),
        (2, 0),
        (3, 0),
    ]
