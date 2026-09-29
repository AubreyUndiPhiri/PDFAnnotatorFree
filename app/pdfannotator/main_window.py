import os
import webbrowser
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QTabWidget, QTabBar, QToolBar, QToolButton, QFileDialog, QMessageBox,
    QInputDialog, QColorDialog, QSpinBox, QDoubleSpinBox, QComboBox, QLabel,
    QStatusBar, QMenu, QWidget, QSizePolicy,
)
from PySide6.QtGui import QAction, QActionGroup, QColor, QKeySequence, QShortcut, QIcon, QPixmap, QPainter
from PySide6.QtCore import Qt, QSize, QRectF

from .document_tab import DocumentTab
from . import fonts, icons, theme
from .dialogs import SignaturePadDialog, PropertiesDialog, ToolStylesDialog, FindBar
from .tools import (
    Tool, STAMP_NAMES, UNITS, STYLED_TOOLS, DEFAULT_TOOL_STYLE,
    TOOL_STYLE_OVERRIDES, TOOL_SHORTCUTS, TOOL_LABELS, TOOL_HINTS, TOOL_ICONS,
    TOOL_GROUPS, WIDTH_TOOLS, FONT_TOOLS, UNIT_TOOLS,
)

APP_TITLE = "Aupedian Annotators"


def resource_path(*parts):
    base_dir = Path(__file__).resolve().parents[1]
    return str(base_dir.joinpath(*parts))


