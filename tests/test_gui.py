import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from system_status.core.sampler import build_sources  # noqa: E402
from system_status.gui.app import GuiApp, create_qt_app  # noqa: E402
from system_status.gui.graph_view import GraphView  # noqa: E402
from tests.sysfs import FakeSys, build_machine  # noqa: E402

CONFIG = """
[sensors]
"hwmon:k10temp/Tctl" = { label = "CPU package" }

[[section]]
title = "CPU"
  [[section.graph]]
  title = "Temperature"
  series = ["hwmon:k10temp/*"]

[[section]]
title = "Fans"
collapsed = true
  [[section.graph]]
  title = "Speed"
  series = ["hwmon:nct6687/fan*"]
  hide_zero = true
"""


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return create_qt_app()


@pytest.fixture
def machine(tmp_path):
    fake = FakeSys(tmp_path / "sys")
    build_machine(fake)
    return lambda: build_sources(fake.root)


def _make_app(qt_app, machine, path) -> GuiApp:
    app = GuiApp(path, qt_app, machine, theme_override="light")
    app.sampler.sample()
    app.sampler.sample()
    app.refresh()
    return app


def test_window_from_default_layout(qt_app, machine, tmp_path):
    app = _make_app(qt_app, machine, tmp_path / "missing.toml")
    try:
        titles = [section.header.title for section in app.window.sections]
        assert titles[:2] == ["CPU", "Memory"]
        assert "GPU · AMD Radeon Graphics" in titles
        assert "Disks" in titles
        app.window.resize(760, 900)
        image = app.window.grab()
        assert not image.isNull()
    finally:
        app.sampler.close()
        app.window.close()


