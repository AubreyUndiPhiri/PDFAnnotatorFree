import os
import webbrowser

from PySide6.QtWidgets import (
    QMainWindow, QTabWidget, QToolBar, QFileDialog, QMessageBox, QInputDialog,
    QColorDialog, QSpinBox, QDoubleSpinBox, QComboBox, QLabel, QPushButton,
    QStatusBar, QMenu,
)
from PySide6.QtGui import QAction, QActionGroup, QColor, QKeySequence, QShortcut, QIcon, QPixmap
from PySide6.QtCore import Qt, QSize

from .document_tab import DocumentTab
from .dialogs import SignaturePadDialog, PropertiesDialog, ToolStylesDialog, FindBar
from .tools import (
    Tool, STAMP_NAMES, UNITS, STYLED_TOOLS, DEFAULT_TOOL_STYLE,
    TOOL_STYLE_OVERRIDES, TOOL_SHORTCUTS, TOOL_LABELS, TOOL_HINTS,
)

APP_TITLE = "PDF Annotator Free"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)

        self.current_tool = Tool.SELECT
        self.tool_styles = {t: {**DEFAULT_TOOL_STYLE, **TOOL_STYLE_OVERRIDES.get(t, {})} for t in Tool}
        self.current_stamp_name = STAMP_NAMES[0]
        self.pending_image_path = None
        self.annotation_clipboard = []
        self.paste_count = 0
        self.favorites = set()
        self._untitled_counter = 0

        self._build_ui()
        self._wire_shortcuts()

        self.new_tab()

    # ---------------------------------------------------------------
    # Shared style state (per current_tool)
    # ---------------------------------------------------------------

    @property
    def current_color(self):
        return QColor(*self.tool_styles[self.current_tool]["color"])

    @current_color.setter
    def current_color(self, qcolor):
        self.tool_styles[self.current_tool]["color"] = (qcolor.red(), qcolor.green(), qcolor.blue())

    @property
    def current_width(self):
        return self.tool_styles[self.current_tool]["width"]

    @current_width.setter
    def current_width(self, value):
        self.tool_styles[self.current_tool]["width"] = value

    @property
    def current_fontsize(self):
        return self.tool_styles[self.current_tool]["fontsize"]

    @current_fontsize.setter
    def current_fontsize(self, value):
        self.tool_styles[self.current_tool]["fontsize"] = value

    @property
    def current_opacity(self):
        return self.tool_styles[self.current_tool]["opacity"]

    # ---------------------------------------------------------------
    # UI construction
    # ---------------------------------------------------------------

    def _build_ui(self):
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._on_tab_switched)
        self.setCentralWidget(self.tabs)

        self.setStatusBar(QStatusBar())
        self.hint_label = QLabel("")
        self.statusBar().addPermanentWidget(self.hint_label)

        self.find_bar = FindBar(self)
        self.find_bar.findRequested.connect(self._on_find_requested)

        self._build_toolbar()
        self._build_menu()

    def current_tab(self) -> DocumentTab:
        return self.tabs.currentWidget()

    def _build_toolbar(self):
        self.tool_toolbar = QToolBar("Tools")
        self.tool_toolbar.setMovable(False)
        self.tool_toolbar.setIconSize(QSize(20, 20))
        self.addToolBar(self.tool_toolbar)

        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_actions = {}

        def add_tool_action(tool, shortcut=None):
            label = TOOL_LABELS.get(tool, tool.name.title())
            act = QAction(label, self)
            act.setCheckable(True)
            act.setToolTip(TOOL_HINTS.get(tool, label))
            if shortcut:
                act.setShortcut(shortcut)
            act.triggered.connect(lambda checked, t=tool: self.set_tool(t))
            act.setData(tool)
            self.tool_group.addAction(act)
            self.tool_toolbar.addAction(act)
            self.tool_actions[tool] = act
            return act

        self.action_select = add_tool_action(Tool.SELECT, TOOL_SHORTCUTS[Tool.SELECT])
        add_tool_action(Tool.EXTRACT_TEXT, TOOL_SHORTCUTS[Tool.EXTRACT_TEXT])
        add_tool_action(Tool.PAN, TOOL_SHORTCUTS[Tool.PAN])
        add_tool_action(Tool.ZOOM, TOOL_SHORTCUTS[Tool.ZOOM])
        self.tool_toolbar.addSeparator()
        add_tool_action(Tool.HIGHLIGHT)
        add_tool_action(Tool.UNDERLINE)
        add_tool_action(Tool.STRIKEOUT)
        add_tool_action(Tool.NOTE)
        self.tool_toolbar.addSeparator()
        add_tool_action(Tool.INK, TOOL_SHORTCUTS[Tool.INK])
        add_tool_action(Tool.MARKER, TOOL_SHORTCUTS[Tool.MARKER])
        add_tool_action(Tool.TEXTBOX, TOOL_SHORTCUTS[Tool.TEXTBOX])
        add_tool_action(Tool.FORMULA)
        add_tool_action(Tool.STAMP, TOOL_SHORTCUTS[Tool.STAMP])
        self.tool_toolbar.addSeparator()
        add_tool_action(Tool.LINE, TOOL_SHORTCUTS[Tool.LINE])
        add_tool_action(Tool.ARROW, TOOL_SHORTCUTS[Tool.ARROW])
        add_tool_action(Tool.RECT, TOOL_SHORTCUTS[Tool.RECT])
        add_tool_action(Tool.ELLIPSE, TOOL_SHORTCUTS[Tool.ELLIPSE])
        add_tool_action(Tool.POLYGON, TOOL_SHORTCUTS[Tool.POLYGON])
        add_tool_action(Tool.DIMENSION, TOOL_SHORTCUTS[Tool.DIMENSION])
        self.tool_toolbar.addSeparator()
        add_tool_action(Tool.ERASER, TOOL_SHORTCUTS[Tool.ERASER])
        add_tool_action(Tool.LASSO, TOOL_SHORTCUTS[Tool.LASSO])
        add_tool_action(Tool.SNAPSHOT, TOOL_SHORTCUTS[Tool.SNAPSHOT])
        add_tool_action(Tool.CROP, TOOL_SHORTCUTS[Tool.CROP])
        add_tool_action(Tool.MEASURE, TOOL_SHORTCUTS[Tool.MEASURE])
        self.tool_toolbar.addSeparator()
        add_tool_action(Tool.LASER_POINTER, TOOL_SHORTCUTS[Tool.LASER_POINTER])
        add_tool_action(Tool.POINTER, TOOL_SHORTCUTS[Tool.POINTER])

        for tool, act in self.tool_actions.items():
            btn = self.tool_toolbar.widgetForAction(act)
            if btn is not None:
                btn.setContextMenuPolicy(Qt.CustomContextMenu)
                btn.customContextMenuRequested.connect(
                    lambda pos, t=tool, b=btn: self._show_tool_context_menu(t, b, pos)
                )

        self.action_select.setChecked(True)

        self.tool_toolbar.addSeparator()
        self.tool_toolbar.addWidget(QLabel(" Stamp: "))
        self.stamp_combo = QComboBox()
        self.stamp_combo.addItems(STAMP_NAMES)
        self.stamp_combo.currentTextChanged.connect(self._set_stamp_name)
        self.tool_toolbar.addWidget(self.stamp_combo)

        self.tool_toolbar.addSeparator()
        insert_image_btn = QPushButton("Insert Image...")
        insert_image_btn.clicked.connect(self.insert_image_stamp)
        self.tool_toolbar.addWidget(insert_image_btn)

        sign_btn = QPushButton("Draw Signature...")
        sign_btn.clicked.connect(self.insert_drawn_signature)
        self.tool_toolbar.addWidget(sign_btn)

        self.tool_toolbar.addSeparator()
        self.tool_toolbar.addWidget(QLabel(" Color: "))
        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(26, 26)
        self.color_btn.clicked.connect(self.pick_color)
        self.tool_toolbar.addWidget(self.color_btn)

        self.tool_toolbar.addWidget(QLabel("  Width: "))
        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(0.5, 20.0)
        self.width_spin.setSingleStep(0.5)
        self.width_spin.valueChanged.connect(self._set_width)
        self.tool_toolbar.addWidget(self.width_spin)

        self.tool_toolbar.addWidget(QLabel("  Font: "))
        self.font_spin = QSpinBox()
        self.font_spin.setRange(6, 96)
        self.font_spin.valueChanged.connect(self._set_fontsize)
        self.tool_toolbar.addWidget(self.font_spin)

        self._refresh_style_controls()

        self.nav_toolbar = QToolBar("Navigation")
        self.nav_toolbar.setMovable(False)
        self.addToolBar(self.nav_toolbar)

        zoom_out_btn = QPushButton("-")
        zoom_out_btn.setFixedWidth(28)
        zoom_out_btn.clicked.connect(lambda: self.current_tab().zoom_out())
        self.nav_toolbar.addWidget(zoom_out_btn)

        self.zoom_combo = QComboBox()
        self.zoom_combo.setEditable(True)
        self.zoom_combo.addItems(["50%", "75%", "100%", "125%", "150%", "200%", "400%"])
        self.zoom_combo.setFixedWidth(80)
        self.zoom_combo.activated.connect(self._on_zoom_combo_changed)
        self.zoom_combo.lineEdit().returnPressed.connect(
            lambda: self._on_zoom_combo_changed(self.zoom_combo.currentIndex())
        )
        self.nav_toolbar.addWidget(self.zoom_combo)

        zoom_in_btn = QPushButton("+")
        zoom_in_btn.setFixedWidth(28)
        zoom_in_btn.clicked.connect(lambda: self.current_tab().zoom_in())
        self.nav_toolbar.addWidget(zoom_in_btn)

        zoom_reset_btn = QPushButton("Reset")
        zoom_reset_btn.clicked.connect(lambda: self.current_tab().zoom_reset())
        self.nav_toolbar.addWidget(zoom_reset_btn)

        self.nav_toolbar.addSeparator()
        self.nav_toolbar.addWidget(QLabel(" Page: "))
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.setMaximum(1)
        self.page_spin.valueChanged.connect(lambda v: self.current_tab().go_to_page(v - 1))
        self.nav_toolbar.addWidget(self.page_spin)
        self.page_count_label = QLabel(" / 1")
        self.nav_toolbar.addWidget(self.page_count_label)

        self.nav_toolbar.addSeparator()
        self.nav_toolbar.addWidget(QLabel(" Unit: "))
        self.unit_combo = QComboBox()
        self.unit_combo.addItems([u[0] for u in UNITS])
        self.unit_combo.currentIndexChanged.connect(self._on_unit_changed)
        self.nav_toolbar.addWidget(self.unit_combo)

    def _build_menu(self):
        menubar = self.menuBar()
        self._build_file_menu(menubar)
        self._build_edit_menu(menubar)
        self._build_tool_menu(menubar)
        self._build_view_menu(menubar)
        self._build_window_menu(menubar)

    def _add_menu_action(self, menu, text, slot, shortcut=None, checkable=False):
        act = QAction(text, self)
        if shortcut:
            act.setShortcut(shortcut)
        if checkable:
            act.setCheckable(True)
        act.triggered.connect(slot)
        menu.addAction(act)
        return act

    def _build_file_menu(self, menubar):
        m = menubar.addMenu("&File")
        self._add_menu_action(m, "New Document", self.new_tab, "Ctrl+T")
        self._add_menu_action(m, "Combine Files...", self.combine_files, "Alt+C")
        self._add_menu_action(m, "Open...", self.open_document, QKeySequence.Open)
        self._add_menu_action(m, "Save", self.save_document, QKeySequence.Save)
        self._add_menu_action(m, "Save As...", self.save_document_as, QKeySequence.SaveAs)
        self._add_menu_action(m, "Save as Template", self.save_as_template)
        self._add_menu_action(m, "Save All", self.save_all)
        m.addSeparator()
        self._add_menu_action(m, "Close", self.close_current_tab, "Ctrl+W")
        self._add_menu_action(m, "Close All", self.close_all_tabs)
        m.addSeparator()
        self._add_menu_action(m, "Properties", self.show_properties, "Ctrl+D")
        self._add_menu_action(m, "Send Mail...", self.send_mail)
        self._add_menu_action(m, "Print...", self.print_document, QKeySequence.Print)
        m.addSeparator()
        self._add_menu_action(m, "Split Every Page to Separate Files...", self.split_pdf)
        m.addSeparator()
        self._add_menu_action(m, "Exit", self.close, "Alt+F4")

    def _build_edit_menu(self, menubar):
        m = menubar.addMenu("&Edit")
        self._add_menu_action(m, "Undo", self.undo, QKeySequence.Undo)
        self._add_menu_action(m, "Redo", self.redo, QKeySequence.Redo)
        m.addSeparator()
        self._add_menu_action(m, "Cut", self.cut_selected, QKeySequence.Cut)
        self._add_menu_action(m, "Copy", self.copy_selected, QKeySequence.Copy)
        self._add_menu_action(m, "Paste", self.paste, QKeySequence.Paste)
        self._add_menu_action(m, "Paste Without Formatting", self.paste_without_formatting, "Shift+Ctrl+V")
        self._add_menu_action(m, "Delete", self.delete_selected, QKeySequence.Delete)
        m.addSeparator()
        self._add_menu_action(m, "Find...", self.show_find_bar, QKeySequence.Find)
        self._add_menu_action(m, "Insert Image", self.insert_image_stamp)

        sel_menu = m.addMenu("Selection")
        self._add_menu_action(sel_menu, "Select All on Page", lambda: self.current_tab().select_all_on_page())
        self._add_menu_action(sel_menu, "Deselect All", lambda: self.current_tab().deselect_all())
        self._add_menu_action(sel_menu, "Invert Selection", lambda: self.current_tab().invert_selection_on_page())

        page_menu = m.addMenu("Page")
        self._add_menu_action(page_menu, "Insert Blank Page",
                               lambda: self.current_tab().insert_blank_page(self.current_tab().current_page_index() + 1))
        self._add_menu_action(page_menu, "Delete Current Page",
                               lambda: self.current_tab().delete_page(self.current_tab().current_page_index()))
        self._add_menu_action(page_menu, "Rotate Left",
                               lambda: self.current_tab().rotate_page(self.current_tab().current_page_index(), -90))
        self._add_menu_action(page_menu, "Rotate Right",
                               lambda: self.current_tab().rotate_page(self.current_tab().current_page_index(), 90))
        self._add_menu_action(page_menu, "Extract Current Page...",
                               lambda: self.current_tab().extract_page(self.current_tab().current_page_index()))

        doc_menu = m.addMenu("Document")
        self._add_menu_action(doc_menu, "Combine Files...", self.combine_files)
        self._add_menu_action(doc_menu, "Split Document...", self.split_pdf)
        self._add_menu_action(doc_menu, "Properties...", self.show_properties)

        m.addSeparator()
        self._add_menu_action(m, "Melt All Annotations", self.melt_all_annotations)
        self._add_menu_action(m, "Remove All Annotations", self.remove_all_annotations)

    def _build_tool_menu(self, menubar):
        m = menubar.addMenu("&Tool")
        for tool, act in self.tool_actions.items():
            m.addAction(act)

        self.favorites_menu = m.addMenu("Favorites")
        self._rebuild_favorites_menu()

        m.addSeparator()
        self._add_menu_action(m, "Tool Styles...", self.show_tool_styles)

    def _rebuild_favorites_menu(self):
        self.favorites_menu.clear()
        if not self.favorites:
            empty = self.favorites_menu.addAction("(right-click any tool button to pin it here)")
            empty.setEnabled(False)
            return
        for tool in self.favorites:
            act = self.favorites_menu.addAction(TOOL_LABELS.get(tool, tool.name))
            act.triggered.connect(lambda checked, t=tool: self.set_tool(t))

    def _show_tool_context_menu(self, tool, widget, pos):
        menu = QMenu(self)
        label = "Remove from Favorites" if tool in self.favorites else "Add to Favorites"
        action = menu.addAction(label)
        chosen = menu.exec(widget.mapToGlobal(pos))
        if chosen == action:
            self.toggle_favorite(tool)

    def toggle_favorite(self, tool):
        if tool in self.favorites:
            self.favorites.discard(tool)
        else:
            self.favorites.add(tool)
        self._rebuild_favorites_menu()

    def _build_view_menu(self, menubar):
        m = menubar.addMenu("&View")
        self._add_menu_action(m, "Zoom In", lambda: self.current_tab().zoom_in(), QKeySequence.ZoomIn)
        self._add_menu_action(m, "Zoom Out", lambda: self.current_tab().zoom_out(), QKeySequence.ZoomOut)
        m.addSeparator()
        self._add_menu_action(m, "Full Screen", self.toggle_full_screen, "Ctrl+L")
        self._add_menu_action(m, "Full Screen (in Window)", self.toggle_full_screen_in_window, "Alt+L")
        m.addSeparator()
        self._add_menu_action(m, "Actual Size", lambda: self.current_tab().actual_size(), "Ctrl+0")
        self._add_menu_action(m, "Fit to Size", lambda: self.current_tab().fit_to_size(), "Ctrl+5")
        self._add_menu_action(m, "Fit to Width", lambda: self.current_tab().fit_to_width(), "Ctrl+6")

        layout_menu = m.addMenu("Page Layout")
        single_act = self._add_menu_action(layout_menu, "Single Page",
                                            lambda: self.current_tab().set_page_layout_mode("single"),
                                            checkable=True)
        continuous_act = self._add_menu_action(layout_menu, "Continuous",
                                                lambda: self.current_tab().set_page_layout_mode("continuous"),
                                                checkable=True)
        continuous_act.setChecked(True)
        layout_group = QActionGroup(self)
        layout_group.addAction(single_act)
        layout_group.addAction(continuous_act)

        aux_menu = m.addMenu("Auxiliary Lines")
        self._add_menu_action(aux_menu, "Add Horizontal Guide", lambda: self.current_tab().add_guide("h"))
        self._add_menu_action(aux_menu, "Add Vertical Guide", lambda: self.current_tab().add_guide("v"))
        self._add_menu_action(aux_menu, "Clear All Guides", lambda: self.current_tab().clear_guides())

        goto_menu = m.addMenu("Go to")
        self._add_menu_action(goto_menu, "First Page", lambda: self.current_tab().go_to_page(0))
        self._add_menu_action(goto_menu, "Previous Page",
                               lambda: self.current_tab().go_to_page(self.current_tab().current_page_index() - 1))
        self._add_menu_action(goto_menu, "Next Page",
                               lambda: self.current_tab().go_to_page(self.current_tab().current_page_index() + 1))
        self._add_menu_action(goto_menu, "Last Page",
                               lambda: self.current_tab().go_to_page(self.current_tab().document.page_count - 1))
        self._add_menu_action(goto_menu, "Go to Page...", self.go_to_page_dialog)

        self.hide_annots_action = self._add_menu_action(
            m, "Hide Annotations", self._toggle_hide_annotations, checkable=True
        )

        sidebar_menu = m.addMenu("Sidebar")
        self.sidebar_action = self._add_menu_action(
            sidebar_menu, "Show Thumbnails", self._toggle_sidebar, checkable=True
        )
        self.sidebar_action.setChecked(True)

        toolbars_menu = m.addMenu("Toolbars")
        self.tool_toolbar_action = self._add_menu_action(
            toolbars_menu, "Tool Toolbar", lambda checked: self.tool_toolbar.setVisible(checked), checkable=True
        )
        self.tool_toolbar_action.setChecked(True)
        self.nav_toolbar_action = self._add_menu_action(
            toolbars_menu, "Navigation Toolbar", lambda checked: self.nav_toolbar.setVisible(checked), checkable=True
        )
        self.nav_toolbar_action.setChecked(True)

    def _build_window_menu(self, menubar):
        self.window_menu = menubar.addMenu("&Window")
        self._rebuild_window_menu()

    def _rebuild_window_menu(self):
        self.window_menu.clear()
        for i in range(self.tabs.count()):
            act = self.window_menu.addAction(self.tabs.tabText(i))
            act.setCheckable(True)
            act.setChecked(i == self.tabs.currentIndex())
            act.triggered.connect(lambda checked, idx=i: self.tabs.setCurrentIndex(idx))

    def _wire_shortcuts(self):
        sc = QShortcut(QKeySequence(Qt.Key_Backspace), self)
        sc.activated.connect(self.delete_selected)

    # ---------------------------------------------------------------
    # Tab lifecycle
    # ---------------------------------------------------------------

    def new_tab(self):
        self._untitled_counter += 1
        tab = DocumentTab(self)
        index = self.tabs.addTab(tab, f"Untitled {self._untitled_counter}")
        self.tabs.setCurrentIndex(index)
        return tab

    def open_files_as_tabs(self, paths):
        for path in paths:
            tab = DocumentTab(self)
            try:
                tab.load(path)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not open file:\n{e}")
                tab.deleteLater()
                continue
            index = self.tabs.addTab(tab, os.path.basename(path))
            self.tabs.setCurrentIndex(index)

    def on_tab_content_changed(self, tab):
        index = self.tabs.indexOf(tab)
        if index < 0:
            return
        name = tab.display_name()
        if tab.document.dirty:
            name = "*" + name
        self.tabs.setTabText(index, name)
        self._rebuild_window_menu()
        if tab is self.current_tab():
            self._sync_toolbar_to_tab(tab)

    def on_zoom_changed(self, zoom):
        self.zoom_combo.setCurrentText(f"{int(zoom * 100)}%")

    def _on_tab_switched(self, index):
        tab = self.tabs.widget(index)
        if tab is None:
            return
        self._sync_toolbar_to_tab(tab)
        self._rebuild_window_menu()

    def _sync_toolbar_to_tab(self, tab):
        self.page_spin.blockSignals(True)
        self.page_spin.setMaximum(max(1, tab.document.page_count))
        self.page_spin.setValue(tab.current_page_index() + 1)
        self.page_spin.blockSignals(False)
        self.page_count_label.setText(f" / {tab.document.page_count}")
        self.zoom_combo.setCurrentText(f"{int(tab.zoom * 100)}%")
        self.unit_combo.blockSignals(True)
        self.unit_combo.setCurrentIndex(tab.unit_index)
        self.unit_combo.blockSignals(False)
        self.hide_annots_action.setChecked(tab.hide_annotations)

    def _tab_dirty(self, tab) -> bool:
        return tab.document.is_open and tab.document.dirty

    def _confirm_close_tab(self, tab) -> bool:
        """Returns True if it's OK to proceed closing (saved, discarded, or clean)."""
        if not self._tab_dirty(tab):
            return True
        index = self.tabs.indexOf(tab)
        name = tab.display_name()
        resp = QMessageBox.question(
            self, "Unsaved Changes", f'Save changes to "{name}" before closing?',
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
        )
        if resp == QMessageBox.Save:
            self._save_tab(tab)
            return not tab.document.dirty
        if resp == QMessageBox.Discard:
            return True
        return False

    def close_tab(self, index):
        tab = self.tabs.widget(index)
        if tab is None:
            return
        if not self._confirm_close_tab(tab):
            return
        self.tabs.removeTab(index)
        tab.deleteLater()
        if self.tabs.count() == 0:
            self.new_tab()
        self._rebuild_window_menu()

    def close_current_tab(self):
        self.close_tab(self.tabs.currentIndex())

    def close_all_tabs(self):
        while self.tabs.count():
            before = self.tabs.count()
            self.close_tab(0)
            if self.tabs.count() == before:  # user cancelled
                return

    # ---------------------------------------------------------------
    # Tool / style state
    # ---------------------------------------------------------------

    def set_tool(self, tool):
        self.current_tool = tool
        if tool in self.tool_actions:
            self.tool_actions[tool].setChecked(True)
        tab = self.current_tab()
        if tab is not None:
            tab.selected = []
            for pw in tab.page_widgets:
                pw.update()
            if tool != Tool.LASER_POINTER:
                tab.hide_laser_pointer()
            tab.set_hint(TOOL_HINTS.get(tool, ""))
        self._refresh_style_controls()

    def _refresh_style_controls(self):
        style = self.tool_styles[self.current_tool]
        self._update_color_button(QColor(*style["color"]))
        self.width_spin.blockSignals(True)
        self.width_spin.setValue(style["width"])
        self.width_spin.blockSignals(False)
        self.font_spin.blockSignals(True)
        self.font_spin.setValue(style.get("fontsize", 12))
        self.font_spin.blockSignals(False)

    def _set_stamp_name(self, name):
        self.current_stamp_name = name

    def _set_width(self, value):
        self.current_width = value

    def _set_fontsize(self, value):
        self.current_fontsize = value

    def _on_unit_changed(self, index):
        tab = self.current_tab()
        if tab is not None:
            tab.unit_index = index

    def pick_color(self):
        color = QColorDialog.getColor(self.current_color, self, "Choose Color")
        if color.isValid():
            self.current_color = color
            self._update_color_button(color)

    def _update_color_button(self, color):
        pix = QPixmap(20, 20)
        pix.fill(color)
        self.color_btn.setIcon(QIcon(pix))

    def _on_zoom_combo_changed(self, index):
        text = self.zoom_combo.currentText().strip().rstrip("%")
        try:
            pct = float(text)
        except ValueError:
            return
        self.current_tab().set_zoom(pct / 100.0)

    # ---------------------------------------------------------------
    # Document lifecycle (File menu)
    # ---------------------------------------------------------------

    def open_document(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Open PDF", "", "PDF Files (*.pdf)")
        if not paths:
            return
        self.open_files_as_tabs(paths)

    def _save_tab(self, tab):
        if not tab.document.is_open:
            return
        if tab.document.path:
            try:
                tab.save()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not save:\n{e}")
            else:
                self.statusBar().showMessage("Saved", 3000)
                self.on_tab_content_changed(tab)
        else:
            self._save_tab_as(tab)

    def _save_tab_as(self, tab, directory=""):
        path, _ = QFileDialog.getSaveFileName(self, "Save PDF As", directory, "PDF Files (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        try:
            tab.save(path)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not save:\n{e}")
        else:
            self.statusBar().showMessage(f"Saved to {path}", 3000)
            self.on_tab_content_changed(tab)

    def save_document(self):
        tab = self.current_tab()
        if tab:
            self._save_tab(tab)

    def save_document_as(self):
        tab = self.current_tab()
        if tab:
            self._save_tab_as(tab)

    def save_as_template(self):
        tab = self.current_tab()
        if not tab or not tab.document.is_open:
            return
        templates_dir = os.path.join(os.path.expanduser("~"), "Documents", "PDFAnnotatorFree", "Templates")
        os.makedirs(templates_dir, exist_ok=True)
        self._save_tab_as(tab, directory=templates_dir)

    def save_all(self):
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if tab.document.is_open and tab.document.dirty:
                self._save_tab(tab)

    def combine_files(self):
        tab = self.current_tab()
        if tab is None:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Select PDFs to Combine In", "", "PDF Files (*.pdf)")
        if not paths:
            return
        tab.merge_pdfs(paths)
        QMessageBox.information(self, "Combined", f"Combined {len(paths)} file(s) into the document.")

    def split_pdf(self):
        tab = self.current_tab()
        if not tab or not tab.document.is_open or tab.document.page_count == 0:
            return
        directory = QFileDialog.getExistingDirectory(self, "Choose Output Folder")
        if not directory:
            return
        count = tab.split_pdf(directory)
        QMessageBox.information(self, "Split Complete", f"Saved {count} file(s) to {directory}")

    def show_properties(self):
        tab = self.current_tab()
        if not tab or not tab.document.is_open:
            return
        dlg = PropertiesDialog(tab.show_properties_data(), self)
        if dlg.exec() == PropertiesDialog.Accepted:
            tab.apply_properties_data(dlg.get_values())
            self.on_tab_content_changed(tab)

    def send_mail(self):
        tab = self.current_tab()
        if not tab or not tab.document.is_open:
            return
        if not tab.document.path or tab.document.dirty:
            resp = QMessageBox.question(
                self, "Save First?", "The document must be saved before it can be attached to an email. Save now?",
                QMessageBox.Save | QMessageBox.Cancel,
            )
            if resp != QMessageBox.Save:
                return
            self._save_tab(tab)
            if not tab.document.path:
                return
        webbrowser.open(f"mailto:?subject={tab.display_name()}")
        QMessageBox.information(
            self, "Send Mail",
            f"Your email client has been opened. Please attach the file manually:\n{tab.document.path}\n\n"
            "(Windows cannot automatically attach files to a new email.)",
        )

    def print_document(self):
        tab = self.current_tab()
        if not tab or not tab.document.is_open:
            return
        from PySide6.QtPrintSupport import QPrinter, QPrintDialog

        printer = QPrinter(QPrinter.HighResolution)
        dlg = QPrintDialog(printer, self)
        if dlg.exec() != QPrintDialog.Accepted:
            return
        self._render_to_printer(tab, printer)

    def _render_to_printer(self, tab, printer):
        from PySide6.QtGui import QPainter, QImage
        from . import pdf_ops

        painter = QPainter(printer)
        try:
            for i in range(tab.document.page_count):
                if i > 0:
                    printer.newPage()
                page = tab.document.page(i)
                dpi = printer.resolution()
                zoom = dpi / 72.0
                pix = page.get_pixmap(matrix=pdf_ops.render_matrix(zoom), alpha=False)
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
                target = painter.viewport()
                scaled = img.scaled(target.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                painter.drawImage(0, 0, scaled)
        finally:
            painter.end()

    def closeEvent(self, event):
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if not self._confirm_close_tab(tab):
                event.ignore()
                return
        event.accept()

    # ---------------------------------------------------------------
    # Undo / redo / clipboard / delete (Edit menu, active tab)
    # ---------------------------------------------------------------

    def undo(self):
        tab = self.current_tab()
        if tab:
            tab.undo()

    def redo(self):
        tab = self.current_tab()
        if tab:
            tab.redo()

    def cut_selected(self):
        tab = self.current_tab()
        if tab:
            tab.cut_selected()

    def copy_selected(self):
        tab = self.current_tab()
        if tab:
            tab.copy_selected()

    def paste(self):
        tab = self.current_tab()
        if tab:
            tab.paste(override_style=False)

    def paste_without_formatting(self):
        tab = self.current_tab()
        if tab:
            tab.paste(override_style=True)

    def delete_selected(self):
        tab = self.current_tab()
        if tab:
            tab.delete_selected()

    def remove_all_annotations(self):
        tab = self.current_tab()
        if tab:
            tab.remove_all_annotations()

    def melt_all_annotations(self):
        tab = self.current_tab()
        if not tab:
            return
        resp = QMessageBox.question(
            self, "Melt All Annotations",
            "This permanently flattens every annotation into the page content. Continue?",
            QMessageBox.Yes | QMessageBox.Cancel,
        )
        if resp == QMessageBox.Yes:
            tab.melt_all_annotations()

    # ---------------------------------------------------------------
    # Find
    # ---------------------------------------------------------------

    def show_find_bar(self):
        self.find_bar.show()
        self.find_bar.raise_()
        self.find_bar.focus_input()

    def _on_find_requested(self, query, forward):
        tab = self.current_tab()
        if tab:
            tab.find_text(query, forward)

    # ---------------------------------------------------------------
    # View menu actions
    # ---------------------------------------------------------------

    def toggle_full_screen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def toggle_full_screen_in_window(self):
        hidden = self.tool_toolbar.isVisible()
        self.tool_toolbar.setVisible(not hidden)
        self.nav_toolbar.setVisible(not hidden)
        self.menuBar().setVisible(not hidden)
        tab = self.current_tab()
        if tab:
            tab.thumbnails.setVisible(not hidden)

    def _toggle_hide_annotations(self, checked):
        tab = self.current_tab()
        if tab:
            tab.toggle_hide_annotations(checked)

    def _toggle_sidebar(self, checked):
        tab = self.current_tab()
        if tab:
            tab.thumbnails.setVisible(checked)

    def go_to_page_dialog(self):
        tab = self.current_tab()
        if not tab or not tab.document.is_open:
            return
        n, ok = QInputDialog.getInt(
            self, "Go to Page", "Page number:", tab.current_page_index() + 1, 1, tab.document.page_count
        )
        if ok:
            tab.go_to_page(n - 1)

    # ---------------------------------------------------------------
    # Image / signature insertion
    # ---------------------------------------------------------------

    def insert_image_stamp(self):
        tab = self.current_tab()
        if not tab or not tab.document.is_open:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Image", "", "Images (*.png *.jpg *.jpeg *.bmp *.gif)"
        )
        if not path:
            return
        tab.start_image_stamp(path)

    def insert_drawn_signature(self):
        tab = self.current_tab()
        if not tab or not tab.document.is_open:
            return
        dlg = SignaturePadDialog(self)
        if dlg.exec() != SignaturePadDialog.Accepted:
            return
        if dlg.is_empty():
            QMessageBox.information(self, "Empty Signature", "Please draw a signature first.")
            return
        path = dlg.save_to_temp_png()
        tab.start_image_stamp(path)

    # ---------------------------------------------------------------
    # Tool Styles dialog
    # ---------------------------------------------------------------

    def show_tool_styles(self):
        display_styles = {t: self.tool_styles[t] for t in STYLED_TOOLS}
        dlg = ToolStylesDialog(display_styles, TOOL_LABELS, self)
        dlg.exec()
        self._refresh_style_controls()
