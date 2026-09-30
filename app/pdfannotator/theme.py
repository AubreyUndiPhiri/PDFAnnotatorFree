"""Design tokens and the application-wide style sheet.

Two looks share one style sheet:

- light: glassmorphism and claymorphism together. The pastel gradient
  backdrop shows through frosted, translucent panels (glass), and those
  panels are puffy like clay: lit from the top with a bright rim, a soft
  underside, a real drop shadow under the floating bars, raised pill
  buttons and pressed / active controls that sink in.
- dark: claymorphism. A matte dark backdrop with soft, puffy panels; each
  raised surface is a vertical gradient with a light top edge and a dark
  bottom edge (Qt style sheets have no box-shadow), pressed and checked
  controls invert that so they look pushed in.

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
        "WINDOW": "#eef1fb",
        "SURFACE": "#ffffff",
        "SURFACE_ALT": "#f3f5fb",
        "CANVAS": "#38ffffff",          # frost over the backdrop, behind the PDF pages (#AARRGGBB)
        "BORDER": "#dfe4f0",
        "BORDER_STRONG": "#c5cde0",
        "TEXT": "#1b2236",
        "TEXT_MUTED": "#5d6781",
        "ICON": "#3f4960",
        "ICON_HOVER": "#1b2236",
        "ICON_DISABLED": "#b3bacb",
        "ACCENT": "#4f46e5",
        "ACCENT_HOVER": "#4338ca",
        "ACCENT_PRESSED": "#3730a3",
        "ACCENT_SOFT": "#e6e8fd",
        "ACCENT_SOFT_BORDER": "#c3c6f7",
        "DANGER": "#dc2626",
        "PAGE_SHADOW": QColor(40, 50, 110, 46),
    },
    DARK: {
        "WINDOW": "#1e2130",
        "SURFACE": "#2a2e3f",
        "SURFACE_ALT": "#32374b",
        "CANVAS": "#171a24",
        "BORDER": "#353a4f",
        "BORDER_STRONG": "#454b63",
        "TEXT": "#e9ebf3",
        "TEXT_MUTED": "#9ba2b8",
        "ICON": "#c4c9d8",
        "ICON_HOVER": "#ffffff",
        "ICON_DISABLED": "#596077",
        "ACCENT": "#9d8cff",
        "ACCENT_HOVER": "#b0a3ff",
        "ACCENT_PRESSED": "#8574f5",
        "ACCENT_SOFT": "#37335a",
        "ACCENT_SOFT_BORDER": "#5b52a3",
        "DANGER": "#f87171",
        "PAGE_SHADOW": QColor(0, 0, 0, 170),
    },
}


def _vgrad(top, bottom):
    return f"qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {top}, stop:1 {bottom})"


# ---- surface recipes (style-sheet only: gradients and rgba())
_SURFACES = {
    LIGHT: {  # glass + clay: frosted, see-through panels that are also soft and puffy
        "backdrop": "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #c5d3ff, stop:0.45 #e6d9ff, stop:1 #c3ede2)",
        # frosted panels, lighter at the top like a lit, rounded clay surface
        "panel": _vgrad("rgba(255, 255, 255, 0.80)", "rgba(240, 237, 255, 0.58)"),
        "panel_top": "rgba(255, 255, 255, 1.0)",       # bright glass rim / clay highlight
        "panel_bottom": "rgba(110, 100, 185, 0.30)",   # the clay's soft underside
        # buttons are little clay pills; pressed and checked ones sink in
        "raised": _vgrad("rgba(255, 255, 255, 0.96)", "rgba(232, 229, 252, 0.82)"),
        "raised_hover": _vgrad("#ffffff", "rgba(241, 239, 255, 0.95)"),
        "pressed": _vgrad("rgba(208, 204, 238, 0.90)", "rgba(246, 245, 255, 0.92)"),
        "press_top": "rgba(100, 90, 175, 0.40)",
        "press_bottom": "rgba(255, 255, 255, 1.0)",
        "field": _vgrad("rgba(232, 231, 250, 0.88)", "rgba(255, 255, 255, 0.92)"),
        "field_top": "rgba(100, 90, 175, 0.32)",
        "field_bottom": "rgba(255, 255, 255, 1.0)",
        "checked": _vgrad("rgba(203, 198, 250, 0.95)", "rgba(233, 231, 255, 0.95)"),
        "checked_top": "rgba(79, 70, 229, 0.50)",
        "checked_bottom": "rgba(255, 255, 255, 0.95)",
        "popup": "rgba(250, 249, 255, 0.97)",
        "popup_edge": "rgba(255, 255, 255, 1.0)",
        "item_hover": _vgrad("rgba(220, 216, 252, 0.95)", "rgba(236, 234, 255, 0.95)"),
        "tab": _vgrad("rgba(255, 255, 255, 0.95)", "rgba(236, 233, 255, 0.85)"),
        "pages": "transparent",                        # the backdrop shows through the frost
        "code": "rgba(255, 255, 255, 0.90)",
        "primary": _vgrad("#8d86f8", "#5a51ea"),
        "primary_hover": _vgrad("#9a94fa", "#665eee"),
        "primary_pressed": _vgrad("#4a41d6", "#6a62ef"),
        "primary_top": "#bcb8fc",
        "primary_bottom": "#3c34c4",
        "primary_text": "#ffffff",
        "scroll": "rgba(90, 80, 170, 0.30)",
        "scroll_hover": "rgba(90, 80, 170, 0.48)",
        "tooltip": "#2a2350",
        "tooltip_text": "#ffffff",
        "radius": 12,
        "bar_radius": 20,
        # real soft shadows under the floating bars (Qt style sheets can't do box-shadow)
        "shadow": (90, 78, 175, 60),
        "shadow_blur": 28,
        "shadow_offset": 7,
    },
    DARK: {
        "backdrop": "#1e2130",
        "panel": _vgrad("#2f3447", "#262a3a"),
        "panel_top": "#454c66",                        # light catching the top of the clay
        "panel_bottom": "#12141c",                     # its shadow underneath
        "raised": _vgrad("#353b51", "#2a2e40"),
        "raised_hover": _vgrad("#3f465f", "#30354a"),
        "pressed": _vgrad("#1b1e29", "#282c3d"),
        "press_top": "#101219",
        "press_bottom": "#3d4460",
        "field": "#1a1d28",
        "field_top": "#0f1118",
        "field_bottom": "#3a4058",
        "checked": _vgrad("#221f3d", "#302b55"),
        "checked_top": "#15122a",
        "checked_bottom": "#5b52a3",
        "popup": "#2a2e3f",
        "popup_edge": "#454c66",
        "item_hover": "#37335a",
        "tab": _vgrad("#30354a", "#2a2e3f"),
        "pages": "#171a24",
        "code": "#1a1d28",
        "primary": _vgrad("#b6aaff", "#8574f5"),
        "primary_hover": _vgrad("#c3b9ff", "#9282f7"),
        "primary_pressed": _vgrad("#7a69ee", "#9d8cff"),
        "primary_top": "#d6ceff",
        "primary_bottom": "#5a4acf",
        "primary_text": "#17132e",
        "scroll": "#3a4058",
        "scroll_hover": "#4b5270",
        "tooltip": "#3a3f56",
        "tooltip_text": "#ffffff",
        "radius": 12,
        "bar_radius": 18,
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
QToolButton#swatch {{ border: 1px solid {BORDER_STRONG}; padding: 3px; }}
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
