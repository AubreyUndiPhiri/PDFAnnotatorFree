"""Helper functions that operate directly on a fitz.Page to create/edit annotations."""
import io
import base64
import json
import zlib
from functools import lru_cache

import pymupdf as fitz
from PySide6.QtGui import QColor

from . import fonts


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


PRESSURE_KEY = "AupPressure"   # per-point stroke widths of a pressure-sensitive pen stroke


def _xy(p):
    return (p.x, p.y) if hasattr(p, "x") else (float(p[0]), float(p[1]))


def add_ink(page: fitz.Page, points, color, width, widths=None) -> fitz.Annot:
    """A pen stroke. With `widths` (one per point, from pen pressure) the
    stroke is drawn thicker and thinner along its length; the PDF still holds
    a normal ink annotation, so other viewers show at least a plain line."""
    coords = [_xy(p) for p in points]
    annot = page.add_ink_annot([coords])
    annot.set_colors(stroke=color_to_rgb(color))
    annot.set_border(width=max(widths) if widths else width)
    annot.update()
    if widths and len(widths) == len(coords):
        page.parent.xref_set_key(annot.xref, PRESSURE_KEY,
                                 "[" + " ".join(f"{w:.3f}" for w in widths) + "]")
        _apply_pressure_appearance(annot)
    return annot


def pressure_widths(annot):
    """The per-point widths of a pressure stroke, or None."""
    if annot is None or annot.type[0] != fitz.PDF_ANNOT_INK:
        return None
    kind, value = annot.parent.parent.xref_get_key(annot.xref, PRESSURE_KEY)
    if kind != "array":
        return None
    try:
        return [float(v) for v in value.strip("[]").split()]
    except ValueError:
        return None


def _apply_pressure_appearance(annot):
    """Draw a pressure stroke segment by segment, each with its own width
    (round caps and joins, so the segments flow into one tapered line)."""
    widths = pressure_widths(annot)
    if not widths:
        return False
    strokes = annot.vertices or []
    points = [tuple(pt) for stroke in strokes for pt in stroke]
    if len(points) != len(widths) or len(points) < 2:
        return False
    doc = annot.parent.parent
    rect = annot.rect
    w, h = max(rect.width, 1), max(rect.height, 1)
    color = (annot.colors or {}).get("stroke") or (0, 0, 0)
    scratch = fitz.open()
    sp = scratch.new_page(width=w, height=h)
    shape = sp.new_shape()
    for (a, b), (wa, wb) in zip(zip(points, points[1:]), zip(widths, widths[1:])):
        shape.draw_line((a[0] - rect.x0, a[1] - rect.y0), (b[0] - rect.x0, b[1] - rect.y0))
        shape.finish(color=color, width=(wa + wb) / 2, lineCap=1, lineJoin=1, closePath=False)
    shape.commit()
    content = sp.read_contents()
    kind, ap = doc.xref_get_key(annot.xref, "AP/N")
    xobj = int(ap.split()[0]) if kind == "xref" else doc.get_new_xref()
    doc.update_object(xobj, f"<</Type/XObject/Subtype/Form/BBox[0 0 {w:g} {h:g}]/Resources<<>>>>")
    doc.update_stream(xobj, content)
    doc.xref_set_key(annot.xref, "AP", f"<</N {xobj} 0 R>>")
    return True


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


# ---------------------------------------------------------------------------
# Text boxes (FreeText annotations)
#
# Every text box is a FreeText annotation, so it can be selected, moved,
# resized and edited later. Base-14 fonts use the viewer-generated
# appearance. Custom fonts (e.g. AUPedean) get an appearance stream drawn
# with the embedded font; the font name is kept in the /AupFont key so the
# appearance can be rebuilt after every move, resize or edit (PyMuPDF
# regenerates a Helvetica appearance whenever the rect or style changes).
# ---------------------------------------------------------------------------

LINE_HEIGHT = 1.2      # baseline-to-baseline distance, in font sizes (matches MuPDF)
FIRST_BASELINE = 0.8   # first baseline below the box top, in font sizes (matches MuPDF)
TEXT_SLACK = 3.0       # extra width (pt) so the viewer never wraps an auto-sized line
CUSTOM_FONT_KEY = "AupFont"
FIXED_WIDTH_KEY = "AupFixedWidth"
_DA_FONTS = {"helv": "Helvetica", "tiro": "Times", "cour": "Courier"}


@lru_cache(maxsize=None)
def _font_file(path):
    return fitz.Font(fontfile=path)


def forget_font_file(path):
    """Drop cached metrics after a font file was rebuilt on disk."""
    _font_file.cache_clear()
    _subset_font.cache_clear()


def _measurer(fontname):
    path = fonts.custom_font_path(fontname)
    if path:
        font = _font_file(path)
        return lambda s, size: font.text_length(s, fontsize=size)
    code = fonts.BASE14_FONTS.get(fontname, "helv")
    return lambda s, size: fitz.get_text_length(s, fontname=code, fontsize=size)


