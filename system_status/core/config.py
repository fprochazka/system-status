"""The settings file: sensor labels and colours, and the sections and graphs of the window.

The file is TOML at ``$XDG_CONFIG_HOME/system-status/settings.toml``. Every key is checked:
an unknown key or a value of the wrong type is an error that names its place in the file, so a
typo never silently does nothing.
"""

import json
import math
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from system_status.core.series import Kind

APP_NAME = "system-status"
THEMES = ("auto", "light", "dark")
_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
# Kinds whose values mean nothing when added up, so a stacked graph can't show them.
UNSTACKABLE = {Kind.TEMP, Kind.VOLTAGE, Kind.FREQUENCY}
GRAPH_TYPES = ("line", "heatmap")


class ConfigError(ValueError):
    """The settings file can't be used; the message says where and why."""


@dataclass(frozen=True)
class SensorOverride:
    label: str | None = None
    color: str | None = None
    # True hides the series everywhere, even where the config names its exact ID. False shows a
    # series that is hidden by default (a duplicate, a raw voltage) in glob matches too.
    hidden: bool | None = None


@dataclass(frozen=True)
class GraphConfig:
    title: str
    series: tuple[str, ...]
    legend: bool = True
    # Leave out series that were 0 for the whole visible history, such as unplugged fan headers.
    hide_zero: bool = False
    # Draw the series as filled areas on top of each other; the top edge is their total.
    stacked: bool = False
    # Keep only series of this kind; by default the kind of the first matched series.
    kind: Kind | None = None
    min: float | None = None
    max: float | None = None
    # Height of the plot in pixels. A heatmap sizes itself by ``row_height`` instead.
    height: int = 90
    # "line" draws a line (or a stacked area) per series; "heatmap" a row per series, coloured
    # by value, grouped by the series' cluster (the CCD of a core).
    type: str = "line"
    # Height of one heatmap row in pixels.
    row_height: int = 7
    # Series drawn against a second y-axis on the right edge, with their own kind and range.
    right: tuple[str, ...] = ()
    right_kind: Kind | None = None
    right_min: float | None = None
    right_max: float | None = None
    # Shown above the right axis; by default derived from the right series, see layout.py.
    right_title: str | None = None


@dataclass(frozen=True)
class SectionConfig:
    title: str
    graphs: tuple[GraphConfig, ...]
    collapsed: bool = False


@dataclass(frozen=True)
class Config:
    history_seconds: int = 300
    interval_seconds: float = 1.0
    theme: str = "auto"
    sensors: Mapping[str, SensorOverride] = field(default_factory=dict)
    # None means "no [[section]] in the file": the window uses the generated default layout.
    sections: tuple[SectionConfig, ...] | None = None


def get_default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_NAME / "settings.toml"


# --- Parsing -------------------------------------------------------------------------------------


