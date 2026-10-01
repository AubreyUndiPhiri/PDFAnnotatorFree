"""Convert a PDF to an editable LaTeX project (main.tex + images/).

Two layouts:

- Exact (the default): every page looks the way it did. The page's lines,
  tables, plots and pictures are kept as a vector background (the page with
  its text taken out, in background.pdf) and every line of text is typed
  over it at its original position, in a matching font (TeX Gyre for
  Helvetica / Times / Courier, Latin Modern and Latin Modern Math for TeX
  documents), size and colour, spread to its original width. See
  exact_tex.py.
- Flowing: the text is rebuilt as headings and paragraphs (below), easier to
  rewrite but only roughly like the original.

- Typed pages (with a text layer) are rebuilt from PyMuPDF's text: headings
  by font size, paragraphs with bold / italic / monospace / colour, tables
  (page.find_tables) and the page's images.
- Scanned pages are read with the same OCR as the Word export (Tesseract,
  GOT-OCR 2.0 or TrOCR, see docx_export.PdfToDocx). GOT-OCR already writes
  LaTeX, so its output is kept as LaTeX wherever it is safe to compile.
- Any page that still can't be converted is kept as a picture of the page.

The project compiles with XeLaTeX (fontspec), e.g. Overleaf with the
compiler set to XeLaTeX, or MiKTeX / TeX Live locally.

No Qt here: the UI runs PdfToLatex.run() in a worker thread.
"""
import re
from collections import Counter
from pathlib import Path

import pymupdf as fitz

from ..docx_export import (
    OCR_AUTO, OCR_GOT, PICTURE_DPI, ConversionError, PdfToDocx, clean_latex, is_scanned, trocr_blocks,
)
from . import texutil

DEFAULT_MARGIN_PT = 54
LAYOUT_EXACT, LAYOUT_FLOW = "exact", "flow"
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
# Span flags from PyMuPDF
_SUPERSCRIPT, _ITALIC, _MONO, _BOLD = 1, 2, 8, 16
# Standard PDF font names -> the TeX Gyre look-alike texutil knows
_BASE_FONTS = (("helvetica", "Helvetica"), ("arial", "Helvetica"), ("times", "Times"), ("courier", "Courier"))


