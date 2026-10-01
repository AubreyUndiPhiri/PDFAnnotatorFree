"""A scientific calculator for the side panel.

evaluate() reads an expression the way it is written on the calculator's
display (× ÷ − ^ √ π e, 5!, 20%, 2π, 3x√8 for the cube root of 8, 5 nCr 2, 2E5, Ans) with its own
small parser: nothing is handed to Python's eval. Angles are in degrees or
radians (DEG / RAD).

CalculatorPanel is the keypad: a display with the expression and its live
result, memory (MC MR M+ M−), 2nd for the inverse functions, a history you
can click to reuse, and the keyboard (digits, + - * / ^ ( ) ! %, Enter,
Backspace, Esc)."""
import math
import re

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from . import theme


class CalcError(ValueError):
    pass


FUNCTIONS = {
    "sin": "trig", "cos": "trig", "tan": "trig", "asin": "atrig", "acos": "atrig", "atan": "atrig",
    "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh, "asinh": math.asinh, "acosh": math.acosh,
    "atanh": math.atanh, "ln": math.log, "log": math.log10, "log2": math.log2, "exp": math.exp,
    "sqrt": math.sqrt, "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x), "abs": abs,
    "floor": math.floor, "ceil": math.ceil, "round": round,
}
_WORDS = sorted(list(FUNCTIONS) + ["pi", "e", "Ans", "nCr", "nPr", "mod", "root"], key=len, reverse=True)
_TOKEN = re.compile(r"\s*(?:(\d+\.?\d*(?:E[+-]?\d+)?|\.\d+(?:E[+-]?\d+)?)|(" + "|".join(_WORDS) + r")|(.))")
_DISPLAY_TO_PLAIN = {"×": "*", "÷": "/", "−": "-", "π": " pi ", "ˣ√": " root ", "x√": " root ", "∛": " cbrt ", "²": "^2",
                     "³": "^3", "⁻¹": "^(-1)", "·": "*"}


def _tokens(text):
    for a, b in _DISPLAY_TO_PLAIN.items():
        text = text.replace(a, b)
    text = text.replace("**", "^")
    out, pos = [], 0
    text = text.strip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            break
        pos = m.end()
        number, word, other = m.groups()
        if number is not None:
            out.append(("num", float(number)))
        elif word is not None:
            out.append(("word", word))
        elif other and not other.isspace():
            out.append(("op", other))
    return out


