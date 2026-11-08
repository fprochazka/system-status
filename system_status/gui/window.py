"""The main window: an error banner on top, then a scrolling column of sections."""

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QMainWindow,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from system_status.core.layout import ResolvedGraph
from system_status.core.snapshot import Snapshot
from system_status.gui.graph_view import GraphView
from system_status.gui.section import CollapsibleSection
from system_status.gui.theme import Theme

WINDOW_TITLE = "System Status"
CONTENT_MARGIN = 18


class MainWindow(QMainWindow):
    def __init__(self, theme: Theme):
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(760, 900)
        self.theme = theme
        self.sections: list[CollapsibleSection] = []
        self.graphs: list[GraphView] = []
        # Sections the user opened or closed, by title; they keep that state across reloads.
        self._user_expanded: dict[str, bool] = {}
        self._snapshot: Snapshot | None = None
        # The shared (left, right) gutters of all graphs; see _align_gutters.
        self._gutters = (0, 0)

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.banner.hide()
        outer.addWidget(self.banner)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll)
        self.setCentralWidget(central)
        self.content: QWidget | None = None
        self._style_banner()

    def _style_banner(self) -> None:
        theme = self.theme
        self.banner.setStyleSheet(
            f"background: {theme.error_background.name()}; color: {theme.error_text.name()};"
            f" padding: 10px {CONTENT_MARGIN}px;"
        )

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self._style_banner()

    def show_error(self, message: str | None) -> None:
        if message:
            self.banner.setText(f"Settings not applied: {message}")
            self.banner.show()
        else:
            self.banner.hide()

    def set_layout(self, sections: Sequence[tuple[str, bool, Sequence[ResolvedGraph]]]) -> None:
        """Replace every section with ``(title, collapsed, graphs)`` built from the config."""
        content = QWidget()
        content.setAutoFillBackground(True)
        palette = content.palette()
        palette.setColor(content.backgroundRole(), QColor(self.theme.window))
        content.setPalette(palette)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(CONTENT_MARGIN - 8, 10, CONTENT_MARGIN, CONTENT_MARGIN)
        layout.setSpacing(6)
        self.sections = []
        self.graphs = []
        for title, collapsed, graphs in sections:
            section = CollapsibleSection(title, self.theme)
            for graph in graphs:
                view = GraphView(graph, self.theme)
                view.setContentsMargins(8, 0, 0, 0)
                section.add_widget(view)
                self.graphs.append(view)
            section.set_expanded(self._user_expanded.get(title, not collapsed))
            section.toggled.connect(
                lambda expanded, title=title: self._user_expanded.__setitem__(title, expanded)
            )
            layout.addWidget(section)
            self.sections.append(section)
        layout.addStretch(1)
        self._align_gutters()
        old = self.scroll.takeWidget()
        self.scroll.setWidget(content)
        self.content = content
        if old is not None:
            old.deleteLater()
        if self._snapshot is not None:
            self.show_snapshot(self._snapshot)

    def _align_gutters(self) -> None:
        """Give every graph the same left and right gutter, so one moment has one x position in
        all of them, collapsed ones included, so they line up once expanded.

        The gutters only grow while the window is open: a reload that drops the widest label
        keeps the plots where they were instead of shifting them.
        """
        left = max([graph.plot.left_needed() for graph in self.graphs] + [self._gutters[0]])
        right = max([graph.plot.right_axis_width() for graph in self.graphs] + [self._gutters[1]])
        self._gutters = (left, right)
        for graph in self.graphs:
            graph.set_gutters(left, right)

    def show_snapshot(self, snapshot: Snapshot) -> None:
        self._snapshot = snapshot
        for graph in self.graphs:
            graph.set_snapshot(snapshot)

    def content_height(self) -> int:
        """Return the height that shows every section without scrolling."""
        if self.content is None:
            return self.height()
        self.content.adjustSize()
        width = self.scroll.viewport().width()
        layout = self.content.layout()
        needed = layout.heightForWidth(width) if layout.hasHeightForWidth() else -1
        if needed < 0:
            needed = layout.sizeHint().height()
        banner = self.banner.sizeHint().height() if self.banner.isVisible() else 0
        return needed + banner
