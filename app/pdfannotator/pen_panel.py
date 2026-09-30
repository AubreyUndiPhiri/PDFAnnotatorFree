"""The pen panel: everything for freehand writing in one small floating bar.

It appears while the ribbon (the toolbars) is hidden and a pen tool is in
use: the Pen, Marker and Eraser, a palette of colours, three line widths,
smoothing and pressure, and undo / redo. Drag it anywhere in the window by
its grip (or any empty spot), and turn it horizontal or vertical with its
rotate button (or a double-click on the grip). Where it sits and which way
it faces are remembered.

The panel holds no state of its own: its buttons are the main window's
actions, and colour / width changes go through the window, so the toolbars
and the panel always agree."""
from PySide6.QtCore import QPoint, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QBoxLayout, QButtonGroup, QFrame, QGridLayout, QToolButton, QWidget

from . import icons, theme
from .tools import Tool

PEN_TOOLS = (Tool.INK, Tool.MARKER, Tool.ERASER)
PANEL_TOOLS = (Tool.INK, Tool.MARKER, Tool.ERASER, Tool.SELECT)

# ink colours, then soft highlighter tones (the Marker draws them see-through)
PALETTE = ["#141414", "#2563eb", "#dc2626", "#16a34a", "#7c3aed", "#ea580c", "#ffeb3b", "#f472b6"]
WIDTHS = {Tool.INK: (1.0, 2.0, 4.0), Tool.MARKER: (6.0, 10.0, 16.0)}


def _chip(color, size=22):
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QColor(0, 0, 0, 50) if theme.mode == theme.LIGHT else QColor(255, 255, 255, 60))
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(1.5, 1.5, size - 3, size - 3))
    p.end()
    return QIcon(pix)


def _dot(diameter, size=22):
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(theme.ICON))
    p.drawEllipse(QRectF((size - diameter) / 2, (size - diameter) / 2, diameter, diameter))
    p.end()
    return QIcon(pix)


