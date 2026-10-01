"""Exact-layout reading of PDF pages, shared by the Word and LaTeX converters.

The idea: a page is two layers.

- The background: the page itself with its text taken out (a redaction that
  removes text only), so rules, boxes, table borders, plots, fraction bars
  and pictures stay exactly as they are.
- The text: every line, split into segments (runs of one font, size, style
  and colour on one baseline), each with the exact position of its first
  character and the room it takes up, so the writer can place it where it
  was and make it exactly as wide.

Fonts are sorted into families the writers know how to match: the PDF's
Helvetica / Nimbus Sans, Times / Nimbus Roman, Courier, Computer Modern /
Latin Modern (text, sans, mono), maths (CM math italic, symbols,
extensions, AMS), and any other named font (Calibri, Garamond...), which is
used by name. Characters a font doesn't map to Unicode can't be retyped, so
those bits are kept as small pictures cut from the page.

No Qt here."""
import math
import re
from dataclasses import dataclass, field

import pymupdf as fitz

SANS, SERIF, MONO = "sans", "serif", "mono"
CM_SERIF, CM_SANS, CM_MONO = "cm-serif", "cm-sans", "cm-mono"
MATH = "math"                      # symbols, Greek, big operators: a maths font
_BOLD, _ITALIC, _MONO_FLAG, _SERIF_FLAG = 16, 2, 8, 4
_SUBSET = re.compile(r"^[A-Z]{6}\+")


def _base_name(font):
    return _SUBSET.sub("", font or "")


def classify(font, flags=0):
    """(family, bold, italic, system font name or None) for a PDF font."""
    name = _base_name(font)
    low = name.lower().replace(" ", "")
    bold = bool(flags & _BOLD) or any(k in low for k in ("bold", "black", "heavy", "semibold", "-bx", "cmbx"))
    italic = bool(flags & _ITALIC) or any(k in low for k in ("italic", "oblique", "ital", "cmti", "cmsl"))
    # Computer Modern / Latin Modern and the TeX maths fonts
    if re.match(r"(cm|lm)(mi|sy|ex|bsy|mib)\d*", low) or low.startswith(("msam", "msbm", "eufm", "eusm", "rsfs",
                                                                        "stmary", "wasy", "latinmodern-math",
                                                                        "lmmath", "cmmib")):
        return MATH, bold, italic or low.startswith(("cmmi", "lmmi")), None
    if re.match(r"(cm|lm)(ss|sans)", low) or low.startswith(("sfss", "lmsans")):
        return CM_SANS, bold, italic, None
    if re.match(r"(cm|lm)(tt|mono|vtt)", low) or low.startswith(("sftt", "lmmono", "lmtypewriter")):
        return CM_MONO, bold, italic, None
    if re.match(r"(cm|lm)(r|bx|ti|sl|csc|u|roman|b)\d", low) or low.startswith(("sfrm", "sfbx", "sfti", "sfsl",
                                                                                 "lmroman", "cmu")):
        return CM_SERIF, bold, italic, None
    # the standard PDF fonts and their free twins
    if any(k in low for k in ("helvetica", "nimbussan", "arial", "liberationsans", "texgyreheros", "freesans")):
        return SANS, bold, italic, None
    if any(k in low for k in ("times", "nimbusrom", "liberationserif", "texgyretermes", "freeserif")):
        return SERIF, bold, italic, None
    if any(k in low for k in ("courier", "nimbusmon", "liberationmono", "texgyrecursor", "freemono")):
        return MONO, bold, italic, None
    if low.startswith(("symbol", "cambriamath", "stix", "asana", "xits")):
        return MATH, bold, italic, None
    # any other font: used by its own name when this PC has it
    family = re.split(r"[-,]", name)[0]
    family = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", family).replace("MT", "").strip()
    generic = MONO if flags & _MONO_FLAG else SERIF if flags & _SERIF_FLAG else SANS
    return generic, bold, italic, family or None


# Greek letters from the TeX math italic font are italic in the original
_GREEK_ITALIC = {chr(0x03B1 + i): chr(0x1D6FC + i) for i in range(25)}   # α..ω -> 𝛼..𝜔
_GREEK_ITALIC.update({"ϑ": "𝜗", "ϕ": "𝜙", "ϖ": "𝜛", "ϱ": "𝜚", "ϵ": "𝜖", "ε": "𝜀", "µ": "𝜇"})
_ASCII_ITALIC = {}
for _i in range(26):
    _ASCII_ITALIC[chr(65 + _i)] = chr(0x1D434 + _i)
    _ASCII_ITALIC[chr(97 + _i)] = chr(0x1D44E + _i)
_ASCII_ITALIC["h"] = "ℎ"   # the one math italic letter that lives elsewhere


_DOUBLE_STRUCK = {chr(65 + i): chr(0x1D538 + i) for i in range(26)}
_DOUBLE_STRUCK.update({"C": "ℂ", "H": "ℍ", "N": "ℕ", "P": "ℙ", "Q": "ℚ", "R": "ℝ", "Z": "ℤ"})


