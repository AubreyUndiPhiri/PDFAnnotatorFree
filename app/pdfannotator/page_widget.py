import time

import pymupdf as fitz
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap, QPointingDevice
from PySide6.QtCore import QEvent, Qt, QPoint, QPointF, QRect, QTimer

from . import handles, pdf_ops, strokes, theme
from .tools import Tool

# Tools that collect a freehand point path while the mouse is dragging
PATH_TOOLS = {Tool.INK, Tool.MARKER, Tool.ERASER, Tool.LASSO}
# Freehand drawing tools: smoothed (and, for the pen, pressure-sensitive)
DRAW_TOOLS = {Tool.INK, Tool.MARKER}
STABILIZER = 0.45   # with Smooth on, each point moves this far towards the pointer (steadies shaky hands)
TABLET_FRESH_S = 0.15  # a tablet reading this recent belongs to the mouse event Qt makes from it

TEXT_TOOLS = (Tool.TEXTBOX, Tool.FORMULA)

# Mouse cursor per tool (anything not listed gets a crosshair)
TOOL_CURSORS = {
    Tool.SELECT: Qt.ArrowCursor,
    Tool.TEXTBOX: Qt.IBeamCursor,
    Tool.FORMULA: Qt.IBeamCursor,
    Tool.EXTRACT_TEXT: Qt.IBeamCursor,
    Tool.PAN: Qt.OpenHandCursor,
    Tool.POINTER: Qt.PointingHandCursor,
    Tool.LASER_POINTER: Qt.ArrowCursor,
    Tool.NOTE: Qt.PointingHandCursor,
}

# Tools drawn as a simple rubber-band rectangle/line while dragging, committed
# as a single (p1, p2) pair on release
RUBBERBAND_TOOLS = {
    Tool.RECT, Tool.ELLIPSE, Tool.LINE, Tool.ARROW, Tool.TEXTBOX, Tool.STAMP,
    Tool.IMAGE_STAMP, Tool.HIGHLIGHT, Tool.UNDERLINE, Tool.STRIKEOUT,
    Tool.DIMENSION, Tool.SNAPSHOT, Tool.CROP, Tool.MEASURE, Tool.EXTRACT_TEXT,
    Tool.FORMULA,
}


