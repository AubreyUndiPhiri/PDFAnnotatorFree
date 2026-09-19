"""Helper functions that operate directly on a fitz.Page to create/edit annotations."""
import fitz


def render_matrix(zoom: float) -> fitz.Matrix:
    """Matrix to pass to Page.get_pixmap(). PyMuPDF already bakes the page's
    own /Rotate value into get_pixmap()'s output automatically, so this must
    NOT also include page.rotation_matrix or rotation would be applied twice."""
    return fitz.Matrix(zoom, zoom)


def coord_matrix(page: fitz.Page, zoom: float) -> fitz.Matrix:
    """Full effective PDF-point -> pixel matrix (rotation + zoom), matching
    what get_pixmap() actually produces. Use this (and its inverse) for any
    conversion between pixel coordinates and PDF point coordinates."""
    return page.rotation_matrix * fitz.Matrix(zoom, zoom)


def pixel_to_pdf(page: fitz.Page, zoom: float, x: float, y: float) -> fitz.Point:
    mat = ~coord_matrix(page, zoom)
    return fitz.Point(x, y) * mat


def color_to_rgb(qcolor) -> tuple:
    return (qcolor.redF(), qcolor.greenF(), qcolor.blueF())


def quads_for_selection(page: fitz.Page, p1: fitz.Point, p2: fitz.Point) -> list:
    """Approximate a text selection: return one rect per text line that the
    drag rectangle overlaps, spanning the full width of the words touched on
    that line. Falls back to the raw drag rectangle on image-only pages."""
    rect = fitz.Rect(p1, p2)
    rect.normalize()
    words = page.get_text("words")
    if not words:
        return [rect]
    lines = {}
    for w in words:
        x0, y0, x1, y1 = w[0], w[1], w[2], w[3]
        wbbox = fitz.Rect(x0, y0, x1, y1)
        if wbbox.intersects(rect):
            key = (w[5], w[6])  # (block_no, line_no)
            lines.setdefault(key, []).append(wbbox)
    if not lines:
        return [rect]
    result = []
    for boxes in lines.values():
        x0 = min(b.x0 for b in boxes)
        x1 = max(b.x1 for b in boxes)
        y0 = min(b.y0 for b in boxes)
        y1 = max(b.y1 for b in boxes)
        result.append(fitz.Rect(x0, y0, x1, y1))
    return result


def add_highlight(page: fitz.Page, p1, p2, color) -> fitz.Annot:
    quads = quads_for_selection(page, p1, p2)
    annot = page.add_highlight_annot(quads=quads)
    annot.set_colors(stroke=color_to_rgb(color))
    annot.update()
    return annot


def add_underline(page: fitz.Page, p1, p2, color) -> fitz.Annot:
    quads = quads_for_selection(page, p1, p2)
    annot = page.add_underline_annot(quads=quads)
    annot.set_colors(stroke=color_to_rgb(color))
    annot.update()
    return annot


def add_strikeout(page: fitz.Page, p1, p2, color) -> fitz.Annot:
    quads = quads_for_selection(page, p1, p2)
    annot = page.add_strikeout_annot(quads=quads)
    annot.set_colors(stroke=color_to_rgb(color))
    annot.update()
    return annot


def add_note(page: fitz.Page, point, text, color) -> fitz.Annot:
    annot = page.add_text_annot(point, text)
    annot.set_colors(stroke=color_to_rgb(color))
    annot.update()
    return annot


def add_ink(page: fitz.Page, points, color, width) -> fitz.Annot:
    coords = [(p.x, p.y) for p in points]
    annot = page.add_ink_annot([coords])
    annot.set_colors(stroke=color_to_rgb(color))
    annot.set_border(width=width)
    annot.update()
    return annot


def add_rect(page: fitz.Page, rect, color, width) -> fitz.Annot:
    annot = page.add_rect_annot(rect)
    annot.set_colors(stroke=color_to_rgb(color))
    annot.set_border(width=width)
    annot.update()
    return annot


def add_ellipse(page: fitz.Page, rect, color, width) -> fitz.Annot:
    annot = page.add_circle_annot(rect)
    annot.set_colors(stroke=color_to_rgb(color))
    annot.set_border(width=width)
    annot.update()
    return annot


def add_line(page: fitz.Page, p1, p2, color, width, arrow=False) -> fitz.Annot:
    annot = page.add_line_annot(p1, p2)
    annot.set_colors(stroke=color_to_rgb(color))
    annot.set_border(width=width)
    if arrow:
        annot.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_OPEN_ARROW)
    annot.update()
    return annot


