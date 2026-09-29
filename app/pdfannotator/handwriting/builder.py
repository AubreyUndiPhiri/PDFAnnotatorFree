"""Turn a photo or scan of a filled-in glyph sheet into a TrueType font.

Pipeline: find the four black corner squares, straighten the sheet with a
perspective transform, read the ink in each box, trace it into smooth
quadratic outlines and write a .ttf with fontTools. Uses only numpy and Qt
(already shipped with the app), so it also works inside the packaged exe.
Lowercase letters reuse the uppercase glyphs."""
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBitmap, QImage, QImageReader, QPainter, QPainterPath, QPolygonF, QRegion, QTransform

from . import layout as L

SCALE = 4.0            # straightened-sheet pixels per PDF point
UPM = 1000
CAP_UNITS = 700        # height of the cap line above the baseline, in font units
SIDE_BEARING = 60
SPACE_WIDTH = 300
MIN_INK_PX = 120       # fewer ink pixels than this => the box is treated as empty
MIN_BLOB_PX = 40       # smaller specks are dropped as dust/noise
INK_LEVEL = 0.58       # ink = darker than this fraction of the paper brightness


class BuildError(Exception):
    """A problem the person can fix (unreadable photo, markers not found...)."""


# ------------------------------------------------------------------ image helpers

def _to_array(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format_Grayscale8)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    buf = np.frombuffer(img.constBits(), np.uint8, count=img.sizeInBytes())
    return buf.reshape(h, bpl)[:, :w].copy()


def _to_image(arr: np.ndarray) -> QImage:
    arr = np.ascontiguousarray(arr, dtype=np.uint8)
    h, w = arr.shape
    return QImage(arr.data, w, h, w, QImage.Format_Grayscale8).copy()


def _otsu(gray: np.ndarray) -> int:
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    p = hist / hist.sum()
    omega = np.cumsum(p)
    mu = np.cumsum(p * np.arange(256))
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mu[-1] * omega - mu) ** 2 / (omega * (1 - omega))
    return int(np.nanargmax(np.nan_to_num(between)))


def _box_blur(a: np.ndarray, r: int) -> np.ndarray:
    """Separable box blur of radius r (edges clamped)."""
    out = a.astype(np.float32)
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (r + 1, r)
        c = np.cumsum(np.pad(out, pad, mode="edge"), axis=axis)
        hi = np.take(c, np.arange(2 * r + 1, c.shape[axis]), axis=axis)
        lo = np.take(c, np.arange(0, c.shape[axis] - 2 * r - 1), axis=axis)
        out = (hi - lo) / (2 * r + 1)
    return out


def _dilate(m: np.ndarray) -> np.ndarray:
    p = np.pad(m, 1)
    out = np.zeros_like(m)
    for dy in range(3):
        for dx in range(3):
            out |= p[dy:dy + m.shape[0], dx:dx + m.shape[1]]
    return out


def _erode(m: np.ndarray) -> np.ndarray:
    return ~_dilate(~m)


def _outlines(mask: np.ndarray) -> list[np.ndarray]:
    """Closed outlines (outer edges and holes) of the True pixels, in pixel
    coordinates, via Qt's region tracing."""
    img = _to_image(np.where(mask, 0, 255))
    bitmap = QBitmap.fromImage(img.convertToFormat(QImage.Format_Mono), Qt.ThresholdDither)
    path = QPainterPath()
    path.addRegion(QRegion(bitmap))
    result = []
    for poly in path.simplified().toSubpathPolygons():
        pts = np.array([(p.x(), p.y()) for p in poly], dtype=np.float64)
        if len(pts) > 1 and np.allclose(pts[0], pts[-1]):
            pts = pts[:-1]
        if len(pts) >= 3:
            result.append(pts)
    return result


def _signed_area(pts: np.ndarray) -> float:
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def _rdp(points: np.ndarray, eps: float) -> np.ndarray:
    """Ramer-Douglas-Peucker simplification of an open polyline."""
    if len(points) < 3:
        return points
    start, end = points[0], points[-1]
    seg = end - start
    norm = np.hypot(*seg)
    if norm == 0:
        d = np.hypot(*(points - start).T)
    else:
        rel = points - start
        d = np.abs(seg[0] * rel[:, 1] - seg[1] * rel[:, 0]) / norm
    i = int(np.argmax(d))
    if d[i] > eps:
        left = _rdp(points[:i + 1], eps)
        right = _rdp(points[i:], eps)
        return np.vstack([left[:-1], right])
    return np.vstack([start, end])


