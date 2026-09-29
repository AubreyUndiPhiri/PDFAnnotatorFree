"""Design tokens and the application-wide style sheet.

Every colour used by the UI lives here so screens, dialogs and painted
overlays stay consistent. apply() is idempotent and is called from both
main.py and MainWindow (the tests build a MainWindow without main())."""
import tempfile
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

# ---- colour tokens
WINDOW = "#f5f6f8"
SURFACE = "#ffffff"
SURFACE_ALT = "#f0f2f5"
CANVAS = "#e3e6eb"          # background behind the PDF pages
BORDER = "#dde1e7"
BORDER_STRONG = "#c3c9d3"
TEXT = "#1d2433"
TEXT_MUTED = "#626b7c"
ICON = "#454e5f"
ICON_HOVER = "#1d2433"
ICON_DISABLED = "#b5bbc5"
ACCENT = "#2563eb"
ACCENT_HOVER = "#1d4fd8"
ACCENT_PRESSED = "#1a43b8"
ACCENT_SOFT = "#e7eefd"
ACCENT_SOFT_BORDER = "#bcd0fa"
DANGER = "#dc2626"

# Painted overlays on the page
SELECTION = QColor(37, 99, 235)
SEARCH_FLASH = QColor(245, 158, 11)
GUIDE = QColor(14, 165, 233, 170)
PAGE_SHADOW = QColor(15, 23, 42, 38)

FONT_FAMILY = "Segoe UI"
FONT_SIZE = 9

_CACHE = Path(tempfile.gettempdir()) / "aupedian-annotators-ui"
_applied = False


