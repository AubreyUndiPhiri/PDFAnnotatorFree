"""Build AUPedean.ttf from the original handwritten alphabet photo.

    python tools/font_builder/aupedean/build_aupedean.py
        [--photo tools/font_builder/aupedean/source_photo.png]
        [--out app/assets/fonts/AUPedean.ttf] [--preview preview.png]

source_photo.png shows the AUPedean alphabet written in blue ballpoint on
lined paper: 26 glyphs, left to right and top to bottom, one per English
letter A-Z, in rows of 7, 6, 6 and 7. Two letters are made of two marks
written close together (J in row 2, R in row 3); marks closer than
MERGE_GAP_PX are treated as one letter.

The letters are traced from the photo. The alphabet has no digits or
punctuation, so those are drawn as simple monoline strokes at the same pen
weight as the handwriting (see EXTRA_GLYPHS), so typing numbers or
punctuation never shows empty boxes. Lowercase letters use the same
glyphs as uppercase.
"""
import argparse
import os
import sys
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "app"))

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPainterPathStroker, QPen, QPolygonF,
)

from pdfannotator.handwriting import builder as hb  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_PHOTO = HERE / "source_photo.png"
DEFAULT_OUT = ROOT / "app" / "assets" / "fonts" / "AUPedean.ttf"

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
ROW_SIZES = (7, 6, 6, 7)          # letters per written row, as a sanity check
TITLE_BOTTOM_PX = 285             # the handwritten "Aupedean" title sits above this
IGNORE_BELOW_PX = 760             # a stray pen line runs across the lower page
MERGE_GAP_PX = 12                 # marks this close in a row form one letter
MIN_OVERLAP = 0.4                 # ...and only if they sit side by side (vertical overlap)
MIN_GLYPH_HEIGHT_PX = 30          # shorter groups are fragments (e.g. the title's tail)
INK_GRAY_DARK, INK_GRAY_LIGHT = 90, 135   # grey levels: fully ink .. not ink
GROUP_RADIUS = 4                  # px: strokes of one mark closer than 2x this are joined
MIN_GLYPH_AREA_PX = 500           # smaller groups are the title's tail or dust
UPSAMPLE = 4                      # trace at 4x the photo resolution for smooth curves

UPM = 1000
CAP_UNITS = 700
SIDE_BEARING = 55
FAMILY = "AUPedean"


# ------------------------------------------------------------------ photo -> ink

def load_rgb(path):
    img = QImage(str(path))
    if img.isNull():
        sys.exit(f"Could not read {path}")
    img = img.convertToFormat(QImage.Format_RGB888)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    buf = np.frombuffer(img.constBits(), np.uint8, count=img.sizeInBytes())
    return buf.reshape(h, bpl)[:, :w * 3].reshape(h, w, 3).astype(np.float32)


