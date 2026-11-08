"""Stable names for disks, GPUs and network interfaces, read from sysfs.

Kernel numbering (``hwmon3``, ``nvme1``, ``card0``) changes between boots, so IDs are built
from what the hardware reports about itself: the disk model, the PCI address order of GPUs, the
interface name that udev gives a NIC.
"""

import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from system_status.core.series import slug
from system_status.core.sources.base import read_text


def realpath(path: Path) -> str | None:
    """Return the resolved path of a sysfs link, or None when it does not exist."""
    if not os.path.lexists(path):
        return None
    return os.path.realpath(path)


@dataclass(frozen=True)
class Disk:
    """A whole physical disk: ``sda`` or ``nvme0n1``, not a partition or a loop device."""

    kernel_name: str
    # The ID part: the model, plus ``@<kernel name>`` when two disks share a model.
    key: str
    model: str
    # The resolved ``device`` link: the SCSI device of a SATA disk, the NVMe controller of an
    # NVMe namespace. Hwmon chips of the disk point to the same directory.
    device_path: str


def scan_disks(sys_root: Path) -> list[Disk]:
    block = sys_root / "class" / "block"
    found: list[tuple[str, str, str]] = []
    for entry in sorted(block.iterdir()) if block.is_dir() else []:
        if (entry / "partition").exists():
            continue
        model = read_text(entry / "device" / "model")
        device_path = realpath(entry / "device")
        if not model or device_path is None:
            continue
        found.append((entry.name, model.strip(), device_path))
    counts = Counter(slug(model) for _, model, _ in found)
    disks = []
    for kernel_name, model, device_path in found:
        key = slug(model)
        if counts[key] > 1:
            key = f"{key}@{kernel_name}"
        disks.append(Disk(kernel_name, key, model, device_path))
    return disks


@dataclass(frozen=True)
class AmdGpu:
    key: str  # "amd0"
    name: str
    device_path: str


_CARD = re.compile(r"card\d+")


def scan_amd_gpus(sys_root: Path) -> list[AmdGpu]:
    """Return the GPUs driven by ``amdgpu``, numbered in PCI address order."""
    drm = sys_root / "class" / "drm"
    paths: set[str] = set()
    for entry in drm.iterdir() if drm.is_dir() else []:
        if not _CARD.fullmatch(entry.name):
            continue
        device_path = realpath(entry / "device")
        driver = realpath(entry / "device" / "driver")
        if device_path and driver and os.path.basename(driver) == "amdgpu":
            paths.add(device_path)
    gpus = []
    for index, device_path in enumerate(sorted(paths, key=os.path.basename)):
        name = read_text(Path(device_path) / "product_name") or "AMD Radeon Graphics"
        gpus.append(AmdGpu(f"amd{index}", name, device_path))
    return gpus


@dataclass(frozen=True)
class NetInterface:
    name: str
    # Virtual interfaces (bridges, veth, tun, loopback) live under /sys/devices/virtual.
    virtual: bool
    # The resolved directory of the interface's parent device, None for virtual ones.
    device_path: str | None


def scan_net_interfaces(sys_root: Path) -> list[NetInterface]:
    net = sys_root / "class" / "net"
    interfaces = []
    for entry in sorted(net.iterdir()) if net.is_dir() else []:
        resolved = realpath(entry) or ""
        virtual = "/devices/virtual/" in resolved
        device_path = None if virtual else realpath(entry / "device")
        interfaces.append(NetInterface(entry.name, virtual, device_path))
    return interfaces
