"""Design tokens and the application-wide style sheet.

Two looks share one style sheet:

- light: white clay-glass. A near-white backdrop washed with soft pastel
  tints (periwinkle, lilac, mint) shows through frosted white panels
  (glass), and those panels are puffy like clay: lit from the top with a
  bright rim, a soft lavender underside and drop shadow, raised pill
  buttons and pressed / active controls that sink in.
- dark: black premium clay. A deep black backdrop with a faint violet glow;
  charcoal clay panels, each a vertical gradient with a fine light rim on
  top and a black edge below, floating on soft black shadows; soft lilac
  accents. Pressed and checked controls invert the edges so they sink in.

Every colour used by the UI lives here so screens, dialogs and painted
overlays stay consistent; code reads theme.X when it paints, so set_mode()
switches the whole app live (icons.py re-tints its icons the same way).
apply() is idempotent and is called from both main.py and MainWindow (the
tests build a MainWindow without main())."""
import tempfile
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSettings, Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QMenu

LIGHT, DARK = "light", "dark"

# ---- colour tokens (plain colours: QColor-readable, used in painting code)
_TOKENS = {
    LIGHT: {
        "WINDOW": "#f6f7fc",
        "SURFACE": "#ffffff",
        "SURFACE_ALT": "#f5f6fb",
        "CANVAS": "#2effffff",          # frost over the backdrop, behind the PDF pages (#AARRGGBB)
        "BORDER": "#e6e8f2",
        "BORDER_STRONG": "#cfd4e4",
        "TEXT": "#1d2233",
        "TEXT_MUTED": "#626a82",
        "ICON": "#454d63",
        "ICON_HOVER": "#1d2233",
        "ICON_DISABLED": "#b8bdcc",
        "ACCENT": "#6366f1",             # soft periwinkle
        "ACCENT_HOVER": "#575ae6",
        "ACCENT_PRESSED": "#4b4ed6",
        "ACCENT_SOFT": "#eceefe",
        "ACCENT_SOFT_BORDER": "#c9ccfa",
        "DANGER": "#dc2626",
        "PAGE_SHADOW": QColor(70, 70, 140, 34),
    },
    DARK: {
        "WINDOW": "#0b0b0e",
        "SURFACE": "#16161b",
        "SURFACE_ALT": "#1c1c22",
        "CANVAS": "#4d000000",          # dims the glowing backdrop a little behind the pages
        "BORDER": "#26262e",
        "BORDER_STRONG": "#363640",
        "TEXT": "#ececf2",
        "TEXT_MUTED": "#9b9bab",
        "ICON": "#c8c8d4",
        "ICON_HOVER": "#ffffff",
        "ICON_DISABLED": "#4e4e5a",
        "ACCENT": "#b4a7ff",             # soft lilac
        "ACCENT_HOVER": "#c5bbff",
        "ACCENT_PRESSED": "#9c8dff",
        "ACCENT_SOFT": "#221e36",
        "ACCENT_SOFT_BORDER": "#4b4383",
        "DANGER": "#f87171",
        "PAGE_SHADOW": QColor(0, 0, 0, 190),
    },
}


def _vgrad(top, bottom):
    return f"qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {top}, stop:1 {bottom})"


