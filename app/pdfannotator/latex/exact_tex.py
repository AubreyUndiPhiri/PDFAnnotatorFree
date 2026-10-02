"""Exact-layout LaTeX: each page is its own background (the original page
with its text taken out, as a vector PDF page, so every line, table border,
plot and picture is exactly as it was) with the text typed over it, every
line at its original position, in a matching font and size, as wide as it
was.

    \\PDFPage{3}{%
    \\T{64.03}{53.29}{\\Sa Section 2.4. Parameter Estimation}%
    ...
    }

\\PT{x}{y}{text} puts text with its baseline at (x, y) points from the
the page's lower-left corner; \\PTW{x}{y}{width}{text} also spreads its words to
the original width (justified lines). The text is ordinary LaTeX: edit it
in place. Compile with XeLaTeX (fontspec)."""
import pymupdf as fitz

from .. import exact_layout as ex
from . import texutil

_STYLES_OF = "Extension=.otf, UprightFont=*-regular, BoldFont=*-bold, ItalicFont=*-italic, BoldItalicFont=*-bolditalic"
# family -> (fontspec font, options); all ship with MiKTeX, TeX Live, Overleaf and Tectonic
_FONTS = {
    ex.SANS: ("texgyreheros", _STYLES_OF),           # = Helvetica / Nimbus Sans / Arial metrics
    ex.SERIF: ("texgyretermes", _STYLES_OF),         # = Times / Nimbus Roman
    ex.MONO: ("texgyrecursor", _STYLES_OF),          # = Courier
    ex.CM_SERIF: ("lmroman10", _STYLES_OF),          # = Computer Modern
    ex.CM_SANS: ("lmsans10", "Extension=.otf, UprightFont=*-regular, BoldFont=*-bold, ItalicFont=*-oblique, "
                             "BoldItalicFont=*-boldoblique"),
    ex.CM_MONO: ("lmmono10", "Extension=.otf, UprightFont=*-regular, ItalicFont=*-italic"),
    ex.MATH: ("latinmodern-math", "Extension=.otf"),
}
_MACRO = {ex.SANS: "FSans", ex.SERIF: "FSerif", ex.MONO: "FMono", ex.CM_SERIF: "FRoman", ex.CM_SANS: "FRomanSans",
          ex.CM_MONO: "FRomanMono", ex.MATH: "FMath"}


# Latin Modern comes in the design sizes Computer Modern had (TeX uses the
# nearest one): (family, bold, italic) -> (file stem, sizes)
_OPTICAL = {
    (ex.CM_SERIF, False, False): ("lmroman{}-regular", (5, 6, 7, 8, 9, 10, 12, 17)),
    (ex.CM_SERIF, True, False): ("lmroman{}-bold", (5, 6, 7, 8, 9, 10, 12)),
    (ex.CM_SERIF, False, True): ("lmroman{}-italic", (7, 8, 9, 10, 12)),
    (ex.CM_SERIF, True, True): ("lmroman{}-bolditalic", (10,)),
    (ex.CM_SANS, False, False): ("lmsans{}-regular", (8, 9, 10, 12, 17)),
    (ex.CM_SANS, False, True): ("lmsans{}-oblique", (8, 9, 10, 12, 17)),
    (ex.CM_SANS, True, False): ("lmsans{}-bold", (10,)),
    (ex.CM_SANS, True, True): ("lmsans{}-boldoblique", (10,)),
    (ex.CM_MONO, False, False): ("lmmono{}-regular", (8, 9, 10, 12)),
    (ex.CM_MONO, False, True): ("lmmono{}-italic", (10,)),
    (ex.CM_MONO, True, False): ("lmmonolt{}-bold", (10,)),
    (ex.CM_MONO, True, True): ("lmmonolt{}-boldoblique", (10,)),
}


def optical_font(family, size, bold, italic):
    """The Latin Modern file of the design size nearest `size`, or None."""
    entry = _OPTICAL.get((family, bold, italic))
    if entry is None:
        return None
    stem, sizes = entry
    design = min(sizes, key=lambda d: (abs(d - size), d))
    return stem.format(design) + ".otf"


def _letters(n):
    """0 -> a, 25 -> z, 26 -> aa... (TeX macro names may only have letters)."""
    out, n = "", n + 1
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(97 + r) + out
    return out


