"""CPU usage by L3 cache group (CCD), by physical core, by kind of time, and clock per CCD.

Everything comes from the kernel's own description of the CPU, so nothing here knows a CPU
model:

- An L3 group is the set of logical CPUs that share an L3 cache
  (``cache/index3/shared_cpu_list``). On an AMD Ryzen that is one CCD; on most Intel desktop
  CPUs it is the whole chip. Groups are numbered by their lowest CPU: ``ccd0``, ``ccd1``.
- A physical core is the set of SMT siblings (``topology/thread_siblings_list``), numbered by
  the lowest CPU: ``pcore0``, ``pcore1``.

Usage is computed from ``/proc/stat`` time deltas between two reads.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from system_status.core.series import Kind, SeriesInfo
from system_status.core.sources.base import Readings, Source, read_int, read_text

_CPU_DIR = re.compile(r"cpu(\d+)")


def parse_cpu_list(text: str) -> list[int]:
    """Parse a kernel CPU list such as ``"0-7,16-23"`` into sorted CPU numbers."""
    cpus: set[int] = set()
    for part in text.strip().split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            first, last = part.split("-", 1)
            cpus.update(range(int(first), int(last) + 1))
        else:
            cpus.add(int(part))
    return sorted(cpus)


@dataclass(frozen=True)
class CpuTopology:
    # Logical CPUs of each L3 group, groups ordered by their lowest CPU.
    ccds: tuple[tuple[int, ...], ...]
    # Logical CPUs of each physical core (SMT siblings), ordered by their lowest CPU.
    cores: tuple[tuple[int, ...], ...]

    def ccd_of_core(self, core: int) -> int:
        first = self.cores[core][0]
        return next(i for i, cpus in enumerate(self.ccds) if first in cpus)


def scan_cpu_topology(sys_root: Path = Path("/sys")) -> CpuTopology:
    """Read the L3 groups and SMT siblings of the online CPUs.

    A CPU without the L3 file joins one group with all such CPUs; a CPU without the siblings
    file is a core of its own.
    """
    base = sys_root / "devices" / "system" / "cpu"
    cpus = []
    for entry in base.iterdir() if base.is_dir() else []:
        match = _CPU_DIR.fullmatch(entry.name)
        if match and (entry / "topology").is_dir():
            cpus.append(int(match.group(1)))
    cpus.sort()
    online = set(cpus)
    l3_groups: dict[tuple[int, ...], None] = {}
    siblings: dict[tuple[int, ...], None] = {}
    for cpu in cpus:
        directory = base / f"cpu{cpu}"
        l3 = read_text(directory / "cache" / "index3" / "shared_cpu_list")
        group = tuple(c for c in parse_cpu_list(l3) if c in online) if l3 else tuple(cpus)
        l3_groups.setdefault(group or (cpu,), None)
        thread = read_text(directory / "topology" / "thread_siblings_list")
        core = tuple(c for c in parse_cpu_list(thread) if c in online) if thread else (cpu,)
        siblings.setdefault(core or (cpu,), None)
    return CpuTopology(
        ccds=tuple(sorted(l3_groups, key=min)),
        cores=tuple(sorted(siblings, key=min)),
    )


@dataclass(frozen=True)
class CpuTimes:
    """One line of /proc/stat, in clock ticks."""

    user: int
    nice: int
    system: int
    idle: int
    iowait: int
    irq: int
    softirq: int
    steal: int

    @property
    def total(self) -> int:
        # guest and guest_nice are already counted in user and nice.
        return (
            self.user
            + self.nice
            + self.system
            + self.idle
            + self.iowait
            + self.irq
            + self.softirq
            + self.steal
        )

    @property
    def busy(self) -> int:
        return self.total - self.idle - self.iowait

    def minus(self, other: "CpuTimes") -> "CpuTimes":
        return CpuTimes(
            *(max(0, getattr(self, f) - getattr(other, f)) for f in CpuTimes.__dataclass_fields__)
        )


def parse_proc_stat(text: str) -> tuple[CpuTimes | None, dict[int, CpuTimes]]:
    """Return the all-CPU line and the per-CPU lines of /proc/stat."""
    total = None
    per_cpu: dict[int, CpuTimes] = {}
    for line in text.splitlines():
        if not line.startswith("cpu"):
            continue
        name, *fields = line.split()
        values = [int(v) for v in fields[:8]] + [0] * (8 - len(fields[:8]))
        times = CpuTimes(*values)
        if name == "cpu":
            total = times
        elif name[3:].isdigit():
            per_cpu[int(name[3:])] = times
    return total, per_cpu


def usage(delta: CpuTimes) -> float | None:
    return 100 * delta.busy / delta.total if delta.total > 0 else None


def breakdown(delta: CpuTimes) -> dict[str, float]:
    """Split the time between two reads into percentages of all CPU time.

    ``system`` includes interrupt time (irq and softirq). The parts and idle add up to 100.
    """
    if delta.total <= 0:
        return {}
    scale = 100 / delta.total
    return {
        "user": delta.user * scale,
        "nice": delta.nice * scale,
        "system": (delta.system + delta.irq + delta.softirq) * scale,
        "iowait": delta.iowait * scale,
        "steal": delta.steal * scale,
    }


BREAKDOWN_LABELS = {
    "user": "User",
    "nice": "Nice",
    "system": "System",
    "iowait": "I/O wait",
    "steal": "Steal",
}


class CpuTopologySource(Source):
    """Series per L3 group, per physical core, per kind of time, and clocks per L3 group.

    - ``cpu:ccd<N>/usage``: usage of the group's logical CPUs together, in %. With a single
      L3 group it equals ``cpu:total`` and is hidden.
    - ``cpu:ccd<N>/clock``: the highest current clock of the group's CPUs, in MHz, so a
      boosting core shows even while its neighbours sleep.
    - ``cpu:pcore<N>``: usage of a physical core: the busier of its SMT threads, in %. One
      saturated thread already occupies most of a core, so the busier thread says more than
      the average of both would.
    - ``cpu:user``, ``cpu:nice``, ``cpu:system`` (with irq and softirq), ``cpu:iowait``,
      ``cpu:steal``: share of all CPU time, in %; stacked they add up to the total usage plus
      I/O wait.
    """

    def __init__(self, sys_root: Path = Path("/sys"), proc_stat: Path = Path("/proc/stat")):
        self.sys_root = sys_root
        self.proc_stat = proc_stat
        self.topology = scan_cpu_topology(sys_root)
        self._previous: tuple[CpuTimes | None, dict[int, CpuTimes]] | None = None

    def series(self) -> list[SeriesInfo]:
        topology = self.topology
        single = len(topology.ccds) == 1
        series = []
        for index, _cpus in enumerate(topology.ccds):
            series.append(
                SeriesInfo(
                    f"cpu:ccd{index}/usage",
                    f"CCD {index + 1}",
                    Kind.PERCENT,
                    "cpu-ccd",
                    hidden=single,
                    hidden_reason="only one L3 group, same as cpu:total" if single else None,
                )
            )
        for index, cpus in enumerate(topology.ccds):
            if (self._cpufreq(cpus[0])).exists():
                series.append(
                    SeriesInfo(
                        f"cpu:ccd{index}/clock", f"CCD {index + 1}", Kind.FREQUENCY, "cpu-clock"
                    )
                )
        for index, _cpus in enumerate(topology.cores):
            ccd = topology.ccd_of_core(index)
            series.append(
                SeriesInfo(
                    f"cpu:pcore{index}",
                    f"Core {index + 1}",
                    Kind.PERCENT,
                    "cpu-pcore",
                    cluster=f"CCD {ccd + 1}" if not single else None,
                )
            )
        for name, label in BREAKDOWN_LABELS.items():
            series.append(SeriesInfo(f"cpu:{name}", label, Kind.PERCENT, "cpu-time"))
        return series

    def _cpufreq(self, cpu: int) -> Path:
        cpufreq = self.sys_root / "devices" / "system" / "cpu" / f"cpu{cpu}" / "cpufreq"
        return cpufreq / "scaling_cur_freq"

    def read(self) -> Readings:
        readings: Readings = {}
        for index, cpus in enumerate(self.topology.ccds):
            clocks = [read_int(self._cpufreq(cpu)) for cpu in cpus]
            known = [clock for clock in clocks if clock is not None]
            readings[f"cpu:ccd{index}/clock"] = max(known) / 1000 if known else None

        text = read_text(self.proc_stat)
        if text is None:
            return readings
        current = parse_proc_stat(text)
        previous, self._previous = self._previous, current
        if previous is None:
            return readings
        total_now, cpus_now = current
        total_before, cpus_before = previous
        deltas = {
            cpu: times.minus(cpus_before[cpu])
            for cpu, times in cpus_now.items()
            if cpu in cpus_before
        }
        for index, cpus in enumerate(self.topology.ccds):
            parts = [deltas[cpu] for cpu in cpus if cpu in deltas]
            busy = sum(part.busy for part in parts)
            all_time = sum(part.total for part in parts)
            readings[f"cpu:ccd{index}/usage"] = 100 * busy / all_time if all_time > 0 else None
        for index, cpus in enumerate(self.topology.cores):
            threads = [usage(deltas[cpu]) for cpu in cpus if cpu in deltas]
            known = [value for value in threads if value is not None]
            readings[f"cpu:pcore{index}"] = max(known) if known else None
        if total_now is not None and total_before is not None:
            for name, value in breakdown(total_now.minus(total_before)).items():
                readings[f"cpu:{name}"] = value
        return readings
