"""Temperatures, fans, power and voltages from ``/sys/class/hwmon``.

A chip is identified by its ``name`` and the device it belongs to, never by its ``hwmonN``
number, which changes between boots. Each chip gets an ID prefix by what it measures:

- ``disk:<model>`` for ``nvme`` and ``drivetemp`` (the same prefix as the disk's I/O series),
- ``dimm:<i2c address>`` for DDR5/DDR4 module sensors (``spd5118``, ``jc42``),
- ``gpu:amd<N>`` for ``amdgpu``,
- ``net:<interface>`` for a chip on a network card,
- ``hwmon:<name>`` for everything else, such as ``k10temp``, ``coretemp`` or a Super-IO chip.

Within a chip, a channel is named by its label (``Tctl``, ``Composite``), or by its channel name
(``temp3``, ``fan11``) when it has no label or when an earlier channel already took the label.
A chip's only unlabeled channel of a kind is named after the kind: ``disk:ST4000VN008-2DR1/temp``.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from system_status.core.series import Kind, SeriesInfo, slug
from system_status.core.sources.base import Readings, Source, read_int, read_text
from system_status.core.sources.devices import (
    AmdGpu,
    Disk,
    NetInterface,
    realpath,
    scan_amd_gpus,
    scan_disks,
    scan_net_interfaces,
)

CPU_CHIPS = {"k10temp", "coretemp", "zenpower", "cpu_thermal"}
DIMM_CHIPS = {"spd5118", "jc42"}
DISK_CHIPS = {"nvme", "drivetemp"}

_CHANNEL_FILE = re.compile(r"(temp|fan|power|in|pwm)(\d+)(_input|_average)?")

# Friendlier default labels of AMD's CPU sensors.
_K10TEMP_LABELS = {"Tctl": "Package (Tctl)", "Tdie": "Die (Tdie)"}
_TCCD = re.compile(r"Tccd(\d+)")
_AMDGPU_LABELS = {"edge": "Edge", "junction": "Junction", "mem": "Memory", "PPT": "Power (PPT)"}


@dataclass
class _Channel:
    kind: str  # the sysfs prefix: temp, fan, power, in, pwm
    number: int
    path: Path
    label: str | None
    metric: str = ""
    hidden_reason: str | None = None
    scale: float = 1.0
    series_kind: Kind = Kind.TEMP


@dataclass
class _Chip:
    name: str
    device_path: str | None
    prefix: str
    group_kind: str  # cpu, dimm, disk, gpu, net, board
    label_prefix: str
    channels: list[_Channel] = field(default_factory=list)
    # Disks keep the order of their I/O series, so a disk has the same colour in both graphs.
    order: int = 0


def _scan_channels(directory: Path) -> list[_Channel]:
    channels: dict[tuple[str, int], _Channel] = {}
    for entry in sorted(directory.iterdir()):
        match = _CHANNEL_FILE.fullmatch(entry.name)
        if match is None:
            continue
        kind, number, suffix = match.group(1), int(match.group(2)), match.group(3)
        if kind == "pwm" and suffix is not None:
            continue
        if kind != "pwm" and suffix is None:
            continue
        key = (kind, number)
        # power1_input wins over power1_average when a chip has both.
        if key in channels and suffix == "_average":
            continue
        label = read_text(directory / f"{kind}{number}_label") or None
        channels[key] = _Channel(kind, number, entry, label)
    kind_order = {"temp": 0, "fan": 1, "power": 2, "pwm": 3, "in": 4}
    return sorted(channels.values(), key=lambda c: (kind_order[c.kind], c.number))


def _name_channels(chip: _Chip) -> None:
    """Give every channel of ``chip`` its metric name, kind, scale, and hidden flag."""
    unlabeled = Counter(c.kind for c in chip.channels if c.label is None)
    taken: dict[str, _Channel] = {}
    for channel in chip.channels:
        channel_name = f"{channel.kind}{channel.number}"
        if channel.label is not None and slug(channel.label) not in taken:
            channel.metric = slug(channel.label)
        elif channel.label is None and unlabeled[channel.kind] == 1:
            channel.metric = channel.kind
        else:
            channel.metric = channel_name
        if channel.label is not None and slug(channel.label) in taken:
            first = taken[slug(channel.label)]
            if read_text(first.path) == read_text(channel.path):
                channel.hidden_reason = f"duplicate of {first.metric}"
        taken.setdefault(channel.metric, channel)
        if channel.label is not None:
            taken.setdefault(slug(channel.label), channel)

        if channel.kind == "temp":
            channel.series_kind, channel.scale = Kind.TEMP, 0.001
        elif channel.kind == "fan":
            channel.series_kind, channel.scale = Kind.FAN, 1.0
        elif channel.kind == "power":
            channel.series_kind, channel.scale = Kind.POWER, 0.000001
        elif channel.kind == "pwm":
            channel.series_kind, channel.scale = Kind.PERCENT, 100 / 255
        else:
            channel.series_kind, channel.scale = Kind.VOLTAGE, 0.001
            channel.hidden_reason = channel.hidden_reason or "raw voltage"


def _channel_label(chip: _Chip, channel: _Channel) -> str:
    text = channel.label
    if chip.name == "k10temp" and text is not None:
        tccd = _TCCD.fullmatch(text)
        text = f"CCD {tccd.group(1)}" if tccd else _K10TEMP_LABELS.get(text, text)
    if chip.name == "amdgpu" and text is not None:
        text = _AMDGPU_LABELS.get(text, text)
    if text is None:
        names = {"temp": "Temperature", "fan": "Fan", "power": "Power", "pwm": "PWM", "in": "In"}
        text = names[channel.kind]
        if channel.metric != channel.kind:
            text = f"{text} {channel.number}"
    elif channel.metric != slug(channel.label or ""):
        text = f"{text} ({channel.kind}{channel.number})"
    if chip.label_prefix and (channel.metric == channel.kind or channel.label == "Composite"):
        # The chip's only sensor of its kind: the drive model or the DIMM name says it all.
        return chip.label_prefix
    return f"{chip.label_prefix} {text}".strip()


def _group(chip: _Chip, channel: _Channel) -> str:
    """Return the default-layout group of a channel, see ``SeriesInfo.group``."""
    if channel.series_kind == Kind.VOLTAGE:
        return "voltage"
    if chip.group_kind == "gpu":
        if channel.series_kind == Kind.POWER and not any(c.kind == "fan" for c in chip.channels):
            # A GPU without a fan is an APU's integrated one. Its "PPT" sensor reads a fraction
            # of a watt that says little, so the default layout leaves it out.
            return "apu-power"
        return f"gpu:{chip.prefix.split(':', 1)[1]}"
    if channel.series_kind == Kind.FAN:
        return "fan"
    if channel.kind == "pwm":
        return "pwm"
    if channel.series_kind == Kind.POWER:
        return "board-power" if chip.group_kind == "board" else f"{chip.group_kind}-power"
    if chip.group_kind == "board" or chip.group_kind == "net":
        return "board-temp"
    return {"cpu": "cpu-temp", "dimm": "mem-temp", "disk": "disk-temp"}[chip.group_kind]


def _parent_interface(device_path: str, interfaces: list[NetInterface]) -> NetInterface | None:
    for interface in interfaces:
        if interface.device_path and device_path.startswith(interface.device_path + "/"):
            return interface
    return None


class HwmonSource(Source):
    def __init__(
        self,
        sys_root: Path = Path("/sys"),
        disks: list[Disk] | None = None,
        amd_gpus: list[AmdGpu] | None = None,
    ):
        self.sys_root = sys_root
        disks = scan_disks(sys_root) if disks is None else disks
        amd_gpus = scan_amd_gpus(sys_root) if amd_gpus is None else amd_gpus
        interfaces = scan_net_interfaces(sys_root)
        self._chips = self._scan(disks, amd_gpus, interfaces)
        self._series: list[SeriesInfo] = []
        self._paths: dict[str, tuple[Path, float]] = {}
        for chip in self._chips:
            for channel in chip.channels:
                series_id = f"{chip.prefix}/{channel.metric}"
                if series_id in self._paths:
                    continue
                self._paths[series_id] = (channel.path, channel.scale)
                self._series.append(
                    SeriesInfo(
                        id=series_id,
                        label=_channel_label(chip, channel),
                        kind=channel.series_kind,
                        group=_group(chip, channel),
                        hidden=channel.hidden_reason is not None,
                        hidden_reason=channel.hidden_reason,
                    )
                )

    def _scan(
        self, disks: list[Disk], amd_gpus: list[AmdGpu], interfaces: list[NetInterface]
    ) -> list[_Chip]:
        hwmon = self.sys_root / "class" / "hwmon"
        directories = list(hwmon.iterdir()) if hwmon.is_dir() else []
        chips: list[_Chip] = []
        for directory in directories:
            name = read_text(directory / "name")
            if not name:
                continue
            device_path = realpath(directory / "device")
            if device_path and "/power_supply/" in device_path:
                # Batteries of mice and laptops; their temperature is not a system sensor.
                continue
            chip = self._classify(name, device_path, disks, amd_gpus, interfaces)
            if chip is None:
                continue
            chip.channels = _scan_channels(directory)
            if chip.channels:
                chips.append(chip)
        # Two chips with one prefix, say two sockets with k10temp: tell them apart by device.
        chips.sort(key=lambda c: (c.prefix, c.device_path or ""))
        counts = Counter(chip.prefix for chip in chips)
        for chip in chips:
            if counts[chip.prefix] > 1:
                chip.prefix = f"{chip.prefix}@{slug(Path(chip.device_path or 'none').name)}"
            _name_channels(chip)
        order = {"cpu": 0, "dimm": 1, "gpu": 2, "disk": 3, "net": 4, "board": 5}
        chips.sort(key=lambda c: (order[c.group_kind], c.order, c.prefix))
        return chips

    @staticmethod
    def _classify(
        name: str,
        device_path: str | None,
        disks: list[Disk],
        amd_gpus: list[AmdGpu],
        interfaces: list[NetInterface],
    ) -> _Chip | None:
        if name in DISK_CHIPS and device_path:
            for disk in disks:
                if disk.device_path == device_path:
                    chip = _Chip(name, device_path, f"disk:{disk.key}", "disk", disk.model)
                    chip.order = disks.index(disk)
                    return chip
        if name in DIMM_CHIPS and device_path:
            # The i2c device is named <bus>-<address>, for example 8-0051.
            address = Path(device_path).name.rpartition("-")[2]
            key = f"0x{address[-2:]}" if address else Path(device_path).name
            return _Chip(name, device_path, f"dimm:{key}", "dimm", f"DIMM {key}")
        if name == "amdgpu" and device_path:
            for gpu in amd_gpus:
                if gpu.device_path == device_path:
                    return _Chip(name, device_path, f"gpu:{gpu.key}", "gpu", "")
        if name in CPU_CHIPS:
            return _Chip(name, device_path, f"hwmon:{slug(name)}", "cpu", "")
        if device_path:
            interface = _parent_interface(device_path, interfaces)
            if interface is not None:
                return _Chip(
                    name, device_path, f"net:{interface.name}", "net", f"{interface.name} NIC"
                )
        return _Chip(name, device_path, f"hwmon:{slug(name)}", "board", "")

    def series(self) -> list[SeriesInfo]:
        return list(self._series)

    def read(self) -> Readings:
        readings: Readings = {}
        for series_id, (path, scale) in self._paths.items():
            raw = read_int(path)
            readings[series_id] = None if raw is None else raw * scale
        return readings
