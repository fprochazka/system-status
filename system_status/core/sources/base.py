"""What every source of samples provides."""

from abc import ABC, abstractmethod
from pathlib import Path

from system_status.core.series import SeriesInfo

Readings = dict[str, float | None]


class Source(ABC):
    """A group of series that are discovered once and then read on every tick.

    ``read()`` runs on the sampler thread and returns a value for each series of ``series()``
    that it could read; a missing or None value is a gap in the graph. Counters (bytes, CPU
    time) are turned into rates inside the source, so the first read may return no rates.
    ``details()`` returns optional extra text per series for the legend, such as "12.3 GiB of
    62.0 GiB" for memory.
    """

    @abstractmethod
    def series(self) -> list[SeriesInfo]: ...

    @abstractmethod
    def read(self) -> Readings: ...

    def details(self) -> dict[str, str]:
        return {}

    def close(self) -> None:  # noqa: B027 - most sources hold nothing to release
        pass


def read_text(path: Path) -> str | None:
    """Return the stripped content of a sysfs file, or None when it can't be read.

    Sysfs files may vanish (a device unplugged) or fail with EIO/ENODATA (a sensor that is
    asleep), so every read is allowed to fail.
    """
    try:
        return path.read_text(errors="replace").strip()
    except OSError:
        return None


def read_int(path: Path) -> int | None:
    text = read_text(path)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None
