"""A fake /sys tree in a temporary directory, made of real files and symlinks."""

import os
from pathlib import Path


class FakeSys:
    """Build the parts of sysfs that the sources read. Paths mimic the real layout."""

    def __init__(self, root: Path):
        self.root = root
        self._hwmon = 0

    def write(self, relative: str, content: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content + "\n")
        return path

    def link(self, relative: str, target: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        (self.root / target).mkdir(parents=True, exist_ok=True)
        os.symlink(self.root / target, path)

    def hwmon(
        self,
        name: str,
        device: str | None,
        files: dict[str, str | int],
        number: int | None = None,
    ) -> str:
        """Add ``/sys/class/hwmon/hwmonN`` with a ``device`` link and the given files."""
        if number is None:
            number = self._hwmon
        self._hwmon = max(self._hwmon, number) + 1
        directory = f"class/hwmon/hwmon{number}"
        self.write(f"{directory}/name", name)
        if device is not None:
            self.link(f"{directory}/device", device)
        for file_name, value in files.items():
            self.write(f"{directory}/{file_name}", str(value))
        return directory

    def block(self, kernel_name: str, device: str, model: str, stat: str = "") -> None:
        self.link(f"class/block/{kernel_name}/device", device)
        self.write(f"{device}/model", model)
        fields = stat or "0 0 0 0 0 0 0 0 0 0 0"
        self.write(f"class/block/{kernel_name}/stat", fields)

    def net(self, name: str, device: str | None, rx: int = 0, tx: int = 0) -> None:
        if device is None:
            target = f"devices/virtual/net/{name}"
        else:
            target = f"{device}/net/{name}"
        self.link(f"class/net/{name}", target)
        if device is not None:
            self.link(f"{target}/device", device)
        self.write(f"{target}/statistics/rx_bytes", str(rx))
        self.write(f"{target}/statistics/tx_bytes", str(tx))

    def amd_gpu(self, card: str, device: str, busy: int, vram_used: int, vram_total: int) -> None:
        self.link(f"class/drm/{card}/device", device)
        self.link(f"{device}/driver", "bus/pci/drivers/amdgpu")
        self.write(f"{device}/gpu_busy_percent", str(busy))
        self.write(f"{device}/mem_info_vram_used", str(vram_used))
        self.write(f"{device}/mem_info_vram_total", str(vram_total))


PCI = "devices/pci0000:00"
NVME_WD = f"{PCI}/0000:00:01.2/0000:02:00.0/nvme/nvme0"
NVME_SAMSUNG = f"{PCI}/0000:00:02.1/0000:05:00.0/nvme/nvme1"
SATA_A = f"{PCI}/0000:12:00.0/ata1/host0/target0:0:0/0:0:0:0"
AMD_GPU = f"{PCI}/0000:00:08.1/0000:79:00.0"
NIC = f"{PCI}/0000:04:05.0/0000:07:00.0"


def build_machine(fake: FakeSys, hwmon_numbers: list[int] | None = None) -> None:
    """Build a machine like the developer's: AMD CPU, Super-IO, DIMMs, disks, iGPU, NIC.

    ``hwmon_numbers`` reorders the hwmonN directories, as a reboot does.
    """
    numbers = iter(hwmon_numbers or range(100))
    for cpu in range(4):
        base = f"devices/system/cpu/cpu{cpu}"
        fake.write(f"{base}/topology/thread_siblings_list", f"{cpu % 2},{cpu % 2 + 2}")
        fake.write(f"{base}/cache/index3/shared_cpu_list", "0,2" if cpu % 2 == 0 else "1,3")
        fake.write(f"{base}/cpufreq/scaling_cur_freq", "4200000")
    fake.block("nvme0n1", NVME_WD, "WD Red SN700 2000GB                     ")
    fake.block("nvme1n1", NVME_SAMSUNG, "Samsung SSD 960 EVO 500GB")
    fake.block("sda", SATA_A, "ST4000VN008-2DR1")
    fake.write("class/block/nvme0n1p1/partition", "1")
    fake.amd_gpu("card0", AMD_GPU, busy=7, vram_used=512 * 2**20, vram_total=2 * 2**30)
    fake.net("enp7s0", NIC, rx=1000, tx=2000)
    fake.net("docker0", None, rx=50, tx=60)
    fake.net("lo", None)

    fake.hwmon(
        "k10temp",
        f"{PCI}/0000:00:18.3",
        {
            "temp1_input": 89375,
            "temp1_label": "Tctl",
            "temp3_input": 91875,
            "temp3_label": "Tccd1",
            "temp4_input": 41500,
            "temp4_label": "Tccd2",
        },
        next(numbers),
    )
    fake.hwmon(
        "nct6687",
        "devices/platform/nct6683.2592",
        {
            "temp1_input": 89000,
            "temp1_label": "AMD TSI Addr 98h",
            "temp2_input": 38000,
            "temp2_label": "Diode 0 (curr)",
            "temp3_input": 39000,
            "temp3_label": "Thermistor 15",
            "temp6_input": 39000,
            "temp6_label": "Thermistor 15",
            "fan1_input": 1481,
            "fan2_input": 0,
            "fan11_input": 979,
            "in0_input": 1008,
            "in0_label": "VIN0",
            "pwm1": 255,
            "pwm1_enable": 5,
        },
        next(numbers),
    )
    fake.hwmon("spd5118", f"{PCI}/0000:00:14.0/i2c-8/8-0051", {"temp1_input": 36750}, next(numbers))
    fake.hwmon("spd5118", f"{PCI}/0000:00:14.0/i2c-8/8-0053", {"temp1_input": 34750}, next(numbers))
    fake.hwmon("nvme", NVME_WD, {"temp1_input": 38850, "temp1_label": "Composite"}, next(numbers))
    fake.hwmon(
        "nvme",
        NVME_SAMSUNG,
        {
            "temp1_input": 21850,
            "temp1_label": "Composite",
            "temp2_input": 21850,
            "temp2_label": "Sensor 1",
        },
        next(numbers),
    )
    fake.hwmon("drivetemp", SATA_A, {"temp1_input": 20000}, next(numbers))
    fake.hwmon(
        "amdgpu",
        AMD_GPU,
        {
            "temp1_input": 44000,
            "temp1_label": "edge",
            "power1_input": 15_500_000,
            "power1_label": "PPT",
            "in0_input": 1290,
            "in0_label": "vddgfx",
            "freq1_input": 600000000,
        },
        next(numbers),
    )
    phy = f"{NIC}/mdio_bus/r8169-0-700/r8169-0-700:00"
    fake.hwmon("r8169_0_700:00", phy, {"temp1_input": 46500}, next(numbers))
    fake.hwmon(
        "hidpp_battery_20",
        "devices/virtual/misc/uhid/0005:046D:B01D.001D/power_supply/hidpp_battery_20",
        {"temp1_input": 30000},
        next(numbers),
    )
