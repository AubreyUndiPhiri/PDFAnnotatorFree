"""The Word tab's dialogs: Paragraph, Page Setup, Word Count, Symbol and
Date & Time. Sizes are shown in points (pt) or centimetres, as Word does."""
import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QListWidget, QPushButton, QScrollArea, QToolButton, QWidget,
)

from .dialogs import _button_row, _dialog_layout, _header, _primary

PT_PER_CM = 72 / 2.54

# Page sizes in points (width, height), portrait
PAGE_SIZES = {
    "Letter (21.59 x 27.94 cm)": (612.0, 792.0),
    "Legal (21.59 x 35.56 cm)": (612.0, 1008.0),
    "A3 (29.7 x 42 cm)": (841.9, 1190.6),
    "A4 (21 x 29.7 cm)": (595.3, 841.9),
    "A5 (14.8 x 21 cm)": (419.5, 595.3),
    "B5 (17.6 x 25 cm)": (498.9, 708.7),
    "Executive (18.42 x 26.67 cm)": (522.0, 756.0),
}
# Word's margin presets: (left, top, right, bottom) in points
MARGIN_PRESETS = {
    "Normal (2.54 cm all round)": (72.0, 72.0, 72.0, 72.0),
    "Narrow (1.27 cm)": (36.0, 36.0, 36.0, 36.0),
    "Moderate (1.91 cm sides, 2.54 cm top / bottom)": (54.0, 72.0, 54.0, 72.0),
    "Wide (5.08 cm sides)": (144.0, 72.0, 144.0, 72.0),
}
LINE_SPACINGS = (("Single", 1.0), ("1.15", 1.15), ("1.5 lines", 1.5), ("Double", 2.0), ("2.5", 2.5),
                 ("Triple", 3.0))

SYMBOLS = (
    "© ® ™ § ¶ † ‡ • · … – — ‘ ’ “ ” « » ‹ › ¡ ¿ ° ′ ″ ‰ ‱ "
    "€ £ ¥ ¢ $ ₹ ₽ ₩ ₦ ₿ ¤ "
    "± × ÷ − ≈ ≠ ≡ ≤ ≥ ∞ √ ∛ ∑ ∏ ∫ ∂ ∆ ∇ ∈ ∉ ∩ ∪ ⊂ ⊃ ⊆ ⊇ ∀ ∃ ¬ ∧ ∨ ⊕ ⊗ ∝ ∠ ⊥ ∥ ½ ⅓ ⅔ ¼ ¾ ⅛ ¹ ² ³ ⁴ ⁿ "
    "α β γ δ ε ζ η θ ι κ λ μ ν ξ ο π ρ σ τ υ φ χ ψ ω Α Β Γ Δ Ε Ζ Η Θ Ι Κ Λ Μ Ν Ξ Ο Π Ρ Σ Τ Υ Φ Χ Ψ Ω "
    "← ↑ → ↓ ↔ ↕ ⇐ ⇑ ⇒ ⇓ ⇔ ↺ ↻ ➔ "
    "✓ ✔ ✗ ✘ ★ ☆ ♠ ♣ ♥ ♦ ♪ ♫ ☎ ✉ ✂ ✎ ☐ ☑ ☒ ○ ● □ ■ ▲ △ ▼ ▽ ◆ ◇ "
    "À Á Â Ã Ä Å Æ Ç È É Ê Ë Ì Í Î Ï Ñ Ò Ó Ô Õ Ö Ø Ù Ú Û Ü Ý ß à á â ã ä å æ ç è é ê ë ì í î ï ñ ò ó ô õ ö ø ù ú û ü ý ÿ"
).split()
COMMON_SYMBOLS = ("©", "®", "™", "€", "£", "§", "°", "±", "×", "÷", "≠", "≤", "≥", "∞", "½", "—", "–", "…",
                  "✓", "→")
SPECIAL_CHARACTERS = (("Em Dash", "—"), ("En Dash", "–"), ("Non-breaking Space", " "),
                      ("Non-breaking Hyphen", "‑"), ("Optional Hyphen", "­"),
                      ("Ellipsis", "…"), ("Copyright", "©"), ("Registered", "®"),
                      ("Trademark", "™"), ("Section", "§"), ("Paragraph", "¶"))


def _spin(value, low, high, suffix, step=1.0, decimals=1):
    box = QDoubleSpinBox()
    box.setRange(low, high)
    box.setDecimals(decimals)
    box.setSingleStep(step)
    box.setSuffix(suffix)
    box.setValue(value)
    return box


def _footer(dialog, ok_text="OK"):
    cancel = QPushButton("Cancel")
    cancel.clicked.connect(dialog.reject)
    ok = _primary(ok_text)
    ok.clicked.connect(dialog.accept)
    return _button_row(cancel, ok)


