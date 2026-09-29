"""Build AUPedean.ttf from a filled-in glyph sheet (photo or scan).

    python tools/font_builder/build_font.py scan.jpg
        [--out app/assets/fonts/AUPedean.ttf] [--family AUPedean] [--debug debug_dir]

Lowercase letters reuse the uppercase glyphs, so typing in either case works.
"""
import argparse
from pathlib import Path

import cv2
import numpy as np
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

import layout as L

SCALE = 4.0            # warped-sheet pixels per PDF point
UPM = 1000
CAP_UNITS = 700        # height of the cap line above the baseline, in font units
SIDE_BEARING = 60
SPACE_WIDTH = 300
MIN_INK_PX = 120       # fewer ink pixels than this => the box is treated as empty
MIN_BLOB_PX = 40       # smaller specks are dropped as dust/noise

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "app" / "assets" / "fonts" / "AUPedean.ttf"


# ------------------------------------------------------------------ sheet

def find_markers(gray):
    """Return the 4 corner-marker centres (TL, TR, BR, BL) in image pixels."""
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, mask = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    min_area = gray.shape[0] * gray.shape[1] * 0.00015
    candidates = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < min_area:
            continue
        approx = cv2.approxPolyDP(c, 0.06 * cv2.arcLength(c, True), True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        x, y, w, h = cv2.boundingRect(approx)
        if not 0.6 < w / h < 1.6:
            continue
        filled = np.zeros_like(mask)
        cv2.drawContours(filled, [c], -1, 255, -1)
        if cv2.mean(mask, mask=filled)[0] < 0.85 * 255:
            continue  # an outline (e.g. a grid cell), not a solid marker
        m = cv2.moments(c)
        candidates.append((area, (m["m10"] / m["m00"], m["m01"] / m["m00"])))
    if len(candidates) < 4:
        raise SystemExit(
            "Could not find the 4 black corner squares. Photograph the whole sheet, "
            "flat and in focus, with all four squares visible."
        )
    biggest = max(a for a, _ in candidates)
    pts = np.array([p for a, p in candidates if a > 0.35 * biggest])
    s, d = pts.sum(axis=1), pts[:, 0] - pts[:, 1]
    return np.array([pts[s.argmin()], pts[d.argmax()], pts[s.argmax()], pts[d.argmin()]], dtype=np.float32)


def straighten(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    src = find_markers(gray)
    dst = np.array(L.MARKER_CENTERS, dtype=np.float32) * SCALE
    matrix = cv2.getPerspectiveTransform(src, dst)
    size = (int(L.PAGE_W * SCALE), int(L.PAGE_H * SCALE))
    return cv2.warpPerspective(gray, matrix, size, flags=cv2.INTER_CUBIC, borderValue=255)


def ink_mask(sheet, index):
    """Binary mask of pen ink inside the drawing area of one cell, plus the
    crop origin (pixels) so contours can be placed relative to the baseline."""
    x0, y0, x1, y1 = L.drawing_rect(index)
    i = L.CROP_INSET
    px0, py0 = int((x0 + i) * SCALE), int((y0 + i) * SCALE)
    px1, py1 = int((x1 - i) * SCALE), int((y1 - i) * SCALE)
    crop = sheet[py0:py1, px0:px1].astype(np.float32)
    paper = np.percentile(crop, 90)
    # Printed guides are light grey; only clearly dark pixels count as ink
    mask = (crop < paper * 0.58).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    for lbl in range(1, count):
        if stats[lbl, cv2.CC_STAT_AREA] < MIN_BLOB_PX:
            mask[labels == lbl] = 0
    # Soften the jagged pixel edge before tracing
    mask = cv2.GaussianBlur(mask, (0, 0), 1.6)
    mask = (mask > 127).astype(np.uint8) * 255
    return mask, (px0, py0)


# ------------------------------------------------------------------ outlines

def signed_area(pts):
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def trace(mask, origin, baseline_px):
    """Trace ink into font-unit contours (y up, baseline at 0), with outer
    contours clockwise and holes counter-clockwise as TrueType expects."""
    units_per_px = CAP_UNITS / ((L.BASELINE_OFFSET - L.CAP_OFFSET) * SCALE)
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    result = []
    for c, h in zip(contours, hierarchy[0] if hierarchy is not None else []):
        if cv2.contourArea(c) < MIN_BLOB_PX / 2:
            continue
        approx = cv2.approxPolyDP(c, 1.0, True).reshape(-1, 2).astype(np.float64)
        if len(approx) < 3:
            continue
        pts = np.empty_like(approx)
        pts[:, 0] = (approx[:, 0] + origin[0]) * units_per_px
        pts[:, 1] = (baseline_px - (approx[:, 1] + origin[1])) * units_per_px
        is_hole = h[3] >= 0
        clockwise = signed_area(pts) < 0
        if clockwise == is_hole:
            pts = pts[::-1]
        result.append(pts)
    return result


def draw_glyph(contours):
    """Contours become smooth quadratic outlines: every traced vertex is an
    off-curve point, with implied on-curve points at the edge midpoints."""
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


def empty_glyph():
    return TTGlyphPen(None).glyph()


def notdef_glyph():
    pen = TTGlyphPen(None)
    for box in ((50, 0, 450, CAP_UNITS), (100, 50, 400, CAP_UNITS - 50)):
        x0, y0, x1, y1 = box
        corners = [(x0, y0), (x0, y1), (x1, y1), (x1, y0)]
        if box[0] == 100:
            corners.reverse()
        pen.moveTo(corners[0])
        for p in corners[1:]:
            pen.lineTo(p)
        pen.closePath()
    return pen.glyph()


# ------------------------------------------------------------------ font

def glyph_name(ch):
    return f"uni{ord(ch):04X}"


def build(image_path, out_path, family, debug_dir=None):
    image = cv2.imread(str(image_path))
    if image is None:
        raise SystemExit(f"Could not read image: {image_path}")
    sheet = straighten(image)
    if debug_dir:
        Path(debug_dir).mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(Path(debug_dir) / "straightened.png"), sheet)

    glyphs = {".notdef": notdef_glyph(), "space": empty_glyph()}
    advances = {".notdef": 500, "space": SPACE_WIDTH}
    cmap = {32: "space", 0xA0: "space"}
    found, skipped = [], []

    for index, ch in enumerate(L.CHARACTERS):
        mask, origin = ink_mask(sheet, index)
        if cv2.countNonZero(mask) < MIN_INK_PX:
            skipped.append(ch)
            continue
        if debug_dir:
            cv2.imwrite(str(Path(debug_dir) / f"cell_{index:02d}.png"), 255 - mask)
        contours = trace(mask, origin, L.baseline_y(index) * SCALE)
        if not contours:
            skipped.append(ch)
            continue
        name = glyph_name(ch)
        glyphs[name], advances[name] = draw_glyph(contours)
        cmap[ord(ch)] = name
        found.append(ch)

    for ch in found:
        if ch.isupper() and ord(ch.lower()) not in cmap:
            cmap[ord(ch.lower())] = glyph_name(ch)

    if not found:
        raise SystemExit("No glyphs found on the sheet - is the ink dark enough?")

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
    fb.setupOS2(
        sTypoAscender=ymax, sTypoDescender=ymin, sTypoLineGap=0,
        usWinAscent=ymax, usWinDescent=-ymin, sCapHeight=CAP_UNITS, sxHeight=CAP_UNITS // 2,
    )
    fb.setupPost()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fb.save(str(out_path))

    print(f"Wrote {out_path.resolve()}")
    print(f"  {len(found)} glyphs: {''.join(found)}")
    if skipped:
        print(f"  empty boxes skipped: {''.join(skipped)}")
    return out_path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", help="photo or scan of the filled-in glyph sheet")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--family", default="AUPedean")
    ap.add_argument("--debug", help="folder to write the straightened sheet and per-glyph masks")
    args = ap.parse_args()
    build(args.image, args.out, args.family, args.debug)


if __name__ == "__main__":
    main()
