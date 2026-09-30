"""LaTeX writing helpers: escaping, inline styles and the fontspec preamble
that points XeLaTeX (Tectonic) at the app's font files."""
import re
import shutil
from pathlib import Path

from .. import fonts

_ESCAPES = {
    "\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$", "&": r"\&", "#": r"\#",
    "_": r"\_", "%": r"\%", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}
_ESCAPE_RE = re.compile(r"[\\{}$&#_%~^]")


def escape(text: str) -> str:
    """Plain text -> LaTeX-safe text (Unicode is fine under XeLaTeX)."""
    return _ESCAPE_RE.sub(lambda m: _ESCAPES[m.group()], text).replace("\u00a0", "~")


def styled(text: str, bold=False, italic=False, mono=False, superscript=False, color=None) -> str:
    """Escaped text wrapped in the inline commands for its style."""
    out = escape(text)
    if not out.strip():
        return out
    if mono:
        out = rf"\texttt{{{out}}}"
    if italic:
        out = rf"\textit{{{out}}}"
    if bold:
        out = rf"\textbf{{{out}}}"
    if superscript:
        out = rf"\textsuperscript{{{out}}}"
    if color and color != (0, 0, 0):
        r, g, b = color
        out = rf"\textcolor[RGB]{{{r},{g},{b}}}{{{out}}}"
    return out


# Standard PDF fonts -> their TeX Gyre look-alikes, which ship with Tectonic
_TEX_GYRE = {
    "Helvetica": ("texgyreheros", "Heros"),
    "Times": ("texgyretermes", "Termes"),
    "Courier": ("texgyrecursor", "Cursor"),
}


def _macro_name(font_name: str) -> str:
    letters = "".join(ch for ch in font_name.title() if ch.isalpha())
    return "font" + (letters or "Custom")


def font_command(kind: str, font_name: str, project_dir: Path, macro: str | None = None) -> str | None:
    """A fontspec line for `font_name`: kind is 'main' (\\setmainfont) or
    'family' (\\newfontfamily\\<macro>). Font files the app bundles are copied
    into <project>/fonts so the project is self-contained; installed Windows
    fonts are referenced where they are. Returns None if the font is unknown."""
    head = r"\setmainfont" if kind == "main" else rf"\newfontfamily\{macro or _macro_name(font_name)}"
    if font_name in _TEX_GYRE:
        base, _ = _TEX_GYRE[font_name]
        return (rf"{head}{{{base}}}[Extension=.otf, UprightFont=*-regular, BoldFont=*-bold, "
                r"ItalicFont=*-italic, BoldItalicFont=*-bolditalic]")
    files = fonts.family_files(font_name)
    if not files:
        return None
    regular = Path(files["regular"])
    bundled = not str(regular).lower().startswith(str(Path("C:/Windows")).lower())
    if bundled:
        target = project_dir / "fonts"
        target.mkdir(parents=True, exist_ok=True)
        for path in files.values():
            if not (target / Path(path).name).exists():
                shutil.copy2(path, target / Path(path).name)
        folder = "./fonts/"
    else:
        folder = regular.parent.as_posix() + "/"
    options = [f"Path={folder}"]
    for key, option in (("bold", "BoldFont"), ("italic", "ItalicFont"), ("bolditalic", "BoldItalicFont")):
        if key in files:
            options.append(f"{option}={Path(files[key]).name}")
    return rf"{head}{{{regular.name}}}[{', '.join(options)}]"


def preamble(page_w_pt, page_h_pt, margins_pt, main_font, font_size_pt, project_dir, families=()):
    """Document preamble. families: [(font_name, macro)] for extra fonts used
    inline with {\\macro ...}."""
    size = max(8, min(20, round(font_size_pt)))
    cls_size = "10pt" if size <= 10 else "11pt" if size == 11 else "12pt"
    left, top, right, bottom = (max(18, round(m)) for m in margins_pt)
    lines = [
        rf"\documentclass[{cls_size}]{{article}}",
        rf"\usepackage[paperwidth={page_w_pt:.0f}pt,paperheight={page_h_pt:.0f}pt,"
        rf"left={left}pt,right={right}pt,top={top}pt,bottom={bottom}pt]{{geometry}}",
        r"\usepackage{fontspec}",
        r"\usepackage{amsmath,amssymb}",
        r"\usepackage{graphicx}",
        r"\usepackage[table]{xcolor}",
        r"\usepackage{array,booktabs,longtable,tabularx}",
        r"\usepackage{enumitem}",
        r"\usepackage{float}",
        r"\usepackage[hidelinks]{hyperref}",
        r"\setlength{\parindent}{0pt}",
        r"\setlength{\parskip}{0.6em}",
    ]
    main = font_command("main", main_font, project_dir) if main_font else None
    if main:
        lines.append(main)
    if cls_size == "12pt" and size > 12:
        lines.append(rf"\AtBeginDocument{{\fontsize{{{size}}}{{{round(size * 1.2)}}}\selectfont}}")
    for name, macro in families:
        cmd = font_command("family", name, project_dir, macro)
        if cmd:
            lines.append(cmd)
    return "\n".join(lines)