# ---- surface recipes (style-sheet only: gradients and rgba())
_SURFACES = {
    LIGHT: {  # white clay-glass: frosted white, puffy panels over a softly tinted white backdrop
        "backdrop": "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #e8ecff, stop:0.35 #f8f7ff, "
                    "stop:0.7 #fbf4fb, stop:1 #e6f6f1)",
        # frosted white panels, brightest at the top like a lit, rounded clay surface
        "panel": _vgrad("rgba(255, 255, 255, 0.90)", "rgba(248, 247, 255, 0.72)"),
        "panel_top": "rgba(255, 255, 255, 1.0)",       # bright glass rim / clay highlight
        "panel_bottom": "rgba(130, 125, 190, 0.22)",   # the clay's soft lavender underside
        # buttons are little white clay pills; pressed and checked ones sink in
        "raised": _vgrad("#ffffff", "rgba(243, 242, 253, 0.92)"),
        "raised_hover": _vgrad("#ffffff", "rgba(246, 245, 255, 0.98)"),
        "pressed": _vgrad("rgba(226, 224, 245, 0.95)", "rgba(250, 250, 255, 0.95)"),
        "press_top": "rgba(120, 115, 185, 0.32)",
        "press_bottom": "rgba(255, 255, 255, 1.0)",
        "field": _vgrad("rgba(240, 240, 250, 0.92)", "rgba(255, 255, 255, 0.96)"),
        "field_top": "rgba(120, 115, 185, 0.24)",
        "field_bottom": "rgba(255, 255, 255, 1.0)",
        "checked": _vgrad("rgba(222, 222, 253, 0.98)", "rgba(240, 240, 255, 0.98)"),
        "checked_top": "rgba(99, 102, 241, 0.38)",
        "checked_bottom": "rgba(255, 255, 255, 1.0)",
        "popup": "rgba(255, 255, 255, 0.98)",
        "popup_edge": "rgba(228, 228, 244, 1.0)",
        "item_hover": _vgrad("rgba(234, 234, 254, 0.98)", "rgba(244, 244, 255, 0.98)"),
        "tab": _vgrad("#ffffff", "rgba(246, 245, 255, 0.92)"),
        "pages": "transparent",                        # the backdrop shows through the frost
        "code": "rgba(255, 255, 255, 0.94)",
        "primary": _vgrad("#9194f8", "#6366f1"),
        "primary_hover": _vgrad("#9ea1fa", "#6f72f3"),
        "primary_pressed": _vgrad("#5557e3", "#7477f4"),
        "primary_top": "#c7c9fd",
        "primary_bottom": "#4f52dc",
        "primary_text": "#ffffff",
        "scroll": "rgba(110, 105, 175, 0.24)",
        "scroll_hover": "rgba(110, 105, 175, 0.40)",
        "tooltip": "#2b2d45",
        "tooltip_text": "#ffffff",
        "radius": 12,
        "bar_radius": 20,
        # real soft shadows under the floating bars (Qt style sheets can't do box-shadow)
        "shadow": (110, 105, 185, 42),
        "shadow_blur": 30,
        "shadow_offset": 7,
    },
    DARK: {  # black premium clay: charcoal panels on soft black shadows, over a faint violet glow
        "backdrop": "qradialgradient(cx:0.12, cy:0.0, radius:1.25, fx:0.12, fy:0.0, stop:0 #1c1830, "
                    "stop:0.45 #0c0c10, stop:1 #060608)",
        "panel": _vgrad("#1b1b21", "#121216"),
        "panel_top": "#2d2d36",                        # a fine rim of light on the clay's top
        "panel_bottom": "#030304",                     # its black underside
        "raised": _vgrad("#222229", "#18181d"),
        "raised_hover": _vgrad("#2a2a33", "#1d1d23"),
        "pressed": _vgrad("#0c0c0f", "#17171c"),
        "press_top": "#030304",
        "press_bottom": "#2f2f39",
        "field": _vgrad("#0d0d10", "#131317"),
        "field_top": "#030304",
        "field_bottom": "#2a2a33",
        "checked": _vgrad("#1b1830", "#262143"),
        "checked_top": "#0c0a18",
        "checked_bottom": "#5a5096",
        "popup": "#17171c",
        "popup_edge": "#2d2d36",
        "item_hover": _vgrad("#262143", "#1f1b36"),
        "tab": _vgrad("#202027", "#16161b"),
        "pages": "transparent",                        # the glow shows through, a little dimmed
        "code": "#0e0e11",
        "primary": _vgrad("#c7bdff", "#9c8dff"),
        "primary_hover": _vgrad("#d3cbff", "#a99bff"),
        "primary_pressed": _vgrad("#8d7ef5", "#b4a7ff"),
        "primary_top": "#e2dcff",
        "primary_bottom": "#6f5fe0",
        "primary_text": "#120f24",
        "scroll": "#2c2c35",
        "scroll_hover": "#3d3d49",
        "tooltip": "#24242c",
        "tooltip_text": "#ffffff",
        "radius": 12,
        "bar_radius": 20,
        # soft black shadows: the clay floats above the backdrop
        "shadow": (0, 0, 0, 170),
        "shadow_blur": 32,
        "shadow_offset": 8,
    },
}