class PdfToLatex(PdfToDocx):
    """Convert a PDF (a path, bytes or an open fitz.Document) to a LaTeX
    project folder.

        PdfToLatex(src, pages=None, ocr=OCR_AUTO).run("out_folder")

    Same options, progress and cancel() as PdfToDocx."""

    def __init__(self, source, pages=None, password=None, ocr=OCR_AUTO, include_annotations=True,
                 keep_scan_images=False, progress=None, layout=LAYOUT_EXACT):
        super().__init__(source, pages=pages, password=password, ocr=ocr, include_annotations=include_annotations,
                         keep_scan_images=keep_scan_images, progress=progress)
        self.tex_path = None
        self.layout = layout

    # ------------------------------------------------------------ run
    def run(self, out_dir):
        self.progress(0.0, "Opening the PDF...")
        doc = self._open_source()
        indexes = list(range(doc.page_count)) if self.pages is None else [i for i in self.pages
                                                                          if 0 <= i < doc.page_count]
        if not indexes:
            raise ConversionError("No pages to convert.")
        self.report.pages = len(indexes)
        project = Path(out_dir)
        images = project / "images"
        try:
            images.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ConversionError(f"Could not create the folder {project}: {e}") from e
        scanned = {i for i in indexes if is_scanned(doc[i])}
        typed = [i for i in indexes if i not in scanned]
        if self.layout == LAYOUT_EXACT:
            return self._run_exact(doc, indexes, scanned, project, images)

        self.progress(0.02, "Reading the text...")
        texts = {i: doc[i].get_text("dict", sort=True) for i in typed}
        body_size, main_font = _body_style(texts.values())
        bodies = {}
        for n, i in enumerate(typed):
            self._check_cancel()
            self.progress(0.05 + 0.55 * n / len(typed), f"Converting page {i + 1} ({n + 1} of {len(typed)})...")
            try:
                bodies[i] = self._typed_page(doc[i], texts[i], body_size, images)
            except Exception as e:  # noqa: BLE001 - that page becomes a picture
                self.report.warnings.append(f"Page {i + 1} could not be converted to text ({e}).")
        ocr_results = self._ocr_scanned(doc, [i for i in indexes if i in scanned]) if scanned else {}

        self.progress(0.95, "Writing main.tex...")
        pages = []
        for i in indexes:
            self._check_cancel()
            if bodies.get(i):
                pages.append(bodies[i])
                self.report.converted.append(i + 1)
            elif i in ocr_results:
                body = ocr_results[i]
                if self.keep_scan_images:
                    body += "\n\n" + self._picture(doc[i], images)
                pages.append(body)
                self.report.ocr.append(i + 1)
            else:
                pages.append(self._picture(doc[i], images))
                self.report.pictures.append(i + 1)

        first = doc[indexes[0]]
        margins = _margins(first, texts.values())
        head = texutil.preamble(first.rect.width, first.rect.height, margins, main_font, body_size, project)
        source_name = Path(self.source).name if isinstance(self.source, (str, Path)) else "a PDF"
        tex = "\n".join([
            f"% Converted from {source_name} by Aupedean Annotator.",
            "% Compile with XeLaTeX (on Overleaf: Menu > Compiler > XeLaTeX).",
            head,
            "",
            r"\begin{document}",
            "",
            "\n\n\\clearpage\n\n".join(pages),
            "",
            r"\end{document}",
            "",
        ])
        self.tex_path = project / "main.tex"
        try:
            self.tex_path.write_text(tex, encoding="utf-8")
        except OSError as e:
            raise ConversionError(f"Could not write {self.tex_path}. If it is open in another program, "
                                  "close it and try again.") from e
        self.progress(1.0, "Done")
        return self.report

    # ------------------------------------------------------------ exact layout
    def _run_exact(self, doc, indexes, scanned, project, images):
        from .. import exact_layout
        from .exact_tex import ExactTex

        writer = ExactTex(system_font_ok=_installed_font)
        layouts, need_background = {}, []
        for n, i in enumerate(indexes):
            self._check_cancel()
            self.progress(0.03 + 0.6 * n / len(indexes), f"Reading page {i + 1} ({n + 1} of {len(indexes)})...")
            try:
                layouts[i] = exact_layout.read_page(doc[i])
            except Exception as e:  # noqa: BLE001 - that page is kept whole as its background
                self.report.warnings.append(f"The text of page {i + 1} could not be read ({e}).")
                layouts[i] = exact_layout.PageLayout(doc[i].rect.width, doc[i].rect.height, has_background=True,
                                                     invisible_text=True)
            if layouts[i].has_background or i in scanned or layouts[i].invisible_text:
                need_background.append(i)
        background_page = {}
        if need_background:
            self.progress(0.65, "Keeping the lines, tables and pictures...")
            bg = exact_layout.text_free_copy(doc, need_background)
            for n, i in enumerate(need_background):
                if i in scanned or layouts[i].invisible_text:   # a scan: the whole page, as it is
                    bg.delete_page(n)
                    bg.insert_pdf(doc, from_page=i, to_page=i, start_at=n)
                background_page[i] = n + 1
            try:
                bg.save(str(project / "background.pdf"), garbage=4, deflate=True)
            except Exception as e:  # noqa: BLE001
                raise ConversionError(f"Could not write {project / 'background.pdf'} ({e}).") from e
        blocks = []
        first = doc[indexes[0]]
        writer.width, writer.height = first.rect.width, first.rect.height
        for n, i in enumerate(indexes):
            self._check_cancel()
            self.progress(0.7 + 0.25 * n / len(indexes), f"Writing page {i + 1}...")
            blocks.append(writer.page(doc[i], layouts[i], background_page.get(i), images))
            if i in scanned or layouts[i].invisible_text:
                self.report.pictures.append(i + 1)
            else:
                self.report.converted.append(i + 1)
        source_name = Path(self.source).name if isinstance(self.source, (str, Path)) else "a PDF"
        tex = writer.document(source_name, first.rect.width, first.rect.height, blocks,
                              "background.pdf" if need_background else "")
        self.tex_path = project / "main.tex"
        try:
            self.tex_path.write_text(tex, encoding="utf-8")
        except OSError as e:
            raise ConversionError(f"Could not write {self.tex_path}. If it is open in another program, "
                                  "close it and try again.") from e
        self.progress(1.0, "Done")
        return self.report

    # ------------------------------------------------------------ scanned pages
    def _ocr_scanned(self, doc, scanned) -> dict:
        results = super()._ocr_scanned(doc, scanned)
        # Tesseract gives blocks; the models' text was already made LaTeX in _model_result
        return {i: r if isinstance(r, str) else blocks_latex(r) for i, r in results.items()}

    def _model_result(self, engine, text):
        return got_latex(text) if engine == OCR_GOT else blocks_latex(trocr_blocks(text))

    def _picture(self, page, images) -> str:
        name = f"page{page.number + 1}.png"
        page.get_pixmap(dpi=PICTURE_DPI).save(str(images / name))
        return (r"\begin{center}" "\n"
                rf"\includegraphics[width=\linewidth,height=0.95\textheight,keepaspectratio]{{images/{name}}}" "\n"
                r"\end{center}")

    # ------------------------------------------------------------ typed pages
    def _typed_page(self, page, text, body_size, images) -> str:
        """LaTeX for one page with a text layer ('' if nothing was found)."""
        items = []  # (y, x, latex), laid out top to bottom
        tables = []
        try:
            tables = page.find_tables().tables
        except Exception:  # noqa: BLE001 - no tables then
            pass
        table_rects = [fitz.Rect(t.bbox) for t in tables]
        for t in tables:
            rows = [[cell or "" for cell in row] for row in t.extract()]
            if rows:
                items.append((t.bbox[1], t.bbox[0], table_latex(rows)))
        text_width = max(1.0, page.rect.width - 2 * DEFAULT_MARGIN_PT)
        n_images = 0
        for block in text["blocks"]:
            rect = fitz.Rect(block["bbox"])
            if any(abs(rect & r) > 0.5 * abs(rect) for r in table_rects):
                continue  # already written as part of a table
            if block.get("type") == 1:
                n_images += 1
                latex = self._image(block, rect, page.number, n_images, images, text_width)
            else:
                latex = text_block_latex(block, body_size)
            if latex:
                items.append((rect.y0, rect.x0, latex))
        items.sort(key=lambda item: (round(item[0]), item[1]))
        return "\n\n".join(latex for _, _, latex in items)

    def _image(self, block, rect, page_number, n, images, text_width) -> str:
        if rect.width < 8 or rect.height < 8 or not block.get("image"):
            return ""
        ext = (block.get("ext") or "png").lower()
        name = f"p{page_number + 1}_img{n}"
        try:
            if ext in ("png", "jpg", "jpeg"):
                (images / f"{name}.{ext}").write_bytes(block["image"])
            else:  # jpx, jbig2, tiff... -> png, which every TeX engine reads
                pix = fitz.Pixmap(block["image"])
                if pix.n - pix.alpha >= 4:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                ext = "png"
                pix.save(str(images / f"{name}.png"))
        except Exception as e:  # noqa: BLE001 - leave that image out
            self.report.warnings.append(f"An image on page {page_number + 1} could not be saved ({e}).")
            return ""
        width = min(1.0, rect.width / text_width)
        return (r"\begin{center}" "\n"
                rf"\includegraphics[width={width:.2f}\linewidth]{{images/{name}.{ext}}}" "\n"
                r"\end{center}")


