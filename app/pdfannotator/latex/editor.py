"""The LaTeX tab: edit .tex sources, compile them and preview the PDF,
all inside the app, with the option to use an external compiler or editor.

- Code editor: syntax highlighting (both themes), line numbers, error
  markers, auto-indent, \\begin/\\end completion, command / label / citation
  completion, comment toggling and snippet menus.
- Compile: XeLaTeX, pdfLaTeX, LuaLaTeX, latexmk, Tectonic or a custom
  command (engines.py), run with QProcess so the UI stays responsive; the log
  becomes a clickable list of problems.
- Preview: the compiled PDF, rendered lazily; double-click a spot to jump to
  its source line (SyncTeX), Ctrl+J jumps from the source to the PDF.
- External: open the file in TeXworks / TeXstudio / VS Code / any program,
  package the project for Overleaf. Changes made outside are reloaded.
"""
import os
import re
import time
import zipfile

import pymupdf as fitz
from PySide6.QtCore import (
    QEvent, QFileSystemWatcher, QPoint, QProcess, QProcessEnvironment, QRect, QSettings, QSize, QStringListModel, Qt,
    QTimer, QUrl, Signal,
)
from PySide6.QtGui import (
    QAction, QColor, QDesktopServices, QFont, QFontDatabase, QImage, QKeySequence, QPainter, QPixmap,
    QSyntaxHighlighter, QTextCharFormat, QTextCursor, QTextFormat,
)
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QCompleter, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
    QSplitter, QTabWidget, QTextEdit, QToolBar, QToolButton, QToolTip, QVBoxLayout, QWidget,
)

from .. import icons, theme
from ..dialogs import _button_row, _dialog_layout, _header, _primary
from ..editor_tab import EditorTab, FindReplaceBar, PaperCanvas
from . import engines

OPEN_SUFFIXES = (".tex", ".bib", ".sty", ".cls", ".ltx", ".bbx", ".cbx")

TEMPLATES = {
    "Article": r"""\documentclass[11pt]{article}
\usepackage[margin=2.5cm]{geometry}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}
\usepackage[hidelinks]{hyperref}

\title{Title}
\author{Author}
\date{\today}

\begin{document}
\maketitle

\section{Introduction}
|

\end{document}
""",
    "Report": r"""\documentclass[12pt]{report}
\usepackage[margin=2.5cm]{geometry}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}
\usepackage[hidelinks]{hyperref}

\title{Report Title}
\author{Author}
\date{\today}

\begin{document}
\maketitle
\tableofcontents

\chapter{Introduction}
|

\end{document}
""",
    "Letter": r"""\documentclass[11pt]{letter}
\usepackage[margin=2.5cm]{geometry}
\signature{Your Name}
\address{Your Address \\ City}

\begin{document}
\begin{letter}{Recipient \\ Address}
\opening{Dear Sir or Madam,}

|

\closing{Yours faithfully,}
\end{letter}
\end{document}
""",
    "Presentation (Beamer)": r"""\documentclass{beamer}
\usetheme{Madrid}

\title{Presentation Title}
\author{Author}
\date{\today}

\begin{document}

\begin{frame}
  \titlepage
\end{frame}

\begin{frame}{First slide}
  \begin{itemize}
    \item |
  \end{itemize}
\end{frame}

\end{document}
""",
    "Blank": r"""\documentclass{article}

\begin{document}
|
\end{document}
""",
}

# (menu, label, snippet): "|" marks where the cursor goes, "{sel}" the selected text
SNIPPETS = [
    ("Structure", "Part", r"\part{|}"), ("Structure", "Chapter", r"\chapter{|}"),
    ("Structure", "Section", r"\section{|}"), ("Structure", "Subsection", r"\subsection{|}"),
    ("Structure", "Subsubsection", r"\subsubsection{|}"), ("Structure", "Paragraph", r"\paragraph{|}"),
    ("Structure", "Table of contents", r"\tableofcontents"),
    ("Format", "Bold", r"\textbf{{sel}|}"), ("Format", "Italic", r"\textit{{sel}|}"),
    ("Format", "Emphasis", r"\emph{{sel}|}"), ("Format", "Underline", r"\underline{{sel}|}"),
    ("Format", "Typewriter", r"\texttt{{sel}|}"), ("Format", "Small caps", r"\textsc{{sel}|}"),
    ("Format", "Centered", "\\begin{center}\n  {sel}|\n\\end{center}"),
    ("Format", "Footnote", r"\footnote{|}"), ("Format", "New page", r"\newpage"),
    ("Lists", "Bulleted list", "\\begin{itemize}\n  \\item |\n\\end{itemize}"),
    ("Lists", "Numbered list", "\\begin{enumerate}\n  \\item |\n\\end{enumerate}"),
    ("Lists", "Description list", "\\begin{description}\n  \\item[|] \n\\end{description}"),
    ("Lists", "Item", r"\item |"),
    ("Math", "Inline math", r"${sel}|$"), ("Math", "Display math", "\\[\n  {sel}|\n\\]"),
    ("Math", "Numbered equation", "\\begin{equation}\n  {sel}|\n  \\label{eq:}\n\\end{equation}"),
    ("Math", "Aligned equations", "\\begin{align}\n  | &= \\\\\n    &= \n\\end{align}"),
    ("Math", "Fraction", r"\frac{|}{}"), ("Math", "Square root", r"\sqrt{|}"),
    ("Math", "Sum", r"\sum_{i=1}^{n} |"), ("Math", "Integral", r"\int_{a}^{b} | \, dx"),
    ("Math", "Limit", r"\lim_{x \to \infty} |"), ("Math", "Matrix", "\\begin{pmatrix}\n  | & \\\\\n   & \n\\end{pmatrix}"),
    ("Math", "Cases", "\\begin{cases}\n  | & \\text{if } \\\\\n   & \\text{otherwise}\n\\end{cases}"),
    ("Insert", "Figure", "\\begin{figure}[htbp]\n  \\centering\n  \\includegraphics[width=0.8\\linewidth]{|}\n"
                         "  \\caption{}\n  \\label{fig:}\n\\end{figure}"),
    ("Insert", "Table", "\\begin{table}[htbp]\n  \\centering\n  \\begin{tabular}{|l|l|l|}\n    \\hline\n"
                        "    | &  &  \\\\\n    \\hline\n  \\end{tabular}\n  \\caption{}\n  \\label{tab:}\n\\end{table}"),
    ("Insert", "Label", r"\label{|}"), ("Insert", "Reference", r"\ref{|}"), ("Insert", "Citation", r"\cite{|}"),
    ("Insert", "Link", r"\href{|}{}"), ("Insert", "Verbatim", "\\begin{verbatim}\n{sel}|\n\\end{verbatim}"),
]
GREEK = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta", "lambda", "mu", "pi", "rho",
         "sigma", "tau", "phi", "chi", "psi", "omega", "Gamma", "Delta", "Theta", "Lambda", "Pi", "Sigma", "Phi",
         "Psi", "Omega"]