# Painted overlays on the page (the pages themselves stay white in both modes)
SELECTION = QColor(79, 70, 229)
SEARCH_FLASH = QColor(245, 158, 11)
GUIDE = QColor(14, 165, 233, 170)

FONT_FAMILY = "Segoe UI"
FONT_SIZE = 9

_CACHE = Path(tempfile.gettempdir()) / "aupedean-annotator-ui"
_SETTINGS_KEY = "ui/theme"
_applied = False
mode = LIGHT
S = _SURFACES[LIGHT]
globals().update(_TOKENS[LIGHT])


def _settings() -> QSettings:
    # defaultFormat(): the registry normally; tests point it at a temp .ini
    return QSettings(QSettings.defaultFormat(), QSettings.UserScope, "AupedeanAnnotator", "AupedeanAnnotator")


def _load_tokens(new_mode):
    global mode, S
    mode = new_mode if new_mode in _TOKENS else LIGHT
    S = _SURFACES[mode]
    globals().update(_TOKENS[mode])


def _qss() -> str:
    from . import icons

    s = S
    r, br = s["radius"], s["bar_radius"]
    down = icons.write_tinted("chevron-down", TEXT_MUTED, _CACHE)
    up = icons.write_tinted("chevron-up", TEXT_MUTED, _CACHE, 12)
    down_small = icons.write_tinted("chevron-down", TEXT_MUTED, _CACHE, 12)
    panel_edges = f"border: 1px solid {s['panel_top']}; border-bottom-color: {s['panel_bottom']};"
    raised_edges = f"border: 1px solid {s['panel_top']}; border-bottom-color: {s['panel_bottom']};"
    pressed_edges = f"border: 1px solid {s['press_bottom']}; border-top-color: {s['press_top']};"
    field_edges = f"border: 1px solid {s['field_top']}; border-bottom-color: {s['field_bottom']};"
    checked_edges = f"border: 1px solid {s['checked_top']}; border-bottom-color: {s['checked_bottom']};"
    return f"""
QMainWindow, QDialog {{ background: {s['backdrop']}; }}
QToolTip {{ background: {s['tooltip']}; color: {s['tooltip_text']}; border: none; padding: 6px 8px; border-radius: 6px; }}
QLabel, QCheckBox, QRadioButton {{ color: {TEXT}; background: transparent; }}

/* ---- menus */
QMenuBar {{ background: transparent; border: none; padding: 4px 8px 0 8px; color: {TEXT}; }}
QMenuBar::item {{ padding: 5px 11px; border-radius: {r}px; background: transparent; }}
QMenuBar::item:selected {{ background: {s['raised_hover']}; }}
QMenu {{ background: {s['popup']}; border: 1px solid {s['popup_edge']}; border-radius: {r + 2}px; padding: 6px; color: {TEXT}; }}
QMenu::item {{ padding: 6px 28px 6px 10px; border-radius: {max(5, r - 3)}px; background: transparent; }}
QMenu::item:selected {{ background: {s['item_hover']}; color: {TEXT}; }}
QMenu::item:disabled {{ color: {ICON_DISABLED}; }}
QMenu::icon {{ padding-left: 8px; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 6px; }}

/* ---- toolbars: floating glass / clay bars */
QToolBar {{ background: {s['panel']}; {panel_edges} border-radius: {br}px; margin: 4px 8px 2px 8px;
    padding: 4px 8px; spacing: 3px; }}
QToolBar::separator {{ background: {BORDER}; width: 1px; margin: 7px 6px; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: {r}px; padding: 4px; color: {TEXT}; }}
QToolButton:hover {{ background: {s['raised_hover']}; {raised_edges} }}
QToolButton:pressed {{ background: {s['pressed']}; {pressed_edges} }}
QToolButton:checked {{ background: {s['checked']}; {checked_edges} }}
QToolButton[popupMode="1"] {{ padding-right: 14px; }}
QToolButton::menu-button {{ border: none; width: 12px; }}
QToolButton::menu-arrow {{ image: url({down_small}); }}
QToolButton[popupMode="2"] {{ padding-right: 18px; }}
QToolButton::menu-indicator {{ image: url({down_small}); subcontrol-origin: padding;
    subcontrol-position: center right; right: 4px; width: 10px; height: 10px; }}
QToolButton#ribbonToggle {{ margin: 2px 10px 0 4px; padding: 4px; }}
QToolButton#swatch {{ border: 1px solid {ICON_DISABLED}; padding: 3px; }}
QToolBar QLabel {{ color: {TEXT_MUTED}; padding: 0 2px 0 6px; }}

/* ---- document tabs */
QTabWidget::pane {{ border: none; background: {s['pages']}; }}
QTabBar {{ background: transparent; qproperty-drawBase: 0; }}
QTabWidget::tab-bar {{ border: none; }}
QMainWindow::separator {{ background: transparent; width: 0; height: 0; }}
QTabBar::tab {{ background: transparent; color: {TEXT_MUTED}; padding: 7px 10px 7px 14px; margin: 4px 0 2px 6px;
    border: 1px solid transparent; border-radius: {r}px; min-width: 90px; }}
QTabBar::tab:hover {{ background: {s['raised_hover']}; color: {TEXT}; }}
QTabBar::tab:selected {{ background: {s['tab']}; color: {TEXT}; {raised_edges} }}
QTabBar QToolButton {{ padding: 2px; border-radius: 6px; }}

/* ---- inputs */
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {s['field']}; color: {TEXT}; {field_edges} border-radius: {r}px; padding: 4px 8px;
    selection-background-color: {ACCENT}; selection-color: #ffffff; }}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {ACCENT}; }}
QComboBox {{ padding-right: 22px; min-height: 18px; }}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {ACCENT_SOFT_BORDER}; }}
QComboBox::drop-down {{ border: none; width: 20px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url({down}); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {s['popup']}; color: {TEXT}; border: 1px solid {s['popup_edge']}; padding: 4px;
    selection-background-color: {s['item_hover']}; selection-color: {TEXT}; outline: none; }}
QSpinBox, QDoubleSpinBox {{ padding-right: 18px; min-height: 18px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{
    border: none; width: 16px; subcontrol-origin: border; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-position: top right; margin: 2px 3px 0 0; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-position: bottom right; margin: 0 3px 2px 0; }}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {s['raised_hover']}; border-radius: 4px; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({up}); width: 9px; height: 9px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({down_small}); width: 9px; height: 9px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; background: {s['field']}; {field_edges} }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border: 1px solid {ACCENT}; image: url({icons.write_tinted("check", "#ffffff", _CACHE, 14)}); }}

/* ---- buttons */
QPushButton {{ background: {s['raised']}; color: {TEXT}; {raised_edges} border-radius: {r}px; padding: 6px 16px; min-height: 16px; }}
QPushButton:hover {{ background: {s['raised_hover']}; }}
QPushButton:pressed {{ background: {s['pressed']}; {pressed_edges} }}
QPushButton:checked {{ background: {s['checked']}; {checked_edges} }}
QPushButton:disabled {{ color: {ICON_DISABLED}; }}
QPushButton#primary {{ background: {s['primary']}; color: {s['primary_text']}; font-weight: 600;
    border: 1px solid {s['primary_top']}; border-bottom-color: {s['primary_bottom']}; }}
QPushButton#primary:hover {{ background: {s['primary_hover']}; }}
QPushButton#primary:pressed {{ background: {s['primary_pressed']}; border: 1px solid {s['primary_bottom']};
    border-bottom-color: {s['primary_top']}; }}
QPushButton#icon {{ padding: 5px; min-width: 18px; }}

/* ---- progress */
QProgressBar {{ background: {s['field']}; {field_edges} border-radius: {r}px; text-align: center; color: {TEXT}; min-height: 14px; }}
QProgressBar::chunk {{ background: {s['primary']}; border-radius: {max(4, r - 2)}px; }}

/* ---- sidebar thumbnails */
QListWidget#thumbnails {{ background: {s['panel']}; {panel_edges} border-radius: {br}px; outline: none; padding: 6px 0;
    margin: 4px 0 6px 8px; }}
QListWidget#thumbnails::item {{ color: {TEXT_MUTED}; border: 2px solid transparent; border-radius: {r}px; padding: 6px 4px; margin: 2px 8px; }}
QListWidget#thumbnails::item:hover {{ background: {s['raised_hover']}; }}
QListWidget#thumbnails::item:selected {{ background: {s['checked']}; border-color: {ACCENT}; color: {TEXT}; }}

/* ---- document canvas */
QScrollArea#canvas, QScrollArea#canvas > QWidget, QWidget#canvasContents {{ background: transparent; border: none; }}
QSplitter {{ background: transparent; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 6px; }}

/* ---- scroll bars */
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle {{ background: {s['scroll']}; border-radius: 4px; min-height: 32px; min-width: 32px; }}
QScrollBar::handle:hover {{ background: {s['scroll_hover']}; }}
QScrollBar::handle:vertical {{ margin: 0 2px; }}
QScrollBar::handle:horizontal {{ margin: 2px 0; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- tables (Tool Styles) */
QTableWidget {{ background: {s['field']}; color: {TEXT}; {field_edges} border-radius: {r}px; gridline-color: {BORDER}; }}
QHeaderView::section {{ background: {s['raised']}; color: {TEXT_MUTED}; border: none; border-bottom: 1px solid {BORDER};
    padding: 6px 8px; font-weight: 600; }}

/* ---- status bar */
QStatusBar {{ background: {s['panel']}; {panel_edges} border-radius: {br}px; margin: 2px 8px 6px 8px; min-height: 26px;
    color: {TEXT_MUTED}; }}
QStatusBar QLabel {{ color: {TEXT_MUTED}; padding: 0 8px; }}
QStatusBar::item {{ border: none; }}

/* ---- labels */
QLabel#dialogTitle {{ font-size: 12pt; font-weight: 600; }}
QLabel#muted {{ color: {TEXT_MUTED}; }}

/* ---- Word and LaTeX tabs: paper stays white in both looks */
QScrollArea#wordScroll, QScrollArea#previewScroll, QScrollArea#wordScroll > QWidget,
QScrollArea#previewScroll > QWidget {{ background: transparent; border: none; }}
QTextEdit#paper {{ background: #ffffff; color: #000000; border: none; border-radius: 0; padding: 0;
    selection-background-color: #b4d5fe; selection-color: #000000; }}
QLabel#paper {{ background: #ffffff; border: none; }}
QPlainTextEdit#latexCode, QPlainTextEdit#latexLog {{ background: {s['code']}; color: {TEXT}; border: none;
    border-radius: 0; padding: 0; selection-background-color: {ACCENT}; selection-color: #ffffff; }}
QWidget#findReplaceBar {{ background: {s['panel']}; border-bottom: 1px solid {BORDER}; }}
QListWidget#projectFiles, QListWidget {{ background: {s['field']}; color: {TEXT}; {field_edges} border-radius: {r}px;
    outline: none; padding: 4px; }}
QListWidget::item {{ padding: 3px 6px; border-radius: {max(4, r - 4)}px; }}
QListWidget::item:selected {{ background: {s['item_hover']}; color: {TEXT}; }}

/* ---- Formula tool: the LaTeX card on the page */
QFrame#formulaEditor {{ background: {s['popup']}; border: 1px solid {ACCENT_SOFT_BORDER};
    border-bottom-color: {s['panel_bottom']}; border-radius: {r + 4}px; }}
QLabel#formulaTitle {{ color: {ACCENT}; font-weight: 600; }}
QPlainTextEdit#formulaSource {{ background: {s['field']}; color: {TEXT}; {field_edges} border-radius: {r}px;
    padding: 4px 6px; selection-background-color: {ACCENT}; selection-color: #ffffff; }}
QLabel#formulaPreview {{ background: #ffffff; color: #6b7280; border: 1px solid {BORDER}; border-radius: {r}px;
    padding: 8px; }}
QLabel#formulaStatus {{ color: {TEXT_MUTED}; font-size: 8pt; }}
QLabel#formulaStatus[error="true"] {{ color: {DANGER}; }}

/* ---- on-page text box while typing */
QTextEdit#inlineText {{ background: transparent; border: 1px dashed {ACCENT}; border-radius: 0; padding: 0;
    selection-background-color: {ACCENT_SOFT_BORDER}; selection-color: {TEXT}; }}
"""


