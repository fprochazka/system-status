"""Colours and fonts: an Adwaita-like light and dark palette, applied the same on X11 and Wayland.

The app uses Qt's Fusion style with its own palette instead of the platform theme, so it looks
the same whatever Qt platform plugin and theme plugin a desktop provides.
"""

import shutil
import subprocess
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

# GNOME's palette, the "3" shades: readable on both light and dark backgrounds.
SERIES_COLORS = (
    "#3584e4",  # blue
    "#e66100",  # orange
    "#2ec27e",  # green
    "#c01c28",  # red
    "#9141ac",  # purple
    "#e5a50a",  # yellow
    "#2190a4",  # teal
    "#986a44",  # brown
    "#d56199",  # pink
    "#6f8396",  # slate
)

# Fonts tried for the whole window when the desktop's own can't be read, GNOME's first.
PREFERRED_FONTS = ("Cantarell", "Ubuntu Sans", "Inter", "Noto Sans", "DejaVu Sans")


@dataclass(frozen=True)
class Theme:
    dark: bool
    window: QColor
    card: QColor
    border: QColor
    grid: QColor
    text: QColor
    dim_text: QColor
    accent: QColor
    error_background: QColor
    error_text: QColor


LIGHT = Theme(
    dark=False,
    window=QColor("#fafafb"),
    card=QColor("#ffffff"),
    border=QColor(0, 0, 6, 30),
    grid=QColor(0, 0, 6, 18),
    text=QColor(0, 0, 6, 204),
    dim_text=QColor(0, 0, 6, 128),
    accent=QColor("#3584e4"),
    error_background=QColor("#f9dcdf"),
    error_text=QColor("#a51d2d"),
)

DARK = Theme(
    dark=True,
    window=QColor("#222226"),
    card=QColor("#2e2e32"),
    border=QColor(255, 255, 255, 26),
    grid=QColor(255, 255, 255, 18),
    text=QColor("#ffffff"),
    dim_text=QColor(255, 255, 255, 140),
    accent=QColor("#78aeed"),
    error_background=QColor("#5c1d24"),
    error_text=QColor("#ffbec5"),
)


def _gsettings(key: str) -> str | None:
    """Return a GNOME interface setting, or None outside GNOME."""
    executable = shutil.which("gsettings")
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, "get", "org.gnome.desktop.interface", key],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip().strip("'")


def system_prefers_dark() -> bool:
    """Ask Qt first (the desktop portal); fall back to GNOME's setting when Qt doesn't know."""
    scheme = QGuiApplication.styleHints().colorScheme()
    if scheme != Qt.ColorScheme.Unknown:
        return scheme == Qt.ColorScheme.Dark
    return _gsettings("color-scheme") == "prefer-dark"


def resolve_theme(name: str) -> Theme:
    if name == "dark":
        return DARK
    if name == "light":
        return LIGHT
    return DARK if system_prefers_dark() else LIGHT


def pick_font() -> QFont:
    """Return the desktop's interface font, like ``"Cantarell 11"`` on GNOME.

    Qt without a GNOME platform theme falls back to fontconfig's "sans-serif", and the offscreen
    platform has no desktop at all, so the font is read from GNOME's settings and picked from
    ``PREFERRED_FONTS`` when that fails.
    """
    families = set(QFontDatabase.families())
    setting = _gsettings("font-name") or ""
    family, _, size = setting.rpartition(" ")
    if family in families and size.replace(".", "", 1).isdigit():
        font = QFont(family)
        font.setPointSizeF(float(size))
        return font
    for family in PREFERRED_FONTS:
        if family in families:
            font = QFont(family)
            font.setPointSizeF(11)
            return font
    return QFont()


def apply_theme(app: QApplication, theme: Theme) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    role = QPalette.ColorRole
    palette.setColor(role.Window, theme.window)
    palette.setColor(role.Base, theme.card)
    palette.setColor(role.AlternateBase, theme.window)
    palette.setColor(role.WindowText, theme.text)
    palette.setColor(role.Text, theme.text)
    palette.setColor(role.ButtonText, theme.text)
    palette.setColor(role.Button, theme.card)
    palette.setColor(role.ToolTipBase, theme.card)
    palette.setColor(role.ToolTipText, theme.text)
    palette.setColor(role.Highlight, theme.accent)
    palette.setColor(role.HighlightedText, QColor("#ffffff"))
    palette.setColor(role.PlaceholderText, theme.dim_text)
    palette.setColor(role.Mid, theme.border)
    palette.setColor(role.Dark, theme.border)
    palette.setColor(role.Light, theme.card)
    palette.setColor(role.Midlight, theme.card)
    palette.setColor(role.Shadow, theme.border)
    app.setPalette(palette)
