"""Loads the SVG icon set in app/assets/icons/ and tints it to the theme.

Icons are drawn with stroke="currentColor"; icon() swaps in a colour per
state (normal, hover, checked, disabled) so one file serves every state."""
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from . import theme

ICONS_DIR = Path(__file__).resolve().parents[1] / "assets" / "icons"
_RENDER_SIZES = (16, 20, 24, 32, 48)


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


@lru_cache(maxsize=None)
def icon(name: str, color: str | None = None, checked_color: str | None = None) -> QIcon:
    """Themed icon. `color` overrides the normal tint (e.g. white on a
    primary button); `checked_color` is used for the On state of checkable
    actions (the active tool)."""
    normal = color or theme.ICON
    on = checked_color or theme.ACCENT
    result = QIcon()
    for size in _RENDER_SIZES:
        result.addPixmap(pixmap(name, size, normal), QIcon.Normal, QIcon.Off)
        result.addPixmap(pixmap(name, size, color or theme.ICON_HOVER), QIcon.Active, QIcon.Off)
        result.addPixmap(pixmap(name, size, theme.ICON_DISABLED), QIcon.Disabled, QIcon.Off)
        for mode in (QIcon.Normal, QIcon.Active):
            result.addPixmap(pixmap(name, size, on), mode, QIcon.On)
    return result


def write_tinted(name: str, color: str, out_dir: Path, size: int = 16) -> str:
    """Render a tinted PNG for use in Qt style sheets (url(...)), which cannot
    tint SVGs themselves. Returns a forward-slash path for QSS."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}-{color.lstrip('#')}-{size}.png"
    if not path.exists():
        pixmap(name, size * 2, color).save(str(path), "PNG")
    return path.as_posix()
