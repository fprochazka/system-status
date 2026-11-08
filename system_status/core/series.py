"""What a series is: its stable ID, its label and the kind of value it carries."""

import math
import re
from dataclasses import dataclass
from enum import StrEnum


class Kind(StrEnum):
    """The physical quantity of a series. Graphs only mix series of one kind."""

    PERCENT = "percent"
    TEMP = "temp"
    FAN = "fan"
    POWER = "power"
    RATE = "rate"
    VOLTAGE = "voltage"
    FREQUENCY = "frequency"


# What a kind is called in a title, such as the default title of a right axis.
KIND_TITLES: dict[Kind, str] = {
    Kind.PERCENT: "Percent",
    Kind.TEMP: "Temperature",
    Kind.FAN: "Fan",
    Kind.POWER: "Power",
    Kind.RATE: "Throughput",
    Kind.VOLTAGE: "Voltage",
    Kind.FREQUENCY: "Clock",
}

UNITS: dict[Kind, str] = {
    Kind.PERCENT: "%",
    Kind.TEMP: "°C",
    Kind.FAN: "RPM",
    Kind.POWER: "W",
    Kind.RATE: "B/s",
    Kind.VOLTAGE: "V",
    Kind.FREQUENCY: "MHz",
}


@dataclass(frozen=True)
class SeriesInfo:
    """One series that a source can sample.

    ``hidden`` series are not matched by globs in the config, only by their exact ID: duplicates
    of another sensor, raw voltages, virtual network interfaces. ``group`` is what the default
    layout uses to place the series, for example ``"cpu-temp"`` or ``"fan"``.
    """

    id: str
    label: str
    kind: Kind
    group: str
    hidden: bool = False
    # Why the series is hidden, shown by ``system-status sensors``.
    hidden_reason: str | None = None
    # Rows of a heatmap are grouped by this, e.g. the CCD of a physical core.
    cluster: str | None = None


_SLUG_INVALID = re.compile(r"[^A-Za-z0-9._+-]+")


def slug(text: str) -> str:
    """Turn a label or model name into an ID part: runs of other characters become ``_``.

    ``"WD Red SN700 2000GB  "`` becomes ``"WD_Red_SN700_2000GB"`` and ``"Diode 0 (curr)"``
    becomes ``"Diode_0_curr"``. Case is kept.
    """
    return _SLUG_INVALID.sub("_", text.strip()).strip("_") or "unnamed"


def _format_bytes(value: float) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    index = 0
    while abs(value) >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    if index == 0:
        return f"{value:.0f} {units[0]}"
    return f"{value:.1f} {units[index]}" if abs(value) < 100 else f"{value:.0f} {units[index]}"


def format_bytes(value: float) -> str:
    """Return ``value`` bytes in binary units, for example ``"12.3 GiB"``."""
    return _format_bytes(value)


def format_value(kind: Kind, value: float | None) -> str:
    """Return ``value`` with its unit, as the legend and the tooltip show it; ``"–"`` for None."""
    if value is None or math.isnan(value):
        return "–"
    if kind == Kind.PERCENT:
        return f"{value:.1f} %" if value < 10 else f"{value:.0f} %"
    if kind == Kind.TEMP:
        return f"{value:.1f} °C"
    if kind == Kind.FAN:
        return f"{value:.0f} RPM"
    if kind == Kind.POWER:
        return f"{value:.1f} W"
    if kind == Kind.RATE:
        return f"{_format_bytes(value)}/s"
    if kind == Kind.FREQUENCY:
        return f"{value / 1000:.2f} GHz"
    return f"{value:.2f} V"


def format_axis(kind: Kind, value: float, top: float | None = None) -> str:
    """Return a y-axis tick label: short, without decimals where the ticks don't need them.

    Byte rates use the unit of ``top``, the highest tick, for every tick of the axis.
    """
    if kind == Kind.PERCENT:
        return f"{value:.0f} %"
    if kind == Kind.TEMP:
        return f"{value:.0f} °C"
    if kind == Kind.FAN:
        return f"{value:.0f} RPM"
    if kind == Kind.POWER:
        return f"{value:g} W"
    if kind == Kind.RATE:
        units = ("B", "KiB", "MiB", "GiB", "TiB")
        index = 0
        scale = top if top is not None else value
        while scale >= 1024 and index < len(units) - 1:
            scale /= 1024
            index += 1
        return f"{round(value / 1024**index, 2):g} {units[index]}/s"
    if kind == Kind.FREQUENCY:
        return f"{round(value / 1000, 2):g} GHz"
    return f"{value:g} V"