def _check_keys(table: Mapping[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        names = ", ".join(f'"{key}"' for key in unknown)
        raise ConfigError(f"{where}: unknown key {names}; allowed: {', '.join(sorted(allowed))}")


def _get(table: Mapping[str, Any], key: str, types: type | tuple[type, ...], where: str) -> Any:
    value = table[key]
    # bool is an int in Python; a number key must not accept true/false.
    if isinstance(value, bool) and bool not in (types if isinstance(types, tuple) else (types,)):
        raise ConfigError(f"{where}.{key}: expected a number, got {value!r}")
    if not isinstance(value, types):
        expected = (
            types.__name__ if isinstance(types, type) else " or ".join(t.__name__ for t in types)
        )
        raise ConfigError(f"{where}.{key}: expected {expected}, got {value!r}")
    return value


def _parse_sensor(series_id: str, raw: Any) -> SensorOverride:
    where = f'sensors."{series_id}"'
    if not isinstance(raw, dict):
        raise ConfigError(f'{where}: expected a table like {{ label = "..." }}')
    _check_keys(raw, {"label", "color", "hidden"}, where)
    label = _get(raw, "label", str, where) if "label" in raw else None
    color = _get(raw, "color", str, where) if "color" in raw else None
    if color is not None and not _COLOR.fullmatch(color):
        raise ConfigError(f'{where}.color: expected "#rrggbb", got {color!r}')
    hidden = _get(raw, "hidden", bool, where) if "hidden" in raw else None
    return SensorOverride(label=label, color=color, hidden=hidden)


def _parse_graph(raw: Any, where: str) -> GraphConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: expected a table")
    allowed = {
        "title",
        "series",
        "legend",
        "hide_zero",
        "stacked",
        "kind",
        "min",
        "max",
        "height",
        "type",
        "row_height",
        "right",
        "right_kind",
        "right_min",
        "right_max",
        "right_title",
    }
    _check_keys(raw, allowed, where)
    if "series" not in raw:
        raise ConfigError(f'{where}: missing key "series"')
    series = _get(raw, "series", list, where)
    if not all(isinstance(item, str) for item in series):
        raise ConfigError(f"{where}.series: expected a list of series IDs (strings)")
    kind = _parse_kind(raw, "kind", where)
    right_kind = _parse_kind(raw, "right_kind", where)
    right = _get(raw, "right", list, where) if "right" in raw else []
    if not all(isinstance(item, str) for item in right):
        raise ConfigError(f"{where}.right: expected a list of series IDs (strings)")
    bounds = {}
    for key in ("min", "max", "right_min", "right_max"):
        if key in raw:
            value = float(_get(raw, key, (int, float), where))
            if not math.isfinite(value):
                raise ConfigError(f"{where}.{key}: must be a finite number")
            bounds[key] = value
    for low, high in (("min", "max"), ("right_min", "right_max")):
        if low in bounds and high in bounds and bounds[low] >= bounds[high]:
            raise ConfigError(f"{where}: {low} must be lower than {high}")
    if not right:
        for key in ("right_kind", "right_min", "right_max", "right_title"):
            if key in raw:
                raise ConfigError(f"{where}.{key}: only with a right axis (right = [...])")
    overlap = sorted(set(series) & set(right))
    if overlap:
        raise ConfigError(f"{where}.right: {overlap[0]!r} is also in series; pick one axis")
    stacked = _get(raw, "stacked", bool, where) if "stacked" in raw else False
    if stacked and kind in UNSTACKABLE:
        raise ConfigError(f"{where}.stacked: {kind.value} values can't be added up")
    height = _get(raw, "height", int, where) if "height" in raw else GraphConfig.height
    if not 30 <= height <= 1000:
        raise ConfigError(f"{where}.height: must be between 30 and 1000 pixels")
    graph_type = _get(raw, "type", str, where) if "type" in raw else "line"
    if graph_type not in GRAPH_TYPES:
        raise ConfigError(
            f"{where}.type: expected one of {', '.join(GRAPH_TYPES)}, got {graph_type!r}"
        )
    if graph_type == "heatmap" and stacked:
        raise ConfigError(f"{where}: a heatmap can't be stacked")
    if graph_type == "heatmap" and right:
        raise ConfigError(f"{where}: a heatmap has no right axis")
    if "row_height" in raw and graph_type != "heatmap":
        raise ConfigError(f'{where}.row_height: only for type = "heatmap"')
    row_height = _get(raw, "row_height", int, where) if "row_height" in raw else 7
    if not 2 <= row_height <= 40:
        raise ConfigError(f"{where}.row_height: must be between 2 and 40 pixels")
    return GraphConfig(
        title=_get(raw, "title", str, where) if "title" in raw else "",
        series=tuple(series),
        legend=_get(raw, "legend", bool, where) if "legend" in raw else True,
        hide_zero=_get(raw, "hide_zero", bool, where) if "hide_zero" in raw else False,
        stacked=stacked,
        kind=kind,
        min=bounds.get("min"),
        max=bounds.get("max"),
        height=height,
        type=graph_type,
        row_height=row_height,
        right=tuple(right),
        right_kind=right_kind,
        right_min=bounds.get("right_min"),
        right_max=bounds.get("right_max"),
        right_title=_get(raw, "right_title", str, where) if "right_title" in raw else None,
    )


def _parse_kind(raw: Mapping[str, Any], key: str, where: str) -> Kind | None:
    if key not in raw:
        return None
    name = _get(raw, key, str, where)
    try:
        return Kind(name)
    except ValueError:
        kinds = ", ".join(k.value for k in Kind)
        raise ConfigError(f"{where}.{key}: unknown kind {name!r}; one of {kinds}") from None


def _parse_section(raw: Any, where: str) -> SectionConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: expected a table")
    _check_keys(raw, {"title", "collapsed", "graph"}, where)
    if "title" not in raw:
        raise ConfigError(f'{where}: missing key "title"')
    graphs_raw = _get(raw, "graph", list, where) if "graph" in raw else []
    graphs = tuple(_parse_graph(g, f"{where}.graph[{i}]") for i, g in enumerate(graphs_raw))
    return SectionConfig(
        title=_get(raw, "title", str, where),
        graphs=graphs,
        collapsed=_get(raw, "collapsed", bool, where) if "collapsed" in raw else False,
    )


def parse_config(data: Mapping[str, Any]) -> Config:
    """Build a ``Config`` from parsed TOML, or raise ``ConfigError``."""
    allowed = {"history_seconds", "interval_seconds", "theme", "sensors", "section"}
    _check_keys(data, allowed, "settings")
    history = data.get("history_seconds", Config.history_seconds)
    if isinstance(history, bool) or not isinstance(history, int) or not 10 <= history <= 86400:
        raise ConfigError("history_seconds: expected a whole number between 10 and 86400")
    interval = data.get("interval_seconds", Config.interval_seconds)
    if isinstance(interval, bool) or not isinstance(interval, int | float):
        raise ConfigError(f"interval_seconds: expected a number, got {interval!r}")
    if not 0.1 <= interval <= 60:
        raise ConfigError("interval_seconds: must be between 0.1 and 60")
    if history / interval > 100_000:
        raise ConfigError("history_seconds / interval_seconds: more than 100000 samples")
    theme = data.get("theme", Config.theme)
    if theme not in THEMES:
        raise ConfigError(f"theme: expected one of {', '.join(THEMES)}, got {theme!r}")
    sensors_raw = data.get("sensors", {})
    if not isinstance(sensors_raw, dict):
        raise ConfigError("sensors: expected a table of series IDs")
    sensors = {key: _parse_sensor(key, value) for key, value in sensors_raw.items()}
    sections = None
    if "section" in data:
        sections_raw = data["section"]
        if not isinstance(sections_raw, list):
            raise ConfigError("section: expected [[section]] tables")
        sections = tuple(_parse_section(s, f"section[{i}]") for i, s in enumerate(sections_raw))
    return Config(
        history_seconds=history,
        interval_seconds=float(interval),
        theme=theme,
        sensors=sensors,
        sections=sections,
    )


def load_config(path: Path) -> Config:
    """Load ``path``; a missing file gives the defaults. Raise ``ConfigError`` on any problem."""
    try:
        text = path.read_text()
    except FileNotFoundError:
        return Config()
    except OSError as error:
        raise ConfigError(f"{path}: {error.strerror}") from error
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path}: invalid TOML: {error}") from error
    try:
        return parse_config(data)
    except ConfigError as error:
        raise ConfigError(f"{path}: {error}") from error


# --- Writing -------------------------------------------------------------------------------------


def _string(value: str) -> str:
    # A JSON string is a valid TOML basic string for everything a label contains.
    return json.dumps(value, ensure_ascii=False)


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))