def wrap_text(text, fontsize, fontname, width=None):
    """Lines as the viewer lays them out: explicit newlines, plus greedy
    word wrap when a box width is given."""
    measure = _measurer(fontname)
    lines = []
    for para in text.split("\n"):
        if width is None:
            lines.append(para)
            continue
        current = ""
        for word in para.split(" "):
            candidate = word if not current else f"{current} {word}"
            if current and measure(candidate, fontsize) > width:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    return lines or [""]


# ---------------------------------------------------------------------------
# Rich text in text boxes: bold, italic, underline, strikethrough,
# superscript and subscript on any part of the text. The box keeps its
# plain text in /Contents (what other apps edit) and the formatting as
# runs in RICH_KEY; its appearance is drawn here run by run (real bold /
# italic fonts where they exist, synthesised otherwise).
# ---------------------------------------------------------------------------

RICH_KEY = "AupRich"
RUN_FLAGS = ("b", "i", "u", "s", "v")      # bold, italic, underline, strike, v: 1 super / -1 sub
SCRIPT_SCALE, SUPER_RISE, SUB_DROP = 0.62, 0.36, 0.14   # sizes and shifts, in font sizes
_BASE14_NAMES = {"helv": "Helvetica", "hebo": "Helvetica-Bold", "heit": "Helvetica-Oblique",
                 "hebi": "Helvetica-BoldOblique", "tiro": "Times-Roman", "tibo": "Times-Bold",
                 "tiit": "Times-Italic", "tibi": "Times-BoldItalic", "cour": "Courier", "cobo": "Courier-Bold",
                 "coit": "Courier-Oblique", "cobi": "Courier-BoldOblique"}


def runs_text(runs):
    return "".join(r["t"] for r in runs)


def runs_formatted(runs) -> bool:
    return bool(runs) and any(r.get(k) for r in runs for k in RUN_FLAGS)


def normalize_runs(runs):
    """Drop empty runs, merge neighbours with the same style, keep only set flags."""
    out = []
    for r in runs or []:
        if not r.get("t"):
            continue
        style = {k: r[k] for k in RUN_FLAGS if r.get(k)}
        if out and {k: out[-1][k] for k in RUN_FLAGS if out[-1].get(k)} == style:
            out[-1]["t"] += r["t"]
        else:
            out.append({"t": r["t"], **style})
    return out


def rstrip_runs(runs):
    runs = [dict(r) for r in normalize_runs(runs)]
    while runs:
        runs[-1]["t"] = runs[-1]["t"].rstrip()
        if runs[-1]["t"]:
            break
        runs.pop()
    return runs


def rich_runs(annot):
    """The formatted runs of a text box, or None for plain text."""
    kind, value = annot.parent.parent.xref_get_key(annot.xref, RICH_KEY)
    if kind != "string" or not value:
        return None
    try:
        runs = json.loads(value)
    except ValueError:
        return None
    return runs if runs_formatted(runs) else None


def _store_layout(doc, xref, text, paras, spacing, align):
    paras = _clean_paras(paras, text)
    doc.xref_set_key(xref, PARAS_KEY, fitz.get_pdf_str(json.dumps(paras)) if any(paras) else "null")
    spacing = float(spacing or 1.0)
    doc.xref_set_key(xref, SPACING_KEY, f"{spacing:g}" if abs(spacing - 1.0) > 1e-3 else "null")
    doc.xref_set_key(xref, JUSTIFY_KEY, "true" if align == JUSTIFY else "null")


def _store_runs(doc, xref, runs):
    runs = normalize_runs(runs)
    doc.xref_set_key(xref, RICH_KEY, fitz.get_pdf_str(json.dumps(runs)) if runs_formatted(runs) else "null")


def _run_size(size, run):
    return size * SCRIPT_SCALE if run.get("v") else size


def _run_font(fontname, bold, italic):
    """How to draw a run: a base-14 font (real bold / italic variants), or a
    font file (its bold / italic sibling if installed, else drawn bolder /
    slanted)."""
    if not fonts.is_custom_font(fontname):
        family = fonts.BASE14_FONTS.get(fontname, "helv")
        return {"code": fonts.BASE14_VARIANTS[family][(bool(bold), bool(italic))], "fake_bold": False,
                "fake_italic": False}
    for b, i in ((bold, italic), (bold, False), (False, italic), (False, False)):
        variant = fonts.style_variant(fontname, b, i)
        if variant and fonts.custom_font_path(variant):
            return {"name": variant, "path": fonts.custom_font_path(variant),
                    "fake_bold": bool(bold) and not b, "fake_italic": bool(italic) and not i}
    return {"name": fontname, "path": fonts.custom_font_path(fontname), "fake_bold": bool(bold),
            "fake_italic": bool(italic)}


@lru_cache(maxsize=None)
def _base14_file(code):
    """The font file behind a base-14 font, for characters its encoding lacks."""
    return fitz.Font(code)


