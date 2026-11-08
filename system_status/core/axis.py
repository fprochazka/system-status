"""The y-axis of a graph: its range and gridlines, chosen from the visible values."""

import math
from collections.abc import Iterable, Sequence

from system_status.core.series import Kind

# The smallest range a graph shows, so an idle disk or a steady temperature isn't magnified
# into noise that fills the whole graph.
MIN_SPAN = {
    Kind.TEMP: 10.0,
    Kind.FAN: 500.0,
    Kind.POWER: 5.0,
    Kind.RATE: 64 * 1024.0,
    Kind.VOLTAGE: 0.5,
    Kind.PERCENT: 10.0,
    Kind.FREQUENCY: 1000.0,
}
# Kinds that can't go below zero, so their axis starts at zero.
ZERO_BASED = {Kind.PERCENT, Kind.FAN, Kind.POWER, Kind.RATE, Kind.FREQUENCY}
TARGET_INTERVALS = 4


def nice_step(raw: float, binary: bool = False) -> float:
    """Return a round step near ``raw``: 1, 2 or 5 times a power of ten.

    With ``binary``, a power of two of a power of 1024, so byte rates get ticks like 256 KiB.
    """
    if raw <= 0 or not math.isfinite(raw):
        return 1.0
    if binary:
        base = 1.0
        while raw >= 1024 * base:
            base *= 1024
        # Powers of two within the unit: 256 KiB, 512 KiB, 1 MiB, 2 MiB.
        return base * 2 ** math.ceil(math.log2(raw / base) - 1e-9)
    magnitude = 10 ** math.floor(math.log10(raw))
    for factor in (1, 2, 5, 10):
        if factor * magnitude >= raw * 0.999:
            return factor * magnitude
    return 10 * magnitude


def axis_range(
    kind: Kind | None,
    values: Iterable[float],
    fixed_min: float | None = None,
    fixed_max: float | None = None,
) -> tuple[float, float, list[float]]:
    """Return ``(low, high, ticks)`` for the given visible values; NaN values are ignored."""
    finite = [v for v in values if math.isfinite(v)]
    if kind == Kind.PERCENT and fixed_min is None and fixed_max is None:
        return 0.0, 100.0, [0.0, 25.0, 50.0, 75.0, 100.0]
    kind = kind or Kind.PERCENT
    data_low = min(finite) if finite else 0.0
    data_high = max(finite) if finite else 0.0
    low = fixed_min if fixed_min is not None else (0.0 if kind in ZERO_BASED else data_low)
    high = fixed_max if fixed_max is not None else data_high
    span = MIN_SPAN[kind]
    if high - low < span:
        if fixed_max is None:
            high = low + span if fixed_min is not None or kind in ZERO_BASED else None
        if high is None:
            # Center a short range on the data, like a temperature that hardly moves.
            middle = (data_low + data_high) / 2
            low, high = middle - span / 2, middle + span / 2
        elif fixed_min is None and kind not in ZERO_BASED:
            low = high - span
    step = nice_step((high - low) / TARGET_INTERVALS, binary=kind == Kind.RATE)
    if fixed_min is None:
        low = math.floor(low / step + 1e-9) * step
    if fixed_max is None:
        high = math.ceil(high / step - 1e-9) * step
    if high <= low:
        high = low + step
    ticks = []
    tick = math.ceil(low / step - 1e-9) * step
    while tick <= high + step * 1e-6:
        ticks.append(round(tick, 9))
        tick += step
    return low, high, ticks


def stack_values(series: Sequence[Sequence[float]]) -> list[tuple[float, ...]]:
    """Return the running totals of ``series``, all of one length: layer k is series 0..k added.

    A missing value (NaN) counts as 0 while another series has a value at that tick, so one
    disk without a reading doesn't cut a hole into the stack. A tick where every series is NaN
    stays NaN in every layer: a gap.
    """
    if not series:
        return []
    length = min(len(values) for values in series)
    empty = [all(math.isnan(values[i]) for values in series) for i in range(length)]
    layers = []
    running = [0.0] * length
    for values in series:
        running = [
            math.nan if empty[i] else running[i] + (0.0 if math.isnan(values[i]) else values[i])
            for i in range(length)
        ]
        layers.append(tuple(running))
    return layers


def aligned_range(
    kind: Kind | None,
    values: Iterable[float],
    intervals: int,
    fixed_min: float | None = None,
    fixed_max: float | None = None,
) -> tuple[float, float, list[float]]:
    """Return ``(low, high, ticks)`` with exactly ``intervals`` steps, for a right-hand axis.

    The right axis has no gridlines of its own: its ticks sit on the left axis's gridlines, so
    its range is stretched to the same number of round steps that cover its values.
    """
    intervals = max(1, intervals)
    low, high, _ticks = axis_range(kind, values, fixed_min, fixed_max)
    # Percent keeps 0-100 % and splits it evenly, even into thirds: a fan at "150 %" would lie.
    fixed_percent = kind == Kind.PERCENT and fixed_min is None and fixed_max is None
    if not fixed_percent and (fixed_min is None or fixed_max is None):
        binary = kind == Kind.RATE
        step = nice_step((high - low) / intervals, binary=binary)
        if fixed_min is None:
            low = math.floor(low / step + 1e-9) * step
        top = high if fixed_max is None else fixed_max
        if fixed_max is not None:
            # Only the bottom is free: the steps follow from the fixed top.
            step = (top - low) / intervals
        else:
            while low + step * intervals < top - 1e-9:
                step = nice_step(step * 1.01, binary=binary)
        high = low + step * intervals
    step = (high - low) / intervals
    return low, high, [round(low + step * i, 9) for i in range(intervals + 1)]
