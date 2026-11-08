import tomllib

import pytest

from system_status.core.axis import aligned_range
from system_status.core.config import ConfigError, GraphConfig, SensorOverride, parse_config
from system_status.core.layout import (
    default_sections,
    resolve_graph,
    right_title,
    unmatched_patterns,
)
from system_status.core.series import Kind, SeriesInfo
from system_status.core.snapshot import Catalog

CATALOG = Catalog(
    (
        SeriesInfo("gpu:nvidia0/util", "GPU", Kind.PERCENT, "gpu:nvidia0"),
        SeriesInfo("gpu:nvidia0/vram", "Video memory", Kind.PERCENT, "gpu:nvidia0"),
        SeriesInfo("gpu:nvidia0/temp", "Temperature", Kind.TEMP, "gpu:nvidia0"),
        SeriesInfo("gpu:nvidia0/fan", "Fan", Kind.PERCENT, "gpu:nvidia0"),
        SeriesInfo("gpu:nvidia0/power", "Power", Kind.POWER, "gpu:nvidia0"),
        SeriesInfo("gpu:amd0/util", "GPU", Kind.PERCENT, "gpu:amd0"),
        SeriesInfo("gpu:amd0/edge", "Edge", Kind.TEMP, "gpu:amd0"),
        SeriesInfo("gpu:amd0/PPT", "Power (PPT)", Kind.POWER, "apu-power"),
        SeriesInfo("hwmon:k10temp/Tctl", "Package", Kind.TEMP, "cpu-temp"),
        SeriesInfo("hwmon:nct6687/fan1", "Fan 1", Kind.FAN, "fan"),
        SeriesInfo("hwmon:nct6687/Thermistor_0", "Thermistor 0", Kind.TEMP, "board-temp"),
    ),
    {"gpu:nvidia0": "NVIDIA", "gpu:amd0": "AMD"},
)

GRAPH = '[[section]]\ntitle = "G"\n[[section.graph]]\nseries = ["gpu:nvidia0/temp"]\n'


def _graph(extra: str) -> GraphConfig:
    return parse_config(tomllib.loads(GRAPH + extra)).sections[0].graphs[0]


def test_right_axis_config():
    graph = _graph('right = ["gpu:nvidia0/fan"]\nright_kind = "percent"\nright_max = 80')

    assert graph.right == ("gpu:nvidia0/fan",)
    assert graph.right_kind == Kind.PERCENT
    assert graph.right_max == 80


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ('right = "gpu:nvidia0/fan"', "right: expected list"),
        ("right = [1]", "right: expected a list of series IDs"),
        ("right_max = 5", "right_max: only with a right axis"),
        ('right_kind = "fan"', "right_kind: only with a right axis"),
        ('right = ["a"]\nright_kind = "rpm"', "right_kind: unknown kind 'rpm'"),
        ('right = ["a"]\nright_min = 5\nright_max = 5', "right_min must be lower than right_max"),
        ('right = ["gpu:nvidia0/temp"]', "'gpu:nvidia0/temp' is also in series"),
        ('right = ["a"]\ntype = "heatmap"', "a heatmap has no right axis"),
    ],
)
def test_right_axis_config_errors(extra, message):
    with pytest.raises(ConfigError, match=message):
        _graph(extra)


def test_axis_assignment():
    graph = GraphConfig("T", ("gpu:nvidia0/temp",), right=("gpu:nvidia0/fan",))
    resolved = resolve_graph(graph, CATALOG, {})

    assert resolved.error is None
    assert (resolved.kind, resolved.right_kind) == (Kind.TEMP, Kind.PERCENT)
    assert [s.info.id for s in resolved.series] == ["gpu:nvidia0/temp"]
    assert [s.info.id for s in resolved.right_series] == ["gpu:nvidia0/fan"]


def test_left_glob_keeps_its_kind_beside_a_right_axis():
    graph = GraphConfig("T", ("hwmon:*",), kind=Kind.TEMP, right=("hwmon:nct6687/fan1",))
    resolved = resolve_graph(graph, CATALOG, {})

    assert [s.info.id for s in resolved.series] == [
        "hwmon:k10temp/Tctl",
        "hwmon:nct6687/Thermistor_0",
    ]
    assert resolved.right_kind == Kind.FAN


def test_right_matching_several_kinds_is_an_error():
    graph = GraphConfig("U", ("gpu:nvidia0/util",), right=("gpu:nvidia0/temp", "gpu:nvidia0/power"))
    resolved = resolve_graph(graph, CATALOG, {})

    assert resolved.right_series == ()
    assert "right matches series of several kinds (power, temp)" in resolved.error


