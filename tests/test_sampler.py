import math

import pytest

from system_status.core.axis import axis_range, nice_step
from system_status.core.sampler import History, Sampler, build_catalog, build_sources
from system_status.core.series import Kind, SeriesInfo, format_axis, format_value, slug
from system_status.core.sources.base import Readings, Source
from tests.sysfs import FakeSys, build_machine


class CountingSource(Source):
    """Return the tick number for ``a`` and nothing for ``b`` on odd ticks."""

    def __init__(self) -> None:
        self.ticks = 0

    def series(self) -> list[SeriesInfo]:
        return [
            SeriesInfo("t:a", "A", Kind.PERCENT, "t"),
            SeriesInfo("t:b", "B", Kind.PERCENT, "t"),
        ]

    def read(self) -> Readings:
        self.ticks += 1
        return {"t:a": self.ticks, "t:b": None if self.ticks % 2 else self.ticks}

    def details(self) -> dict[str, str]:
        return {"t:a": f"tick {self.ticks}"}


class FailingSource(Source):
    def series(self) -> list[SeriesInfo]:
        return [SeriesInfo("t:a", "duplicate", Kind.PERCENT, "t")]

    def read(self) -> Readings:
        raise OSError("sensor went away")


def test_history_is_a_ring():
    history = History(["x"], capacity=3)
    for tick in range(5):
        history.append(float(tick), {"x": tick * 10})

    assert list(history.timestamps) == [2.0, 3.0, 4.0]
    assert list(history.values["x"]) == [20, 30, 40]


def test_history_marks_missing_values_as_nan():
    history = History(["x", "y"], capacity=3)
    history.append(1.0, {"x": 1})

    assert math.isnan(history.values["y"][0])


def test_sampler_snapshot():
    clock = iter(range(1000, 2000))
    sampler = Sampler(
        [CountingSource(), FailingSource()],
        interval_seconds=1,
        history_seconds=3,
        clock=lambda: float(next(clock)),
    )
    for _ in range(6):
        sampler.sample()
    snapshot = sampler.snapshot()

    # The first source keeps the ID; the failing one is skipped on every tick.
    assert [info.label for info in sampler.catalog.series] == ["A", "B"]
    assert snapshot.timestamps == (1002.0, 1003.0, 1004.0, 1005.0)
    assert snapshot.values["t:a"] == (3.0, 4.0, 5.0, 6.0)
    assert snapshot.latest("t:a") == 6
    assert snapshot.latest("t:b") == 6
    assert snapshot.details["t:a"] == "tick 6"


def test_sampler_thread_starts_and_stops():
    sampler = Sampler([CountingSource()], interval_seconds=0.1, history_seconds=10)
    sampler.start()
    try:
        for _ in range(100):
            if len(sampler.snapshot().timestamps) >= 2:
                break
            sampler._stop.wait(0.05)
    finally:
        sampler.close()

    assert len(sampler.snapshot().timestamps) >= 2


def test_build_sources_on_fake_machine(tmp_path):
    fake = FakeSys(tmp_path)
    build_machine(fake)
    catalog = build_catalog(build_sources(tmp_path))
    ids = [info.id for info in catalog.series]

    assert "hwmon:k10temp/Tctl" in ids
    assert "gpu:amd0/util" in ids
    assert "disk:WD_Red_SN700_2000GB/read" in ids
    assert catalog.gpu_names["gpu:amd0"] == "AMD Radeon Graphics"
    assert len(ids) == len(set(ids))


def test_slug():
    assert slug("WD Red SN700 2000GB    ") == "WD_Red_SN700_2000GB"
    assert slug("Diode 0 (curr)") == "Diode_0_curr"
    assert slug("r8169_0_700:00") == "r8169_0_700_00"
    assert slug("   ") == "unnamed"


def test_format_value():
    assert format_value(Kind.TEMP, 45.25) == "45.2 °C"
    assert format_value(Kind.PERCENT, 5.25) == "5.2 %"
    assert format_value(Kind.PERCENT, 55.5) == "56 %"
    assert format_value(Kind.RATE, 1536) == "1.5 KiB/s"
    assert format_value(Kind.FAN, None) == "–"
    assert format_axis(Kind.RATE, 512 * 1024, top=2 * 1024 * 1024) == "0.5 MiB/s"


def test_nice_step():
    assert nice_step(23) == 50
    assert nice_step(2.1) == 5
    assert nice_step(9) == 10
    assert nice_step(300 * 1024, binary=True) == 512 * 1024


@pytest.mark.parametrize(
    ("kind", "values", "fixed", "expected"),
    [
        (Kind.PERCENT, [3, 7], (None, None), (0, 100)),
        (Kind.TEMP, [40, 90], (None, None), (40, 100)),
        # A steady temperature still gets a 10 °C range around it.
        (Kind.TEMP, [36.5, 36.6], (None, None), (30, 45)),
        (Kind.FAN, [0, 1481], (None, None), (0, 1500)),
        (Kind.RATE, [0, 0], (None, None), (0, 64 * 1024)),
        (Kind.TEMP, [math.nan], (20, 100), (20, 100)),
    ],
)
def test_axis_range(kind, values, fixed, expected):
    low, high, ticks = axis_range(kind, values, *fixed)

    assert (low, high) == expected
    assert ticks[0] >= low and ticks[-1] <= high
    assert 2 <= len(ticks) <= 9
