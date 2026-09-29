"""Font registry.

Three groups of fonts are offered everywhere a font can be chosen:

1. Handwriting / custom fonts: .ttf/.otf files in app/assets/fonts/ (shipped
   with the app) and in the user's fonts folder (USER_FONTS_DIR), where the
   in-app "Create Handwriting Font" command saves AUPedean.ttf.
2. The PDF base-14 families Helvetica, Times and Courier.
3. Fonts installed in Windows that allow embedding.

Base-14 text uses the viewer's built-in fonts. Every other font is embedded
in the PDF and used to draw the text box (see pdf_ops._apply_custom_appearance),
so the text looks the same in any PDF viewer."""
import json
import os
import sys
from pathlib import Path

FONTS_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
APP_DATA = Path(os.environ.get("APPDATA") or Path.home()) / "AupedianAnnotators"
USER_FONTS_DIR = APP_DATA / "fonts"
_SYSTEM_CACHE = APP_DATA / "system_fonts.json"
_CACHE_VERSION = 1

DEFAULT_FONT = "Helvetica"
HANDWRITING_FONT = "AUPedean"
MAX_SYSTEM_FONT_BYTES = 15_000_000  # skip huge CJK fonts: embedding them would bloat every PDF

# Display name -> PyMuPDF base-14 font code used by add_freetext_annot
BASE14_FONTS = {
    "Helvetica": "helv",
    "Times": "tiro",
    "Courier": "cour",
}

# On-screen stand-ins for the base-14 families (Windows lacks the PDF names)
_PREVIEW_FAMILIES = {
    "Helvetica": ["Helvetica", "Arial", "Segoe UI"],
    "Times": ["Times", "Times New Roman", "Georgia"],
    "Courier": ["Courier", "Courier New", "Consolas"],
}

# name -> {"path", "family", "bold", "italic"}
_custom_fonts: dict[str, dict] = {}
_system_fonts: dict[str, dict] = {}


# ---------------------------------------------------------------- loading

def register_custom_fonts() -> dict[str, str]:
    """Load handwriting/custom font files into Qt (for on-screen preview) and
    remember their paths (for PDF embedding). Safe to call again after a new
    font file was added. Needs a QGuiApplication."""
    from PySide6.QtGui import QFontDatabase

    known = {info["path"] for info in _custom_fonts.values()}
    for folder in (FONTS_DIR, USER_FONTS_DIR):
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in (".ttf", ".otf") or str(path) in known:
                continue
            _add_custom(path)
    return {name: info["path"] for name, info in _custom_fonts.items()}


def _add_custom(path: Path) -> str:
    from PySide6.QtGui import QFontDatabase

    font_id = QFontDatabase.addApplicationFont(str(path))
    families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
    family = families[0] if families else path.stem
    _custom_fonts[family] = {"path": str(path), "family": family, "bold": False, "italic": False, "id": font_id}
    return family


def install_font_file(path) -> str:
    """Register a newly created or replaced font file (e.g. a freshly built
    AUPedean.ttf) so it can be used immediately. Returns its family name."""
    from PySide6.QtGui import QFontDatabase

    path = Path(path)
    for name, info in list(_custom_fonts.items()):
        if Path(info["path"]) == path:
            if info.get("id", -1) >= 0:
                QFontDatabase.removeApplicationFont(info["id"])
            del _custom_fonts[name]
    from . import pdf_ops

    pdf_ops.forget_font_file(str(path))
    return _add_custom(path)


def load_system_fonts(folders=None):
    """Index installed fonts that permit embedding. Results are cached in
    APP_DATA and refreshed only when a font file's size or date changes."""
    if folders is None:
        folders = _system_font_folders()
    try:
        cache = json.loads(_SYSTEM_CACHE.read_text(encoding="utf-8"))
        if cache.get("version") != _CACHE_VERSION:
            cache = {}
    except (OSError, ValueError):
        cache = {}
    entries = cache.get("files", {})
    fresh = {}
    for folder in folders:
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if path.suffix.lower() not in (".ttf", ".otf", ".ttc"):
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            key = str(path)
            stamp = [st.st_size, int(st.st_mtime)]
            old = entries.get(key)
            if old and old.get("stamp") == stamp:
                fresh[key] = old
                continue
            fresh[key] = {"stamp": stamp, "info": _read_font_info(path, st.st_size)}
    try:
        APP_DATA.mkdir(parents=True, exist_ok=True)
        _SYSTEM_CACHE.write_text(json.dumps({"version": _CACHE_VERSION, "files": fresh}), encoding="utf-8")
    except OSError:
        pass

    _system_fonts.clear()
    for key, entry in sorted(fresh.items()):
        info = entry.get("info")
        if not info or info["name"] in BASE14_FONTS or info["name"] in _system_fonts:
            continue
        _system_fonts[info["name"]] = {"path": key, "family": info["family"],
                                       "bold": info["bold"], "italic": info["italic"]}