def test_right_kind_narrows_a_right_glob():
    graph = GraphConfig("U", ("gpu:nvidia0/util",), right=("gpu:nvidia0/*",), right_kind=Kind.POWER)
    resolved = resolve_graph(graph, CATALOG, {})

    assert resolved.error is None
    assert [s.info.id for s in resolved.right_series] == ["gpu:nvidia0/power"]


def test_series_on_both_axes_is_an_error():
    graph = GraphConfig("U", ("gpu:nvidia0/*",), right=("gpu:nvidia0/vram",))
    resolved = resolve_graph(graph, CATALOG, {})

    assert "gpu:nvidia0/vram matches both series and right" in resolved.error
    assert resolved.right_series == ()


def test_unmatched_right_patterns_are_reported():
    config = parse_config(tomllib.loads(GRAPH + 'right = ["gpu:nvidia9/fan"]'))

    assert unmatched_patterns(config, CATALOG) == ["gpu:nvidia9/fan"]


def test_default_gpu_layout_pairs_axes_and_skips_apu_power():
    sections = {s.title: s for s in default_sections(CATALOG)}
    nvidia = sections["GPU · NVIDIA"]
    amd = sections["GPU · AMD"]

    assert [(g.title, g.series, g.right) for g in nvidia.graphs] == [
        ("Usage", ("gpu:nvidia0/util", "gpu:nvidia0/vram"), ("gpu:nvidia0/power",)),
        ("Temperature", ("gpu:nvidia0/temp",), ("gpu:nvidia0/fan",)),
    ]
    assert [(g.title, g.series, g.right) for g in amd.graphs] == [
        ("Usage", ("gpu:amd0/util",), ()),
        ("Temperature", ("gpu:amd0/edge",), ()),
    ]


@pytest.mark.parametrize(
    ("kind", "values", "intervals", "fixed", "expected"),
    [
        # Watts beside a 4-step percent axis: 4 round steps that cover 33 W.
        (Kind.POWER, [5, 33], 4, (None, None), (0, 40, [0, 10, 20, 30, 40])),
        # Percent stays 0-100 %, split evenly even into thirds.
        (Kind.PERCENT, [7], 3, (None, None), (0, 100, [0, 33.333333333, 66.666666667, 100])),
        (Kind.FAN, [0, 1503], 4, (None, None), (0, 2000, [0, 500, 1000, 1500, 2000])),
        (Kind.FAN, [900], 2, (None, 3000), (0, 3000, [0, 1500, 3000])),
    ],
)
def test_aligned_range(kind, values, intervals, fixed, expected):
    low, high, ticks = aligned_range(kind, values, intervals, *fixed)

    assert (low, high) == expected[:2]
    assert ticks == pytest.approx(expected[2])


@pytest.mark.parametrize(
    ("graph", "expected"),
    [
        # One right series with a short label: its label.
        (GraphConfig("U", ("gpu:nvidia0/util",), right=("gpu:nvidia0/power",)), "Power"),
        # Set in the config: that.
        (
            GraphConfig("U", ("gpu:nvidia0/util",), right=("gpu:nvidia0/power",), right_title="W"),
            "W",
        ),
        # Several right series: the kind's name.
        (
            GraphConfig("T", ("hwmon:k10temp/Tctl",), right=("gpu:nvidia0/temp", "gpu:amd0/edge")),
            "Temperature",
        ),
        (GraphConfig("U", ("gpu:nvidia0/util",)), None),
    ],
)
def test_right_title(graph, expected):
    assert right_title(resolve_graph(graph, CATALOG, {})) == expected


def test_right_title_prefers_the_kind_over_a_long_label():
    sensors = {"hwmon:nct6687/fan1": SensorOverride(label="Noctua NH-D15 (CPU_FAN1, 2x NF-A15)")}
    graph = GraphConfig("T", ("hwmon:k10temp/Tctl",), right=("hwmon:nct6687/fan1",))

    assert right_title(resolve_graph(graph, CATALOG, sensors)) == "Fan"


def test_right_title_config():
    graph = _graph('right = ["gpu:nvidia0/fan"]\nright_title = "Fan speed"')

    assert graph.right_title == "Fan speed"
    with pytest.raises(ConfigError, match="right_title: only with a right axis"):
        _graph('right_title = "Fan"')
