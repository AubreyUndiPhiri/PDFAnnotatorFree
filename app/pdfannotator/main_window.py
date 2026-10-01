import os
import webbrowser
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QTabWidget, QTabBar, QToolBar, QToolButton, QFileDialog, QMessageBox,
    QInputDialog, QColorDialog, QSpinBox, QDoubleSpinBox, QComboBox, QLabel,
    QStatusBar, QMenu, QWidget, QSizePolicy, QWidgetAction,
)
from PySide6.QtGui import QAction, QActionGroup, QColor, QGuiApplication, QKeySequence, QShortcut, QIcon, QPixmap, QPainter
from PySide6.QtCore import Qt, QSize, QRectF

from .document_tab import DocumentTab
from .editor_tab import EditorTab
from . import fonts, icons, theme
from .dialogs import SignaturePadDialog, PropertiesDialog, ToolStylesDialog, FindBar, HandwritingFontDialog
from .tools import (
    Tool, STAMP_NAMES, UNITS, STYLED_TOOLS, DEFAULT_TOOL_STYLE,
    TOOL_STYLE_OVERRIDES, TOOL_SHORTCUTS, TOOL_LABELS, TOOL_HINTS, TOOL_ICONS,
    TOOL_GROUPS, WIDTH_TOOLS, FONT_TOOLS, UNIT_TOOLS,
)

