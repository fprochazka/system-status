"""The painted line chart and its legend; ``graph_view`` puts them together."""

import math
import re
import time
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QHelpEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from system_status.core.axis import aligned_range, axis_range, stack_values
from system_status.core.layout import ResolvedGraph, assign_colors
from system_status.core.series import Kind, format_axis, format_value
from system_status.core.snapshot import Catalog, Snapshot
from system_status.gui.theme import SERIES_COLORS, Theme

# Space between the plot's left edge and the widget's, for the y-axis labels.
AXIS_SAMPLE_LABEL = "888.8 KiB/s"
CORNER_RADIUS = 6.0
LINE_WIDTH = 1.6
SWATCH_SIZE = 10
LEGEND_ITEM_GAP = 18
# Between a legend swatch and its label, and between the label and its value.
SWATCH_GAP = 7
VALUE_GAP = 6
# Vertical gridlines every this many seconds, counted from the right edge.
TIME_GRID_SECONDS = 60
# The dashes of a right-axis line, in pen widths: dash, gap.
DASH_PATTERN = [3.0, 2.0]
# The widest tick value of each kind, to size the right-hand axis labels.
RIGHT_SAMPLE = {
    Kind.PERCENT: 100,
    Kind.TEMP: 100,
    Kind.FAN: 8888,
    Kind.POWER: 888,
    Kind.RATE: 888,
    Kind.VOLTAGE: 88.8,
    Kind.FREQUENCY: 8880,
}
# How much each further series of one disk is mixed towards white.
SHADE_STEP = 0.35


def small_font(font: QFont) -> QFont:
    small = QFont(font)
    small.setPointSizeF(max(7.0, font.pointSizeF() * 0.8))
    return small


@dataclass(frozen=True)
class Line:
    """One series as the plot and the legend show it."""

    series_id: str
    label: str
    color: QColor
    values: tuple[float, ...]
    latest: float | None
    detail: str | None
    kind: Kind | None = None
    # Drawn dashed against the right-hand axis.
    right: bool = False


def _all_zero(values: tuple[float, ...]) -> bool:
    return all(math.isnan(v) or v == 0 for v in values)


def shade_color(base: QColor, shade: int) -> QColor:
    """Return ``base`` mixed towards white: shade 0 is ``base``, each next shade lighter."""
    if shade <= 0:
        return QColor(base)
    mix = min(0.6, SHADE_STEP * shade)
    return QColor.fromRgbF(
        base.redF() + (1 - base.redF()) * mix,
        base.greenF() + (1 - base.greenF()) * mix,
        base.blueF() + (1 - base.blueF()) * mix,
    )


def build_lines(graph: ResolvedGraph, snapshot: Snapshot | None) -> list[Line]:
    """Return the lines of ``graph`` with their values, without the all-zero ones if asked to.

    Default colours come from ``assign_colors``: per disk, the same in every graph, and by
    position for everything else. A colour set in ``[sensors]`` wins.
    """
    shown = []
    sides = [(s, False) for s in graph.series] + [(s, True) for s in graph.right_series]
    for series, right in sides:
        values = snapshot.values.get(series.info.id, ()) if snapshot else ()
        if graph.config.hide_zero and _all_zero(values):
            continue
        shown.append((series, values, right))
    every = graph.series + graph.right_series
    catalog = snapshot.catalog if snapshot else Catalog(tuple(s.info for s in every))
    colors = assign_colors([series.info.id for series, _, _ in shown], catalog)
    lines = []
    for (series, values, right), (index, shade) in zip(shown, colors, strict=True):
        if series.color:
            color = QColor(series.color)
        else:
            color = shade_color(QColor(SERIES_COLORS[index % len(SERIES_COLORS)]), shade)
        latest = snapshot.latest(series.info.id) if snapshot else None
        detail = snapshot.details.get(series.info.id) if snapshot else None
        kind = graph.right_kind if right else graph.kind
        line = Line(series.info.id, series.label, color, values, latest, detail, kind, right)
        lines.append(line)
    return lines


def value_text(kind: Kind | None, line: Line) -> str:
    value = format_value(line.kind or kind or Kind.PERCENT, line.latest)
    return f"{line.detail} · {value}" if line.detail else value


