"""Read every source once per interval on a background thread and keep a fixed-size history."""

import logging
import math
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from pathlib import Path

from system_status.core.snapshot import Catalog, Snapshot
from system_status.core.sources.base import Readings, Source
from system_status.core.sources.cpu import CpuTopologySource
from system_status.core.sources.devices import scan_amd_gpus, scan_disks
from system_status.core.sources.gpu import AmdGpuSource, NvidiaGpuSource
from system_status.core.sources.hwmon import HwmonSource
from system_status.core.sources.system import CpuMemorySource, DiskIOSource, NetworkSource

logger = logging.getLogger(__name__)


def build_sources(sys_root: Path = Path("/sys")) -> list[Source]:
    """Create every source of this machine. Discovery happens here, once."""
    disks = scan_disks(sys_root)
    amd_gpus = scan_amd_gpus(sys_root)
    return [
        CpuMemorySource(),
        CpuTopologySource(sys_root),
        NvidiaGpuSource(),
        AmdGpuSource(sys_root, amd_gpus),
        HwmonSource(sys_root, disks, amd_gpus),
        DiskIOSource(sys_root, disks),
        NetworkSource(sys_root),
    ]


def build_catalog(sources: Sequence[Source]) -> Catalog:
    """Collect the series of ``sources``; the first source to claim an ID keeps it."""
    seen: set[str] = set()
    series = []
    gpu_names: dict[str, str] = {}
    for source in sources:
        for info in source.series():
            if info.id in seen:
                logger.warning("Series %s is reported twice; keeping the first", info.id)
                continue
            seen.add(info.id)
            series.append(info)
        names = getattr(source, "names", None)
        if callable(names):
            gpu_names.update({f"gpu:{key}": name for key, name in names().items()})
    return Catalog(tuple(series), gpu_names)


def read_all(sources: Sequence[Source]) -> tuple[Readings, dict[str, str]]:
    """Read every source; one that fails gives no values for this tick and is logged."""
    readings: Readings = {}
    details: dict[str, str] = {}
    for source in sources:
        try:
            readings.update(source.read())
            details.update(source.details())
        except Exception:
            logger.exception("Reading %s failed", type(source).__name__)
    return readings, details


class History:
    """A ring of the last ``capacity`` ticks: one timestamp and one value per series per tick."""

    def __init__(self, series_ids: Sequence[str], capacity: int):
        if capacity < 2:
            raise ValueError("capacity must be at least 2")
        self.capacity = capacity
        self.timestamps: deque[float] = deque(maxlen=capacity)
        self.values: dict[str, deque[float]] = {
            series_id: deque(maxlen=capacity) for series_id in series_ids
        }

    def append(self, timestamp: float, readings: Readings) -> None:
        self.timestamps.append(timestamp)
        for series_id, values in self.values.items():
            value = readings.get(series_id)
            values.append(math.nan if value is None else float(value))

    def __len__(self) -> int:
        return len(self.timestamps)


class Sampler:
    """Sample ``sources`` every ``interval_seconds`` on a daemon thread.

    The GUI calls ``snapshot()`` from its own thread; the history is only touched under the lock,
    and the snapshot is a copy.
    """

    def __init__(
        self,
        sources: Sequence[Source],
        interval_seconds: float = 1.0,
        history_seconds: float = 300,
        clock: Callable[[], float] = time.time,
    ):
        self.sources = list(sources)
        self.catalog = build_catalog(self.sources)
        self.interval_seconds = interval_seconds
        self.history_seconds = history_seconds
        capacity = max(2, math.ceil(history_seconds / interval_seconds) + 1)
        self._history = History([info.id for info in self.catalog.series], capacity)
        self._details: dict[str, str] = {}
        self._clock = clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def sample(self) -> None:
        """Take one sample now, on the calling thread."""
        readings, details = read_all(self.sources)
        with self._lock:
            self._history.append(self._clock(), readings)
            self._details.update(details)

    def snapshot(self) -> Snapshot:
        with self._lock:
            return Snapshot(
                catalog=self.catalog,
                interval_seconds=self.interval_seconds,
                history_seconds=self.history_seconds,
                timestamps=tuple(self._history.timestamps),
                values={key: tuple(values) for key, values in self._history.values.items()},
                details=dict(self._details),
            )

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="sampler", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        next_tick = time.monotonic()
        while not self._stop.is_set():
            self.sample()
            next_tick += self.interval_seconds
            now = time.monotonic()
            if next_tick < now:
                # Late after a suspend: resume the cadence from now instead of catching up.
                next_tick = now + self.interval_seconds
            self._stop.wait(next_tick - now)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def close(self) -> None:
        self.stop()
        for source in self.sources:
            source.close()