DUMP_HEADER = """\
# system-status settings. The window reloads this file when it is saved.
#
# Series IDs: run `system-status sensors` to list every series of this machine with its ID,
# default label and current value. In `series`, globs work ("hwmon:k10temp/*", "cpu:core*");
# a glob skips hidden series (duplicates, raw voltages, virtual network interfaces).
#
# [sensors] overrides a series anywhere it is shown:
#   "hwmon:k10temp/Tctl" = { label = "CPU package", color = "#e01b24" }
#   "hwmon:nct6687/fan11" = { hidden = true }
#
# Graph keys: title, series, legend (true/false), hide_zero, stacked (areas on top of each
# other, the top edge is the total; one kind only, not temp, voltage or frequency), type ("line"
# or "heatmap": a row per series coloured by value, e.g. "cpu:pcore*"), row_height (heatmap
# row, pixels), kind (percent, temp, fan, power, rate, voltage, frequency), min, max, height
# (pixels), right (series on a second y-axis on the right, drawn dashed: e.g. temp on the left,
# fan on the right), right_kind, right_min, right_max, right_title (shown above the right axis;
# default: the right series' label when there is one short one, else its kind, like "Power").
# Section keys: title, collapsed, graph.
# Delete every [[section]] to go back to the generated default layout.
"""