def _simplify_closed(pts: np.ndarray, eps: float) -> np.ndarray:
    far = int(np.argmax(np.hypot(*(pts - pts[0]).T)))
    if far == 0:
        return pts
    a = _rdp(pts[:far + 1], eps)
    b = _rdp(np.vstack([pts[far:], pts[:1]]), eps)
    return np.vstack([a[:-1], b[:-1]])


# ------------------------------------------------------------------ sheet

def load_image(path) -> QImage:
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)   # respect phone photo rotation (EXIF)
    img = reader.read()
    if img.isNull():
        raise BuildError(f"Could not open the image: {reader.errorString()}")
    return img.convertToFormat(QImage.Format_Grayscale8)


def find_markers(img: QImage) -> np.ndarray:
    """The 4 corner-marker centres (TL, TR, BR, BL) in image pixels."""
    factor = min(1.0, 1400.0 / max(img.width(), img.height()))
    small = img.scaled(round(img.width() * factor), round(img.height() * factor),
                       Qt.IgnoreAspectRatio, Qt.SmoothTransformation) if factor < 1 else img
    gray = _box_blur(_to_array(small), 1)
    mask = gray <= _otsu(gray.astype(np.uint8))
    min_area = mask.size * 0.00015
    candidates = []
    for pts in _outlines(mask):
        area = abs(_signed_area(pts))
        if area < min_area:
            continue
        x0, y0 = pts.min(axis=0)
        x1, y1 = pts.max(axis=0)
        w, h = x1 - x0, y1 - y0
        if h == 0 or not 0.6 < w / h < 1.6 or area < 0.8 * w * h:
            continue  # not square
        if mask[int(y0):int(y1), int(x0):int(x1)].mean() < 0.85:
            continue  # an outline or a hole, not a solid square
        candidates.append((area, ((x0 + x1) / 2, (y0 + y1) / 2)))
    if len(candidates) < 4:
        raise BuildError(
            "Couldn't find the 4 black corner squares. Photograph or scan the whole sheet, "
            "flat and in focus, with all four squares visible."
        )
    biggest = max(a for a, _ in candidates)
    pts = np.array([c for a, c in candidates if a > 0.35 * biggest]) / factor
    s, d = pts.sum(axis=1), pts[:, 0] - pts[:, 1]
    return np.array([pts[s.argmin()], pts[d.argmax()], pts[s.argmax()], pts[d.argmin()]])


def straighten(img: QImage) -> np.ndarray:
    src = find_markers(img)
    transform = QTransform()
    ok = QTransform.quadToQuad(
        QPolygonF([QPointF(x, y) for x, y in src]),
        QPolygonF([QPointF(x * SCALE, y * SCALE) for x, y in L.MARKER_CENTERS]),
        transform,
    )
    if not ok:
        raise BuildError("The corner squares are in an impossible position; please retake the photo.")
    out = QImage(round(L.PAGE_W * SCALE), round(L.PAGE_H * SCALE), QImage.Format_RGB32)
    out.fill(Qt.white)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    painter.setTransform(transform)
    painter.drawImage(0, 0, img)
    painter.end()
    return _to_array(out)


def ink_mask(sheet: np.ndarray, index: int):
    """Ink inside one box's writing area, plus the crop origin in pixels."""
    x0, y0, x1, y1 = L.drawing_rect(index)
    i = L.CROP_INSET
    px0, py0 = int((x0 + i) * SCALE), int((y0 + i) * SCALE)
    px1, py1 = int((x1 - i) * SCALE), int((y1 - i) * SCALE)
    crop = sheet[py0:py1, px0:px1].astype(np.float32)
    paper = np.percentile(crop, 90)
    mask = crop < paper * INK_LEVEL           # printed guides are light grey and drop out
    mask = _erode(_dilate(mask))               # close pinholes in strokes
    smooth = _box_blur(_box_blur(mask.astype(np.float32), 2), 2)
    return smooth > 0.5, (px0, py0)


# ------------------------------------------------------------------ outlines -> glyphs