def _palette() -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(WINDOW))
    palette.setColor(QPalette.WindowText, QColor(TEXT))
    palette.setColor(QPalette.Base, QColor(SURFACE))
    palette.setColor(QPalette.AlternateBase, QColor(SURFACE_ALT))
    palette.setColor(QPalette.ToolTipBase, QColor(S["tooltip"]))
    palette.setColor(QPalette.ToolTipText, QColor(S["tooltip_text"]))
    palette.setColor(QPalette.Text, QColor(TEXT))
    palette.setColor(QPalette.PlaceholderText, QColor(ICON_DISABLED))
    palette.setColor(QPalette.Button, QColor(SURFACE))
    palette.setColor(QPalette.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.Link, QColor(ACCENT))
    return palette


class _RoundedPopups(QObject):
    """Menus are separate windows: without a translucent background their
    rounded corners are filled in square."""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Polish and isinstance(obj, QMenu):
            obj.setAttribute(Qt.WA_TranslucentBackground)
            obj.setWindowFlag(Qt.NoDropShadowWindowHint)
        return False


_popups = _RoundedPopups()

# The floating bars that get a real soft shadow in light mode
_SHADOW_NAMES = {"commandBar", "toolBar", "editorBar"}


def _wants_shadow(widget):
    from PySide6.QtWidgets import QStatusBar

    return isinstance(widget, QStatusBar) or widget.objectName() in _SHADOW_NAMES


