"""Immutable view of the sampled history, built for one frame of the GUI."""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

from system_status.core.series import SeriesInfo


@dataclass(frozen=True)
class Catalog:
    """Every series the sources found, in discovery order, and the names of GPUs."""

    series: tuple[SeriesInfo, ...]
    # "gpu:amd0" -> "AMD Radeon Graphics", used for section titles of the default layout.
    gpu_names: Mapping[str, str] = field(default_factory=dict)

    def by_id(self) -> dict[str, SeriesInfo]:
        return {info.id: info for info in self.series}


@dataclass(frozen=True)
class Snapshot:
    """The last ``history_seconds`` of every series.

    ``values[id][i]`` belongs to ``timestamps[i]`` (wall-clock seconds); NaN marks a tick where
    the series had no value. Read-only by convention: the GUI never writes into a snapshot.
    """

    catalog: Catalog
    interval_seconds: float
    history_seconds: float
    timestamps: tuple[float, ...]
    values: Mapping[str, tuple[float, ...]]
    details: Mapping[str, str]

    def latest(self, series_id: str) -> float | None:
        values = self.values.get(series_id)
        if not values or math.isnan(values[-1]):
            return None
        return values[-1]

    @property
    def latest_time(self) -> float | None:
        return self.timestamps[-1] if self.timestamps else None