def _installed_font(name):
    """True when Windows has the font `name` installed (XeLaTeX finds it by name)."""
    import os

    from .. import fonts

    files = fonts.family_files(name) if name else None
    if not files:
        return False
    roots = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts").lower(),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts").lower()]
    return any(str(files["regular"]).lower().startswith(r) for r in roots)


# --------------------------------------------------------------------------
# Typed text -> LaTeX
# --------------------------------------------------------------------------

def _clean(text: str) -> str:
    return _CONTROL.sub("", text)


def _spans(block):
    for line in block.get("lines", []):
        for span in line["spans"]:
            if span["text"].strip():
                yield span


def _body_style(texts):
    """(body font size, main font for texutil or None) from the typed pages:
    the size and font most of the characters use."""
    sizes, font_chars = Counter(), Counter()
    for text in texts:
        for block in text["blocks"]:
            for span in _spans(block):
                sizes[round(span["size"], 1)] += len(span["text"])
                font_chars[span["font"]] += len(span["text"])
    body = sizes.most_common(1)[0][0] if sizes else 11
    main = None
    if font_chars:
        name = font_chars.most_common(1)[0][0].lower()
        main = next((base for key, base in _BASE_FONTS if key in name), None)
    return body, main


def _margins(page, texts):
    """(left, top, right, bottom): the smallest distance from the text to
    each pair of edges over all typed pages, used on both sides (a half-empty
    page would otherwise give a huge right or bottom margin)."""
    rects = [fitz.Rect(b["bbox"]) for text in texts for b in text["blocks"] if b.get("type") == 0]
    if not rects:
        return (DEFAULT_MARGIN_PT,) * 4
    box = fitz.Rect(rects[0])
    for r in rects[1:]:
        box |= r
    side = min(box.x0, page.rect.width - box.x1)
    end = min(box.y0, page.rect.height - box.y1)
    side, end = (max(18.0, min(108.0, m)) for m in (side, end))
    return side, end, side, end


