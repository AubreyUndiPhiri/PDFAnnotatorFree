"""Convert to Word / Convert to LaTeX: the options dialog and the worker
thread that runs docx_export.PdfToDocx or latex.pdf_to_latex.PdfToLatex
behind a progress dialog."""
import os
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QProgressDialog, QPushButton,
)

from . import docx_export
from .dialogs import _button_row, _dialog_layout, _header, _primary

WORD, LATEX = "word", "latex"
_TITLES = {WORD: "Convert to Word", LATEX: "Convert to LaTeX"}
_SUBTITLES = {
    WORD: "Creates an editable .docx file that looks like the PDF. Scanned pages are read with OCR; "
          "anything that can't be converted is kept as a picture of the page.",
    LATEX: "Creates a LaTeX project folder (main.tex and its files) that looks like the PDF. Compile it "
           "with XeLaTeX (it opens and compiles straight away here, or use Overleaf).",
}
LAYOUTS = (
    (docx_export.LAYOUT_EXACT, "Exact: looks just like the PDF (recommended)",
     "Every line stays where it was, in a matching font and size; lines, tables, charts and pictures "
     "are kept exactly. Edit the text in place."),
    (docx_export.LAYOUT_FLOW, "Flowing: paragraphs that reflow (easier to rewrite)",
     "Rebuilt as headings, paragraphs and tables that reflow as you type; the look is only roughly "
     "like the PDF."),
)


def default_output(fmt, pdf_path, name) -> str:
    folder = os.path.dirname(pdf_path) if pdf_path else os.path.join(os.path.expanduser("~"), "Documents")
    stem = Path(name).stem or "Untitled"
    return os.path.join(folder, f"{stem}.docx" if fmt == WORD else f"{stem} LaTeX")


class ConvertDialog(QDialog):
    def __init__(self, fmt, page_count, output, parent=None):
        super().__init__(parent)
        self.fmt = fmt
        self.page_count = page_count
        self.pages = None
        self.setWindowTitle(_TITLES[fmt])
        self.setMinimumWidth(520)
        layout = _dialog_layout(self)
        layout.addLayout(_header(_TITLES[fmt], _SUBTITLES[fmt]))

        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.output_edit = QLineEdit(output)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.output_edit, 1)
        row.addWidget(browse)
        form.addRow("Save to" if fmt == WORD else "Project folder", row)

        self.pages_edit = QLineEdit()
        self.pages_edit.setPlaceholderText(f"All {page_count} pages, or for example 1-3, 5")
        form.addRow("Pages", self.pages_edit)

        self.layout_combo = QComboBox()
        for key, label, tip in LAYOUTS:
            self.layout_combo.addItem(label, key)
            self.layout_combo.setItemData(self.layout_combo.count() - 1, tip, Qt.ToolTipRole)
        from . import theme

        saved = theme._settings().value(f"convert/{fmt}_layout", docx_export.LAYOUT_EXACT)
        self.layout_combo.setCurrentIndex(max(0, self.layout_combo.findData(saved)))
        self.layout_note = QLabel()
        self.layout_note.setObjectName("muted")
        self.layout_note.setWordWrap(True)
        self.layout_combo.currentIndexChanged.connect(self._show_layout_note)
        self._show_layout_note()
        form.addRow("Layout", self.layout_combo)
        form.addRow("", self.layout_note)

        self.ocr_combo = QComboBox()
        for key, label in docx_export.OCR_CHOICES.items():
            self.ocr_combo.addItem(label, key)
        form.addRow("Scanned pages", self.ocr_combo)

        self.annots_check = QCheckBox("Include annotations (highlights, ink, stamps, text boxes)")
        self.annots_check.setChecked(True)
        form.addRow("", self.annots_check)
        self.pictures_check = QCheckBox("Also keep a picture of each scanned page")
        form.addRow("", self.pictures_check)
        layout.addLayout(form)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        ok_btn = _primary("Convert")
        ok_btn.clicked.connect(self.accept)
        layout.addLayout(_button_row(cancel_btn, ok_btn))

    def _show_layout_note(self, *_):
        self.layout_note.setText(LAYOUTS[max(0, self.layout_combo.currentIndex())][2])

    def _browse(self):
        current = self.output_edit.text().strip()
        if self.fmt == WORD:
            path, _ = QFileDialog.getSaveFileName(self, "Save Word Document", current, "Word Documents (*.docx)")
        else:
            path = QFileDialog.getExistingDirectory(self, "Choose the Project Folder", os.path.dirname(current))
            if path:
                path = os.path.join(path, os.path.basename(current) or "LaTeX")
        if path:
            self.output_edit.setText(os.path.normpath(path))

    def accept(self):
        out = self.output_edit.text().strip()
        if not out:
            QMessageBox.warning(self, _TITLES[self.fmt], "Choose where to save the result.")
            return
        if self.fmt == WORD and not out.lower().endswith(".docx"):
            out += ".docx"
            self.output_edit.setText(out)
        try:
            self.pages = docx_export.parse_page_range(self.pages_edit.text(), self.page_count)
        except ValueError as e:
            QMessageBox.warning(self, _TITLES[self.fmt], str(e))
            return
        existing = out if self.fmt == WORD else os.path.join(out, "main.tex")
        if os.path.exists(existing):
            resp = QMessageBox.question(self, _TITLES[self.fmt], f"{existing} already exists. Replace it?",
                                        QMessageBox.Yes | QMessageBox.No)
            if resp != QMessageBox.Yes:
                return
        from . import theme

        theme._settings().setValue(f"convert/{self.fmt}_layout", self.layout_combo.currentData())
        super().accept()

    def options(self) -> dict:
        return {
            "layout": self.layout_combo.currentData(),
            "pages": self.pages,
            "ocr": self.ocr_combo.currentData(),
            "include_annotations": self.annots_check.isChecked(),
            "keep_scan_images": self.pictures_check.isChecked(),
        }

    def output(self) -> str:
        return os.path.normpath(self.output_edit.text().strip())


