"""A heatmap graph: one thin row per series, time on the x-axis, colour for the value."""

import math
import time

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QHelpEvent,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from system_status.core.series import Kind, format_axis, format_value
from system_status.gui.graph_widget import AXIS_SAMPLE_LABEL, CORNER_RADIUS, Line, small_font
from system_status.gui.theme import Theme

# Space between two clusters of rows, such as two CCDs.
CLUSTER_GAP = 6
ROW_GAP = 1

# The colour ramp from no load to full load. Position 0 is the card colour, so an idle row
# disappears into the background in both themes; then light blue, blue, purple and red.
RAMP_STOPS = (
    (0.0, None),
    (0.15, ("#3584e4", 0.35)),
    (0.45, ("#3584e4", 1.0)),
    (0.75, ("#9141ac", 1.0)),
    (1.0, ("#e01b24", 1.0)),
)


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor.fromRgbF(
        a.redF() + (b.redF() - a.redF()) * t,
        a.greenF() + (b.greenF() - a.greenF()) * t,
        a.blueF() + (b.blueF() - a.blueF()) * t,
    )


def _stop_color(theme: Theme, stop) -> QColor:
    if stop is None:
        return QColor(theme.card)
    color, strength = stop
    return _mix(theme.card, QColor(color), strength)


def ramp_color(theme: Theme, t: float) -> QColor:
    """Return the ramp colour at ``t`` in 0..1, values outside are clamped."""
    t = min(1.0, max(0.0, t))
    for (start, low), (end, high) in zip(RAMP_STOPS, RAMP_STOPS[1:], strict=False):
        if t <= end:
            local = (t - start) / (end - start) if end > start else 0.0
            return _mix(_stop_color(theme, low), _stop_color(theme, high), local)
    return _stop_color(theme, RAMP_STOPS[-1][1])


def row_layout(clusters: list[str | None], row_height: int) -> list[tuple[int, float]]:
    """Return ``(cluster index, top)`` of each row: a gap wherever the cluster changes."""
    rows = []
    cluster_index = 0
    top = 0.0
    for index, cluster in enumerate(clusters):
        if index > 0 and cluster != clusters[index - 1]:
            cluster_index += 1
            top += CLUSTER_GAP
        rows.append((cluster_index, top))
        top += row_height
    return rows