def _num(v):
    return f"{v:.2f}".rstrip("0").rstrip(".") or "0"


class ExactTex:
    """Builds main.tex for a document converted with the exact layout."""

    def __init__(self, system_font_ok=None):
        self.styles = {}              # style tuple -> macro name
        self.system_fonts = {}        # font name -> macro
        self.optical = {}             # Latin Modern file of one design size -> macro
        self.system_font_ok = system_font_ok or (lambda name: False)
        self.pictures = 0
        self._glyphs = None           # (document, page) with only the glyphs kept as pictures
        self.width = self.height = 0

    # ---- styles
    def _style_macro(self, seg):
        key = seg.style
        if key not in self.styles:
            self.styles[key] = "S" + _letters(len(self.styles))
        return "\\" + self.styles[key]

    def _family_macro(self, family, system):
        if system and self.system_font_ok(system):
            if system not in self.system_fonts:
                self.system_fonts[system] = "FSys" + _letters(len(self.system_fonts))
            return "\\" + self.system_fonts[system]
        return "\\" + _MACRO[family]

    def _style_definitions(self):
        out = []
        for (family, size, bold, italic, color, system), name in self.styles.items():
            file = optical_font(family, size, bold, italic)
            if file:   # the right design size, already in the right style
                if file not in self.optical:
                    self.optical[file] = "FLm" + _letters(len(self.optical))
                parts = ["\\" + self.optical[file]]
            else:
                parts = [self._family_macro(family, system)]
            parts.append(rf"\fontsize{{{_num(size)}}}{{{_num(size * 1.2)}}}\selectfont")
            if bold and family != ex.MATH and not file:
                parts.append(r"\bfseries")
            if italic and family != ex.MATH and not file:
                parts.append(r"\itshape")
            if color != (0, 0, 0):
                parts.append(rf"\color[RGB]{{{color[0]},{color[1]},{color[2]}}}")
            out.append(rf"\newcommand\{name}{{{''.join(parts)}}}")
        return out

    # ---- text
    def _body(self, seg):
        text = texutil.escape(seg.text)
        return rf"{self._style_macro(seg)} {text}" if text.strip() else ""

    def segment(self, seg, page, images):
        if seg.picture:
            return self._picture(seg, page, images)
        body = self._body(seg)
        if not body:
            return ""
        if ex.justified(seg) and seg.width > seg.size:
            return rf"\PTW{{{_num(seg.x)}}}{{{self._y(seg.y)}}}{{{_num(seg.width)}}}{{{body}}}%"
        return rf"\PT{{{_num(seg.x)}}}{{{self._y(seg.y)}}}{{{body}}}%"

    def _y(self, top_y):
        """LaTeX's picture coordinates run up from the bottom of the page."""
        return _num(self.height - top_y)

    def _picture(self, seg, page, images):
        """A glyph that can't be retyped (a display integral, an odd symbol
        font): cut from a copy of the page that holds only such glyphs."""
        source = self._glyphs[1] if self._glyphs else page
        rect = ex.picture_rect(seg, page.rect)
        if rect.is_empty or rect.width < 0.5 or rect.height < 0.5:
            return ""
        self.pictures += 1
        name = f"p{page.number + 1}_glyph{self.pictures}.png"
        pix = source.get_pixmap(dpi=400, clip=rect * ~page.rotation_matrix, alpha=True)
        pix.save(str(images / name))
        return (rf"\PG{{{_num(rect.x0)}}}{{{self._y(rect.y1)}}}{{{_num(rect.width)}}}{{{_num(rect.height)}}}"
                rf"{{images/{name}}}%")

    def page(self, page, layout, background_page, images):
        """The \\PDFPage{...} block for one page."""
        lines = [rf"% ---- page {page.number + 1}"]
        if abs(layout.width - self.width) > 0.5 or abs(layout.height - self.height) > 0.5:
            lines.append(rf"\PDFSize{{{_num(layout.width)}}}{{{_num(layout.height)}}}")
            self.width, self.height = layout.width, layout.height
        lines.append(rf"\PDFPage{{{background_page or 0}}}{{%")
        self._glyphs = ex.glyph_page(page, layout)
        for line in layout.lines:
            if line.angle:   # turned text (a sideways label): typed whole, turned about its start
                body = "".join(f"{{{self._body(s)}}}" for s in line.segments if not s.picture and self._body(s))
                if body:
                    x, y = line.origin
                    lines.append(rf"\PR{{{_num(x)}}}{{{self._y(y)}}}{{{_num(line.angle)}}}{{{body}}}%")
                continue
            lines.extend(p for p in (self.segment(s, page, images) for s in line.segments) if p)
        for rect, uri in layout.links:
            target = uri.replace("\\", "/").replace("%", r"\%").replace("#", r"\#")
            lines.append(rf"\PL{{{_num(rect.x0)}}}{{{self._y(rect.y1)}}}{{{_num(rect.width)}}}{{{_num(rect.height)}}}"
                         rf"{{{target}}}%")
        lines.append("}")
        if self._glyphs:
            self._glyphs[0].close()
            self._glyphs = None
        return "\n".join(lines)

    # ---- the document
    def document(self, source_name, first_w, first_h, page_blocks, background_file):
        head = [
            f"% Converted from {source_name} by AUPedean Annotator, keeping the exact layout.",
            "% Compile with XeLaTeX (on Overleaf: Menu > Compiler > XeLaTeX).",
            "% Each page is \\PDFPage{background page}{text}: the background holds the page's lines,",
            "% tables and pictures; \\PT{x}{y}{text} puts text with its baseline at (x, y) points from",
            "% the page's lower-left corner, and \\PTW{x}{y}{width}{text} also spreads it to that width.",
            "% Edit the text in place.",
            r"\documentclass{article}",
            rf"\usepackage[paperwidth={_num(first_w)}bp,paperheight={_num(first_h)}bp,margin=0pt]{{geometry}}",
            r"\usepackage{fontspec}",
            r"\usepackage{graphicx}",
            r"\usepackage{xcolor}",
            r"\usepackage{eso-pic}",
            r"\usepackage[hidelinks]{hyperref}",
            r"\pagestyle{empty}",
            r"\hbadness=10000 \hfuzz=\maxdimen \vbadness=10000 \vfuzz=\maxdimen",
            r"\setlength{\unitlength}{1bp}",
        ]
        for family, (font, options) in _FONTS.items():
            head.append(rf"\newfontfamily\{_MACRO[family]}{{{font}}}[{options}]")
        for name, macro in self.system_fonts.items():
            head.append(rf"\newfontfamily\{macro}{{{name}}}")
        styles = self._style_definitions()
        for file, macro in self.optical.items():
            head.append(rf"\newfontfamily\{macro}{{{file}}}")
        head.extend(styles)
        head.extend([
            r"\newcommand\PDFput[3]{\put(#1,#2){#3}}",
            r"\newcommand\PT[3]{\PDFput{#1}{#2}{\mbox{#3}}}",
            r"\newcommand\PTW[4]{\PDFput{#1}{#2}{\makebox[#3bp][s]{#4}}}",
            r"\newcommand\PG[5]{\PDFput{#1}{#2}{\includegraphics[width=#3bp,height=#4bp]{#5}}}",
            r"\newcommand\PL[5]{\PDFput{#1}{#2}{\href{#5}{\rule{0pt}{#4bp}\hspace{#3bp}}}}",
            r"\newcommand\PR[4]{\PDFput{#1}{#2}{\rotatebox{#3}{\mbox{#4}}}}",
            r"\newcommand\PDFSize[2]{\setlength\paperwidth{#1bp}\setlength\paperheight{#2bp}"
            r"\setlength\pdfpagewidth{#1bp}\setlength\pdfpageheight{#2bp}}",
            r"\newcommand\PDFPage[2]{%",
            r"  \ifnum#1>0 \AddToShipoutPictureBG*{\put(0,0){\includegraphics[page=#1,"
            r"width=\paperwidth,height=\paperheight]{" + (background_file or "") + r"}}}\fi",
            r"  \AddToShipoutPictureFG*{\setlength\unitlength{1bp}#2}\null\clearpage}",
            "",
            r"\begin{document}",
            "",
        ])
        return "\n".join(head) + "\n" + "\n\n".join(page_blocks) + "\n\n\\end{document}\n"