def _run_width(text, size, fontname, run):
    f = _run_font(fontname, run.get("b"), run.get("i"))
    s = _run_size(size, run)
    if "code" in f:
        if any(ord(ch) > 255 for ch in text):   # drawn with the embedded font file: measure that
            return _base14_file(f["code"]).text_length(text, fontsize=s)
        return fitz.get_text_length(text, fontname=f["code"], fontsize=s)
    return _font_file(f["path"]).text_length(text, fontsize=s)


PARAS_KEY = "AupParas"        # per paragraph: "" | "bullet" | "number"
SPACING_KEY = "AupSpacing"    # line spacing, a multiple of the normal line height
JUSTIFY_KEY = "AupJustify"    # "true": justified (PDF's /Q only knows left / centre / right)
JUSTIFY = 3                   # the align value for justified text
LIST_INDENT = 1.25            # how far list text sits from the box edge, in font sizes


def _clean_paras(paras, text):
    """One entry per paragraph of `text` ("" when it isn't in a list)."""
    count = text.count("\n") + 1
    paras = [p if p in ("bullet", "number") else "" for p in (paras or [])][:count]
    return paras + [""] * (count - len(paras))


def needs_own_drawing(runs=None, paras=None, spacing=1.0, align=0):
    """True when a text box has anything PyMuPDF's own appearance can't show."""
    return runs_formatted(runs) or any(paras or []) or abs((spacing or 1.0) - 1.0) > 1e-3 or align == JUSTIFY


def rich_layout(runs, size, fontname, width=None, paras=None):
    """The box's lines: dicts with "segs" [(text, run, width)], "w" (text
    width), "indent", "marker" ("•", "3." or "") and "end" (last line of its
    paragraph). Words (which may mix styles, like x²) wrap at `width`; list
    paragraphs wrap at the width left after their indent (a hanging indent)."""
    import re

    paragraphs = [[]]             # per paragraph: words ("sp" | "w", [(text, run), ...])
    for run in normalize_runs(runs):
        for k, part in enumerate(run["t"].split("\n")):
            if k:
                paragraphs.append([])
            words = paragraphs[-1]
            for piece in re.split(r"( +)", part):
                if not piece:
                    continue
                kind = "sp" if piece[0] == " " else "w"
                if words and words[-1][0] == kind == "w":
                    words[-1][1].append((piece, run))
                else:
                    words.append((kind, [(piece, run)]))
    paras = _clean_paras(paras, "\n" * (len(paragraphs) - 1))
    lines, number = [], 0
    for words, kind in zip(paragraphs, paras):
        number = number + 1 if kind == "number" else 0
        indent = LIST_INDENT * size if kind else 0.0
        marker = "•" if kind == "bullet" else f"{number}." if kind == "number" else ""
        room = None if width is None else max(size, width - indent)
        line, line_w, spaces, first = [], 0.0, [], True
        for item in words:
            segs = [(t, r, _run_width(t, size, fontname, r)) for t, r in item[1]]
            if item[0] == "sp":
                spaces.extend(segs)
                continue
            word_w = sum(s[2] for s in segs)
            space_w = sum(s[2] for s in spaces)
            if room is not None and line and line_w + space_w + word_w > room:
                lines.append({"segs": line, "w": line_w, "indent": indent, "marker": marker if first else "",
                              "end": False})
                line, line_w, first = [], 0.0, False
            elif line:
                line.extend(spaces)
                line_w += space_w
            spaces = []
            line.extend(segs)
            line_w += word_w
        lines.append({"segs": line, "w": line_w, "indent": indent, "marker": marker if first else "", "end": True})
    return lines


def _line_pitch(size, spacing):
    return LINE_HEIGHT * size * (spacing or 1.0)


