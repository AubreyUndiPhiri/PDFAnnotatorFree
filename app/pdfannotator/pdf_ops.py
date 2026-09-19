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
