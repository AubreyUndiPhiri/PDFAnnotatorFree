import os
import fitz

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QScrollArea, QSplitter,
    QToolBar, QFileDialog, QMessageBox, QInputDialog, QColorDialog,
    QSpinBox, QDoubleSpinBox, QComboBox, QLabel, QPushButton, QStatusBar,
)
from PySide6.QtGui import QAction, QActionGroup, QColor, QKeySequence, QShortcut, QIcon, QPixmap
from PySide6.QtCore import Qt, QPoint, QTimer, QSize

from .document import PDFDocument
from .page_widget import PageWidget
from .thumbnail_panel import ThumbnailPanel
from .dialogs import SignaturePadDialog
from .tools import Tool, STAMP_NAMES
from . import pdf_ops


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF Annotator Free")

        self.document = PDFDocument()
        self.zoom = 1.2
        self.current_tool = Tool.SELECT
        self.current_color = QColor(255, 210, 0)
        self.current_width = 2.0
        self.current_fontsize = 12
        self.current_stamp_name = STAMP_NAMES[0]
        self.pending_image_path = None

        self.selected = None
        self.select_dragging = False
        self.select_offset_px = QPoint(0, 0)
        self._select_start_px = None

        self.page_widgets = []

        self._build_ui()
        self._wire_shortcuts()

        self.new_document()

    # ---------------------------------------------------------------
    # UI construction
    # ---------------------------------------------------------------

    def _build_ui(self):
        self.thumbnails = ThumbnailPanel(self)
        self.thumbnails.pageActivated.connect(self.go_to_page)
        self.thumbnails.pagesReordered.connect(self.reorder_pages)

        self.pages_container = QWidget()
        self.pages_layout = QVBoxLayout(self.pages_container)
        self.pages_layout.setSpacing(16)
        self.pages_layout.setContentsMargins(16, 16, 16, 16)
        self.pages_layout.setAlignment(Qt.AlignHCenter | Qt.AlignTop)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(self.pages_container)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self.update_visible_pages)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.thumbnails)
        splitter.addWidget(self.scroll_area)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)

        self.setStatusBar(QStatusBar())
        self.hint_label = QLabel("")
        self.statusBar().addPermanentWidget(self.hint_label)

        self._build_toolbar()
        self._build_menu()

    def _build_toolbar(self):
        tb = QToolBar("Tools")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        self.addToolBar(tb)

        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)

        def add_tool_action(label, tool, tooltip):
            act = QAction(label, self)
            act.setCheckable(True)
            act.setToolTip(tooltip)
            act.triggered.connect(lambda checked, t=tool: self.set_tool(t))
            self.tool_group.addAction(act)
            tb.addAction(act)
            return act

        self.action_select = add_tool_action("Select", Tool.SELECT, "Select / Move / Delete annotations")
        tb.addSeparator()
        add_tool_action("Highlight", Tool.HIGHLIGHT, "Drag across text to highlight it")
        add_tool_action("Underline", Tool.UNDERLINE, "Drag across text to underline it")
        add_tool_action("Strikeout", Tool.STRIKEOUT, "Drag across text to strike it out")
        add_tool_action("Note", Tool.NOTE, "Click to add a sticky note comment")
        tb.addSeparator()
        add_tool_action("Pen", Tool.INK, "Freehand drawing")
        add_tool_action("Rect", Tool.RECT, "Draw a rectangle")
        add_tool_action("Ellipse", Tool.ELLIPSE, "Draw an ellipse")
        add_tool_action("Line", Tool.LINE, "Draw a line")
        add_tool_action("Arrow", Tool.ARROW, "Draw an arrow")
        add_tool_action("Text Box", Tool.TEXTBOX, "Add a typed text box")
        tb.addSeparator()
        add_tool_action("Stamp", Tool.STAMP, "Place the selected stamp")

        self.action_select.setChecked(True)

        tb.addSeparator()
        tb.addWidget(QLabel(" Stamp: "))
        self.stamp_combo = QComboBox()
        self.stamp_combo.addItems(STAMP_NAMES)
        self.stamp_combo.currentTextChanged.connect(self._set_stamp_name)
        tb.addWidget(self.stamp_combo)

        tb.addSeparator()
        insert_image_btn = QPushButton("Insert Image...")
        insert_image_btn.clicked.connect(self.insert_image_stamp)
        tb.addWidget(insert_image_btn)

        sign_btn = QPushButton("Draw Signature...")
        sign_btn.clicked.connect(self.insert_drawn_signature)
        tb.addWidget(sign_btn)

        tb.addSeparator()
        tb.addWidget(QLabel(" Color: "))
        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(26, 26)
        self.color_btn.clicked.connect(self.pick_color)
        self._update_color_button()
        tb.addWidget(self.color_btn)

        tb.addWidget(QLabel("  Width: "))
        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(0.5, 20.0)
        self.width_spin.setSingleStep(0.5)
        self.width_spin.setValue(self.current_width)
        self.width_spin.valueChanged.connect(self._set_width)
        tb.addWidget(self.width_spin)

        tb.addWidget(QLabel("  Font: "))
        self.font_spin = QSpinBox()
        self.font_spin.setRange(6, 96)
        self.font_spin.setValue(self.current_fontsize)
        self.font_spin.valueChanged.connect(self._set_fontsize)
        tb.addWidget(self.font_spin)

        tb2 = QToolBar("Navigation")
        tb2.setMovable(False)
        self.addToolBar(tb2)

        zoom_out_btn = QPushButton("-")
        zoom_out_btn.setFixedWidth(28)
        zoom_out_btn.clicked.connect(self.zoom_out)
        tb2.addWidget(zoom_out_btn)

        self.zoom_label = QLabel(f"{int(self.zoom * 100)}%")
        self.zoom_label.setFixedWidth(50)
        self.zoom_label.setAlignment(Qt.AlignCenter)
        tb2.addWidget(self.zoom_label)

        zoom_in_btn = QPushButton("+")
        zoom_in_btn.setFixedWidth(28)
        zoom_in_btn.clicked.connect(self.zoom_in)
        tb2.addWidget(zoom_in_btn)

        zoom_reset_btn = QPushButton("Reset")
        zoom_reset_btn.clicked.connect(self.zoom_reset)
        tb2.addWidget(zoom_reset_btn)

        tb2.addSeparator()
        tb2.addWidget(QLabel(" Page: "))
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.setMaximum(1)
        self.page_spin.valueChanged.connect(lambda v: self.go_to_page(v - 1))
        tb2.addWidget(self.page_spin)
        self.page_count_label = QLabel(" / 1")
        tb2.addWidget(self.page_count_label)

    def _build_menu(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        self._add_menu_action(file_menu, "New", self.new_document, QKeySequence.New)
        self._add_menu_action(file_menu, "Open...", self.open_document, QKeySequence.Open)
        self._add_menu_action(file_menu, "Save", self.save_document, QKeySequence.Save)
        self._add_menu_action(file_menu, "Save As...", self.save_document_as, QKeySequence.SaveAs)
        file_menu.addSeparator()
        self._add_menu_action(file_menu, "Merge PDFs Into This Document...", self.merge_pdfs)
        self._add_menu_action(file_menu, "Split Every Page to Separate Files...", self.split_pdf)
        file_menu.addSeparator()
        self._add_menu_action(file_menu, "Exit", self.close)

        edit_menu = menubar.addMenu("&Edit")
        self._add_menu_action(edit_menu, "Undo", self.undo, QKeySequence.Undo)
        self._add_menu_action(edit_menu, "Redo", self.redo, QKeySequence.Redo)
        edit_menu.addSeparator()
        self._add_menu_action(edit_menu, "Delete Selected Annotation", self.delete_selected, QKeySequence.Delete)

        page_menu = menubar.addMenu("&Page")
        self._add_menu_action(
            page_menu, "Insert Blank Page",
            lambda: self.insert_blank_page(self.current_page_index() + 1),
        )
        self._add_menu_action(
            page_menu, "Delete Current Page",
            lambda: self.delete_page(self.current_page_index()),
        )
        self._add_menu_action(
            page_menu, "Rotate Left",
            lambda: self.rotate_page(self.current_page_index(), -90),
        )
        self._add_menu_action(
            page_menu, "Rotate Right",
            lambda: self.rotate_page(self.current_page_index(), 90),
        )
        self._add_menu_action(
            page_menu, "Extract Current Page...",
            lambda: self.extract_page(self.current_page_index()),
        )

    def _add_menu_action(self, menu, text, slot, shortcut=None):
        act = QAction(text, self)
        if shortcut:
            act.setShortcut(shortcut)
        act.triggered.connect(slot)
        menu.addAction(act)
        return act

    def _wire_shortcuts(self):
        sc = QShortcut(QKeySequence(Qt.Key_Backspace), self)
        sc.activated.connect(self.delete_selected)

    # ---------------------------------------------------------------
    # Tool / style state
    # ---------------------------------------------------------------

    def set_tool(self, tool):
        self.current_tool = tool
        self.selected = None
        for pw in self.page_widgets:
            pw.update()
        hints = {
            Tool.SELECT: "Click an annotation to select it. Drag to move, Delete to remove, double-click text to edit.",
            Tool.HIGHLIGHT: "Drag across text to highlight it.",
            Tool.UNDERLINE: "Drag across text to underline it.",
            Tool.STRIKEOUT: "Drag across text to strike it out.",
            Tool.NOTE: "Click anywhere to add a sticky note.",
            Tool.INK: "Drag to draw freehand.",
            Tool.RECT: "Drag to draw a rectangle.",
            Tool.ELLIPSE: "Drag to draw an ellipse.",
            Tool.LINE: "Drag to draw a line.",
            Tool.ARROW: "Drag to draw an arrow.",
            Tool.TEXTBOX: "Drag to size a text box, then type your text.",
            Tool.STAMP: "Drag to place the selected stamp.",
            Tool.IMAGE_STAMP: "Drag to place the chosen image.",
        }
        self.hint_label.setText(hints.get(tool, ""))

    def _set_stamp_name(self, name):
        self.current_stamp_name = name

    def _set_width(self, value):
        self.current_width = value

    def _set_fontsize(self, value):
        self.current_fontsize = value

    def pick_color(self):
        color = QColorDialog.getColor(self.current_color, self, "Choose Color")
        if color.isValid():
            self.current_color = color
            self._update_color_button()

    def _update_color_button(self):
        pix = QPixmap(20, 20)
        pix.fill(self.current_color)
        self.color_btn.setIcon(QIcon(pix))

    # ---------------------------------------------------------------
    # Document lifecycle
    # ---------------------------------------------------------------

    def new_document(self):
        self.document.new()
        self.selected = None
        self.rebuild_viewer()
        self.setWindowTitle("PDF Annotator Free - Untitled")

    def open_document(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF Files (*.pdf)")
        if not path:
            return
        self._open_path(path)

    def _open_path(self, path):
        try:
            self.document.load(path)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not open file:\n{e}")
            return
        self.selected = None
        self.rebuild_viewer()
        self.setWindowTitle(f"PDF Annotator Free - {os.path.basename(path)}")

    def save_document(self):
        if not self.document.is_open:
            return
        if self.document.path:
            try:
                self.document.save()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not save:\n{e}")
            else:
                self.statusBar().showMessage("Saved", 3000)
        else:
            self.save_document_as()

    def save_document_as(self):
        if not self.document.is_open:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save PDF As", "", "PDF Files (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        try:
            self.document.save(path)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not save:\n{e}")
        else:
            self.setWindowTitle(f"PDF Annotator Free - {os.path.basename(path)}")
            self.statusBar().showMessage(f"Saved to {path}", 3000)

    def merge_pdfs(self):
        if not self.document.is_open:
            self.new_document()
        paths, _ = QFileDialog.getOpenFileNames(self, "Select PDFs to Merge In", "", "PDF Files (*.pdf)")
        if not paths:
            return
        for p in paths:
            with open(p, "rb") as f:
                data = f.read()
            other = fitz.open(stream=data, filetype="pdf")
            self.document.doc.insert_pdf(other)
            other.close()
        self.document.snapshot()
        self.selected = None
        self.rebuild_viewer()
        QMessageBox.information(self, "Merged", f"Merged {len(paths)} file(s) into the document.")

    def split_pdf(self):
        if not self.document.is_open or self.document.page_count == 0:
            return
        directory = QFileDialog.getExistingDirectory(self, "Choose Output Folder")
        if not directory:
            return
        base = "page"
        if self.document.path:
            base = os.path.splitext(os.path.basename(self.document.path))[0]
        count = self.document.page_count
        for i in range(count):
            new_doc = fitz.open()
            new_doc.insert_pdf(self.document.doc, from_page=i, to_page=i)
            out_path = os.path.join(directory, f"{base}_p{i + 1:03d}.pdf")
            new_doc.save(out_path)
            new_doc.close()
        QMessageBox.information(self, "Split Complete", f"Saved {count} file(s) to {directory}")

    def closeEvent(self, event):
        if self.document.is_open and self.document.dirty:
            resp = QMessageBox.question(
                self, "Unsaved Changes", "Save changes before closing?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            )
            if resp == QMessageBox.Save:
                self.save_document()
                if self.document.dirty:
                    event.ignore()
                    return
            elif resp == QMessageBox.Cancel:
                event.ignore()
                return
        event.accept()

    # ---------------------------------------------------------------
    # Undo / redo
    # ---------------------------------------------------------------

    def undo(self):
        if not self.document.can_undo():
            return
        self.document.undo()
        self.selected = None
        self.rebuild_viewer()

    def redo(self):
        if not self.document.can_redo():
            return
        self.document.redo()
        self.selected = None
        self.rebuild_viewer()

    # ---------------------------------------------------------------
    # Viewer management
    # ---------------------------------------------------------------

    def rebuild_viewer(self):
        self.document.invalidate_page_cache()
        while self.pages_layout.count():
            item = self.pages_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self.page_widgets = []
        if self.document.is_open:
            for i in range(self.document.page_count):
                pw = PageWidget(self, i)
                self.pages_layout.addWidget(pw, alignment=Qt.AlignHCenter)
                self.page_widgets.append(pw)
        self.thumbnails.rebuild()
        self.page_spin.blockSignals(True)
        self.page_spin.setMaximum(max(1, self.document.page_count))
        self.page_spin.blockSignals(False)
        self.page_count_label.setText(f" / {self.document.page_count}")
        QTimer.singleShot(0, self.update_visible_pages)

    def get_page_widget(self, index):
        return self.page_widgets[index]

    def current_page_index(self):
        if self.selected is not None:
            return self.selected[0]
        return 0

    def update_visible_pages(self):
        if not self.page_widgets:
            return
        viewport_h = self.scroll_area.viewport().height()
        top = self.scroll_area.verticalScrollBar().value()
        bottom = top + viewport_h
        buffer = viewport_h
        for pw in self.page_widgets:
            g = pw.geometry()
            if g.bottom() >= top - buffer and g.top() <= bottom + buffer:
                if not pw.rendered:
                    pw.render()

    def go_to_page(self, index):
        if 0 <= index < len(self.page_widgets):
            self.scroll_area.ensureWidgetVisible(self.page_widgets[index], 0, 0)
            self.update_visible_pages()

    def refresh_thumbnail(self, index):
        self.thumbnails.refresh_one(index)

    # ---------------------------------------------------------------
    # Zoom
    # ---------------------------------------------------------------

    def set_zoom(self, zoom):
        zoom = max(0.2, min(zoom, 6.0))
        if abs(zoom - self.zoom) < 1e-6:
            return
        self.zoom = zoom
        self.selected = None
        for pw in self.page_widgets:
            pw.invalidate()
        self.zoom_label.setText(f"{int(zoom * 100)}%")
        QTimer.singleShot(0, self.update_visible_pages)

    def zoom_in(self):
        self.set_zoom(self.zoom * 1.2)

    def zoom_out(self):
        self.set_zoom(self.zoom / 1.2)

    def zoom_reset(self):
        self.set_zoom(1.2)

    # ---------------------------------------------------------------
    # Page tools
    # ---------------------------------------------------------------

    def rotate_page(self, index, degrees):
        if not (0 <= index < self.document.page_count):
            return
        page = self.document.page(index)
        page.set_rotation((page.rotation + degrees) % 360)
        self.document.snapshot()
        self.selected = None
        self.get_page_widget(index).render()
        self.thumbnails.refresh_one(index)
        self.update_visible_pages()

    def delete_page(self, index):
        if not (0 <= index < self.document.page_count):
            return
        if self.document.page_count <= 1:
            QMessageBox.warning(self, "Cannot Delete", "The document must have at least one page.")
            return
        self.document.doc.delete_page(index)
        self.document.snapshot()
        self.selected = None
        self.rebuild_viewer()

    def insert_blank_page(self, index):
        if not self.document.is_open:
            return
        w, h = fitz.paper_size("a4")
        if self.document.page_count:
            ref = self.document.page(max(0, min(index - 1, self.document.page_count - 1)))
            w, h = ref.rect.width, ref.rect.height
        index = max(0, min(index, self.document.page_count))
        self.document.doc.new_page(pno=index, width=w, height=h)
        self.document.snapshot()
        self.selected = None
        self.rebuild_viewer()

    def extract_page(self, index):
        if not (0 <= index < self.document.page_count):
            return
        path, _ = QFileDialog.getSaveFileName(self, "Extract Page As", "", "PDF Files (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        new_doc = fitz.open()
        new_doc.insert_pdf(self.document.doc, from_page=index, to_page=index)
        new_doc.save(path)
        new_doc.close()
        QMessageBox.information(self, "Extracted", f"Page {index + 1} saved to {path}")

    def reorder_pages(self, order):
        if not self.document.is_open:
            return
        self.document.doc.select(order)
        self.document.snapshot()
        self.selected = None
        self.rebuild_viewer()

    # ---------------------------------------------------------------
    # Image / signature insertion
    # ---------------------------------------------------------------

    def insert_image_stamp(self):
        if not self.document.is_open:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Image", "", "Images (*.png *.jpg *.jpeg *.bmp *.gif)"
        )
        if not path:
            return
        self.pending_image_path = path
        self.set_tool(Tool.IMAGE_STAMP)
        self.hint_label.setText("Drag on the page to place the image.")

    def insert_drawn_signature(self):
        if not self.document.is_open:
            return
        dlg = SignaturePadDialog(self)
        if dlg.exec() != SignaturePadDialog.Accepted:
            return
        if dlg.is_empty():
            QMessageBox.information(self, "Empty Signature", "Please draw a signature first.")
            return
        path = dlg.save_to_temp_png()
        self.pending_image_path = path
        self.set_tool(Tool.IMAGE_STAMP)
        self.hint_label.setText("Drag on the page to place your signature.")

    # ---------------------------------------------------------------
    # Interaction callbacks invoked by PageWidget
    # ---------------------------------------------------------------

    def commit_ink(self, widget, points):
        page = widget.page()
        pdf_ops.add_ink(page, points, self.current_color, self.current_width)
        self.document.snapshot()
        widget.render()
        self.refresh_thumbnail(widget.page_index)

    def commit_drag_tool(self, widget, tool, p1, p2):
        page = widget.page()
        rect = fitz.Rect(p1, p2)
        rect.normalize()
        if rect.width < 4 and rect.height < 4 and tool in (
            Tool.RECT, Tool.ELLIPSE, Tool.TEXTBOX, Tool.STAMP, Tool.IMAGE_STAMP,
        ):
            default_w, default_h = (150, 40) if tool == Tool.TEXTBOX else (120, 60)
            rect = fitz.Rect(p1.x, p1.y, p1.x + default_w, p1.y + default_h)

        try:
            if tool == Tool.HIGHLIGHT:
                pdf_ops.add_highlight(page, p1, p2, self.current_color)
            elif tool == Tool.UNDERLINE:
                pdf_ops.add_underline(page, p1, p2, self.current_color)
            elif tool == Tool.STRIKEOUT:
                pdf_ops.add_strikeout(page, p1, p2, self.current_color)
            elif tool == Tool.RECT:
                pdf_ops.add_rect(page, rect, self.current_color, self.current_width)
            elif tool == Tool.ELLIPSE:
                pdf_ops.add_ellipse(page, rect, self.current_color, self.current_width)
            elif tool == Tool.LINE:
                pdf_ops.add_line(page, p1, p2, self.current_color, self.current_width, arrow=False)
            elif tool == Tool.ARROW:
                pdf_ops.add_line(page, p1, p2, self.current_color, self.current_width, arrow=True)
            elif tool == Tool.TEXTBOX:
                text, ok = QInputDialog.getMultiLineText(self, "Add Text Box", "Text:")
                if not ok or not text.strip():
                    return
                pdf_ops.add_freetext(page, rect, text, self.current_color, self.current_fontsize)
                self.set_tool(Tool.SELECT)
            elif tool == Tool.STAMP:
                pdf_ops.add_stamp(page, rect, self.current_stamp_name)
                self.set_tool(Tool.SELECT)
            elif tool == Tool.IMAGE_STAMP:
                if not self.pending_image_path:
                    return
                pdf_ops.insert_image(page, rect, image_path=self.pending_image_path)
                self.pending_image_path = None
                self.set_tool(Tool.SELECT)
            else:
                return
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not add annotation:\n{e}")
            return

        self.document.snapshot()
        widget.render()
        self.refresh_thumbnail(widget.page_index)

    def place_note(self, widget, pos):
        text, ok = QInputDialog.getMultiLineText(self, "Add Note", "Comment:")
        if not ok or not text.strip():
            return
        page = widget.page()
        pdf_pt = widget.to_pdf_point(pos)
        pdf_ops.add_note(page, pdf_pt, text, self.current_color)
        self.document.snapshot()
        widget.render()
        self.refresh_thumbnail(widget.page_index)

    def try_edit_annot_text(self, widget, pos):
        page = widget.page()
        pdf_pt = widget.to_pdf_point(pos)
        annot = pdf_ops.find_annot_at(page, pdf_pt)
        if annot is None:
            return
        if annot.type[0] not in (fitz.PDF_ANNOT_FREETEXT, fitz.PDF_ANNOT_TEXT):
            return
        old_text = annot.info.get("content", "")
        text, ok = QInputDialog.getMultiLineText(self, "Edit Text", "Content:", old_text)
        if ok:
            annot.set_info(content=text)
            annot.update()
            self.document.snapshot()
            widget.render()
            self.refresh_thumbnail(widget.page_index)

    def delete_selected(self):
        if self.selected is None:
            return
        page_index, annot = self.selected
        page = self.document.page(page_index)
        try:
            page.delete_annot(annot)
        except Exception:
            pass
        self.document.snapshot()
        self.selected = None
        self.get_page_widget(page_index).render()
        self.refresh_thumbnail(page_index)

    # ---------------------------------------------------------------
    # Select tool: click to select, drag to move
    # ---------------------------------------------------------------

    def begin_select_drag(self, widget, pos):
        page = widget.page()
        pdf_pt = widget.to_pdf_point(pos)
        annot = pdf_ops.find_annot_at(page, pdf_pt)
        if annot is None:
            self.selected = None
            self.select_dragging = False
            widget.update()
            return
        self.selected = (widget.page_index, annot)
        self.select_dragging = True
        self._select_start_px = pos
        self.select_offset_px = QPoint(0, 0)
        widget.update()

    def update_select_drag(self, widget, pos):
        if not self.select_dragging or self.selected is None or self.selected[0] != widget.page_index:
            return
        self.select_offset_px = pos - self._select_start_px
        widget.update()

    def end_select_drag(self, widget, pos):
        if not self.select_dragging or self.selected is None or self.selected[0] != widget.page_index:
            self.select_dragging = False
            return
        page_index, annot = self.selected
        offset = pos - self._select_start_px
        self.select_dragging = False
        self.select_offset_px = QPoint(0, 0)
        if offset.manhattanLength() < 3:
            widget.update()
            return
        p0 = widget.to_pdf_point(QPoint(0, 0))
        p1 = widget.to_pdf_point(QPoint(offset.x(), offset.y()))
        dx, dy = p1.x - p0.x, p1.y - p0.y
        try:
            pdf_ops.move_annot(annot, dx, dy)
        except Exception as e:
            QMessageBox.warning(self, "Cannot Move", f"This annotation type could not be moved:\n{e}")
            widget.update()
            return
        self.document.snapshot()
        widget.render()
        self.refresh_thumbnail(page_index)
