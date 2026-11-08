"""From config to what the window draws: the default layout, and series patterns to series."""

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase

from system_status.core.config import (
    HIDE_ZERO_DEVICE,
    UNSTACKABLE,
    Config,
    GraphConfig,
    SectionConfig,
    SensorOverride,
)
from system_status.core.series import KIND_TITLES, Kind, SeriesInfo
from system_status.core.snapshot import Catalog

_GLOB_CHARACTERS = set("*?[")


@dataclass(frozen=True)
class ResolvedSeries:
    info: SeriesInfo
    label: str
    color: str | None


@dataclass(frozen=True)
class ResolvedGraph:
    config: GraphConfig
    # None when nothing matched: the graph has no unit to show.
    kind: Kind | None
    series: tuple[ResolvedSeries, ...]
    # Why the graph can't be drawn as configured, shown in the window's error banner.
    error: str | None = None
    # The second y-axis on the right: its kind and series. None and empty without one.
    right_kind: Kind | None = None
    right_series: tuple[ResolvedSeries, ...] = ()


def is_glob(pattern: str) -> bool:
    return bool(_GLOB_CHARACTERS & set(pattern))


def resolve_graph(
    graph: GraphConfig, catalog: Catalog, sensors: Mapping[str, SensorOverride]
) -> ResolvedGraph:
    """Match the graph's patterns against the catalog, in pattern order, each series once.

    An exact ID matches a hidden-by-default series too; a glob doesn't, unless the config un-hides
    it with ``hidden = false``. ``hidden = true`` in the config removes a series everywhere. Series
    of another kind than the graph's are dropped, so ``hwmon:nct6687/*`` in a temperature graph
    keeps only the temperatures.
    """
    title = graph.title or "untitled"
    matched = _match(graph.series, catalog, sensors)
    kind = graph.kind or (matched[0].kind if matched else None)
    resolved, dropped = _of_kind(matched, kind, sensors)
    error = None
    if graph.stacked and dropped and graph.kind is None:
        # A sum of mixed units is meaningless; ask for an explicit kind rather than guess.
        kinds = sorted({kind.value} | {info.kind.value for info in dropped})
        error = (
            f'stacked graph "{title}" matches series of several kinds ({", ".join(kinds)});'
            " set its kind or narrow its series"
        )
        resolved = []
    elif graph.stacked and kind in UNSTACKABLE:
        error = f'graph "{title}": {kind.value} values can\'t be stacked'
        resolved = []
    if not graph.right:
        return ResolvedGraph(graph, kind, tuple(resolved), error)

    right_matched = _match(graph.right, catalog, sensors)
    right_kind = graph.right_kind or (right_matched[0].kind if right_matched else None)
    right, right_dropped = _of_kind(right_matched, right_kind, sensors)
    both = {s.info.id for s in resolved} & {s.info.id for s in right}
    if both:
        error = error or (
            f'graph "{title}": {sorted(both)[0]} matches both series and right; pick one axis'
        )
        right = []
    elif right_dropped and graph.right_kind is None:
        # A graph has two axes, so two kinds at most: one left, one right.
        kinds = sorted({right_kind.value} | {info.kind.value for info in right_dropped})
        error = error or (
            f'graph "{title}": right matches series of several kinds ({", ".join(kinds)});'
            " set right_kind or narrow right"
        )
        right = []
    return ResolvedGraph(graph, kind, tuple(resolved), error, right_kind, tuple(right))


# A right series' own label becomes the right title only when it is this short; a long one,
# like a fan named after its cooler, gives way to the kind's name.
MAX_RIGHT_TITLE_LABEL = 20


def right_title(graph: ResolvedGraph) -> str | None:
    """Return the title shown above the right axis, or None for a graph without one.

    ``right_title`` from the config wins. Otherwise a single right series with a short label
    gives its label ("Power", "Fan"), and anything else the kind's name ("Fan", "Clock").
    """
    if not graph.config.right:
        return None
    if graph.config.right_title is not None:
        return graph.config.right_title
    series = graph.right_series
    if len(series) == 1 and len(series[0].label) <= MAX_RIGHT_TITLE_LABEL:
        return series[0].label
    return KIND_TITLES[graph.right_kind] if graph.right_kind is not None else None


def zero_device(series_id: str) -> str:
    """Return the device a series belongs to for ``hide_zero = "device"``: its ID up to the
    last ``/``, such as ``disk:WD_Red_SN700_2000GB``; an ID without ``/`` is its own device."""
    device, slash, _metric = series_id.rpartition("/")
    return device if slash else series_id