def math_text(text, italic):
    """Text from a maths font, in the Unicode maths letters, so a maths
    font shows it the way TeX did (math italic letters and Greek)."""
    if not italic:
        return text
    return "".join(_ASCII_ITALIC.get(ch) or _GREEK_ITALIC.get(ch) or ch for ch in text)


def _unmappable(text):
    return any(ch == "�" or 0xE000 <= ord(ch) <= 0xF8FF for ch in text)


@dataclass
class Segment:
    x: float                 # where its first character starts (page points, from the left)
    y: float                 # its baseline (page points, from the top)
    width: float             # the room it takes up up to its last character
    text: str
    family: str
    size: float
    bold: bool = False
    italic: bool = False
    color: tuple = (0, 0, 0)
    system_font: str | None = None
    bbox: tuple = (0, 0, 0, 0)
    picture: bool = False    # couldn't be retyped: cut out of the page as a picture
    font: str = ""           # the PDF font's name (lower case, without the subset tag)
    words: list = field(default_factory=list)   # [(word, x where it starts)], for writers that place words

    @property
    def style(self):
        return (self.family, round(self.size, 2), self.bold, self.italic, self.color, self.system_font)


@dataclass
class Line:
    segments: list
    angle: float = 0.0       # degrees, counter-clockwise (0: ordinary text)
    origin: tuple = (0, 0)

    @property
    def x(self):
        return self.segments[0].x

    @property
    def y(self):
        return self.segments[0].y


@dataclass
class PageLayout:
    width: float
    height: float
    lines: list = field(default_factory=list)
    links: list = field(default_factory=list)      # (rect, uri)
    has_background: bool = False
    invisible_text: bool = False                     # an OCR text layer over a scan: keep the scan, drop the text


def _color(value):
    rgb = ((value >> 16) & 255, (value >> 8) & 255, value & 255)
    return (0, 0, 0) if max(rgb) < 40 else rgb


def _invisible_ratio(page):
    try:
        trace = page.get_texttrace()
    except Exception:  # noqa: BLE001
        return 0.0
    total = sum(len(t.get("chars", ())) for t in trace) or 1
    hidden = sum(len(t.get("chars", ())) for t in trace if t.get("type") == 3 or t.get("opacity", 1) == 0)
    return hidden / total