COMMANDS = sorted({
    "documentclass", "usepackage", "begin", "end", "section", "subsection", "subsubsection", "chapter", "part",
    "paragraph", "title", "author", "date", "maketitle", "tableofcontents", "textbf", "textit", "emph", "underline",
    "texttt", "textsc", "footnote", "label", "ref", "eqref", "pageref", "cite", "citep", "citet", "item",
    "includegraphics", "caption", "centering", "hline", "cline", "multicolumn", "multirow", "frac", "dfrac", "sqrt",
    "sum", "prod", "int", "iint", "oint", "lim", "infty", "partial", "nabla", "cdot", "times", "leq", "geq", "neq",
    "approx", "equiv", "rightarrow", "leftarrow", "Rightarrow", "Leftrightarrow", "mathbb", "mathcal", "mathrm",
    "mathbf", "text", "left", "right", "quad", "qquad", "hspace", "vspace", "newpage", "clearpage", "noindent",
    "linewidth", "textwidth", "small", "large", "Large", "LARGE", "huge", "normalsize", "footnotesize", "href", "url",
    "newcommand", "renewcommand", "bibliography", "bibliographystyle", "printbibliography", "addbibresource", "input",
    "include", "appendix", "today", "hfill", "vfill", "centering", "raggedright", "setlength", "textcolor",
    *GREEK,
})


def _settings():
    return QSettings(QSettings.defaultFormat(), QSettings.UserScope, "AupedeanAnnotator", "AupedeanAnnotator")


def _monospace(size=11):
    font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    for family in ("Cascadia Mono", "Consolas", "Courier New"):
        if family in QFontDatabase.families():
            font = QFont(family)
            break
    font.setPointSize(size)
    font.setStyleHint(QFont.Monospace)
    return font


# --------------------------------------------------------------------------
# Syntax highlighting
# --------------------------------------------------------------------------

_COLORS = {
    theme.LIGHT: {"command": "#4f46e5", "env": "#0f766e", "math": "#b45309", "comment": "#6b7280",
                  "brace": "#9333ea", "special": "#dc2626", "marker": "#fde68a"},
    theme.DARK: {"command": "#a99bff", "env": "#5eead4", "math": "#fbbf24", "comment": "#8b93a7",
                 "brace": "#f0abfc", "special": "#f87171", "marker": "#5b4a1a"},
}
_RE_ENV = re.compile(r"\\(begin|end)\s*\{([^}]*)\}")
_RE_COMMAND = re.compile(r"\\(?:[A-Za-z@]+\*?|.)")
_RE_INLINE_MATH = re.compile(r"(?<!\\)\$(?:\\.|[^$\\])+(?<!\\)\$|\\\((?:.)*?\\\)")
_RE_BRACE = re.compile(r"(?<!\\)[{}\[\]]")
_RE_SPECIAL = re.compile(r"(?<!\\)(?:&|~|\\\\)")
_RE_COMMENT = re.compile(r"(?<!\\)%.*$")
_IN_DISPLAY_MATH = 1


class LatexHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self._formats = {}
        self.refresh_colors()

    def refresh_colors(self):
        colors = _COLORS.get(theme.mode, _COLORS[theme.LIGHT])
        self._formats = {}
        for key, value in colors.items():
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(value))
            if key == "env":
                fmt.setFontWeight(QFont.DemiBold)
            if key == "comment":
                fmt.setFontItalic(True)
            self._formats[key] = fmt
        self.rehighlight()

    def highlightBlock(self, text):
        f = self._formats
        # display math that runs over several lines: \[ ... \] and $$ ... $$
        state = self.previousBlockState()
        pos = 0
        if state == _IN_DISPLAY_MATH:
            end = self._display_end(text, 0)
            if end < 0:
                self.setFormat(0, len(text), f["math"])
                self.setCurrentBlockState(_IN_DISPLAY_MATH)
                self._commands(text, 0, len(text))
                self._comments(text)
                return
            self.setFormat(0, end, f["math"])
            pos = end
        self.setCurrentBlockState(0)
        while True:
            m = re.compile(r"(?<!\\)(\\\[|\$\$)").search(text, pos)
            if not m:
                break
            end = self._display_end(text, m.end())
            if end < 0:
                self.setFormat(m.start(), len(text) - m.start(), f["math"])
                self.setCurrentBlockState(_IN_DISPLAY_MATH)
                break
            self.setFormat(m.start(), end - m.start(), f["math"])
            pos = end
        for m in _RE_INLINE_MATH.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), f["math"])
        self._commands(text, 0, len(text))
        for m in _RE_ENV.finditer(text):
            self.setFormat(m.start(), len(m.group(1)) + 1, f["command"])
            self.setFormat(m.start(2), len(m.group(2)), f["env"])
        for m in _RE_BRACE.finditer(text):
            self.setFormat(m.start(), 1, f["brace"])
        for m in _RE_SPECIAL.finditer(text):
            self.setFormat(m.start(), len(m.group()), f["special"])
        self._comments(text)

    @staticmethod
    def _display_end(text, start):
        m = re.compile(r"(?<!\\)(\\\]|\$\$)").search(text, start)
        return m.end() if m else -1

    def _commands(self, text, start, end):
        for m in _RE_COMMAND.finditer(text, start, end):
            self.setFormat(m.start(), m.end() - m.start(), self._formats["command"])

    def _comments(self, text):
        m = _RE_COMMENT.search(text)
        if m:
            self.setFormat(m.start(), len(text) - m.start(), self._formats["comment"])


# --------------------------------------------------------------------------
# Code editor
# --------------------------------------------------------------------------