def _draw_rich(annot, style, runs):
    """The box's appearance drawn run by run (see the section notes)."""
    doc = annot.parent.parent
    size, fontname, color, align = style["fontsize"], style["fontname"], style["color"], style["align"]
    spacing = style.get("spacing") or 1.0
    rect = annot.rect
    w, h = max(rect.width, 1), max(rect.height, 1)
    scratch = fitz.open()
    sp = scratch.new_page(width=w, height=h)
    resources = {}        # resource name -> font object xref (in the real document)
    file_fonts = {}       # font path -> resource name
    chars = "".join(sorted(set(runs_text(runs).replace("\n", "") + "•0123456789.")))

    def font_ref(f, text):
        if "code" in f and any(ord(ch) > 255 for ch in text):
            # beyond the base-14 encoding (dashes, curly quotes, bullets, ...): embed the font itself
            ref = file_fonts.get(f["code"])
            if ref is None:
                ref = f"B-{f['code']}"
                data = _base14_file(f["code"]).buffer
                sp.insert_font(fontname=ref, fontbuffer=data)
                resources[ref] = _embed_font(annot.parent, ref, data)
                file_fonts[f["code"]] = ref
            return ref
        if "code" in f:
            ref = f["code"]
            if ref not in resources:
                xref = doc.get_new_xref()
                doc.update_object(xref, f"<</Type/Font/Subtype/Type1/BaseFont/{_BASE14_NAMES[ref]}"
                                        "/Encoding/WinAnsiEncoding>>")
                resources[ref] = xref
            return ref
        ref = file_fonts.get(f["path"])
        if ref is None:
            try:
                data = _subset_font(f["path"], chars)
            except Exception:
                with open(f["path"], "rb") as fh:
                    data = fh.read()
            ref = f"{fonts.pdf_font_ref(f['name'])}-{zlib.crc32(data) & 0xFFFFFF:06X}"
            sp.insert_font(fontname=ref, fontbuffer=data)
            resources[ref] = _embed_font(annot.parent, ref, data)
            file_fonts[f["path"]] = ref
        return ref

    plain = _run_font(fontname, False, False)
    for i, line in enumerate(rich_layout(runs, size, fontname, w, style.get("paras"))):
        baseline = FIRST_BASELINE * size + i * _line_pitch(size, spacing)
        room = w - line["indent"]
        x = line["indent"] + {1: (room - line["w"]) / 2, 2: room - line["w"]}.get(align, 0)
        stretch = 0.0
        if align == JUSTIFY and not line["end"]:
            gaps = sum(1 for t, _r, _w in line["segs"] if not t.strip())
            stretch = (room - line["w"]) / gaps if gaps else 0.0
        if line["marker"]:
            mx = line["indent"] - LIST_INDENT * size * 0.8
            sp.insert_text((mx, baseline), line["marker"], fontname=font_ref(plain, line["marker"]), fontsize=size,
                           color=color)
        for text, run, seg_w in line["segs"]:
            s = _run_size(size, run)
            y = baseline - SUPER_RISE * size if run.get("v") == 1 else \
                baseline + SUB_DROP * size if run.get("v") == -1 else baseline
            f = _run_font(fontname, run.get("b"), run.get("i"))
            if not text.strip():
                seg_w += stretch          # justified: the gaps take up the slack
            else:
                extra = {}
                if f["fake_bold"]:
                    extra.update(render_mode=2, border_width=0.035)
                if f["fake_italic"]:
                    extra["morph"] = (fitz.Point(x, y), fitz.Matrix(1, 0, 0.2, 1, 0, 0))
                sp.insert_text((x, y), text, fontname=font_ref(f, text), fontsize=s, color=color, **extra)
            thick = max(0.5, s * 0.06)
            if run.get("u"):
                sp.draw_line((x, y + 0.13 * s), (x + seg_w, y + 0.13 * s), color=color, width=thick)
            if run.get("s"):
                sp.draw_line((x, y - 0.3 * s), (x + seg_w, y - 0.3 * s), color=color, width=thick)
            x += seg_w
    content = sp.read_contents()
    kind, ap = doc.xref_get_key(annot.xref, "AP/N")
    xobj = int(ap.split()[0]) if kind == "xref" else doc.get_new_xref()
    font_dict = "".join(f"/{ref} {xref} 0 R" for ref, xref in resources.items())
    doc.update_object(xobj, f"<</Type/XObject/Subtype/Form/BBox[0 0 {w:g} {h:g}]"
                            f"/Resources<</Font<<{font_dict}>>>>>>")
    doc.update_stream(xobj, content)
    doc.xref_set_key(annot.xref, "AP", f"<</N {xobj} 0 R>>")
    return True


def text_box_size(text, fontsize, fontname, width=None, runs=None, paras=None, spacing=1.0):
    """(width, height) in points of a box that fits `text`. With width=None
    the box grows to the longest line; otherwise lines wrap at `width`.
    Formatting, lists and line spacing are measured as drawn."""
    if needs_own_drawing(runs, paras, spacing):
        lines = rich_layout(runs or [{"t": text}], fontsize, fontname, width, paras)
        if width is None:
            width = max(line["indent"] + line["w"] for line in lines) + TEXT_SLACK
        height = LINE_HEIGHT * fontsize + (max(1, len(lines)) - 1) * _line_pitch(fontsize, spacing)
        return max(width, fontsize), height
    lines = wrap_text(text, fontsize, fontname, width)
    if width is None:
        measure = _measurer(fontname)
        width = max(measure(line, fontsize) for line in lines) + TEXT_SLACK
    return max(width, fontsize), max(1, len(lines)) * LINE_HEIGHT * fontsize


def add_text_box(page, origin, text, color, fontsize=12, fontname=fonts.DEFAULT_FONT, align=0, width=None,
                 runs=None, paras=None, spacing=1.0):
    """Text box with its top-left corner at `origin`, sized to fit the text."""
    w, h = text_box_size(text, fontsize, fontname, width, runs, paras, spacing)
    rect = fitz.Rect(origin.x, origin.y, origin.x + w, origin.y + h)
    annot = add_freetext(page, rect, text, color, fontsize, fontname, align, runs=runs, paras=paras, spacing=spacing)
    if width is not None:
        page.parent.xref_set_key(annot.xref, FIXED_WIDTH_KEY, "true")
    return annot