class _Parser:
    def __init__(self, tokens, degrees, ans):
        self.t, self.i, self.degrees, self.ans = tokens, 0, degrees, ans

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def expect(self, op):
        kind, value = self.take()
        if value != op:
            raise CalcError("Syntax error")

    def parse(self):
        if not self.t:
            raise CalcError("Syntax error")
        value = self.expr()
        while self.peek() == ("op", ")"):   # a stray ")" at the end: tolerate it
            self.take()
        if self.i != len(self.t):
            raise CalcError("Syntax error")
        return value

    def expr(self):
        value = self.term()
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.take()[1]
            right = self.term()
            value = value + right if op == "+" else value - right
        return value

    def _starts_value(self):
        kind, value = self.peek()
        return kind == "num" or (kind == "word" and value not in ("nCr", "nPr", "mod", "root")) or value in ("(", "√")

    def term(self):
        value = self.unary()
        while True:
            kind, op = self.peek()
            if op in ("*", "/") or (kind == "word" and op in ("mod", "nCr", "nPr")):
                self.take()
                right = self.unary()
                if op == "*":
                    value *= right
                elif op == "/":
                    if right == 0:
                        raise CalcError("Can't divide by zero")
                    value /= right
                elif op == "mod":
                    if right == 0:
                        raise CalcError("Can't divide by zero")
                    value = math.fmod(value, right)
                else:
                    value = self._combination(value, right, op == "nCr")
            elif self._starts_value():   # 2π, 3(4+1), 2sin(30): multiplication without ×
                value *= self.unary()
            else:
                return value

    @staticmethod
    def _combination(n, r, choose):
        if n != int(n) or r != int(r) or n < 0 or r < 0 or r > n:
            raise CalcError("Math error")
        return float(math.comb(int(n), int(r)) if choose else math.perm(int(n), int(r)))

    def unary(self):
        kind, op = self.peek()
        if op == "-":
            self.take()
            return -self.unary()
        if op == "+":
            self.take()
            return self.unary()
        return self.power()

    def power(self):
        base = self.postfix()
        kind, op = self.peek()
        if op == "^":
            self.take()
            exponent = self.unary()   # right to left: 2^3^2 = 2^9
            try:
                result = base ** exponent
            except (OverflowError, ZeroDivisionError):
                raise CalcError("Math error")
            if isinstance(result, complex):
                raise CalcError("Math error")
            return result
        if op == "root":   # 3ˣ√8: the cube root of 8
            self.take()
            radicand = self.unary()
            if base == 0:
                raise CalcError("Math error")
            if radicand < 0 and int(base) == base and int(base) % 2 == 1:
                return -((-radicand) ** (1 / base))
            if radicand < 0:
                raise CalcError("Math error")
            return radicand ** (1 / base)
        return base

    def postfix(self):
        value = self.primary()
        while True:
            kind, op = self.peek()
            if op == "!":
                self.take()
                if value < 0 or value > 170:
                    raise CalcError("Math error")
                value = float(math.factorial(int(value))) if value == int(value) else math.gamma(value + 1)
            elif op == "%":
                self.take()
                value /= 100
            else:
                return value

    def primary(self):
        kind, value = self.take()
        if kind == "num":
            return value
        if value == "(":
            inner = self.expr()
            if self.peek() == ("op", ")"):
                self.take()
            return inner                     # an unclosed "(" closes at the end, as on a calculator
        if value == "√":
            return self._apply(math.sqrt, self._argument())
        if kind == "word":
            if value == "pi":
                return math.pi
            if value == "e":
                return math.e
            if value == "Ans":
                return self.ans
            if value in FUNCTIONS:
                return self._call(value, self._argument())
        raise CalcError("Syntax error")

    def _argument(self):
        if self.peek() == ("op", "("):
            return self.primary()
        return self.power()                  # sin 30, √9, ln e

    @staticmethod
    def _apply(fn, x):
        try:
            return fn(x)
        except (ValueError, OverflowError):
            raise CalcError("Math error")

    def _call(self, name, x):
        kind = FUNCTIONS[name]
        if kind == "trig":
            fn = getattr(math, name)
            angle = math.radians(x) if self.degrees else x
            if name == "tan" and abs(math.cos(angle)) < 1e-15:
                raise CalcError("Math error")
            result = fn(angle)
            return 0.0 if abs(result) < 1e-15 else result
        if kind == "atrig":
            result = self._apply(getattr(math, name), x)
            return math.degrees(result) if self.degrees else result
        return self._apply(kind, x)


def evaluate(text, degrees=True, ans=0.0):
    """The value of a calculator expression; CalcError when it has none."""
    value = _Parser(_tokens(text), degrees, ans).parse()
    if isinstance(value, complex) or math.isnan(value):
        raise CalcError("Math error")
    if math.isinf(value):
        raise CalcError("Overflow")
    return value


def format_number(value):
    """Up to 12 significant digits; very big or small numbers as 1.5E20."""
    if value == 0:
        return "0"
    if abs(value) >= 1e15 or abs(value) < 1e-9:
        mantissa, exponent = f"{value:.10e}".split("e")
        mantissa = mantissa.rstrip("0").rstrip(".")
        return f"{mantissa}E{int(exponent)}"
    text = f"{value:.12g}"
    if "e" in text:
        text = f"{value:.12f}".rstrip("0").rstrip(".")
    return text.replace("-", "−")


# ---------------------------------------------------------------------------
# key: (label, inserted text, role); role styles the key
_KEYS = [
    [("2nd", "#2nd", "mode"), ("DEG", "#angle", "mode"), ("MC", "#mc", "mem"), ("MR", "#mr", "mem"),
     ("M+", "#m+", "mem")],
    [("sin", "sin(", "fn"), ("cos", "cos(", "fn"), ("tan", "tan(", "fn"), ("π", "π", "fn"), ("e", "e", "fn")],
    [("x²", "²", "fn"), ("x³", "³", "fn"), ("x^y", "^", "fn"), ("√", "√(", "fn"), ("y√x", "x√", "fn")],
    [("ln", "ln(", "fn"), ("log", "log(", "fn"), ("e^x", "e^", "fn"), ("10^x", "10^", "fn"), ("1/x", "⁻¹", "fn")],
    [("n!", "!", "fn"), ("nCr", " nCr ", "fn"), ("nPr", " nPr ", "fn"), ("|x|", "abs(", "fn"), ("mod", " mod ", "fn")],
    [("(", "(", "fn"), (")", ")", "fn"), ("%", "%", "fn"), ("EXP", "E", "fn"), ("DEL", "#back", "clear")],
    [("7", "7", "digit"), ("8", "8", "digit"), ("9", "9", "digit"), ("÷", "÷", "op"), ("AC", "#clear", "clear")],
    [("4", "4", "digit"), ("5", "5", "digit"), ("6", "6", "digit"), ("×", "×", "op"), ("Ans", "Ans", "fn")],
    [("1", "1", "digit"), ("2", "2", "digit"), ("3", "3", "digit"), ("−", "−", "op"), ("±", "#neg", "fn")],
    [("0", "0", "digit"), (".", ".", "digit"), ("M−", "#m-", "mem"), ("+", "+", "op"), ("=", "#equals", "equals")],
]
# with 2nd on, these keys give their inverse
_SECOND = {"sin": ("asin", "asin("), "cos": ("acos", "acos("), "tan": ("atan", "atan("),
           "ln": ("sinh", "sinh("), "log": ("cosh", "cosh("), "e^x": ("tanh", "tanh("),
           "10^x": ("log2", "log2("), "x²": ("∛x", "∛("), "x³": ("floor", "floor("), "|x|": ("ceil", "ceil(")}