class _LineNumbers(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_gutter(event)

    def mousePressEvent(self, event):
        block = self.editor.cursorForPosition(QPoint(0, int(event.position().y()))).block()
        problem = self.editor.markers.get(block.blockNumber() + 1)
        if problem:
            QToolTip.showText(event.globalPosition().toPoint(), problem.message, self)


class CodeEditor(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("latexCode")
        self.setFont(_monospace(int(_settings().value("latex/font_size", 11))))
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 2)
        self.gutter = _LineNumbers(self)
        self.markers = {}      # line -> engines.Problem
        self.key_actions = {}  # "F5" -> QAction
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self.highlighter = LatexHighlighter(self.document())
        self._update_gutter_width()
        self._highlight_current_line()
        self._completer = QCompleter(self)
        self._completer.setWidget(self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.activated.connect(self._insert_completion)
        self._completion_model = QStringListModel(self)
        self._completer.setModel(self._completion_model)

    # ---- gutter ------------------------------------------------------------
    def gutter_width(self):
        digits = len(str(max(1, self.blockCount())))
        return 18 + self.fontMetrics().horizontalAdvance("9") * max(3, digits)

    def _update_gutter_width(self, *_):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def paint_gutter(self, event):
        painter = QPainter(self.gutter)
        painter.fillRect(event.rect(), QColor(theme.SURFACE_ALT if theme.mode == theme.LIGHT else "#161923"))
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        current = self.textCursor().blockNumber()
        colors = {"error": QColor(theme.DANGER), "warning": QColor("#f59e0b"), "badbox": QColor("#9ca3af")}
        while block.isValid() and top <= event.rect().bottom():
            height = round(self.blockBoundingRect(block).height())
            if block.isVisible() and top + height >= event.rect().top():
                problem = self.markers.get(number + 1)
                if problem:
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(colors.get(problem.kind, colors["error"]))
                    painter.drawEllipse(4, top + self.fontMetrics().height() // 2 - 3, 7, 7)
                painter.setPen(QColor(theme.TEXT if number == current else theme.TEXT_MUTED))
                painter.drawText(0, top, self.gutter.width() - 6, self.fontMetrics().height(), Qt.AlignRight,
                                 str(number + 1))
            block = block.next()
            top += height
            number += 1

    def set_markers(self, problems):
        self.markers = {}
        for p in problems:
            if p.line and (p.line not in self.markers or p.kind == "error"):
                self.markers[p.line] = p
        self.gutter.update()
        self._highlight_current_line()

    def _highlight_current_line(self):
        selections = []
        line = QTextEdit.ExtraSelection()
        line.format.setBackground(QColor(theme.ACCENT_SOFT))
        line.format.setProperty(QTextFormat.FullWidthSelection, True)
        line.cursor = self.textCursor()
        line.cursor.clearSelection()
        selections.append(line)
        for number, problem in self.markers.items():
            if problem.kind != "error":
                continue
            block = self.document().findBlockByNumber(number - 1)
            if not block.isValid():
                continue
            sel = QTextEdit.ExtraSelection()
            sel.format.setUnderlineStyle(QTextCharFormat.WaveUnderline)
            sel.format.setUnderlineColor(QColor(theme.DANGER))
            sel.cursor = QTextCursor(block)
            sel.cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
            selections.append(sel)
        self.setExtraSelections(selections)

    def go_to_line(self, line, column=0):
        block = self.document().findBlockByNumber(max(0, line - 1))
        cursor = QTextCursor(block)
        cursor.movePosition(QTextCursor.Right, n=min(column, max(0, block.length() - 1)))
        self.setTextCursor(cursor)
        self.centerCursor()
        self.setFocus()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.PaletteChange, QEvent.StyleChange):
            self.highlighter.refresh_colors()
            self._highlight_current_line()

    # ---- keys ---------------------------------------------------------------
    def _action_for(self, event):
        return self.key_actions.get(QKeySequence(event.keyCombination()).toString())

    def event(self, event):
        if event.type() == QEvent.ShortcutOverride and self._action_for(event) is not None:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        popup = self._completer.popup()
        if popup.isVisible() and event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab, Qt.Key_Escape):
            if event.key() == Qt.Key_Escape:
                popup.hide()
                return
            index = popup.currentIndex()
            if index.isValid():
                self._insert_completion(index.data())
            popup.hide()
            return
        action = self._action_for(event)
        if action is not None and action.isEnabled():
            action.trigger()
            return
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers() & (Qt.ControlModifier | Qt.AltModifier):
            self._newline()
            return
        if key == Qt.Key_Tab and not event.modifiers():
            cursor = self.textCursor()
            if cursor.hasSelection():
                self._indent_selection(1)
            else:
                cursor.insertText("  ")
            return
        if key == Qt.Key_Backtab:
            self._indent_selection(-1)
            return
        if event.text() == "{" and self._next_char() in ("", " ", "\t", "\u2029", "}", ")", "]", ","):
            cursor = self.textCursor()
            selected = cursor.selectedText()
            cursor.insertText("{" + selected + "}")
            if not selected:
                cursor.movePosition(QTextCursor.Left)
                self.setTextCursor(cursor)
            self._update_completion()
            return
        super().keyPressEvent(event)
        if event.text() and (event.text().isalnum() or event.text() in "\\{,"):
            self._update_completion()
        elif popup.isVisible() and key not in (Qt.Key_Up, Qt.Key_Down, Qt.Key_Shift):
            popup.hide()

    def _next_char(self):
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor)
        return cursor.selectedText()

    def _newline(self):
        cursor = self.textCursor()
        line = cursor.block().text()
        indent = re.match(r"\s*", line).group()
        before = line[:cursor.positionInBlock()]
        m = re.search(r"\\begin\{([^}]+)\}\s*$", before)
        cursor.beginEditBlock()
        if m and not self._has_matching_end(cursor.block(), m.group(1), indent):
            cursor.insertText("\n" + indent + "  ")
            keep = cursor.position()
            cursor.insertText("\n" + indent + "\\end{" + m.group(1) + "}")
            cursor.setPosition(keep)
            self.setTextCursor(cursor)
        else:
            if re.search(r"\\item\b", before) is None and before.rstrip().endswith("{"):
                indent += "  "
            cursor.insertText("\n" + indent)
        cursor.endEditBlock()
        self.ensureCursorVisible()

    def _has_matching_end(self, block, env, indent):
        """True if an \\end{env} already closes this \\begin (so don't add one)."""
        depth = 0
        block = block.next()
        for _ in range(400):
            if not block.isValid():
                return False
            text = block.text()
            depth += len(re.findall(r"\\begin\{" + re.escape(env) + r"\}", text))
            ends = len(re.findall(r"\\end\{" + re.escape(env) + r"\}", text))
            if ends > depth:
                return True
            depth -= ends
            block = block.next()
        return False

    def _indent_selection(self, step):
        cursor = self.textCursor()
        doc = self.document()
        first = doc.findBlock(cursor.selectionStart())
        last = doc.findBlock(max(cursor.selectionStart(), cursor.selectionEnd() - 1))
        cursor.beginEditBlock()
        block = first
        while block.isValid():
            c = QTextCursor(block)
            if step > 0:
                c.insertText("  ")
            else:
                text = block.text()
                remove = len(text) - len(text.lstrip(" "))
                c.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, min(2, remove))
                c.removeSelectedText()
            if block == last:
                break
            block = block.next()
        cursor.endEditBlock()

    def toggle_comment(self):
        cursor = self.textCursor()
        doc = self.document()
        first = doc.findBlock(cursor.selectionStart())
        last = doc.findBlock(max(cursor.selectionStart(), cursor.selectionEnd() - (1 if cursor.hasSelection() else 0)))
        blocks = []
        block = first
        while block.isValid():
            blocks.append(block)
            if block == last:
                break
            block = block.next()
        commented = all(b.text().lstrip().startswith("%") or not b.text().strip() for b in blocks)
        cursor.beginEditBlock()
        for b in blocks:
            text = b.text()
            c = QTextCursor(b)
            if commented:
                i = text.find("%")
                if i >= 0:
                    c.setPosition(b.position() + i)
                    c.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, 2 if text[i + 1:i + 2] == " " else 1)
                    c.removeSelectedText()
            elif text.strip():
                c.setPosition(b.position() + len(text) - len(text.lstrip()))
                c.insertText("% ")
        cursor.endEditBlock()

    def insert_snippet(self, snippet):
        cursor = self.textCursor()
        selected = cursor.selectedText().replace("\u2029", "\n")
        indent = re.match(r"\s*", cursor.block().text()).group()
        text = snippet.replace("{sel}", selected).replace("\n", "\n" + indent)
        caret = text.find("|")
        text = text.replace("|", "", 1)
        start = cursor.selectionStart()
        cursor.insertText(text)
        if caret >= 0:
            cursor.setPosition(start + caret)
            self.setTextCursor(cursor)
        self.setFocus()

    # ---- completion ------------------------------------------------------------
    def _update_completion(self):
        cursor = self.textCursor()
        before = cursor.block().text()[:cursor.positionInBlock()]
        m_ref = re.search(r"\\(?:ref|eqref|pageref|autoref|cref|Cref)\{([^}\s]*)$", before)
        m_cite = re.search(r"\\(?:cite|citep|citet|parencite|textcite|autocite)(?:\[[^]]*\])*\{(?:[^}]*,)?\s*([^,}\s]*)$",
                           before)
        m_cmd = re.search(r"\\([A-Za-z]{2,})$", before)
        if m_ref:
            words, prefix = self.labels(), m_ref.group(1)
        elif m_cite:
            words, prefix = self.citation_keys(), m_cite.group(1)
        elif m_cmd:
            words, prefix = COMMANDS, m_cmd.group(1)
        else:
            self._completer.popup().hide()
            return
        self._completion_model.setStringList(words)
        self._completer.setCompletionPrefix(prefix)
        if self._completer.completionCount() == 0 or (self._completer.completionCount() == 1 and
                                                      self._completer.currentCompletion() == prefix):
            self._completer.popup().hide()
            return
        rect = self.cursorRect()
        rect.setWidth(260)
        self._completer.popup().setCurrentIndex(self._completer.completionModel().index(0, 0))
        self._completer.complete(rect)

    def _insert_completion(self, word):
        prefix = self._completer.completionPrefix()
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.Left, QTextCursor.KeepAnchor, len(prefix))
        cursor.insertText(word)
        self.setTextCursor(cursor)

    def labels(self):
        return sorted(set(re.findall(r"\\label\{([^}]+)\}", self.toPlainText())))

    def citation_keys(self):
        keys = set(re.findall(r"\\bibitem(?:\[[^]]*\])?\{([^}]+)\}", self.toPlainText()))
        project_dir = getattr(self, "project_dir", None)
        folder = project_dir() if callable(project_dir) else None
        if folder:
            for name in re.findall(r"\\(?:bibliography|addbibresource)\{([^}]+)\}", self.toPlainText()):
                for part in name.split(","):
                    path = os.path.join(folder, part.strip())
                    if not path.lower().endswith(".bib"):
                        path += ".bib"
                    try:
                        with open(path, encoding="utf-8", errors="replace") as f:
                            keys.update(re.findall(r"@\w+\s*\{\s*([^,\s]+)\s*,", f.read()))
                    except OSError:
                        pass
        return sorted(keys)


