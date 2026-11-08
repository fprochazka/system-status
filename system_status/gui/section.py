"""A collapsible block of graphs with a clickable header, like GNOME System Monitor's."""

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QEnterEvent, QFont, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from system_status.gui.theme import Theme

CHEVRON_SIZE = 7.0
HEADER_PADDING = 8


class SectionHeader(QWidget):
    """A chevron and a bold title; a click toggles the section."""

    clicked = Signal()

    def __init__(self, title: str, theme: Theme, parent: QWidget | None = None):
        super().__init__(parent)
        self.title = title
        self.theme = theme
        self.expanded = True
        self._hovered = False
        font = QFont(self.font())
        font.setWeight(QFont.Weight.Bold)
        font.setPointSizeF(font.pointSizeF() * 1.08)
        self.setFont(font)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self.fontMetrics().height() + 2 * HEADER_PADDING)

    def sizeHint(self) -> QSize:
        return QSize(200, self.height())

    def set_expanded(self, expanded: bool) -> None:
        self.expanded = expanded
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()

    def enterEvent(self, event: QEnterEvent) -> None:
        self._hovered = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            if self._hovered:
                hover = QColor(self.theme.text)
                hover.setAlpha(14)
                path = QPainterPath()
                path.addRoundedRect(QRectF(self.rect()), 6, 6)
                painter.fillPath(path, hover)
            center_y = self.height() / 2
            center_x = HEADER_PADDING + CHEVRON_SIZE
            half = CHEVRON_SIZE / 2
            pen = QPen(self.theme.dim_text, 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            if self.expanded:
                points = [
                    QPointF(center_x - half, center_y - half / 2),
                    QPointF(center_x, center_y + half / 2),
                    QPointF(center_x + half, center_y - half / 2),
                ]
            else:
                points = [
                    QPointF(center_x - half / 2, center_y - half),
                    QPointF(center_x + half / 2, center_y),
                    QPointF(center_x - half / 2, center_y + half),
                ]
            painter.drawPolyline(points)
            painter.setPen(self.theme.text)
            text_left = int(center_x + CHEVRON_SIZE + 8)
            text_rect = self.rect().adjusted(text_left, 0, -HEADER_PADDING, 0)
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, self.title)
        finally:
            painter.end()


class CollapsibleSection(QWidget):
    toggled = Signal(bool)

    def __init__(self, title: str, theme: Theme, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.header = SectionHeader(title, theme)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 2, 0, 10)
        self.body_layout.setSpacing(16)
        layout.addWidget(self.header)
        layout.addWidget(self.body)
        self.header.clicked.connect(self.toggle)

    @property
    def expanded(self) -> bool:
        return self.header.expanded

    def add_widget(self, widget: QWidget) -> None:
        self.body_layout.addWidget(widget)

    def set_expanded(self, expanded: bool) -> None:
        self.header.set_expanded(expanded)
        self.body.setVisible(expanded)

    def toggle(self) -> None:
        self.set_expanded(not self.expanded)
        self.toggled.emit(self.expanded)