def add_freetext(page: fitz.Page, rect, text, color, fontsize=12) -> fitz.Annot:
    annot = page.add_freetext_annot(
        rect, text, fontsize=fontsize, text_color=color_to_rgb(color)
    )
    annot.update()
    return annot


def add_stamp(page: fitz.Page, rect, stamp_name: str) -> fitz.Annot:
    stamp_id = getattr(fitz, f"STAMP_{stamp_name}")
    annot = page.add_stamp_annot(rect, stamp=stamp_id)
    annot.update()
    return annot


def insert_image(page: fitz.Page, rect, image_path: str = None, image_bytes: bytes = None):
    """Bakes an image (signature / logo stamp) directly into page content."""
    if image_bytes is not None:
        page.insert_image(rect, stream=image_bytes)
    else:
        page.insert_image(rect, filename=image_path)


def find_annot_at(page: fitz.Page, point: fitz.Point):
    for annot in page.annots():
        if annot.rect.contains(point):
            return annot
    return None


def move_annot(annot: fitz.Annot, dx: float, dy: float):
    r = annot.rect
    new_rect = fitz.Rect(r.x0 + dx, r.y0 + dy, r.x1 + dx, r.y1 + dy)
    annot.set_rect(new_rect)
    annot.update()


# ---------------------------------------------------------------------------
# Extended tools
# ---------------------------------------------------------------------------

def add_marker(page: fitz.Page, points, color, width, opacity=0.35) -> fitz.Annot:
    """A translucent freehand highlighter stroke, distinct from the
    text-selection-aware Highlight tool."""
    coords = [(p.x, p.y) for p in points]
    annot = page.add_ink_annot([coords])
    annot.set_colors(stroke=color_to_rgb(color))
    annot.set_border(width=width)
    annot.set_opacity(opacity)
    annot.update()
    return annot


def add_polygon(page: fitz.Page, points, color, width) -> fitz.Annot:
    coords = [(p.x, p.y) for p in points]
    annot = page.add_polygon_annot(coords)
    annot.set_colors(stroke=color_to_rgb(color))
    annot.set_border(width=width)
    annot.update()
    return annot


def distance_in_units(p1: fitz.Point, p2: fitz.Point, points_per_unit: float) -> float:
    dx, dy = p2.x - p1.x, p2.y - p1.y
    return ((dx * dx + dy * dy) ** 0.5) / points_per_unit


def add_dimension(page: fitz.Page, p1, p2, color, width, unit_name, points_per_unit):
    """A measurement: a Line annotation plus a small FreeText label near its
    midpoint showing the real-world distance. Returns (line_annot, label_annot)."""
    line = add_line(page, p1, p2, color, width, arrow=False)
    dist = distance_in_units(p1, p2, points_per_unit)
    mid = fitz.Point((p1.x + p2.x) / 2, (p1.y + p2.y) / 2)
    label_rect = fitz.Rect(mid.x - 30, mid.y - 10, mid.x + 30, mid.y + 10)
    label = add_freetext(page, label_rect, f"{dist:.2f} {unit_name}", color, fontsize=9)
    return line, label


def extract_text(page: fitz.Page, p1: fitz.Point, p2: fitz.Point) -> str:
    rect = fitz.Rect(p1, p2)
    rect.normalize()
    return page.get_textbox(rect)


def snapshot_pixmap(page: fitz.Page, p1: fitz.Point, p2: fitz.Point, zoom: float) -> fitz.Pixmap:
    rect = fitz.Rect(p1, p2)
    rect.normalize()
    return page.get_pixmap(matrix=render_matrix(zoom), clip=rect, alpha=False)


def crop_page(page: fitz.Page, p1: fitz.Point, p2: fitz.Point):
    rect = fitz.Rect(p1, p2)
    rect.normalize()
    page.set_cropbox(rect)


def erase_along_path(page: fitz.Page, points) -> int:
    """Delete every annotation whose rect contains any point along the given
    path. Returns the number of annotations removed."""
    to_delete = {}
    for annot in page.annots():
        for p in points:
            if annot.rect.contains(p):
                to_delete[annot.xref] = annot
                break
    for annot in to_delete.values():
        page.delete_annot(annot)
    return len(to_delete)


def point_in_polygon(point: fitz.Point, polygon) -> bool:
    """Ray-casting point-in-polygon test. `polygon` is a list of fitz.Point."""
    x, y = point.x, point.y
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i].x, polygon[i].y
        xj, yj = polygon[j].x, polygon[j].y
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def annots_in_lasso(page: fitz.Page, polygon) -> list:
    """Every annotation on the page whose rect center falls inside the lasso polygon."""
    matches = []
    for annot in page.annots():
        r = annot.rect
        center = fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
        if point_in_polygon(center, polygon):
            matches.append(annot)
    return matches


