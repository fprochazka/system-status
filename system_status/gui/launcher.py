"""The application launcher: ``$XDG_DATA_HOME/applications/system-status.desktop`` and its icon.

The launcher puts System Status in the desktop's application list, and the icon theme lookup
finds ``Icon=system-status`` in ``$XDG_DATA_HOME/icons/hicolor/scalable/apps``. The window uses
the same name as its app id (``QGuiApplication.setDesktopFileName``) and X11 window class, so
GNOME's dock groups the window under the launcher and shows its icon.

This module needs no Qt, so the launcher can be installed and removed without a display. The
Exec helpers are separate from the launcher, so a login autostart entry could reuse them.
"""

import os
import shutil
import subprocess
import sys
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

APP_ID = "system-status"
FILE_NAME = f"{APP_ID}.desktop"
ICON_NAME = APP_ID
# How long a cache update may take before it is left alone.
_CACHE_UPDATE_TIMEOUT_SECONDS = 30

# Characters that make the Desktop Entry spec require quoting an Exec argument.
_RESERVED = set(" \t\n\"'\\><~|&;$*?#()`")
# Inside quotes, these keep their meaning unless a backslash precedes them.
_ESCAPED_IN_QUOTES = set('"`$\\')


def xdg_home(variable: str, default: str) -> Path:
    """Return the directory in ``$variable``, or ``~/default`` when unset, empty or relative.

    The XDG Base Directory spec says to ignore a relative value.
    """
    value = os.environ.get(variable, "")
    if value and os.path.isabs(value):
        return Path(value)
    return Path.home() / default


def data_home() -> Path:
    """Return ``$XDG_DATA_HOME``, or ``~/.local/share``."""
    return xdg_home("XDG_DATA_HOME", ".local/share")


def entry_path() -> Path:
    return data_home() / "applications" / FILE_NAME


def icon_path() -> Path:
    return data_home() / "icons" / "hicolor" / "scalable" / "apps" / f"{ICON_NAME}.svg"


def icon_resource() -> Traversable:
    """Return the packaged application icon, an SVG file."""
    return files("system_status.gui").joinpath("resources", f"{ICON_NAME}.svg")


def system_status_executable() -> Path:
    """Return the absolute path of the ``system-status`` command that runs this process.

    Symlinks stay as they are: ``uv tool install`` links ``~/.local/bin/system-status`` into the
    tool's environment, and the link keeps working when the tool is reinstalled. When this
    process isn't the script, for example under ``python -m system_status``, the command is
    looked up on ``PATH``. Raises ``FileNotFoundError`` if there is none.
    """
    candidates = [sys.argv[0], APP_ID] if sys.argv and sys.argv[0] else [APP_ID]
    for candidate in candidates:
        found = shutil.which(candidate)
        if found is not None and not found.endswith(".py"):
            return Path(found).absolute()
    raise FileNotFoundError("the system-status command is not on PATH")


def quote_exec_argument(argument: str) -> str:
    """Quote one ``Exec`` argument as the Desktop Entry spec requires.

    An argument with a reserved character goes in double quotes, with a backslash before each
    ``"``, `````, ``$`` and ``\\``. A ``%`` is doubled, because ``%`` starts a field code.
    """
    argument = argument.replace("%", "%%")
    if argument and not set(argument) & _RESERVED:
        return argument
    quoted = "".join(f"\\{char}" if char in _ESCAPED_IN_QUOTES else char for char in argument)
    return f'"{quoted}"'


def escape_string_value(value: str) -> str:
    """Escape a Desktop Entry string value; this applies on top of the ``Exec`` quoting."""
    return (
        value.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r")
    )


def exec_line(executable: Path, config: Path | None) -> str:
    """Return the ``Exec`` value that opens the window, with ``config`` if one was given."""
    arguments = [str(executable)]
    if config is not None:
        # The desktop runs the entry from the home directory, not from the current one.
        arguments += ["--config", str(config.absolute())]
    return escape_string_value(" ".join(quote_exec_argument(arg) for arg in arguments))


def entry_text(executable: Path, config: Path | None) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=System Status\n"
        "GenericName=System Monitor\n"
        "Comment=Graph CPU, memory, temperatures, fans, disks and GPUs\n"
        f"Exec={exec_line(executable, config)}\n"
        f"Icon={ICON_NAME}\n"
        "Terminal=false\n"
        "Categories=System;Monitor;\n"
        "Keywords=system;monitor;temperature;sensors;fan;gpu;cpu;hwmon;\n"
        "StartupNotify=true\n"
        # Qt sets the window's class and Wayland app id from setDesktopFileName(APP_ID).
        f"StartupWMClass={APP_ID}\n"
    )


def install(executable: Path, config: Path | None) -> tuple[Path, Path]:
    """Write the launcher and the icon, replacing existing ones, and return their paths."""
    icon = icon_path()
    icon.parent.mkdir(parents=True, exist_ok=True)
    icon.write_bytes(icon_resource().read_bytes())
    entry = entry_path()
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(entry_text(executable, config))
    update_caches()
    return entry, icon


def remove() -> tuple[Path | None, Path | None]:
    """Delete the launcher and the icon, and return the paths of those that existed."""
    removed = (_unlink(entry_path()), _unlink(icon_path()))
    update_caches()
    return removed


def update_caches() -> None:
    """Refresh the desktop's caches of the user's launchers and icons, ignoring any failure.

    ``update-desktop-database`` indexes the launchers by MIME type. The icon cache is updated
    only when one exists: GTK checks only the theme directory's time to tell whether the cache
    is current, so a cache created here could later hide icons that another program adds
    without updating it. Without a cache, GTK reads the directories.
    """
    if entry_path().parent.is_dir():
        _run_quietly("update-desktop-database", "-q", str(entry_path().parent))
    theme = icon_path().parents[2]
    if (theme / "icon-theme.cache").exists():
        _run_quietly("gtk-update-icon-cache", "-q", "-f", "-t", str(theme))


def _run_quietly(command: str, *arguments: str) -> None:
    executable = shutil.which(command)
    if executable is None:
        return
    try:
        subprocess.run(
            [executable, *arguments],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=_CACHE_UPDATE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _unlink(path: Path) -> Path | None:
    try:
        path.unlink()
    except FileNotFoundError:
        return None
    return path
