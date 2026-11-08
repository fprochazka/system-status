"""The desktop app: Qt in the main thread, the sampler in its own, the config file watched."""

import os
import signal
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QTimer
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication

from system_status.core.config import Config, ConfigError, load_config
from system_status.core.layout import effective_sections, resolve_graph
from system_status.core.sampler import Sampler
from system_status.core.sources.base import Source
from system_status.gui import launcher
from system_status.gui.theme import apply_theme, pick_font, resolve_theme
from system_status.gui.window import WINDOW_TITLE, MainWindow

# How often the window looks for a new sample. It repaints only when there is one.
REFRESH_MILLISECONDS = 250
# Editors save in bursts (truncate, write, rename); reload once the burst is over.
RELOAD_DELAY_MILLISECONDS = 200

SourceFactory = Callable[[], Sequence[Source]]


class GuiApp:
    """Sample on a background thread and show the snapshots in a ``MainWindow``.

    The config file is watched. A change that parses replaces the layout; one that doesn't
    shows its error above the graphs and keeps the last good layout. A change of
    ``history_seconds`` or ``interval_seconds`` restarts sampling with an empty history.
    """

    def __init__(
        self,
        config_path: Path,
        qt_app: QApplication,
        source_factory: SourceFactory,
        theme_override: str | None = None,
    ):
        self.qt_app = qt_app
        self.config_path = config_path
        self.source_factory = source_factory
        self.theme_override = theme_override
        error = None
        try:
            self.config = load_config(config_path)
        except ConfigError as exc:
            self.config = Config()
            error = str(exc)
        self.theme = resolve_theme(theme_override or self.config.theme)
        apply_theme(qt_app, self.theme)
        self.sampler = self._new_sampler(self.config)
        self.layout_error: str | None = None
        self.window = MainWindow(self.theme)
        self._apply_layout()
        self.window.show_error(error or self.layout_error)
        self._last_shown: float | None = None

        self.timer = QTimer()
        self.timer.setInterval(REFRESH_MILLISECONDS)
        self.timer.timeout.connect(self.refresh)

        self.watcher = QFileSystemWatcher()
        self._reload_timer = QTimer()
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(RELOAD_DELAY_MILLISECONDS)
        self._reload_timer.timeout.connect(self.reload)
        self.watcher.fileChanged.connect(lambda _path: self._reload_timer.start())
        self.watcher.directoryChanged.connect(lambda _path: self._reload_timer.start())
        self._watch()
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_color_scheme_changed)

    def _new_sampler(self, config: Config) -> Sampler:
        return Sampler(
            self.source_factory(),
            interval_seconds=config.interval_seconds,
            history_seconds=config.history_seconds,
        )

    def _watch(self) -> None:
        """Watch the file and its directory.

        Editors that save atomically replace the file, and the watcher drops a path whose file
        was replaced, so the path is added again after every change. The directory is watched
        too, for a file that does not exist yet.
        """
        directory = self.config_path.parent
        if directory.is_dir() and str(directory) not in self.watcher.directories():
            self.watcher.addPath(str(directory))
        if self.config_path.exists() and str(self.config_path) not in self.watcher.files():
            self.watcher.addPath(str(self.config_path))

    def _apply_layout(self) -> None:
        catalog = self.sampler.catalog
        sections = []
        errors = []
        for section in effective_sections(self.config, catalog):
            graphs = [resolve_graph(g, catalog, self.config.sensors) for g in section.graphs]
            errors += [f"{section.title}: {g.error}" for g in graphs if g.error]
            sections.append((section.title, section.collapsed, graphs))
        self.window.set_layout(sections)
        self.layout_error = "; ".join(errors) or None

    def reload(self) -> None:
        self._watch()
        try:
            config = load_config(self.config_path)
        except ConfigError as error:
            self.window.show_error(str(error))
            return
        self.window.show_error(self.layout_error)
        if config == self.config:
            return
        restart = (config.history_seconds, config.interval_seconds) != (
            self.config.history_seconds,
            self.config.interval_seconds,
        )
        theme_changed = config.theme != self.config.theme
        self.config = config
        if restart:
            self.sampler.close()
            self.sampler = self._new_sampler(config)
            self.sampler.start()
            self._last_shown = None
        if theme_changed:
            self._set_theme()
        self._apply_layout()
        self.window.show_error(self.layout_error)
        self.refresh()

    def _set_theme(self) -> None:
        self.theme = resolve_theme(self.theme_override or self.config.theme)
        apply_theme(self.qt_app, self.theme)
        self.window.set_theme(self.theme)

    def _on_color_scheme_changed(self) -> None:
        if (self.theme_override or self.config.theme) == "auto":
            self._set_theme()
            self._apply_layout()

    def refresh(self) -> None:
        snapshot = self.sampler.snapshot()
        if snapshot.latest_time is None or snapshot.latest_time == self._last_shown:
            return
        self._last_shown = snapshot.latest_time
        self.window.show_snapshot(snapshot)

    def run(self) -> int:
        """Show the window and run Qt's event loop until the window closes or Ctrl+C."""
        previous_handler = signal.signal(signal.SIGINT, lambda *_: self.qt_app.quit())
        try:
            self.sampler.start()
            self.window.show()
            self.timer.start()
            return self.qt_app.exec()
        finally:
            signal.signal(signal.SIGINT, previous_handler)
            self.timer.stop()
            self.sampler.close()


def create_qt_app() -> QApplication:
    qt_app = QApplication.instance()
    if qt_app is None:
        qt_app = QApplication(sys.argv[:1])
    assert isinstance(qt_app, QApplication)
    qt_app.setApplicationName(WINDOW_TITLE)
    # The Wayland app id and X11 window class, which the shell matches to the launcher's
    # system-status.desktop to group the window and to find its icon.
    qt_app.setDesktopFileName(launcher.APP_ID)
    # The icon of every window, for desktops that don't take it from the launcher.
    qt_app.setWindowIcon(application_icon())
    qt_app.setFont(pick_font())
    return qt_app


def application_icon() -> QIcon:
    """Return the packaged SVG icon, which Qt renders at every size it is asked for.

    Qt reads the file when it first draws the icon, so the file has to stay where it is. It does
    for a package installed as files, the way uv and pip install it.
    """
    return QIcon(str(launcher.icon_resource()))


def run_gui(config_path: Path, source_factory: SourceFactory, theme: str | None = None) -> int:
    return GuiApp(config_path, create_qt_app(), source_factory, theme).run()


def render_screenshot(
    config_path: Path,
    source_factory: SourceFactory,
    output: Path,
    seconds: float,
    theme: str | None = None,
    width: int = 760,
) -> Path:
    """Sample for ``seconds``, then save the whole window, unscrolled, as a PNG.

    Uses Qt's offscreen platform unless another one is set, so no window appears on screen.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_app = create_qt_app()
    app = GuiApp(config_path, qt_app, source_factory, theme)
    app.sampler.start()
    app.window.resize(width, 900)
    app.window.show()
    app.timer.start()
    QTimer.singleShot(int(seconds * 1000), qt_app.quit)
    try:
        qt_app.exec()
        app.refresh()
        qt_app.processEvents()
        app.window.resize(width, app.window.content_height())
        qt_app.processEvents()
        app.window.resize(width, app.window.content_height())
        qt_app.processEvents()
        output.parent.mkdir(parents=True, exist_ok=True)
        if not app.window.grab().save(str(output)):
            raise OSError(f"could not write {output}")
    finally:
        app.timer.stop()
        app.sampler.close()
        app.window.close()
    return output