def zero_hidden(values: Mapping[str, Sequence[float]], mode: bool | str) -> set[str]:
    """Return the IDs that ``hide_zero`` leaves out, given each series' visible values.

    A series is zero when every visible value is 0 or missing. With ``True`` each zero series
    is left out on its own; with ``"device"`` a device's series are left out together, and only
    when all of them are zero.
    """

    def zero(series: Sequence[float]) -> bool:
        return all(math.isnan(v) or v == 0 for v in series)

    if not mode:
        return set()
    if mode != HIDE_ZERO_DEVICE:
        return {series_id for series_id, series in values.items() if zero(series)}
    groups: dict[str, list[str]] = {}
    for series_id in values:
        groups.setdefault(zero_device(series_id), []).append(series_id)
    return {
        series_id
        for members in groups.values()
        if all(zero(values[member]) for member in members)
        for series_id in members
    }


def _match(
    patterns: Sequence[str], catalog: Catalog, sensors: Mapping[str, SensorOverride]
) -> list[SeriesInfo]:
    matched: list[SeriesInfo] = []
    seen: set[str] = set()
    by_id = catalog.by_id()
    for pattern in patterns:
        if is_glob(pattern):
            candidates = [
                info
                for info in catalog.series
                if fnmatchcase(info.id, pattern) and not _hidden(info, sensors, by_glob=True)
            ]
        else:
            info = by_id.get(pattern)
            candidates = [info] if info is not None and not _hidden(info, sensors) else []
        for info in candidates:
            if info.id not in seen:
                seen.add(info.id)
                matched.append(info)
    return matched


def _of_kind(
    matched: list[SeriesInfo], kind: Kind | None, sensors: Mapping[str, SensorOverride]
) -> tuple[list[ResolvedSeries], list[SeriesInfo]]:
    """Split ``matched`` into the series of ``kind``, resolved, and the dropped others."""
    resolved = []
    dropped = []
    for info in matched:
        if info.kind != kind:
            dropped.append(info)
            continue
        override = sensors.get(info.id, SensorOverride())
        resolved.append(ResolvedSeries(info, override.label or info.label, override.color))
    return resolved, dropped


def _hidden(info: SeriesInfo, sensors: Mapping[str, SensorOverride], by_glob=False) -> bool:
    override = sensors.get(info.id)
    if override is not None and override.hidden is not None:
        return override.hidden
    return info.hidden and by_glob


def unmatched_patterns(config: Config, catalog: Catalog) -> list[str]:
    """Return the patterns of the config's graphs that match no series of this machine."""
    missing = []
    for section in config.sections or ():
        for graph in section.graphs:
            for pattern in graph.series + graph.right:
                ids = [info.id for info in catalog.series]
                if not any(fnmatchcase(series_id, pattern) for series_id in ids):
                    missing.append(pattern)
    return missing


def _ids(series: Sequence[SeriesInfo], group: str, kind: Kind | None = None) -> tuple[str, ...]:
    return tuple(
        info.id
        for info in series
        if info.group == group and not info.hidden and (kind is None or info.kind == kind)
    )


def _disk_throughput(series: Sequence[SeriesInfo]) -> tuple[str, ...]:
    """Read and write of each disk, next to each other, so the pair shares a colour."""
    reads = _ids(series, "disk-read")
    writes = set(_ids(series, "disk-write"))
    pairs = []
    for read in reads:
        pairs.append(read)
        write = read.removesuffix("/read") + "/write"
        if write in writes:
            pairs.append(write)
    return tuple(pairs)