def trace(mask, origin, baseline_px):
    """Ink outlines in font units (y up, baseline at 0): outer contours
    clockwise, holes counter-clockwise, as TrueType expects."""
    units_per_px = CAP_UNITS / ((L.BASELINE_OFFSET - L.CAP_OFFSET) * SCALE)
    raw = [p for p in _outlines(mask) if abs(_signed_area(p)) >= MIN_BLOB_PX]
    polys = [QPolygonF([QPointF(x, y) for x, y in p]) for p in raw]
    result = []
    for i, pts in enumerate(raw):
        probe = QPointF(*pts[0])
        depth = sum(1 for j, other in enumerate(polys) if j != i and other.containsPoint(probe, Qt.OddEvenFill))
        is_hole = depth % 2 == 1
        simple = _simplify_closed(pts, 1.0)
        if len(simple) < 3:
            continue
        font_pts = np.empty_like(simple)
        font_pts[:, 0] = (simple[:, 0] + origin[0]) * units_per_px
        font_pts[:, 1] = (baseline_px - (simple[:, 1] + origin[1])) * units_per_px
        clockwise = _signed_area(font_pts) < 0
        if clockwise == is_hole:
            font_pts = font_pts[::-1]
        result.append(font_pts)
    return result


def _glyph(contours):
    """Smooth quadratic outlines: every traced vertex is an off-curve point,
    with implied on-curve points at the edge midpoints."""
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    xmin = min(float(c[:, 0].min()) for c in contours)
    shift = SIDE_BEARING - xmin
    pen = TTGlyphPen(None)
    xmax = 0.0
    for c in contours:
        pts = [(round(x + shift), round(y)) for x, y in c]
        xmax = max(xmax, max(p[0] for p in pts))
        pen.qCurveTo(*pts, None)
        pen.closePath()
    return pen.glyph(), int(xmax + SIDE_BEARING)


def _notdef():
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    pen = TTGlyphPen(None)
    for x0, y0, x1, y1, reverse in ((50, 0, 450, CAP_UNITS, False), (100, 50, 400, CAP_UNITS - 50, True)):
        corners = [(x0, y0), (x0, y1), (x1, y1), (x1, y0)]
        if reverse:
            corners.reverse()
        pen.moveTo(corners[0])
        for p in corners[1:]:
            pen.lineTo(p)
        pen.closePath()
    return pen.glyph()


def build_font(image_path, out_path, family="AUPedean", debug_dir=None) -> dict:
    """Build `out_path` (.ttf) from the sheet photo. Returns
    {"path", "found": str, "skipped": str}. Raises BuildError."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    sheet = straighten(load_image(image_path))
    if debug_dir:
        Path(debug_dir).mkdir(parents=True, exist_ok=True)
        _to_image(sheet).save(str(Path(debug_dir) / "straightened.png"))

    glyphs = {".notdef": _notdef(), "space": TTGlyphPen(None).glyph()}
    advances = {".notdef": 500, "space": SPACE_WIDTH}
    cmap = {32: "space", 0xA0: "space"}
    found, skipped = [], []
    for index, ch in enumerate(L.CHARACTERS):
        mask, origin = ink_mask(sheet, index)
        contours = trace(mask, origin, L.baseline_y(index) * SCALE) if mask.sum() >= MIN_INK_PX else []
        if not contours:
            skipped.append(ch)
            continue
        if debug_dir:
            _to_image(np.where(mask, 0, 255)).save(str(Path(debug_dir) / f"cell_{index:02d}.png"))
        name = f"uni{ord(ch):04X}"
        glyphs[name], advances[name] = _glyph(contours)
        cmap[ord(ch)] = name
        found.append(ch)
    if not found:
        raise BuildError("No handwriting was found in the boxes. Use a dark pen and good light.")
    for ch in found:
        if ch.isupper() and ord(ch.lower()) not in cmap:
            cmap[ord(ch.lower())] = f"uni{ord(ch):04X}"

    order = list(glyphs)
    fb = FontBuilder(UPM, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyphs)
    glyf = fb.font["glyf"]
    fb.setupHorizontalMetrics({
        n: (advances[n], getattr(glyf[n], "xMin", 0) if glyf[n].numberOfContours else 0) for n in order
    })
    ymax = max([getattr(glyf[n], "yMax", 0) for n in order] + [CAP_UNITS + 100])
    ymin = min([getattr(glyf[n], "yMin", 0) for n in order] + [-200])
    fb.setupHorizontalHeader(ascent=ymax, descent=ymin)
    fb.setupNameTable({"familyName": family, "styleName": "Regular"})
    fb.setupOS2(sTypoAscender=ymax, sTypoDescender=ymin, sTypoLineGap=0, usWinAscent=ymax,
                usWinDescent=-ymin, sCapHeight=CAP_UNITS, sxHeight=CAP_UNITS // 2, fsType=0)
    fb.setupPost()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fb.save(str(out_path))
    return {"path": str(out_path), "found": "".join(found), "skipped": "".join(skipped)}
