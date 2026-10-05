"""What AUPedea does to the paper itself: read a page (its words, where they
are, and a picture of it), and add annotations: pen strokes, shapes, text
marked up or circled, and text written on the page. Each call is one undo
step, made with the same functions the app's own tools use.

Coordinates are PDF points from the page's top-left corner (x right, y down)."""
import math
import re

import pymupdf as fitz
from PySide6.QtGui import QColor

from .. import pdf_ops

HANDWRITING_FONT = "Caveat"
PRINT_FONT = "Helvetica"
MAX_PAGE_TEXT = 9000          # characters of page text handed to the AI


class PageError(Exception):
    """A request AUPedea can't do on this page (said back to the AI or the person)."""


def color(value, default="#2563eb"):
    c = QColor(str(value or default).strip())
    return c if c.isValid() else QColor(default)


def pdf_tab(window):
    """The current PDF tab (not a Word or LaTeX one), or None."""
    tab = window.current_tab() if hasattr(window, "current_tab") else None
    if tab is None or getattr(tab, "document", None) is None or not tab.document.is_open:
        return None
    if hasattr(window, "current_editor") and window.current_editor() is not None:
        return None
    return tab


def page_index(tab, page_number):
    """1-based page number (or None for the current page) -> 0-based index."""
    n = tab.document.page_count
    if page_number in (None, "", 0):
        return tab.current_page_index()
    try:
        index = int(page_number) - 1
    except (TypeError, ValueError):
        raise PageError(f"{page_number!r} isn't a page number.") from None
    if not 0 <= index < n:
        raise PageError(f"There's no page {page_number}: the document has {n} page{'s' if n != 1 else ''}.")
    return index


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

def read_page(tab, index, limit=None):
    """The page's text, line by line, each with its box [x0, y0, x1, y1] in points (up to `limit` characters)."""
    limit = limit or MAX_PAGE_TEXT
    page = tab.document.page(index)
    r = page.rect
    lines = {}
    for x0, y0, x1, y1, word, block, line, _n in page.get_text("words"):
        box = lines.setdefault((block, line), [x0, y0, x1, y1, []])
        box[0], box[1] = min(box[0], x0), min(box[1], y0)
        box[2], box[3] = max(box[2], x1), max(box[3], y1)
        box[4].append(word)
    out = [f"Page {index + 1} of {tab.document.page_count}: {r.width:.0f} x {r.height:.0f} points "
           "(x from the left, y from the top)."]
    rows = sorted(lines.values(), key=lambda b: (round(b[1] / 3), b[0]))
    if not rows:
        out.append("There's no text on this page (it may be a scan or a picture).")
    used = len(out[0])
    for x0, y0, x1, y1, words in rows:
        line = f"[{x0:.0f},{y0:.0f},{x1:.0f},{y1:.0f}] {' '.join(words)}"
        if used + len(line) > limit:
            out.append(f"... ({len(rows)} lines in all; the rest is cut off)")
            break
        out.append(line)
        used += len(line) + 1
    annots = list(page.annots() or [])
    if annots:
        kinds = {}
        for a in annots:
            kinds[a.type[1]] = kinds.get(a.type[1], 0) + 1
        out.append("Annotations already on it: " + ", ".join(f"{n} {k}" for k, n in kinds.items()))
    return "\n".join(out)


def page_png(tab, index, longest=1100):
    """A picture of the page (annotations included), at most `longest` pixels on its long side."""
    page = tab.document.page(index)
    zoom = longest / max(page.rect.width, page.rect.height)
    return page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False).tobytes("png")


def find_text(tab, index, text):
    """Each place `text` appears on the page, as a list of quads (one per line it spans)."""
    page = tab.document.page(index)
    text = " ".join(str(text).split())
    if not text:
        raise PageError("No text was given to find.")
    hits = page.search_for(text, quads=True)
    if not hits and len(text) > 60:                  # long quotes: match the start, it's usually enough
        hits = page.search_for(text[:60], quads=True)
    if not hits:
        raise PageError(f'"{text[:80]}" isn\'t on page {index + 1}. Read the page and use its exact words.')
    return hits


def group_lines(quads):
    """Quads (from search_for) as rectangles, one per line of text."""
    return [fitz.Rect(q.rect) for q in quads]


# ---------------------------------------------------------------------------
# shapes as pen strokes (what the scribble's nib follows)
# ---------------------------------------------------------------------------

def _clamp_points(page, pts):
    r = page.rect
    return [(min(max(float(x), 0.0), r.width), min(max(float(y), 0.0), r.height)) for x, y in pts]


def clean_strokes(page, strokes, max_strokes=60, max_points=400):
    """Validate strokes from the AI: lists of [x, y] points, kept on the page."""
    out = []
    for stroke in list(strokes or [])[:max_strokes]:
        pts = []
        for p in list(stroke or [])[:max_points]:
            try:
                pts.append((float(p[0]), float(p[1])))
            except (TypeError, ValueError, IndexError):
                raise PageError("Each stroke must be a list of [x, y] points.") from None
        if len(pts) == 1:
            pts.append((pts[0][0] + 0.5, pts[0][1] + 0.5))     # a dot
        if pts:
            out.append(_clamp_points(page, pts))
    if not out:
        raise PageError("There were no points to draw.")
    return out


def ellipse_points(rect, steps=48, turns=1.0, start=-math.pi / 2):
    cx, cy, rx, ry = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2, rect.width / 2, rect.height / 2
    return [(cx + math.cos(start + turns * math.tau * i / steps) * rx,
             cy + math.sin(start + turns * math.tau * i / steps) * ry) for i in range(steps + 1)]