def test_window_from_config_and_reload(qt_app, machine, tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text(CONFIG)
    app = _make_app(qt_app, machine, path)
    try:
        assert [s.header.title for s in app.window.sections] == ["CPU", "Fans"]
        assert not app.window.sections[1].expanded
        temperature: GraphView = app.window.graphs[0]
        labels = [line.label for line in temperature.legend.lines]
        assert labels == ["CPU package", "CCD 1", "CCD 2"]
        # fan2 has been 0 the whole time.
        fans = app.window.graphs[1]
        assert [line.label for line in fans.legend.lines] == ["Fan 1", "Fan 11"]

        path.write_text(CONFIG.replace('title = "Fans"', 'titel = "Fans"'))
        app.reload()
        assert app.window.banner.isVisibleTo(app.window)
        assert 'unknown key "titel"' in app.window.banner.text()
        assert [s.header.title for s in app.window.sections] == ["CPU", "Fans"]

        path.write_text(CONFIG.replace('title = "Fans"', 'title = "Coolers"'))
        app.reload()
        assert not app.window.banner.isVisibleTo(app.window)
        assert [s.header.title for s in app.window.sections] == ["CPU", "Coolers"]
    finally:
        app.sampler.close()
        app.window.close()


def test_toggled_section_keeps_its_state_across_reloads(qt_app, machine, tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text(CONFIG)
    app = _make_app(qt_app, machine, path)
    try:
        app.window.sections[0].toggle()
        path.write_text(CONFIG.replace('title = "Temperature"', 'title = "Temps"'))
        app.reload()
        assert not app.window.sections[0].expanded
    finally:
        app.sampler.close()
        app.window.close()


def test_heatmap_ramp_and_rows(qt_app):
    from system_status.gui.heatmap import CLUSTER_GAP, ramp_color, row_layout
    from system_status.gui.theme import DARK, LIGHT

    for theme in (LIGHT, DARK):
        assert ramp_color(theme, 0).name() == theme.card.name()
        assert ramp_color(theme, 1).name() == "#e01b24"
        assert ramp_color(theme, 2).name() == "#e01b24"
    rows = row_layout(["CCD 1", "CCD 1", "CCD 2"], row_height=7)
    assert rows == [(0, 0.0), (0, 7.0), (1, 14.0 + CLUSTER_GAP)]


def test_heatmap_graph_renders(qt_app, machine, tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text(
        '[[section]]\ntitle = "CPU"\n[[section.graph]]\ntitle = "Cores"\n'
        'series = ["cpu:pcore*"]\ntype = "heatmap"\n'
    )
    app = _make_app(qt_app, machine, path)
    try:
        view = app.window.graphs[0]
        assert view.heatmap_mode
        assert not view.legend.isVisibleTo(view)
        assert len(view.plot.lines) >= 1
        app.window.resize(760, 400)
        assert not app.window.grab().isNull()
    finally:
        app.sampler.close()
        app.window.close()


def test_dual_axis_graph_renders(qt_app, machine, tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text(
        '[[section]]\ntitle = "CPU"\n[[section.graph]]\ntitle = "Temperature"\n'
        'series = ["hwmon:k10temp/*"]\nright = ["hwmon:nct6687/fan1"]\n'
    )
    app = _make_app(qt_app, machine, path)
    try:
        view = app.window.graphs[0]
        assert view.plot.has_right
        assert [line.label for line in view.plot.right_lines] == ["Fan 1"]
        assert [line.right for line in view.legend.lines][-1] is True
        assert view.plot.right_axis_width() > 0
        # The right title replaces the current value at the right end of the title row.
        assert view.right_title.text() == "Fan 1"
        assert view.value.isHidden()
        # Both titles line up with the plot: the left one starts at its left edge, the right one
        # ends at its right edge, not over the right axis's labels.
        app.window.show()
        qt_app.processEvents()
        rect = view.plot.plot_rect()
        plot_left = view.plot.mapTo(view, rect.topLeft().toPoint()).x()
        plot_right = view.plot.mapTo(view, rect.topRight().toPoint()).x()
        title = view.right_title.geometry()
        assert abs(view.title.geometry().left() - plot_left) <= 1
        # QRect.right() is the last pixel inside; x + width is the edge, like the plot's.
        assert abs(title.x() + title.width() - plot_right) <= 1
        app.window.resize(760, 400)
        assert not app.window.grab().isNull()
    finally:
        app.sampler.close()
        app.window.close()


def test_flow_rows_wrap_like_words():
    from system_status.gui.graph_widget import flow_rows

    assert flow_rows([40, 40, 40], available=200, gap=10) == [[0, 1, 2]]
    assert flow_rows([80, 50, 100, 30], available=150, gap=10) == [[0, 1], [2, 3]]
    # An item wider than the row gets a row of its own.
    assert flow_rows([300, 20], available=150, gap=10) == [[0], [1]]
    assert flow_rows([], available=150, gap=10) == []


def test_legend_height_follows_width_and_values_do_not_reflow(qt_app):
    from PySide6.QtGui import QColor

    from system_status.core.series import Kind
    from system_status.gui.graph_widget import LegendWidget, Line
    from system_status.gui.theme import LIGHT

    def lines(value: float) -> list[Line]:
        return [
            Line(f"x:{n}", f"Sensor {n}", QColor("#3584e4"), (value,), value, None)
            for n in range(6)
        ]

    legend = LegendWidget(LIGHT)
    legend.set_lines(Kind.TEMP, lines(9.5))
    row = legend.heightForWidth(5000)
    assert row > 0
    assert legend.heightForWidth(150) == 6 * row
    assert row < legend.heightForWidth(400) < 6 * row
    widths = legend._item_widths()
    # 10.0 has one digit more than 9.5: wider once...
    legend.set_lines(Kind.TEMP, lines(10.0))
    wider = legend._item_widths()
    assert all(w > v for w, v in zip(wider, widths, strict=True))
    # ...and back at 9.5 the reserved width stays, so the items don't jump around.
    legend.set_lines(Kind.TEMP, lines(9.5))
    assert legend._item_widths() == wider


def test_all_graphs_share_the_plot_x_range(qt_app, machine, tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text(
        '[[section]]\ntitle = "CPU"\n'
        '[[section.graph]]\ntitle = "Usage"\nseries = ["cpu:total"]\n'
        '[[section.graph]]\ntitle = "Cores"\nseries = ["cpu:pcore*"]\ntype = "heatmap"\n'
        '[[section.graph]]\ntitle = "Temperature"\nseries = ["hwmon:k10temp/*"]\n'
        'right = ["hwmon:nct6687/fan1"]\n'
        '[[section]]\ntitle = "Disks"\ncollapsed = true\n'
        '[[section.graph]]\ntitle = "Throughput"\nseries = ["disk:*/read"]\nstacked = true\n'
    )
    app = _make_app(qt_app, machine, path)
    try:
        app.window.resize(760, 900)
        app.window.show()
        app.window.sections[1].set_expanded(True)
        qt_app.processEvents()
        ranges = set()
        for view in app.window.graphs:
            rect = view.plot.plot_rect()
            left = view.plot.mapTo(app.window, rect.topLeft().toPoint()).x()
            ranges.add((left, round(rect.width(), 3)))
        assert len(app.window.graphs) == 4
        assert len(ranges) == 1, ranges
        # The right gutter is reserved on every graph because one graph has a right axis.
        assert all(view.plot.right_gutter_width() > 0 for view in app.window.graphs)
    finally:
        app.sampler.close()
        app.window.close()


def test_unknown_override_is_a_warning_and_the_layout_applies(qt_app, machine, tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text(
        CONFIG.replace("[sensors]\n", '[sensors]\n"disk:Gone/temp" = { label = "x" }\n')
    )
    app = _make_app(qt_app, machine, path)
    try:
        assert [s.header.title for s in app.window.sections] == ["CPU", "Fans"]
        assert app.window.banner.isVisibleTo(app.window)
        assert app.window.banner.text().startswith("Warning: ")
        assert "disk:Gone/temp" in app.window.banner.text()
        assert not app.window._banner_is_error

        path.write_text(CONFIG)
        app.reload()
        assert not app.window.banner.isVisibleTo(app.window)
    finally:
        app.sampler.close()
        app.window.close()
