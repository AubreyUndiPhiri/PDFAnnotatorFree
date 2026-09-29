"""Font registry: the PDF base-14 families plus any custom .ttf/.otf files
dropped into app/assets/fonts/ (e.g. AUPedean.ttf).

Base-14 fonts are written as editable FreeText annotations. Custom fonts are
embedded in the PDF and drawn into the page content, because FreeText
annotations cannot carry an arbitrary embedded font."""
from pathlib import Path

FONTS_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"

DEFAULT_FONT = "Helvetica"

# Display name -> PyMuPDF base-14 font code used by add_freetext_annot
BASE14_FONTS = {
    "Helvetica": "helv",
    "Times": "tiro",
    "Courier": "cour",
}

# Display family -> absolute font file path, filled by register_custom_fonts()
_custom_fonts: dict[str, str] = {}


def register_custom_fonts() -> dict[str, str]:
    """Load every font file in FONTS_DIR into Qt (for on-screen preview) and
    remember its path (for PDF embedding). Safe to call more than once; needs
    a QGuiApplication to exist."""
    from PySide6.QtGui import QFontDatabase

    if not FONTS_DIR.is_dir():
        return dict(_custom_fonts)
    for path in sorted(FONTS_DIR.iterdir()):
        if path.suffix.lower() not in (".ttf", ".otf"):
            continue
        if str(path) in _custom_fonts.values():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
        family = families[0] if families else path.stem
        _custom_fonts[family] = str(path)
    return dict(_custom_fonts)


def available_fonts() -> list[str]:
    return list(BASE14_FONTS) + [f for f in _custom_fonts if f not in BASE14_FONTS]


def custom_font_path(name: str) -> str | None:
    return _custom_fonts.get(name)


# On-screen stand-ins for the base-14 families (Windows lacks the PDF names)
_PREVIEW_FAMILIES = {
    "Helvetica": ["Helvetica", "Arial", "Segoe UI"],
    "Times": ["Times", "Times New Roman", "Georgia"],
    "Courier": ["Courier", "Courier New", "Consolas"],
}


def preview_families(name: str) -> list[str]:
    """Qt font family fallbacks that look like `name`, for editor previews."""
    return _PREVIEW_FAMILIES.get(name, [name])


def is_custom_font(name: str) -> bool:
    return name in _custom_fonts


def pdf_font_ref(name: str) -> str:
    """Resource name used for the embedded font inside a PDF page."""
    return "F-" + "".join(ch for ch in name if ch.isalnum())