def _style(span):
    flags, font = span["flags"], span["font"].lower()
    color = span.get("color", 0)
    rgb = ((color >> 16) & 255, (color >> 8) & 255, color & 255)
    if max(rgb) < 48:
        rgb = (0, 0, 0)  # near-black text is just black
    return (bool(flags & _BOLD or "bold" in font or "black" in font),
            bool(flags & _ITALIC or "italic" in font or "oblique" in font),
            bool(flags & _MONO or "mono" in font or "courier" in font),
            bool(flags & _SUPERSCRIPT),
            rgb)


def text_block_latex(block, body_size) -> str:
    """One PyMuPDF text block -> a \\section* heading or a styled paragraph."""
    runs = []  # [text, style], lines joined, same-style neighbours merged
    sizes = []
    for n, line in enumerate(block.get("lines", [])):
        parts = [(_clean(s["text"]), _style(s)) for s in line["spans"] if s["text"]]
        sizes.extend(s["size"] for s in line["spans"] if s["text"].strip())
        if not any(t.strip() for t, _ in parts):
            continue
        if runs and n:
            prev = runs[-1][0].rstrip()
            if prev.endswith("-") and prev[-2:-1].isalpha():
                runs[-1][0] = prev[:-1]  # a word hyphenated across lines
            else:
                runs[-1][0] = prev + " "
        for text, style in parts:
            if runs and runs[-1][1] == style:
                runs[-1][0] += text
            else:
                runs.append([text, style])
    if not runs:
        return ""
    plain = "".join(t for t, _ in runs).strip()
    if not plain:
        return ""
    size = max(sizes) if sizes else body_size
    lines = len(block.get("lines", []))
    if size >= body_size * 1.3 and len(plain) < 150 and lines <= 3:
        command = "section" if size >= body_size * 1.8 else "subsection"
        return rf"\{command}*{{{texutil.escape(plain)}}}"
    out = "".join(texutil.styled(t, bold=b, italic=i, mono=m, superscript=s, color=c)
                  for t, (b, i, m, s, c) in runs)
    if size <= body_size * 0.85:
        out = rf"{{\small {out}}}"
    return out.strip()


