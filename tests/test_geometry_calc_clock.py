"""Geometry tools (ruler, set squares, protractor, compass), the scientific
calculator and the clock panel."""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from pdfannotator.calculator import CalcError, evaluate, format_number
from pdfannotator.tools import Tool

CM = 72 / 2.54


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch):
    from pdfannotator.main_window import MainWindow

    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    tab = w.current_tab()
    tab.set_zoom(1.0)
    for _ in range(6):
        app.processEvents()
    yield w, tab
    monkeypatch.setattr(w, "_confirm_close_tab", lambda _tab: True)
    w.close()


def _pump(app, n=6):
    for _ in range(n):
        app.processEvents()


def _send(widget, kind, p, button, buttons):
    QApplication.sendEvent(widget, QMouseEvent(kind, QPointF(p), widget.mapToGlobal(QPointF(p)), button, buttons,
                                               Qt.NoModifier))


@pytest.mark.parametrize("expression, expected", [
    ("2+3×4", 14), ("2π", 2 * math.pi), ("sin(30)", 0.5), ("sin 30", 0.5), ("5!", 120), ("20%", 0.2),
    ("2^3^2", 512), ("−2^2", -4), ("3x√8", 2), ("5 nCr 2", 10), ("6 nPr 2", 30), ("2E3", 2000),
    ("ln(e)", 1), ("log2(8)", 3), ("√(16)", 4), ("2√9", 6), ("(1+2", 3), ("10 mod 3", 1), ("asin(1)", 90),
    ("abs(−5)", 5), ("4²", 16), ("2⁻¹", 0.5), ("cos(90)", 0), ("0.1+0.2", 0.3),
])
def test_calculator_evaluates(expression, expected):
    assert evaluate(expression) == pytest.approx(expected)


def test_calculator_errors_radians_and_format():
    for bad in ("1÷0", "sqrt(−1)", "2+", "tan(90)", "171!"):
        with pytest.raises(CalcError):
            evaluate(bad)
    assert evaluate("sin(pi/2)", degrees=False) == pytest.approx(1)
    assert evaluate("Ans×2", ans=21) == 42
    assert format_number(0.30000000000000004) == "0.3" and format_number(3e20) == "3E20"
    assert format_number(-2.5) == "−2.5"


def test_calculator_panel_keys(app):
    from pdfannotator.calculator import CalculatorPanel

    panel = CalculatorPanel()
    for key in ("2", "x^y", "1", "0", "="):
        panel.press(key)
    assert panel.result.text() == "1024"
    panel.press("+")            # carries on from the answer
    panel.press("1")
    panel.press("=")
    assert panel.result.text() == "1025"
    panel.press("M+")
    assert panel.memory == 1025
    panel.press("AC")
    panel.press("2nd")
    panel.press("sin")          # asin with 2nd
    assert panel.entry.text() == "asin("
    panel.press("MC")


def test_ruler_draws_true_lengths_and_follows_the_page(window, app):
    w, tab = window
    viewport = tab.scroll_area.viewport()
    ruler = w.add_geometry_tool("ruler")
    ruler.center = QPointF(viewport.width() / 2, 160)
    ruler.set_angle(-10, snap=False)
    widget = tab.page_widgets[0]
    w.set_tool(Tool.INK)
    a, b = ruler.edges()[0][1], ruler.edges()[0][2]
    points = [widget.mapFrom(viewport, a + (b - a) * (0.1 + 0.2 * k / 10) + QPointF(1, -8)) for k in range(11)]
    _send(widget, QEvent.MouseButtonPress, points[0], Qt.LeftButton, Qt.LeftButton)
    for p in points[1:]:
        _send(widget, QEvent.MouseMove, p, Qt.NoButton, Qt.LeftButton)
    assert tab.geometry._canvas.readout[0] == "6.20 cm"     # 20 % of the 31 cm body
    _send(widget, QEvent.MouseButtonRelease, points[-1], Qt.LeftButton, Qt.NoButton)
    ink = list(widget.page().annots())[-1]
    first, last = ink.vertices[0][0], ink.vertices[0][-1]
    assert math.dist(first, last) / CM == pytest.approx(6.2, abs=0.01)
    assert math.degrees(math.atan2(-(last[1] - first[1]), last[0] - first[0])) == pytest.approx(10, abs=0.2)
    # zoomed in, the ruler is longer on screen and stays on its spot of the paper
    spot = ruler.anchor
    length_100 = ruler.width()
    tab.set_zoom(2.0)
    _pump(app, 10)
    tab.geometry.sync()
    assert ruler.anchor[0] == spot[0] and ruler.anchor[1] == pytest.approx(spot[1], abs=0.5)
    assert ruler.width() > 1.8 * length_100
    w.set_tool(Tool.SELECT)