# --------------------------------------------------------------------------
# PDF preview
# --------------------------------------------------------------------------

class _PreviewPage(QLabel):
    def __init__(self, preview, number):
        super().__init__()
        self.setObjectName("paper")
        self.preview = preview
        self.number = number
        self.rendered_zoom = None
        self.marker_y = None

    def mouseDoubleClickEvent(self, event):
        zoom = self.preview.zoom
        self.preview.inverse_search.emit(self.number + 1, event.position().x() / zoom, event.position().y() / zoom)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.marker_y is not None:
            painter = QPainter(self)
            color = QColor(theme.ACCENT)
            color.setAlpha(70)
            painter.fillRect(0, int(self.marker_y * self.preview.zoom) - 10, self.width(), 20, color)
            painter.end()


class PdfPreview(QWidget):
    inverse_search = Signal(int, float, float)   # page (1-based), x, y in PDF points
    open_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.zoom = float(_settings().value("latex/preview_zoom", 0.8))
        self.pdf_path = None
        self.doc = None
        self.pages = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        bar = QToolBar()
        bar.setObjectName("editorBar")
        bar.setIconSize(QSize(16, 16))
        self.title = QLabel("Preview")
        self.title.setObjectName("muted")
        bar.addWidget(self.title)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar.addWidget(spacer)
        bar.addAction(icons.icon("zoom-out"), "Zoom Out", lambda: self.set_zoom(self.zoom / 1.15))
        self.zoom_label = QLabel("")
        bar.addWidget(self.zoom_label)
        bar.addAction(icons.icon("zoom-in"), "Zoom In", lambda: self.set_zoom(self.zoom * 1.15))
        bar.addAction(icons.icon("fit-width"), "Fit Width", self.fit_width)
        self.open_act = bar.addAction(icons.icon("note"), "Open the PDF in a tab to annotate or print it",
                                      lambda: self.pdf_path and self.open_requested.emit(self.pdf_path))
        layout.addWidget(bar)
        self.canvas = PaperCanvas()
        self.column = QVBoxLayout(self.canvas)
        self.column.setContentsMargins(16, 16, 16, 16)
        self.column.setSpacing(14)
        self.column.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.empty = QLabel("Compile (F5) to see the PDF here.")
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignCenter)
        self.column.addWidget(self.empty)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("previewScroll")
        self.scroll.setWidget(self.canvas)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameStyle(QScrollArea.NoFrame)
        self.scroll.verticalScrollBar().valueChanged.connect(lambda _v: self._render_visible())
        layout.addWidget(self.scroll, 1)
        self._update_zoom_label()

    def load(self, pdf_path):
        try:
            with open(pdf_path, "rb") as f:  # read it all, so the compiler can overwrite the file next time
                doc = fitz.open(stream=f.read(), filetype="pdf")
        except Exception:  # noqa: BLE001 - a half-written PDF; keep the old preview
            return False
        bar = self.scroll.verticalScrollBar()
        ratio = bar.value() / bar.maximum() if bar.maximum() else 0
        if self.doc is not None:
            self.doc.close()
        self.doc, self.pdf_path = doc, pdf_path
        self.title.setText(f"{os.path.basename(pdf_path)} · {doc.page_count} page{'s' if doc.page_count != 1 else ''}")
        for page in self.pages:
            page.deleteLater()
        self.pages = []
        self.empty.hide()
        for i in range(doc.page_count):
            label = _PreviewPage(self, i)
            self.pages.append(label)
            self.column.addWidget(label)
        self._layout_pages()
        QTimer.singleShot(0, lambda: (bar.setValue(round(ratio * bar.maximum())), self._render_visible()))
        return True

    def _layout_pages(self):
        for label in self.pages:
            rect = self.doc[label.number].rect
            label.setFixedSize(round(rect.width * self.zoom), round(rect.height * self.zoom))
            label.rendered_zoom = None
            label.clear()
        QTimer.singleShot(0, self._render_visible)

    def _render_visible(self):
        if self.doc is None:
            return
        top = self.scroll.verticalScrollBar().value()
        height = self.scroll.viewport().height()
        for label in self.pages:
            y = label.y()
            near = y + label.height() >= top - height and y <= top + 2 * height
            if near and label.rendered_zoom != self.zoom:
                dpr = self.devicePixelRatioF()
                pix = self.doc[label.number].get_pixmap(matrix=fitz.Matrix(self.zoom * dpr, self.zoom * dpr),
                                                        alpha=False)
                image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
                pixmap = QPixmap.fromImage(image)
                pixmap.setDevicePixelRatio(dpr)
                label.setPixmap(pixmap)
                label.rendered_zoom = self.zoom
            elif not near and label.rendered_zoom is not None:
                label.clear()  # far away: free the memory
                label.rendered_zoom = None

    def set_zoom(self, zoom):
        self.zoom = max(0.2, min(4.0, zoom))
        _settings().setValue("latex/preview_zoom", self.zoom)
        self._update_zoom_label()
        if self.doc is not None:
            self._layout_pages()

    def fit_width(self):
        if self.doc is None or not self.doc.page_count:
            return
        width = self.scroll.viewport().width() - 40
        self.set_zoom(width / self.doc[0].rect.width)

    def _update_zoom_label(self):
        self.zoom_label.setText(f"{round(self.zoom * 100)}%")

    def show_location(self, page, y):
        if not self.pages or not 1 <= page <= len(self.pages):
            return
        for label in self.pages:
            label.marker_y = None
            label.update()
        label = self.pages[page - 1]
        label.marker_y = y
        self.scroll.ensureVisible(label.x() + label.width() // 2, label.y() + round(y * self.zoom),
                                  0, self.scroll.viewport().height() // 3)
        label.update()
        QTimer.singleShot(2500, lambda: (setattr(label, "marker_y", None), label.update()))

    def clear(self):
        if self.doc is not None:
            self.doc.close()
        self.doc = None


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

class LatexSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("LaTeX Settings")
        self.setMinimumWidth(560)
        s = _settings()
        layout = _dialog_layout(self)
        layout.addLayout(_header("LaTeX settings", "Choose how documents are compiled and which external "
                                                   "editor to use. Compilers found on this PC are listed first."))
        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        self.custom_edit = QLineEdit(s.value("latex/custom_command", ""))
        self.custom_edit.setPlaceholderText('e.g.  latexmk -pdf -interaction=nonstopmode "{file}"')
        form.addRow("Custom compile command", self.custom_edit)
        hint = QLabel("{file} is the .tex file, {stem} its name without .tex, {dir} its folder. "
                      "Pick “Custom command” in the compiler list to use it.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        form.addRow("", hint)
        self.editor_edit = QLineEdit(s.value("latex/external_editor", ""))
        self.editor_edit.setPlaceholderText("Path to another editor's .exe (optional)")
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.editor_edit, 1)
        row.addWidget(browse)
        form.addRow("Other external editor", row)
        self.compile_on_save = QCheckBox("Compile every time the file is saved")
        self.compile_on_save.setChecked(s.value("latex/compile_on_save", "false") == "true")
        form.addRow("", self.compile_on_save)
        self.font_size = QComboBox()
        self.font_size.addItems([str(n) for n in range(8, 25)])
        self.font_size.setCurrentText(str(s.value("latex/font_size", 11)))
        form.addRow("Editor font size", self.font_size)
        found = engines.available_engines()
        info = QLabel("Found: " + (", ".join(f"{e.label.split(' (')[0]}" for e, _ in found) if found else
                                    "no LaTeX compiler. Install MiKTeX from miktex.org, or use Overleaf."))
        info.setObjectName("muted")
        info.setWordWrap(True)
        form.addRow("Compilers", info)
        layout.addLayout(form)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = _primary("Save")
        ok.clicked.connect(self.accept)
        layout.addLayout(_button_row(cancel, ok))

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose an Editor", "", "Programs (*.exe)")
        if path:
            self.editor_edit.setText(os.path.normpath(path))

    def accept(self):
        s = _settings()
        s.setValue("latex/custom_command", self.custom_edit.text().strip())
        s.setValue("latex/external_editor", self.editor_edit.text().strip())
        s.setValue("latex/compile_on_save", "true" if self.compile_on_save.isChecked() else "false")
        s.setValue("latex/font_size", int(self.font_size.currentText()))
        super().accept()


# --------------------------------------------------------------------------
# The tab
# --------------------------------------------------------------------------

class LatexTab(EditorTab):
    icon_name = "file-latex"
    kind_label = "LaTeX Document"
    save_filters = "LaTeX (*.tex);;BibTeX (*.bib);;All Files (*)"
    default_suffix = ".tex"
    save_suffixes = OPEN_SUFFIXES
    supports_zoom = True

    def __init__(self, parent=None, path=None, template=None):
        super().__init__(parent)
        self.process = None
        self.problems = []
        self._compile_started = 0.0
        self._saved_at = 0.0
        self._recompile = False
        self._pdf_mtime = 0.0

        self.editor = CodeEditor(self)
        self.editor.project_dir = self.project_dir
        self.find_bar = FindReplaceBar(self.editor, self)
        self.preview = PdfPreview(self)
        self.preview.inverse_search.connect(self._inverse_search)
        self.preview.open_requested.connect(self.open_path_requested.emit)

        self.files = QListWidget()
        self.files.setObjectName("projectFiles")
        self.files.itemActivated.connect(self._open_project_file)
        self.problem_list = QListWidget()
        self.problem_list.itemActivated.connect(self._go_to_problem)
        self.problem_list.itemClicked.connect(self._go_to_problem)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("latexLog")
        self.log_view.setFont(_monospace(9))
        self.bottom = QTabWidget()
        self.bottom.setDocumentMode(True)
        self.bottom.addTab(self.problem_list, "Problems")
        self.bottom.addTab(self.log_view, "Compiler output")

        editor_box = QWidget()
        ev = QVBoxLayout(editor_box)
        ev.setContentsMargins(0, 0, 0, 0)
        ev.setSpacing(0)
        ev.addWidget(self.find_bar)
        ev.addWidget(self.editor, 1)
        self.left = QSplitter(Qt.Vertical)
        self.left.addWidget(editor_box)
        self.left.addWidget(self.bottom)
        self.left.setStretchFactor(0, 4)
        self.left.setStretchFactor(1, 1)
        self.left.setSizes([700, 150])
        files_box = QWidget()
        fv = QVBoxLayout(files_box)
        fv.setContentsMargins(6, 6, 0, 6)
        heading = QLabel("Project files")
        heading.setObjectName("muted")
        fv.addWidget(heading)
        fv.addWidget(self.files, 1)
        self.split = QSplitter(Qt.Horizontal)
        self.split.addWidget(files_box)
        self.split.addWidget(self.left)
        self.split.addWidget(self.preview)
        self.split.setStretchFactor(1, 3)
        self.split.setStretchFactor(2, 2)
        self.split.setSizes([160, 700, 520])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_toolbar())
        layout.addWidget(self.split, 1)

        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self._file_changed_outside)
        self.editor.document().modificationChanged.connect(self.set_dirty)
        self.editor.cursorPositionChanged.connect(self.changed.emit)

        if path:
            self.load(path)
        else:
            text = TEMPLATES.get(template or "Article", TEMPLATES["Article"])
            caret = text.find("|")
            self.editor.setPlainText(text.replace("|", "", 1))
            cursor = self.editor.textCursor()
            cursor.setPosition(max(0, caret))
            self.editor.setTextCursor(cursor)
            self.editor.document().setModified(False)
        self._refresh_files()

    # ------------------------------------------------------------ toolbar
    def _build_toolbar(self):
        bar = QToolBar()
        bar.setObjectName("editorBar")
        bar.setIconSize(QSize(18, 18))

        def act(text, icon, slot, shortcut=None, tip=None):
            a = QAction(icons.icon(icon), text, self)
            keys = QKeySequence(shortcut).toString(QKeySequence.NativeText) if shortcut else ""
            a.setToolTip((tip or text) + (f"  ({keys})" if keys else ""))
            a.triggered.connect(slot)
            if shortcut:  # handled by the code editor itself (see CodeEditor.event)
                self.editor.key_actions[QKeySequence(shortcut).toString()] = a
            bar.addAction(a)
            return a

        act("Save", "save", self.save)
        self.compile_act = act("Compile", "play", self.compile, "F5", "Compile and refresh the preview")
        self.editor.key_actions[QKeySequence("Ctrl+Return").toString()] = self.compile_act
        self.stop_act = act("Stop", "stop", self.stop_compile, None, "Stop compiling")
        self.stop_act.setEnabled(False)
        self.engine_combo = QComboBox()
        self.engine_combo.setToolTip("Compiler")
        self._fill_engines()
        self.engine_combo.activated.connect(self._engine_chosen)
        bar.addWidget(self.engine_combo)
        self.status_label = QLabel("")
        self.status_label.setObjectName("muted")
        bar.addWidget(self.status_label)
        bar.addSeparator()
        act("Bold", "bold", lambda: self.editor.insert_snippet(r"\textbf{{sel}|}"), "Ctrl+B")
        act("Italic", "italic", lambda: self.editor.insert_snippet(r"\textit{{sel}|}"), "Ctrl+I")
        act("Inline Math", "sigma", lambda: self.editor.insert_snippet(r"${sel}|$"), "Ctrl+M")
        for menu_name, icon in (("Structure", "list-ordered"), ("Format", "text-color"), ("Lists", "list-bullet"),
                                ("Math", "formula"), ("Insert", "braces")):
            button = QToolButton()
            button.setIcon(icons.icon(icon))
            button.setText(menu_name)
            button.setToolTip(f"Insert: {menu_name}")
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            button.setPopupMode(QToolButton.InstantPopup)
            menu = QMenu(button)
            for group, label, snippet in SNIPPETS:
                if group == menu_name:
                    menu.addAction(label, lambda s=snippet: self.editor.insert_snippet(s))
            if menu_name == "Math":
                greek = menu.addMenu("Greek letters")
                for name in GREEK:
                    greek.addAction(f"\\{name}", lambda n=name: self.editor.insert_snippet(f"\\{n}|"))
            if menu_name == "Insert":
                menu.addSeparator()
                menu.addAction(icons.icon("image"), "Picture from file...", self.insert_picture)
            button.setMenu(menu)
            bar.addWidget(button)
        bar.addSeparator()
        act("Toggle Comment", "note", self.editor.toggle_comment, "Ctrl+/")
        act("Find and Replace", "find", self.show_find, "Ctrl+H")
        act("Show in PDF", "fit-page", self.forward_search, "Ctrl+J", "Show this line in the PDF preview")
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar.addWidget(spacer)
        external = QToolButton()
        external.setIcon(icons.icon("external"))
        external.setText("Open With")
        external.setToolTip("Open in an external editor, or package the project for Overleaf")
        external.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        external.setPopupMode(QToolButton.InstantPopup)
        self.external_menu = QMenu(external)
        self.external_menu.aboutToShow.connect(self._fill_external_menu)
        external.setMenu(self.external_menu)
        bar.addWidget(external)
        act("LaTeX Settings...", "settings", self.show_settings)
        return bar

    def _fill_engines(self):
        self.engine_combo.clear()
        found = {e.key for e, _ in engines.available_engines()}
        for engine in engines.ENGINES:
            label = engine.label if engine.key in found else f"{engine.label} (not installed)"
            self.engine_combo.addItem(label, engine.key)
        self.engine_combo.addItem("Custom command...", engines.CUSTOM)
        saved = _settings().value("latex/engine", "")
        keys = [self.engine_combo.itemData(i) for i in range(self.engine_combo.count())]
        if saved in keys and (saved in found or saved == engines.CUSTOM):
            self.engine_combo.setCurrentIndex(keys.index(saved))
        elif found:
            self.engine_combo.setCurrentIndex(keys.index(next(e.key for e in engines.ENGINES if e.key in found)))

    def _engine_chosen(self, index):
        key = self.engine_combo.itemData(index)
        _settings().setValue("latex/engine", key)
        if key == engines.CUSTOM and not _settings().value("latex/custom_command", ""):
            self.show_settings()
        self.changed.emit()

    def _fill_external_menu(self):
        menu = self.external_menu
        menu.clear()
        for label, path in engines.external_editors():
            menu.addAction(icons.icon("external"), label, lambda p=path: self.open_externally(p))
        custom = _settings().value("latex/external_editor", "")
        if custom and os.path.isfile(custom):
            menu.addAction(icons.icon("external"), os.path.splitext(os.path.basename(custom))[0],
                           lambda: self.open_externally(custom))
        menu.addAction(icons.icon("open"), "Default program for .tex files", lambda: self.open_externally(None))
        menu.addAction(icons.icon("settings"), "Choose another editor...", self._choose_editor)
        menu.addSeparator()
        menu.addAction(icons.icon("folder"), "Open the project folder", self.open_folder)
        menu.addAction(icons.icon("combine"), "Package for Overleaf (.zip)...", self.package_for_overleaf)

    # ------------------------------------------------------------ files
    def load(self, path):
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        self.path = os.path.abspath(path)
        self.editor.setPlainText(text)
        self.editor.document().setModified(False)
        self._watch()
        pdf = self.pdf_path()
        if pdf and os.path.isfile(pdf):
            self.preview.load(pdf)
            self._pdf_mtime = os.path.getmtime(pdf)
        self._refresh_files()
        self.changed.emit()

    def write(self, path):
        text = self.editor.toPlainText()
        if self.path and path != self.path:
            self.watcher.removePath(self.path)
        self._saved_at = time.time()
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text if text.endswith("\n") else text + "\n")
        self.editor.document().setModified(False)
        self.path = os.path.abspath(path)
        self._watch()
        self._refresh_files()
        if _settings().value("latex/compile_on_save", "false") == "true" and self.process is None:
            QTimer.singleShot(0, self.compile)

    def _watch(self):
        if self.path and self.path not in self.watcher.files():
            self.watcher.addPath(self.path)

    def _file_changed_outside(self, path):
        if time.time() - self._saved_at < 2 or not os.path.isfile(path):
            self._watch()  # our own save, or an editor that replaces the file: keep watching
            return
        self._watch()
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        if text.rstrip("\n") == self.editor.toPlainText().rstrip("\n"):
            return
        if self.dirty:
            resp = QMessageBox.question(self, "File Changed", f"{os.path.basename(path)} was changed by another "
                                        "program. Load that version and lose your changes here?",
                                        QMessageBox.Yes | QMessageBox.No)
            if resp != QMessageBox.Yes:
                return
        line = self.editor.textCursor().blockNumber() + 1
        self.editor.setPlainText(text)
        self.editor.document().setModified(False)
        self.editor.go_to_line(line)

    def project_dir(self):
        return os.path.dirname(self.path) if self.path else None

    def root_file(self):
        """The file to compile: a "% !TEX root = ..." line wins, then this file
        if it has \\documentclass, then main.tex in the same folder."""
        if not self.path:
            return None
        head = self.editor.toPlainText()[:3000]
        m = re.search(r"^\s*%\s*!\s*TEX\s+root\s*=\s*(.+?)\s*$", head, re.M | re.I)
        if m:
            root = os.path.normpath(os.path.join(self.project_dir(), m.group(1)))
            if os.path.isfile(root):
                return root
        if "\\documentclass" in head or not self.path.lower().endswith(".tex"):
            return self.path
        for name in ("main.tex", "Main.tex"):
            candidate = os.path.join(self.project_dir(), name)
            if os.path.isfile(candidate):
                return candidate
        return self.path

    def pdf_path(self):
        root = self.root_file()
        return os.path.splitext(root)[0] + ".pdf" if root else None

    def _refresh_files(self):
        self.files.clear()
        folder = self.project_dir()
        if not folder or not os.path.isdir(folder):
            self.files.addItem("Save to see the project")
            return
        wanted = OPEN_SUFFIXES + (".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg")
        entries = []
        for root, dirs, names in os.walk(folder):
            dirs[:] = [d for d in dirs if not d.startswith(".")][:20]
            for name in names:
                if name.lower().endswith(wanted):
                    entries.append(os.path.relpath(os.path.join(root, name), folder))
            if len(entries) > 300:
                break
        for rel in sorted(entries, key=lambda r: (not r.lower().endswith(".tex"), r.lower())):
            item = QListWidgetItem(icons.icon("file-latex" if rel.lower().endswith(OPEN_SUFFIXES) else
                                              "note" if rel.lower().endswith(".pdf") else "image"), rel)
            item.setData(Qt.UserRole, os.path.join(folder, rel))
            if self.path and os.path.normcase(os.path.join(folder, rel)) == os.path.normcase(self.path):
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self.files.addItem(item)

    def _open_project_file(self, item):
        path = item.data(Qt.UserRole)
        if not path:
            return
        if path.lower().endswith(OPEN_SUFFIXES + (".pdf",)):
            if os.path.normcase(path) != os.path.normcase(self.path or ""):
                self.open_path_requested.emit(path)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def insert_picture(self):
        start = self.project_dir() or ""
        path, _ = QFileDialog.getOpenFileName(self, "Insert Picture", start,
                                              "Images (*.png *.jpg *.jpeg *.pdf *.eps)")
        if not path:
            return
        rel = os.path.relpath(path, self.project_dir()).replace("\\", "/") if self.project_dir() else path
        if rel.startswith(".."):
            rel = path.replace("\\", "/")
        self.editor.insert_snippet("\\begin{figure}[htbp]\n  \\centering\n  \\includegraphics[width=0.8\\linewidth]{"
                                   + rel + "}\n  \\caption{|}\n  \\label{fig:}\n\\end{figure}")

    # ------------------------------------------------------------ compiling
    def _ensure_saved(self):
        if self.path and not self.dirty:
            return True
        if not self.path:
            QMessageBox.information(self, "Compile", "Save the document first: the PDF and the files LaTeX "
                                                     "makes go next to it.")
        return self.save()

    def compile(self):
        if self.process is not None:
            self._recompile = True  # a save during a compile: run again when it ends
            return
        if not self._ensure_saved():
            return
        root = self.root_file()
        key = self.engine_combo.currentData()
        argv, error = engines.build_command(key, root, _settings().value("latex/custom_command", ""))
        if argv is None:
            self._no_compiler(error)
            return
        self.log_view.setPlainText("> " + " ".join(f'"{a}"' if " " in a else a for a in argv) + "\n")
        self.process = QProcess(self)
        self.process.setWorkingDirectory(os.path.dirname(root))
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("max_print_line", "1000")  # one log line per message, easier to read
        self.process.setProcessEnvironment(env)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(lambda code, _status: self._compiled(code, root))
        self.process.errorOccurred.connect(self._process_error)
        self._compile_started = time.time()
        self.compile_act.setEnabled(False)
        self.stop_act.setEnabled(True)
        self._set_status(f"Compiling with {self.engine_combo.currentText().split(' (')[0]}...")
        self.process.start(argv[0], argv[1:])

    def _read_output(self):
        if self.process is None:
            return
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.log_view.moveCursor(QTextCursor.End)
        self.log_view.insertPlainText(data)
        self.log_view.moveCursor(QTextCursor.End)

    def _process_error(self, error):
        if error == QProcess.FailedToStart and self.process is not None:
            self.log_view.appendPlainText(f"\nCould not start the compiler: {self.process.errorString()}")
            process, self.process = self.process, None
            process.deleteLater()
            self._finish_ui()
            self._set_status("The compiler could not be started.")

    def stop_compile(self):
        if self.process is not None:
            self._recompile = False
            self.process.kill()

    def _finish_ui(self):
        self.compile_act.setEnabled(True)
        self.stop_act.setEnabled(False)

    def _compiled(self, code, root):
        if self.process is None:
            return
        self._read_output()
        process, self.process = self.process, None
        process.deleteLater()
        self._finish_ui()
        seconds = time.time() - self._compile_started
        log_path = os.path.splitext(root)[0] + ".log"
        log = ""
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                log = f.read()
        except OSError:
            pass
        if self.engine_combo.currentData() == "tectonic" or not log:
            log = log + "\n" + self.log_view.toPlainText()
        self.problems = engines.parse_log(log, os.path.basename(root))
        self._show_problems(root)
        pdf = os.path.splitext(root)[0] + ".pdf"
        updated = os.path.isfile(pdf) and os.path.getmtime(pdf) >= self._compile_started - 1
        if updated:
            self.preview.load(pdf)
            self._pdf_mtime = os.path.getmtime(pdf)
        errors = sum(p.kind == "error" for p in self.problems)
        warnings = sum(p.kind == "warning" for p in self.problems)
        if errors:
            self._set_status(f"✗ {errors} error{'s' if errors != 1 else ''}, {warnings} warning"
                             f"{'s' if warnings != 1 else ''} · {seconds:.1f} s")
            self.bottom.setCurrentWidget(self.problem_list)
        elif code != 0 and not updated:
            self._set_status(f"✗ The compiler stopped (exit code {code}). See the compiler output.")
            self.bottom.setCurrentWidget(self.log_view)
        else:
            self._set_status(f"✓ Compiled in {seconds:.1f} s" + (f" · {warnings} warning{'s' if warnings != 1 else ''}"
                                                                 if warnings else ""))
        self._refresh_files()
        if self._recompile:
            self._recompile = False
            QTimer.singleShot(0, self.compile)

    def _show_problems(self, root):
        self.problem_list.clear()
        this = os.path.basename(self.path or "")
        mine = []
        for p in self.problems:
            where = f"{p.file or os.path.basename(root)}:{p.line}" if p.line else (p.file or "")
            label = {"error": "Error", "warning": "Warning", "badbox": "Layout"}[p.kind]
            item = QListWidgetItem(f"{label}  {where}  {p.message}")
            item.setForeground(QColor(theme.DANGER) if p.kind == "error" else
                               QColor("#d97706") if p.kind == "warning" else QColor(theme.TEXT_MUTED))
            item.setData(Qt.UserRole, p)
            self.problem_list.addItem(item)
            if not p.file or p.file == this:
                mine.append(p)
        if not self.problems:
            self.problem_list.addItem("No problems.")
        self.editor.set_markers(mine)
        self.bottom.setTabText(0, f"Problems ({len(self.problems)})" if self.problems else "Problems")

    def _go_to_problem(self, item):
        p = item.data(Qt.UserRole)
        if p is None or not p.line:
            return
        if p.file and self.path and p.file != os.path.basename(self.path):
            other = os.path.join(self.project_dir(), p.file)
            if os.path.isfile(other):
                self.open_path_requested.emit(other)
            return
        self.editor.go_to_line(p.line)

    def _no_compiler(self, message):
        box = QMessageBox(QMessageBox.Warning, "No LaTeX Compiler", message, parent=self)
        miktex = box.addButton("Get MiKTeX", QMessageBox.ActionRole)
        overleaf = box.addButton("Package for Overleaf", QMessageBox.ActionRole)
        settings = box.addButton("Settings...", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Close)
        box.exec()
        if box.clickedButton() is miktex:
            QDesktopServices.openUrl(QUrl("https://miktex.org/download"))
        elif box.clickedButton() is overleaf:
            self.package_for_overleaf()
        elif box.clickedButton() is settings:
            self.show_settings()

    def _set_status(self, text):
        self.status_label.setText("  " + text)
        self.changed.emit()

    # ------------------------------------------------------------ SyncTeX
    def _inverse_search(self, page, x, y):
        pdf = self.preview.pdf_path
        argv = engines.synctex_edit_command(pdf, page, x, y) if pdf else None
        if not argv:
            return
        out = self._run_quick(argv, os.path.dirname(pdf))
        file, line = engines.parse_synctex_edit(out)
        if not line:
            return
        if file and self.path and os.path.normcase(os.path.abspath(os.path.join(os.path.dirname(pdf), file))) \
                != os.path.normcase(self.path):
            target = os.path.abspath(os.path.join(os.path.dirname(pdf), file))
            if os.path.isfile(target):
                self.open_path_requested.emit(target)
            return
        self.editor.go_to_line(line)

    def forward_search(self):
        pdf = self.preview.pdf_path
        if not pdf or not self.path:
            return
        line = self.editor.textCursor().blockNumber() + 1
        argv = engines.synctex_view_command(self.path, line, pdf)
        if not argv:
            return
        page, y = engines.parse_synctex_view(self._run_quick(argv, os.path.dirname(pdf)))
        if page:
            self.preview.show_location(page, y)

    @staticmethod
    def _run_quick(argv, cwd):
        process = QProcess()
        process.setWorkingDirectory(cwd)
        process.start(argv[0], argv[1:])
        if not process.waitForFinished(5000):
            process.kill()
            return ""
        return bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")

    # ------------------------------------------------------------ external
    def open_externally(self, program):
        if not self.path:
            if not self.save():
                return
        elif self.dirty:
            self.save()
        if program is None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.path))
            return
        if not QProcess.startDetached(program, [self.path], self.project_dir()):
            QMessageBox.warning(self, "Open With", f"Could not start {program}.")
            return
        self._set_status(f"Opened in {os.path.splitext(os.path.basename(program))[0]}. Changes saved there "
                         "reload here.")

    def _choose_editor(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose an Editor", "", "Programs (*.exe)")
        if path:
            _settings().setValue("latex/external_editor", os.path.normpath(path))
            self.open_externally(os.path.normpath(path))

    def open_folder(self):
        if self.project_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.project_dir()))

    def package_for_overleaf(self):
        if not self._ensure_saved():
            return
        folder = self.project_dir()
        start = os.path.join(os.path.dirname(folder), os.path.basename(folder) + " (Overleaf).zip")
        target, _ = QFileDialog.getSaveFileName(self, "Package for Overleaf", start, "Zip Archives (*.zip)")
        if not target:
            return
        skip = (".aux", ".log", ".out", ".toc", ".synctex.gz", ".fls", ".fdb_latexmk", ".xdv", ".bbl", ".blg",
                ".lof", ".lot", ".nav", ".snm", ".zip")
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, names in os.walk(folder):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for name in names:
                    full = os.path.join(root, name)
                    if name.lower().endswith(skip) or os.path.abspath(full) == os.path.abspath(target):
                        continue
                    zf.write(full, os.path.relpath(full, folder))
        box = QMessageBox(QMessageBox.Information, "Package for Overleaf",
                          f"Saved {target}.\n\nOn Overleaf choose New Project → Upload Project and pick this file. "
                          "Set Menu → Compiler to match (XeLaTeX for projects made by Convert to LaTeX).", parent=self)
        web = box.addButton("Open Overleaf", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Close)
        box.exec()
        if box.clickedButton() is web:
            QDesktopServices.openUrl(QUrl("https://www.overleaf.com/project"))

    def show_settings(self):
        dlg = LatexSettingsDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self.editor.setFont(_monospace(int(_settings().value("latex/font_size", 11))))
            self._fill_engines()

    # ------------------------------------------------------------ EditorTab hooks
    def text_widget(self):
        return self.editor

    def status_text(self):
        cursor = self.editor.textCursor()
        engine = self.engine_combo.currentText().split(" (")[0] if hasattr(self, "engine_combo") else ""
        return f"Ln {cursor.blockNumber() + 1}, Col {cursor.positionInBlock() + 1} · {engine}"

    def set_zoom(self, zoom):
        self.zoom = zoom
        font = self.editor.font()
        font.setPointSizeF(max(6.0, int(_settings().value("latex/font_size", 11)) * zoom))
        self.editor.setFont(font)
        self.editor._update_gutter_width()

    def print_document(self):
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter

        if self.preview.pdf_path and os.path.isfile(self.preview.pdf_path):
            resp = QMessageBox.question(self, "Print", "Print the compiled PDF? (No prints the LaTeX source.)",
                                        QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if resp == QMessageBox.Cancel:
                return
            if resp == QMessageBox.Yes:
                self.open_path_requested.emit(self.preview.pdf_path)
                QMessageBox.information(self, "Print", "The PDF is open in its own tab: use File → Print there.")
                return
        printer = QPrinter(QPrinter.HighResolution)
        if QPrintDialog(printer, self).exec() == QDialog.Accepted:
            self.editor.print_(printer)

    def confirm_close(self):
        ok = super().confirm_close()
        if ok and self.process is not None:
            self.process.kill()
            self.process = None
        if ok:
            self.preview.clear()
        return ok