APP_TITLE = "Aupedean Annotator"


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
        self.setWindowIcon(QIcon(resource_path("assets", "aupedean_annotator.svg")))

        fonts.register_custom_fonts()
        fonts.load_system_fonts()
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
        self.tabs.tabBar().setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.tabBar().customContextMenuRequested.connect(self._tab_menu)
        self.tabs.currentChanged.connect(self._on_tab_switched)
        self.setCentralWidget(self.tabs)

        self.setStatusBar(QStatusBar())
        self.statusBar().setSizeGripEnabled(False)
        self.hint_label = QLabel("")
        self.statusBar().addWidget(self.hint_label, 1)
        self.status_sync_label = QLabel("")
        self.status_sync_label.setObjectName("syncStatus")
        self.statusBar().addPermanentWidget(self.status_sync_label)
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
        self._build_ribbon_toggle()

    def _tab_menu(self, pos):
        """Right-click on a document tab."""
        from PySide6.QtWidgets import QMenu

        bar = self.tabs.tabBar()
        index = bar.tabAt(pos)
        if index < 0:
            return
        widget = self.tabs.widget(index)
        path = self.tab_path(widget)
        menu = QMenu(self)
        menu.addAction(icons.icon("close"), "Close", lambda: self.close_tab(self.tabs.indexOf(widget)))

        def close_others():
            for i in reversed(range(self.tabs.count())):
                if self.tabs.widget(i) is not widget:
                    self.close_tab(i)

        others = menu.addAction("Close Others", close_others)
        others.setEnabled(self.tabs.count() > 1)
        menu.addAction("Close All", lambda: [self.close_tab(i) for i in reversed(range(self.tabs.count()))])
        menu.addSeparator()
        folder = menu.addAction(icons.icon("folder"), "Show in Folder",
                                lambda: __import__("subprocess").Popen(["explorer", "/select,", os.path.normpath(path)]))
        copy = menu.addAction(icons.icon("copy"), "Copy File Path",
                              lambda: QGuiApplication.clipboard().setText(os.path.normpath(path)))
        folder.setEnabled(bool(path) and os.name == "nt")
        copy.setEnabled(bool(path))
        self.exec_menu(menu, bar.mapToGlobal(pos))

    def exec_menu(self, menu, global_pos):
        menu.exec(global_pos)

    def _build_ribbon_toggle(self):
        """The arrow at the far right of the menu bar: hides the toolbars (the
        ribbon) for more room, and brings them back."""
        self.ribbon_btn = QToolButton()
        self.ribbon_btn.setObjectName("ribbonToggle")
        self.ribbon_btn.setAutoRaise(True)
        self.ribbon_btn.setIconSize(QSize(16, 16))
        self.ribbon_btn.clicked.connect(self.act_ribbon.trigger)
        self.menuBar().setCornerWidget(self.ribbon_btn, Qt.TopRightCorner)
        from .pen_panel import PenPanel

        self.pen_panel = PenPanel(self)
        self._pen_panel_closed = False
        self._apply_toolbar_visibility()

    # ---- the pen panel: floats while the ribbon is hidden and a pen tool is in use
    def _update_pen_panel(self):
        from .pen_panel import PEN_TOOLS

        if not hasattr(self, "pen_panel"):
            return
        if self.ribbon_shown:
            self._pen_panel_closed = False
            want = False
        else:
            want = not self._pen_panel_closed and (self.pen_panel.isVisible() or self.current_tool in PEN_TOOLS)
        want = want and self.current_tab() is not None
        if want and not self.pen_panel.isVisible():
            self.pen_panel.refresh()
            self.pen_panel.show()
            self.pen_panel.place()
            self.pen_panel.raise_()
        elif not want:
            self.pen_panel.hide()

    def close_pen_panel(self):
        self._pen_panel_closed = True
        self.pen_panel.hide()

    def set_pen_color(self, color):
        """A colour from the pen panel: for the Pen or Marker (switching to the Pen from other tools)."""
        if self.current_tool not in (Tool.INK, Tool.MARKER):
            self.set_tool(Tool.INK)
        self.current_color = color
        self._update_color_button(color)
        self.pen_panel.refresh()

    def set_pen_width(self, width):
        self.width_spin.setValue(width)   # its signal stores it for the current tool
        self.pen_panel.refresh()

    def _set_ribbon_shown(self, shown):
        self.ribbon_shown = bool(shown)
        theme._settings().setValue("ui/ribbon", "true" if self.ribbon_shown else "false")
        self._apply_toolbar_visibility()

    def _apply_toolbar_visibility(self):
        """Both toolbars follow the ribbon arrow, their own View > Toolbars
        switch, and (the tools) whether a PDF is showing."""
        editor = isinstance(self.tabs.currentWidget(), EditorTab)
        self.nav_toolbar.setVisible(self.ribbon_shown and self.nav_toolbar_action.isChecked())
        self.tool_toolbar.setVisible(self.ribbon_shown and self.tool_toolbar_action.isChecked() and not editor)
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if isinstance(tab, DocumentTab):
                tab.set_compact(not self.ribbon_shown)
        self._update_pen_panel()
        if hasattr(self, "ribbon_btn"):
            self.ribbon_btn.setIcon(icons.icon("chevron-up" if self.ribbon_shown else "chevron-down"))
            keys = self.act_ribbon.shortcut().toString(QKeySequence.NativeText)
            self.ribbon_btn.setToolTip(("Hide the ribbon" if self.ribbon_shown else "Show the ribbon") + f"  ({keys})")

    def current_tab(self) -> DocumentTab:
        """The active PDF tab, or None while a Word / LaTeX tab is active."""
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, DocumentTab) else None

    def current_editor(self) -> EditorTab:
        """The active Word / LaTeX tab, or None while a PDF tab is active."""
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, EditorTab) else None

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
        self.act_new_word = a("New Word Document", self.new_word_document, "file-word", "Ctrl+Alt+W",
                              tip="Write a new Word document here")
        self.act_new_latex = a("New LaTeX Document...", self.new_latex_document, "file-latex", "Ctrl+Alt+L",
                               tip="Write a new LaTeX document here, from a template")
        self.act_open = a("Open...", self.open_document, "open", QKeySequence.Open)
        self.act_save = a("Save", self.save_document, "save", QKeySequence.Save)
        self.act_save_as = a("Save As...", self.save_document_as, None, QKeySequence.SaveAs)
        self.act_save_all = a("Save All", self.save_all, "save-all")
        self.act_print = a("Print...", self.print_document, "print", QKeySequence.Print)
        self.act_combine = a("Combine Files...", self.combine_files, "combine", "Alt+C")
        self.act_split = a("Split Every Page to Separate Files...", self.split_pdf, "split")
        self.act_properties = a("Properties...", self.show_properties, "properties", "Ctrl+D")
        self.act_mail = a("Send Mail...", self.send_mail, "mail")
        self.act_to_word = a("Convert to Word...", lambda: self.convert_document("word"), "file-word",
                             "Ctrl+Shift+W", tip="Convert the PDF to an editable Word document")
        self.act_to_latex = a("Convert to LaTeX...", lambda: self.convert_document("latex"), "file-latex",
                              "Ctrl+Shift+L", tip="Convert the PDF to an editable LaTeX project")

        self.act_undo = a("Undo", self.undo, "undo", QKeySequence.Undo)
        self.act_redo = a("Redo", self.redo, "redo", QKeySequence.Redo)
        self.act_cut = a("Cut", self.cut_selected, "cut", QKeySequence.Cut)
        self.act_copy = a("Copy", self.copy_selected, "copy", QKeySequence.Copy)
        self.act_paste = a("Paste", self.paste, "paste", QKeySequence.Paste)
        self.act_delete = a("Delete", self.delete_selected, "delete", QKeySequence.Delete)
        self.act_find = a("Find...", self.show_find_bar, "find", QKeySequence.Find)
        self.act_image = a("Insert Image...", self.insert_image_stamp, "image")
        self.act_signature = a("Draw Signature...", self.insert_drawn_signature, "signature")

        self.act_zoom_in = a("Zoom In", lambda: self._zoom("zoom_in", "zoom_in"), "zoom-in", QKeySequence.ZoomIn)
        self.act_zoom_out = a("Zoom Out", lambda: self._zoom("zoom_out", "zoom_out"), "zoom-out", QKeySequence.ZoomOut)
        self.act_actual = a("Actual Size", lambda: self._zoom("actual_size", "reset_zoom"), "actual-size", "Ctrl+0")
        self.act_fit_page = a("Fit Page", lambda: tab().fit_to_size(), "fit-page", "Ctrl+5")
        self.act_fit_width = a("Fit Width", lambda: tab().fit_to_width(), "fit-width", "Ctrl+6")
        self.act_first = a("First Page", lambda: tab().go_to_page(0), "first-page")
        self.act_prev = a("Previous Page", lambda: tab().go_to_page(tab().current_page_index() - 1),
                          "chevron-left")
        self.act_next = a("Next Page", lambda: tab().go_to_page(tab().current_page_index() + 1),
                          "chevron-right")
        self.act_last = a("Last Page", lambda: tab().go_to_page(tab().document.page_count - 1), "last-page")
        self.act_sidebar = a("Page Thumbnails", self._toggle_sidebar, "sidebar", "F4", checkable=True,
                             tip="Show or hide the page thumbnails")
        self.act_sidebar.setChecked(True)
        # Pen / marker options, remembered between sessions
        settings = theme._settings()
        self.ink_smoothing = settings.value("draw/smooth", "true") == "true"
        self.ink_pressure = settings.value("draw/pressure", "false") == "true"
        self.act_smooth_ink = a("Smooth Handwriting", self._set_ink_smoothing, "smooth-stroke", checkable=True,
                                tip="Smooth out shaky pen and marker strokes")
        self.act_smooth_ink.setChecked(self.ink_smoothing)
        self.act_pressure_ink = a("Pressure Sensitivity", self._set_ink_pressure, "pressure", checkable=True,
                                  tip="Pen strokes get thicker and thinner: with a pen tablet or stylus from "
                                      "how hard you press, with a mouse from how fast you draw")
        self.act_pressure_ink.setChecked(self.ink_pressure)
        self._create_text_format_actions()
        self.ribbon_shown = settings.value("ui/ribbon", "true") == "true"
        self.act_ribbon = a("Show Ribbon", self._set_ribbon_shown, None, "Ctrl+F1", checkable=True,
                            tip="Show or hide the toolbars at the top")
        self.act_ribbon.setChecked(self.ribbon_shown)
        self.act_fullscreen = a("Full Screen", self.toggle_full_screen, "fullscreen", "Ctrl+L")
        self.act_dark = a("Dark Mode", self.toggle_dark_mode, "moon", "Ctrl+Shift+D", checkable=True,
                          tip="Switch between the light (glass) and dark (clay) look")
        self.act_dark.setChecked(theme.mode == theme.DARK)

    TEXT_FORMATS = (("b", "Bold", "bold", "Ctrl+B"), ("i", "Italic", "italic", "Ctrl+I"),
                    ("u", "Underline", "text-underline", "Ctrl+U"), ("s", "Strikethrough", "strikeout", ""),
                    ("sup", "Superscript", "superscript", "Ctrl+Shift+="), ("sub", "Subscript", "subscript", "Ctrl+="))

    def _create_text_format_actions(self):
        """Bold, italic, underline, strikethrough, superscript, subscript and
        alignment for the Text tool. Unlike other actions they don't end the
        typing: they format the selected text (or what is typed next), or the
        whole of each selected text box."""
        self.text_format_actions = {}
        for kind, label, icon, keys in self.TEXT_FORMATS:
            act = QAction(icons.icon(icon), label, self)
            act.setCheckable(True)
            act.setToolTip(f"{label}  ({keys})" if keys else label)
            act.triggered.connect(lambda _c=False, k=kind: self._on_text_format(k))
            self.text_format_actions[kind] = act
        self.text_align = 0
        self.align_group = QActionGroup(self)
        for align, (label, icon) in enumerate((("Align Left", "align-left"), ("Centre", "align-center"),
                                               ("Align Right", "align-right"))):
            act = QAction(icons.icon(icon), label, self)
            act.setCheckable(True)
            act.setChecked(align == 0)
            act.setToolTip(label)
            act.triggered.connect(lambda _c=False, a=align: self._on_text_format(f"align{a}"))
            self.align_group.addAction(act)
            self.text_format_actions[f"align{align}"] = act

    def _on_text_format(self, kind):
        tab = self.current_tab()
        if kind.startswith("align"):
            self.text_align = int(kind[-1])
        applied = tab is not None and tab.format_text(kind)
        if applied and tab.text_edit is not None:
            self.sync_text_format(tab.text_edit["editor"])

    def pending_text_formats(self):
        """The formats switched on (before typing) for the next new text box."""
        return [k for k, *_ in self.TEXT_FORMATS if self.text_format_actions[k].isChecked()]

    def sync_text_format(self, editor):
        """The buttons show the formatting where the cursor is."""
        from .inline_text import run_style

        style = run_style(editor.currentCharFormat())
        states = {"b": "b" in style, "i": "i" in style, "u": "u" in style, "s": "s" in style,
                  "sup": style.get("v") == 1, "sub": style.get("v") == -1}
        for kind, on in states.items():
            self.text_format_actions[kind].setChecked(on)
        self.text_align = editor.align
        act = self.text_format_actions[f"align{editor.align}"]
        act.setChecked(True)
        if hasattr(self, "align_button"):
            self.align_button.setIcon(act.icon())

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
        bar.addAction(self.act_to_word)
        bar.addAction(self.act_to_latex)
        bar.addSeparator()
        bar.addAction(self.act_undo)
        bar.addAction(self.act_redo)
        bar.addSeparator()
        bar.addAction(self.act_image)
        bar.addAction(self.act_signature)
        self._request_signature_slot = bar.addSeparator()  # the Request Signature button goes here
        bar.addSeparator()
        bar.addAction(self.act_find)
        bar.addAction(self.act_dark)

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
        self._zoom_combo_action = bar.addWidget(self.zoom_combo)
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
        self.page_spin.valueChanged.connect(lambda v: self.current_tab() and self.current_tab().go_to_page(v - 1))
        self._page_widget_actions = [bar.addWidget(self.page_spin)]
        self.page_count_label = QLabel("of 1")
        self._page_widget_actions.append(bar.addWidget(self.page_count_label))
        bar.addAction(self.act_next)

    def _build_toolbar(self):
        self.addToolBarBreak()
        self.tool_toolbar = QToolBar("Tools")
        self.tool_toolbar.setObjectName("toolBar")
        self.tool_toolbar.setMovable(False)
        self.tool_toolbar.setIconSize(QSize(19, 19))
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
        prop("color", None, self.color_btn)  # the swatch explains itself (tooltip: Colour)

        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(0.5, 20.0)
        self.width_spin.setSingleStep(0.5)
        self.width_spin.setDecimals(1)
        self.width_spin.setSuffix(" pt")
        self.width_spin.setToolTip("Line width")
        self.width_spin.valueChanged.connect(self._set_width)
        prop("width", None, self.width_spin)  # "2.5 pt" (tooltip: Line width)

        self.font_family_combo = QComboBox()
        fonts.fill_font_combo(self.font_family_combo, offer_create=True)
        self.font_family_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.font_family_combo.setMinimumContentsLength(12)   # not as wide as the longest font name
        self.font_family_combo.setMinimumWidth(140)
        self.font_family_combo.setToolTip("Font (type to search)")
        self.font_family_combo.textActivated.connect(self._on_font_picked)
        prop("font", None, self.font_family_combo)   # the tooltip names it

        self.font_spin = QSpinBox()
        self.font_spin.setRange(6, 96)
        self.font_spin.setSuffix(" pt")
        self.font_spin.setToolTip("Font size")
        self.font_spin.valueChanged.connect(self._set_fontsize)
        prop("fontsize", None, self.font_spin)

        format_acts = [self.tool_toolbar.addWidget(self._spacer(4))]
        for kind, act in self.text_format_actions.items():
            if kind.startswith("align"):
                continue
            button = QToolButton()
            button.setDefaultAction(act)
            button.setFocusPolicy(Qt.NoFocus)      # the text being typed keeps the focus
            format_acts.append(self.tool_toolbar.addWidget(button))
        # the three alignments share one button (its menu), to keep the toolbar short
        from PySide6.QtWidgets import QMenu

        self.align_button = QToolButton()
        self.align_button.setFocusPolicy(Qt.NoFocus)
        self.align_button.setPopupMode(QToolButton.InstantPopup)
        self.align_button.setToolTip("Alignment")
        align_menu = QMenu(self.align_button)
        align_menu.addActions(self.align_group.actions())
        self.align_button.setMenu(align_menu)
        self.align_group.triggered.connect(lambda act: self.align_button.setIcon(act.icon()))
        self.align_button.setIcon(self.text_format_actions["align0"].icon())
        format_acts.append(self.tool_toolbar.addWidget(self.align_button))
        self._property_actions["textformat"] = format_acts

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

        for key, act in (("smooth", self.act_smooth_ink), ("pressure", self.act_pressure_ink)):
            button = QToolButton()
            button.setObjectName(f"{key}Toggle")
            button.setDefaultAction(act)  # icon-only (the toolbar is full); the tooltip names it
            prop(key, None, button)

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
            "font": tool in FONT_TOOLS and tool != Tool.FORMULA,  # maths is set in its own fonts
            "fontsize": tool in FONT_TOOLS,
            "textformat": tool == Tool.TEXTBOX,
            "stamp": tool == Tool.STAMP,
            "unit": tool in UNIT_TOOLS,
            "smooth": tool in (Tool.INK, Tool.MARKER),
            "pressure": tool == Tool.INK,
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
        from .cloud.ui import CloudController

        self.cloud = CloudController(self)
        self.cloud.build_menu(menubar)
        file_actions = self._file_menu.actions()
        after_open = file_actions[file_actions.index(self.act_open) + 1] if self.act_open in file_actions[:-1] else None
        self._file_menu.insertAction(after_open, self.cloud.act_open)
        # signing: in the File menu and on the main toolbar
        after_convert = self.act_to_latex
        file_actions = self._file_menu.actions()
        anchor = file_actions[file_actions.index(after_convert) + 1] if after_convert in file_actions[:-1] else None
        self._file_menu.insertActions(anchor, [self.cloud.act_request, self.cloud.act_requests,
                                               self.cloud.act_add_returned, self.cloud.act_sign_account])
        self.nav_toolbar.insertAction(self._request_signature_slot, self.cloud.act_request)
        button = self.nav_toolbar.widgetForAction(self.cloud.act_request)
        if button is not None:  # labelled: it's the one new users look for
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            button.setText("Request Signature")
            button.setPopupMode(QToolButton.InstantPopup)   # the three ways, Quick Email first
        self._build_window_menu(menubar)
        self._collect_pdf_only_actions()

    def _collect_pdf_only_actions(self):
        """Every command that only makes sense on a PDF: they are disabled
        while a Word or LaTeX tab is active (so their shortcuts don't fire
        into the editor either)."""
        shared = {self.act_new, self.act_new_word, self.act_new_latex, self.act_open, self.act_save,
                  self.act_save_as, self.act_save_all, self.act_print, self.act_undo, self.act_redo, self.act_cut,
                  self.act_copy, self.act_paste, self.act_find, self.act_zoom_in, self.act_zoom_out,
                  self.act_actual, self.act_fullscreen, self.act_dark, self.nav_toolbar_action, self.act_ribbon}
        shared |= self.cloud.shared_actions()
        found = []

        def walk(menu):
            for act in menu.actions():
                if act.isSeparator():
                    continue
                if act.menu():
                    walk(act.menu())
                elif act not in shared and act.text().replace("&", "") not in ("Close", "Close All", "Exit"):
                    found.append(act)

        for top in self.menuBar().actions():
            if top.menu() and top.menu() is not self.window_menu and top.menu() is not self.favorites_menu:
                walk(top.menu())
        for bar in (self.nav_toolbar, self.tool_toolbar):
            for act in bar.actions():  # toolbar-only commands (widgets are hidden separately)
                if not (act.isSeparator() or act in shared or act in found or isinstance(act, QWidgetAction)):
                    found.append(act)
        self._pdf_only_actions = [a for a in found if a not in self._page_widget_actions
                                  and a is not self._zoom_combo_action]

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
        m.addActions([self.act_new, self.act_new_word, self.act_new_latex, self.act_open])
        self._file_menu = m  # "Open from Google Drive..." is added here by the Google Drive menu
        m.addSeparator()
        m.addActions([self.act_save, self.act_save_as, self.act_save_all])
        self._add_menu_action(m, "Save as Template...", self.save_as_template)
        m.addSeparator()
        m.addActions([self.act_combine, self.act_split])
        m.addSeparator()
        m.addActions([self.act_to_word, self.act_to_latex])
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
        m.addActions([self.act_smooth_ink, self.act_pressure_ink])
        m.addSeparator()
        hw_menu = m.addMenu(icons.icon("signature"), "Handwriting Font")
        self._add_menu_action(hw_menu, "Create Font from Your Handwriting...", self.create_handwriting_font,
                              icon="signature")
        self._add_menu_action(hw_menu, "Save Glyph Sheet to Print...", self.save_glyph_sheet, icon="print")
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
        m.addAction(self.act_dark)
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

        m.addAction(self.act_ribbon)
        toolbars_menu = m.addMenu("Toolbars")
        self.nav_toolbar_action = self._add_menu_action(
            toolbars_menu, "Main Toolbar", lambda _checked: self._apply_toolbar_visibility(), checkable=True
        )
        self.nav_toolbar_action.setChecked(True)
        self.tool_toolbar_action = self._add_menu_action(
            toolbars_menu, "Tools Toolbar", lambda _checked: self._apply_toolbar_visibility(), checkable=True
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

    def _add_tab(self, tab, title, icon="note"):
        if isinstance(tab, DocumentTab):
            tab.set_compact(not getattr(self, "ribbon_shown", True))
        index = self.tabs.addTab(tab, icons.icon(icon), title)
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

    def open_files_as_tabs(self, paths, check_signed=True):
        from .latex.editor import OPEN_SUFFIXES as LATEX_SUFFIXES
        from .word_editor import OPEN_SUFFIXES as WORD_SUFFIXES

        before = {self.tabs.widget(i) for i in range(self.tabs.count())}
        blank = [w for w in before if isinstance(w, DocumentTab)              # an untouched "Untitled"
                 and not w.document.path and not w.document.dirty]
        self._open_paths(paths, check_signed, WORD_SUFFIXES, LATEX_SUFFIXES)
        if any(self.tabs.widget(i) not in before for i in range(self.tabs.count())):
            for w in blank:   # the empty "Untitled" tab has served its purpose
                index = self.tabs.indexOf(w)
                if index >= 0:
                    self.tabs.removeTab(index)
                    w.deleteLater()
            self._rebuild_window_menu()

    def _open_paths(self, paths, check_signed, WORD_SUFFIXES, LATEX_SUFFIXES):
        for path in paths:
            # a signed copy of one of our signing files: add the signature to the original instead
            if check_signed and path.lower().endswith(".pdf") and hasattr(self, "cloud") \
                    and self.cloud.take_returned(path):
                continue
            existing = self._tab_for_path(path)
            if existing is not None:
                self.tabs.setCurrentWidget(existing)
                continue
            ext = os.path.splitext(path)[1].lower()
            if ext in WORD_SUFFIXES or ext == ".doc":
                self._open_editor("word", path)
                continue
            if ext in LATEX_SUFFIXES:
                self._open_editor("latex", path)
                continue
            tab = DocumentTab(self)
            try:
                tab.load(path)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not open file:\n{e}")
                tab.deleteLater()
                continue
            self._add_tab(tab, os.path.basename(path))

    # ---- helpers the Google Drive sync uses (any kind of tab) ----------------
    def tab_path(self, tab):
        if isinstance(tab, EditorTab):
            return tab.path
        if isinstance(tab, DocumentTab):
            return tab.document.path
        return None

    def tab_dirty(self, tab) -> bool:
        if isinstance(tab, EditorTab):
            return tab.dirty
        return isinstance(tab, DocumentTab) and self._tab_dirty(tab)

    def tab_for_path(self, path):
        return self._tab_for_path(path) if path else None

    def reload_tab(self, tab):
        """Show the file's new content after Google Drive replaced it."""
        if isinstance(tab, DocumentTab):
            page = tab.current_page_index()
            tab.load(tab.document.path)
            tab.go_to_page(min(page, tab.document.page_count - 1))
        elif isinstance(tab, EditorTab) and hasattr(tab, "watcher"):
            return  # the LaTeX editor notices changed files by itself
        elif isinstance(tab, EditorTab) and tab.path:
            tab.load(tab.path)
        self.on_tab_content_changed(tab)

    def update_sync_status(self):
        from .cloud import drive_desktop

        path = self.tab_path(self.tabs.currentWidget())
        text = self.cloud.sync.status_text(path) if path else ""
        entry = self.cloud.sync.entry(path) if path else None
        tip = f"Google Drive: {entry.name}" if entry else ""
        if not text and path:
            where = drive_desktop.containing_root(path)
            if where:
                text = f"In Google Drive ({where[0]}): Google Drive for desktop syncs every save"
                tip = path
        self.status_sync_label.setText(("☁ " + text) if text else "")
        self.status_sync_label.setToolTip(tip)

    def _tab_for_path(self, path):
        want = os.path.normcase(os.path.abspath(path))
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            have = tab.path if isinstance(tab, EditorTab) else tab.document.path
            if have and os.path.normcase(os.path.abspath(have)) == want:
                return tab
        return None

    def _open_editor(self, kind, path=None, template=None):
        try:
            if kind == "word":
                from .word_editor import WordTab

                editor = WordTab(self, path)
            else:
                from .latex.editor import LatexTab

                editor = LatexTab(self, path, template)
        except Exception as e:  # noqa: BLE001 - a file that can't be read
            QMessageBox.critical(self, "Error", f"Could not open file:\n{e}")
            return None
        editor.changed.connect(lambda e=editor: self.on_tab_content_changed(e))
        editor.open_path_requested.connect(lambda p: self.open_files_as_tabs([p]))
        self._add_tab(editor, editor.display_name(), editor.icon_name)
        self.on_tab_content_changed(editor)
        return editor

    def new_word_document(self):
        return self._open_editor("word")

    def new_latex_document(self):
        from .latex.editor import TEMPLATES

        name, ok = QInputDialog.getItem(self, "New LaTeX Document", "Start from:", list(TEMPLATES), 0, False)
        if ok:
            return self._open_editor("latex", template=name)
        return None

    def on_tab_content_changed(self, tab):
        index = self.tabs.indexOf(tab)
        if index < 0:
            return
        editor = isinstance(tab, EditorTab)
        name = tab.display_name()
        if (tab.dirty if editor else tab.document.dirty):
            name = "● " + name
        if self.tabs.tabText(index) != name:
            self.tabs.setTabText(index, name)
            self._rebuild_window_menu()
        self.tabs.setTabToolTip(index, (tab.path if editor else tab.document.path) or "Not saved yet")
        if tab is self.tabs.currentWidget():
            if editor:
                self._show_editor_status(tab)
            else:
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
        editor = isinstance(tab, EditorTab)
        for act in getattr(self, "_pdf_only_actions", []):
            act.setEnabled(not editor)
        self._apply_toolbar_visibility()
        for act in self._page_widget_actions:
            act.setVisible(not editor)
        self._zoom_combo_action.setVisible(not editor)
        zoomable = not editor or tab.supports_zoom
        for act in (self.act_zoom_in, self.act_zoom_out, self.act_actual):
            act.setEnabled(zoomable)
        if editor:
            self.hint_label.setText("")
            self._show_editor_status(tab)
        else:
            self.status_tool_label.setText(TOOL_LABELS.get(self.current_tool, self.current_tool.name.title()))
            self._sync_toolbar_to_tab(tab)
        self._rebuild_window_menu()
        if hasattr(self, "cloud"):
            self.update_sync_status()

    def _show_editor_status(self, editor):
        self.status_tool_label.setText(editor.kind_label)
        self.status_page_label.setText(editor.status_text())
        self.status_zoom_label.setText(f"{round(editor.zoom * 100)}%" if editor.supports_zoom else "")

    def _zoom(self, pdf_method, editor_method):
        editor = self.current_editor()
        if editor is not None:
            getattr(editor, editor_method)()
            self._show_editor_status(editor)
        elif self.current_tab() is not None:
            getattr(self.current_tab(), pdf_method)()

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
        if isinstance(tab, EditorTab):
            return tab.confirm_close()
        tab.finish_text_editing()
        if not self._tab_dirty(tab):
            return True
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
        if tool in (Tool.INK, Tool.MARKER, Tool.ERASER):
            self._pen_panel_closed = False   # picking a pen tool again brings the panel back
        self._update_pen_panel()

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
        if hasattr(self, "pen_panel"):
            self.pen_panel.refresh()

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

    def _on_font_picked(self, text):
        combo = self.font_family_combo
        if combo.currentData(Qt.UserRole) == fonts.CREATE_HANDWRITING:
            combo.setCurrentText(self.current_fontname)
            self.create_handwriting_font()
        elif text in fonts.available_fonts():
            self._set_fontname(text)
        else:
            combo.setCurrentText(self.current_fontname)  # half-typed search: keep the real font

    def create_handwriting_font(self):
        dlg = HandwritingFontDialog(self)
        dlg.fontCreated.connect(self._on_handwriting_font_created)
        dlg.exec()

    def _on_handwriting_font_created(self, family):
        for tool in (Tool.TEXTBOX, Tool.FORMULA):
            self.tool_styles[tool]["fontname"] = family
        fonts.fill_font_combo(self.font_family_combo, self.current_fontname, offer_create=True)
        self._refresh_style_controls()
        self._restyle_text_edit()

    def save_glyph_sheet(self):
        from PySide6.QtCore import QStandardPaths, QUrl
        from PySide6.QtGui import QDesktopServices
        from .handwriting.sheet import make_sheet

        docs = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        path, _ = QFileDialog.getSaveFileName(self, "Save Glyph Sheet",
                                              os.path.join(docs, f"{fonts.HANDWRITING_FONT}_glyph_sheet.pdf"),
                                              "PDF Files (*.pdf)")
        if path:
            make_sheet(path)
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

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
            self.pen_panel.refresh()

    def _update_color_button(self, color):
        self.color_btn.setIcon(QIcon(swatch_pixmap(color)))

    def _on_zoom_combo_changed(self, index):
        text = self.zoom_combo.currentText().strip().rstrip("%")
        try:
            pct = float(text)
        except ValueError:
            return
        if self.current_tab() is not None:
            self.current_tab().set_zoom(pct / 100.0)

    # ---------------------------------------------------------------
    # Document lifecycle (File menu)
    # ---------------------------------------------------------------

    def open_document(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open", "",
            "All Supported (*.pdf *.docx *.tex *.bib *.odt *.html *.htm *.md *.txt);;PDF Files (*.pdf);;"
            "Word Documents (*.docx);;LaTeX (*.tex *.bib *.sty *.cls);;Text and Web Pages (*.txt *.md *.html *.htm)")
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
        editor = self.current_editor()
        if editor is not None:
            if editor.save():
                self.statusBar().showMessage(f"Saved to {editor.path}", 3000)
            return
        tab = self.current_tab()
        if tab:
            self._save_tab(tab)

    def save_document_as(self, directory=""):
        editor = self.current_editor()
        if editor is not None:
            if editor.save_as(directory):
                self.statusBar().showMessage(f"Saved to {editor.path}", 3000)
            return
        tab = self.current_tab()
        if tab:
            self._save_tab_as(tab, directory=os.path.join(directory, tab.display_name()) if directory else "")

    def save_as_template(self):
        tab = self.current_tab()
        if not tab or not tab.document.is_open:
            return
        templates_dir = os.path.join(os.path.expanduser("~"), "Documents", "AupedeanAnnotator", "Templates")
        old_templates = os.path.join(os.path.expanduser("~"), "Documents", "AupedianAnnotators", "Templates")
        if os.path.isdir(old_templates) and not os.path.exists(templates_dir):
            import shutil  # carry templates over from before the app was renamed

            shutil.copytree(old_templates, templates_dir)
        os.makedirs(templates_dir, exist_ok=True)
        self._save_tab_as(tab, directory=templates_dir)

    def save_all(self):
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if isinstance(tab, EditorTab):
                if tab.dirty:
                    tab.save()
            elif tab.document.is_open and tab.document.dirty:
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

    def toggle_dark_mode(self, checked):
        theme.set_mode(theme.DARK if checked else theme.LIGHT)
        self.pen_panel.refresh()   # its colour chips and width dots are drawn in the look's colours

    def convert_document(self, fmt):
        tab = self.current_tab()
        if not tab or not tab.document.is_open or tab.document.page_count == 0:
            return
        from . import convert_dialog

        # A snapshot of the document as it is now, edits included; the
        # conversion thread never touches the open document itself
        self._conversion = convert_dialog.convert(self, fmt, tab.document.doc.tobytes(), tab.document.path,
                                                  tab.display_name(), tab.document.page_count)

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
        if self.current_editor() is not None:
            self.current_editor().print_document()
            return
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
        if self.current_editor() is not None:
            self.current_editor().undo()
            return
        tab = self.current_tab()
        if tab:
            tab.undo()

    def redo(self):
        if self.current_editor() is not None:
            self.current_editor().redo()
            return
        tab = self.current_tab()
        if tab:
            tab.redo()

    def cut_selected(self):
        if self.current_editor() is not None:
            self.current_editor().cut()
            return
        tab = self.current_tab()
        if tab:
            tab.cut_selected()

    def copy_selected(self):
        if self.current_editor() is not None:
            self.current_editor().copy()
            return
        tab = self.current_tab()
        if tab:
            tab.copy_selected()

    def paste(self):
        if self.current_editor() is not None:
            self.current_editor().paste()
            return
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
        if self.current_editor() is not None:
            self.current_editor().show_find()
            return
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
        hidden = self.menuBar().isVisible()
        if hidden:
            self.tool_toolbar.setVisible(False)
            self.nav_toolbar.setVisible(False)
        else:
            self._apply_toolbar_visibility()   # back to how the ribbon was
        self.menuBar().setVisible(not hidden)
        tab = self.current_tab()
        if tab:
            tab.thumbnails.setVisible(not hidden)

    def _toggle_hide_annotations(self, checked):
        tab = self.current_tab()
        if tab:
            tab.toggle_hide_annotations(checked)

    def _toggle_sidebar(self, checked):
        for i in range(self.tabs.count()):  # one setting for every PDF tab
            tab = self.tabs.widget(i)
            if isinstance(tab, DocumentTab):
                tab.thumbnails.setVisible(checked)

    def _set_ink_smoothing(self, checked):
        self.ink_smoothing = bool(checked)
        theme._settings().setValue("draw/smooth", "true" if checked else "false")

    def _set_ink_pressure(self, checked):
        self.ink_pressure = bool(checked)
        theme._settings().setValue("draw/pressure", "true" if checked else "false")

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