def add_freetext(page: fitz.Page, rect, text, color, fontsize=12, fontname=fonts.DEFAULT_FONT, align=0,
                 runs=None, paras=None, spacing=1.0) -> fitz.Annot:
    """FreeText annotation in the chosen font. align: 0 left, 1 centre, 2 right.
    `runs` carry bold / italic / ... formatting (see rich text below)."""
    annot = page.add_freetext_annot(
        rect, text, fontsize=fontsize, fontname=fonts.BASE14_FONTS.get(fontname, "helv"),
        text_color=color_to_rgb(color), align=0 if align == JUSTIFY else align,
    )
    annot.update()
    if fonts.is_custom_font(fontname):
        page.parent.xref_set_key(annot.xref, CUSTOM_FONT_KEY, fitz.get_pdf_str(fontname))
    if runs is not None:
        _store_runs(page.parent, annot.xref, runs)
    _store_layout(page.parent, annot.xref, text, paras, spacing, align)
    _apply_custom_appearance(annot)
    return annot


def freetext_style(annot) -> dict:
    """Font, size, colour (0-1 RGB), alignment and text of a FreeText box."""
    doc = annot.parent.parent
    da = doc.xref_get_key(annot.xref, "DA")[1] or ""
    style = {"fontsize": 12.0, "fontname": fonts.DEFAULT_FONT, "color": (0.0, 0.0, 0.0), "align": 0,
             "text": annot.info.get("content", ""), "fixed_width": False}
    tokens = da.replace("/", " /").split()
    for i, tok in enumerate(tokens):
        try:
            if tok == "Tf" and i >= 2:
                style["fontname"] = _DA_FONTS.get(tokens[i - 2].lstrip("/").lower()[:4], fonts.DEFAULT_FONT)
                style["fontsize"] = float(tokens[i - 1])
            elif tok == "rg" and i >= 3:
                style["color"] = tuple(float(v) for v in tokens[i - 3:i])
            elif tok == "g" and i >= 1:
                style["color"] = (float(tokens[i - 1]),) * 3
        except ValueError:
            pass
    kind, custom = doc.xref_get_key(annot.xref, CUSTOM_FONT_KEY)
    if kind == "string" and custom:
        style["fontname"] = custom
    kind, q = doc.xref_get_key(annot.xref, "Q")
    if kind == "int":
        style["align"] = int(q)
    if doc.xref_get_key(annot.xref, JUSTIFY_KEY)[1] == "true":
        style["align"] = JUSTIFY
    kind, value = doc.xref_get_key(annot.xref, SPACING_KEY)
    try:
        style["spacing"] = float(value) if kind in ("float", "real", "int") else 1.0
    except ValueError:
        style["spacing"] = 1.0
    kind, value = doc.xref_get_key(annot.xref, PARAS_KEY)
    try:
        style["paras"] = _clean_paras(json.loads(value) if kind == "string" else [], style["text"])
    except ValueError:
        style["paras"] = _clean_paras([], style["text"])
    style["fixed_width"] = doc.xref_get_key(annot.xref, FIXED_WIDTH_KEY)[1] == "true"
    style["runs"] = rich_runs(annot)
    return style


def set_freetext(annot, text, color, fontsize, fontname, rect, align=None, fixed_width=None, runs=None,
                 paras=None, spacing=None):
    """Rewrite an existing text box's text, style and rect in place (its
    formatting, lists and spacing too when given; otherwise they are kept)."""
    doc = annot.parent.parent
    if runs is not None:
        _store_runs(doc, annot.xref, runs)
    if paras is not None or spacing is not None or align is not None:
        old = freetext_style(annot)
        _store_layout(doc, annot.xref, text, old["paras"] if paras is None else paras,
                      old["spacing"] if spacing is None else spacing, old["align"] if align is None else align)
    if align is not None:
        doc.xref_set_key(annot.xref, "Q", str(0 if align == JUSTIFY else int(align)))
    if fixed_width is not None:
        doc.xref_set_key(annot.xref, FIXED_WIDTH_KEY, "true" if fixed_width else "null")
    annot.set_info(content=text)
    annot.set_rect(fitz.Rect(rect))
    annot.update(fontsize=fontsize, fontname=fonts.BASE14_FONTS.get(fontname, "helv"),
                 text_color=color_to_rgb(color))
    if fonts.is_custom_font(fontname):
        doc.xref_set_key(annot.xref, CUSTOM_FONT_KEY, fitz.get_pdf_str(fontname))
    else:
        doc.xref_set_key(annot.xref, CUSTOM_FONT_KEY, "null")
    _apply_custom_appearance(annot)


@lru_cache(maxsize=64)
def _subset_font(path, chars):
    """The font file cut down to `chars` (plus space), so a PDF carries a few
    KB per text box instead of the whole font."""
    import logging

    from fontTools import subset
    from fontTools.ttLib import TTFont

    logging.getLogger("fontTools.subset").setLevel(logging.ERROR)
    font = TTFont(path, fontNumber=0)
    options = subset.Options()
    options.name_IDs = ["*"]
    options.notdef_outline = True
    options.layout_features = []
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=sorted({ord(c) for c in chars} | {32}))
    subsetter.subset(font)
    buf = io.BytesIO()
    font.save(buf)
    return buf.getvalue()