def ink_probability(rgb):
    """0..1 ink score from brightness. In this photo the pen ink is very dark
    (grey level ~40) while the paper is ~185 and the printed ruled lines ~150,
    so darkness alone separates them; a small blue bias keeps grey shadows out."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    gray = 0.299 * r + 0.587 * g + 0.114 * b
    darkness = np.clip((INK_GRAY_LIGHT - gray) / (INK_GRAY_LIGHT - INK_GRAY_DARK), 0, 1)
    blue_ok = np.clip(((b - r) + 6) / 10, 0, 1)
    p = darkness * blue_ok
    p[:TITLE_BOTTOM_PX] = 0
    p[IGNORE_BELOW_PX:] = 0
    return p


def dilate(mask, radius):
    out = mask.copy()
    for _ in range(radius):
        out = hb._dilate(out)
    return out


def glyph_groups(mask):
    """Bounding boxes and membership masks of each written letter, in reading
    order, as a list of rows."""
    grouped = dilate(mask, GROUP_RADIUS)
    outlines = [p for p in hb._outlines(grouped) if abs(hb._signed_area(p)) > 50]
    polys = [QPolygonF([QPointF(x, y) for x, y in p]) for p in outlines]
    outer = [i for i, p in enumerate(outlines)
             if not any(j != i and polys[j].containsPoint(QPointF(*p[0]), Qt.OddEvenFill) for j in range(len(polys)))]
    groups = []
    for i in outer:
        # the outline was grown by GROUP_RADIUS; shrink the box back to the ink itself
        x0, y0 = outlines[i].min(axis=0) + GROUP_RADIUS
        x1, y1 = outlines[i].max(axis=0) - GROUP_RADIUS
        groups.append({"box": [x0, y0, x1, y1], "polys": [outlines[i]]})

    groups.sort(key=lambda g: (g["box"][1] + g["box"][3]) / 2)
    rows, current = [], [groups[0]]
    for g in groups[1:]:
        prev = current[-1]
        if abs((g["box"][1] + g["box"][3]) / 2 - (prev["box"][1] + prev["box"][3]) / 2) < 45:
            current.append(g)
        else:
            rows.append(current)
            current = [g]
    rows.append(current)

    result = []
    for row in rows:
        row.sort(key=lambda g: g["box"][0])
        merged = []
        for g in row:
            if merged and g["box"][0] - merged[-1]["box"][2] < MERGE_GAP_PX and _overlap(merged[-1], g) >= MIN_OVERLAP:
                m = merged[-1]
                m["box"] = [min(m["box"][0], g["box"][0]), min(m["box"][1], g["box"][1]),
                            max(m["box"][2], g["box"][2]), max(m["box"][3], g["box"][3])]
                m["polys"] += g["polys"]
            else:
                merged.append(g)
        merged = [g for g in merged if (g["box"][2] - g["box"][0]) * (g["box"][3] - g["box"][1]) >= MIN_GLYPH_AREA_PX
                  and g["box"][3] - g["box"][1] >= MIN_GLYPH_HEIGHT_PX]
        if merged:
            result.append(merged)
    return result


def _overlap(a, b):
    """Vertical overlap of two groups, as a fraction of the shorter one."""
    top = max(a["box"][1], b["box"][1])
    bottom = min(a["box"][3], b["box"][3])
    shorter = min(a["box"][3] - a["box"][1], b["box"][3] - b["box"][1])
    return max(0.0, bottom - top) / max(shorter, 1)


def membership(shape, polys):
    """Pixels belonging to one letter (inside its grouped outlines)."""
    h, w = shape
    img = QImage(w, h, QImage.Format_Grayscale8)
    img.fill(0)
    painter = QPainter(img)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(255, 255, 255))
    for p in polys:
        painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in p]))
    painter.end()
    return hb._to_array(img) > 127


def upsampled(p, factor):
    img = hb._to_image((np.clip(p, 0, 1) * 255).astype(np.uint8))
    img = img.scaled(img.width() * factor, img.height() * factor, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return hb._to_array(img).astype(np.float32) / 255


# ------------------------------------------------------------------ outlines -> glyphs

def outlines_to_contours(polys_px, to_units):
    """Pixel outlines -> font-unit contours with TrueType orientation."""
    qpolys = [QPolygonF([QPointF(x, y) for x, y in p]) for p in polys_px]
    contours = []
    for i, pts in enumerate(polys_px):
        depth = sum(1 for j, q in enumerate(qpolys) if j != i and q.containsPoint(QPointF(*pts[0]), Qt.OddEvenFill))
        simple = hb._simplify_closed(pts, 0.9)
        if len(simple) < 3:
            continue
        fp = to_units(simple)
        if (hb._signed_area(fp) < 0) == (depth % 2 == 1):
            fp = fp[::-1]
        contours.append(fp)
    return contours


def trace_letter(p, group, stroke_widths):
    x0, y0, x1, y1 = (int(v) for v in group["box"])
    pad = 6
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(p.shape[1], x1 + pad), min(p.shape[0], y1 + pad)
    member = membership(p.shape, group["polys"])[y0:y1, x0:x1]
    crop = p[y0:y1, x0:x1] * member
    big = hb._box_blur(upsampled(crop, UPSAMPLE), 2)
    mask = big > 0.5
    raw = [q for q in hb._outlines(mask) if abs(hb._signed_area(q)) >= 60]
    ink_area = mask.sum()
    perimeter = sum(np.hypot(*np.diff(np.vstack([q, q[:1]]), axis=0).T).sum() for q in raw)
    if perimeter:
        stroke_widths.append(2 * ink_area / perimeter)
    return raw, (x0, y0), mask.shape


# ------------------------------------------------------------------ extra monoline glyphs

# Drawn in a 0..600 wide, 0..700 tall box (y down, 700 = baseline), stroked
# with the handwriting's pen width. Each character is a list of strokes:
#   [(x, y), (x, y), ...]         straight segments through the points
#   ["~", (x, y), (x, y), ...]    a smooth curve through the points
#   [("e", cx, cy, rx, ry)]       an ellipse outline
#   [("d", cx, cy)]               a filled dot
EXTRA_GLYPHS = {
    "0": [[("e", 300, 380, 200, 320)]],
    "1": [[(190, 170), (330, 60), (330, 700)]],
    "2": [["~", (130, 200), (200, 70), (340, 60), (460, 150), (440, 300), (130, 700)], [(130, 700), (490, 700)]],
    "3": [["~", (130, 120), (270, 50), (420, 110), (420, 250), (280, 330)],
          ["~", (280, 330), (450, 420), (460, 580), (320, 700), (140, 640)]],
    "4": [[(390, 700), (390, 60), (90, 480), (500, 480)]],
    "5": [[(450, 60), (170, 60), (140, 320)], ["~", (140, 320), (300, 270), (450, 380), (460, 560), (330, 700), (130, 650)]],
    "6": [["~", (420, 90), (270, 60), (150, 230), (130, 480), (200, 670), (330, 700), (450, 580), (440, 420),
           (300, 350), (140, 440)]],
    "7": [[(100, 70), (490, 70), (230, 700)]],
    "8": [[("e", 300, 200, 150, 140)], [("e", 300, 520, 180, 180)]],
    "9": [["~", (450, 260), (300, 360), (160, 300), (150, 150), (270, 60), (410, 110), (450, 280), (420, 520),
           (320, 690), (160, 670)]],
    ".": [[("d", 150, 660)]],
    ",": [[(170, 640), (140, 790)]],
    ":": [[("d", 150, 300)], [("d", 150, 660)]],
    ";": [[("d", 170, 300)], [(180, 640), (150, 790)]],
    "!": [[(160, 60), (160, 500)], [("d", 160, 660)]],
    "?": [["~", (110, 170), (220, 60), (380, 70), (440, 190), (380, 300), (270, 380), (260, 500)], [("d", 260, 660)]],
    "'": [[(150, 60), (140, 230)]],
    '"': [[(120, 60), (110, 230)], [(250, 60), (240, 230)]],
    "-": [[(90, 420), (360, 420)]],
    "_": [[(40, 740), (560, 740)]],
    "(": [["~", (290, 30), (150, 250), (130, 470), (180, 650), (290, 760)]],
    ")": [["~", (110, 30), (250, 250), (270, 470), (220, 650), (110, 760)]],
    "[": [[(290, 30), (130, 30), (130, 760), (290, 760)]],
    "]": [[(110, 30), (270, 30), (270, 760), (110, 760)]],
    "/": [[(420, 30), (120, 760)]],
    "\\": [[(120, 30), (420, 760)]],
    "+": [[(300, 230), (300, 610)], [(110, 420), (490, 420)]],
    "=": [[(110, 330), (490, 330)], [(110, 510), (490, 510)]],
    "*": [[(300, 70), (300, 330)], [(180, 130), (420, 270)], [(420, 130), (180, 270)]],
    "&": [["~", (500, 700), (190, 330), (180, 160), (280, 60), (390, 150), (340, 290), (130, 480), (160, 660),
           (330, 690), (500, 470)]],
    "%": [[("e", 150, 180, 90, 110)], [(480, 60), (120, 700)], [("e", 450, 580, 90, 110)]],
    "#": [[(230, 120), (180, 700)], [(410, 120), (360, 700)], [(110, 300), (510, 300)], [(90, 520), (490, 520)]],
    "@": [["~", (430, 470), (420, 300), (310, 260), (220, 360), (240, 470), (330, 490), (420, 420), (470, 520),
           (560, 470), (570, 300), (470, 130), (300, 90), (130, 190), (70, 400), (160, 620), (380, 690), (520, 630)]],
    "$": [["~", (450, 150), (300, 90), (160, 150), (160, 300), (300, 360), (440, 430), (440, 600), (300, 660),
           (140, 600)], [(300, 20), (300, 740)]],
    "<": [[(470, 180), (130, 420), (470, 660)]],
    ">": [[(130, 180), (470, 420), (130, 660)]],
}
EXTRA_WIDTHS = {".": 300, ",": 300, ":": 300, ";": 320, "!": 320, "'": 280, '"': 360, "-": 450, "(": 400, ")": 400,
                "[": 400, "]": 400, "/": 540, "\\": 540, "?": 520, "%": 600, "#": 600, "@": 640}


def _stroke_path(strokes, pen_units):
    """(centre-line path to stroke, filled dots path) for one character."""
    lines, dots = QPainterPath(), QPainterPath()
    for stroke in strokes:
        first = stroke[0]
        if isinstance(first, tuple) and first[0] == "d":
            dots.addEllipse(QPointF(first[1], first[2]), pen_units * 0.75, pen_units * 0.75)
        elif isinstance(first, tuple) and first[0] == "e":
            _, cx, cy, rx, ry = first
            lines.addEllipse(QPointF(cx, cy), rx, ry)
        elif first == "~":
            pts = [QPointF(*pt) for pt in stroke[1:]]
            sub = QPainterPath(pts[0])
            for i in range(1, len(pts) - 1):   # quadratic segments through the points' midpoints
                end = (pts[i] + pts[i + 1]) / 2 if i < len(pts) - 2 else pts[i + 1]
                sub.quadTo(pts[i], end)
            lines.addPath(sub)
        else:
            pts = [QPointF(*pt) for pt in stroke]
            sub = QPainterPath(pts[0])
            for pt in pts[1:]:
                sub.lineTo(pt)
            lines.addPath(sub)
    return lines, dots


def stroke_glyph(strokes, pen_units):
    """Monoline glyph outline (font units) from EXTRA_GLYPHS strokes."""
    lines, dots = _stroke_path(strokes, pen_units)
    stroker = QPainterPathStroker()
    stroker.setWidth(pen_units)
    stroker.setCapStyle(Qt.RoundCap)
    stroker.setJoinStyle(Qt.RoundJoin)
    outline = stroker.createStroke(lines).united(dots).simplified()
    polys = []
    for poly in outline.toSubpathPolygons():
        pts = np.array([(pt.x(), pt.y()) for pt in poly])
        if len(pts) > 1 and np.allclose(pts[0], pts[-1]):
            pts = pts[:-1]
        if len(pts) >= 3 and abs(hb._signed_area(pts)) > 20:
            polys.append(pts)
    return outlines_to_contours(polys, lambda s: np.column_stack([s[:, 0], 700 - s[:, 1]]))


# ------------------------------------------------------------------ font

def build(photo, out, preview=None):
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    p = ink_probability(load_rgb(photo))
    rows = glyph_groups(p > 0.5)
    sizes = tuple(len(r) for r in rows)
    letters = [g for row in rows for g in row]
    if len(letters) != len(LETTERS):
        sys.exit(f"Expected 26 letters in rows {ROW_SIZES}, found {len(letters)} in rows {sizes}")
    if sizes != ROW_SIZES:
        print(f"note: rows have {sizes} letters (expected {ROW_SIZES})")

    stroke_widths = []
    traced = [trace_letter(p, g, stroke_widths) for g in letters]
    heights = [(g["box"][3] - g["box"][1]) for g in letters]
    px_per_unit_scale = CAP_UNITS / (np.median(heights) * UPSAMPLE)   # font units per upsampled px

    glyphs = {".notdef": hb._notdef(), "space": TTGlyphPen(None).glyph()}
    advances = {".notdef": 500, "space": 320}
    cmap = {32: "space", 0xA0: "space"}

    def to_glyph(contours, fixed_advance=None):
        xmin = min(float(c[:, 0].min()) for c in contours)
        xmax = max(float(c[:, 0].max()) for c in contours)
        shift = SIDE_BEARING - xmin if fixed_advance is None else (fixed_advance - (xmax - xmin)) / 2 - xmin
        pen = TTGlyphPen(None)
        for c in contours:
            pen.qCurveTo(*[(round(x + shift), round(y)) for x, y in c], None)
            pen.closePath()
        advance = fixed_advance or int(xmax - xmin + 2 * SIDE_BEARING)
        return pen.glyph(), advance

    for ch, (raw, origin, shape) in zip(LETTERS, traced):
        bottom_px = shape[0] - 6 * UPSAMPLE   # the letter's box bottom sits on the baseline

        def to_units(s, bottom=bottom_px):
            return np.column_stack([s[:, 0] * px_per_unit_scale, (bottom - s[:, 1]) * px_per_unit_scale])

        contours = outlines_to_contours(raw, to_units)
        name = f"uni{ord(ch):04X}"
        glyphs[name], advances[name] = to_glyph(contours)
        cmap[ord(ch)] = name
        cmap[ord(ch.lower())] = name

    pen_units = float(np.median(stroke_widths)) * px_per_unit_scale
    for ch, strokes in EXTRA_GLYPHS.items():
        contours = stroke_glyph(strokes, pen_units)
        name = f"uni{ord(ch):04X}"
        glyphs[name], advances[name] = to_glyph(contours, EXTRA_WIDTHS.get(ch, 600))
        cmap[ord(ch)] = name
    # typographic look-alikes typed by word processors
    for alias, base in {"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"}.items():
        cmap[ord(alias)] = f"uni{ord(base):04X}"

    order = list(glyphs)
    fb = FontBuilder(UPM, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyphs)
    glyf = fb.font["glyf"]
    fb.setupHorizontalMetrics({n: (advances[n], getattr(glyf[n], "xMin", 0) if glyf[n].numberOfContours else 0)
                               for n in order})
    ymax = max([getattr(glyf[n], "yMax", 0) for n in order] + [CAP_UNITS + 100])
    ymin = min([getattr(glyf[n], "yMin", 0) for n in order] + [-200])
    fb.setupHorizontalHeader(ascent=ymax, descent=ymin)
    fb.setupNameTable({
        "familyName": FAMILY, "styleName": "Regular", "uniqueFontIdentifier": f"{FAMILY}-Regular-1.0",
        "fullName": FAMILY, "psName": f"{FAMILY}-Regular", "version": "Version 1.000",
        "copyright": "AUPedean script. Letters traced from the author's handwritten alphabet.",
    })
    fb.setupOS2(sTypoAscender=ymax, sTypoDescender=ymin, sTypoLineGap=0, usWinAscent=ymax, usWinDescent=-ymin,
                sCapHeight=CAP_UNITS, sxHeight=CAP_UNITS, fsType=0, achVendID="AUPD")
    fb.setupPost()
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fb.save(str(out))
    print(f"Wrote {out}  ({len(LETTERS)} letters + {len(EXTRA_GLYPHS)} digits/punctuation, pen {pen_units:.0f} units)")
    if preview:
        render_preview(out, preview)


def render_preview(font_path, out_png):
    """Key image: each written letter labelled with the English letter it
    types, plus sample text."""
    import pymupdf as fitz

    doc = fitz.open()
    page = doc.new_page(width=720, height=560)
    page.insert_font(fontname="AUP", fontfile=str(font_path))
    for i, ch in enumerate(LETTERS):
        col, row = i % 7, i // 7
        x, y = 30 + col * 98, 40 + row * 105
        page.draw_rect(fitz.Rect(x, y, x + 88, y + 95), color=(0.85, 0.87, 0.9), width=0.6)
        page.insert_text((x + 5, y + 14), ch, fontsize=10, color=(0.4, 0.45, 0.55))
        page.insert_text((x + 22, y + 78), ch, fontname="AUP", fontsize=52, color=(0.08, 0.1, 0.35))
    page.insert_text((30, 480), "Hello world, typed in AUPedean!", fontname="AUP", fontsize=26, color=(0.08, 0.1, 0.35))
    page.insert_text((30, 525), "0123456789 .,:;!?'\"-()[]/&+=*%#@$<>", fontname="AUP", fontsize=22, color=(0.08, 0.1, 0.35))
    page.get_pixmap(dpi=110).save(str(out_png))
    print(f"Wrote preview {out_png}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--photo", default=str(DEFAULT_PHOTO))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--preview", help="write a PNG key: each letter, the English letter it types, sample text")
    args = ap.parse_args()
    app = QGuiApplication(sys.argv)  # noqa: F841
    build(args.photo, args.out, args.preview)


if __name__ == "__main__":
    main()
