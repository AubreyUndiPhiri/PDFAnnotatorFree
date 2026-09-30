"""Edit Photo: crop, rotate, flip, remove the background, erase a colour,
and adjust brightness / contrast / saturation, for a picture on the page.

The picture is kept as an RGBA numpy array. Crop, rotate, flip and the
background tools change it (each step can be undone); the sliders are
applied on top, live, and baked in when you press Apply.

Removing the background: when the picture's edge is one plain colour (a
signature or logo on paper, a product shot on white), everything of that
colour connected to the edge becomes transparent; otherwise OpenCV's
GrabCut separates the subject from its surroundings."""
import cv2
import numpy as np
from PySide6.QtCore import QBuffer, QByteArray, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSlider,
    QVBoxLayout, QWidget,
)

from . import icons, theme

MAX_WORK_SIDE = 4000        # bigger pictures are scaled down first (plenty for a page)
GRABCUT_SIDE = 700          # GrabCut runs on a smaller copy; its mask is scaled back up


# ---- conversions
def image_to_rgba(data: bytes) -> np.ndarray:
    img = QImage.fromData(data)
    if img.isNull():
        raise ValueError("That picture can't be read.")
    img = img.convertToFormat(QImage.Format_RGBA8888)
    if max(img.width(), img.height()) > MAX_WORK_SIDE:
        img = img.scaled(MAX_WORK_SIDE, MAX_WORK_SIDE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    w, h = img.width(), img.height()
    arr = np.frombuffer(img.constBits(), np.uint8, img.bytesPerLine() * h).reshape(h, img.bytesPerLine())
    return arr[:, : w * 4].reshape(h, w, 4).copy()


def rgba_to_qimage(arr: np.ndarray) -> QImage:
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, 4 * w, QImage.Format_RGBA8888).copy()


def rgba_to_png(arr: np.ndarray) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QBuffer.WriteOnly)
    rgba_to_qimage(arr).save(buf, "PNG")
    return bytes(data)


# ---- edits (pure functions on RGBA arrays)
def adjust(arr, brightness=0, contrast=0, saturation=0, grayscale=False):
    """brightness / contrast / saturation from -100 to 100."""
    if not (brightness or contrast or saturation or grayscale):
        return arr
    rgb = arr[..., :3].astype(np.float32)
    if saturation or grayscale:
        gray = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
        factor = 0.0 if grayscale else 1.0 + saturation / 100.0
        rgb = gray[..., None] + (rgb - gray[..., None]) * factor
    if contrast:
        c = contrast * 2.55
        rgb = (259 * (c + 255)) / (255 * (259 - c)) * (rgb - 128) + 128
    if brightness:
        rgb = rgb + brightness * 1.28
    out = arr.copy()
    out[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    return out


def _edge_pixels(rgb):
    return np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]]).astype(np.float32)


