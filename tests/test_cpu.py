import tomllib

import pytest

from system_status.core.config import ConfigError, parse_config
from system_status.core.layout import assign_colors, default_sections
from system_status.core.series import Kind, format_axis, format_value
from system_status.core.snapshot import Catalog
from system_status.core.sources.cpu import (
    CpuTimes,
    CpuTopologySource,
    breakdown,
    parse_cpu_list,
    parse_proc_stat,
    scan_cpu_topology,
)
from tests.sysfs import FakeSys


def _cpu(fake: FakeSys, cpu: int, l3: str | None, siblings: str, khz: int | None = None) -> None:
    base = f"devices/system/cpu/cpu{cpu}"
    fake.write(f"{base}/topology/thread_siblings_list", siblings)
    if l3 is not None:
        fake.write(f"{base}/cache/index3/shared_cpu_list", l3)
    if khz is not None:
        fake.write(f"{base}/cpufreq/scaling_cur_freq", str(khz))


def two_ccd_machine(fake: FakeSys) -> None:
    """4 cores, 8 threads, two L3 groups, numbered like a Ryzen: siblings are N and N+4."""
    for cpu in range(8):
        core = cpu % 4
        l3 = "0-1,4-5" if core < 2 else "2-3,6-7"
        _cpu(fake, cpu, l3, f"{core},{core + 4}", khz=(5_000_000 if cpu == 1 else 600_000 + cpu))
    fake.write("devices/system/cpu/online", "0-7")


def proc_stat(per_cpu: dict[int, tuple[int, ...]]) -> str:
    total = [sum(values[i] for values in per_cpu.values()) for i in range(8)]
    lines = ["cpu  " + " ".join(map(str, total)) + " 0 0"]
    for cpu, values in per_cpu.items():
        lines.append(f"cpu{cpu} " + " ".join(map(str, values)) + " 0 0")
    lines.append("intr 12345")
    return "\n".join(lines) + "\n"


def idle(ticks: int) -> tuple[int, ...]:
    return (0, 0, 0, ticks, 0, 0, 0, 0)


def test_parse_cpu_list():
    assert parse_cpu_list("0-7,16-23") == [*range(8), *range(16, 24)]
    assert parse_cpu_list("3") == [3]
    assert parse_cpu_list("0,2-3\n") == [0, 2, 3]


def test_topology(tmp_path):
    fake = FakeSys(tmp_path)
    two_ccd_machine(fake)

    topology = scan_cpu_topology(tmp_path)

    assert topology.ccds == ((0, 1, 4, 5), (2, 3, 6, 7))
    assert topology.cores == ((0, 4), (1, 5), (2, 6), (3, 7))
    assert [topology.ccd_of_core(i) for i in range(4)] == [0, 0, 1, 1]


def test_topology_without_cache_or_siblings_files(tmp_path):
    fake = FakeSys(tmp_path)
    for cpu in range(2):
        fake.write(f"devices/system/cpu/cpu{cpu}/topology/core_id", str(cpu))

    topology = scan_cpu_topology(tmp_path)

    assert topology.ccds == ((0, 1),)
    assert topology.cores == ((0,), (1,))


def test_series_ids_and_clusters(tmp_path):
    fake = FakeSys(tmp_path)
    two_ccd_machine(fake)
    series = {info.id: info for info in CpuTopologySource(tmp_path, tmp_path / "stat").series()}

    assert series["cpu:ccd0/usage"].label == "CCD 1"
    assert not series["cpu:ccd1/usage"].hidden
    assert series["cpu:ccd1/clock"].kind == Kind.FREQUENCY
    assert series["cpu:pcore2"].label == "Core 3"
    assert series["cpu:pcore2"].cluster == "CCD 2"
    assert {"cpu:user", "cpu:system", "cpu:iowait", "cpu:nice", "cpu:steal"} <= set(series)


def test_single_l3_group_hides_its_usage(tmp_path):
    fake = FakeSys(tmp_path)
    for cpu in range(4):
        _cpu(fake, cpu, "0-3", f"{cpu % 2},{cpu % 2 + 2}")
    series = {info.id: info for info in CpuTopologySource(tmp_path, tmp_path / "stat").series()}

    assert series["cpu:ccd0/usage"].hidden
    assert series["cpu:pcore0"].cluster is None
    # No cpufreq on this machine: no clock series.
    assert "cpu:ccd0/clock" not in series


