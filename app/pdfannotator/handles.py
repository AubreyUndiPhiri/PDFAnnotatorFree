"""Geometry of the resize handles drawn around a single selected annotation."""
from PySide6.QtCore import QPointF, QRectF, Qt

HIT_RADIUS = 7.0        # px around a handle that still grabs it
CORNER_RADIUS = 4.5     # drawn size of corner handles
EDGE_RADIUS = 3.5       # drawn size of edge handles
MIN_SIZE = 8.0          # px, smallest box a resize can produce

CURSORS = {
    "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
    "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
    "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
    "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
}


def handle_points(rect: QRectF) -> dict:
    cx, cy = rect.center().x(), rect.center().y()
    return {
        "tl": rect.topLeft(), "t": QPointF(cx, rect.top()), "tr": rect.topRight(),
        "r": QPointF(rect.right(), cy), "br": rect.bottomRight(), "b": QPointF(cx, rect.bottom()),
        "bl": rect.bottomLeft(), "l": QPointF(rect.left(), cy),
    }


def handle_at(rect: QRectF, pos) -> str | None:
    for name, point in handle_points(rect).items():
        if abs(point.x() - pos.x()) <= HIT_RADIUS and abs(point.y() - pos.y()) <= HIT_RADIUS:
            return name
    return None


def resized(start: QRectF, handle: str, pos) -> QRectF:
    """`start` with the edges named by `handle` dragged to `pos`."""
    r = QRectF(start)
    if "l" in handle:
        r.setLeft(min(pos.x(), r.right() - MIN_SIZE))
    if "r" in handle:
        r.setRight(max(pos.x(), r.left() + MIN_SIZE))
    if "t" in handle:
        r.setTop(min(pos.y(), r.bottom() - MIN_SIZE))
    if "b" in handle:
        r.setBottom(max(pos.y(), r.top() + MIN_SIZE))
    return r