def swatch_pixmap(color, size=36):
    """Rounded colour chip used by colour-picker buttons."""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QColor(0, 0, 0, 40))
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), size * 0.22, size * 0.22)
    painter.end()
    return pix


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        theme.apply()
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(QIcon(resource_path("assets", "aupedian_annotators.svg")))

        fonts.register_custom_fonts()
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
    def current_fontname(self):
        return self.tool_styles[self.current_tool].get("fontname", fonts.DEFAULT_FONT)

    @current_fontname.setter
    def current_fontname(self, value):
        self.tool_styles[self.current_tool]["fontname"] = value

    @property
    def current_opacity(self):
        return self.tool_styles[self.current_tool]["opacity"]

    # ---------------------------------------------------------------
    # UI construction
    # ---------------------------------------------------------------

    def _build_ui(self):
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(True)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setElideMode(Qt.ElideMiddle)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._on_tab_switched)
        self.setCentralWidget(self.tabs)

        self.setStatusBar(QStatusBar())
        self.statusBar().setSizeGripEnabled(False)
        self.hint_label = QLabel("")
        self.statusBar().addWidget(self.hint_label, 1)
        self.status_tool_label = QLabel("")
        self.status_page_label = QLabel("")
        self.status_zoom_label = QLabel("")
        for label in (self.status_tool_label, self.status_page_label, self.status_zoom_label):
            self.statusBar().addPermanentWidget(label)

        self.find_bar = FindBar(self)
        self.find_bar.findRequested.connect(self._on_find_requested)

        self._create_actions()
        self._build_command_bar()
        self._build_toolbar()
        self._build_menu()

    def current_tab(self) -> DocumentTab:
        return self.tabs.currentWidget()

    def _action(self, text, slot, icon=None, shortcut=None, tip=None, checkable=False):
        act = QAction(text, self)
        if icon:
            act.setIcon(icons.icon(icon))
        if shortcut:
            act.setShortcut(shortcut)
        if checkable:
            act.setCheckable(True)
        keys = act.shortcut().toString(QKeySequence.NativeText)
        act.setToolTip(f"{tip or text.replace('...', '')}" + (f"  ({keys})" if keys else ""))
        act.triggered.connect(self._finish_text_editing)
        act.triggered.connect(slot)
        return act

    def _finish_text_editing(self, *_):
        tab = self.current_tab()
        if tab is not None:
            tab.finish_text_editing()

    def _create_actions(self):
        """Actions shared by the toolbars and the menus (one icon, one
        shortcut, one tooltip per command)."""
        tab = self.current_tab
        a = self._action
        self.act_new = a("New Document", self.new_tab, "file-new", "Ctrl+T")
        self.act_open = a("Open...", self.open_document, "open", QKeySequence.Open)
        self.act_save = a("Save", self.save_document, "save", QKeySequence.Save)
        self.act_save_as = a("Save As...", self.save_document_as, None, QKeySequence.SaveAs)
        self.act_save_all = a("Save All", self.save_all, "save-all")
        self.act_print = a("Print...", self.print_document, "print", QKeySequence.Print)
        self.act_combine = a("Combine Files...", self.combine_files, "combine", "Alt+C")
        self.act_split = a("Split Every Page to Separate Files...", self.split_pdf, "split")
        self.act_properties = a("Properties...", self.show_properties, "properties", "Ctrl+D")
        self.act_mail = a("Send Mail...", self.send_mail, "mail")

        self.act_undo = a("Undo", self.undo, "undo", QKeySequence.Undo)
        self.act_redo = a("Redo", self.redo, "redo", QKeySequence.Redo)
        self.act_cut = a("Cut", self.cut_selected, "cut", QKeySequence.Cut)
        self.act_copy = a("Copy", self.copy_selected, "copy", QKeySequence.Copy)
        self.act_paste = a("Paste", self.paste, "paste", QKeySequence.Paste)
        self.act_delete = a("Delete", self.delete_selected, "delete", QKeySequence.Delete)
        self.act_find = a("Find...", self.show_find_bar, "find", QKeySequence.Find)
        self.act_image = a("Insert Image...", self.insert_image_stamp, "image")
        self.act_signature = a("Draw Signature...", self.insert_drawn_signature, "signature")

        self.act_zoom_in = a("Zoom In", lambda: tab().zoom_in(), "zoom-in", QKeySequence.ZoomIn)
        self.act_zoom_out = a("Zoom Out", lambda: tab().zoom_out(), "zoom-out", QKeySequence.ZoomOut)
        self.act_actual = a("Actual Size", lambda: tab().actual_size(), "actual-size", "Ctrl+0")
        self.act_fit_page = a("Fit Page", lambda: tab().fit_to_size(), "fit-page", "Ctrl+5")
        self.act_fit_width = a("Fit Width", lambda: tab().fit_to_width(), "fit-width", "Ctrl+6")
        self.act_first = a("First Page", lambda: tab().go_to_page(0), "first-page")
        self.act_prev = a("Previous Page", lambda: tab().go_to_page(tab().current_page_index() - 1),
                          "chevron-left")
        self.act_next = a("Next Page", lambda: tab().go_to_page(tab().current_page_index() + 1),
                          "chevron-right")
        self.act_last = a("Last Page", lambda: tab().go_to_page(tab().document.page_count - 1), "last-page")
        self.act_sidebar = a("Page Thumbnails", self._toggle_sidebar, "sidebar", "F4", checkable=True)
        self.act_sidebar.setChecked(True)
        self.act_fullscreen = a("Full Screen", self.toggle_full_screen, "fullscreen", "Ctrl+L")

    @staticmethod
    def _spacer(width=None):
        w = QWidget()
        if width is None:
            w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        else:
            w.setFixedWidth(width)
        return w

    def _build_command_bar(self):
        self.nav_toolbar = QToolBar("Main")
        self.nav_toolbar.setObjectName("commandBar")
        self.nav_toolbar.setMovable(False)
        self.nav_toolbar.setIconSize(QSize(18, 18))
        self.addToolBar(self.nav_toolbar)
        bar = self.nav_toolbar

        for act in (self.act_open, self.act_save, self.act_print):
            bar.addAction(act)
        bar.addSeparator()
        bar.addAction(self.act_undo)
        bar.addAction(self.act_redo)
        bar.addSeparator()
        bar.addAction(self.act_image)
        bar.addAction(self.act_signature)
        bar.addSeparator()
        bar.addAction(self.act_find)
        bar.addAction(self.act_sidebar)

        bar.addWidget(self._spacer())

        bar.addAction(self.act_zoom_out)
        self.zoom_combo = QComboBox()
        self.zoom_combo.setEditable(True)
        self.zoom_combo.addItems(["50%", "75%", "100%", "125%", "150%", "200%", "300%", "400%"])
        self.zoom_combo.setFixedWidth(86)
        self.zoom_combo.setToolTip("Zoom level")
        self.zoom_combo.activated.connect(self._on_zoom_combo_changed)
        self.zoom_combo.lineEdit().returnPressed.connect(
            lambda: self._on_zoom_combo_changed(self.zoom_combo.currentIndex())
        )
        bar.addWidget(self.zoom_combo)
        bar.addAction(self.act_zoom_in)
        bar.addAction(self.act_fit_width)
        bar.addAction(self.act_fit_page)
        bar.addSeparator()

        bar.addAction(self.act_prev)
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.setMaximum(1)
        self.page_spin.setFixedWidth(62)
        self.page_spin.setAlignment(Qt.AlignRight)
        self.page_spin.setToolTip("Go to page")
        self.page_spin.valueChanged.connect(lambda v: self.current_tab().go_to_page(v - 1))
        bar.addWidget(self.page_spin)
        self.page_count_label = QLabel("of 1")
        bar.addWidget(self.page_count_label)
        bar.addAction(self.act_next)

    def _build_toolbar(self):
        self.addToolBarBreak()
        self.tool_toolbar = QToolBar("Tools")
        self.tool_toolbar.setObjectName("toolBar")
        self.tool_toolbar.setMovable(False)
        self.tool_toolbar.setIconSize(QSize(20, 20))
        self.addToolBar(self.tool_toolbar)

        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_actions = {}

        for g, group in enumerate(TOOL_GROUPS):
            if g:
                self.tool_toolbar.addSeparator()
            for tool in group:
                self.tool_toolbar.addAction(self._make_tool_action(tool))

        for tool, act in self.tool_actions.items():
            btn = self.tool_toolbar.widgetForAction(act)
            if btn is not None:
                btn.setContextMenuPolicy(Qt.CustomContextMenu)
                btn.customContextMenuRequested.connect(
                    lambda pos, t=tool, b=btn: self._show_tool_context_menu(t, b, pos)
                )

        self.action_select = self.tool_actions[Tool.SELECT]
        self.action_select.setChecked(True)

        # ---- contextual properties: only what the active tool uses is shown
        self.tool_toolbar.addWidget(self._spacer(12))
        self._property_actions = {}

        def prop(key, label, widget):
            acts = [self.tool_toolbar.addWidget(QLabel(label))] if label else []
            acts.append(self.tool_toolbar.addWidget(widget))
            self._property_actions[key] = acts

        self.color_btn = QToolButton()
        self.color_btn.setObjectName("swatch")
        self.color_btn.setIconSize(QSize(18, 18))
        self.color_btn.setToolTip("Colour")
        self.color_btn.clicked.connect(self.pick_color)
        prop("color", "Colour", self.color_btn)

        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(0.5, 20.0)
        self.width_spin.setSingleStep(0.5)
        self.width_spin.setDecimals(1)
        self.width_spin.setSuffix(" pt")
        self.width_spin.setToolTip("Line width")
        self.width_spin.valueChanged.connect(self._set_width)
        prop("width", "Width", self.width_spin)

        self.font_family_combo = QComboBox()
        self.font_family_combo.addItems(fonts.available_fonts())
        self.font_family_combo.setMinimumWidth(120)
        self.font_family_combo.setToolTip("Font")
        self.font_family_combo.currentTextChanged.connect(self._set_fontname)
        prop("font", "Font", self.font_family_combo)

        self.font_spin = QSpinBox()
        self.font_spin.setRange(6, 96)
        self.font_spin.setSuffix(" pt")
        self.font_spin.setToolTip("Font size")
        self.font_spin.valueChanged.connect(self._set_fontsize)
        prop("fontsize", None, self.font_spin)

        self.stamp_combo = QComboBox()
        self.stamp_combo.addItems(STAMP_NAMES)
        self.stamp_combo.setToolTip("Stamp")
        self.stamp_combo.currentTextChanged.connect(self._set_stamp_name)
        prop("stamp", "Stamp", self.stamp_combo)

        self.unit_combo = QComboBox()
        self.unit_combo.addItems([u[0] for u in UNITS])
        self.unit_combo.setToolTip("Measurement unit")
        self.unit_combo.currentIndexChanged.connect(self._on_unit_changed)
        prop("unit", "Unit", self.unit_combo)

        self._refresh_style_controls()

    def _make_tool_action(self, tool):
        label = TOOL_LABELS.get(tool, tool.name.title())
        act = QAction(icons.icon(TOOL_ICONS[tool]), label, self)
        act.setCheckable(True)
        shortcut = TOOL_SHORTCUTS.get(tool)
        if shortcut:
            act.setShortcut(shortcut)
        key = f"  ({shortcut})" if shortcut else ""
        act.setToolTip(f"<b>{label}</b>{key}<br>{TOOL_HINTS.get(tool, '')}")
        act.setStatusTip(TOOL_HINTS.get(tool, label))
        act.triggered.connect(lambda checked, t=tool: self.set_tool(t))
        act.setData(tool)
        self.tool_group.addAction(act)
        self.tool_actions[tool] = act
        return act

    def _update_property_visibility(self):
        tool = self.current_tool
        visible = {
            "color": tool in STYLED_TOOLS,
            "width": tool in WIDTH_TOOLS,
            "font": tool in FONT_TOOLS,
            "fontsize": tool in FONT_TOOLS,
            "stamp": tool == Tool.STAMP,
            "unit": tool in UNIT_TOOLS,
        }
        for key, acts in self._property_actions.items():
            for act in acts:
                act.setVisible(visible[key])

    def _build_menu(self):
        menubar = self.menuBar()
        self._build_file_menu(menubar)
        self._build_edit_menu(menubar)
        self._build_tool_menu(menubar)
        self._build_view_menu(menubar)
        self._build_window_menu(menubar)

    def _add_menu_action(self, menu, text, slot, shortcut=None, checkable=False, icon=None):
        act = QAction(text, self)
        if icon:
            act.setIcon(icons.icon(icon))
        if shortcut:
            act.setShortcut(shortcut)
        if checkable:
            act.setCheckable(True)
        act.triggered.connect(self._finish_text_editing)
        act.triggered.connect(slot)
        menu.addAction(act)
        return act

    def _build_file_menu(self, menubar):
        m = menubar.addMenu("&File")
        m.addActions([self.act_new, self.act_open])
        m.addSeparator()
        m.addActions([self.act_save, self.act_save_as, self.act_save_all])
        self._add_menu_action(m, "Save as Template...", self.save_as_template)
        m.addSeparator()
        m.addActions([self.act_combine, self.act_split])
        m.addSeparator()
        m.addActions([self.act_properties, self.act_mail, self.act_print])
        m.addSeparator()
        self._add_menu_action(m, "Close", self.close_current_tab, "Ctrl+W", icon="close")
        self._add_menu_action(m, "Close All", self.close_all_tabs)
        m.addSeparator()
        self._add_menu_action(m, "Exit", self.close, "Alt+F4", icon="exit")

    def _build_edit_menu(self, menubar):
        m = menubar.addMenu("&Edit")
        m.addActions([self.act_undo, self.act_redo])
        m.addSeparator()
        m.addActions([self.act_cut, self.act_copy, self.act_paste])
        self._add_menu_action(m, "Paste Without Formatting", self.paste_without_formatting, "Shift+Ctrl+V")
        m.addAction(self.act_delete)
        m.addSeparator()
        m.addActions([self.act_find, self.act_image, self.act_signature])

        sel_menu = m.addMenu(icons.icon("select-all"), "Selection")
        self._add_menu_action(sel_menu, "Select All on Page", lambda: self.current_tab().select_all_on_page(),
                              "Ctrl+A", icon="select-all")
        self._add_menu_action(sel_menu, "Deselect All", lambda: self.current_tab().deselect_all())
        self._add_menu_action(sel_menu, "Invert Selection", lambda: self.current_tab().invert_selection_on_page())

        page_menu = m.addMenu(icons.icon("page-add"), "Page")
        tab = self.current_tab
        self._add_menu_action(page_menu, "Insert Blank Page",
                              lambda: tab().insert_blank_page(tab().current_page_index() + 1), icon="page-add")
        self._add_menu_action(page_menu, "Delete Current Page",
                              lambda: tab().delete_page(tab().current_page_index()), icon="page-delete")
        page_menu.addSeparator()
        self._add_menu_action(page_menu, "Rotate Left",
                              lambda: tab().rotate_page(tab().current_page_index(), -90), icon="rotate-left")
        self._add_menu_action(page_menu, "Rotate Right",
                              lambda: tab().rotate_page(tab().current_page_index(), 90), icon="rotate-right")
        page_menu.addSeparator()
        self._add_menu_action(page_menu, "Extract Current Page...",
                              lambda: tab().extract_page(tab().current_page_index()), icon="page-extract")

        m.addSeparator()
        self._add_menu_action(m, "Flatten All Annotations...", self.melt_all_annotations, icon="layers")
        self._add_menu_action(m, "Remove All Annotations", self.remove_all_annotations, icon="clear-all")

    def _build_tool_menu(self, menubar):
        m = menubar.addMenu("&Tools")
        for g, group in enumerate(TOOL_GROUPS):
            if g:
                m.addSeparator()
            for tool in group:
                m.addAction(self.tool_actions[tool])

        m.addSeparator()
        self.favorites_menu = m.addMenu(icons.icon("star"), "Favorites")
        self._rebuild_favorites_menu()
        self._add_menu_action(m, "Tool Styles...", self.show_tool_styles, icon="settings")

    def _rebuild_favorites_menu(self):
        self.favorites_menu.clear()
        if not self.favorites:
            empty = self.favorites_menu.addAction("Right-click a tool button to pin it here")
            empty.setEnabled(False)
            return
        for tool in self.favorites:
            act = self.favorites_menu.addAction(icons.icon(TOOL_ICONS[tool]), TOOL_LABELS.get(tool, tool.name))
            act.triggered.connect(lambda checked, t=tool: self.set_tool(t))

    def _show_tool_context_menu(self, tool, widget, pos):
        menu = QMenu(self)
        label = "Remove from Favorites" if tool in self.favorites else "Add to Favorites"
        action = menu.addAction(icons.icon("star"), label)
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
        m.addActions([self.act_zoom_in, self.act_zoom_out])
        m.addSeparator()
        m.addActions([self.act_actual, self.act_fit_page, self.act_fit_width])
        m.addSeparator()
        m.addAction(self.act_fullscreen)
        self._add_menu_action(m, "Full Screen (in Window)", self.toggle_full_screen_in_window, "Alt+L")
        m.addSeparator()

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

        goto_menu = m.addMenu("Go To")
        goto_menu.addActions([self.act_first, self.act_prev, self.act_next, self.act_last])
        goto_menu.addSeparator()
        self._add_menu_action(goto_menu, "Page...", self.go_to_page_dialog, "Ctrl+G")

        aux_menu = m.addMenu(icons.icon("guides"), "Guides")
        self._add_menu_action(aux_menu, "Add Horizontal Guide", lambda: self.current_tab().add_guide("h"))
        self._add_menu_action(aux_menu, "Add Vertical Guide", lambda: self.current_tab().add_guide("v"))
        self._add_menu_action(aux_menu, "Clear All Guides", lambda: self.current_tab().clear_guides())

        m.addSeparator()
        self.hide_annots_action = self._add_menu_action(
            m, "Hide Annotations", self._toggle_hide_annotations, checkable=True, icon="eye-off"
        )
        self.sidebar_action = self.act_sidebar
        m.addAction(self.act_sidebar)

        toolbars_menu = m.addMenu("Toolbars")
        self.nav_toolbar_action = self._add_menu_action(
            toolbars_menu, "Main Toolbar", lambda checked: self.nav_toolbar.setVisible(checked), checkable=True
        )
        self.nav_toolbar_action.setChecked(True)
        self.tool_toolbar_action = self._add_menu_action(
            toolbars_menu, "Tools Toolbar", lambda checked: self.tool_toolbar.setVisible(checked), checkable=True
        )
        self.tool_toolbar_action.setChecked(True)

    def _build_window_menu(self, menubar):
        self.window_menu = menubar.addMenu("&Window")
        self._rebuild_window_menu()

    def _rebuild_window_menu(self):
        self.window_menu.clear()
        for i in range(self.tabs.count()):
            act = self.window_menu.addAction(self.tabs.tabText(i).lstrip("● "))
            act.setCheckable(True)
            act.setChecked(i == self.tabs.currentIndex())
            act.triggered.connect(lambda checked, idx=i: self.tabs.setCurrentIndex(idx))

    def _wire_shortcuts(self):
        sc = QShortcut(QKeySequence(Qt.Key_Backspace), self)
        sc.activated.connect(self.delete_selected)

    # ---------------------------------------------------------------
    # Tab lifecycle
    # ---------------------------------------------------------------

    def _add_tab(self, tab, title):
        index = self.tabs.addTab(tab, icons.icon("note"), title)
        close_btn = QToolButton()
        close_btn.setIcon(icons.icon("close"))
        close_btn.setIconSize(QSize(12, 12))
        close_btn.setAutoRaise(True)
        close_btn.setToolTip("Close document")
        close_btn.clicked.connect(lambda: self.close_tab(self.tabs.indexOf(tab)))
        self.tabs.tabBar().setTabButton(index, QTabBar.RightSide, close_btn)
        self.tabs.setCurrentIndex(index)
        return index

    def new_tab(self):
        self._untitled_counter += 1
        tab = DocumentTab(self)
        self._add_tab(tab, f"Untitled {self._untitled_counter}")
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
            self._add_tab(tab, os.path.basename(path))

    def on_tab_content_changed(self, tab):
        index = self.tabs.indexOf(tab)
        if index < 0:
            return
        name = tab.display_name()
        if tab.document.dirty:
            name = "● " + name
        self.tabs.setTabText(index, name)
        self.tabs.setTabToolTip(index, tab.document.path or "Not saved yet")
        self._rebuild_window_menu()
        if tab is self.current_tab():
            self._sync_toolbar_to_tab(tab)

    def on_zoom_changed(self, zoom):
        self.zoom_combo.setCurrentText(f"{int(zoom * 100)}%")
        self.status_zoom_label.setText(f"{int(zoom * 100)}%")

    def on_current_page_changed(self, tab):
        """Called while scrolling so the page box and status bar follow the view."""
        if tab is not self.current_tab():
            return
        self._sync_page_controls(tab)

    def _sync_page_controls(self, tab):
        count = tab.document.page_count
        current = tab.current_page_index() + 1
        self.page_spin.blockSignals(True)
        self.page_spin.setMaximum(max(1, count))
        self.page_spin.setValue(current)
        self.page_spin.blockSignals(False)
        self.page_count_label.setText(f"of {count}")
        self.status_page_label.setText(f"Page {current} of {count}" if count else "")
        self.act_prev.setEnabled(current > 1)
        self.act_first.setEnabled(current > 1)
        self.act_next.setEnabled(current < count)
        self.act_last.setEnabled(current < count)

    def _on_tab_switched(self, index):
        tab = self.tabs.widget(index)
        if tab is None:
            return
        self._sync_toolbar_to_tab(tab)
        self._rebuild_window_menu()

    def _sync_toolbar_to_tab(self, tab):
        self._sync_page_controls(tab)
        self.zoom_combo.setCurrentText(f"{int(tab.zoom * 100)}%")
        self.status_zoom_label.setText(f"{int(tab.zoom * 100)}%")
        self.unit_combo.blockSignals(True)
        self.unit_combo.setCurrentIndex(tab.unit_index)
        self.unit_combo.blockSignals(False)
        self.hide_annots_action.setChecked(tab.hide_annotations)

    def _tab_dirty(self, tab) -> bool:
        return tab.document.is_open and tab.document.dirty

    def _confirm_close_tab(self, tab) -> bool:
        """Returns True if it's OK to proceed closing (saved, discarded, or clean)."""
        tab.finish_text_editing()
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
        tab = self.current_tab()
        if tab is not None and tab.text_edit is not None:
            tab.finish_text_editing()  # may itself switch to Select; the requested tool wins below
        self.current_tool = tool
        if tool in self.tool_actions:
            self.tool_actions[tool].setChecked(True)
        if tab is not None:
            for pw in tab.page_widgets:
                pw.apply_tool_cursor()
            tab.selected = []
            for pw in tab.page_widgets:
                pw.update()
            if tool != Tool.LASER_POINTER:
                tab.hide_laser_pointer()
            tab.set_hint(TOOL_HINTS.get(tool, ""))
        self.status_tool_label.setText(TOOL_LABELS.get(tool, tool.name.title()))
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
        self.font_family_combo.blockSignals(True)
        self.font_family_combo.setCurrentText(style.get("fontname", fonts.DEFAULT_FONT))
        self.font_family_combo.blockSignals(False)
        self._update_property_visibility()

    def _set_stamp_name(self, name):
        self.current_stamp_name = name

    def _set_width(self, value):
        self.current_width = value

    def _set_fontsize(self, value):
        self.current_fontsize = value
        self._restyle_text_edit()

    def _set_fontname(self, name):
        self.current_fontname = name
        self._restyle_text_edit()

    def _restyle_text_edit(self):
        tab = self.current_tab()
        if tab is not None:
            tab.update_text_edit_style()

    def _on_unit_changed(self, index):
        tab = self.current_tab()
        if tab is not None:
            tab.unit_index = index

    def pick_color(self):
        color = QColorDialog.getColor(self.current_color, self, "Choose Colour")
        if color.isValid():
            self.current_color = color
            self._update_color_button(color)
            self._restyle_text_edit()

    def _update_color_button(self, color):
        self.color_btn.setIcon(QIcon(swatch_pixmap(color)))

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
        tab.finish_text_editing()
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
        templates_dir = os.path.join(os.path.expanduser("~"), "Documents", "AupedianAnnotators", "Templates")
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