def _system_font_folders():
    if sys.platform != "win32":
        return [Path("/usr/share/fonts"), Path.home() / ".fonts"]
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    folders = [windir / "Fonts"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        folders.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    return folders


def _read_font_info(path, size):
    """Name and style of the first face in a font file, or None when it can't
    or mustn't be embedded (restricted licence, symbol font, too large)."""
    if size > MAX_SYSTEM_FONT_BYTES:
        return None
    try:
        from fontTools.ttLib import TTFont

        font = TTFont(str(path), lazy=True, fontNumber=0)
        os2 = font["OS/2"] if "OS/2" in font else None
        if os2 is not None:
            if os2.fsType & 0x0002:                                # restricted licence: no embedding
                return None
            if getattr(os2, "ulCodePageRange1", 0) & (1 << 31):    # symbol character set
                return None
        names = font["name"]
        full = names.getDebugName(4) or names.getDebugName(1)
        family = names.getDebugName(1) or full
        subfamily = (names.getDebugName(2) or "").lower()
        if not full:
            return None
        weight = os2.usWeightClass if os2 is not None else 400
        italic = bool(os2.fsSelection & 1) if os2 is not None else "italic" in subfamily
        return {"name": full, "family": family, "bold": "bold" in subfamily or weight >= 700, "italic": italic}
    except Exception:
        return None


# ---------------------------------------------------------------- queries

def handwriting_fonts() -> list[str]:
    return sorted(_custom_fonts)


def system_fonts() -> list[str]:
    return sorted(_system_fonts, key=str.casefold)


def available_fonts() -> list[str]:
    return handwriting_fonts() + list(BASE14_FONTS) + [
        f for f in system_fonts() if f not in _custom_fonts and f not in BASE14_FONTS
    ]


def has_handwriting_font() -> bool:
    return HANDWRITING_FONT in _custom_fonts


def _info(name):
    return _custom_fonts.get(name) or _system_fonts.get(name)


def custom_font_path(name: str) -> str | None:
    info = _info(name)
    return info["path"] if info else None


def is_custom_font(name: str) -> bool:
    """True for every font that is embedded (handwriting and system fonts)."""
    return _info(name) is not None


def preview_families(name: str) -> list[str]:
    """Qt font family fallbacks that look like `name`, for previews."""
    if name in _PREVIEW_FAMILIES:
        return _PREVIEW_FAMILIES[name]
    info = _info(name)
    return [info["family"]] if info else [name]


def preview_font(name: str, pixel_size: int | None = None):
    """QFont that shows `name` on screen (family, bold and italic)."""
    from PySide6.QtGui import QFont

    font = QFont()
    font.setFamilies(preview_families(name))
    info = _info(name)
    if info:
        font.setBold(info["bold"])
        font.setItalic(info["italic"])
    if pixel_size:
        font.setPixelSize(max(4, int(pixel_size)))
    return font


def pdf_font_ref(name: str) -> str:
    """Resource name used for the embedded font inside a PDF page."""
    return "F-" + "".join(ch for ch in name if ch.isalnum())


# ---------------------------------------------------------------- UI helper

CREATE_HANDWRITING = "__create_handwriting__"


def fill_font_combo(combo, current=None, offer_create=False):
    """Populate a QComboBox with every font, grouped and previewed in its own
    typeface, with type-to-search. With offer_create, a missing AUPedean is
    listed as an entry that starts the Create Handwriting Font steps."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QCompleter

    current = current or combo.currentText() or DEFAULT_FONT
    combo.blockSignals(True)
    combo.clear()

    def add(name):
        combo.addItem(name)
        combo.setItemData(combo.count() - 1, preview_font(name, 15), Qt.FontRole)

    if offer_create and not has_handwriting_font():
        from PySide6.QtGui import QColor

        from . import icons, theme

        combo.addItem(icons.icon("signature", theme.ACCENT), f"Create {HANDWRITING_FONT} from your handwriting...")
        combo.setItemData(combo.count() - 1, CREATE_HANDWRITING, Qt.UserRole)
        combo.setItemData(combo.count() - 1, QColor(theme.ACCENT), Qt.ForegroundRole)
    for name in handwriting_fonts():
        add(name)
    if combo.count():
        combo.insertSeparator(combo.count())
    for name in BASE14_FONTS:
        add(name)
    combo.insertSeparator(combo.count())
    for name in system_fonts():
        if name not in _custom_fonts and name not in BASE14_FONTS:
            add(name)

    combo.setEditable(True)
    combo.setInsertPolicy(combo.InsertPolicy.NoInsert)
    completer = QCompleter([n for n in available_fonts()], combo)
    completer.setCaseSensitivity(Qt.CaseInsensitive)
    completer.setFilterMode(Qt.MatchContains)
    combo.setCompleter(completer)
    combo.setMaxVisibleItems(18)
    combo.view().setMinimumWidth(320)
    combo.setCurrentText(current if current in available_fonts() else DEFAULT_FONT)
    combo.blockSignals(False)