def read_page(page):
    """The page's text as positioned lines of segments (see PageLayout)."""
    rot = page.rotation_matrix
    layout = PageLayout(page.rect.width, page.rect.height)
    layout.invisible_text = _invisible_ratio(page) > 0.6
    layout.has_background = bool(page.get_drawings() or page.get_images(full=True))
    for link in page.get_links():
        uri = link.get("uri")
        if uri:
            layout.links.append((fitz.Rect(link["from"]) * rot, uri))
    if layout.invisible_text:
        return layout
    data = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_PRESERVE_LIGATURES
                         | fitz.TEXT_MEDIABOX_CLIP)
    for block in data["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            dx, dy = line.get("dir", (1, 0))
            angle = -math.degrees(math.atan2(dy, dx))
            segments = []
            for span in line["spans"]:
                for seg in _span_segments(span, rot, angle):
                    if segments and _joins(segments[-1], seg):
                        prev = segments[-1]
                        gap = seg.x - (prev.x + prev.width)
                        prev.text += (" " if gap > 0.15 * seg.size and not prev.text.endswith(" ") else "") + seg.text
                        prev.width = seg.x + seg.width - prev.x
                        prev.words.extend(seg.words)
                        prev.bbox = tuple(fitz.Rect(prev.bbox) | fitz.Rect(seg.bbox))
                    else:
                        segments.append(seg)
            if segments:
                layout.lines.append(Line(segments, angle if abs(angle) > 0.5 else 0.0,
                                         (segments[0].x, segments[0].y)))
    layout.lines.sort(key=lambda l: (round(l.y, 1), l.x))
    return layout


def _span_segments(span, rot, angle):
    """A span as segments that start at their first visible character and end
    at their last (the spaces round them take no room). Maths is split at its
    spaces, so each piece lands exactly where it was (its letters are not
    quite as wide as TeX's, so it can't be spread out like text)."""
    chars = [c for c in span.get("chars", []) if c["c"]]
    family = classify(span["font"], span["flags"])[0]
    if family != MATH:
        seg = _chars_segment(span, chars, rot, angle)
        return [seg] if seg else []
    out, piece = [], []
    for c in chars + [{"c": " "}]:
        if c["c"].isspace() or c["c"] == " ":
            if piece:
                seg = _chars_segment(span, piece, rot, angle)
                if seg:
                    out.append(seg)
            piece = []
        else:
            piece.append(c)
    return out


def _chars_segment(span, chars, rot, angle):
    visible = [i for i, c in enumerate(chars) if not c["c"].isspace() and c["c"] != " "]
    if not visible:
        return None
    first, last = chars[visible[0]], chars[visible[-1]]
    text = "".join(c["c"] for c in chars[visible[0]:visible[-1] + 1]).replace(" ", " ")
    family, bold, italic, system = classify(span["font"], span["flags"])
    origin = fitz.Point(first["origin"]) * rot
    if angle:
        end = fitz.Point(last["bbox"][2], last["bbox"][3]) * rot
        width = math.hypot(end.x - origin.x, end.y - origin.y)
    else:
        width = max(0.0, (fitz.Rect(last["bbox"]) * rot).x1 - origin.x)
    box = fitz.Rect(first["bbox"]) | fitz.Rect(last["bbox"])
    seg = Segment(origin.x, origin.y, width, text, family, span["size"], bold, italic,
                  _color(span.get("color", 0)), system, tuple(box * rot), picture=_unmappable(text))
    word, word_x = "", None
    for c in chars[visible[0]:visible[-1] + 1]:
        if c["c"].isspace() or c["c"] == " ":
            if word:
                seg.words.append((word, word_x))
            word, word_x = "", None
        else:
            if not word:
                word_x = (fitz.Point(c["origin"]) * rot).x
            word += c["c"]
    if word:
        seg.words.append((word, word_x))
    base = _base_name(span["font"]).lower()
    if family == MATH:
        if base.startswith(("msbm", "bbold", "dsrom")):   # blackboard bold: ℝ, ℕ, ℤ...
            convert = lambda t: "".join(_DOUBLE_STRUCK.get(ch, ch) for ch in t)  # noqa: E731
        else:
            convert = lambda t: math_text(t, italic)  # noqa: E731
        seg.text = convert(text)
        seg.words = [(convert(w), x) for w, x in seg.words]
        # TeX's big operators and delimiters (∫ ∑ ( ) at display size) have no Unicode of
        # their own size: those few glyphs are kept as sharp pictures from the page
        tall = (box.height > 1.45 * span["size"])
        if base.startswith(("cmex", "lmex")) or (tall and any(ch in "∫∬∭∮∑∏∐⋃⋂⨁⨂()[]{}⟨⟩|‖√" for ch in text)):
            seg.picture = True
    seg.font = base
    return seg


def _joins(prev, seg):
    """Can `seg` be typeset straight after `prev` (same style, same baseline,
    no jump across the line)?"""
    if prev.picture or seg.picture or prev.style != seg.style or abs(prev.y - seg.y) > 0.3:
        return False
    if seg.family == MATH and seg.x - (prev.x + prev.width) > 0.1 * seg.size:
        return False   # maths across a space: placed piece by piece
    gap = seg.x - (prev.x + prev.width)
    # a wider gap (after a list number, before a tab stop) keeps its own width:
    # the next piece is placed where it was
    return -0.5 * seg.size < gap < 0.6 * seg.size


def text_free_copy(doc, indexes):
    """A PDF of the pages `indexes` with all their text taken out (graphics,
    pictures and annotations stay), for the backgrounds."""
    out = fitz.open()
    for i in indexes:
        out.insert_pdf(doc, from_page=i, to_page=i)
    for page in out:
        try:
            page.add_redact_annot(page.rect, fill=False)
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                                  text=fitz.PDF_REDACT_TEXT_REMOVE)
        except Exception:  # noqa: BLE001 - an odd page: keep it whole as its own background
            pass
    return out


def glyph_page(page, layout):
    """A copy of `page` with only the glyphs kept as pictures left in its
    text (everything else typed is taken out), so cutting a generous area
    around a tall glyph (a display integral) doesn't pick up its neighbours.
    Returns (document, page) or None when the page has no such glyphs."""
    pictures = [s for line in layout.lines for s in line.segments if s.picture]
    if not pictures:
        return None
    doc = fitz.open()
    doc.insert_pdf(page.parent, from_page=page.number, to_page=page.number)
    copy = doc[0]
    inverse = ~page.rotation_matrix
    for line in layout.lines:
        for seg in line.segments:
            if not seg.picture:
                r = fitz.Rect(seg.bbox) * inverse
                copy.add_redact_annot(r + (0.5, 0.5, -0.5, -0.5), fill=False)
    try:
        copy.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                              text=fitz.PDF_REDACT_TEXT_REMOVE)
        # ...and the lines and pictures, which the background already has
        copy.add_redact_annot(copy.rect, fill=False)
        copy.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE,
                              graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED, text=fitz.PDF_REDACT_TEXT_NONE)
    except Exception:  # noqa: BLE001 - cut from the page as it is
        pass
    return doc, copy


def picture_rect(seg, page_rect):
    """The area to cut for a glyph kept as a picture: tall glyphs (big
    integrals, brackets) reach well above and below their line."""
    r = fitz.Rect(seg.bbox)
    grow_y = 1.4 * seg.size
    return (fitz.Rect(r.x0 - 0.15 * seg.size, r.y0 - grow_y, r.x1 + 0.15 * seg.size, r.y1 + grow_y)
            & page_rect)


def justified(seg):
    """True for a segment whose words were spread out to fill the line."""
    return " " in seg.text.strip()