def _embed_font(page, ref, data):
    """Add a font object to the document for an appearance stream. The entry
    PyMuPDF also puts in the page's resources is removed again, so the font
    is only kept alive by the text box that uses it (unused subsets are then
    dropped when the PDF is saved)."""
    xref = page.insert_font(fontname=ref, fontbuffer=data)
    try:
        page.parent.xref_set_key(page.xref, f"Resources/Font/{ref}", "null")
    except Exception:
        pass
    return xref


def _apply_custom_appearance(annot):
    """Draw the box's text with its embedded custom font. No-op for base-14
    boxes, and for custom boxes whose font file is not installed (the saved
    appearance is then kept until the box is changed)."""
    doc = annot.parent.parent
    if is_text_box(annot):
        style = freetext_style(annot)
        if needs_own_drawing(style["runs"], style["paras"], style["spacing"], style["align"]):
            return _draw_rich(annot, style, style["runs"] or [{"t": style["text"]}])
    kind, fontname = doc.xref_get_key(annot.xref, CUSTOM_FONT_KEY)
    if kind != "string" or not fontname:
        return False
    fontfile = fonts.custom_font_path(fontname)
    if not fontfile:
        return False
    style = freetext_style(annot)
    size = style["fontsize"]
    rect = annot.rect
    w, h = max(rect.width, 1), max(rect.height, 1)
    measure = _measurer(fontname)
    chars = "".join(sorted(set(style["text"].replace("\n", ""))))
    try:
        data = _subset_font(fontfile, chars)
    except Exception:
        with open(fontfile, "rb") as f:   # subsetting failed: embed the whole font
            data = f.read()
    ref = f"{fonts.pdf_font_ref(fontname)}-{zlib.crc32(data) & 0xFFFFFF:06X}"

    scratch = fitz.open()
    sp = scratch.new_page(width=w, height=h)
    sp.insert_font(fontname=ref, fontbuffer=data)
    for i, line in enumerate(wrap_text(style["text"], size, fontname, w)):
        if not line:
            continue
        free = w - measure(line, size)
        x = {1: free / 2, 2: free}.get(style["align"], 0)
        sp.insert_text((x, FIRST_BASELINE * size + i * LINE_HEIGHT * size), line,
                       fontname=ref, fontsize=size, color=style["color"])
    content = sp.read_contents()

    font_xref = _embed_font(annot.parent, ref, data)
    kind, ap = doc.xref_get_key(annot.xref, "AP/N")
    xobj = int(ap.split()[0]) if kind == "xref" else doc.get_new_xref()
    doc.update_object(xobj, f"<</Type/XObject/Subtype/Form/BBox[0 0 {w:g} {h:g}]"
                            f"/Resources<</Font<</{ref} {font_xref} 0 R>>>>>>")
    doc.update_stream(xobj, content)
    doc.xref_set_key(annot.xref, "AP", f"<</N {xobj} 0 R>>")
    return True


def is_text_box(annot) -> bool:
    return annot is not None and annot.type[0] == fitz.PDF_ANNOT_FREE_TEXT


# Annotation types whose rect can be dragged to a new size
RESIZABLE_TYPES = {fitz.PDF_ANNOT_FREE_TEXT, fitz.PDF_ANNOT_SQUARE, fitz.PDF_ANNOT_CIRCLE, fitz.PDF_ANNOT_STAMP}


def is_resizable(annot) -> bool:
    return annot is not None and annot.type[0] in RESIZABLE_TYPES


def resize_annot(annot, rect):
    """Give an annotation a new rect. Text boxes re-wrap to the new width and
    never shrink below the height their text needs."""
    rect = fitz.Rect(rect).normalize()
    if is_text_box(annot):
        style = freetext_style(annot)
        _w, need_h = text_box_size(style["text"], style["fontsize"], style["fontname"], rect.width, style["runs"],
                                   style["paras"], style["spacing"])
        rect.y1 = max(rect.y1, rect.y0 + need_h)
        set_freetext(annot, style["text"], QColor.fromRgbF(*style["color"]), style["fontsize"],
                     style["fontname"], rect, fixed_width=True)
        return
    annot.set_rect(rect)
    annot.update()


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


# ---------------------------------------------------------------------------
# Pictures you place: a stamp annotation showing the picture (so it can be
# selected, moved, resized and edited), with the original kept in the PDF
# ---------------------------------------------------------------------------

IMAGE_KEY = "AupImage"