def _apply_depth(widget):
    """Give (or take away) the clay drop shadow under a floating bar."""
    from PySide6.QtWidgets import QGraphicsDropShadowEffect

    spec = S.get("shadow")
    effect = widget.graphicsEffect()
    if spec is None:
        if isinstance(effect, QGraphicsDropShadowEffect):
            widget.setGraphicsEffect(None)
        return
    if not isinstance(effect, QGraphicsDropShadowEffect):
        if effect is not None:
            return  # someone else's effect: leave it
        effect = QGraphicsDropShadowEffect(widget)
        widget.setGraphicsEffect(effect)
    effect.setColor(QColor(*spec))
    effect.setBlurRadius(S["shadow_blur"])
    effect.setOffset(0, S["shadow_offset"])


class _Depth(QObject):
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Polish and hasattr(obj, "objectName") and _wants_shadow(obj):
            _apply_depth(obj)
        return False


_depth = _Depth()


def _restyle(app):
    app.setPalette(_palette())
    app.setStyleSheet(_qss())


def apply(app: QApplication | None = None):
    global _applied
    app = app or QApplication.instance()
    if app is None or _applied:
        return
    app.setStyle("Fusion")
    app.setFont(QFont(FONT_FAMILY, FONT_SIZE))
    app.installEventFilter(_popups)
    app.installEventFilter(_depth)
    _load_tokens(_settings().value(_SETTINGS_KEY, LIGHT))
    _restyle(app)
    _applied = True


def set_mode(new_mode, app: QApplication | None = None):
    """Switch between LIGHT (glass + clay) and DARK (clay) live, and remember it."""
    app = app or QApplication.instance()
    _load_tokens(new_mode)
    _settings().setValue(_SETTINGS_KEY, mode)
    if app is None:
        return
    _restyle(app)
    for widget in app.allWidgets():
        if _wants_shadow(widget):
            _apply_depth(widget)
        widget.update()