def test_compass_draws_an_arc_of_its_radius(window, app):
    w, tab = window
    viewport = tab.scroll_area.viewport()
    compass = w.add_geometry_tool("compass")
    compass.center = QPointF(viewport.width() / 2, 420)
    compass.relayout()
    head = compass.to_viewport(compass._head()) - QPointF(compass.pos())
    _send(compass, QEvent.MouseButtonPress, head, Qt.LeftButton, Qt.LeftButton)
    target = head
    for k in range(1, 30):
        ang = math.radians(-90 - k * 5)
        target = compass.center + QPointF(math.cos(ang), math.sin(ang)) * 90 - QPointF(compass.pos())
        _send(compass, QEvent.MouseMove, target, Qt.NoButton, Qt.LeftButton)
    _send(compass, QEvent.MouseButtonRelease, target, Qt.LeftButton, Qt.NoButton)
    widget = tab.page_widgets[0]
    arc = list(widget.page().annots())[-1]
    centre = widget.to_pdf_point(widget.mapFrom(viewport, compass.center))
    radii = [math.dist((centre.x, centre.y), p) / CM for p in arc.vertices[0]]
    assert min(radii) == pytest.approx(compass.radius, abs=0.01) and max(radii) == pytest.approx(compass.radius,
                                                                                               abs=0.01)


def test_protractor_set_squares_and_units(window, app):
    w, tab = window
    protractor = w.add_geometry_tool("protractor")
    protractor.arms = [20.0, 110.0]
    protractor.relayout()
    assert [e[0] for e in protractor.edges()] == ["arc", "line"]
    for kind in ("square45", "square30"):
        square = w.add_geometry_tool(kind)
        assert len(square.edges()) == 3
    w._set_geometry_unit("in")
    assert tab.geometry.unit == "in"
    w._set_geometry_unit("cm")
    tab.geometry.clear()
    assert not tab.geometry.tools


def test_clock_panel_timer_stopwatch_alarm(app, monkeypatch):
    from PySide6.QtCore import QTime
    from PySide6.QtWidgets import QMainWindow

    from pdfannotator.clock_panel import ClockPanel

    host = QMainWindow()
    host.resize(900, 700)
    panel = ClockPanel(host)
    rang = []
    monkeypatch.setattr(panel.chime, "start", lambda: rang.append("chime"))
    timer = panel.timer_view
    timer.set_duration(60, start=True)
    assert timer.running and "⏱" in timer.status()
    timer.deadline -= 61          # a minute later
    timer.tick()
    assert not timer.running and rang and panel._timer_card is not None
    panel.timer_alert_dismissed()
    assert panel._timer_card is None

    watch = panel.stopwatch_view
    watch.toggle()
    watch.banked = 5.0
    watch.lap_or_reset()
    watch.lap_or_reset()
    watch.toggle()
    assert len(watch.laps) == 2 and watch.list.count() == 2
    watch.lap_or_reset()          # stopped: Reset
    assert watch.elapsed() == 0 and not watch.laps

    alarms = panel.alarm_view
    alarms.alarms = []
    alarms.time_edit.setTime(QTime.currentTime())
    alarms.label_edit.setText("Stand-up")
    alarms.add_alarm()
    rang.clear()
    alarms.tick()
    assert rang == ["chime"] and not alarms.alarms[0]["on"]   # "Once": off after ringing
    alarms.tick()
    assert rang == ["chime"]                                  # not twice in the same minute
    panel.clock_view.tick()
    assert panel.clock_view.time_label.text()
    panel.shutdown()
    host.close()
