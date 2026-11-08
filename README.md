# System Status

Linux system monitor in the style of GNOME System Monitor's Resources tab, which also graphs what that one doesn't: CPU, DIMM, disk, GPU and motherboard temperatures, fan speeds, GPU utilization and power. The sections, the graphs, which series go into which graph, the sensor labels and the legends all come from one TOML file, so you can change the layout by editing text, or by asking an agent to edit it, instead of clicking through a settings dialog.

## Features

- **Collapsible sections of graphs**: CPU, Memory, one section per GPU, Disks, Fans, Network, Motherboard by default
- **Everything hwmon reports**: temperatures, fans, PWM duty, power, with stable IDs that survive reboots (hwmon numbers don't)
- **GPUs**: NVIDIA through NVML (proprietary driver), AMD through sysfs; utilization, video memory, temperature, fan, power
- **Disks and network**: read/write and receive/send rates, disk temperatures labelled with the drive model
- **Config-driven layout**: globs over series IDs, per-sensor labels and colours, legends on or off per graph
- **Hot reload**: saving the settings file updates the window; a broken file shows its error above the graphs and keeps the last good layout
- **Light and dark**: follows the desktop's colour scheme, or set `theme`
- **X11 and Wayland**: Qt 6 with its own Fusion palette, so it looks the same on both

## Requirements

- Linux with sysfs (`/sys/class/hwmon`, `/sys/class/block`, `/sys/class/net`, `/sys/class/drm`)
- Python 3.12+
- Optional: the NVIDIA proprietary driver for NVIDIA GPUs (its `libnvidia-ml.so`). Without it there are no NVIDIA series, and nothing fails.
- SATA disk temperatures need the `drivetemp` kernel module: `sudo modprobe drivetemp`, and `echo drivetemp | sudo tee /etc/modules-load.d/drivetemp.conf` to load it at boot.

No root access is needed.

## Installation

```bash
git clone https://github.com/fprochazka/system-status.git
cd system-status

# Install as a tool with uv
uv tool install .

# Or in editable mode for development
uv tool install -e .

# Or straight from GitHub, without a clone
uv tool install git+https://github.com/fprochazka/system-status.git

# Add System Status with its icon to the desktop's application list
system-status --install-desktop-entry
```

### Application launcher

To start System Status from the desktop's application list, like any other app, run:

```bash
system-status --install-desktop-entry
```

This writes the launcher `~/.local/share/applications/system-status.desktop` and the icon `~/.local/share/icons/hicolor/scalable/apps/system-status.svg`, or the same files under `$XDG_DATA_HOME` when that is set. The launcher runs the absolute path of the `system-status` command you ran, so install System Status with `uv tool install` first; the `~/.local/bin/system-status` link that uv creates is kept as it is, so the launcher survives a reinstall. A `--config` given with `--install-desktop-entry` goes into the launcher as an absolute path; without one, the launcher uses the default settings file. The command checks that the settings file loads before it writes anything, and prints the paths it wrote. Run it again after you move System Status or the settings file. When `update-desktop-database` is installed, the command runs it on the applications directory, and when the icon directory already has an icon cache, it updates the cache with `gtk-update-icon-cache`; a failure of either is ignored.

The window's Wayland app id and X11 window class are `system-status`, the launcher's name, so GNOME's dock groups the window under the launcher and shows its icon. The icon is a graph with a filled area and a thermometer on a dark rounded tile; the window uses it too.

To uninstall, remove the launcher and the icon first, then the tool:

```bash
system-status --remove-desktop-entry
uv tool uninstall system-status
```

## Usage

```bash
# Open the window (same as `system-status gui`)
system-status

# List every series of this machine: ID, label, current value, and why it is hidden
system-status sensors
system-status sensors --json

# Print the generated default layout as a settings file, or write it to the settings path
system-status dump-config
system-status dump-config --write

# Render the window off screen to a PNG after sampling for 10 seconds
system-status screenshot /tmp/system-status.png --seconds 10 --theme dark

# Use another settings file
system-status --config ./my-settings.toml
```

The window shows the last `history_seconds` of every graph, newest on the right, with a vertical gridline every minute. Hover over a graph to see the time and the values of the sample under the cursor. Click a section header to collapse or expand it; the window remembers that until it closes, also across reloads of the settings file.

Default colours: every disk keeps one colour in every graph, by its position among the machine's disks, so its temperature and its throughput look alike. A disk's further series in the same graph get lighter shades of that colour: write after read, `Sensor_1` after `Composite`. CCD N gets the same colour in its usage, `Tccd<N+1>` temperature and clock lines, with the first palette colour left for the total or the package. Other series take the next free colour by position. `color` in `[sensors]` overrides all of this.

A graph with a single series shows its current value at the right end of its title, and no legend. A graph with more series shows a legend under the plot: a colour swatch, the label and the current value of each series, flowing left to right like words in a paragraph and wrapping when a line is full. Each value keeps the widest width it has had, so items do not shift every second. Memory and video memory add the used and total bytes to the value.

## Series IDs

Every value the app can graph is a series with an ID of the form `<group>:<device>/<metric>`, or `<group>:<metric>` for the machine-wide ones. IDs are built from what hardware reports about itself, never from kernel numbering like `hwmon3` or `nvme1`, which changes between boots.

| ID | What | Unit |
|---|---|---|
| `cpu:total`, `cpu:core<N>` | CPU usage, total and per logical CPU (from 0) | % |
| `cpu:ccd<N>/usage` | Usage of the logical CPUs that share one L3 cache: a CCD on AMD Ryzen. Hidden when the CPU has a single L3 group, because it equals `cpu:total` | % |
| `cpu:ccd<N>/clock` | The highest current clock among the L3 group's CPUs (`scaling_cur_freq`), so a boosting core shows even while its neighbours sleep | MHz, shown as GHz |
| `cpu:pcore<N>` | Usage of a physical core: the busier of its SMT threads, because one saturated thread already occupies most of a core. Labelled `Core <N+1>`; meant for a heatmap | % |
| `cpu:user`, `cpu:nice`, `cpu:system`, `cpu:iowait`, `cpu:steal` | Share of all CPU time; `system` includes irq and softirq. Stacked, they add up to usage plus I/O wait | % |
| `mem:used`, `swap:used` | Memory and swap in use | % |
| `hwmon:<chip>/<sensor>` | CPU sensors (`k10temp`, `coretemp`) and every other hwmon chip, such as a Super-IO (`nct6687`, `it8686`) | °C, RPM, W, % |
| `dimm:<i2c address>/temp` | DDR4/DDR5 module temperature (`spd5118`, `jc42`), e.g. `dimm:0x51/temp` | °C |
| `disk:<model>/<sensor>` | Drive temperature: `Composite`, `Sensor_1` of NVMe drives, `temp` of SATA drives | °C |
| `disk:<model>/read`, `disk:<model>/write` | Disk throughput | B/s |
| `gpu:nvidia<N>/<metric>` | NVIDIA, in NVML order: `util`, `mem_util`, `vram`, `temp`, `fan` (in %), `power` | %, °C, W |
| `gpu:amd<N>/<metric>` | AMD, in PCI address order: `util`, `vram`, and the hwmon sensors such as `edge`, `PPT` | %, °C, W |
| `net:total/rx`, `net:total/tx` | Network traffic of all physical interfaces | B/s |
| `net:<interface>/rx`, `net:<interface>/tx` | Traffic of one interface | B/s |
| `net:<interface>/temp` | Temperature of a network card's PHY | °C |

L3 groups and physical cores come from the kernel's topology files (`cache/index3/shared_cpu_list`, `topology/thread_siblings_list`), numbered from 0 by their lowest logical CPU; on a Ryzen 9 9950X `ccd0` is CPUs 0–7 and 16–23, and `pcore0` is CPUs 0 and 16. CCD labels count from 1 (`CCD 1` is `ccd0`), like k10temp's `Tccd1`.

Rules for the parts:

- `<model>` and labels become ID parts by replacing each run of characters other than letters, digits, `.`, `_`, `+` and `-` with `_`: `WD Red SN700 2000GB` becomes `WD_Red_SN700_2000GB`, `Diode 0 (curr)` becomes `Diode_0_curr`. Case is kept, and matching is case-sensitive.
- A hwmon sensor is named by its label (`Tctl`, `Composite`, `AMD_TSI_Addr_98h`), or by its channel (`temp3`, `fan11`, `pwm2`) when it has no label. A chip's only unlabeled sensor of a kind is named after the kind, so a SATA drive's temperature is `disk:ST4000VN008-2DR1/temp`.
- When two sensors of a chip have the same label, the first keeps the label and the next one gets its channel name, e.g. `hwmon:nct6687/temp6`. If both read the same value, the second one is a duplicate and hidden.
- Two disks of the same model get the kernel name: `disk:ST4000VN008-2DR1@sda`. Kernel names can change between boots, so such IDs are less stable than the others. Two chips of the same name, such as two CPU sockets, get their device: `hwmon:k10temp@0000_40_18.3`.

Some series are hidden: duplicates, raw voltages and virtual network interfaces (bridges, veth, VPN tunnels). A glob does not match them, their exact ID does. `system-status sensors` lists them with the reason.

## Configuration

The settings file is `~/.config/system-status/settings.toml` (`$XDG_CONFIG_HOME` is respected). Without it, the window uses a generated default layout; `system-status dump-config` prints exactly that layout, as a starting point.

```toml
history_seconds = 300          # how much history every graph shows
interval_seconds = 1.0         # how often everything is sampled
theme = "auto"                 # auto, light or dark

[sensors]                      # per-series overrides, by exact ID, wherever the series is shown
"hwmon:k10temp/Tctl"  = { label = "CPU package", color = "#e01b24" }
"hwmon:nct6687/fan11" = { label = "Rear exhaust" }
"hwmon:nct6687/temp6" = { hidden = false }     # show a hidden series in globs too
"disk:Samsung_SSD_960_EVO_500GB/Sensor_1" = { hidden = true }   # hide it everywhere

[[section]]                    # sections appear in file order
title = "CPU"
collapsed = false

  [[section.graph]]            # graphs appear in file order within their section
  title = "Usage"
  series = ["cpu:total"]
  legend = false

  [[section.graph]]
  title = "Cores"
  series = ["cpu:pcore*"]
  type = "heatmap"             # a row per physical core, grouped by CCD

  [[section.graph]]
  title = "Temperature"
  series = ["hwmon:k10temp/*", "hwmon:nct6687/AMD_TSI_Addr_98h"]
  right = ["hwmon:nct6687/fan1"]   # the CPU fan, dashed, on a second axis in RPM
  min = 20
  max = 100

[[section]]
title = "Fans"

  [[section.graph]]
  title = "Speed"
  series = ["hwmon:nct6687/*"]
  kind = "fan"                 # only the fans of the chip, not its temperatures
  hide_zero = true             # leave out unpopulated fan headers

[[section]]
title = "Disks"

  [[section.graph]]
  title = "Throughput"
  series = ["disk:*/read", "disk:*/write"]
  stacked = true               # areas on top of each other; the top edge is the total
  hide_zero = true
```

Top-level keys:

| Key | Default | Meaning |
|---|---|---|
| `history_seconds` | `300` | Width of every graph in seconds, 10 to 86400 |
| `interval_seconds` | `1.0` | Sampling interval, 0.1 to 60 seconds |
| `theme` | `"auto"` | `auto` follows the desktop, or `light`, `dark`; `--theme` overrides it |

`[sensors]` entries take `label`, `color` (`"#rrggbb"`) and `hidden`. `hidden = true` removes the series from every graph, even where its exact ID is listed; `hidden = false` lets globs match a series that is hidden by default.

`[[section]]` keys: `title` (required), `collapsed` (default `false`), and its `[[section.graph]]` tables.

`[[section.graph]]` keys:

| Key | Default | Meaning |
|---|---|---|
| `title` | `""` | Shown above the plot |
| `series` | required | IDs and globs (`*`, `?`, `[...]`), matched in order, each series once |
| `legend` | `true` | Show the legend under a graph with more than one series |
| `hide_zero` | `false` | Leave out series that were 0 for the whole visible history |
| `stacked` | `false` | Draw the left-axis series as filled areas on top of each other; the top edge is their total and the y-axis scales to it. The legend still shows each series' own value, the hover tooltip adds the total. Only for kinds that add up: not `temp`, `voltage` or `frequency`, and not for a heatmap |
| `kind` | kind of the first match | Keep only series of this kind: `percent`, `temp`, `fan`, `power`, `rate`, `voltage`, `frequency` |
| `type` | `"line"` | `"heatmap"` draws one thin row per series instead of a line, coloured from the card colour (0 %) through blue and purple to red (100 %), time on the x-axis. Rows are grouped by cluster (the CCD of a `cpu:pcore<N>`), with the cluster's name on the left. Percent series use 0–100 %, others `min`/`max` or the visible range. The title row shows the colour scale; hovering shows the row and value. No legend. |
| `right` | `[]` | IDs and globs drawn against a second y-axis on the right edge, with its own kind, unit and range, as dashed lines. Their legend swatch is a dashed stroke with an arrow to the right. The right axis has no gridlines of its own: its ticks sit on the left axis's gridlines. In a graph with two axes, an axis with a single series has its tick labels in that series' colour. A series can't be on both axes |
| `right_kind` | kind of the first right match | Keep only right series of this kind. Without it, right patterns matching several kinds are an error: a graph has two kinds at most, one per axis |
| `right_min`, `right_max` | `0`–`100` for percent, automatic otherwise | Fixed bounds of the right axis |
| `right_title` | see right | Shown at the right end of the title row, above the right axis, in the right series' colour when the axis has one series. By default the label of the single right series when it has at most 20 characters (`Power`, `Fan`), otherwise the kind's name: `Percent`, `Temperature`, `Fan`, `Power`, `Throughput`, `Voltage`, `Clock`. A graph with a right axis doesn't show a current value in its title row |
| `row_height` | `7` | Height of one heatmap row in pixels, 2 to 40; only with `type = "heatmap"`, whose height follows from its rows |
| `min`, `max` | `0`–`100` for percent, automatic otherwise | Fixed y-axis bounds |
| `height` | `90` | Height of the plot in pixels, 30 to 1000 |

Without any `[[section]]`, the default layout is used, so a file with only `[sensors]` relabels the default layout. A graph never mixes kinds: series of another kind than the graph's are dropped. A stacked graph whose patterns match several kinds is an error shown in the banner, unless it sets `kind`, because a sum of mixed units means nothing. Unknown keys and wrong types are errors that name their place, like `section[2].graph[0]: unknown key "lgend"`, so a typo never silently does nothing. `system-status sensors` ends with the patterns of the settings file that match no series of this machine.

### Reloading

The window watches the settings file and reloads it about 0.2 s after it changes, also when an editor saves it by replacing the file. If the new file has an error, a red banner above the graphs shows it and the previous layout stays. A change of `history_seconds` or `interval_seconds` restarts sampling with an empty history.

### Default layout

- **CPU**: usage (total and one line per CCD), a heatmap of physical cores grouped by CCD, CPU temperatures, clock per CCD
- **Memory**: memory and swap usage, DIMM temperatures
- **GPU · <name>**, one per GPU: usage (utilization, memory controller, video memory) with power on a right axis, and temperature with the fan on a right axis. The power sensor of a GPU without a fan, an APU's integrated one, is left out: it reads a fraction of a watt
- **Disks**: temperatures, and one stacked throughput graph with the read and write rate of every disk (idle ones hidden)
- **Fans**: every fan that has spun during the visible history
- **Network**: total receive and send rates of the physical interfaces
- **Motherboard**: remaining temperatures (Super-IO, network card), other power sensors

Per-logical-CPU usage, the CPU time breakdown, PWM duty and per-interface traffic are not in the default layout; add them by ID or glob. For example, where the CPU time goes:

```toml
  [[section.graph]]
  title = "Time"
  series = ["cpu:user", "cpu:nice", "cpu:system", "cpu:iowait", "cpu:steal"]
  stacked = true
  hide_zero = true
```

## Development

```bash
uv sync
uv run system-status
uv run ruff check . && uv run ruff format --check .
uv run pytest
```

The `system_status/core` package has no Qt imports: sources read sysfs, psutil and NVML into a sampler that runs on a background thread and keeps a ring buffer per series. The GUI reads an immutable snapshot four times a second and repaints when a new sample arrived. Tests build a fake `/sys` tree of real files in a temporary directory; the GUI tests run with `QT_QPA_PLATFORM=offscreen`.

## Known limits

- Devices that appear after start (a USB disk, a VPN interface) are not picked up until the app restarts.
- Intel GPU utilization is not read.
- NVIDIA fan speed is in percent of full speed, as NVML reports it, not in RPM.
- The AMD `PPT` sensor of an APU's integrated GPU reports what the driver calls package power; its meaning varies by chip.

## License

MIT
