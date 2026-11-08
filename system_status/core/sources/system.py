"""CPU and memory from psutil; disk and network throughput from sysfs counters."""

import time
from collections.abc import Callable
from pathlib import Path

import psutil

from system_status.core.series import Kind, SeriesInfo, format_bytes
from system_status.core.sources.base import Readings, Source, read_int, read_text
from system_status.core.sources.devices import Disk, scan_disks, scan_net_interfaces

# The kernel counts block I/O in 512-byte sectors, whatever the disk's real sector size is.
SECTOR_BYTES = 512


class RateTracker:
    """Turn ever-growing counters into per-second rates.

    The first value of a counter has no rate. A counter that goes down (a device that was reset,
    a wrap) gives no rate for that tick either, instead of a huge negative spike.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._last: dict[str, tuple[float, int]] = {}

    def rates(self, counters: dict[str, int | None]) -> Readings:
        now = self._clock()
        rates: Readings = {}
        for key, value in counters.items():
            previous = self._last.get(key)
            if value is None:
                self._last.pop(key, None)
                rates[key] = None
                continue
            self._last[key] = (now, value)
            if previous is None or now <= previous[0] or value < previous[1]:
                rates[key] = None
            else:
                rates[key] = (value - previous[1]) / (now - previous[0])
        return rates


class CpuMemorySource(Source):
    """``cpu:total``, ``cpu:core<N>``, ``mem:used`` and ``swap:used``, all in percent."""

    def __init__(self) -> None:
        self._cores = psutil.cpu_count(logical=True) or 0
        self._primed = False
        self._details: dict[str, str] = {}
        # The first call starts psutil's CPU time counting; its result means nothing.
        psutil.cpu_percent(percpu=True)

    def series(self) -> list[SeriesInfo]:
        series = [SeriesInfo("cpu:total", "CPU", Kind.PERCENT, "cpu")]
        series += [
            SeriesInfo(f"cpu:core{n}", f"CPU {n + 1}", Kind.PERCENT, "cpu-core")
            for n in range(self._cores)
        ]
        series.append(SeriesInfo("mem:used", "Memory", Kind.PERCENT, "mem"))
        series.append(SeriesInfo("swap:used", "Swap", Kind.PERCENT, "mem"))
        return series

    def read(self) -> Readings:
        readings: Readings = {}
        per_core = psutil.cpu_percent(percpu=True)
        if per_core:
            readings["cpu:total"] = sum(per_core) / len(per_core)
            for n, value in enumerate(per_core):
                readings[f"cpu:core{n}"] = value
        memory = psutil.virtual_memory()
        readings["mem:used"] = memory.percent
        used = memory.total - memory.available
        self._details["mem:used"] = f"{format_bytes(used)} of {format_bytes(memory.total)}"
        swap = psutil.swap_memory()
        readings["swap:used"] = swap.percent if swap.total else None
        self._details["swap:used"] = f"{format_bytes(swap.used)} of {format_bytes(swap.total)}"
        return readings

    def details(self) -> dict[str, str]:
        return dict(self._details)


class DiskIOSource(Source):
    """``disk:<model>/read`` and ``disk:<model>/write`` in bytes per second."""

    def __init__(self, sys_root: Path = Path("/sys"), disks: list[Disk] | None = None, clock=None):
        self.sys_root = sys_root
        self.disks = scan_disks(sys_root) if disks is None else disks
        self._rates = RateTracker(clock or time.monotonic)

    def series(self) -> list[SeriesInfo]:
        series = []
        for disk in self.disks:
            for direction in ("read", "write"):
                series.append(
                    SeriesInfo(
                        f"disk:{disk.key}/{direction}",
                        f"{disk.model} {direction}",
                        Kind.RATE,
                        f"disk-{direction}",
                    )
                )
        return series

    def read(self) -> Readings:
        counters: dict[str, int | None] = {}
        for disk in self.disks:
            stat = read_text(self.sys_root / "class" / "block" / disk.kernel_name / "stat")
            fields = stat.split() if stat else []
            ok = len(fields) >= 7
            counters[f"disk:{disk.key}/read"] = int(fields[2]) * SECTOR_BYTES if ok else None
            counters[f"disk:{disk.key}/write"] = int(fields[6]) * SECTOR_BYTES if ok else None
        return self._rates.rates(counters)


class NetworkSource(Source):
    """``net:<interface>/rx|tx`` and ``net:total/rx|tx`` in bytes per second.

    ``net:total`` sums the physical interfaces only, because traffic through a bridge, a veth or
    a VPN tunnel also crosses a physical interface and would be counted twice. Virtual interfaces
    are hidden: a glob doesn't match them, their exact ID does.
    """

    def __init__(self, sys_root: Path = Path("/sys"), clock=None):
        self.sys_root = sys_root
        self.interfaces = [i for i in scan_net_interfaces(sys_root) if i.name != "lo"]
        self._rates = RateTracker(clock or time.monotonic)

    def series(self) -> list[SeriesInfo]:
        series = [
            SeriesInfo("net:total/rx", "Receiving", Kind.RATE, "net"),
            SeriesInfo("net:total/tx", "Sending", Kind.RATE, "net"),
        ]
        for interface in self.interfaces:
            for direction, word in (("rx", "receiving"), ("tx", "sending")):
                series.append(
                    SeriesInfo(
                        f"net:{interface.name}/{direction}",
                        f"{interface.name} {word}",
                        Kind.RATE,
                        "net-interface",
                        hidden=interface.virtual,
                        hidden_reason="virtual interface" if interface.virtual else None,
                    )
                )
        return series

    def read(self) -> Readings:
        counters: dict[str, int | None] = {}
        totals = {"rx": 0, "tx": 0}
        for interface in self.interfaces:
            statistics = self.sys_root / "class" / "net" / interface.name / "statistics"
            for direction in ("rx", "tx"):
                value = read_int(statistics / f"{direction}_bytes")
                counters[f"net:{interface.name}/{direction}"] = value
                if value is not None and not interface.virtual:
                    totals[direction] += value
        counters["net:total/rx"] = totals["rx"]
        counters["net:total/tx"] = totals["tx"]
        return self._rates.rates(counters)
