"""Loads the SVG icon set in app/assets/icons/ and tints it to the theme.

Icons are drawn with stroke="currentColor"; icon() swaps in a colour per
state (normal, hover, checked, disabled) so one file serves every state.
The colours are looked up when the icon is drawn, so icons follow
theme.set_mode() without being rebuilt."""
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QIconEngine, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from . import theme

ICONS_DIR = Path(__file__).resolve().parents[1] / "assets" / "icons"


@lru_cache(maxsize=None)
def _svg_source(name: str) -> str:
    return (ICONS_DIR / f"{name}.svg").read_text(encoding="utf-8")


def pixmap(name: str, size: int, color: str) -> QPixmap:
    data = _svg_source(name).replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(data.encode("utf-8")))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return pm


@lru_cache(maxsize=2048)
def _cached(name: str, size: int, color: str) -> QPixmap:
    return pixmap(name, size, color)


class _ThemedIconEngine(QIconEngine):
    def __init__(self, name, color=None, checked_color=None):
        super().__init__()
        self.name, self.color, self.checked_color = name, color, checked_color

    def _color(self, mode, state) -> str:
        if mode == QIcon.Disabled:
            return theme.ICON_DISABLED
        if state == QIcon.On:
            return self.checked_color or theme.ACCENT
        if mode in (QIcon.Active, QIcon.Selected):
            return self.color or theme.ICON_HOVER
        return self.color or theme.ICON

    def pixmap(self, size, mode, state):
        return self.scaledPixmap(size, mode, state, 1.0)

    def scaledPixmap(self, size, mode, state, scale):
        side = max(1, round(max(size.width(), size.height()) * scale))
        pm = QPixmap(_cached(self.name, side, self._color(mode, state)))
        pm.setDevicePixelRatio(scale)
        return pm

    def paint(self, painter, rect, mode, state):
        scale = painter.device().devicePixelRatioF() if painter.device() else 1.0
        painter.drawPixmap(rect, self.scaledPixmap(rect.size(), mode, state, scale))

    def actualSize(self, size, mode, state):
        side = min(size.width(), size.height())
        return QSize(side, side)

    def clone(self):
        engine = _ThemedIconEngine(self.name, self.color, self.checked_color)
        _ENGINES.append(engine)
        return engine


_ENGINES = []  # keep the Python side of every engine alive while Qt uses it


@lru_cache(maxsize=None)
def icon(name: str, color: str | None = None, checked_color: str | None = None) -> QIcon:
    """Themed icon. `color` overrides the normal tint (e.g. white on a
    primary button); `checked_color` is used for the On state of checkable
    actions (the active tool)."""
    _svg_source(name)  # a missing icon fails here, not on first paint
    engine = _ThemedIconEngine(name, color, checked_color)
    _ENGINES.append(engine)
    return QIcon(engine)


def write_tinted(name: str, color: str, out_dir: Path, size: int = 16) -> str:
    """Render a tinted PNG for use in Qt style sheets (url(...)), which cannot
    tint SVGs themselves. Returns a forward-slash path for QSS."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}-{color.lstrip('#')}-{size}.png"
    if not path.exists():
        pixmap(name, size * 2, color).save(str(path), "PNG")
    return path.as_posix()
