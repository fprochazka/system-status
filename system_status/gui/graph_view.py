"""One graph: a title row, a line chart or a heatmap, and an optional legend below it."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from system_status.core.layout import ResolvedGraph, right_title
from system_status.core.snapshot import Snapshot
from system_status.gui.graph_widget import LegendWidget, PlotWidget, build_lines, value_text
from system_status.gui.heatmap import HeatmapWidget, RampKey
from system_status.gui.theme import Theme


class GraphView(QWidget):
    """A graph with its title row and legend.

    With one series, the legend is left out and its value goes to the right end of the title
    row. With ``legend = false``, multi-series graphs show no values at all. A heatmap has no
    legend: its rows are labelled by cluster, and its title row shows the colour ramp.
    """

    def __init__(self, graph: ResolvedGraph, theme: Theme, parent: QWidget | None = None):
        super().__init__(parent)
        self.graph = graph
        self.theme = theme
        self.heatmap_mode = graph.config.type == "heatmap"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        if self.heatmap_mode:
            self.plot: PlotWidget | HeatmapWidget = HeatmapWidget(theme, graph.config.row_height)
            # Known before the first sample, so the window can size the shared gutter for
            # the cluster names.
            self.plot.clusters = [series.info.cluster for series in graph.series]
            self.plot._fit_height()
        else:
            self.plot = PlotWidget(theme, graph.config.height)
            self.plot.stacked = graph.config.stacked
            self.plot.has_right = bool(graph.right_series)
            self.plot.right_kind = graph.right_kind
            self.plot.right_min = graph.config.right_min
            self.plot.right_max = graph.config.right_max
        title_row = QHBoxLayout()
        self.paired = bool(graph.config.right) and isinstance(self.plot, PlotWidget)
        self.title_row = title_row
        self.title = QLabel(graph.config.title)
        title_font = QFont(self.font())
        title_font.setWeight(QFont.Weight.DemiBold)
        self.title.setFont(title_font)
        self.value = QLabel()
        self.value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.value.setStyleSheet(f"color: {theme.dim_text.name(QColor.NameFormat.HexArgb)}")
        title_row.addWidget(self.title)
        title_row.addStretch(1)
        title_row.addWidget(self.value)
        self.right_title = QLabel(right_title(graph) or "") if self.paired else None
        if self.right_title is not None:
            self.right_title.setFont(title_font)
            self.right_title.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self.value.hide()
            title_row.addWidget(self.right_title)
        self.ramp = RampKey(theme) if self.heatmap_mode else None
        if self.ramp is not None:
            title_row.addWidget(self.ramp)
        layout.addLayout(title_row)
        layout.addWidget(self.plot)
        self.legend = LegendWidget(theme)
        layout.addWidget(self.legend)
        self.set_gutters(self.plot.axis_width(), self.plot.right_axis_width())
        self.set_snapshot(None)

    def set_gutters(self, left: int, right: int) -> None:
        """Make the plot span from ``left`` to ``right`` pixels off the edges, like every other
        graph of the window. The title row's left title starts where the plot starts, and its
        right-hand items (right title, current value, colour scale) end where the plot ends.
        """
        self.plot.left_gutter = left
        self.plot.right_gutter = right
        # The plot spans [left + 0.5, width - right - 0.5]: title-row items start at its left
        # edge and end at its right edge, whatever the gutters hold.
        self.title_row.setContentsMargins(left + 1, 0, right, 0)
        self.legend.left_inset = left + 1
        self.legend.updateGeometry()
        self.plot.update()

    def set_snapshot(self, snapshot: Snapshot | None) -> None:
        graph = self.graph
        lines = build_lines(graph, snapshot)
        timestamps = snapshot.timestamps if snapshot else ()
        history = snapshot.history_seconds if snapshot else 300.0
        if isinstance(self.plot, HeatmapWidget):
            clusters = {s.info.id: s.info.cluster for s in graph.series}
            self.plot.set_data(
                graph.kind,
                lines,
                [clusters.get(line.series_id) for line in lines],
                timestamps,
                history,
                snapshot.interval_seconds if snapshot else 1.0,
                graph.config.min,
                graph.config.max,
            )
            assert self.ramp is not None
            self.ramp.set_range(graph.kind, self.plot.low, self.plot.high)
            self.legend.setVisible(False)
            return
        self.plot.set_data(
            graph.kind, lines, timestamps, history, graph.config.min, graph.config.max
        )
        self.plot.zero_hidden = bool(graph.series or graph.right_series) and not lines
        single = len(graph.series) + len(graph.right_series) == 1
        show_legend = graph.config.legend and not single and bool(lines)
        self.legend.setVisible(show_legend)
        if show_legend:
            self.legend.set_lines(graph.kind, lines)
        self.value.setText(value_text(graph.kind, lines[0]) if single and lines else "")
        if self.right_title is not None:
            # Tinted like the right axis's tick labels: only when one series owns the axis.
            right_lines = self.plot.right_lines
            color = right_lines[0].color if len(right_lines) == 1 else self.theme.text
            self.right_title.setStyleSheet(f"color: {color.name(QColor.NameFormat.HexArgb)}")