def _qss() -> str:
    from . import icons

    down = icons.write_tinted("chevron-down", TEXT_MUTED, _CACHE)
    up = icons.write_tinted("chevron-up", TEXT_MUTED, _CACHE, 12)
    down_small = icons.write_tinted("chevron-down", TEXT_MUTED, _CACHE, 12)
    return f"""
QMainWindow, QDialog {{ background: {WINDOW}; }}
QToolTip {{ background: {TEXT}; color: #ffffff; border: none; padding: 6px 8px; border-radius: 4px; }}

/* ---- menus */
QMenuBar {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; padding: 2px 6px; }}
QMenuBar::item {{ padding: 5px 10px; border-radius: 4px; background: transparent; }}
QMenuBar::item:selected {{ background: {SURFACE_ALT}; }}
QMenu {{ background: {SURFACE}; border: 1px solid {BORDER}; padding: 6px; }}
QMenu::item {{ padding: 6px 28px 6px 10px; border-radius: 5px; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}
QMenu::item:disabled {{ color: {ICON_DISABLED}; }}
QMenu::icon {{ padding-left: 8px; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 6px; }}

/* ---- toolbars */
QToolBar {{ background: {SURFACE}; border: none; border-bottom: 1px solid {BORDER}; padding: 4px 8px; spacing: 2px; }}
QToolBar::separator {{ background: {BORDER}; width: 1px; margin: 6px 6px; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 4px; }}
QToolButton:hover {{ background: {SURFACE_ALT}; border-color: {BORDER}; }}
QToolButton:pressed {{ background: {BORDER}; }}
QToolButton:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT_SOFT_BORDER}; }}
QToolButton[popupMode="1"] {{ padding-right: 14px; }}
QToolButton::menu-button {{ border: none; width: 12px; }}
QToolButton::menu-arrow {{ image: url({down_small}); }}
QToolButton#swatch {{ border: 1px solid {BORDER_STRONG}; padding: 3px; }}
QToolBar QLabel {{ color: {TEXT_MUTED}; padding: 0 2px 0 6px; }}

/* ---- document tabs */
QTabWidget::pane {{ border: none; background: {CANVAS}; }}
QTabBar {{ background: {WINDOW}; }}
QTabBar::tab {{ background: transparent; color: {TEXT_MUTED}; padding: 7px 10px 7px 14px; margin: 4px 0 0 4px;
    border: 1px solid transparent; border-bottom: none; border-top-left-radius: 7px; border-top-right-radius: 7px; min-width: 90px; }}
QTabBar::tab:hover {{ background: {SURFACE_ALT}; color: {TEXT}; }}
QTabBar::tab:selected {{ background: {SURFACE}; color: {TEXT}; border-color: {BORDER}; }}
QTabBar QToolButton {{ padding: 2px; border-radius: 4px; }}

/* ---- inputs */
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {SURFACE}; border: 1px solid {BORDER_STRONG}; border-radius: 6px; padding: 4px 6px;
    selection-background-color: {ACCENT}; selection-color: #ffffff; }}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT}; }}
QComboBox {{ padding-right: 22px; min-height: 18px; }}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {ICON_DISABLED}; }}
QComboBox::drop-down {{ border: none; width: 20px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url({down}); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px; padding: 4px;
    selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT}; outline: none; }}
QSpinBox, QDoubleSpinBox {{ padding-right: 18px; min-height: 18px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{
    border: none; width: 16px; subcontrol-origin: border; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-position: top right; margin: 2px 2px 0 0; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-position: bottom right; margin: 0 2px 2px 0; }}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {SURFACE_ALT}; border-radius: 3px; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({up}); width: 9px; height: 9px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({down_small}); width: 9px; height: 9px; }}

/* ---- buttons */
QPushButton {{ background: {SURFACE}; border: 1px solid {BORDER_STRONG}; border-radius: 6px; padding: 6px 14px; min-height: 16px; }}
QPushButton:hover {{ background: {SURFACE_ALT}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT_SOFT_BORDER}; }}
QPushButton:disabled {{ color: {ICON_DISABLED}; }}
QPushButton#primary {{ background: {ACCENT}; border-color: {ACCENT}; color: #ffffff; font-weight: 600; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#primary:pressed {{ background: {ACCENT_PRESSED}; }}
QPushButton#icon {{ padding: 5px; min-width: 18px; }}

/* ---- sidebar thumbnails */
QListWidget#thumbnails {{ background: {SURFACE}; border: none; border-right: 1px solid {BORDER}; outline: none; padding: 6px 0; }}
QListWidget#thumbnails::item {{ color: {TEXT_MUTED}; border: 2px solid transparent; border-radius: 6px; padding: 6px 4px; margin: 2px 8px; }}
QListWidget#thumbnails::item:hover {{ background: {SURFACE_ALT}; }}
QListWidget#thumbnails::item:selected {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; color: {TEXT}; }}

/* ---- document canvas */
QScrollArea#canvas, QWidget#canvasContents {{ background: {CANVAS}; border: none; }}
QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:horizontal {{ width: 1px; }}

/* ---- scroll bars */
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle {{ background: {BORDER_STRONG}; border-radius: 4px; min-height: 32px; min-width: 32px; }}
QScrollBar::handle:hover {{ background: {ICON_DISABLED}; }}
QScrollBar::handle:vertical {{ margin: 0 2px; }}
QScrollBar::handle:horizontal {{ margin: 2px 0; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- tables (Tool Styles) */
QTableWidget {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; gridline-color: {SURFACE_ALT}; }}
QHeaderView::section {{ background: {SURFACE_ALT}; color: {TEXT_MUTED}; border: none; border-bottom: 1px solid {BORDER};
    padding: 6px 8px; font-weight: 600; }}

/* ---- status bar */
QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; color: {TEXT_MUTED}; }}
QStatusBar QLabel {{ color: {TEXT_MUTED}; padding: 0 8px; }}
QStatusBar::item {{ border: none; }}

/* ---- labels */
QLabel#dialogTitle {{ font-size: 12pt; font-weight: 600; }}
QLabel#muted {{ color: {TEXT_MUTED}; }}

/* ---- on-page text box while typing */
QTextEdit#inlineText {{ background: transparent; border: 1px dashed {ACCENT}; border-radius: 0; padding: 0;
    selection-background-color: {ACCENT_SOFT_BORDER}; selection-color: {TEXT}; }}
"""


def apply(app: QApplication | None = None):
    global _applied
    app = app or QApplication.instance()
    if app is None or _applied:
        return
    app.setStyle("Fusion")
    app.setFont(QFont(FONT_FAMILY, FONT_SIZE))

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(WINDOW))
    palette.setColor(QPalette.WindowText, QColor(TEXT))
    palette.setColor(QPalette.Base, QColor(SURFACE))
    palette.setColor(QPalette.AlternateBase, QColor(SURFACE_ALT))
    palette.setColor(QPalette.ToolTipBase, QColor(TEXT))
    palette.setColor(QPalette.ToolTipText, QColor("#ffffff"))
    palette.setColor(QPalette.Text, QColor(TEXT))
    palette.setColor(QPalette.PlaceholderText, QColor(ICON_DISABLED))
    palette.setColor(QPalette.Button, QColor(SURFACE))
    palette.setColor(QPalette.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.Link, QColor(ACCENT))
    app.setPalette(palette)
    app.setStyleSheet(_qss())
    _applied = True