class PlotWidget(QWidget):
    """The painted chart: gridlines, y-axis labels on the left, a line per series.

    The newest sample is at the right edge; the x-axis spans ``history_seconds`` of the
    snapshot. A single series gets a soft fill under its line. Hovering shows the values of the
    sample under the cursor.
    """

    def __init__(self, theme: Theme, height: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.theme = theme
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.kind: Kind | None = None
        self.lines: list[Line] = []
        self.timestamps: tuple[float, ...] = ()
        self.history_seconds = 300.0
        self.fixed_min: float | None = None
        self.fixed_max: float | None = None
        self._hover_x: float | None = None
        # Every series was left out by ``hide_zero``.
        self.zero_hidden = False
        self.stacked = False
        # Running totals of the lines when stacked; what the plot draws instead of the values.
        self.layers: list[tuple[float, ...]] = []
        # The right-hand axis: set by the graph view when the graph has one.
        self.has_right = False
        self.right_kind: Kind | None = None
        self.right_lines: list[Line] = []
        self.right_min: float | None = None
        self.right_max: float | None = None
        # Gutters shared by every graph of the window, so one moment has one x everywhere.
        # None until the window sets them; then they replace this graph's own needs.
        self.left_gutter: int | None = None
        self.right_gutter: int | None = None

    def axis_width(self) -> int:
        """The left gutter: the shared one, or what this graph's own labels need."""
        return self.left_gutter if self.left_gutter is not None else self.left_needed()

    def left_needed(self) -> int:
        return QFontMetrics(small_font(self.font())).horizontalAdvance(AXIS_SAMPLE_LABEL) + 10

    def right_gutter_width(self) -> int:
        return self.right_gutter if self.right_gutter is not None else self.right_axis_width()

    def right_axis_width(self) -> int:
        """Room for the right-hand tick labels: the widest label the right kind can have."""
        if not self.has_right:
            return 0
        kind = self.right_kind or Kind.PERCENT
        sample = AXIS_SAMPLE_LABEL if kind == Kind.RATE else format_axis(kind, RIGHT_SAMPLE[kind])
        return QFontMetrics(small_font(self.font())).horizontalAdvance(sample) + 10

    def plot_rect(self) -> QRectF:
        top = QFontMetrics(small_font(self.font())).height() / 2
        left = self.axis_width()
        width = self.width() - left - self.right_gutter_width() - 1
        return QRectF(left + 0.5, top + 0.5, width, self.height() - top * 2 - 1)

    def set_data(
        self,
        kind: Kind | None,
        lines: list[Line],
        timestamps: tuple[float, ...],
        history_seconds: float,
        fixed_min: float | None,
        fixed_max: float | None,
    ) -> None:
        self.kind = kind
        self.lines = [line for line in lines if not line.right]
        self.right_lines = [line for line in lines if line.right]
        lines = self.lines
        self.timestamps = timestamps
        self.history_seconds = history_seconds
        self.fixed_min = fixed_min
        self.fixed_max = fixed_max
        self.layers = stack_values([line.values for line in lines]) if self.stacked else []
        self.update()

    def _x(self, rect: QRectF, timestamp: float, newest: float) -> float:
        return rect.right() - (newest - timestamp) / self.history_seconds * rect.width()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QPainter) -> None:
        theme = self.theme
        rect = self.plot_rect()
        background = QPainterPath()
        background.addRoundedRect(rect, CORNER_RADIUS, CORNER_RADIUS)
        painter.fillPath(background, theme.card)

        if self.stacked:
            values = list(self.layers[-1]) if self.layers else []
        else:
            values = [v for line in self.lines for v in line.values]
        low, high, ticks = axis_range(self.kind, values, self.fixed_min, self.fixed_max)
        span = high - low or 1.0

        def y(value: float) -> float:
            return rect.bottom() - (value - low) / span * rect.height()

        # Gridlines and labels.
        painter.setFont(small_font(self.font()))
        metrics = painter.fontMetrics()
        painter.setPen(QPen(theme.grid, 1))
        for tick in ticks:
            if low < tick < high:
                painter.drawLine(QPointF(rect.left(), y(tick)), QPointF(rect.right(), y(tick)))
        seconds = TIME_GRID_SECONDS
        while seconds < self.history_seconds:
            x = rect.right() - seconds / self.history_seconds * rect.width()
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            seconds += TIME_GRID_SECONDS
        painter.setPen(self._axis_color(self.lines))
        for tick in ticks:
            label = format_axis(self.kind or Kind.PERCENT, tick, ticks[-1])
            text_rect = QRectF(0, y(tick) - metrics.height() / 2, rect.left() - 8, metrics.height())
            painter.drawText(
                text_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label
            )

        right_y = None
        if self.has_right:
            right_values = [v for line in self.right_lines for v in line.values]
            right_low, right_high, right_ticks = aligned_range(
                self.right_kind, right_values, len(ticks) - 1, self.right_min, self.right_max
            )
            right_span = right_high - right_low or 1.0

            def right_y(value: float) -> float:
                return rect.bottom() - (value - right_low) / right_span * rect.height()

            painter.setPen(self._axis_color(self.right_lines))
            align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            for tick in right_ticks:
                label = format_axis(self.right_kind or Kind.PERCENT, tick, right_ticks[-1])
                top = right_y(tick) - metrics.height() / 2
                text_rect = QRectF(rect.right() + 8, top, self.width(), metrics.height())
                painter.drawText(text_rect, align, label)

        painter.save()
        painter.setClipPath(background)
        if not (self.lines or self.right_lines) or not self.timestamps:
            painter.setPen(theme.dim_text)
            painter.setFont(self.font())
            if not (self.lines or self.right_lines):
                message = "Nothing above zero" if self.zero_hidden else "No matching sensors"
            else:
                message = "Waiting for data…"
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, message)
        else:
            newest = self.timestamps[-1]
            if self.stacked:
                self._paint_stack(painter, rect, newest, y)
            else:
                single = len(self.lines) == 1
                for line in self.lines:
                    self._paint_line(painter, rect, line, newest, y, fill=single)
            if right_y is not None:
                for line in self.right_lines:
                    self._paint_line(painter, rect, line, newest, right_y, fill=False)
            if self._hover_x is not None:
                self._paint_hover(painter, rect, newest, y, right_y)
        painter.restore()

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(theme.border, 1))
        painter.drawPath(background)

    def _paint_line(self, painter, rect: QRectF, line: Line, newest: float, y, fill: bool) -> None:
        segments: list[list[QPointF]] = [[]]
        for timestamp, value in zip(self.timestamps, line.values, strict=False):
            if math.isnan(value):
                if segments[-1]:
                    segments.append([])
                continue
            segments[-1].append(QPointF(self._x(rect, timestamp, newest), y(value)))
        for points in segments:
            if not points:
                continue
            path = QPainterPath(points[0])
            for point in points[1:]:
                path.lineTo(point)
            if fill and len(points) > 1:
                area = QPainterPath(path)
                area.lineTo(points[-1].x(), rect.bottom())
                area.lineTo(points[0].x(), rect.bottom())
                area.closeSubpath()
                fill_color = QColor(line.color)
                fill_color.setAlpha(70 if self.theme.dark else 45)
                painter.fillPath(area, fill_color)
            pen = QPen(line.color, LINE_WIDTH)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            if line.right:
                pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                pen.setDashPattern(DASH_PATTERN)
            painter.strokePath(path, pen)

    def _axis_color(self, lines: list[Line]) -> QColor:
        """In a graph with two axes, tint an axis's labels with its series colour when it has
        one series, so a reader can tell which axis a line belongs to; neutral otherwise."""
        if self.has_right and len(lines) == 1:
            return QColor(lines[0].color)
        return QColor(self.theme.dim_text)

    def _paint_stack(self, painter: QPainter, rect: QRectF, newest: float, y) -> None:
        """Fill each layer between the one below it and its own running total."""
        xs = [self._x(rect, timestamp, newest) for timestamp in self.timestamps]
        below: tuple[float, ...] = tuple(0.0 for _ in xs)
        for line, layer in zip(self.lines, self.layers, strict=True):
            segment: list[int] = []
            segments = []
            for i in range(min(len(xs), len(layer))):
                if math.isnan(layer[i]):
                    if segment:
                        segments.append(segment)
                    segment = []
                else:
                    segment.append(i)
            if segment:
                segments.append(segment)
            for indices in segments:
                top = [QPointF(xs[i], y(layer[i])) for i in indices]
                area = QPainterPath(top[0])
                for point in top[1:]:
                    area.lineTo(point)
                edge = QPainterPath(area)
                for i in reversed(indices):
                    base = below[i] if i < len(below) and not math.isnan(below[i]) else 0.0
                    area.lineTo(xs[i], y(base))
                area.closeSubpath()
                fill = QColor(line.color)
                fill.setAlpha(150 if self.theme.dark else 120)
                painter.fillPath(area, fill)
                pen = QPen(line.color, 1.2)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.strokePath(edge, pen)
            below = layer

    def _hover_index(self, rect: QRectF) -> int | None:
        if self._hover_x is None or not self.timestamps:
            return None
        newest = self.timestamps[-1]
        target = newest - (rect.right() - self._hover_x) / rect.width() * self.history_seconds
        index = min(range(len(self.timestamps)), key=lambda i: abs(self.timestamps[i] - target))
        if abs(self.timestamps[index] - target) > self.history_seconds / 50 + 2:
            return None
        return index

    def _paint_hover(self, painter: QPainter, rect: QRectF, newest: float, y, right_y) -> None:
        index = self._hover_index(rect)
        if index is None:
            return
        x = self._x(rect, self.timestamps[index], newest)
        painter.setPen(QPen(self.theme.dim_text, 1))
        painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        heights = self.layers if self.stacked else [line.values for line in self.lines]
        points = [(line, values, y) for line, values in zip(self.lines, heights, strict=False)]
        if right_y is not None:
            points += [(line, line.values, right_y) for line in self.right_lines]
        for line, values, to_y in points:
            value = values[index] if index < len(values) else math.nan
            if math.isnan(value):
                continue
            painter.setPen(QPen(self.theme.card, 1.5))
            painter.setBrush(line.color)
            painter.drawEllipse(QPointF(x, to_y(value)), 3.5, 3.5)

    def hover_text(self) -> str | None:
        index = self._hover_index(self.plot_rect())
        if index is None:
            return None
        age = self.timestamps[-1] - self.timestamps[index]
        when = time.strftime("%H:%M:%S", time.localtime(self.timestamps[index]))
        rows = [f"{when}  ({age:.0f} s ago)" if age >= 0.5 else f"{when}  (now)"]
        for line in self.lines + self.right_lines:
            value = line.values[index] if index < len(line.values) else math.nan
            kind = line.kind or self.kind or Kind.PERCENT
            shown = format_value(kind, None if math.isnan(value) else value)
            rows.append(f"{line.label}: {shown}")
        if self.stacked and self.layers and index < len(self.layers[-1]):
            total = self.layers[-1][index]
            shown = format_value(self.kind or Kind.PERCENT, None if math.isnan(total) else total)
            rows.append(f"Total: {shown}")
        return "\n".join(rows)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        rect = self.plot_rect()
        x = event.position().x()
        self._hover_x = x if rect.left() <= x <= rect.right() else None
        self.update()
        text = self.hover_text()
        if text:
            QToolTip.showText(event.globalPosition().toPoint(), text, self)
        else:
            QToolTip.hideText()

    def leaveEvent(self, event: QEvent) -> None:
        self._hover_x = None
        QToolTip.hideText()
        self.update()

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            assert isinstance(event, QHelpEvent)
            # Tooltips follow the mouse in mouseMoveEvent; the delayed one would flicker.
            return True
        return super().event(event)