def add_image_stamp(page: fitz.Page, rect, image_bytes: bytes) -> fitz.Annot:
    """A movable picture filling `rect` (keeping its proportions, centred)."""
    pix = fitz.Pixmap(image_bytes)
    rect = _fit(fitz.Rect(rect), pix.width, pix.height)
    png = pix.tobytes("png")   # stamps take PNG/JPEG; PNG keeps transparency (a removed background)
    annot = page.add_stamp_annot(rect, stamp=png)
    annot.set_info(subject="Picture", title="Aupedean Annotator")
    annot.update()
    doc = page.parent
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, png)
    doc.xref_set_key(annot.xref, IMAGE_KEY, f"{xref} 0 R")
    return annot


def _fit(rect, width, height):
    if not width or not height or rect.is_empty:
        return rect
    scale = min(rect.width / width, rect.height / height)
    w, h = width * scale, height * scale
    x, y = rect.x0 + (rect.width - w) / 2, rect.y0 + (rect.height - h) / 2
    return fitz.Rect(x, y, x + w, y + h)


def image_stamp_bytes(annot):
    """The picture (PNG bytes) of a picture stamp, else None."""
    if annot is None or annot.type[0] != fitz.PDF_ANNOT_STAMP:
        return None
    doc = annot.parent.parent
    kind, value = doc.xref_get_key(annot.xref, IMAGE_KEY)
    if kind != "xref":
        return None
    try:
        return doc.xref_stream(int(value.split()[0]))
    except (ValueError, RuntimeError):
        return None


def is_image_stamp(annot) -> bool:
    return image_stamp_bytes(annot) is not None


def replace_image_stamp(page: fitz.Page, annot, image_bytes: bytes) -> fitz.Annot:
    """Swap a picture stamp's picture for an edited one, in the same place
    (the box keeps its width and takes the new proportions)."""
    rect = fitz.Rect(annot.rect)
    pix = fitz.Pixmap(image_bytes)
    if pix.width and pix.height:
        rect = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + rect.width * pix.height / pix.width)
    page.delete_annot(annot)
    return add_image_stamp(page, rect, image_bytes)


def page_image_at(page: fitz.Page, point):
    """(xref, bbox) of a picture that is part of the page's content under
    `point` (the topmost), else None."""
    found = None
    for info in page.get_image_info(xrefs=True):
        bbox = fitz.Rect(info["bbox"])
        if info.get("xref") and bbox.contains(point):
            found = (info["xref"], bbox)
    return found


def page_image_bytes(page: fitz.Page, xref) -> bytes:
    """A page picture as PNG (with its transparency, if it has a mask)."""
    pix = fitz.Pixmap(page.parent, xref)
    if pix.alpha == 0:
        smask = page.parent.xref_get_key(xref, "SMask")
        if smask[0] == "xref":
            pix = fitz.Pixmap(pix, fitz.Pixmap(page.parent, int(smask[1].split()[0])))
    if pix.colorspace and pix.colorspace.n > 3:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    return pix.tobytes("png")


def replace_page_image(page: fitz.Page, xref, image_bytes: bytes):
    page.replace_image(xref, stream=image_bytes)


# ---------------------------------------------------------------------------
# Formulas: LaTeX rendered to a picture, kept in a stamp annotation together
# with its source so it can be moved, resized and edited again
# ---------------------------------------------------------------------------

FORMULA_KEY = "AupFormula"


def add_formula(page: fitz.Page, origin, rendered, source, fontsize, color_rgb, rect=None) -> fitz.Annot:
    """A stamp showing `rendered` (formula.Rendered) with its top-left at
    `origin` (or filling `rect`), remembering the LaTeX `source`."""
    if rect is None:
        # the picture's exact proportions, so MuPDF doesn't centre (and shift) it in the box
        pix = fitz.Pixmap(rendered.png)
        height = rendered.width_pt * pix.height / pix.width if pix.width else rendered.height_pt
        rect = fitz.Rect(origin.x, origin.y, origin.x + rendered.width_pt, origin.y + height)
    annot = page.add_stamp_annot(fitz.Rect(rect), stamp=rendered.png)
    annot.set_info(content=source, subject="Formula", title="Aupedean Annotator")
    annot.update()
    data = {"source": source, "fontsize": float(fontsize), "color": [int(c) for c in color_rgb[:3]],
            "engine": rendered.engine}
    page.parent.xref_set_key(annot.xref, FORMULA_KEY, fitz.get_pdf_str(json.dumps(data)))
    return annot


def formula_data(annot):
    """{"source", "fontsize", "color", "engine"} of a formula stamp, else None."""
    if annot is None or annot.type[0] != fitz.PDF_ANNOT_STAMP:
        return None
    kind, value = annot.parent.parent.xref_get_key(annot.xref, FORMULA_KEY)
    if kind != "string":
        return None
    try:
        return json.loads(value)
    except ValueError:
        return None


def is_formula(annot) -> bool:
    return formula_data(annot) is not None


def find_annot_at(page: fitz.Page, point: fitz.Point):
    for annot in page.annots():
        if annot.rect.contains(point):
            return annot
    return None