def default_sections(catalog: Catalog) -> tuple[SectionConfig, ...]:
    """Lay out every useful series of this machine, the way ``dump-config`` writes it."""
    series = catalog.series
    sections: list[SectionConfig] = []

    def section(title: str, *graphs: GraphConfig | None) -> None:
        kept = tuple(g for g in graphs if g is not None and g.series)
        if kept:
            sections.append(SectionConfig(title=title, graphs=kept))

    usage = _ids(series, "cpu") + _ids(series, "cpu-ccd")
    section(
        "CPU",
        GraphConfig("Usage", usage, legend=len(usage) > 1),
        GraphConfig("Cores", _ids(series, "cpu-pcore"), type="heatmap"),
        GraphConfig("Temperature", _ids(series, "cpu-temp")),
        GraphConfig("Clock", _ids(series, "cpu-clock")),
    )
    section(
        "Memory",
        GraphConfig("Usage", _ids(series, "mem")),
        GraphConfig("Temperature", _ids(series, "mem-temp")),
    )
    gpu_groups = sorted(
        {info.group for info in series if info.group.startswith("gpu:")},
        key=lambda g: (not g.startswith("gpu:nvidia"), g),
    )
    for group in gpu_groups:
        key = group.split(":", 1)[1]
        by_metric = {info.id.split("/", 1)[1]: info.id for info in series if info.group == group}
        usage = tuple(by_metric[m] for m in ("util", "mem_util", "vram") if m in by_metric)
        power = _ids(series, group, Kind.POWER)
        temperature = _ids(series, group, Kind.TEMP)
        fan = tuple(i for i in (by_metric.get("fan"),) if i)
        name = catalog.gpu_names.get(group, key)
        section(
            f"GPU · {name}",
            # Power next to usage, fan next to temperature, each on a right axis; a graph of
            # its own when its partner is missing.
            GraphConfig("Usage", usage, right=power) if usage else GraphConfig("Power", power),
            (
                GraphConfig("Temperature", temperature, right=fan)
                if temperature
                else GraphConfig("Fan", fan, legend=False)
            ),
        )
    section(
        "Disks",
        GraphConfig("Temperature", _ids(series, "disk-temp")),
        GraphConfig(
            "Throughput",
            _disk_throughput(series),
            # An active disk shows both read and write; an idle one neither.
            hide_zero=HIDE_ZERO_DEVICE,
            stacked=True,
        ),
    )
    section("Fans", GraphConfig("Speed", _ids(series, "fan"), hide_zero=True))
    section("Network", GraphConfig("Throughput", _ids(series, "net")))
    section(
        "Motherboard",
        GraphConfig("Temperature", _ids(series, "board-temp")),
        GraphConfig("Power", _ids(series, "board-power")),
    )
    return tuple(sections)


def effective_sections(config: Config, catalog: Catalog) -> tuple[SectionConfig, ...]:
    return config.sections if config.sections is not None else default_sections(catalog)


_TCCD = re.compile(r"hwmon:k10temp[^/]*/Tccd(\d+)")
_CCD = re.compile(r"cpu:ccd(\d+)/.+")


def device_key(series_id: str) -> str | None:
    """Return the physical device a series belongs to, for colouring, or None.

    Disks: every ``disk:<model>/...`` series is one drive. CCDs: ``cpu:ccd0/...`` and
    k10temp's ``Tccd1`` are the same chiplet, ``ccd0``. Other prefixes, such as
    ``hwmon:nct6687``, cover many unrelated sensors, so they are coloured by position.
    """
    prefix = series_id.split("/", 1)[0]
    if prefix.startswith("disk:") and "/" in series_id:
        return prefix
    if match := _CCD.fullmatch(series_id):
        return f"ccd{match.group(1)}"
    if match := _TCCD.fullmatch(series_id):
        return f"ccd{int(match.group(1)) - 1}"
    return None


def _palette_index(key: str, disks: list[str]) -> int:
    if key.startswith("ccd"):
        # Palette colour 0 stays free for the total or the package next to the CCDs.
        return int(key[3:]) + 1
    return disks.index(key)


def assign_colors(series_ids: Sequence[str], catalog: Catalog) -> list[tuple[int, int]]:
    """Return ``(palette index, shade)`` for each series of a graph.

    A disk gets the palette colour of its position among all disks of the catalog, and CCD N
    gets colour N + 1, in every graph, so a disk's temperature and throughput, or a CCD's
    usage, temperature and clock, look alike. A device's second series in the same graph
    (write after read, Sensor 1 after Composite) gets shade 1, a lighter variant, and so on.
    Every other series gets the next free palette colour by position, shade 0.
    """
    disks: list[str] = []
    for info in catalog.series:
        key = device_key(info.id)
        if key is not None and key.startswith("disk:") and key not in disks:
            disks.append(key)
    keys = [device_key(series_id) for series_id in series_ids]
    known = [key is not None and (key.startswith("ccd") or key in disks) for key in keys]
    # Positional colours skip the ones the graph's devices already use.
    taken = {_palette_index(k, disks) for k, ok in zip(keys, known, strict=True) if ok and k}
    shades: dict[str, int] = {}
    position = 0
    colors = []
    for key, ok in zip(keys, known, strict=True):
        if ok and key is not None:
            shade = shades.get(key, 0)
            shades[key] = shade + 1
            colors.append((_palette_index(key, disks), shade))
        else:
            while position in taken:
                position += 1
            colors.append((position, 0))
            position += 1
    return colors