class HeatmapWidget(QWidget):
    """Rows of coloured cells. Rows of one cluster share a rounded card; the cluster's name
    is in the left margin, where line graphs have their y-axis labels.
    """

    def __init__(self, theme: Theme, row_height: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.theme = theme
        self.row_height = row_height
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.kind: Kind | None = None
        self.lines: list[Line] = []
        self.clusters: list[str | None] = []
        self.timestamps: tuple[float, ...] = ()
        self.history_seconds = 300.0
        self.interval_seconds = 1.0
        self.low = 0.0
        self.high = 100.0
        self._hover: QPointF | None = None
        self._fit_height()

    # Gutters shared by every graph of the window; see PlotWidget.
    left_gutter: int | None = None
    right_gutter: int | None = None

    def axis_width(self) -> int:
        return self.left_gutter if self.left_gutter is not None else self.left_needed()

    def left_needed(self) -> int:
        """Room for the cluster names, at least as much as a line graph's tick labels."""
        metrics = QFontMetrics(small_font(self.font()))
        names = [metrics.horizontalAdvance(c) + 10 for c in self.clusters if c]
        return max([metrics.horizontalAdvance(AXIS_SAMPLE_LABEL) + 10, *names])

    def right_axis_width(self) -> int:
        return 0

    def right_gutter_width(self) -> int:
        return self.right_gutter or 0

    def _plot_width(self) -> float:
        # The same as PlotWidget.plot_rect, to the half pixel.
        return self.width() - self.axis_width() - self.right_gutter_width() - 1

    def plot_rect(self) -> QRectF:
        """Where the cells go, across all rows; its x and width match every line graph's."""
        return QRectF(self._plot_left(), 0.5, self._plot_width(), self.height() - 1)

    def _fit_height(self) -> None:
        rows = row_layout(self.clusters, self.row_height)
        height = (rows[-1][1] + self.row_height) if rows else 40
        self.setFixedHeight(int(math.ceil(height)) + 1)

    def set_data(
        self,
        kind: Kind | None,
        lines: list[Line],
        clusters: list[str | None],
        timestamps: tuple[float, ...],
        history_seconds: float,
        interval_seconds: float,
        fixed_min: float | None,
        fixed_max: float | None,
    ) -> None:
        relayout = clusters != self.clusters
        self.kind = kind
        self.lines = lines
        self.clusters = clusters
        self.timestamps = timestamps
        self.history_seconds = history_seconds
        self.interval_seconds = interval_seconds
        values = [v for line in lines for v in line.values if math.isfinite(v)]
        if kind == Kind.PERCENT or not values:
            low, high = 0.0, 100.0
        else:
            low, high = min(values), max(values)
        self.low = fixed_min if fixed_min is not None else low
        self.high = fixed_max if fixed_max is not None else high
        if self.high <= self.low:
            self.high = self.low + 1
        if relayout:
            self._fit_height()
        self.update()

    def _plot_left(self) -> float:
        return self.axis_width() + 0.5

    def _x(self, timestamp: float, newest: float) -> float:
        left = self._plot_left()
        width = self._plot_width()
        return left + width - (newest - timestamp) / self.history_seconds * width

    def _cluster_rects(self) -> list[tuple[str | None, QRectF]]:
        rows = row_layout(self.clusters, self.row_height)
        rects: list[tuple[str | None, QRectF]] = []
        left = self._plot_left()
        width = self._plot_width()
        for (cluster_index, top), cluster in zip(rows, self.clusters, strict=True):
            bottom = top + self.row_height - ROW_GAP
            if cluster_index < len(rects):
                _name, rect = rects[cluster_index]
                rect.setBottom(bottom + 0.5)
            else:
                rects.append((cluster, QRectF(left, top + 0.5, width, bottom - top)))
        return rects

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QPainter) -> None:
        theme = self.theme
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cards = self._cluster_rects()
        if not self.lines:
            rect = QRectF(self._plot_left(), 0.5, self._plot_width(), 38)
            path = QPainterPath()
            path.addRoundedRect(rect, CORNER_RADIUS, CORNER_RADIUS)
            painter.fillPath(path, theme.card)
            painter.setPen(theme.dim_text)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "No matching sensors")
            return
        clip = QPainterPath()
        for _name, rect in cards:
            path = QPainterPath()
            path.addRoundedRect(rect, CORNER_RADIUS / 2, CORNER_RADIUS / 2)
            painter.fillPath(path, theme.card)
            clip.addPath(path)

        painter.save()
        painter.setClipPath(clip)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        if self.timestamps:
            newest = self.timestamps[-1]
            xs = [self._x(t, newest) for t in self.timestamps]
            plot_width = self._plot_width()
            step = self.interval_seconds / self.history_seconds * plot_width
            starts = [xs[i - 1] if i > 0 else xs[0] - step for i in range(len(xs))]
            span = self.high - self.low
            rows = row_layout(self.clusters, self.row_height)
            for line, (_cluster, top) in zip(self.lines, rows, strict=True):
                height = self.row_height - ROW_GAP
                for i, value in enumerate(line.values[: len(xs)]):
                    if math.isnan(value):
                        continue
                    x0, x1 = round(starts[i]), round(xs[i])
                    if x1 <= x0:
                        x1 = x0 + 1
                    color = ramp_color(theme, (value - self.low) / span)
                    painter.fillRect(int(x0), int(top + 0.5), x1 - x0, int(height), color)
            hover = self._hover_cell()
            if hover is not None:
                painter.setPen(QPen(theme.text, 1))
                x = xs[hover[1]]
                painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        painter.restore()

        painter.setPen(QPen(theme.border, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setFont(small_font(self.font()))
        for _name, rect in cards:
            painter.drawRoundedRect(rect, CORNER_RADIUS / 2, CORNER_RADIUS / 2)
        painter.setPen(theme.dim_text)
        for name, rect in cards:
            if name:
                label_rect = QRectF(0, rect.top(), rect.left() - 8, rect.height())
                align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                painter.drawText(label_rect, align, name)

    def _hover_cell(self) -> tuple[int, int] | None:
        """Return ``(row, sample index)`` under the mouse."""
        if self._hover is None or not self.timestamps or not self.lines:
            return None
        rows = row_layout(self.clusters, self.row_height)
        y = self._hover.y()
        row = next(
            (i for i, (_c, top) in enumerate(rows) if top <= y < top + self.row_height), None
        )
        if row is None or self._hover.x() < self._plot_left():
            return None
        newest = self.timestamps[-1]
        left = self._plot_left()
        width = self._plot_width()
        target = newest - (left + width - self._hover.x()) / width * self.history_seconds
        index = min(range(len(self.timestamps)), key=lambda i: abs(self.timestamps[i] - target))
        if abs(self.timestamps[index] - target) > self.interval_seconds * 1.5:
            return None
        return row, index

    def hover_text(self) -> str | None:
        cell = self._hover_cell()
        if cell is None:
            return None
        row, index = cell
        line = self.lines[row]
        value = line.values[index] if index < len(line.values) else math.nan
        when = time.strftime("%H:%M:%S", time.localtime(self.timestamps[index]))
        cluster = self.clusters[row]
        name = f"{line.label} · {cluster}" if cluster else line.label
        shown = format_value(self.kind or Kind.PERCENT, None if math.isnan(value) else value)
        return f"{name}\n{when}: {shown}"

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._hover = event.position()
        self.update()
        text = self.hover_text()
        if text:
            QToolTip.showText(event.globalPosition().toPoint(), text, self)
        else:
            QToolTip.hideText()

    def leaveEvent(self, event: QEvent) -> None:
        self._hover = None
        QToolTip.hideText()
        self.update()

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            assert isinstance(event, QHelpEvent)
            return True
        return super().event(event)


class RampKey(QWidget):
    """The colour ramp with its low and high value, for the title row of a heatmap."""

    BAR_WIDTH = 72
    BAR_HEIGHT = 8

    def __init__(self, theme: Theme, parent: QWidget | None = None):
        super().__init__(parent)
        self.theme = theme
        self.kind: Kind = Kind.PERCENT
        self.low = 0.0
        self.high = 100.0
        self.setFont(small_font(self.font()))

    def set_range(self, kind: Kind | None, low: float, high: float) -> None:
        self.kind = kind or Kind.PERCENT
        if (self.low, self.high) != (low, high):
            self.low, self.high = low, high
            self.updateGeometry()
        self.update()

    def _labels(self) -> tuple[str, str]:
        return format_axis(self.kind, self.low, self.high), format_axis(self.kind, self.high)

    def sizeHint(self) -> QSize:
        metrics = self.fontMetrics()
        low, high = self._labels()
        width = metrics.horizontalAdvance(low) + metrics.horizontalAdvance(high)
        return QSize(width + self.BAR_WIDTH + 12, metrics.height())

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            metrics = self.fontMetrics()
            low, high = self._labels()
            painter.setPen(self.theme.dim_text)
            x = self.width() - self.sizeHint().width()
            align = Qt.AlignmentFlag.AlignVCenter
            painter.drawText(QRectF(x, 0, 100, self.height()), align, low)
            x += metrics.horizontalAdvance(low) + 6
            bar = QRectF(x, (self.height() - self.BAR_HEIGHT) / 2, self.BAR_WIDTH, self.BAR_HEIGHT)
            gradient = QLinearGradient(bar.topLeft(), bar.topRight())
            for position, _stop in RAMP_STOPS:
                gradient.setColorAt(position, ramp_color(self.theme, position))
            path = QPainterPath()
            path.addRoundedRect(bar, 3, 3)
            painter.fillPath(path, gradient)
            painter.setPen(QPen(self.theme.border, 1))
            painter.drawPath(path)
            painter.setPen(self.theme.dim_text)
            painter.drawText(QRectF(bar.right() + 6, 0, 100, self.height()), align, high)
        finally:
            painter.end()