class ConversionThread(QThread):
    progressed = Signal(float, str)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, converter, out_path, parent=None):
        super().__init__(parent)
        self.converter = converter
        self.out_path = out_path
        converter.progress = lambda fraction, text: self.progressed.emit(fraction, text)

    def run(self):
        try:
            report = self.converter.run(self.out_path)
        except docx_export.ConversionCancelled:
            self.cancelled.emit()
        except docx_export.ConversionError as e:
            self.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001 - shown to the user instead of crashing the app
            self.failed.emit(f"{type(e).__name__}: {e}")
        else:
            self.succeeded.emit(report)


def convert(parent, fmt, pdf_bytes, pdf_path, name, page_count):
    """Ask for the options, then convert pdf_bytes to Word or LaTeX with a
    progress dialog. Runs the conversion in a thread; returns straight away."""
    dlg = ConvertDialog(fmt, page_count, default_output(fmt, pdf_path, name), parent)
    if dlg.exec() != QDialog.Accepted:
        return
    out = dlg.output()
    if fmt == WORD:
        converter = docx_export.PdfToDocx(pdf_bytes, **dlg.options())
    else:
        from .latex.pdf_to_latex import PdfToLatex

        converter = PdfToLatex(pdf_bytes, **dlg.options())

    progress = QProgressDialog("Starting...", "Cancel", 0, 1000, parent)
    progress.setWindowTitle(_TITLES[fmt])
    progress.setWindowModality(Qt.WindowModal)
    progress.setMinimumWidth(460)
    progress.setMinimumDuration(0)
    progress.setAutoClose(False)
    progress.setAutoReset(False)
    thread = ConversionThread(converter, out, parent)

    def on_progress(fraction, text):
        progress.setValue(int(max(0.0, min(1.0, fraction)) * 1000))
        progress.setLabelText(text)

    def on_cancel():
        progress.setLabelText("Cancelling...")
        converter.cancel()

    def done():
        progress.canceled.disconnect(on_cancel)  # closing the dialog emits canceled too
        progress.close()
        progress.deleteLater()
        thread.deleteLater()

    def on_success(report):
        done()
        target = str(converter.tex_path) if fmt == LATEX else out
        text = f"Saved to {target}\n\n{report.summary()}"
        if report.warnings:
            text += "\n\n" + "\n".join(f"• {w}" for w in report.warnings[:8])
            if len(report.warnings) > 8:
                text += f"\n• ...and {len(report.warnings) - 8} more"
        exact_word = fmt == WORD and getattr(converter, "layout", "") == docx_export.LAYOUT_EXACT
        if exact_word:
            text += ("\n\nOpen it in Microsoft Word to see it exactly as the PDF; the Word editor here shows "
                     "its text in order but not each line's position.")
        box = QMessageBox(QMessageBox.Information, _TITLES[fmt], text, parent=parent)
        open_btn = box.addButton("Open in Word", QMessageBox.AcceptRole) if exact_word else None
        here_btn = box.addButton("Open in the Word Editor" if fmt == WORD else "Open in the LaTeX Editor",
                                 QMessageBox.ActionRole if exact_word else QMessageBox.AcceptRole)
        if fmt == WORD and not exact_word:
            open_btn = box.addButton("Open in Word", QMessageBox.ActionRole)
        if open_btn is not None and exact_word:
            box.setDefaultButton(open_btn)
        folder_btn = box.addButton("Open Folder", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Close)
        box.exec()
        clicked = box.clickedButton()
        try:
            if clicked is here_btn and hasattr(parent, "open_files_as_tabs"):
                parent.open_files_as_tabs([target])
                if fmt == LATEX:   # compile at once, so the preview shows the result
                    editor = parent.tab_for_path(target) if hasattr(parent, "tab_for_path") else None
                    if editor is not None and hasattr(editor, "compile"):
                        from PySide6.QtCore import QTimer

                        QTimer.singleShot(200, editor.compile)
            elif clicked is open_btn and open_btn is not None:
                os.startfile(target)
            elif clicked is folder_btn:
                os.startfile(os.path.dirname(target))
        except OSError as e:
            QMessageBox.warning(parent, _TITLES[fmt], f"Could not open it:\n{e}")

    def on_failure(message):
        done()
        QMessageBox.critical(parent, _TITLES[fmt], f"The conversion failed.\n\n{message}")

    progress.canceled.connect(on_cancel)
    thread.progressed.connect(on_progress)
    thread.succeeded.connect(on_success)
    thread.failed.connect(on_failure)
    thread.cancelled.connect(done)
    thread.start()
    return thread
