import pytest

from system_status.core.series import Kind
from system_status.core.sources.devices import scan_amd_gpus, scan_disks
from system_status.core.sources.hwmon import HwmonSource
from tests.sysfs import SATA_A, FakeSys, build_machine


@pytest.fixture
def fake(tmp_path) -> FakeSys:
    fake = FakeSys(tmp_path)
    build_machine(fake)
    return fake


def test_ids_labels_and_kinds(fake):
    series = {info.id: info for info in HwmonSource(fake.root).series()}

    assert series["hwmon:k10temp/Tctl"].label == "Package (Tctl)"
    assert series["hwmon:k10temp/Tccd1"].label == "CCD 1"
    assert series["hwmon:k10temp/Tctl"].group == "cpu-temp"
    assert series["dimm:0x51/temp"].label == "DIMM 0x51"
    assert series["dimm:0x53/temp"].group == "mem-temp"
    assert series["disk:WD_Red_SN700_2000GB/Composite"].label == "WD Red SN700 2000GB"
    assert series["disk:Samsung_SSD_960_EVO_500GB/Sensor_1"].label == (
        "Samsung SSD 960 EVO 500GB Sensor 1"
    )
    assert series["disk:ST4000VN008-2DR1/temp"].group == "disk-temp"
    assert series["gpu:amd0/edge"].label == "Edge"
    assert series["gpu:amd0/edge"].group == "gpu:amd0"
    assert series["gpu:amd0/PPT"].kind == Kind.POWER
    assert series["net:enp7s0/temp"].label == "enp7s0 NIC"
    assert series["hwmon:nct6687/AMD_TSI_Addr_98h"].group == "board-temp"
    assert series["hwmon:nct6687/fan11"].label == "Fan 11"
    assert series["hwmon:nct6687/fan11"].kind == Kind.FAN
    assert series["hwmon:nct6687/pwm"].kind == Kind.PERCENT
    # Batteries are skipped, frequencies aren't read.
    assert not any("hidpp" in series_id for series_id in series)
    assert not any("freq" in series_id for series_id in series)


def test_duplicates_and_voltages_are_hidden_but_listed(fake):
    series = {info.id: info for info in HwmonSource(fake.root).series()}

    assert not series["hwmon:nct6687/Thermistor_15"].hidden
    duplicate = series["hwmon:nct6687/temp6"]
    assert duplicate.hidden
    assert duplicate.hidden_reason == "duplicate of Thermistor_15"
    assert duplicate.label == "Thermistor 15 (temp6)"
    assert series["hwmon:nct6687/VIN0"].hidden
    assert series["gpu:amd0/vddgfx"].kind == Kind.VOLTAGE


def test_same_label_with_other_value_is_not_a_duplicate(tmp_path):
    fake = FakeSys(tmp_path)
    fake.hwmon(
        "it8686",
        "devices/platform/it87.2624",
        {"temp1_input": 30000, "temp1_label": "PCH", "temp2_input": 50000, "temp2_label": "PCH"},
    )
    series = {info.id: info for info in HwmonSource(tmp_path).series()}

    assert not series["hwmon:it8686/temp2"].hidden


def test_values_are_scaled(fake):
    readings = HwmonSource(fake.root).read()

    assert readings["hwmon:k10temp/Tctl"] == pytest.approx(89.375)
    assert readings["gpu:amd0/PPT"] == pytest.approx(15.5)
    assert readings["hwmon:nct6687/fan1"] == 1481
    assert readings["hwmon:nct6687/pwm"] == pytest.approx(100)
    assert readings["hwmon:nct6687/VIN0"] == pytest.approx(1.008)


def test_unreadable_sensor_reads_as_none(fake):
    source = HwmonSource(fake.root)
    (fake.root / "class/hwmon/hwmon0/temp1_input").unlink()

    assert source.read()["hwmon:k10temp/Tctl"] is None


def test_ids_do_not_depend_on_hwmon_numbers(tmp_path):
    first = FakeSys(tmp_path / "boot1")
    build_machine(first)
    second = FakeSys(tmp_path / "boot2")
    build_machine(second, hwmon_numbers=[9, 3, 7, 1, 5, 0, 8, 2, 6, 4])

    ids_first = [(i.id, i.label) for i in HwmonSource(first.root).series()]
    ids_second = [(i.id, i.label) for i in HwmonSource(second.root).series()]

    assert ids_first == ids_second


def test_disks_with_the_same_model_get_the_kernel_name(tmp_path):
    fake = FakeSys(tmp_path)
    other = SATA_A.replace("ata1", "ata2").replace("0:0:0:0", "1:0:0:0")
    fake.block("sda", SATA_A, "ST4000VN008-2DR1")
    fake.block("sdb", other, "ST4000VN008-2DR1")
    fake.hwmon("drivetemp", SATA_A, {"temp1_input": 20000})
    fake.hwmon("drivetemp", other, {"temp1_input": 25000})

    disks = scan_disks(tmp_path)
    ids = {info.id for info in HwmonSource(tmp_path).series()}

    assert [d.key for d in disks] == ["ST4000VN008-2DR1@sda", "ST4000VN008-2DR1@sdb"]
    assert ids == {"disk:ST4000VN008-2DR1@sda/temp", "disk:ST4000VN008-2DR1@sdb/temp"}


def test_two_chips_of_one_name_are_told_apart_by_device(tmp_path):
    fake = FakeSys(tmp_path)
    fake.hwmon(
        "k10temp", "devices/pci0000:00/0000:00:18.3", {"temp1_input": 1, "temp1_label": "Tctl"}
    )
    fake.hwmon(
        "k10temp", "devices/pci0000:40/0000:40:18.3", {"temp1_input": 2, "temp1_label": "Tctl"}
    )

    ids = [info.id for info in HwmonSource(tmp_path).series()]

    assert ids == ["hwmon:k10temp@0000_00_18.3/Tctl", "hwmon:k10temp@0000_40_18.3/Tctl"]


def test_amd_gpus_are_numbered_in_pci_order(tmp_path):
    fake = FakeSys(tmp_path)
    fake.amd_gpu("card1", "devices/pci0000:00/0000:03:00.0", 1, 1, 2)
    fake.amd_gpu("card0", "devices/pci0000:00/0000:79:00.0", 1, 1, 2)

    gpus = scan_amd_gpus(tmp_path)

    assert [(g.key, g.device_path.rsplit("/", 1)[1]) for g in gpus] == [
        ("amd0", "0000:03:00.0"),
        ("amd1", "0000:79:00.0"),
    ]