def flow_rows(widths: list[int], available: int, gap: int) -> list[list[int]]:
    """Place items of ``widths`` left to right, wrapping like words: return each row's items.

    An item wider than ``available`` gets a row of its own.
    """
    rows: list[list[int]] = []
    used = 0
    for index, width in enumerate(widths):
        if rows and used + gap + width <= available:
            rows[-1].append(index)
            used += gap + width
        else:
            rows.append([index])
            used = width
    return rows


def _digits_as_eights(text: str) -> str:
    return re.sub(r"[0-9]", "8", text)


class LegendWidget(QWidget):
    """Swatch, label and current value of each line, flowing left to right like words.

    Each item takes its natural width, so short ones don't waste a column. Values change every
    second, so each value's width is reserved as the widest it has been, counting every digit
    as an "8": a value going from 9.9 to 10.0 widens its item once, not back and forth.
    """

    def __init__(self, theme: Theme, parent: QWidget | None = None):
        super().__init__(parent)
        self.theme = theme
        self.kind: Kind | None = None
        self.lines: list[Line] = []
        self.left_inset = 0
        self._value_widths: dict[str, int] = {}
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def set_lines(self, kind: Kind | None, lines: list[Line]) -> None:
        relayout = [line.series_id for line in lines] != [line.series_id for line in self.lines]
        self.kind = kind
        self.lines = lines
        value_metrics = QFontMetrics(small_font(self.font()))
        for line in lines:
            width = value_metrics.horizontalAdvance(_digits_as_eights(value_text(kind, line)))
            if width > self._value_widths.get(line.series_id, 0):
                self._value_widths[line.series_id] = width
                relayout = True
        if relayout:
            self.updateGeometry()
        self.update()

    def _label_widths(self) -> list[int]:
        metrics = QFontMetrics(self.font())
        return [metrics.horizontalAdvance(line.label) for line in self.lines]

    def _item_widths(self) -> list[int]:
        return [
            SWATCH_SIZE + SWATCH_GAP + label + VALUE_GAP + self._value_widths.get(line.series_id, 0)
            for line, label in zip(self.lines, self._label_widths(), strict=True)
        ]

    def _rows(self, width: int) -> list[list[int]]:
        return flow_rows(self._item_widths(), width - self.left_inset, LEGEND_ITEM_GAP)

    def _row_height(self) -> int:
        return QFontMetrics(self.font()).height() + 6

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        if not self.lines:
            return 0
        return len(self._rows(width)) * self._row_height()

    def sizeHint(self) -> QSize:
        return QSize(300, self.heightForWidth(max(self.width(), 300)))

    def resizeEvent(self, event) -> None:
        # The height depends on the width; tell the layout when a resize changes it.
        if self.height() != self.heightForWidth(self.width()):
            self.updateGeometry()
        super().resizeEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QPainter) -> None:
        if not self.lines:
            return
        row_height = self._row_height()
        small = small_font(self.font())
        labels = self._label_widths()
        widths = self._item_widths()
        for row_index, row in enumerate(self._rows(self.width())):
            x = float(self.left_inset)
            top = row_index * row_height
            for index in row:
                line = self.lines[index]
                swatch = QRectF(x, top + (row_height - SWATCH_SIZE) / 2, SWATCH_SIZE, SWATCH_SIZE)
                if line.right:
                    # A right-axis series: a dashed stroke like its line, under a small arrow
                    # pointing at the right-hand axis.
                    pen = QPen(line.color, 2.0)
                    pen.setDashPattern([2.0, 1.5])
                    painter.setPen(pen)
                    middle = swatch.center().y() + 2
                    painter.drawLine(
                        QPointF(swatch.left(), middle), QPointF(swatch.right(), middle)
                    )
                    arrow = QPen(line.color, 1.4)
                    arrow.setCapStyle(Qt.PenCapStyle.RoundCap)
                    painter.setPen(arrow)
                    tip = QPointF(swatch.right(), swatch.top() + 1.5)
                    painter.drawLine(QPointF(swatch.left() + 1, tip.y()), tip)
                    painter.drawLine(QPointF(tip.x() - 3, tip.y() - 2.5), tip)
                    painter.drawLine(QPointF(tip.x() - 3, tip.y() + 2.5), tip)
                else:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(line.color)
                    painter.drawRoundedRect(swatch, 3, 3)
                text_x = x + SWATCH_SIZE + SWATCH_GAP
                painter.setFont(self.font())
                painter.setPen(self.theme.text)
                label_rect = QRectF(text_x, top, labels[index] + 1, row_height)
                painter.drawText(label_rect, Qt.AlignmentFlag.AlignVCenter, line.label)
                painter.setFont(small)
                painter.setPen(self.theme.dim_text)
                value_left = text_x + labels[index] + VALUE_GAP
                value_rect = QRectF(value_left, top, x + widths[index] - value_left + 1, row_height)
                align = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
                painter.drawText(value_rect, align, value_text(self.kind, line))
                x += widths[index] + LEGEND_ITEM_GAP