def test_usage_per_ccd_and_physical_core(tmp_path):
    fake = FakeSys(tmp_path)
    two_ccd_machine(fake)
    stat = tmp_path / "stat"
    stat.write_text(proc_stat({cpu: idle(1000) for cpu in range(8)}))
    source = CpuTopologySource(tmp_path, stat)
    first = source.read()
    assert "cpu:ccd0/usage" not in first
    assert first["cpu:ccd0/clock"] == 5000
    assert first["cpu:ccd1/clock"] == pytest.approx(600.007)

    later = {cpu: idle(1100) for cpu in range(8)}
    # CPU 0 fully busy (user), its sibling CPU 4 idle; CPU 2 half busy in system time.
    later[0] = (100, 0, 0, 1000, 0, 0, 0, 0)
    later[2] = (0, 0, 40, 1050, 0, 5, 5, 0)
    stat.write_text(proc_stat(later))
    readings = source.read()

    # CCD 0: one of four CPUs busy for all 100 ticks.
    assert readings["cpu:ccd0/usage"] == pytest.approx(25)
    assert readings["cpu:ccd1/usage"] == pytest.approx(12.5)
    # A physical core is as busy as its busier thread.
    assert readings["cpu:pcore0"] == pytest.approx(100)
    assert readings["cpu:pcore2"] == pytest.approx(50)
    assert readings["cpu:pcore1"] == 0
    assert readings["cpu:user"] == pytest.approx(12.5)
    assert readings["cpu:system"] == pytest.approx(6.25)


def test_breakdown_adds_up_with_idle():
    delta = CpuTimes(user=30, nice=5, system=10, idle=40, iowait=8, irq=3, softirq=2, steal=2)
    parts = breakdown(delta)

    assert parts["system"] == pytest.approx(15)
    assert parts["iowait"] == pytest.approx(8)
    assert sum(parts.values()) + 40 == pytest.approx(100)
    assert breakdown(CpuTimes(0, 0, 0, 0, 0, 0, 0, 0)) == {}


def test_parse_proc_stat_and_counter_reset():
    total, per_cpu = parse_proc_stat(proc_stat({0: idle(10), 1: (1, 2, 3, 4, 5, 6, 7, 8)}))

    assert total == CpuTimes(1, 2, 3, 14, 5, 6, 7, 8)
    assert per_cpu[1].busy == 1 + 2 + 3 + 6 + 7 + 8
    # A counter that went backwards gives 0, not a negative delta.
    assert CpuTimes(5, 0, 0, 0, 0, 0, 0, 0).minus(CpuTimes(9, 0, 0, 0, 0, 0, 0, 0)).user == 0


def test_frequency_formatting():
    assert format_value(Kind.FREQUENCY, 5663.584) == "5.66 GHz"
    assert format_axis(Kind.FREQUENCY, 2000) == "2 GHz"


def test_ccd_colours_match_tccd_temperatures(tmp_path):
    fake = FakeSys(tmp_path)
    two_ccd_machine(fake)
    catalog = Catalog(tuple(CpuTopologySource(tmp_path, tmp_path / "stat").series()))

    usage = assign_colors(["cpu:total", "cpu:ccd0/usage", "cpu:ccd1/usage"], catalog)
    temps = assign_colors(
        ["hwmon:k10temp/Tctl", "hwmon:k10temp/Tccd1", "hwmon:k10temp/Tccd2"], catalog
    )
    clocks = assign_colors(["cpu:ccd0/clock", "cpu:ccd1/clock"], catalog)

    assert usage == temps == [(0, 0), (1, 0), (2, 0)]
    assert clocks == [(1, 0), (2, 0)]


def test_default_cpu_section(tmp_path):
    fake = FakeSys(tmp_path)
    two_ccd_machine(fake)
    from system_status.core.series import SeriesInfo

    catalog = Catalog(
        (SeriesInfo("cpu:total", "CPU", Kind.PERCENT, "cpu"),)
        + tuple(CpuTopologySource(tmp_path, tmp_path / "stat").series())
    )
    cpu = default_sections(catalog)[0]

    assert [(g.title, g.type) for g in cpu.graphs] == [
        ("Usage", "line"),
        ("Cores", "heatmap"),
        ("Clock", "line"),
    ]
    assert cpu.graphs[0].series == ("cpu:total", "cpu:ccd0/usage", "cpu:ccd1/usage")
    assert cpu.graphs[1].series == tuple(f"cpu:pcore{i}" for i in range(4))


GRAPH = '[[section]]\ntitle = "C"\n[[section.graph]]\nseries = ["cpu:pcore*"]\n'


def test_heatmap_config():
    graph = parse_config(tomllib.loads(GRAPH + 'type = "heatmap"\nrow_height = 9')).sections[0]

    assert graph.graphs[0].type == "heatmap"
    assert graph.graphs[0].row_height == 9


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ('type = "bars"', "type: expected one of line, heatmap"),
        ('type = "heatmap"\nstacked = true', "a heatmap can't be stacked"),
        ("row_height = 9", 'row_height: only for type = "heatmap"'),
        ('type = "heatmap"\nrow_height = 1', "row_height: must be between 2 and 40"),
        ('type = "heatmap"\nrow_height = "big"', "row_height: expected int"),
    ],
)
def test_heatmap_config_errors(extra, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(tomllib.loads(GRAPH + extra))
