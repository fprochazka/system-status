"""GPU utilization and memory: amdgpu from sysfs, NVIDIA through NVML.

Every GPU gets the same metric names, so a config can say ``gpu:*/util``:

- ``util`` — how busy the GPU is, in percent,
- ``mem_util`` — how busy the memory controller is (NVIDIA only),
- ``vram`` — used video memory in percent of the total, with the bytes in the legend,
- ``temp``, ``fan`` (in percent of full speed), ``power`` — NVIDIA only; an AMD GPU reports
  these through hwmon, as ``gpu:amd0/Edge``-style IDs (see ``hwmon.py``).
"""

import logging
from pathlib import Path
from typing import Any

from system_status.core.series import Kind, SeriesInfo, format_bytes
from system_status.core.sources.base import Readings, Source, read_int
from system_status.core.sources.devices import AmdGpu, scan_amd_gpus

logger = logging.getLogger(__name__)


class AmdGpuSource(Source):
    def __init__(self, sys_root: Path = Path("/sys"), gpus: list[AmdGpu] | None = None):
        self.gpus = scan_amd_gpus(sys_root) if gpus is None else gpus
        self._details: dict[str, str] = {}

    def series(self) -> list[SeriesInfo]:
        series = []
        for gpu in self.gpus:
            device = Path(gpu.device_path)
            group = f"gpu:{gpu.key}"
            if (device / "gpu_busy_percent").exists():
                series.append(SeriesInfo(f"{group}/util", "GPU", Kind.PERCENT, group))
            if (device / "mem_info_vram_total").exists():
                series.append(SeriesInfo(f"{group}/vram", "Video memory", Kind.PERCENT, group))
        return series

    def read(self) -> Readings:
        readings: Readings = {}
        for gpu in self.gpus:
            device = Path(gpu.device_path)
            busy = read_int(device / "gpu_busy_percent")
            readings[f"gpu:{gpu.key}/util"] = None if busy is None else float(busy)
            used = read_int(device / "mem_info_vram_used")
            total = read_int(device / "mem_info_vram_total")
            if used is not None and total:
                readings[f"gpu:{gpu.key}/vram"] = 100 * used / total
                details = f"{format_bytes(used)} of {format_bytes(total)}"
                self._details[f"gpu:{gpu.key}/vram"] = details
        return readings

    def details(self) -> dict[str, str]:
        return dict(self._details)

    def names(self) -> dict[str, str]:
        return {gpu.key: gpu.name for gpu in self.gpus}


def _load_nvml() -> Any | None:
    try:
        import pynvml

        pynvml.nvmlInit()
    except Exception as error:  # the library is missing, or no driver is loaded
        logger.debug("NVML is not available: %s", error)
        return None
    return pynvml


class NvidiaGpuSource(Source):
    """NVIDIA GPUs through NVML, the library of the proprietary driver.

    Without the driver there are no NVIDIA series. A metric that a card doesn't support (some
    have no fan sensor) is left out when the source starts.
    """

    def __init__(self, nvml: Any | None = None):
        self._nvml = nvml if nvml is not None else _load_nvml()
        self._handles: list[Any] = []
        self._names: dict[str, str] = {}
        self._series: list[SeriesInfo] = []
        self._details: dict[str, str] = {}
        if self._nvml is None:
            return
        try:
            count = self._nvml.nvmlDeviceGetCount()
            self._handles = [self._nvml.nvmlDeviceGetHandleByIndex(i) for i in range(count)]
        except Exception as error:
            logger.warning("NVML could not list GPUs: %s", error)
            self._handles = []
        for index, handle in enumerate(self._handles):
            key = f"nvidia{index}"
            self._names[key] = self._call(self._nvml.nvmlDeviceGetName, handle) or "NVIDIA GPU"
            sample = self._read_one(handle, key)
            labels = {
                "util": ("GPU", Kind.PERCENT),
                "mem_util": ("Memory controller", Kind.PERCENT),
                "vram": ("Video memory", Kind.PERCENT),
                "temp": ("Temperature", Kind.TEMP),
                "fan": ("Fan", Kind.PERCENT),
                "power": ("Power", Kind.POWER),
            }
            for metric, (label, kind) in labels.items():
                series_id = f"gpu:{key}/{metric}"
                if sample.get(series_id) is not None:
                    self._series.append(SeriesInfo(series_id, label, kind, f"gpu:{key}"))

    @staticmethod
    def _call(function, *args):
        try:
            return function(*args)
        except Exception:
            return None

    def _read_one(self, handle: Any, key: str) -> Readings:
        nvml = self._nvml
        readings: Readings = {}
        utilization = self._call(nvml.nvmlDeviceGetUtilizationRates, handle)
        if utilization is not None:
            readings[f"gpu:{key}/util"] = float(utilization.gpu)
            readings[f"gpu:{key}/mem_util"] = float(utilization.memory)
        memory = self._call(nvml.nvmlDeviceGetMemoryInfo, handle)
        if memory is not None and memory.total:
            readings[f"gpu:{key}/vram"] = 100 * memory.used / memory.total
            self._details[f"gpu:{key}/vram"] = (
                f"{format_bytes(memory.used)} of {format_bytes(memory.total)}"
            )
        temperature = self._call(nvml.nvmlDeviceGetTemperature, handle, 0)  # 0 = the GPU die
        readings[f"gpu:{key}/temp"] = None if temperature is None else float(temperature)
        fan = self._call(nvml.nvmlDeviceGetFanSpeed, handle)
        readings[f"gpu:{key}/fan"] = None if fan is None else float(fan)
        power = self._call(nvml.nvmlDeviceGetPowerUsage, handle)  # milliwatts
        readings[f"gpu:{key}/power"] = None if power is None else power / 1000
        return readings

    def series(self) -> list[SeriesInfo]:
        return list(self._series)

    def read(self) -> Readings:
        readings: Readings = {}
        for index, handle in enumerate(self._handles):
            readings.update(self._read_one(handle, f"nvidia{index}"))
        return readings

    def details(self) -> dict[str, str]:
        return dict(self._details)

    def names(self) -> dict[str, str]:
        return dict(self._names)

    def close(self) -> None:
        if self._nvml is not None:
            self._call(self._nvml.nvmlShutdown)
