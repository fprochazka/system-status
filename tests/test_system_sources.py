import pytest

from system_status.core.sources.gpu import AmdGpuSource, NvidiaGpuSource
from system_status.core.sources.system import (
    SECTOR_BYTES,
    CpuMemorySource,
    DiskIOSource,
    NetworkSource,
    RateTracker,
)
from tests.sysfs import FakeSys, build_machine


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def fake(tmp_path) -> FakeSys:
    fake = FakeSys(tmp_path)
    build_machine(fake)
    return fake


def test_rate_tracker():
    clock = Clock()
    tracker = RateTracker(clock)

    assert tracker.rates({"a": 1000}) == {"a": None}
    clock.now += 2
    assert tracker.rates({"a": 3000}) == {"a": 1000}
    clock.now += 1
    # A counter that went down was reset: no rate rather than a negative one.
    assert tracker.rates({"a": 10}) == {"a": None}
    clock.now += 1
    assert tracker.rates({"a": 110}) == {"a": 100}
    assert tracker.rates({"a": None}) == {"a": None}


def test_disk_io_rates_and_ids(fake):
    clock = Clock()
    source = DiskIOSource(fake.root, clock=clock)

    assert [info.id for info in source.series()][:2] == [
        "disk:WD_Red_SN700_2000GB/read",
        "disk:WD_Red_SN700_2000GB/write",
    ]
    assert source.read()["disk:WD_Red_SN700_2000GB/read"] is None
    fake.write("class/block/nvme0n1/stat", "10 0 2048 0 5 0 4096 0 0 0 0")
    clock.now += 1
    readings = source.read()

    assert readings["disk:WD_Red_SN700_2000GB/read"] == 2048 * SECTOR_BYTES
    assert readings["disk:WD_Red_SN700_2000GB/write"] == 4096 * SECTOR_BYTES
    assert readings["disk:ST4000VN008-2DR1/read"] == 0


def test_network_total_counts_physical_interfaces_only(fake):
    clock = Clock()
    source = NetworkSource(fake.root, clock=clock)
    series = {info.id: info for info in source.series()}

    assert "net:lo/rx" not in series
    assert series["net:docker0/rx"].hidden
    assert not series["net:enp7s0/rx"].hidden

    source.read()
    fake.write(
        "devices/pci0000:00/0000:04:05.0/0000:07:00.0/net/enp7s0/statistics/rx_bytes", "3000"
    )
    fake.write("devices/virtual/net/docker0/statistics/rx_bytes", "100050")
    clock.now += 2
    readings = source.read()

    assert readings["net:total/rx"] == 1000
    assert readings["net:enp7s0/rx"] == 1000
    assert readings["net:docker0/rx"] == 50000


def test_amd_gpu_utilization_and_vram(fake):
    source = AmdGpuSource(fake.root)

    assert [info.id for info in source.series()] == ["gpu:amd0/util", "gpu:amd0/vram"]
    readings = source.read()
    assert readings["gpu:amd0/util"] == 7
    assert readings["gpu:amd0/vram"] == pytest.approx(25)
    assert source.details()["gpu:amd0/vram"] == "512 MiB of 2.0 GiB"


class BrokenNvml:
    """An NVML whose every call fails, like a driver that is installed but not loaded."""

    def __getattr__(self, name):
        def fail(*args):
            raise RuntimeError("NVML_ERROR_DRIVER_NOT_LOADED")

        return fail


def test_nvidia_without_a_working_driver_has_no_series():
    source = NvidiaGpuSource(nvml=BrokenNvml())

    assert source.series() == []
    assert source.read() == {}


def test_cpu_and_memory_series():
    source = CpuMemorySource()
    ids = [info.id for info in source.series()]
    readings = source.read()

    assert ids[0] == "cpu:total"
    assert "cpu:core0" in ids
    assert 0 <= readings["mem:used"] <= 100
    assert " of " in source.details()["mem:used"]