def dump_config(config: Config) -> str:
    """Return ``config`` as TOML text, with a header that explains the format."""
    lines = [DUMP_HEADER]
    lines.append(f"history_seconds = {config.history_seconds}")
    lines.append(f"interval_seconds = {_number(config.interval_seconds)}")
    lines.append(f"theme = {_string(config.theme)}")
    lines.append("")
    lines.append("[sensors]")
    for series_id, override in config.sensors.items():
        parts = []
        if override.label is not None:
            parts.append(f"label = {_string(override.label)}")
        if override.color is not None:
            parts.append(f"color = {_string(override.color)}")
        if override.hidden is not None:
            parts.append(f"hidden = {str(override.hidden).lower()}")
        lines.append(f"{_string(series_id)} = {{ {', '.join(parts)} }}")
    for section in config.sections or ():
        lines.append("")
        lines.append("[[section]]")
        lines.append(f"title = {_string(section.title)}")
        if section.collapsed:
            lines.append("collapsed = true")
        for graph in section.graphs:
            lines.append("")
            lines.append("  [[section.graph]]")
            lines.append(f"  title = {_string(graph.title)}")
            if len(graph.series) <= 2:
                series = ", ".join(_string(s) for s in graph.series)
                lines.append(f"  series = [{series}]")
            else:
                lines.append("  series = [")
                lines.extend(f"    {_string(s)}," for s in graph.series)
                lines.append("  ]")
            if not graph.legend:
                lines.append("  legend = false")
            if graph.hide_zero:
                lines.append("  hide_zero = true")
            if graph.stacked:
                lines.append("  stacked = true")
            if graph.type != "line":
                lines.append(f"  type = {_string(graph.type)}")
            if graph.type == "heatmap" and graph.row_height != GraphConfig.row_height:
                lines.append(f"  row_height = {graph.row_height}")
            if graph.kind is not None:
                lines.append(f"  kind = {_string(graph.kind.value)}")
            if graph.min is not None:
                lines.append(f"  min = {_number(graph.min)}")
            if graph.max is not None:
                lines.append(f"  max = {_number(graph.max)}")
            if graph.right:
                lines.append(f"  right = [{', '.join(_string(r) for r in graph.right)}]")
            if graph.right_kind is not None:
                lines.append(f"  right_kind = {_string(graph.right_kind.value)}")
            if graph.right_min is not None:
                lines.append(f"  right_min = {_number(graph.right_min)}")
            if graph.right_max is not None:
                lines.append(f"  right_max = {_number(graph.right_max)}")
            if graph.right_title is not None:
                lines.append(f"  right_title = {_string(graph.right_title)}")
            if graph.height != GraphConfig.height:
                lines.append(f"  height = {graph.height}")
    return "\n".join(lines) + "\n"