def remove_all_annotations(doc: fitz.Document):
    for page in doc:
        for annot in list(page.annots()):
            page.delete_annot(annot)


def melt_all_annotations(doc: fitz.Document):
    """Permanently flatten every annotation into page content. Uses PyMuPDF's
    native bake() when available (keeps everything vector/text-selectable);
    falls back to a lossy full-page rasterization otherwise."""
    if hasattr(doc, "bake"):
        doc.bake(annots=True, widgets=True)
        return
    for page in doc:  # pragma: no cover - fallback for very old PyMuPDF only
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        for annot in list(page.annots()):
            page.delete_annot(annot)
        page.clean_contents()
        page.insert_image(page.rect, pixmap=pix)


_COPYABLE_QUAD_TYPES = {"Highlight": "add_highlight_annot", "Underline": "add_underline_annot",
                         "StrikeOut": "add_strikeout_annot"}


def serialize_annot(annot: fitz.Annot) -> dict:
    """Capture enough of an annotation to reconstruct a copy of it elsewhere."""
    type_name = annot.type[1]
    colors = annot.colors or {}
    data = {
        "type": type_name,
        "stroke": tuple(colors.get("stroke") or (0, 0, 0)),
        "width": (annot.border or {}).get("width", 1.0) or 1.0,
        "rect": tuple(annot.rect),
        "content": annot.info.get("content", ""),
    }
    if type_name in _COPYABLE_QUAD_TYPES:
        data["vertices"] = list(annot.vertices)
    elif type_name == "Ink":
        data["vertices"] = list(annot.vertices)
    elif type_name in ("Polygon", "Line"):
        data["vertices"] = list(annot.vertices)
        if type_name == "Line":
            ends = getattr(annot, "line_ends", (0, 0))
            data["arrow"] = bool(ends and ends[1] not in (0, None))
    elif type_name == "FreeText":
        data["fontsize"] = 12
    return data


def deserialize_and_add(page: fitz.Page, data: dict, offset=(0.0, 0.0), override_color=None, override_width=None):
    """Reconstruct an annotation from serialize_annot() output, shifted by offset."""
    dx, dy = offset
    stroke = override_color if override_color is not None else data["stroke"]
    width = override_width if override_width is not None else data["width"]
    type_name = data["type"]

    def shift(pt):
        return fitz.Point(pt[0] + dx, pt[1] + dy)

    if type_name in _COPYABLE_QUAD_TYPES:
        verts = [shift(v) for v in data["vertices"]]
        quads = [fitz.Quad(verts[i:i + 4]) for i in range(0, len(verts), 4)]
        annot = getattr(page, _COPYABLE_QUAD_TYPES[type_name])(quads=quads)
        annot.set_colors(stroke=stroke)
        annot.update()
        return annot
    if type_name == "Ink":
        strokes = [[tuple(shift(p)) for p in stroke_pts] for stroke_pts in data["vertices"]]
        annot = page.add_ink_annot(strokes)
        annot.set_colors(stroke=stroke)
        annot.set_border(width=width)
        annot.update()
        return annot
    if type_name == "Polygon":
        pts = [tuple(shift(p)) for p in data["vertices"]]
        annot = page.add_polygon_annot(pts)
        annot.set_colors(stroke=stroke)
        annot.set_border(width=width)
        annot.update()
        return annot
    if type_name == "Line":
        p1, p2 = data["vertices"]
        annot = page.add_line_annot(shift(p1), shift(p2))
        annot.set_colors(stroke=stroke)
        annot.set_border(width=width)
        if data.get("arrow"):
            annot.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_OPEN_ARROW)
        annot.update()
        return annot
    if type_name == "FreeText":
        r = data["rect"]
        rect = fitz.Rect(r[0] + dx, r[1] + dy, r[2] + dx, r[3] + dy)
        annot = page.add_freetext_annot(rect, data["content"], fontsize=data.get("fontsize", 12), text_color=stroke)
        annot.update()
        return annot
    if type_name == "Text":
        r = data["rect"]
        annot = page.add_text_annot(fitz.Point(r[0] + dx, r[1] + dy), data["content"])
        annot.set_colors(stroke=stroke)
        annot.update()
        return annot
    if type_name in ("Square", "Circle"):
        r = data["rect"]
        rect = fitz.Rect(r[0] + dx, r[1] + dy, r[2] + dx, r[3] + dy)
        annot = page.add_rect_annot(rect) if type_name == "Square" else page.add_circle_annot(rect)
        annot.set_colors(stroke=stroke)
        annot.set_border(width=width)
        annot.update()
        return annot
    return None