class CalculatorPanel(QWidget):
    """The calculator, for a side panel (or a window of its own)."""

    result_copied = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("calcPanel")
        settings = theme._settings()
        self.degrees = settings.value("calc/degrees", "true") == "true"
        self.second = False
        try:
            self.memory = float(settings.value("calc/memory", 0.0))
        except (TypeError, ValueError):
            self.memory = 0.0
        self.ans = 0.0
        self._just_evaluated = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        display = QFrame()
        display.setObjectName("calcDisplay")
        box = QVBoxLayout(display)
        box.setContentsMargins(14, 10, 14, 12)
        box.setSpacing(2)
        status = QHBoxLayout()
        self.mode_label = QLabel()
        self.mode_label.setObjectName("calcStatus")
        self.memory_label = QLabel()
        self.memory_label.setObjectName("calcStatus")
        status.addWidget(self.mode_label)
        status.addStretch(1)
        status.addWidget(self.memory_label)
        box.addLayout(status)
        self.entry = QLineEdit()
        self.entry.setObjectName("calcEntry")
        self.entry.setAlignment(Qt.AlignRight)
        self.entry.setPlaceholderText("0")
        self.entry.textChanged.connect(self._preview)
        self.entry.returnPressed.connect(self.equals)
        self.entry.installEventFilter(self)
        box.addWidget(self.entry)
        self.result = QLabel("0")
        self.result.setObjectName("calcResult")
        self.result.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.result.setToolTip("Click Copy (or Ctrl+Shift+C) to copy the result")
        box.addWidget(self.result)
        outer.addWidget(display)

        grid = QGridLayout()
        grid.setSpacing(6)
        self.buttons = {}
        for r, row in enumerate(_KEYS):
            for c, (label, text, role) in enumerate(row):
                btn = QPushButton(label)
                btn.setObjectName("calcKey")
                btn.setProperty("role", role)
                btn.setFocusPolicy(Qt.NoFocus)
                btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                btn.setMinimumSize(44, 34)
                btn.clicked.connect(lambda _c=False, l=label: self.press(l))
                grid.addWidget(btn, r, c)
                self.buttons[label] = btn
        outer.addLayout(grid, 1)

        row = QHBoxLayout()
        history_title = QLabel("History")
        history_title.setObjectName("calcStatus")
        row.addWidget(history_title)
        row.addStretch(1)
        copy = QPushButton("Copy")
        copy.setObjectName("calcLink")
        copy.setFocusPolicy(Qt.NoFocus)
        copy.clicked.connect(self.copy_result)
        clear = QPushButton("Clear")
        clear.setObjectName("calcLink")
        clear.setFocusPolicy(Qt.NoFocus)
        clear.clicked.connect(lambda: self.history.clear())
        row.addWidget(copy)
        row.addWidget(clear)
        outer.addLayout(row)
        self.history = QListWidget()
        self.history.setObjectName("calcHistory")
        self.history.setMaximumHeight(130)
        self.history.itemClicked.connect(self._reuse)
        outer.addWidget(self.history)
        self._refresh_labels()

    # ---- keys
    def _key_text(self, label):
        for row in _KEYS:
            for key_label, text, _role in row:
                if key_label == label:
                    if self.second and label in _SECOND:
                        return _SECOND[label][1]
                    return text
        return label

    def press(self, label):
        text = self._key_text(label)
        if text == "#2nd":
            self.second = not self.second
            self._refresh_labels()
            return
        if text == "#angle":
            self.degrees = not self.degrees
            theme._settings().setValue("calc/degrees", "true" if self.degrees else "false")
            self._refresh_labels()
            self._preview()
            return
        if text == "#clear":
            self.entry.clear()
            self.result.setText("0")
        elif text == "#back":
            self.entry.backspace()
        elif text == "#equals":
            self.equals()
        elif text == "#neg":
            self._negate()
        elif text in ("#mc", "#mr", "#m+", "#m-"):
            self._memory(text)
        else:
            self.insert(text)
        if self.second and label in _SECOND:
            self.second = False
            self._refresh_labels()

    def insert(self, text):
        if self._just_evaluated:   # after =, an operator carries on from the answer; a number starts afresh
            self._just_evaluated = False
            if text and text[0] in "+−×÷^²³!%⁻ˣx" or text.strip() in ("nCr", "nPr", "mod"):
                self.entry.setText("Ans")
            else:
                self.entry.clear()
        self.entry.insert(text)
        self.entry.setFocus()

    def _negate(self):
        """± : the whole entry changes sign (an empty one starts with −)."""
        text = self.entry.text()
        self._just_evaluated = False
        if not text:
            self.entry.setText("−")
        elif text.startswith("−(") and text.endswith(")"):
            self.entry.setText(text[2:-1])
        else:
            self.entry.setText("−(" + text + ")")

    def _memory(self, kind):
        if kind == "#mc":
            self.memory = 0.0
        elif kind == "#mr":
            self.insert(format_number(self.memory))
        else:
            try:
                if self._just_evaluated or not self.entry.text().strip():
                    value = self.ans           # the answer on show
                else:
                    value = evaluate(self.entry.text(), self.degrees, self.ans)
            except CalcError:
                return
            self.memory += value if kind == "#m+" else -value
        theme._settings().setValue("calc/memory", self.memory)
        self._refresh_labels()

    def equals(self):
        expression = self.entry.text().strip()
        if not expression:
            return
        try:
            value = evaluate(expression, self.degrees, self.ans)
        except (CalcError, ValueError, OverflowError) as e:
            self.result.setText(str(e) if isinstance(e, CalcError) else "Math error")
            self.result.setProperty("error", True)
            self._repolish(self.result)
            return
        self.ans = value
        shown = format_number(value)
        self.result.setText(shown)
        self.result.setProperty("error", False)
        self._repolish(self.result)
        item = QListWidgetItem(f"{expression}  =  {shown}")
        item.setData(Qt.UserRole, (expression, value))
        self.history.insertItem(0, item)
        self._just_evaluated = True

    def _preview(self, *_):
        """The result shows (muted) as you type."""
        text = self.entry.text().strip()
        if not text:
            self.result.setText("0")
            return
        try:
            value = evaluate(text, self.degrees, self.ans)
        except (CalcError, ValueError, OverflowError, RecursionError):
            return
        self.result.setText(format_number(value))
        self.result.setProperty("error", False)
        self._repolish(self.result)

    def _reuse(self, item):
        expression, value = item.data(Qt.UserRole)
        self.entry.setText(expression)
        self.ans = value
        self._just_evaluated = False

    def copy_result(self):
        text = self.result.text().replace("−", "-")
        QGuiApplication.clipboard().setText(text)
        self.result_copied.emit(text)

    def _refresh_labels(self):
        self.mode_label.setText(("DEG" if self.degrees else "RAD") + ("   2nd" if self.second else ""))
        self.memory_label.setText(f"M = {format_number(self.memory)}" if self.memory else "")
        self.buttons["DEG"].setText("RAD" if not self.degrees else "DEG")
        self.buttons["2nd"].setProperty("active", self.second)
        self._repolish(self.buttons["2nd"])
        for label, (second_label, _text) in _SECOND.items():
            self.buttons[label].setText(second_label if self.second else label)

    @staticmethod
    def _repolish(widget):
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    # ---- keyboard
    def eventFilter(self, obj, event):
        if obj is self.entry and event.type() == QEvent.KeyPress:
            key, text = event.key(), event.text()
            if key == Qt.Key_Escape:
                self.press("AC")
                return True
            if event.matches(QKeySequence.Copy) and not self.entry.hasSelectedText():
                self.copy_result()
                return True
            mapping = {"*": "×", "/": "÷", "-": "−"}
            if text in mapping:
                self.insert(mapping[text])
                return True
            if text and (text in "+^!%()." or text.isdigit()) and self._just_evaluated:
                self.insert(text)
                return True
        return False

    def keyPressEvent(self, event):
        self.entry.setFocus()
        self.entry.event(event)