class ParagraphDialog(QDialog):
    """Indents and spacing, like Word's Paragraph dialog. Values in points;
    `values` is a dict (align, left, right, first, before, after, line, page_break)."""

    ALIGNS = (("Left", Qt.AlignLeft), ("Centred", Qt.AlignHCenter), ("Right", Qt.AlignRight),
              ("Justified", Qt.AlignJustify))
    SPECIAL = ("(none)", "First line", "Hanging")

    def __init__(self, values, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Paragraph")
        layout = _dialog_layout(self)
        layout.addLayout(_header("Paragraph", "Alignment, indents and spacing of the selected paragraphs"))

        general = QFormLayout()
        self.align = QComboBox()
        for label, _flag in self.ALIGNS:
            self.align.addItem(label)
        flags = [f for _l, f in self.ALIGNS]
        self.align.setCurrentIndex(flags.index(values["align"]) if values["align"] in flags else 0)
        general.addRow("Alignment", self.align)
        layout.addLayout(general)

        indent_box = QGroupBox("Indentation")
        form = QFormLayout(indent_box)
        self.left = _spin(values["left"], 0, 600, " pt")
        self.right = _spin(values["right"], 0, 600, " pt")
        self.special = QComboBox()
        self.special.addItems(self.SPECIAL)
        first = values["first"]
        self.special.setCurrentIndex(0 if abs(first) < 0.05 else 1 if first > 0 else 2)
        self.by = _spin(abs(first), 0, 300, " pt")
        row = QHBoxLayout()
        row.addWidget(self.special, 1)
        row.addWidget(QLabel("By"))
        row.addWidget(self.by)
        form.addRow("Left", self.left)
        form.addRow("Right", self.right)
        form.addRow("Special", row)
        layout.addWidget(indent_box)

        spacing_box = QGroupBox("Spacing")
        form = QFormLayout(spacing_box)
        self.before = _spin(values["before"], 0, 600, " pt", 6)
        self.after = _spin(values["after"], 0, 600, " pt", 6)
        self.line = QComboBox()
        for label, value in LINE_SPACINGS:
            self.line.addItem(label, value)
        self.line.addItem("Multiple", None)
        self.multiple = _spin(values["line"], 0.5, 5.0, "", 0.05, 2)
        match = next((i for i, (_l, v) in enumerate(LINE_SPACINGS) if abs(v - values["line"]) < 1e-3), None)
        self.line.setCurrentIndex(match if match is not None else len(LINE_SPACINGS))
        self.multiple.setEnabled(match is None)
        self.line.currentIndexChanged.connect(lambda i: self.multiple.setEnabled(self.line.itemData(i) is None))
        row = QHBoxLayout()
        row.addWidget(self.line, 1)
        row.addWidget(QLabel("At"))
        row.addWidget(self.multiple)
        form.addRow("Before", self.before)
        form.addRow("After", self.after)
        form.addRow("Line spacing", row)
        layout.addWidget(spacing_box)

        self.page_break = QCheckBox("Page break before")
        self.page_break.setChecked(values["page_break"])
        layout.addWidget(self.page_break)
        layout.addLayout(_footer(self))

    def values(self):
        line = self.line.currentData()
        first = self.by.value() * (0 if self.special.currentIndex() == 0 else 1 if self.special.currentIndex() == 1
                                   else -1)
        return {"align": self.ALIGNS[self.align.currentIndex()][1], "left": self.left.value(),
                "right": self.right.value(), "first": first, "before": self.before.value(),
                "after": self.after.value(), "line": line if line is not None else self.multiple.value(),
                "page_break": self.page_break.isChecked()}


class PageSetupDialog(QDialog):
    """Paper size, orientation and margins (centimetres)."""

    def __init__(self, width_pt, height_pt, margins_pt, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Page Setup")
        layout = _dialog_layout(self)
        layout.addLayout(_header("Page setup"))
        landscape = width_pt > height_pt
        portrait = (min(width_pt, height_pt), max(width_pt, height_pt))

        paper = QGroupBox("Paper")
        form = QFormLayout(paper)
        self.size = QComboBox()
        for name in PAGE_SIZES:
            self.size.addItem(name)
        self.size.addItem("Custom")
        match = next((i for i, v in enumerate(PAGE_SIZES.values())
                      if abs(v[0] - portrait[0]) < 2 and abs(v[1] - portrait[1]) < 2), None)
        self.size.setCurrentIndex(match if match is not None else len(PAGE_SIZES))
        self.width = _spin(width_pt / PT_PER_CM, 5, 120, " cm", 0.1, 2)
        self.height = _spin(height_pt / PT_PER_CM, 5, 120, " cm", 0.1, 2)
        self.orientation = QComboBox()
        self.orientation.addItems(["Portrait", "Landscape"])
        self.orientation.setCurrentIndex(1 if landscape else 0)
        self.size.currentIndexChanged.connect(self._size_picked)
        self.orientation.currentIndexChanged.connect(self._size_picked)
        form.addRow("Size", self.size)
        form.addRow("Width", self.width)
        form.addRow("Height", self.height)
        form.addRow("Orientation", self.orientation)
        layout.addWidget(paper)

        margins = QGroupBox("Margins")
        grid = QGridLayout(margins)
        left, top, right, bottom = (m / PT_PER_CM for m in margins_pt)
        self.m_top, self.m_bottom = _spin(top, 0, 20, " cm", 0.1, 2), _spin(bottom, 0, 20, " cm", 0.1, 2)
        self.m_left, self.m_right = _spin(left, 0, 20, " cm", 0.1, 2), _spin(right, 0, 20, " cm", 0.1, 2)
        for i, (label, box) in enumerate((("Top", self.m_top), ("Bottom", self.m_bottom), ("Left", self.m_left),
                                          ("Right", self.m_right))):
            grid.addWidget(QLabel(label), i // 2, (i % 2) * 2)
            grid.addWidget(box, i // 2, (i % 2) * 2 + 1)
        layout.addWidget(margins)
        layout.addLayout(_footer(self))

    def _size_picked(self):
        name = self.size.currentText()
        if name not in PAGE_SIZES:
            w, h = sorted((self.width.value(), self.height.value()))
        else:
            w, h = (v / PT_PER_CM for v in PAGE_SIZES[name])
        if self.orientation.currentIndex() == 1:
            w, h = h, w
        self.width.setValue(w)
        self.height.setValue(h)

    def values(self):
        """(width_pt, height_pt, (left, top, right, bottom) in points)."""
        return (self.width.value() * PT_PER_CM, self.height.value() * PT_PER_CM,
                tuple(b.value() * PT_PER_CM for b in (self.m_left, self.m_top, self.m_right, self.m_bottom)))


class WordCountDialog(QDialog):
    def __init__(self, stats, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Word Count")
        layout = _dialog_layout(self)
        layout.addLayout(_header("Word count", stats.pop("_scope", None)))
        form = QFormLayout()
        for label, value in stats.items():
            number = QLabel(f"{value:,}")
            number.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            form.addRow(label, number)
        layout.addLayout(form)
        close = _primary("Close")
        close.clicked.connect(self.accept)
        layout.addLayout(_button_row(close))


class SymbolDialog(QDialog):
    """A grid of symbols and special characters; double-click (or Insert)
    puts one in the text. `inserted` is called with each chosen character."""

    def __init__(self, inserted, font_family="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Symbol")
        self.inserted = inserted
        layout = _dialog_layout(self)
        layout.addLayout(_header("Insert a symbol", "Click a symbol to put it in the text"))
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setSpacing(2)
        font = QFont(font_family) if font_family else QFont()
        font.setPointSize(14)
        for i, ch in enumerate(SYMBOLS):
            btn = QToolButton()
            btn.setText(ch)
            btn.setFont(font)
            btn.setFixedSize(34, 34)
            btn.setToolTip(f"U+{ord(ch):04X}")
            btn.clicked.connect(lambda _c=False, c=ch: self.inserted(c))
            grid.addWidget(btn, i // 16, i % 16)
        scroll = QScrollArea()
        scroll.setWidget(holder)
        scroll.setWidgetResizable(True)
        scroll.setMinimumSize(16 * 36 + 30, 300)
        layout.addWidget(scroll, 1)
        special = QHBoxLayout()
        special.addWidget(QLabel("Special characters:"))
        self.special = QComboBox()
        for name, ch in SPECIAL_CHARACTERS:
            self.special.addItem(f"{name}   {ch if ch.strip() and ch != chr(0xad) else ''}", ch)
        special.addWidget(self.special, 1)
        put = QPushButton("Insert")
        put.clicked.connect(lambda: self.inserted(self.special.currentData()))
        special.addWidget(put)
        layout.addLayout(special)
        close = _primary("Close")
        close.clicked.connect(self.accept)
        layout.addLayout(_button_row(close))


DATE_FORMATS = ("%d/%m/%Y", "%A, %d %B %Y", "%d %B %Y", "%d-%m-%y", "%Y-%m-%d", "%d %b. %y", "%B %Y",
                "%m/%d/%Y", "%B %d, %Y", "%d/%m/%Y %H:%M", "%d/%m/%Y %I:%M:%S %p", "%H:%M", "%I:%M %p",
                "%H:%M:%S")


class DateTimeDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Date and Time")
        layout = _dialog_layout(self)
        layout.addLayout(_header("Date and time", "Pick a format"))
        now = datetime.datetime.now()
        self.list = QListWidget()
        for fmt in DATE_FORMATS:
            self.list.addItem(now.strftime(fmt))
        self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        layout.addWidget(self.list, 1)
        layout.addLayout(_footer(self, "Insert"))

    def text(self):
        item = self.list.currentItem()
        return item.text() if item else ""