def hand_loop(rect, pad=5.0, seed=0):
    """A teacher's circle round some text: a bit more than once round, a little wobbly."""
    import random

    rnd = random.Random(seed)
    big = fitz.Rect(rect.x0 - pad * 1.6, rect.y0 - pad, rect.x1 + pad * 1.6, rect.y1 + pad)
    pts = []
    steps, turns, start = 56, 1.12, math.radians(rnd.uniform(200, 230))
    cx, cy, rx, ry = (big.x0 + big.x1) / 2, (big.y0 + big.y1) / 2, big.width / 2, big.height / 2
    for i in range(steps + 1):
        u = i / steps
        a = start + u * turns * math.tau
        grow = 1 + 0.06 * u + 0.02 * math.sin(u * 13 + seed)
        pts.append((cx + math.cos(a) * rx * grow, cy + math.sin(a) * ry * grow))
    return pts


def shape_strokes(kind, x0, y0, x1, y1):
    """The outline the nib follows for a shape (arrows get their head as a second stroke)."""
    if kind == "rect":
        r = fitz.Rect(x0, y0, x1, y1).normalize()
        return [[(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1), (r.x0, r.y0)]]
    if kind == "ellipse":
        return [ellipse_points(fitz.Rect(x0, y0, x1, y1).normalize())]
    line = [(x0, y0), (x1, y1)]
    if kind == "line":
        return [line]
    if kind == "arrow":
        a = math.atan2(y1 - y0, x1 - x0)
        size = min(14.0, max(6.0, math.hypot(x1 - x0, y1 - y0) / 4))
        head = [(x1 - size * math.cos(a - 0.45), y1 - size * math.sin(a - 0.45)), (x1, y1),
                (x1 - size * math.cos(a + 0.45), y1 - size * math.sin(a + 0.45))]
        return [line, head]
    raise PageError(f"Unknown shape {kind!r}: use rect, ellipse, line or arrow.")


# ---------------------------------------------------------------------------
# writing annotations (each call: one undo step)
# ---------------------------------------------------------------------------

def _committed(tab, index):
    tab.document.snapshot()
    pw = tab.get_page_widget(index)
    pw.render()
    tab.refresh_thumbnail(index)


def add_strokes(tab, index, strokes, ink, width):
    page = tab.document.page(index)
    for stroke in strokes:
        pdf_ops.add_ink(page, stroke, ink, width)
    _committed(tab, index)
    return len(strokes)


def add_shape(tab, index, kind, x0, y0, x1, y1, ink, width):
    page = tab.document.page(index)
    r = fitz.Rect(x0, y0, x1, y1).normalize()
    if kind == "rect":
        pdf_ops.add_rect(page, r, ink, width)
    elif kind == "ellipse":
        pdf_ops.add_ellipse(page, r, ink, width)
    elif kind in ("line", "arrow"):
        pdf_ops.add_line(page, fitz.Point(x0, y0), fitz.Point(x1, y1), ink, width, arrow=kind == "arrow")
    else:
        raise PageError(f"Unknown shape {kind!r}.")
    _committed(tab, index)


def add_markup(tab, index, style, quads, ink, width=2.0, seed=0):
    """highlight / underline / strikeout as the PDF's own text markup; circle as a hand-drawn loop."""
    page = tab.document.page(index)
    if style == "circle":
        for i, r in enumerate(group_lines(quads)):
            pdf_ops.add_ink(page, hand_loop(r, seed=seed + i), ink, width)
    else:
        make = {"highlight": page.add_highlight_annot, "underline": page.add_underline_annot,
                "strikeout": page.add_strikeout_annot}.get(style)
        if make is None:
            raise PageError(f"Unknown style {style!r}: use highlight, underline, strikeout or circle.")
        annot = make(quads=quads)
        annot.set_colors(stroke=pdf_ops.color_to_rgb(ink))
        annot.update()
    _committed(tab, index)


def text_layout(page, x, y, text, size=14, handwriting=True, width=None):
    """Where text written at (x, y) goes and how big it is: {"x", "y", "size", "font", "width", "rect"}."""
    text = str(text).strip()
    if not text:
        raise PageError("There was no text to write.")
    size = min(max(float(size or 14), 6.0), 72.0)
    font = HANDWRITING_FONT if handwriting else PRINT_FONT
    x = min(max(float(x), 0.0), page.rect.width - 20)
    y = min(max(float(y), 0.0), page.rect.height - size)
    if width:
        width = min(max(float(width), 30.0), page.rect.width - x)
    elif len(text) >= 60:                            # a long note wraps at the margin
        width = max(120.0, page.rect.width - x - 36)
    else:
        width = None
    w, h = pdf_ops.text_box_size(text, size, font, width)
    return {"x": x, "y": y, "size": size, "font": font, "width": width, "rect": fitz.Rect(x, y, x + w, y + h)}


def add_text(tab, index, layout, text, ink):
    page = tab.document.page(index)
    annot = pdf_ops.add_text_box(page, fitz.Point(layout["x"], layout["y"]), str(text).strip(), ink, layout["size"],
                                 layout["font"], width=layout["width"])
    rect = fitz.Rect(annot.rect)
    _committed(tab, index)
    return rect


def clean_words(text):
    return re.sub(r"\s+", " ", str(text)).strip()