class _Grip(QWidget):
    """Six dots that say "drag me"; the panel itself handles the drag."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.vertical = False
        self.setFixedSize(14, 26)

    def set_vertical(self, vertical):
        self.vertical = vertical
        self.setFixedSize(26, 14) if vertical else self.setFixedSize(14, 26)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.TEXT_MUTED))
        cols, rows = (3, 2) if self.vertical else (2, 3)
        x0 = (self.width() - (cols - 1) * 6) / 2
        y0 = (self.height() - (rows - 1) * 6) / 2
        for i in range(cols):
            for j in range(rows):
                p.drawEllipse(QRectF(x0 + i * 6 - 1.5, y0 + j * 6 - 1.5, 3, 3))


class PenPanel(QFrame):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setObjectName("penPanel")
        self.setCursor(Qt.SizeAllCursor)
        self._drag = None
        self.vertical = theme._settings().value("ui/pen_panel_vertical", "false") == "true"
        self.layout_ = QBoxLayout(QBoxLayout.LeftToRight, self)
        self.layout_.setContentsMargins(8, 6, 8, 6)
        self.layout_.setSpacing(2)
        self.grip = _Grip(self)
        self.layout_.addWidget(self.grip, 0, Qt.AlignCenter)
        self._rules = []
        self._groups = []   # [(grid holder, [buttons])]: one row when horizontal, two columns when vertical
        self._group()

        for tool in PANEL_TOOLS:
            self._add(self._button(window.tool_actions[tool]))
        self._rule()
        self.swatches = QButtonGroup(self)
        self.swatches.setExclusive(True)
        for color in PALETTE:
            btn = QToolButton(self)
            btn.setObjectName("penSwatch")
            btn.setCheckable(True)
            btn.setIconSize(QSize(20, 20))
            btn.setToolTip(f"Colour {color}")
            btn.setProperty("color", color)
            btn.clicked.connect(lambda _c=False, c=color: window.set_pen_color(QColor(c)))
            self.swatches.addButton(btn)
            self._add(btn)
        more = QToolButton(self)
        more.setIcon(icons.icon("palette"))
        more.setToolTip("More colours...")
        more.clicked.connect(window.pick_color)
        self._add(more)
        self._rule()
        self.width_buttons = []
        for i, diameter in enumerate((4, 7, 11)):
            btn = QToolButton(self)
            btn.setCheckable(True)
            btn.setIcon(_dot(diameter))
            btn.setToolTip(("Thin", "Medium", "Thick")[i] + " line")
            btn.clicked.connect(lambda _c=False, i=i: self._pick_width(i))
            self.width_buttons.append(btn)
            self._add(btn)
        self._rule()
        for act in (window.act_smooth_ink, window.act_pressure_ink, window.act_undo, window.act_redo):
            self._add(self._button(act))
        self._rule()
        turn = QToolButton(self)
        turn.setIcon(icons.icon("rotate-right"))
        turn.setToolTip("Turn the panel (horizontal / vertical)")
        turn.clicked.connect(self.toggle_orientation)
        self._add(turn)
        close = QToolButton(self)
        close.setIcon(icons.icon("close"))
        close.setToolTip("Close the pen panel (it comes back with the Pen while the ribbon is hidden)")
        close.clicked.connect(window.close_pen_panel)
        self._add(close)
        for child in self.findChildren(QToolButton):
            child.setCursor(Qt.ArrowCursor)
            child.setAutoRaise(True)
        self._set_orientation(self.vertical)
        window.installEventFilter(self)
        self.hide()

    # ---- building
    def _button(self, action):
        btn = QToolButton(self)
        btn.setDefaultAction(action)
        btn.setIconSize(QSize(20, 20))
        return btn

    def _group(self):
        holder = QWidget(self)
        holder.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        self._groups.append((holder, []))
        self.layout_.addWidget(holder, 0, Qt.AlignCenter)

    def _add(self, widget):
        holder, members = self._groups[-1]
        widget.setParent(holder)
        members.append(widget)

    def _rule(self):
        rule = QFrame(self)
        rule.setObjectName("penRule")
        self._rules.append(rule)
        self.layout_.addWidget(rule, 0, Qt.AlignCenter)
        self._group()

    # ---- orientation and position
    def toggle_orientation(self):
        center = self.geometry().center()
        self._set_orientation(not self.vertical)
        theme._settings().setValue("ui/pen_panel_vertical", "true" if self.vertical else "false")
        self.move(center - QPoint(self.width() // 2, self.height() // 2))
        self._keep_inside()

    def _set_orientation(self, vertical):
        self.vertical = vertical
        self.layout_.setDirection(QBoxLayout.TopToBottom if vertical else QBoxLayout.LeftToRight)
        self.grip.set_vertical(vertical)
        for rule in self._rules:
            rule.setFixedSize(QSize(50, 1) if vertical else QSize(1, 22))
        for holder, members in self._groups:
            grid = holder.layout()
            for w in members:
                grid.removeWidget(w)
            for i, w in enumerate(members):
                grid.addWidget(w, *(divmod(i, 2) if vertical else (0, i)), Qt.AlignCenter)
            holder.adjustSize()
        self.adjustSize()

    def place(self):
        """Where it was last left (as fractions of the window), else top centre."""
        self.adjustSize()
        s = theme._settings()
        area = self.window.rect()
        try:
            fx, fy = float(s.value("ui/pen_panel_x", -1)), float(s.value("ui/pen_panel_y", -1))
        except (TypeError, ValueError):
            fx = fy = -1
        if 0 <= fx <= 1 and 0 <= fy <= 1:
            self.move(int(fx * area.width()), int(fy * area.height()))
        else:
            self.move((area.width() - self.width()) // 2, self.window.menuBar().height() + 12)
        self._keep_inside()

    def _keep_inside(self):
        area = self.window.rect()
        top = self.window.menuBar().height() + 2 if self.window.menuBar().isVisible() else 0
        x = min(max(self.x(), 4), max(4, area.width() - self.width() - 4))
        y = min(max(self.y(), top), max(top, area.height() - self.height() - 4))
        self.move(x, y)

    def _remember_position(self):
        area = self.window.rect()
        s = theme._settings()
        s.setValue("ui/pen_panel_x", self.x() / max(1, area.width()))
        s.setValue("ui/pen_panel_y", self.y() / max(1, area.height()))

    def eventFilter(self, obj, event):
        if obj is self.window and event.type() == event.Type.Resize and self.isVisible():
            self._keep_inside()
        return False

    # ---- dragging (from the grip or any empty spot)
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.pos()
            self.raise_()

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            self.move(event.globalPosition().toPoint() - self._drag)
            self._keep_inside()

    def mouseReleaseEvent(self, event):
        if self._drag is not None:
            self._drag = None
            self._remember_position()

    def mouseDoubleClickEvent(self, event):
        if self.grip.geometry().adjusted(-6, -6, 6, 6).contains(event.position().toPoint()):
            self.toggle_orientation()

    # ---- following the window's state
    def refresh(self):
        tool = self.window.current_tool
        style = self.window.tool_styles[tool]
        current = QColor(*style["color"]).name().lower()
        for btn in self.swatches.buttons():
            btn.setIcon(_chip(btn.property("color")))   # re-drawn: the rim follows the look
            btn.setChecked(btn.property("color") == current)
        if not any(b.isChecked() for b in self.swatches.buttons()):
            self.swatches.setExclusive(False)
            for btn in self.swatches.buttons():
                btn.setChecked(False)
            self.swatches.setExclusive(True)
        widths = WIDTHS.get(tool)
        for i, btn in enumerate(self.width_buttons):
            btn.setIcon(_dot((4, 7, 11)[i]))
            btn.setEnabled(widths is not None)
            btn.setChecked(bool(widths) and abs(style["width"] - widths[i]) < 0.01)

    def _pick_width(self, index):
        tool = self.window.current_tool if self.window.current_tool in WIDTHS else Tool.INK
        if self.window.current_tool not in WIDTHS:
            self.window.set_tool(Tool.INK)
        self.window.set_pen_width(WIDTHS[tool][index])