# Annotations positioned by their points, not their rect (MuPDF refuses set_rect for them)
_VERTEX_KEYS = {fitz.PDF_ANNOT_INK: "InkList", fitz.PDF_ANNOT_LINE: "L", fitz.PDF_ANNOT_POLYGON: "Vertices",
                fitz.PDF_ANNOT_POLY_LINE: "Vertices"}


def _move_vertices(annot, dx, dy):
    """Shift an ink / line / polygon annotation by rewriting its points."""
    doc, page = annot.parent.parent, annot.parent
    to_pdf = ~page.transformation_matrix  # page coordinates -> PDF user space

    def pdf(pt):
        q = fitz.Point(pt[0] + dx, pt[1] + dy) * to_pdf
        return f"{q.x:.3f} {q.y:.3f}"

    kind = annot.type[0]
    vertices = annot.vertices or []
    if kind == fitz.PDF_ANNOT_INK:
        value = "[" + "".join("[" + " ".join(pdf(pt) for pt in stroke) + "]" for stroke in vertices) + "]"
    else:
        value = "[" + " ".join(pdf(pt) for pt in vertices) + "]"
    doc.xref_set_key(annot.xref, _VERTEX_KEYS[kind], value)
    r = annot.rect
    doc.xref_set_key(annot.xref, "Rect", "[{:.3f} {:.3f} {:.3f} {:.3f}]".format(
        *fitz.Rect(r.x0 + dx, r.y0 + dy, r.x1 + dx, r.y1 + dy).transform(to_pdf).normalize()))


def move_annot(annot: fitz.Annot, dx: float, dy: float):
    if annot.type[0] in _VERTEX_KEYS:
        _move_vertices(annot, dx, dy)
    else:
        r = annot.rect
        annot.set_rect(fitz.Rect(r.x0 + dx, r.y0 + dy, r.x1 + dx, r.y1 + dy))
    annot.update()
    _apply_custom_appearance(annot)
    _apply_pressure_appearance(annot)


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
        data["pressure"] = pressure_widths(annot)
        data["opacity"] = annot.opacity
    elif type_name in ("Polygon", "Line"):
        data["vertices"] = list(annot.vertices)
        if type_name == "Line":
            ends = getattr(annot, "line_ends", (0, 0))
            data["arrow"] = bool(ends and ends[1] not in (0, None))
    elif type_name == "FreeText":
        data["text_style"] = freetext_style(annot)
    elif type_name == "Stamp" and is_formula(annot):
        data["formula"] = formula_data(annot)
    elif type_name == "Stamp" and is_image_stamp(annot):
        data["image"] = base64.b64encode(image_stamp_bytes(annot)).decode()
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
        widths = data.get("pressure")
        if widths and override_width is not None and max(widths):
            widths = [w * override_width / max(widths) for w in widths]
        annot.set_border(width=max(widths) if widths else width)
        if data.get("opacity") is not None and 0 <= data["opacity"] < 1:
            annot.set_opacity(data["opacity"])
        annot.update()
        if widths:
            page.parent.xref_set_key(annot.xref, PRESSURE_KEY, "[" + " ".join(f"{w:.3f}" for w in widths) + "]")
            _apply_pressure_appearance(annot)
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
        style = data.get("text_style") or {}
        color = override_color if override_color is not None else style.get("color", (0, 0, 0))
        annot = add_freetext(page, rect, data["content"], QColor.fromRgbF(*color), style.get("fontsize", 12),
                             style.get("fontname", fonts.DEFAULT_FONT), style.get("align", 0), runs=style.get("runs"),
                             paras=style.get("paras"), spacing=style.get("spacing", 1.0))
        if style.get("fixed_width"):
            page.parent.xref_set_key(annot.xref, FIXED_WIDTH_KEY, "true")
        return annot
    if type_name == "Text":
        r = data["rect"]
        annot = page.add_text_annot(fitz.Point(r[0] + dx, r[1] + dy), data["content"])
        annot.set_colors(stroke=stroke)
        annot.update()
        return annot
    if type_name == "Stamp" and data.get("image"):
        r = data["rect"]
        rect = fitz.Rect(r[0] + dx, r[1] + dy, r[2] + dx, r[3] + dy)
        return add_image_stamp(page, rect, base64.b64decode(data["image"]))
    if type_name == "Stamp" and data.get("formula"):
        from . import formula

        f = data["formula"]
        color = f["color"] if override_color is None else [round(c * 255) for c in override_color]
        rendered = formula.render(f["source"], f["fontsize"], color)
        r = data["rect"]
        rect = fitz.Rect(r[0] + dx, r[1] + dy, r[2] + dx, r[3] + dy)
        return add_formula(page, rect.tl, rendered, f["source"], f["fontsize"], color, rect=rect)
    if type_name in ("Square", "Circle"):
        r = data["rect"]
        rect = fitz.Rect(r[0] + dx, r[1] + dy, r[2] + dx, r[3] + dy)
        annot = page.add_rect_annot(rect) if type_name == "Square" else page.add_circle_annot(rect)
        annot.set_colors(stroke=stroke)
        annot.set_border(width=width)
        annot.update()
        return annot
    return None