class PageWidget(QWidget):
    """Renders one PDF page and handles all mouse interaction for annotation tools."""

    def __init__(self, controller, page_index: int, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.page_index = page_index
        self.pixmap = None
        self.rendered = False
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)

        self._dragging = False
        self._drag_start = None
        self._drag_current = None
        self._path_points = []
        self._path_pressure = []        # one per point while drawing with Pressure on
        self._pressure_from_pen = False  # real tablet pressure (else simulated from speed)
        self._speed_pressure = None
        self._tablet_pressure = None
        self._tablet_time = 0.0
        self._pan_by_button = None        # middle button / pen barrel button held: panning
        self._tool_before_eraser = None   # the pen's eraser end is in use
        self._last_tablet_press = None    # (time, pos): a double-tap is a double-click
        self.setAttribute(Qt.WA_TabletTracking)   # the pen hovering updates the cursor

        self._polygon_points_px = []
        self._polygon_rubber_px = None

        self._pan_last_pos = None
        self._flash_rect = None

        self._snap_edge = None            # a ruler / protractor edge the pen is following
        self._eraser_hover = None         # where the Eraser's tip is drawn
        self._erase_queue = []            # rubbing not erased yet (PDF points)
        self._erase_last = None
        self._erase_timer = QTimer(self)
        self._erase_timer.setSingleShot(True)
        self._erase_timer.setInterval(16)
        self._erase_timer.timeout.connect(self._flush_erase)

        self.ensure_placeholder()
        self.apply_tool_cursor()

    def apply_tool_cursor(self):
        if self.controller.current_tool == Tool.ERASER and self._rubbing_eraser():
            self.setCursor(Qt.BlankCursor)   # the eraser tip is drawn instead (see _paint_eraser_tip)
            return
        self.setCursor(TOOL_CURSORS.get(self.controller.current_tool, Qt.CrossCursor))

    def _to_viewport(self, point):
        return self.mapTo(self.controller.scroll_area.viewport(), QPointF(point))

    def _snapped(self, point):
        """`point` moved onto the tool edge the stroke is following."""
        viewport = self.controller.scroll_area.viewport()
        on_edge = self.controller.geometry.project(self._snap_edge, self._to_viewport(point))
        return self.mapFrom(viewport, on_edge)

    def _rubbing_eraser(self):
        """The Eraser (rubs out parts of strokes), not the Stroke Eraser."""
        return getattr(self.controller, "eraser_mode", "point") != "stroke"

    def _paint_eraser_tip(self, painter):
        """The Eraser's tip under the pointer: a soft round rubber, as big as
        what it rubs out, like the end of a pencil seen from above."""
        if self._eraser_hover is None:
            return
        try:
            r = self.controller.eraser_radius(self) * self.controller.px_per_pt(self)
        except Exception:   # no page yet
            r = 6.0
        r = max(3.0, r)
        c = QPointF(self._eraser_hover)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        for spread, alpha in ((3.0, 18), (1.6, 30)):   # a soft shadow round the rubber
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawEllipse(c + QPointF(0, 1), r + spread, r + spread)
        painter.setBrush(QColor(255, 255, 255, 215))
        painter.setPen(QPen(QColor(120, 124, 134, 230), 1.0))
        painter.drawEllipse(c, r, r)
        painter.restore()

    def _queue_erase(self, pdf_point):
        """Rubbing is erased in small batches (about 60 a second), so the page
        keeps up with the pointer and the cut follows the whole path."""
        self._erase_queue.append(pdf_point)
        if not self._erase_timer.isActive():
            self._erase_timer.start()

    def _flush_erase(self):
        self._erase_timer.stop()
        if not self._erase_queue:
            return
        path = ([self._erase_last] if self._erase_last is not None else []) + self._erase_queue
        self._erase_last = self._erase_queue[-1]
        self._erase_queue = []
        self.controller.erase_step(self, path)

    def page(self) -> fitz.Page:
        return self.controller.document.page(self.page_index)

    def zoom(self) -> float:
        return self.controller.zoom

    def page_size_px(self):
        page = self.page()
        mat = pdf_ops.coord_matrix(page, self.zoom())
        r = page.rect * mat
        r = fitz.Rect(r).normalize()
        return max(1, int(round(r.width))), max(1, int(round(r.height)))

    def ensure_placeholder(self):
        w, h = self.page_size_px()
        self.setFixedSize(w, h)

    def render(self):
        page = self.page()
        mat = pdf_ops.render_matrix(self.zoom())
        pix = page.get_pixmap(matrix=mat, alpha=False, annots=not self.controller.hide_annotations)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
        self.pixmap = QPixmap.fromImage(img)
        self.setFixedSize(self.pixmap.size())
        self.rendered = True
        self.update()

    def invalidate(self):
        self.rendered = False
        self.ensure_placeholder()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        if self.rendered and self.pixmap is not None:
            painter.drawPixmap(0, 0, self.pixmap)
        else:
            painter.fillRect(self.rect(), QColor(theme.SURFACE))
            painter.setPen(QColor(theme.BORDER))
            painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

        if self.rendered:
            self._paint_selection(painter)

        if self._flash_rect is not None and self.rendered:
            try:
                r = fitz.Rect(self._flash_rect) * pdf_ops.coord_matrix(self.page(), self.zoom())
                r = r.normalize()
                pen = QPen(theme.SEARCH_FLASH, 3)
                painter.setPen(pen)
                painter.drawRect(int(r.x0), int(r.y0), max(1, int(r.width)), max(1, int(r.height)))
            except Exception:
                pass

        if self._polygon_points_px and self.rendered:
            pen = QPen(self.controller.current_color, max(1, int(self.controller.current_width)))
            painter.setPen(pen)
            pts = self._polygon_points_px
            for i in range(1, len(pts)):
                painter.drawLine(pts[i - 1], pts[i])
            if self._polygon_rubber_px is not None:
                painter.drawLine(pts[-1], self._polygon_rubber_px)
            for p in pts:
                painter.drawEllipse(p, 2, 2)

        if self._dragging and self.rendered:
            pen = QPen(self.controller.current_color, max(1, int(self.controller.current_width)))
            painter.setPen(pen)
            tool = self.controller.current_tool
            if tool in DRAW_TOOLS and len(self._path_points) > 1:
                self._paint_stroke_preview(painter, tool)
            elif tool == Tool.ERASER and self._rubbing_eraser():
                pass   # it erases as it goes; the tip shows where
            elif tool in PATH_TOOLS and len(self._path_points) > 1:
                if tool == Tool.ERASER:   # the Stroke Eraser: the line it will erase along
                    painter.setRenderHint(QPainter.Antialiasing)
                    painter.setPen(QPen(self.controller.current_color, 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                for i in range(1, len(self._path_points)):
                    painter.drawLine(self._path_points[i - 1], self._path_points[i])
            elif self._drag_start and self._drag_current:
                r = QRect(self._drag_start, self._drag_current).normalized()
                if tool in TEXT_TOOLS:
                    painter.setPen(QPen(theme.SELECTION, 1, Qt.DashLine))
                    painter.drawRect(r)
                elif tool in (Tool.LINE, Tool.ARROW, Tool.DIMENSION):
                    painter.drawLine(self._drag_start, self._drag_current)
                elif tool == Tool.ELLIPSE:
                    painter.drawEllipse(r)
                else:
                    painter.drawRect(r)
        if self.controller.current_tool == Tool.ERASER and self._rubbing_eraser() and self.rendered:
            self._paint_eraser_tip(painter)
        painter.end()

    def _paint_stroke_preview(self, painter, tool):
        """The stroke being drawn: a smooth, antialiased curve, thicker and
        thinner with the pressure when that is on."""
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor(self.controller.current_color)
        if tool == Tool.MARKER:
            color.setAlphaF(self.controller.current_opacity)
        scale = self.controller.px_per_pt(self)
        base = self.controller.current_width
        pts = self._path_points
        if tool == Tool.INK and self._path_pressure and len(self._path_pressure) == len(pts):
            for i in range(1, len(pts)):
                width = strokes.width_for(base, (self._path_pressure[i - 1] + self._path_pressure[i]) / 2) * scale
                painter.setPen(QPen(color, max(0.8, width), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                painter.drawLine(pts[i - 1], pts[i])
        else:
            path = QPainterPath(pts[0])
            for i in range(1, len(pts) - 1):  # curves through the midpoints: no corners
                mid = (pts[i] + pts[i + 1]) / 2
                path.quadTo(pts[i], mid)
            path.lineTo(pts[-1])
            painter.setPen(QPen(color, max(1.0, base * scale), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
        painter.setRenderHint(QPainter.Antialiasing, False)

    # ---- pen tablets / styluses
    def tabletEvent(self, event):
        """The pen drives the tools directly (every reading, with its pressure,
        no mouse emulation in between). Its eraser end erases while it's
        turned over; a barrel button set to right-click opens the page menu,
        and one set to middle-click pans the page."""
        self._tablet_pressure = event.pressure()
        self._tablet_time = time.monotonic()
        kind = event.type()
        pos = self._event_pos(event)
        if kind == QEvent.TabletPress:
            if event.button() == Qt.RightButton:
                event.accept()
                self.controller.show_page_menu(self, pos, event.globalPosition().toPoint())
                return
            if event.button() == Qt.MiddleButton:
                self._pan_by_button = pos
                event.accept()
                return
            if event.pointerType() == QPointingDevice.PointerType.Eraser \
                    and self.controller.current_tool != Tool.ERASER:
                self._tool_before_eraser = self.controller.current_tool
                self.controller.window.set_tool(Tool.ERASER)
            now = time.monotonic()
            last = self._last_tablet_press
            self._last_tablet_press = (now, pos)
            if last and now - last[0] < QApplication.doubleClickInterval() / 1000 \
                    and (pos - last[1]).manhattanLength() < 8:
                self._last_tablet_press = None
                self.mouseDoubleClickEvent(event)
            else:
                self.mousePressEvent(event)
        elif kind == QEvent.TabletMove:
            if self._pan_by_button is not None:
                delta = pos - self._pan_by_button
                self.controller.pan_scroll(delta.x(), delta.y())
                self._pan_by_button = pos
            else:
                self.mouseMoveEvent(event)
        elif kind == QEvent.TabletRelease:
            if self._pan_by_button is not None:
                self._pan_by_button = None
            elif event.button() != Qt.RightButton:
                self.mouseReleaseEvent(event)
            if self._tool_before_eraser is not None:
                self.controller.window.set_tool(self._tool_before_eraser)
                self._tool_before_eraser = None
        event.accept()

    def contextMenuEvent(self, event):
        if not self.rendered or self.controller.current_tool == Tool.ZOOM:
            return   # the Zoom tool zooms out on right-click instead
        self.controller.show_page_menu(self, event.pos(), event.globalPos())

    def _pressure_now(self, pos_f, event):
        if self._tablet_pressure is not None and time.monotonic() - self._tablet_time < TABLET_FRESH_S:
            self._pressure_from_pen = True
            return max(0.05, float(self._tablet_pressure))
        if self._speed_pressure is None:
            self._speed_pressure = strokes.SpeedPressure()
        scale = max(0.01, self.controller.px_per_pt(self))
        t = event.timestamp() if hasattr(event, "timestamp") else time.monotonic() * 1000
        return self._speed_pressure.feed((pos_f.x() / scale, pos_f.y() / scale), float(t))

    def _paint_selection(self, painter):
        painter.setRenderHint(QPainter.Antialiasing)
        frame = self.controller.selection_frame(self)
        if frame is not None:
            rect, show_handles = frame
            painter.setPen(QPen(theme.SELECTION, 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect)
            if show_handles:
                painter.setPen(QPen(QColor("#ffffff"), 1.2))
                painter.setBrush(theme.SELECTION)
                for name, point in handles.handle_points(rect).items():
                    radius = handles.CORNER_RADIUS if len(name) == 2 else handles.EDGE_RADIUS
                    painter.drawEllipse(point, radius, radius)
            painter.setRenderHint(QPainter.Antialiasing, False)
            return
        offset = self.controller.select_offset_px if self.controller.select_dragging else QPoint(0, 0)
        painter.setPen(QPen(theme.SELECTION, 1, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        for sel_page, annot in self.controller.selected:
            if sel_page != self.page_index:
                continue
            try:
                rect = self.controller.annot_rect_px(self, annot)
                painter.drawRect(rect.translated(offset.x(), offset.y()))
            except Exception:
                pass
        painter.setRenderHint(QPainter.Antialiasing, False)

    def to_pdf_point(self, qpoint: QPoint) -> fitz.Point:
        return pdf_ops.pixel_to_pdf(self.page(), self.zoom(), qpoint.x(), qpoint.y())

    @staticmethod
    def _event_pos(event) -> QPoint:
        return event.position().toPoint() if hasattr(event, "position") else event.pos()

    # ---------------------------------------------------------------
    # Mouse handling
    # ---------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:          # middle button: drag the page around
            self._pan_by_button = self._event_pos(event)
            self.setCursor(Qt.ClosedHandCursor)
            return
        if not self.rendered or event.button() not in (Qt.LeftButton, Qt.RightButton):
            return
        tool = self.controller.current_tool
        pos = self._event_pos(event)
        if self.controller.text_edit is not None:
            # Clicking anywhere on the page outside the box finishes typing
            self.controller.finish_text_editing()
            return
        self.setFocus()

        if event.button() == Qt.RightButton:
            if tool == Tool.ZOOM:
                self.controller.zoom_click(self, pos, zoom_in=False)
            return

        if tool == Tool.SELECT:
            additive = bool(event.modifiers() & Qt.ControlModifier)
            self.controller.begin_select_drag(self, pos, additive)
            return

        if tool == Tool.NOTE:
            self.controller.place_note(self, pos)
            return

        if tool in TEXT_TOOLS:
            existing = pdf_ops.find_annot_at(self.page(), self.to_pdf_point(pos))
            if tool == Tool.FORMULA and pdf_ops.is_formula(existing):
                self.controller.begin_formula_edit(self, annot=existing)
                return
            if pdf_ops.is_text_box(existing):
                self.controller.begin_text_edit(self, annot=existing)
                return
            # otherwise fall through: a click types at that spot, a drag sets the box width

        if tool == Tool.ZOOM:
            self.controller.zoom_click(self, pos, zoom_in=True)
            return

        if tool == Tool.POINTER:
            self.controller.inspect_annot(self, pos)
            return

        if tool == Tool.PAN:
            self._pan_last_pos = pos
            return

        if tool == Tool.POLYGON:
            self._polygon_points_px.append(pos)
            self._polygon_rubber_px = pos
            self.update()
            return

        self._dragging = True
        self._drag_start = pos
        self._drag_current = pos
        if tool in PATH_TOOLS:
            pos_f = event.position() if hasattr(event, "position") else QPointF(pos)
            self._path_points = [QPointF(pos_f)]
            self._path_pressure = []
            self._pressure_from_pen = False
            self._speed_pressure = None
            if tool == Tool.INK and self.controller.ink_pressure:
                self._path_pressure = [self._pressure_now(pos_f, event)]
            self._snap_edge = None
            geometry = getattr(self.controller, "geometry", None)
            if tool in DRAW_TOOLS and geometry is not None and geometry.tools:
                self._snap_edge = geometry.edge_near(self._to_viewport(pos_f))
                if self._snap_edge is not None:   # the line follows the ruler (or the curve)
                    self._snap_start = self._snapped(pos_f)
                    self._path_points = [self._snap_start]
            if tool == Tool.ERASER and self._rubbing_eraser():
                self._erase_queue, self._erase_last = [], None
                self._queue_erase(self.to_pdf_point(pos_f))
        self.update()

    def mouseMoveEvent(self, event):
        pos = self._event_pos(event)
        tool = self.controller.current_tool
        if self._pan_by_button is not None:
            delta = pos - self._pan_by_button
            self.controller.pan_scroll(delta.x(), delta.y())
            self._pan_by_button = pos
            return

        if tool == Tool.LASER_POINTER:
            self.controller.update_laser_pointer(self, pos)

        if tool == Tool.SELECT:
            if event.buttons() & Qt.LeftButton:
                self.controller.update_select_drag(self, pos)
            else:
                self.setCursor(self.controller.hover_cursor(self, pos))
            return

        if tool == Tool.POLYGON:
            if self._polygon_points_px:
                self._polygon_rubber_px = pos
                self.update()
            return

        if tool == Tool.PAN:
            if self._pan_last_pos is not None and (event.buttons() & Qt.LeftButton):
                delta = pos - self._pan_last_pos
                self.controller.pan_scroll(delta.x(), delta.y())
                self._pan_last_pos = pos
            return

        if tool == Tool.ERASER and self._rubbing_eraser():
            self._eraser_hover = QPointF(event.position()) if hasattr(event, "position") else QPointF(pos)
            self.update()

        if not self._dragging:
            return
        self._drag_current = pos
        if tool in PATH_TOOLS:
            pos_f = QPointF(event.position()) if hasattr(event, "position") else QPointF(pos)
            if self._snap_edge is not None and tool in DRAW_TOOLS:
                pos_f = self._snapped(pos_f)
                geometry = self.controller.geometry
                geometry.show_readout(geometry.measure_text(self._snap_edge, self._to_viewport(self._snap_start),
                                                            self._to_viewport(pos_f)), self._to_viewport(pos_f))
            elif tool in DRAW_TOOLS and self.controller.ink_smoothing and self._path_points:
                last = self._path_points[-1]
                pos_f = last + (pos_f - last) * STABILIZER
            self._path_points.append(pos_f)
            if self._path_pressure:
                self._path_pressure.append(self._pressure_now(pos_f, event))
            if tool == Tool.ERASER and self._rubbing_eraser():   # it erases as it goes
                self._queue_erase(self.to_pdf_point(pos_f))
        if tool == Tool.MEASURE:
            p1 = self.to_pdf_point(self._drag_start)
            p2 = self.to_pdf_point(pos)
            self.controller.update_measure(self, p1, p2)
        self.update()

    def leaveEvent(self, event):
        if self._eraser_hover is not None:
            self._eraser_hover = None
            self.update()
        if self.controller.current_tool == Tool.LASER_POINTER:
            self.controller.hide_laser_pointer()
        super().leaveEvent(event)

    NUDGE_KEYS = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}

    def keyPressEvent(self, event):
        if self.controller.current_tool == Tool.SELECT and self.controller.selected and event.key() in self.NUDGE_KEYS:
            step = 10 if event.modifiers() & Qt.ShiftModifier else 1
            dx, dy = self.NUDGE_KEYS[event.key()]
            self.controller.nudge_selected(self, dx * step, dy * step)
            return
        if self.controller.current_tool == Tool.POLYGON and self._polygon_points_px:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self._finish_polygon()
                return
            if event.key() == Qt.Key_Escape:
                self._polygon_points_px = []
                self._polygon_rubber_px = None
                self.update()
                return
        super().keyPressEvent(event)

    def _finish_polygon(self):
        if len(self._polygon_points_px) < 3:
            self._polygon_points_px = []
            self._polygon_rubber_px = None
            self.update()
            return
        pts = [self.to_pdf_point(p) for p in self._polygon_points_px]
        self._polygon_points_px = []
        self._polygon_rubber_px = None
        self.update()
        self.controller.commit_polygon(self, pts)

    def mouseReleaseEvent(self, event):
        pos = self._event_pos(event)
        tool = self.controller.current_tool
        if self._pan_by_button is not None and event.button() == Qt.MiddleButton:
            self._pan_by_button = None
            self.apply_tool_cursor()
            return

        if tool == Tool.SELECT:
            self.controller.end_select_drag(self, pos)
            return

        if tool == Tool.PAN:
            self._pan_last_pos = None
            return

        if not self._dragging:
            return
        self._dragging = False
        start, end = self._drag_start, pos
        self._drag_start = None
        self._drag_current = None
        path_points = self._path_points
        pressures = self._path_pressure
        self._path_points = []
        self._path_pressure = []
        self.update()

        snapped, self._snap_edge = self._snap_edge is not None and tool in DRAW_TOOLS, None
        if snapped:
            self.controller.geometry.hide_readout()
        if tool in PATH_TOOLS:
            if tool in DRAW_TOOLS and path_points and self.controller.ink_smoothing and not snapped:
                end = QPointF(event.position()) if hasattr(event, "position") else QPointF(pos)
                if (end - path_points[-1]).manhattanLength() > 0.5:
                    path_points.append(end)  # the stabilizer lags: finish where the pen lifted
                    if pressures:
                        pressures.append(pressures[-1])
            if tool == Tool.ERASER and self._rubbing_eraser():
                self._flush_erase()   # the last bit of rubbing, then one undo step for it all
                self.controller.commit_eraser(self, [self.to_pdf_point(p) for p in path_points])
                return
            if len(path_points) < 2:
                return
            pts = [self.to_pdf_point(p) for p in path_points]
            if tool == Tool.INK:
                if pressures and not self._pressure_from_pen:
                    pressures = strokes.taper(pressures)  # simulated: ease in and out like a real pen
                self.controller.commit_ink(self, pts, pressures or None, smooth=not snapped)
            elif tool == Tool.MARKER:
                self.controller.commit_marker(self, pts, smooth=not snapped)
            elif tool == Tool.ERASER:
                self.controller.commit_eraser(self, pts)
            elif tool == Tool.LASSO:
                self.controller.commit_lasso(self, pts)
            return

        if tool == Tool.FORMULA:
            self.controller.begin_formula_edit(self, origin_pdf=self.to_pdf_point(QRect(start, end).normalized().topLeft()))
            return

        if tool in TEXT_TOOLS:
            if (start - end).manhattanLength() < 6:
                self.controller.begin_text_edit(self, origin_pdf=self.to_pdf_point(start))
            else:
                box = QRect(start, end).normalized()
                p1 = self.to_pdf_point(box.topLeft())
                p2 = self.to_pdf_point(box.topRight())
                self.controller.begin_text_edit(self, origin_pdf=p1, width_pt=abs(p2.x - p1.x))
            return

        if (start - end).manhattanLength() < 3 and tool not in (Tool.STAMP, Tool.IMAGE_STAMP):
            return

        p1 = self.to_pdf_point(start)
        p2 = self.to_pdf_point(end)

        if tool == Tool.DIMENSION:
            self.controller.commit_dimension(self, p1, p2)
        elif tool == Tool.SNAPSHOT:
            self.controller.commit_snapshot(self, p1, p2)
        elif tool == Tool.CROP:
            self.controller.commit_crop(self, p1, p2)
        elif tool == Tool.EXTRACT_TEXT:
            self.controller.commit_extract_text(self, p1, p2)
        elif tool == Tool.MEASURE:
            pass  # live readout only, nothing committed
        else:
            self.controller.commit_drag_tool(self, tool, p1, p2)

    def mouseDoubleClickEvent(self, event):
        pos = self._event_pos(event)
        tool = self.controller.current_tool
        if tool == Tool.SELECT:
            self.controller.try_edit_annot_text(self, pos)
        elif tool == Tool.POLYGON:
            self._finish_polygon()