def remove_background(arr, tolerance=28):
    """The picture with its background made transparent (see the module notes)."""
    rgb = np.ascontiguousarray(arr[..., :3])
    h, w = rgb.shape[:2]
    edge = _edge_pixels(rgb)
    if edge.std(axis=0).max() < 18:                          # a plain background: take that colour out
        return _flood_from_edges(arr, tolerance)
    small = rgb
    scale = min(1.0, GRABCUT_SIDE / max(h, w))
    if scale < 1.0:
        small = cv2.resize(rgb, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    sh, sw = small.shape[:2]
    mask = np.zeros((sh, sw), np.uint8)
    inset = max(2, int(min(sh, sw) * 0.03))
    rect = (inset, inset, max(1, sw - 2 * inset), max(1, sh - 2 * inset))
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.grabCut(cv2.cvtColor(small, cv2.COLOR_RGB2BGR), mask, rect, bgd, fgd, 5, cv2.GC_INIT_WITH_RECT)
    keep = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    if scale < 1.0:
        keep = cv2.resize(keep, (w, h), interpolation=cv2.INTER_LINEAR)
    keep = cv2.GaussianBlur(keep, (3, 3), 0)                  # soft, not jagged, edges
    out = arr.copy()
    out[..., 3] = np.minimum(out[..., 3], keep)
    return out


def _flood_from_edges(arr, tolerance):
    rgb = np.ascontiguousarray(arr[..., :3])
    h, w = rgb.shape[:2]
    mask = np.zeros((h + 2, w + 2), np.uint8)
    diff = (tolerance,) * 3
    flags = 4 | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (255 << 8)
    step = max(1, min(h, w) // 40)
    seeds = [(x, 0) for x in range(0, w, step)] + [(x, h - 1) for x in range(0, w, step)]
    seeds += [(0, y) for y in range(0, h, step)] + [(w - 1, y) for y in range(0, h, step)]
    work = rgb.copy()
    for x, y in seeds:
        if mask[y + 1, x + 1] == 0:
            cv2.floodFill(work, mask, (x, y), (0, 0, 0), diff, diff, flags)
    gone = mask[1:-1, 1:-1] > 0
    out = arr.copy()
    alpha = out[..., 3].copy()
    alpha[gone] = 0
    out[..., 3] = cv2.GaussianBlur(alpha, (3, 3), 0) if gone.any() else alpha
    return out


def erase_color_at(arr, x, y, tolerance=28):
    """The area of similar colour around (x, y) made transparent (the magic eraser)."""
    rgb = np.ascontiguousarray(arr[..., :3]).copy()
    h, w = rgb.shape[:2]
    if not (0 <= x < w and 0 <= y < h):
        return arr
    mask = np.zeros((h + 2, w + 2), np.uint8)
    diff = (tolerance,) * 3
    cv2.floodFill(rgb, mask, (int(x), int(y)), (0, 0, 0), diff, diff,
                  4 | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (255 << 8))
    out = arr.copy()
    out[..., 3][mask[1:-1, 1:-1] > 0] = 0
    return out


def crop(arr, x0, y0, x1, y1):
    h, w = arr.shape[:2]
    x0, x1 = sorted((max(0, min(w, int(x0))), max(0, min(w, int(x1)))))
    y0, y1 = sorted((max(0, min(h, int(y0))), max(0, min(h, int(y1)))))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return arr
    return arr[y0:y1, x0:x1].copy()


# ---- the picture view: checkerboard behind, crop box and magic eraser on top
class _Canvas(QWidget):
    clicked = Signal(float, float)   # a point in picture pixels (magic eraser)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(520, 420)
        self.image = QImage()
        self.mode = None            # None, "crop" or "erase"
        self.crop_box = None        # QRectF in picture pixels
        self._press = None

    def set_image(self, image):
        self.image = image
        self.update()

    def _frame(self):
        if self.image.isNull():
            return QRectF(), 1.0
        area = QRectF(self.rect()).adjusted(12, 12, -12, -12)
        scale = min(area.width() / self.image.width(), area.height() / self.image.height())
        w, h = self.image.width() * scale, self.image.height() * scale
        return QRectF(area.center().x() - w / 2, area.center().y() - h / 2, w, h), scale

    def to_image(self, point):
        frame, scale = self._frame()
        return QPointF((point.x() - frame.x()) / scale, (point.y() - frame.y()) / scale)

    def paintEvent(self, _event):
        p = QPainter(self)
        frame, scale = self._frame()
        if frame.isEmpty():
            return
        tile = QPixmap(16, 16)
        tile.fill(QColor("#ffffff"))
        tp = QPainter(tile)
        tp.fillRect(0, 0, 8, 8, QColor("#e4e6ee"))
        tp.fillRect(8, 8, 8, 8, QColor("#e4e6ee"))
        tp.end()
        p.fillRect(frame, tile)      # transparency shows as a checkerboard
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawImage(frame, self.image)
        if self.crop_box is not None:
            box = QRectF(frame.x() + self.crop_box.x() * scale, frame.y() + self.crop_box.y() * scale,
                         self.crop_box.width() * scale, self.crop_box.height() * scale).normalized()
            shade = QColor(10, 10, 20, 120)
            p.fillRect(QRectF(frame.left(), frame.top(), frame.width(), box.top() - frame.top()), shade)
            p.fillRect(QRectF(frame.left(), box.bottom(), frame.width(), frame.bottom() - box.bottom()), shade)
            p.fillRect(QRectF(frame.left(), box.top(), box.left() - frame.left(), box.height()), shade)
            p.fillRect(QRectF(box.right(), box.top(), frame.right() - box.right(), box.height()), shade)
            p.setPen(QPen(QColor(theme.ACCENT), 2, Qt.DashLine))
            p.drawRect(box)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        point = self.to_image(event.position())
        if self.mode == "crop":
            self._press = point
            self.crop_box = QRectF(point, point)
            self.update()
        elif self.mode == "erase":
            self.clicked.emit(point.x(), point.y())

    def mouseMoveEvent(self, event):
        if self.mode == "crop" and self._press is not None:
            self.crop_box = QRectF(self._press, self.to_image(event.position())).normalized()
            self.update()

    def mouseReleaseEvent(self, event):
        self._press = None

    def tabletEvent(self, event):
        event.ignore()   # a pen works like the mouse here


class ImageEditorDialog(QDialog):
    def __init__(self, image_bytes: bytes, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Photo")
        self.resize(900, 640)
        self.original = image_to_rgba(image_bytes)
        self.base = self.original.copy()
        self.history = []

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(14)
        side = QVBoxLayout()
        side.setSpacing(6)
        title = QLabel("Edit photo")
        title.setObjectName("dialogTitle")
        side.addWidget(title)

        def button(text, icon, slot, tip=""):
            b = QPushButton(text)
            if icon:
                b.setIcon(icons.icon(icon))
            b.setToolTip(tip)
            b.clicked.connect(slot)
            side.addWidget(b)
            return b

        button("Remove Background", "layers", self._remove_background,
               "Make the background transparent (plain backgrounds by colour, photos with GrabCut)")
        self.erase_btn = button("Magic Eraser", "eraser", lambda: self._set_mode("erase"),
                                "Click a colour in the picture to make that area transparent")
        self.erase_btn.setCheckable(True)
        self.crop_btn = button("Crop", "crop", lambda: self._set_mode("crop"), "Drag a box on the picture, then Apply Crop")
        self.crop_btn.setCheckable(True)
        self.apply_crop_btn = button("Apply Crop", "check", self._apply_crop)
        self.apply_crop_btn.setVisible(False)
        grid = QGridLayout()
        grid.setSpacing(6)
        for i, (text, icon, slot) in enumerate((
                ("Left", "rotate-left", lambda: self._change(np.rot90(self.base, 1))),
                ("Right", "rotate-right", lambda: self._change(np.rot90(self.base, -1))),
                ("Flip ↔", None, lambda: self._change(self.base[:, ::-1])),
                ("Flip ↕", None, lambda: self._change(self.base[::-1])))):
            b = QPushButton(text)
            if icon:
                b.setIcon(icons.icon(icon))
            b.clicked.connect(slot)
            grid.addWidget(b, i // 2, i % 2)
        side.addLayout(grid)

        form = QFormLayout()
        form.setSpacing(6)
        self.tolerance = self._slider(5, 90, 28)
        form.addRow("Tolerance", self.tolerance)
        self.brightness = self._slider(-100, 100, 0)
        self.contrast = self._slider(-100, 100, 0)
        self.saturation = self._slider(-100, 100, 0)
        form.addRow("Brightness", self.brightness)
        form.addRow("Contrast", self.contrast)
        form.addRow("Saturation", self.saturation)
        self.gray = QCheckBox("Black && white")
        self.gray.toggled.connect(self._show)
        form.addRow("", self.gray)
        side.addLayout(form)
        for s in (self.brightness, self.contrast, self.saturation):
            s.valueChanged.connect(self._show)
        side.addStretch(1)
        row = QHBoxLayout()
        undo = QPushButton("Undo")
        undo.setIcon(icons.icon("undo"))
        undo.clicked.connect(self._undo)
        reset = QPushButton("Reset")
        reset.clicked.connect(self._reset)
        row.addWidget(undo)
        row.addWidget(reset)
        side.addLayout(row)
        row = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Apply")
        ok.setObjectName("primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        side.addLayout(row)
        holder = QWidget()
        holder.setLayout(side)
        holder.setFixedWidth(250)
        root.addWidget(holder)
        self.canvas = _Canvas(self)
        self.canvas.clicked.connect(self._erase_at)
        root.addWidget(self.canvas, 1)
        self._show()

    @staticmethod
    def _slider(lo, hi, value):
        s = QSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        s.setValue(value)
        return s

    # ---- state
    def adjusted(self):
        return adjust(self.base, self.brightness.value(), self.contrast.value(), self.saturation.value(),
                      self.gray.isChecked())

    def _show(self):
        self.canvas.set_image(rgba_to_qimage(self.adjusted()))

    def _change(self, new):
        self.history.append(self.base)
        self.history = self.history[-20:]
        self.base = np.ascontiguousarray(new)
        self.canvas.crop_box = None
        self._show()

    def _undo(self):
        if self.history:
            self.base = self.history.pop()
            self._show()

    def _reset(self):
        self._change(self.original.copy())
        for s in (self.brightness, self.contrast, self.saturation):
            s.setValue(0)
        self.gray.setChecked(False)

    def _set_mode(self, mode):
        on = self.canvas.mode != mode
        self.canvas.mode = mode if on else None
        self.crop_btn.setChecked(self.canvas.mode == "crop")
        self.erase_btn.setChecked(self.canvas.mode == "erase")
        self.apply_crop_btn.setVisible(self.canvas.mode == "crop")
        self.canvas.setCursor(Qt.CrossCursor if self.canvas.mode else Qt.ArrowCursor)
        if self.canvas.mode != "crop":
            self.canvas.crop_box = None
            self.canvas.update()

    # ---- tools
    def _remove_background(self):
        from PySide6.QtGui import QGuiApplication

        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self._change(remove_background(self.base, self.tolerance.value()))
        except cv2.error as e:
            QMessageBox.warning(self, "Edit Photo", f"The background couldn't be removed:\n{e}")
        finally:
            QGuiApplication.restoreOverrideCursor()

    def _erase_at(self, x, y):
        self._change(erase_color_at(self.base, int(x), int(y), self.tolerance.value()))

    def _apply_crop(self):
        box = self.canvas.crop_box
        if box is None or box.width() < 2 or box.height() < 2:
            QMessageBox.information(self, "Crop", "Drag a box on the picture first.")
            return
        self._change(crop(self.base, box.left(), box.top(), box.right(), box.bottom()))
        self._set_mode("crop")   # done cropping

    def result_png(self) -> bytes:
        return rgba_to_png(self.adjusted())