def table_latex(rows) -> str:
    cols = max(len(r) for r in rows)
    spec = "|" + "X|" * cols
    lines = [r"\noindent\begin{tabularx}{\linewidth}{" + spec + "}", r"\hline"]
    for row in rows:
        cells = [texutil.escape(_clean(c).replace("\n", " ").strip()) for c in row] + [""] * (cols - len(row))
        lines.append(" & ".join(cells) + r" \\ \hline")
    lines.append(r"\end{tabularx}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# OCR output -> LaTeX
# --------------------------------------------------------------------------

def blocks_latex(blocks) -> str:
    """docx_export blocks (heading / para / table) -> LaTeX."""
    out = []
    for block in blocks:
        if block[0] == "heading":
            command = {0: "section", 1: "section", 2: "subsection"}.get(block[1], "subsubsection")
            out.append(rf"\{command}*{{{texutil.escape(_clean(block[2]))}}}")
        elif block[0] == "table":
            if block[1]:
                out.append(table_latex(block[1]))
        else:
            out.append(texutil.escape(_clean(block[1])))
    return "\n\n".join(out)


_TABULAR_ENV = re.compile(r"\\begin\{tabular\}.*?\\end\{tabular\}", re.S)
_MATH = re.compile(r"\\\(.*?\\\)|\\\[.*?\\\]|\$\$.*?\$\$|\$[^$]*\$", re.S)
_SPECIAL_OUTSIDE_MATH = re.compile(r"(?<!\\)[_^#&]")


def _braces_balanced(text: str) -> bool:
    depth = 0
    for m in re.finditer(r"\\.|[{}]", text, re.S):
        tok = m.group()
        if tok == "{":
            depth += 1
        elif tok == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def safe_latex(chunk: str, table=False) -> bool:
    """True when GOT-OCR's LaTeX `chunk` should compile as it is: balanced
    braces, math delimiters and environments, and no stray special
    characters outside math (& is allowed inside a tabular)."""
    if not _braces_balanced(chunk):
        return False
    if chunk.count(r"\(") != chunk.count(r"\)") or chunk.count(r"\[") != chunk.count(r"\]"):
        return False
    if len(re.findall(r"(?<!\\)\$", chunk)) % 2:
        return False
    if sorted(re.findall(r"\\begin\{(\w+\*?)\}", chunk)) != sorted(re.findall(r"\\end\{(\w+\*?)\}", chunk)):
        return False
    outside = _MATH.sub("", chunk)
    if table:
        outside = outside.replace("&", "")
    return not _SPECIAL_OUTSIDE_MATH.search(outside)


def got_latex(text: str) -> str:
    """GOT-OCR 2.0's formatted output (light LaTeX) -> LaTeX for main.tex.
    Anything that might not compile is written as escaped plain text."""
    text = _clean(text)
    text = re.sub(r"\\title\*?\{", r"\\section*{", text)
    text = re.sub(r"\\(section|subsection|subsubsection)\{", r"\\\1*{", text)
    text = re.sub(r"\\(?:author|date)\{[^{}]*\}|\\maketitle", "", text)
    chunks, pos = [], 0
    for m in _TABULAR_ENV.finditer(text):
        chunks.extend((line, False) for line in text[pos:m.start()].splitlines())
        chunks.append((m.group(), True))
        pos = m.end()
    chunks.extend((line, False) for line in text[pos:].splitlines())
    out = []
    for chunk, is_table in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        if safe_latex(chunk, table=is_table):
            out.append(r"\begin{center}" "\n" + chunk + "\n" r"\end{center}" if is_table else chunk)
        elif is_table:
            rows = [[clean_latex(c) for c in re.split(r"(?<!\\)&", row)]
                    for row in re.split(r"\\\\", chunk)]
            rows = [r for r in rows if any(r)]
            if rows:
                out.append(table_latex(rows))
        else:
            line = _mixed_line(chunk)
            if line:
                out.append(line)
    return "\n\n".join(out)


def _mixed_line(line: str) -> str:
    """A line of text with inline math: formulas are kept as LaTeX when they
    are well formed, the text around them is kept or escaped piece by piece."""
    out, pos = [], 0
    for m in _MATH.finditer(line):
        out.append(_text_piece(line[pos:m.start()]))
        math = m.group()
        out.append(math if _braces_balanced(math) else texutil.escape(clean_latex(math)))
        pos = m.end()
    out.append(_text_piece(line[pos:]))
    return "".join(out).strip()


def _text_piece(text: str) -> str:
    core = text.strip()
    if not core:
        return text and " "
    lead = " " if text[0].isspace() else ""
    trail = " " if text[-1].isspace() else ""
    if "\\" in core and safe_latex(core) and not re.search(r"\\[()\[\]]|(?<!\\)\$", core):
        return lead + core + trail  # e.g. \section*{...} or \textbf{...}
    return lead + texutil.escape(clean_latex(core) if "\\" in core else core) + trail


def convert(source, out_dir, **options):
    """One-call form of PdfToLatex(source, **options).run(out_dir)."""
    return PdfToLatex(source, **options).run(out_dir)
