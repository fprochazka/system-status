from system_status.core.config import Config, GraphConfig, SectionConfig, SensorOverride
from system_status.core.layout import default_sections, resolve_graph, unmatched_patterns
from system_status.core.series import Kind, SeriesInfo
from system_status.core.snapshot import Catalog

CATALOG = Catalog(
    (
        SeriesInfo("cpu:total", "CPU", Kind.PERCENT, "cpu"),
        SeriesInfo("cpu:core0", "CPU 1", Kind.PERCENT, "cpu-core"),
        SeriesInfo("cpu:core1", "CPU 2", Kind.PERCENT, "cpu-core"),
        SeriesInfo("mem:used", "Memory", Kind.PERCENT, "mem"),
        SeriesInfo("hwmon:k10temp/Tctl", "Package (Tctl)", Kind.TEMP, "cpu-temp"),
        SeriesInfo("gpu:nvidia0/util", "GPU", Kind.PERCENT, "gpu:nvidia0"),
        SeriesInfo("gpu:nvidia0/vram", "Video memory", Kind.PERCENT, "gpu:nvidia0"),
        SeriesInfo("gpu:nvidia0/temp", "Temperature", Kind.TEMP, "gpu:nvidia0"),
        SeriesInfo("gpu:nvidia0/fan", "Fan", Kind.PERCENT, "gpu:nvidia0"),
        SeriesInfo("hwmon:nct6687/Thermistor_15", "Thermistor 15", Kind.TEMP, "board-temp"),
        SeriesInfo(
            "hwmon:nct6687/temp6",
            "Thermistor 15 (temp6)",
            Kind.TEMP,
            "board-temp",
            hidden=True,
            hidden_reason="duplicate of Thermistor_15",
        ),
        SeriesInfo("hwmon:nct6687/fan1", "Fan 1", Kind.FAN, "fan"),
        SeriesInfo("hwmon:nct6687/VIN0", "VIN0", Kind.VOLTAGE, "voltage", hidden=True),
        SeriesInfo("net:total/rx", "Receiving", Kind.RATE, "net"),
        SeriesInfo("net:total/tx", "Sending", Kind.RATE, "net"),
    ),
    {"gpu:nvidia0": "NVIDIA GeForce GTX 1060 6GB"},
)


def _ids(graph: GraphConfig, sensors=None) -> list[str]:
    resolved = resolve_graph(graph, CATALOG, sensors or {})
    return [series.info.id for series in resolved.series]


def test_globs_keep_catalog_order_and_skip_hidden():
    assert _ids(GraphConfig("", ("cpu:core*",))) == ["cpu:core0", "cpu:core1"]
    assert _ids(GraphConfig("", ("hwmon:nct6687/*",), kind=Kind.TEMP)) == [
        "hwmon:nct6687/Thermistor_15"
    ]


def test_exact_id_shows_hidden_series():
    assert _ids(GraphConfig("", ("hwmon:nct6687/temp6",))) == ["hwmon:nct6687/temp6"]


def test_patterns_in_order_and_each_series_once():
    graph = GraphConfig("", ("cpu:core1", "cpu:*", "missing:thing"))

    assert _ids(graph) == ["cpu:core1", "cpu:total", "cpu:core0"]


def test_graph_kind_is_the_first_match_and_others_are_dropped():
    graph = GraphConfig("", ("hwmon:nct6687/fan1", "hwmon:nct6687/Thermistor_15"))
    resolved = resolve_graph(graph, CATALOG, {})

    assert resolved.kind == Kind.FAN
    assert [s.info.id for s in resolved.series] == ["hwmon:nct6687/fan1"]


def test_sensor_overrides():
    sensors = {
        "cpu:core0": SensorOverride(hidden=True),
        "hwmon:nct6687/temp6": SensorOverride(hidden=False, label="Board"),
        "cpu:core1": SensorOverride(label="Second", color="#ff0000"),
    }
    resolved = resolve_graph(GraphConfig("", ("cpu:core*",)), CATALOG, sensors)

    assert [(s.info.id, s.label, s.color) for s in resolved.series] == [
        ("cpu:core1", "Second", "#ff0000")
    ]
    assert "hwmon:nct6687/temp6" in _ids(GraphConfig("", ("hwmon:*",), kind=Kind.TEMP), sensors)
    assert _ids(GraphConfig("", ("cpu:core0",)), sensors) == []


def test_nothing_matched_has_no_kind():
    resolved = resolve_graph(GraphConfig("", ("nope:*",)), CATALOG, {})

    assert resolved.kind is None
    assert resolved.series == ()


def test_unmatched_patterns():
    config = Config(
        sections=(SectionConfig("A", (GraphConfig("", ("cpu:*", "gpu:amd0/*", "x:y")),)),)
    )

    assert unmatched_patterns(config, CATALOG) == ["gpu:amd0/*", "x:y"]


def test_default_layout():
    sections = {s.title: s for s in default_sections(CATALOG)}

    assert list(sections) == [
        "CPU",
        "Memory",
        "GPU · NVIDIA GeForce GTX 1060 6GB",
        "Fans",
        "Network",
        "Motherboard",
    ]
    cpu = sections["CPU"]
    # Per-core usage is left out of the default layout, and the usage graph has no legend.
    assert cpu.graphs[0].series == ("cpu:total",)
    assert not cpu.graphs[0].legend
    assert cpu.graphs[1].series == ("hwmon:k10temp/Tctl",)
    gpu = sections["GPU · NVIDIA GeForce GTX 1060 6GB"]
    assert [(g.title, g.series, g.right) for g in gpu.graphs] == [
        ("Usage", ("gpu:nvidia0/util", "gpu:nvidia0/vram"), ()),
        ("Temperature", ("gpu:nvidia0/temp",), ("gpu:nvidia0/fan",)),
    ]
    assert sections["Fans"].graphs[0].hide_zero
    # Hidden series stay out of the default layout.
    assert sections["Motherboard"].graphs[0].series == ("hwmon:nct6687/Thermistor_15",)
