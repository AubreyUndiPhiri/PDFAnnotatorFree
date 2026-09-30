"""Render LaTeX maths to a picture for the Formula tool.

Two engines:
- LaTeX (MiKTeX / TeX Live, found by latex.engines): full LaTeX with
  amsmath / amssymb, so cases, matrices, align and line breaks work.
- matplotlib's mathtext, bundled with the app: no TeX needed, covers the
  common maths (fractions, roots, sums, integrals, Greek, accents...) but not
  environments or line breaks.

render() returns a transparent PNG and its size in PDF points at the chosen
font size. Results are cached, so the live preview and the final placement
don't render twice. Safe to call from a worker thread.
"""
import hashlib
import io
import os
import re
import shutil
import subprocess
import tempfile
import threading
from collections import OrderedDict
from dataclasses import dataclass

ENGINE_LATEX = "LaTeX"
ENGINE_MATHTEXT = "matplotlib"
PNG_DPI = 600          # picture resolution at 100 % zoom (crisp when zoomed in and printed)
TEX_BASE_PT = 10       # standalone's font size; the picture is scaled to the tool's size
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Environments that are a whole display on their own; anything else (cases,
# pmatrix, array...) goes inside maths
_DISPLAY_ENV = re.compile(r"^\\begin\s*\{(?:align|gather|equation|multline|flalign|alignat|eqnarray)\*?\}")
_INNER_ENV = re.compile(r"\\begin\s*\{([^}]+)\}.*?\\end\s*\{\1\}", re.S)
_LINE_BREAK = "\\\\"


class FormulaError(Exception):
    pass


@dataclass(frozen=True)
class Rendered:
    png: bytes
    width_pt: float
    height_pt: float
    engine: str


_cache = OrderedDict()
_lock = threading.Lock()
_tex_dir = None
_tex_program = None
_mathtext_lock = threading.Lock()


def strip_delimiters(source: str) -> str:
    """'$x^2$', '\\[x^2\\]', '\\(x^2\\)' and '$$x^2$$' -> 'x^2'."""
    s = source.strip()
    for start, end in (("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)"), ("$", "$")):
        if s.startswith(start) and s.endswith(end) and len(s) > len(start) + len(end):
            return s[len(start):len(s) - len(end)].strip()
    return s


def tex_program():
    """pdfLaTeX (fastest), else XeLaTeX / LuaLaTeX; None without a TeX install."""
    global _tex_program
    if _tex_program is None:
        from .latex import engines

        _tex_program = next((p for p in (engines.find_program(n) for n in ("pdflatex", "xelatex", "lualatex")) if p),
                            "")
    return _tex_program or None


def available_engine():
    if tex_program():
        return ENGINE_LATEX
    import importlib.util

    return ENGINE_MATHTEXT if importlib.util.find_spec("matplotlib") else None


def _top_level(s: str) -> str:
    """`s` without its inner environments, to see what is at the top level."""
    for _ in range(10):
        s, n = _INNER_ENV.subn("", s)
        if not n:
            break
    return s


def tex_body(source: str) -> str:
    """The maths as it goes inside the standalone document."""
    s = strip_delimiters(source)
    if _DISPLAY_ENV.match(s):
        return s  # the user wrote a display environment themselves
    top = _top_level(s)
    if _LINE_BREAK in top:  # several lines: stack them, aligned at & if there are any
        env = "align*" if "&" in top else "gather*"
        return f"\\begin{{{env}}}\n{s}\n\\end{{{env}}}"
    return f"$\\displaystyle {s}$"


def render(source: str, fontsize: float, color=(0, 0, 0), engine=None) -> Rendered:
    """Render `source` (LaTeX maths, with or without $ delimiters) at
    `fontsize` points in `color` (0-255 RGB). Raises FormulaError."""
    source = source.strip()
    if not source:
        raise FormulaError("Type a formula, for example \\frac{a}{b} or E = mc^2.")
    engine = engine or available_engine()
    if engine is None:
        raise FormulaError("Formulas need matplotlib or a LaTeX installation (MiKTeX or TeX Live).")
    color = tuple(int(c) for c in color[:3])
    key = (source, round(float(fontsize), 2), color, engine)
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    if engine == ENGINE_LATEX:
        result = _render_tex(source, fontsize, color)
    else:
        result = _render_mathtext(source, fontsize, color)
    with _lock:
        _cache[key] = result
        while len(_cache) > 64:
            _cache.popitem(last=False)
    return result


# --------------------------------------------------------------------------
# LaTeX
# --------------------------------------------------------------------------

def _tex_folder():
    global _tex_dir
    if _tex_dir is None or not os.path.isdir(_tex_dir):
        _tex_dir = tempfile.mkdtemp(prefix="aupedean-formula-")
    return _tex_dir


def _render_tex(source, fontsize, color):
    import pymupdf as fitz

    program = tex_program()
    if not program:
        raise FormulaError("No LaTeX installation was found.")
    r, g, b = color
    tex = "\n".join([
        r"\documentclass[border=1pt,varwidth]{standalone}",
        r"\usepackage{amsmath,amssymb}",
        r"\usepackage{xcolor}",
        r"\begin{document}",
        rf"\color[RGB]{{{r},{g},{b}}}",
        tex_body(source),
        r"\end{document}",
        "",
    ])
    job = "f" + hashlib.sha1(tex.encode("utf-8")).hexdigest()[:16]
    folder = _tex_folder()
    with open(os.path.join(folder, job + ".tex"), "w", encoding="utf-8") as f:
        f.write(tex)
    try:
        subprocess.run([program, "-interaction=nonstopmode", "-halt-on-error", job + ".tex"], cwd=folder,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                       timeout=90, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired as e:
        raise FormulaError("LaTeX took too long (MiKTeX may be installing a package the first time). "
                           "Try again in a moment.") from e
    except OSError as e:
        raise FormulaError(f"LaTeX could not be started: {e}") from e
    pdf = os.path.join(folder, job + ".pdf")
    try:
        if not os.path.isfile(pdf):
            raise FormulaError(_tex_error(os.path.join(folder, job + ".log")))
        doc = fitz.open(pdf)
        page = doc[0]
        # Display maths is set on a full-width line; keep just the ink
        ink = None
        for _kind, rect in page.get_bboxlog():
            box = fitz.Rect(rect)
            if not box.is_empty:
                ink = box if ink is None else ink | box
        clip = (ink + (-1, -1, 1, 1)) & page.rect if ink is not None else page.rect
        scale = fontsize / TEX_BASE_PT
        zoom = scale * PNG_DPI / 72
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=True)
        result = Rendered(pix.tobytes("png"), clip.width * scale, clip.height * scale, ENGINE_LATEX)
        doc.close()
        return result
    finally:
        for ext in (".tex", ".pdf", ".log", ".aux"):
            try:
                os.remove(os.path.join(folder, job + ext))
            except OSError:
                pass


def _tex_error(log_path):
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return "LaTeX could not render this formula."
    for i, line in enumerate(lines):
        if line.startswith("! "):
            message = line[2:].strip()
            context = next((ln for ln in lines[i + 1:i + 6] if ln.startswith("l.")), "")
            detail = re.sub(r"^l\.\d+\s*", "", context).strip()
            return f"{message}  (near: {detail})" if detail else message
    return "LaTeX could not render this formula."


# --------------------------------------------------------------------------
# matplotlib mathtext
# --------------------------------------------------------------------------

def _render_mathtext(source, fontsize, color):
    s = strip_delimiters(source)
    if re.search(r"\\begin\s*\{", s) or _LINE_BREAK in s:
        raise FormulaError("Environments (cases, matrices, align) and line breaks need LaTeX: install MiKTeX "
                           "(miktex.org). Without it, write one line of maths.")
    try:
        from matplotlib import mathtext
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
        from matplotlib.font_manager import FontProperties
    except ImportError as e:
        raise FormulaError("Formulas need matplotlib or a LaTeX installation.") from e
    text = f"${s}$"
    prop = FontProperties(size=fontsize)
    buf = io.BytesIO()
    with _mathtext_lock:
        try:
            # what matplotlib.mathtext.math_to_image does, with a transparent background
            width, height, depth, _, _ = mathtext.MathTextParser("path").parse(text, dpi=72, prop=prop)
            fig = Figure(figsize=(width / 72, height / 72))
            FigureCanvasAgg(fig)
            fig.text(0, depth / height, text, fontproperties=prop, color="#{:02x}{:02x}{:02x}".format(*color))
            fig.savefig(buf, dpi=PNG_DPI, format="png", transparent=True)
        except Exception as e:  # noqa: BLE001 - matplotlib raises ValueError with a parser message
            message = [ln for ln in str(e).strip().splitlines() if ln.strip()]
            raise FormulaError(message[-1] if message else "This formula could not be read.") from e
    return Rendered(buf.getvalue(), width, height, ENGINE_MATHTEXT)


def cleanup():
    """Remove the temporary LaTeX folder (called when the app exits)."""
    if _tex_dir and os.path.isdir(_tex_dir):
        shutil.rmtree(_tex_dir, ignore_errors=True)
