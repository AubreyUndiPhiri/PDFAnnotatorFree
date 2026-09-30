"""Google Drive and signing-link dialogs, and CloudController, which adds
the Google Drive menu to the main window and ties the background sync and
signature checking to the open tabs."""
import calendar
import os
import threading
import time
import webbrowser

import pymupdf as fitz
from PySide6.QtCore import (
    QFileSystemWatcher, QMimeData, QObject, QRect, QRectF, QSettings, QSize, Qt, QTimer, QUrl, QUrlQuery, Signal,
)
from PySide6.QtGui import QAction, QColor, QDesktopServices, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import icons, theme
from ..dialogs import _button_row, _dialog_layout, _header, _primary
from . import drive_desktop, google_auth, sign_service, signing, signing_file, worker
from .drive import FOLDER, GOOGLE_DOC, DriveClient, is_openable, local_name
from .google_auth import ApiError, AuthError, Offline
from .signing import SetupError, SigningManager
from .sync import SyncManager

CLOUD_CONSOLE = {
    "project": "https://console.cloud.google.com/projectcreate",
    "drive_api": "https://console.cloud.google.com/apis/library/drive.googleapis.com",
    "script_api": "https://console.cloud.google.com/apis/library/script.googleapis.com",
    "consent": "https://console.cloud.google.com/auth/overview",
    "credentials": "https://console.cloud.google.com/apis/credentials",
}


def friendly(exc):
    if isinstance(exc, Offline):
        return "Can't reach Google. Check the internet connection and try again."
    if isinstance(exc, (AuthError, SetupError)):
        return str(exc)
    if isinstance(exc, ApiError):
        return f"Google said: {exc.message}"
    return f"{type(exc).__name__}: {exc}"


def _link_button(text, url, tip=""):
    btn = QPushButton(text)
    btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
    if tip:
        btn.setToolTip(f"<table width=440><tr><td>{tip}</td></tr></table>")   # rich text, wrapped
    return btn


def _muted(text):
    label = QLabel(text)
    label.setObjectName("muted")
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
    label.setOpenExternalLinks(True)
    return label


class DriveDesktopDialog(QDialog):
    """Shown when Google Drive for desktop isn't found: how to get it going."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Google Drive for desktop")
        self.setMinimumWidth(560)
        layout = _dialog_layout(self)
        installed = drive_desktop.installed_exe() is not None
        layout.addLayout(_header(
            "Google Drive isn't on this PC yet" if not installed else "Google Drive isn't running",
            "Google Drive for desktop is Google's free app that puts your Drive on this PC (as a \"Google Drive\" "
            "drive, usually G:). Open files from it here and Google syncs every save automatically. Nothing else "
            "needs setting up in Aupedean."))
        steps = ("1. Download and install Google Drive for desktop.\n2. Sign in with your Google account when it "
                 "asks.\n3. Come back here and choose Open from Google Drive again." if not installed else
                 "Start Google Drive and sign in if it asks, then choose Open from Google Drive again.")
        layout.addWidget(QLabel(steps))
        self.status = _muted("")
        layout.addWidget(self.status)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        check = QPushButton("Check Again")
        check.clicked.connect(self._check)
        if installed:
            main = _primary("Start Google Drive")
            main.clicked.connect(self._start)
        else:
            main = _primary("Download Google Drive")
            main.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(drive_desktop.DOWNLOAD_URL)))
        layout.addLayout(_button_row(close, check, main))

    def _start(self):
        if drive_desktop.start():
            self.status.setText("Starting Google Drive... When it has signed in, press Check Again.")

    def _check(self):
        if drive_desktop.find_roots(refresh=True):
            self.accept()
        else:
            self.status.setText("Google Drive still isn't showing up. It can take a minute after signing in.")


class _Progress(QObject):
    text = Signal(str)   # emitted from worker threads, shown on the UI thread


# --------------------------------------------------------------------------
# Connecting the Google account
# --------------------------------------------------------------------------

class ConnectDialog(QDialog):
    connected = Signal()

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._cancel = None
        self.setWindowTitle("Google Account")
        self.setMinimumWidth(600)
        layout = _dialog_layout(self)
        layout.addLayout(_header("Connect your Google account",
                                 "Open, edit and sync Google Drive files, and send signing links. Google needs "
                                 "a free \"OAuth client\" for this app: you create it once (about five minutes)."))
        self.steps = QWidget()
        steps = QVBoxLayout(self.steps)
        steps.setContentsMargins(0, 0, 0, 0)
        steps.setSpacing(8)
        for number, text, buttons in (
            ("1", "Create a Google Cloud project (any name, e.g. \"Aupedean\").",
             [("Open Google Cloud", CLOUD_CONSOLE["project"])]),
            ("2", "Enable the Google Drive API and the Apps Script API in that project.",
             [("Drive API", CLOUD_CONSOLE["drive_api"]), ("Apps Script API", CLOUD_CONSOLE["script_api"])]),
            ("3", "Set up the OAuth consent screen: choose External, fill in the app name and your email, add "
                  "your own Google address as a test user, then Publish app (otherwise Google signs you out "
                  "every 7 days).", [("Consent screen", CLOUD_CONSOLE["consent"])]),
            ("4", "Create credentials: OAuth client ID, application type \"Desktop app\". Download its JSON file.",
             [("Credentials", CLOUD_CONSOLE["credentials"])]),
        ):
            row = QHBoxLayout()
            label = QLabel(f"<b>{number}.</b> {text}")
            label.setWordWrap(True)
            row.addWidget(label, 1)
            for caption, url in buttons:
                row.addWidget(_link_button(caption, url, SETUP_TIPS.get(caption, "")))
            steps.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("<b>5.</b> Choose the downloaded file:"), 1)
        choose = QPushButton("Choose Client File...")
        choose.clicked.connect(self._choose_client)
        row.addWidget(choose)
        steps.addLayout(row)
        layout.addWidget(self.steps)
        self.client_label = _muted("")
        layout.addWidget(self.client_label)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        note = _muted("Google will say the app isn't verified: that's expected for your own private app. Choose "
                      "Continue. The sign-in is stored encrypted for your Windows account only.")
        layout.addWidget(note)
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(self._disconnect)
        self.new_client_btn = QPushButton("Use Another Client File...")
        self.new_client_btn.clicked.connect(self._choose_client)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        self.sign_in_btn = _primary("Sign In with Google")
        self.sign_in_btn.clicked.connect(self._sign_in)
        layout.addLayout(_button_row(close, self.sign_in_btn, leading=(self.disconnect_btn, self.new_client_btn)))
        self._refresh()

    def _refresh(self):
        client = google_auth.load_client()
        creds = google_auth.load_credentials()
        self.steps.setVisible(client is None)
        self.new_client_btn.setVisible(client is not None)
        self.client_label.setText(f"OAuth client: {client['client_id']}" if client else "")
        self.sign_in_btn.setEnabled(client is not None)
        self.disconnect_btn.setVisible(bool(creds))
        if creds:
            self.status.setText(f"Connected as <b>{creds.get('email') or 'your Google account'}</b>.")
            self.sign_in_btn.setText("Sign In Again")
        else:
            self.status.setText("Not connected.")
            self.sign_in_btn.setText("Sign In with Google")

    def _choose_client(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose the OAuth Client File", os.path.expanduser("~/Downloads"),
                                              "Google client file (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                client = google_auth.parse_client_json(f.read())
        except (OSError, AuthError) as e:
            QMessageBox.warning(self, "Google Account", str(e))
            return
        google_auth.save_client(client)
        self._refresh()

    def _sign_in(self):
        client = google_auth.load_client()
        if client is None:
            return
        self._cancel = threading.Event()
        self.sign_in_btn.setEnabled(False)
        self.status.setText("Finish signing in in your web browser...")

        def done(creds):
            self._cancel = None
            self.controller.reset_session()
            self._refresh()
            self.connected.emit()

        def failed(exc):
            self._cancel = None
            self.sign_in_btn.setEnabled(True)
            self.status.setText(f"<span style='color:{theme.DANGER}'>{friendly(exc)}</span>")

        worker.run(lambda: google_auth.sign_in(client, webbrowser.open, self._cancel), done, failed)

    def _disconnect(self):
        if QMessageBox.question(self, "Disconnect", "Disconnect the Google account? Synced files stay on this PC and "
                                "on Google Drive; they upload again after you reconnect.",
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        worker.run(google_auth.disconnect, lambda _r: (self.controller.reset_session(), self._refresh()))

    def reject(self):
        if self._cancel is not None:
            self._cancel.set()
        super().reject()


# --------------------------------------------------------------------------
# Browsing Google Drive
# --------------------------------------------------------------------------

def _icon_for(meta):
    mime = meta.get("mimeType", "")
    name = local_name(meta).lower()
    if mime == FOLDER:
        return icons.icon("folder")
    if name.endswith(".pdf"):
        return icons.icon("file-pdf")
    if mime == GOOGLE_DOC or name.endswith((".docx", ".odt")):
        return icons.icon("file-word")
    if name.endswith((".tex", ".bib", ".sty", ".cls")):
        return icons.icon("file-latex")
    return icons.icon("note")


class DriveBrowser(QDialog):
    """Pick a file to open (mode "open") or a folder to save into ("save")."""
    PLACES = [("My Drive", "root"), ("Shared with me", "shared"), ("Recent", "recent"), ("Starred", "starred")]

    def __init__(self, session, mode="open", suggested_name="", parent=None):
        super().__init__(parent)
        self.drive = DriveClient(session)
        self.mode = mode
        self.selected = None
        self.folder = {"id": "root", "name": "My Drive"}
        self._stack = []
        self._request = 0
        self.setWindowTitle("Open from Google Drive" if mode == "open" else "Save to Google Drive")
        self.resize(860, 560)
        layout = _dialog_layout(self)
        top = QHBoxLayout()
        self.back_btn = QPushButton()
        self.back_btn.setIcon(icons.icon("chevron-left"))
        self.back_btn.setToolTip("Back")
        self.back_btn.clicked.connect(self._back)
        self.path_label = QLabel("My Drive")
        self.path_label.setObjectName("dialogTitle")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search Google Drive")
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(self._search)
        top.addWidget(self.back_btn)
        top.addWidget(self.path_label, 1)
        top.addWidget(self.search, 1)
        layout.addLayout(top)
        body = QHBoxLayout()
        self.places = QListWidget()
        self.places.setFixedWidth(160)
        for label, _key in self.PLACES:
            self.places.addItem(label)
        self.places.currentRowChanged.connect(self._place)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Modified", "Owner"])
        self.tree.setRootIsDecorated(False)
        self.tree.setIconSize(QSize(18, 18))
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.itemDoubleClicked.connect(self._activated)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        body.addWidget(self.places)
        body.addWidget(self.tree, 1)
        layout.addLayout(body, 1)
        self.status = _muted("")
        layout.addWidget(self.status)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        if mode == "open":
            self.ok_btn = _primary("Open")
            self.ok_btn.setEnabled(False)
            self.name_edit = None
            layout.addLayout(_button_row(cancel, self.ok_btn))
        else:
            self.name_edit = QLineEdit(suggested_name)
            row = QHBoxLayout()
            row.addWidget(QLabel("File name"))
            row.addWidget(self.name_edit, 1)
            layout.addLayout(row)
            self.ok_btn = _primary("Save Here")
            layout.addLayout(_button_row(cancel, self.ok_btn))
        self.ok_btn.clicked.connect(self._accept)
        self.places.setCurrentRow(0)

    def _load(self, fetch, title):
        self._request += 1
        request = self._request
        self.path_label.setText(title)
        self.tree.clear()
        self.status.setText("Loading...")
        self.back_btn.setEnabled(bool(self._stack))

        def done(files):
            if request != self._request:
                return
            self.tree.clear()
            for meta in files:
                if meta.get("mimeType") == "application/vnd.google-apps.shortcut":
                    continue
                item = QTreeWidgetItem([meta["name"], meta.get("modifiedTime", "")[:16].replace("T", " "),
                                        ", ".join(o.get("displayName", "") for o in meta.get("owners", []))])
                item.setIcon(0, _icon_for(meta))
                item.setData(0, Qt.UserRole, meta)
                usable = meta.get("mimeType") == FOLDER or (self.mode == "open" and is_openable(meta))
                if not usable:
                    item.setForeground(0, QColor(theme.ICON_DISABLED))
                    item.setToolTip(0, "This kind of file can't be opened in Aupedean Annotator")
                self.tree.addTopLevelItem(item)
            self.status.setText(f"{len(files)} item{'s' if len(files) != 1 else ''}" if files else "Empty")

        def failed(exc):
            if request == self._request:
                self.status.setText(friendly(exc))

        worker.run(fetch, done, failed)

    def _place(self, row):
        if row < 0:
            return
        label, key = self.PLACES[row]
        self._stack = []
        self.search.clear()
        if key == "root":
            self.folder = {"id": "root", "name": "My Drive"}
            self._load(lambda: self.drive.children("root"), label)
        else:
            fetch = {"shared": self.drive.shared_with_me, "recent": self.drive.recent,
                     "starred": self.drive.starred}[key]
            self._load(fetch, label)

    def _search(self):
        text = self.search.text().strip()
        if text:
            self._stack.append((self.folder, self.path_label.text()))
            self._load(lambda: self.drive.search(text), f"Search: {text}")

    def _open_folder(self, meta):
        self._stack.append((self.folder, self.path_label.text()))
        self.folder = meta
        self._load(lambda: self.drive.children(meta["id"]), meta["name"])

    def _back(self):
        if not self._stack:
            return
        self.folder, title = self._stack.pop()
        folder_id = self.folder["id"]
        self._load(lambda: self.drive.children(folder_id), title)
        self.back_btn.setEnabled(bool(self._stack))

    def _current(self):
        items = self.tree.selectedItems()
        return items[0].data(0, Qt.UserRole) if items else None

    def _selection_changed(self):
        if self.mode == "open":
            meta = self._current()
            self.ok_btn.setEnabled(bool(meta) and is_openable(meta))

    def _activated(self, item):
        meta = item.data(0, Qt.UserRole)
        if meta.get("mimeType") == FOLDER:
            self._open_folder(meta)
        elif self.mode == "open" and is_openable(meta):
            self.selected = meta
            self.accept()

    def _accept(self):
        if self.mode == "open":
            meta = self._current()
            if meta and meta.get("mimeType") == FOLDER:
                self._open_folder(meta)
            elif meta and is_openable(meta):
                self.selected = meta
                self.accept()
            return
        name = self.name_edit.text().strip()
        if not name:
            return
        meta = self._current()
        self.selected = meta if meta and meta.get("mimeType") == FOLDER else self.folder
        self.accept()

    def file_name(self):
        return self.name_edit.text().strip() if self.name_edit else ""


# --------------------------------------------------------------------------
# Signing links
# --------------------------------------------------------------------------

class SigningSetupDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Set Up Signing Links")
        self.setMinimumWidth(620)
        self._progress = _Progress(self)
        self._progress.text.connect(self._log)
        layout = _dialog_layout(self)
        layout.addLayout(_header("Signing links",
                                 "Anyone you send a link to can sign a PDF in their web browser, after signing in "
                                 "with their Google account. The signing page lives in your own Google account "
                                 "(Google Apps Script), so there is no server to run and nothing to pay."))
        for number, text, button in (
            ("1", "Turn on \"Google Apps Script API\" in your Apps Script settings (one switch).",
             _link_button("Apps Script Settings", signing.APPS_SCRIPT_SETTINGS)),
            ("2", "Create the signing page in your Google account.", None),
            ("3", "Approve it: open the service once, signed in as yourself, and choose Allow. It only uses the "
                  "\"Aupedean Signing\" folder in your Drive.", None),
        ):
            row = QHBoxLayout()
            label = QLabel(f"<b>{number}.</b> {text}")
            label.setWordWrap(True)
            row.addWidget(label, 1)
            if number == "2":
                self.create_btn = QPushButton("Create")
                self.create_btn.clicked.connect(self._create)
                button = self.create_btn
            elif number == "3":
                self.approve_btn = QPushButton("Approve...")
                self.approve_btn.clicked.connect(self._approve)
                self.check_btn = QPushButton("Check")
                self.check_btn.clicked.connect(self._check)
                row.addWidget(self.approve_btn)
                button = self.check_btn
            row.addWidget(button)
            layout.addLayout(row)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)
        layout.addWidget(self.log)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        layout.addLayout(_button_row(close))
        self._refresh()

    def _log(self, text):
        self.log.appendPlainText(text)

    def _refresh(self):
        config = signing.load_config()
        self.approve_btn.setEnabled(bool(config))
        self.check_btn.setEnabled(bool(config))
        self.create_btn.setText("Create Again" if config else "Create")
        if not config:
            self.status.setText("Not set up yet.")
        elif config.get("approved"):
            self.status.setText(f"Ready. Signing page: <a href='{config['page_url']}'>{config['page_url']}</a>")
            self.status.setOpenExternalLinks(True)
        else:
            self.status.setText("Created. Now approve the service (step 3), then press Check.")

    def _create(self):
        session = self.controller.session()
        if session is None:
            QMessageBox.information(self, "Signing Links", "Connect your Google account first.")
            return
        if not session.has_scope("https://www.googleapis.com/auth/script.projects"):
            QMessageBox.information(self, "Signing Links", "Your Google sign-in is missing the permission to create "
                                    "the signing page. Use Google Drive > Google Account > Sign In Again.")
            return
        self.create_btn.setEnabled(False)
        self.log.clear()

        def failed(exc):
            self.create_btn.setEnabled(True)
            self._log(friendly(exc))
            if isinstance(exc, SetupError) and exc.url:
                if QMessageBox.question(self, "Signing Links", f"{exc}\n\nOpen that page now?",
                                        QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
                    QDesktopServices.openUrl(QUrl(exc.url))

        def done(_config):
            self.create_btn.setEnabled(True)
            self._refresh()

        worker.run(lambda: signing.setup_service(session, self._progress.text.emit), done, failed)

    def _approve(self):
        config = signing.load_config()
        if config:
            QDesktopServices.openUrl(QUrl(config["service_url"]))
            self._log("In the browser: choose your account, Advanced, Go to Aupedean Sign (unsafe), Allow. "
                      "Then press Check here.")

    def _check(self):
        config = signing.load_config()
        if not config:
            return
        self.check_btn.setEnabled(False)

        def done(ok):
            self.check_btn.setEnabled(True)
            if ok:
                config["approved"] = True
                signing.save_config(config)
                self._log("The signing service is approved and working.")
            else:
                self._log("Not approved yet: open it with Approve..., allow it, then Check again.")
            self._refresh()

        worker.run(lambda: signing.service_approved(config), done,
                   lambda exc: (self.check_btn.setEnabled(True), self._log(friendly(exc))))


class FieldPicker(QWidget):
    """A page preview on which the signature box is dragged out."""

    def __init__(self, doc, page_index=0, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.setMinimumSize(360, 460)
        self.setCursor(Qt.CrossCursor)
        self._drag = None
        self.set_page(page_index)

    def set_page(self, index):
        self.page_index = index
        page = self.doc[index]
        self.zoom = min(440 / page.rect.width, 560 / page.rect.height)
        pix = page.get_pixmap(matrix=fitz.Matrix(self.zoom, self.zoom), alpha=False)
        self.pixmap = QPixmap.fromImage(QImage(pix.samples, pix.width, pix.height, pix.stride,
                                               QImage.Format_RGB888).copy())
        self.setFixedSize(self.pixmap.size())
        # a sensible default: 180 x 50 pt, bottom right, above the margin
        w, h = page.rect.width, page.rect.height
        self.box = QRectF((w - 250) * self.zoom, (h - 130) * self.zoom, 180 * self.zoom, 50 * self.zoom)
        self.update()

    def set_box(self, rect):
        """Show the box `rect` (unrotated PDF coordinates)."""
        shown = fitz.Rect(rect) * self.doc[self.page_index].rotation_matrix * self.zoom
        shown.normalize()
        self.box = QRectF(shown.x0, shown.y0, shown.width, shown.height) & QRectF(self.rect())
        self.update()

    def rect_pdf(self):
        """The box in unrotated PDF coordinates (what the annotation code uses)."""
        page = self.doc[self.page_index]
        r = fitz.Rect(self.box.left(), self.box.top(), self.box.right(), self.box.bottom()) / self.zoom
        r = r * page.derotation_matrix
        r.normalize()
        return r

    def mousePressEvent(self, event):
        self._drag = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            self.box = QRectF(QRect(self._drag, event.position().toPoint()).normalized()) & QRectF(self.rect())
            self.update()

    def mouseReleaseEvent(self, event):
        if self._drag is not None and (self.box.width() < 20 or self.box.height() < 10):
            # a click rather than a drag: a standard-size box at that spot
            size = QSize(round(180 * self.zoom), round(50 * self.zoom))
            self.box = QRectF(QRect(self._drag, size)) & QRectF(self.rect())
        self._drag = None
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.drawPixmap(0, 0, self.pixmap)
        p.setRenderHint(QPainter.Antialiasing)
        fill = QColor(theme.ACCENT)
        fill.setAlpha(40)
        p.fillRect(self.box, fill)
        p.setPen(QPen(QColor(theme.ACCENT), 2, Qt.DashLine))
        p.drawRect(self.box)
        p.drawText(self.box, Qt.AlignCenter, "Sign here")
        p.end()


def _settings():
    return QSettings(QSettings.defaultFormat(), QSettings.UserScope, "AupedeanAnnotator", "AupedeanAnnotator")


class RequestSignatureDialog(QDialog):
    def __init__(self, doc_bytes, title, parent=None, google=False, mode=None):
        super().__init__(parent)
        self.mode = mode or ("google" if google else "file")
        self.google = self.mode == "google"
        needs_email = self.mode in ("google", "service")
        google = self.google
        self.setWindowTitle("Request a Signature")
        self.doc = fitz.open(stream=doc_bytes, filetype="pdf")
        layout = _dialog_layout(self)
        layout.addLayout(_header(
            "Request a signature",
            "The signer gets a link, signs in with Google, and draws their signature in the box. It is added to "
            "this PDF automatically when they have signed." if google else
            "The signer gets an email with a link. Only they can open it (a code is sent to their inbox). They "
            "sign on the document in their browser, and the signature is added to this PDF automatically."
            if self.mode == "service" else
            "Type the signer's email and press Send: Aupedean emails it to them, they sign in their web browser, "
            "and the signature comes back into this PDF by itself. Or leave the email empty to make a signing file "
            "to send by WhatsApp or any way you like."))
        body = QHBoxLayout()
        form = QFormLayout()
        s = _settings()
        self.my_name = QLineEdit(s.value("sign/my_name", ""))
        self.my_name.setPlaceholderText("Your name")
        self.my_email = QLineEdit(s.value("sign/my_email", ""))
        self.my_email.setPlaceholderText("you@example.com (the signer can email it back)")
        self.email = QLineEdit()
        self.email.setPlaceholderText("signer@example.com" if needs_email else "Fill in to send it straight away")
        self.name = QLineEdit()
        self.name.setPlaceholderText("Optional")
        self.title = QLineEdit(title)
        self.message = QPlainTextEdit()
        self.message.setPlaceholderText("Optional note shown to the signer")
        self.message.setMaximumHeight(90)
        self.expires = QSpinBox()  # (signing links only)
        self.expires.setRange(1, 90)
        self.expires.setValue(14)
        self.expires.setSuffix(" days")
        self.page = QComboBox()
        self.page.addItems([f"Page {i + 1}" for i in range(self.doc.page_count)])
        self.page.setCurrentIndex(self.doc.page_count - 1)
        if self.mode == "file":
            form.addRow("Your name", self.my_name)
            form.addRow("Your email", self.my_email)
        elif self.mode == "service":
            form.addRow("Your name", self.my_name)
        form.addRow("Signer's Google email" if google else "Signer's email", self.email)
        form.addRow("Signer's name", self.name)
        form.addRow("Document title", self.title)
        form.addRow("Message", self.message)
        if self.mode in ("google", "service"):
            form.addRow("Link expires after", self.expires)
        form.addRow("Signature on", self.page)
        form.addRow("", _muted("Drag on the page preview to place the signature box."))
        for widget in (self.my_name, self.my_email, self.email, self.name, self.title, self.message):
            widget.setMinimumWidth(280)
        body.addLayout(form, 1)
        self.picker = FieldPicker(self.doc, self.doc.page_count - 1)
        self.page.currentIndexChanged.connect(self.picker.set_page)
        body.addWidget(self.picker)
        layout.addLayout(body)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = _primary({"google": "Create Signing Link", "service": "Send for Signature"}.get(self.mode,
                                                                                             "Create Signing File"))
        ok.clicked.connect(self.accept)
        if self.mode == "file":   # with an email it goes straight to the signer
            self.email.textChanged.connect(
                lambda text: ok.setText("Send" if text.strip() else "Create Signing File"))
        layout.addLayout(_button_row(cancel, ok))

    def accept(self):
        email = self.email.text().strip()
        needs_email = self.mode in ("google", "service")
        if (needs_email or email) and ("@" not in email or "." not in email.split("@")[-1]):
            QMessageBox.warning(self, "Request a Signature", "Enter the signer's Google email address."
                                if self.google else "Enter the signer's email address." if needs_email
                                else "That doesn't look like an email address.")
            return
        s = _settings()
        s.setValue("sign/my_name", self.my_name.text().strip())
        s.setValue("sign/my_email", self.my_email.text().strip())
        super().accept()

    def values(self):
        return {"signer_email": self.email.text().strip(), "signer_name": self.name.text().strip(),
                "title": self.title.text().strip() or "Document", "message": self.message.toPlainText().strip(),
                "expires_days": self.expires.value(), "page_index": self.picker.page_index,
                "rect": self.picker.rect_pdf(), "my_name": self.my_name.text().strip(),
                "my_email": self.my_email.text().strip()}


class QuickEmailDialog(QDialog):
    """The easiest request: the signer's email, and send. The signature spot
    is found automatically (and shown, to drag somewhere else if needed)."""

    def __init__(self, doc_bytes, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Quick Email Request")
        self.doc = fitz.open(stream=doc_bytes, filetype="pdf")
        self.title = title
        layout = _dialog_layout(self)
        layout.addLayout(_header(
            "Send for signature by email",
            "Type your client's email and press Send. They click the link in the email and sign, with no account "
            "or code. You get an email when they have signed, and the signature is added to this PDF by itself."))
        body = QHBoxLayout()
        form = QFormLayout()
        self.email = QLineEdit()
        self.email.setPlaceholderText("client@example.com")
        self.email.setMinimumWidth(260)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Optional")
        self.message = QLineEdit()
        self.message.setPlaceholderText("Optional note")
        form.addRow("Client's email", self.email)
        form.addRow("Client's name", self.name)
        form.addRow("Note", self.message)
        form.addRow("", _muted("The box on the page shows where they sign. Drag on the page to move it."))
        body.addLayout(form, 1)
        page_index, rect = signing.find_signature_spot(self.doc)
        self.picker = FieldPicker(self.doc, page_index)
        self.picker.set_box(rect)
        body.addWidget(self.picker)
        layout.addLayout(body)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = _primary("Send")
        ok.clicked.connect(self.accept)
        layout.addLayout(_button_row(cancel, ok))
        self.email.setFocus()

    def accept(self):
        email = self.email.text().strip()
        if "@" not in email or "." not in email.split("@")[-1]:
            QMessageBox.warning(self, "Quick Email Request", "Enter your client's email address.")
            return
        super().accept()

    def values(self):
        return {"signer_email": self.email.text().strip(), "signer_name": self.name.text().strip(),
                "title": self.title or "Document", "message": self.message.text().strip(),
                "page_index": self.picker.page_index, "rect": self.picker.rect_pdf()}


def copy_file_to_clipboard(path):
    """Put the file itself on the clipboard: paste it into WhatsApp, an email or a folder."""
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(path)])
    data.setText(path)
    QGuiApplication.clipboard().setMimeData(data)


def show_in_folder(path):
    if os.name == "nt" and os.path.exists(path):
        import subprocess

        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))


class SigningFileDialog(QDialog):
    def __init__(self, path, req, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Signing File")
        self.setMinimumWidth(580)
        who = req.signer_name or req.signer_email or "the signer"
        layout = _dialog_layout(self)
        layout.addLayout(_header(
            f"Send this file to {who}",
            "Send it by WhatsApp, email or any way you like. They open it in a web browser, tap Sign here, sign on "
            "the document and send the signed PDF back. When that PDF is opened here, or lands in your Downloads "
            "folder, the signature is added to your document automatically."))
        name = QLabel(f"<b>{os.path.basename(path)}</b>")
        layout.addWidget(name)
        layout.addWidget(_muted(os.path.dirname(path)))
        copy = QPushButton("Copy File")
        copy.setToolTip("Then paste it into WhatsApp, an email or a chat (Ctrl+V)")
        copy.clicked.connect(lambda: (copy_file_to_clipboard(path), copy.setText("Copied: now paste it")))
        folder = QPushButton("Show in Folder")
        folder.clicked.connect(lambda: show_in_folder(path))
        email = QPushButton("Email It...")
        subject = f"Please sign: {req.title}"
        body = (f"Hello{(' ' + req.signer_name) if req.signer_name else ''},\n\nPlease sign \"{req.title}\". "
                "Open the attached file in your web browser, tap \"Sign here\", sign, and send the signed PDF back "
                "to me.\n\n" + (req.message + "\n\n" if req.message else ""))
        mailto = QUrl(f"mailto:{req.signer_email}")
        query = QUrlQuery()
        query.addQueryItem("subject", subject)
        query.addQueryItem("body", body)
        mailto.setQuery(query)

        def send_email():
            copy_file_to_clipboard(path)
            QDesktopServices.openUrl(mailto)
            QMessageBox.information(self, "Email It", "Your email app opens with the message ready. Attach the "
                                    "signing file: it's on the clipboard, so paste it (Ctrl+V), or drag it in "
                                    "from the folder.")
            show_in_folder(path)

        email.clicked.connect(send_email)
        done = _primary("Done")
        done.clicked.connect(self.accept)
        layout.addLayout(_button_row(done, leading=(copy, email, folder)))


class SignAccountDialog(QDialog):
    """Sign in to the signature service with an email code."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Signature Account")
        self.setMinimumWidth(520)
        layout = _dialog_layout(self)
        layout.addLayout(_header("Sign in for signatures",
                                 "Type your email and we'll send you a 6-digit code. After that, Request Signature "
                                 "emails documents to your signers and their signatures come back by themselves."))
        form = QFormLayout()
        self.email = QLineEdit(_settings().value("sign/my_email", ""))
        self.email.setPlaceholderText("you@example.com")
        self.code = QLineEdit()
        self.code.setPlaceholderText("123456")
        self.code.setMaxLength(6)
        self.code.setEnabled(False)
        form.addRow("Your email", self.email)
        form.addRow("Code from the email", self.code)
        self.url = QLineEdit(sign_service.service_url())
        self.url.setPlaceholderText("https://aupedean-sign.<name>.workers.dev")
        url_label = QLabel("Service address")
        form.addRow(url_label, self.url)
        built_in = bool(sign_service.built_in_url())      # shipped with the app: nothing to set up
        url_label.setVisible(not built_in)
        self.url.setVisible(not built_in)
        layout.addLayout(form)
        self.status = _muted("")
        layout.addWidget(self.status)
        self.setup_btn = QPushButton("Set Up a Service...")
        self.setup_btn.setToolTip("One time, for whoever runs the service (free Cloudflare and Brevo accounts)")
        self.setup_btn.clicked.connect(self._setup)
        self.setup_btn.setVisible(not built_in)
        self.out_btn = QPushButton("Sign Out")
        self.out_btn.clicked.connect(self._sign_out)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        self.send_btn = QPushButton("Send Code")
        self.send_btn.clicked.connect(self._send_code)
        self.ok_btn = _primary("Sign In")
        self.ok_btn.clicked.connect(self._verify)
        self.ok_btn.setEnabled(False)
        layout.addLayout(_button_row(close, self.send_btn, self.ok_btn, leading=(self.setup_btn, self.out_btn)))
        self._refresh()

    def _refresh(self):
        session = sign_service.load_session()
        self.out_btn.setVisible(bool(session))
        if session:
            self.status.setText(f"Signed in as {session['email']}.")
        elif not sign_service.service_url():
            self.status.setText("No signature service yet. Whoever looks after Aupedean for you sets one up once "
                                "(Set Up a Service), then everyone just signs in here.")

    def _client(self):
        url = self.url.text().strip().rstrip("/")
        if not url.startswith("https://") and not url.startswith("http://127.0.0.1"):
            QMessageBox.warning(self, "Signature Account", "Enter the service address (it starts with https://).")
            return None
        if url != sign_service.service_url():
            sign_service.save_service_url(url)
        return sign_service.SignService(url)

    def _send_code(self):
        client = self._client()
        email = self.email.text().strip().lower()
        if client is None or "@" not in email:
            return
        self.send_btn.setEnabled(False)
        self.status.setText("Sending the code...")

        def done(_r):
            self.send_btn.setEnabled(True)
            self.send_btn.setText("Send Another Code")
            self.code.setEnabled(True)
            self.ok_btn.setEnabled(True)
            self.code.setFocus()
            self.status.setText(f"We emailed a code to {email}. It works for 10 minutes.")

        def failed(exc):
            self.send_btn.setEnabled(True)
            self.status.setText(str(exc) if isinstance(exc, sign_service.ServiceError) else friendly(exc))

        worker.run(lambda: client.login_code(email), done, failed)

    def _verify(self):
        client = self._client()
        email, code = self.email.text().strip().lower(), self.code.text().strip()
        if client is None or len(code) != 6:
            return
        self.ok_btn.setEnabled(False)

        def done(result):
            signed_email, token = result
            sign_service.save_session(signed_email, token)
            _settings().setValue("sign/my_email", signed_email)
            self.controller.sign_service_changed()
            self.accept()

        def failed(exc):
            self.ok_btn.setEnabled(True)
            self.status.setText(str(exc) if isinstance(exc, sign_service.ServiceError) else friendly(exc))

        worker.run(lambda: client.login_verify(email, code), done, failed)

    def _sign_out(self):
        sign_service.sign_out()
        self.controller.sign_service_changed()
        self._refresh()

    def _setup(self):
        dlg = ServiceSetupDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self.url.setText(sign_service.service_url())
            self.email.setText(dlg.owner.text().strip())
            self._refresh()
            self._send_code()


SETUP_TIPS = {
    "Cloudflare": (
        "<b>Cloudflare account (free, no card)</b>"
        "<ol>"
        "<li>Click to open the sign-up page. Sign up with your email and a password.</li>"
        "<li>Open the email Cloudflare sends and click <b>Verify email</b>. Workers only run for verified "
        "accounts.</li>"
        "<li>If asked to add a website or choose a plan, skip it: no domain is needed and the "
        "<b>Free</b> plan is enough.</li>"
        "<li>Aupedean creates the rest itself: the <i>aupedean-sign</i> Worker, its D1 database and a "
        "<i>workers.dev</i> web address.</li>"
        "</ol>"
        "Free plan limits (100,000 requests a day) are far more than signing needs."),
    "API Tokens": (
        "<b>Cloudflare API token</b>"
        "<ol>"
        "<li>Click to open <b>My Profile &gt; API Tokens</b> (sign in if asked).</li>"
        "<li>Click <b>Create Token</b>, scroll to <b>Create Custom Token</b> and click <b>Get started</b>.</li>"
        "<li>Token name: <i>Aupedean Sign</i>.</li>"
        "<li>Under <b>Permissions</b> add three rows (use <b>+ Add more</b>):"
        "<br>&nbsp;&bull; Account &nbsp;|&nbsp; Workers Scripts &nbsp;|&nbsp; <b>Edit</b>"
        "<br>&nbsp;&bull; Account &nbsp;|&nbsp; D1 &nbsp;|&nbsp; <b>Edit</b>"
        "<br>&nbsp;&bull; Account &nbsp;|&nbsp; Account Settings &nbsp;|&nbsp; <b>Read</b></li>"
        "<li><b>Account Resources</b>: Include &gt; your account (or All accounts).</li>"
        "<li>Leave Client IP filtering empty and TTL unset.</li>"
        "<li><b>Continue to summary</b> &gt; <b>Create Token</b>, then <b>copy the token</b> and paste it "
        "below. Cloudflare shows it only once.</li>"
        "</ol>"
        "Not the <i>Global API Key</i>: a custom token can only do these three things."),
    "Brevo": (
        "<b>Brevo account (free, 300 emails a day, no card)</b>"
        "<ol>"
        "<li>Click to open the sign-up page. Sign up and confirm the email Brevo sends you.</li>"
        "<li>Fill in the profile questions (name, company, address, phone). Brevo needs these before it "
        "sends emails; a new account can take a short while to be activated.</li>"
        "<li><b>The address emails come from</b>: your Brevo login email works. For better delivery, add "
        "one under <b>Senders, Domains &amp; Dedicated IPs &gt; Senders &gt; Add a sender</b> and click the "
        "link in the confirmation email. An address at your own domain (verified under <b>Domains</b>) "
        "is best: emails sent as a Gmail address often land in spam.</li>"
        "<li><b>Important</b>: under <b>Security &gt; Authorised IPs</b>, turn the IP check <b>off</b>. "
        "The service runs on Cloudflare, whose IP addresses change, so Brevo would block it.</li>"
        "</ol>"
        "Put the sender address in <b>Emails come from</b> below."),
    "API Keys": (
        "<b>Brevo API key</b>"
        "<ol>"
        "<li>Click to open <b>SMTP &amp; API &gt; API Keys</b> (sign in if asked).</li>"
        "<li>Click <b>Generate a new API key</b> and name it <i>Aupedean Sign</i>.</li>"
        "<li><b>Copy the key</b> and paste it below. Brevo shows it only once.</li>"
        "</ol>"
        "It starts with <b>xkeysib-</b>. Not the <i>SMTP</i> key (xsmtpsib-), which can't be used here. "
        "A key copied for MCP (a long code starting with eyJ) also works."),
}

HOSTED_REQUESTS_PER_DAY = 10    # per person, when the service is open to anyone
HOSTED_EMAILS_PER_DAY = 280      # the whole service: inside Brevo's free 300 a day


class ServiceSetupDialog(QDialog):
    """One time, by whoever runs the service: install it in their free
    Cloudflare account, sending email through their free Brevo account."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set Up the Signature Service")
        self.setMinimumWidth(640)
        self._progress = _Progress(self)
        self._progress.text.connect(lambda t: self.log.appendPlainText(t))
        layout = _dialog_layout(self)
        layout.addLayout(_header("Set up the signature service (one time)",
                                 "Two free accounts and two keys; Aupedean installs everything else. Nobody else "
                                 "has to do this: they just sign in with their email."))
        for number, text, caption, url in (
            ("1", "Create a free Cloudflare account (no card needed).", "Cloudflare", "https://dash.cloudflare.com/sign-up"),
            ("2", "Make a Cloudflare API token: Create Token > Create Custom Token, with permissions Account > "
                  "Workers Scripts > Edit, Account > D1 > Edit and Account > Account Settings > Read.",
             "API Tokens", "https://dash.cloudflare.com/profile/api-tokens"),
            ("3", "Create a free Brevo account (300 emails a day, no card needed).", "Brevo", "https://onboarding.brevo.com/account/register"),
            ("4", "In Brevo: SMTP & API > API Keys > Generate a new API key.", "API Keys",
             "https://app.brevo.com/settings/keys/api"),
        ):
            row = QHBoxLayout()
            label = QLabel(f"<b>{number}.</b> {text}")
            label.setWordWrap(True)
            row.addWidget(label, 1)
            row.addWidget(_link_button(caption, url, SETUP_TIPS.get(caption, "")))
            layout.addLayout(row)
        form = QFormLayout()
        self.cf = QLineEdit()
        self.cf.setEchoMode(QLineEdit.Password)
        self.cf.setPlaceholderText("Cloudflare API token")
        self.brevo = QLineEdit()
        self.brevo.setEchoMode(QLineEdit.Password)
        self.brevo.setPlaceholderText("xkeysib-...")
        self.owner = QLineEdit(_settings().value("sign/my_email", ""))
        self.owner.setPlaceholderText("you@example.com (you'll sign in with it)")
        self.sender = QLineEdit()
        self.sender.setPlaceholderText("The address emails come from (your Brevo login, or a verified sender)")
        self.who = QComboBox()
        self.who.addItems(["Only me", "Me and people at my email's domain", "Me and these addresses...", "Anyone"])
        self.extra = QLineEdit()
        self.extra.setPlaceholderText("friend@gmail.com, @mycompany.com")
        self.extra.setVisible(False)
        self.who.currentIndexChanged.connect(lambda i: self.extra.setVisible(i == 2))
        form.addRow("Cloudflare API token", self.cf)
        form.addRow("Brevo API key", self.brevo)
        form.addRow("Your email", self.owner)
        form.addRow("Emails come from", self.sender)
        form.addRow("Who may send requests", self.who)
        form.addRow("", self.extra)
        layout.addLayout(form)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(110)
        self.limits = _muted(f"Open to anyone, so each person can send {HOSTED_REQUESTS_PER_DAY} requests a day and "
                             f"the service stops at {HOSTED_EMAILS_PER_DAY} emails a day (Brevo's free plan is 300).")
        self.limits.setWordWrap(True)
        self.limits.setVisible(False)
        self.who.currentIndexChanged.connect(lambda i: self.limits.setVisible(i == 3))
        layout.addWidget(self.limits)
        layout.addWidget(self.log)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.go = _primary("Install")
        self.go.clicked.connect(self._install)
        layout.addLayout(_button_row(cancel, self.go))

    def _allow(self):
        owner = self.owner.text().strip().lower()
        choice = self.who.currentIndex()
        if choice == 1 and "@" in owner:
            return "@" + owner.split("@", 1)[1]
        if choice == 2:
            return ",".join(p.strip().lower() for p in self.extra.text().split(",") if p.strip())
        if choice == 3:
            return "*"
        return ""

    def _install(self):
        cf, brevo = self.cf.text().strip(), self.brevo.text().strip()
        owner = self.owner.text().strip().lower()
        sender = self.sender.text().strip().lower() or owner
        if not cf or not brevo or "@" not in owner:
            QMessageBox.warning(self, "Set Up", "Fill in both keys and your email.")
            return
        if self.who.currentIndex() == 3 and QMessageBox.question(
                self, "Set Up", "Anyone who finds the service could then send emails from your Brevo account "
                "(capped at 280 a day). Choose this to host the service for your clients. Allow anyone?", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self.go.setEnabled(False)
        self.log.clear()

        allow = self._allow()
        hosted = allow == "*"

        def done(url):
            self.log.appendPlainText(f"Installed at {url}")
            _settings().setValue("sign/my_email", owner)
            if hosted:
                share = ("To ship it with Aupedean, put this in app/assets/sign_service.json before building:\n"
                         f'{{"url": "{url}"}}\n\nClients then just sign in with their email.')
            else:
                share = "Give this address to anyone else who uses Aupedean with you."
            QMessageBox.information(self, "Set Up", f"The signature service is ready:\n{url}\n\n{share} "
                                    "Now sign in with your email.")
            self.accept()

        def failed(exc):
            self.go.setEnabled(True)
            self.log.appendPlainText(str(exc))
            url = getattr(exc, "url", None)
            if url and QMessageBox.question(self, "Set Up", f"{exc}\n\nOpen that page?",
                                            QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
                QDesktopServices.openUrl(QUrl(url))

        limits = dict(requests_per_day=HOSTED_REQUESTS_PER_DAY, emails_per_day=HOSTED_EMAILS_PER_DAY) if hosted else {}
        worker.run(lambda: sign_service.install(cf, brevo, owner, sender, allow, progress=self._progress.text.emit,
                                                **limits), done, failed)


class ReturnWatcher(QObject):
    """Notices signed PDFs that come back (downloaded from WhatsApp, email...)
    in the Downloads folder, for the signing files still waiting."""
    found = Signal(str)   # path of a returned signed PDF

    def __init__(self, store, parent=None, folders=None):
        super().__init__(parent)
        self.store = store
        self.folders = folders or [os.path.join(os.path.expanduser("~"), "Downloads"),
                                   os.path.join(os.path.expanduser("~"), "Desktop")]
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(lambda _d: self._timer.start())
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(1500)   # let the browser finish writing the file
        self._timer.timeout.connect(self.scan)
        self._seen = set()
        self.update()

    def waiting(self):
        return [r for r in self.store.items if r.kind == "file" and r.status == signing.WAITING]

    def update(self):
        """Watch the folders only while signing files are waiting."""
        want = [f for f in self.folders if os.path.isdir(f)] if self.waiting() else []
        have = self.watcher.directories()
        if have:
            self.watcher.removePaths(have)
        if want:
            self.watcher.addPaths(want)
            QTimer.singleShot(0, self.scan)

    def scan(self):
        waiting = self.waiting()
        if not waiting:
            return
        oldest = min(calendar.timegm(time.strptime(r.created, "%Y-%m-%dT%H:%M:%SZ")) for r in waiting)
        for folder in self.watcher.directories():
            try:
                names = os.listdir(folder)
            except OSError:
                continue
            for name in names:
                if not name.lower().endswith(".pdf"):
                    continue
                path = os.path.join(folder, name)
                try:
                    stamp = os.path.getmtime(path)
                except OSError:
                    continue
                key = (path, stamp)
                if key in self._seen or stamp < oldest - 60:
                    continue
                self._seen.add(key)
                receipt = signing_file.read_receipt(path)
                if receipt and signing_file.match(self.store, receipt):
                    self.found.emit(path)


class LinkDialog(QDialog):
    def __init__(self, url, req, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Signing Link")
        self.setMinimumWidth(560)
        layout = _dialog_layout(self)
        layout.addLayout(_header("Send this link to the signer",
                                 f"Only {req.signer_email} can sign with it (they sign in with Google). You'll see "
                                 "the signature in the PDF once they've signed."))
        self.edit = QLineEdit(url)
        self.edit.setReadOnly(True)
        layout.addWidget(self.edit)
        copy = QPushButton("Copy Link")
        copy.clicked.connect(lambda: (QGuiApplication.clipboard().setText(url), copy.setText("Copied")))
        subject = f"Please sign: {req.title}"
        body = (f"Hello{(' ' + req.signer_name) if req.signer_name else ''},\n\n"
                f"Please sign \"{req.title}\" here:\n{url}\n\n"
                f"{req.message + chr(10) + chr(10) if req.message else ''}Sign in with {req.signer_email} "
                "to open it.")
        gmail = QUrl("https://mail.google.com/mail/")
        query = QUrlQuery()
        for key, value in (("view", "cm"), ("to", req.signer_email), ("su", subject), ("body", body)):
            query.addQueryItem(key, value)
        gmail.setQuery(query)
        email = QPushButton("Email It with Gmail")
        email.clicked.connect(lambda: QDesktopServices.openUrl(gmail))
        close = _primary("Done")
        close.clicked.connect(self.accept)
        layout.addLayout(_button_row(close, leading=(copy, email)))


class RequestsDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Signature Requests")
        self.resize(900, 440)
        layout = _dialog_layout(self)
        layout.addLayout(_header("Signature requests",
                                 "Checked every two minutes while the app is open. Signed documents get the "
                                 "signature added automatically."))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Document", "Signer", "Status", "Sent", "Signed"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table, 1)
        self.buttons = {}
        row = []
        for key, text in (("copy", "Copy Link"), ("check", "Check Now"), ("apply", "Add Signature to PDF"),
                          ("cancel", "Cancel Request"), ("remove", "Remove from List"), ("folder", "Open in Drive")):
            btn = QPushButton(text)
            btn.clicked.connect(lambda _c=False, k=key: self._do(k))
            self.buttons[key] = btn
            row.append(btn)
        close = _primary("Close")
        close.clicked.connect(self.accept)
        layout.addLayout(_button_row(close, leading=row))
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        controller.signing.changed.connect(self._fill)
        self._fill()

    def _fill(self):
        items = self.controller.signing.store.items
        self.table.setRowCount(len(items))
        for i, req in enumerate(items):
            cells = [req.title + f"  ({os.path.basename(req.doc_path)})", req.signer_email, req.status_text,
                     req.created[:16].replace("T", " "), req.signed_at[:16].replace("T", " ")]
            for j, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, req.id)
                if j == 2 and req.error:
                    item.setToolTip(req.error)
                self.table.setItem(i, j, item)
        self._sync_buttons()

    def _selected(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        return self.controller.signing.store.get(self.table.item(rows[0].row(), 0).data(Qt.UserRole))

    def _sync_buttons(self):
        req = self._selected()
        active = req is not None and req.status == signing.WAITING
        is_file = req is not None and req.kind == "file"
        is_service = req is not None and req.kind == "service"
        self.buttons["copy"].setText("Copy File" if is_file else "Copy Link")
        self.buttons["copy"].setVisible(not is_service)
        self.buttons["folder"].setVisible(not is_service)
        self.buttons["folder"].setText("Show File" if is_file else "Open in Drive")
        self.buttons["copy"].setEnabled(active and (not is_file or os.path.isfile(req.file_path)))
        self.buttons["cancel"].setEnabled(active)
        self.buttons["apply"].setEnabled(req is not None and req.status == signing.SIGNED and not is_file)
        self.buttons["remove"].setEnabled(req is not None and req.status != signing.WAITING)
        self.buttons["folder"].setEnabled(req is not None)

    def _do(self, key):
        req = self._selected()
        ctrl = self.controller
        if key == "check":
            ctrl.returns.scan()
            ctrl.service.check()
            self.buttons["check"].setEnabled(False)
            ctrl.signing.check(on_done=lambda: self.buttons["check"].setEnabled(True))
            return
        if req is None:
            return
        if key == "copy" and req.kind == "file":
            copy_file_to_clipboard(req.file_path)
        elif key == "copy":
            config = signing.load_config()
            if config:
                QGuiApplication.clipboard().setText(signing.link(config, req))
        elif key == "apply":
            ctrl.signing.check()
        elif key == "cancel" and req.kind == "service":
            client = sign_service.current_client()
            if client is None or QMessageBox.question(self, "Cancel Request", f"Cancel the request to "
                                                      f"{req.signer_email}? Their link stops working.",
                                                      QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return

            def cancelled(_r):
                req.status = signing.CANCELLED
                ctrl.signing.store.save()
                self._fill()

            worker.run(lambda: client.cancel(req.id), cancelled,
                       lambda exc: QMessageBox.warning(self, "Cancel Request", str(exc)))
        elif key == "cancel" and req.kind == "file":
            if QMessageBox.question(self, "Cancel Request", "Stop waiting for this signature? A signed copy that "
                                    "comes back later won't be added.", QMessageBox.Yes | QMessageBox.No) \
                    == QMessageBox.Yes:
                req.status = signing.CANCELLED
                ctrl.signing.store.save()
                ctrl.returns.update()
                self._fill()
        elif key == "cancel":
            if QMessageBox.question(self, "Cancel Request", f"Cancel the request to {req.signer_email}? The link "
                                    "stops working.", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return
            session = ctrl.session()
            if session is None:
                return

            def done(_r):
                req.status = signing.CANCELLED
                ctrl.signing.store.save()
                self._fill()

            worker.run(lambda: signing.cancel_request(session, req), done,
                       lambda exc: QMessageBox.warning(self, "Cancel Request", friendly(exc)))
        elif key == "remove":
            ctrl.signing.store.remove(req.id)
            self._fill()
        elif key == "folder" and req.kind == "file":
            show_in_folder(req.file_path)
        elif key == "folder":
            QDesktopServices.openUrl(QUrl(f"https://drive.google.com/drive/folders/{req.folder_id}"))


# --------------------------------------------------------------------------
# The controller the main window owns
# --------------------------------------------------------------------------

class CloudController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._session = None
        self.sync = SyncManager(self.session, open_paths=self._open_paths, parent=self)
        self.sync.state_changed.connect(lambda _p: window.update_sync_status())
        self.sync.remote_updated.connect(self._reload)
        self.sync.conflict.connect(self._conflict)
        self.sync.notice.connect(lambda text: window.statusBar().showMessage(text, 8000))
        self.signing = SigningManager(self.session, parent=self)
        self.signing.signed.connect(self._apply_signature)
        self.signing.changed.connect(lambda: self.returns.update())
        self.returns = ReturnWatcher(self.signing.store, parent=self)
        self.service = sign_service.SignServiceManager(self.signing.store, parent=self)
        self.service.signed.connect(self._apply_signature)
        self.service.changed.connect(lambda: self.signing.changed.emit())
        self.returns.found.connect(lambda path: self.take_returned(path, automatic=True))
        self._asking = set()

    # ---- session ------------------------------------------------------------------
    def session(self):
        if self._session is None:
            try:
                self._session = google_auth.Session()
            except AuthError:
                return None
        return self._session

    def reset_session(self):
        self._session = None
        self.window.update_sync_status()

    def connected(self):
        return self.session() is not None

    def _open_paths(self):
        return [p for p in (self.window.tab_path(self.window.tabs.widget(i)) for i in range(self.window.tabs.count()))
                if p]

    # ---- menu -----------------------------------------------------------------------
    def build_menu(self, menubar):
        """Google Drive for desktop does the syncing; the Google account
        connection is only needed for signing links (and the in-app browser)."""
        m = menubar.addMenu("&Google Drive")
        self.act_open = self._act("Open from Google Drive...", self.open_from_desktop, "folder", "Ctrl+Shift+O")
        self.act_save = self._act("Save to Google Drive...", self.save_to_desktop, "save")
        self.act_desktop = self._act("Google Drive for desktop...", self.show_drive_desktop, "external")
        self.act_request = self._act("Request Signature...", self.request_signature, "send")
        self.act_request.setToolTip("Send a document to be signed: they sign in their web browser, no account needed")
        self.act_request_quick = self._act("Quick Email (easiest)...", self.request_signature_quick, "send")
        self.act_request_quick.setToolTip("Type your client's email and send: they sign from the link, and the "
                                          "signature comes back into this PDF by itself")
        self.act_request_email = self._act("Email with Options...", self.request_signature_email, "send")
        self.act_request_email.setToolTip("Choose the page, place the box, set an expiry; the signer confirms "
                                          "their inbox with a code")
        self.act_request_file = self._act("Signing File (WhatsApp, email...)...", self.request_signature_file, "save")
        self.act_request_file.setToolTip("No accounts or internet service: send the file any way you like")
        request_menu = QMenu(self.window)
        request_menu.addActions([self.act_request_quick, self.act_request_email, self.act_request_file])
        self.act_request.setMenu(request_menu)
        self.act_add_returned = self._act("Add a Signed Copy...", self.add_returned, "check")
        self.act_sign_account = self._act("Signature Account...", self.show_sign_account, "settings")
        self.act_request_google = self._act("Request Signature with a Google Link...", self.request_signature_google,
                                            "link")
        self.act_requests = self._act("Signature Requests...", self.show_requests, "list-bullet")
        self.act_setup = self._act("Set Up Signing Links...", self.show_signing_setup, "link")
        self.act_account = self._act("Google Account...", self.show_connect, "settings")
        self.act_browse = self._act("Browse Drive in the App...", self.open_from_drive, "open")
        self.act_upload = self._act("Upload and Sync in the App...", self.save_to_drive, "combine")
        self.act_sync = self._act("Sync Now", self.sync_now, "redo")
        m.addActions([self.act_open, self.act_save, self.act_desktop])
        m.addSeparator()
        google_links = m.addMenu(icons.icon("link"), "Signing Links with Google (advanced)")
        google_links.addActions([self.act_request_google, self.act_setup, self.act_account])
        advanced = m.addMenu(icons.icon("settings"), "Without Google Drive for desktop")
        advanced.addActions([self.act_browse, self.act_upload, self.act_sync])
        m.aboutToShow.connect(self._update_menu)
        self.menu = m
        return m

    def _act(self, text, slot, icon, shortcut=None):
        act = QAction(icons.icon(icon), text, self.window)
        if shortcut:
            act.setShortcut(shortcut)
        act.triggered.connect(slot)
        return act

    def shared_actions(self):
        """Commands that also work while a Word or LaTeX tab is active."""
        return {self.act_account, self.act_open, self.act_save, self.act_desktop, self.act_browse, self.act_upload,
                self.act_sync, self.act_requests, self.act_setup, self.act_add_returned, self.act_sign_account}

    def _update_menu(self):
        creds = google_auth.load_credentials()
        self.act_account.setText(f"Google Account ({creds['email']})..." if creds and creds.get("email")
                                 else "Connect Google Account (for signing links)...")
        roots = drive_desktop.find_roots()
        self.act_desktop.setText("Google Drive for desktop: " + (roots[0][1] if roots else "not found") + "...")

    def _need_session(self):
        session = self.session()
        if session is None:
            self.show_connect()
            session = self.session()
        return session

    def show_connect(self):
        ConnectDialog(self, self.window).exec()

    # ---- Google Drive for desktop (Google syncs; these are ordinary files here) -----------
    FILE_FILTER = ("All Supported (*.pdf *.docx *.tex *.bib *.html *.htm *.md *.txt);;PDF Files (*.pdf);;"
                   "Word Documents (*.docx);;LaTeX (*.tex *.bib);;Text and Web Pages (*.txt *.md *.html *.htm)")

    def _drive_root(self):
        roots = drive_desktop.find_roots()
        if not roots:
            if DriveDesktopDialog(self.window).exec() != QDialog.Accepted:
                return None
            roots = drive_desktop.find_roots(refresh=True)
        return roots[0][1] if roots else None

    def show_drive_desktop(self):
        roots = drive_desktop.find_roots(refresh=True)
        if roots:
            QDesktopServices.openUrl(QUrl.fromLocalFile(roots[0][1]))
        else:
            DriveDesktopDialog(self.window).exec()

    def open_from_desktop(self):
        root = self._drive_root()
        if not root:
            return
        paths, _ = QFileDialog.getOpenFileNames(self.window, "Open from Google Drive", root, self.FILE_FILTER)
        if paths:
            self.window.open_files_as_tabs(paths)

    def save_to_desktop(self):
        w = self.window
        widget = w.tabs.currentWidget()
        path = w.tab_path(widget)
        if path and drive_desktop.containing_root(path):
            if w.tab_dirty(widget):
                w.save_document()
            QMessageBox.information(w, "Save to Google Drive", "This file is already in Google Drive: every save "
                                    "is synced by Google Drive for desktop.")
            return
        root = self._drive_root()
        if not root:
            return
        w.save_document_as(directory=root)
        w.update_sync_status()

    # ---- Drive through the Google account (no Google Drive for desktop) -----------------
    def open_from_drive(self):
        session = self._need_session()
        if session is None:
            return
        dlg = DriveBrowser(session, "open", parent=self.window)
        if dlg.exec() != QDialog.Accepted or not dlg.selected:
            return
        meta = dlg.selected
        self.window.statusBar().showMessage(f"Opening {meta['name']} from Google Drive...")
        self.sync.open_remote(
            meta, lambda path: (self.window.open_files_as_tabs([path]), self.window.statusBar().clearMessage(),
                                self.window.update_sync_status()),
            lambda exc: QMessageBox.warning(self.window, "Open from Google Drive", friendly(exc)))

    def save_to_drive(self):
        w = self.window
        widget = w.tabs.currentWidget()
        path = w.tab_path(widget)
        if path and self.sync.entry(path):
            QMessageBox.information(w, "Save to Google Drive", "This file is already on Google Drive and syncs "
                                    "automatically every time you save.")
            return
        if not path or w.tab_dirty(widget):
            QMessageBox.information(w, "Save to Google Drive", "Save the document on this PC first; it is then "
                                    "uploaded and kept in sync.")
            w.save_document()
            path = w.tab_path(widget)
            if not path or w.tab_dirty(widget):
                return
        session = self._need_session()
        if session is None:
            return
        dlg = DriveBrowser(session, "save", suggested_name=os.path.basename(path), parent=w)
        if dlg.exec() != QDialog.Accepted or not dlg.selected:
            return
        w.statusBar().showMessage("Uploading to Google Drive...")
        self.sync.add_local(path, dlg.selected["id"], dlg.file_name(),
                            lambda meta: (w.statusBar().showMessage(f"Saved to Google Drive as {meta['name']}; "
                                                                    "it syncs from now on.", 8000),
                                          w.update_sync_status()),
                            lambda exc: QMessageBox.warning(w, "Save to Google Drive", friendly(exc)))

    def sync_now(self):
        if self._need_session() is None:
            return
        self.sync.poll()
        self.signing.check()
        self.window.statusBar().showMessage("Checking Google Drive...", 3000)

    def _reload(self, path):
        w = self.window
        tab = w.tab_for_path(path)
        if tab is None:
            return
        if w.tab_dirty(tab):
            resp = QMessageBox.question(w, "Changed on Google Drive",
                                        f"{os.path.basename(path)} was changed on Google Drive. Load that version? "
                                        "Your unsaved changes here would be lost.", QMessageBox.Yes | QMessageBox.No)
            if resp != QMessageBox.Yes:
                return
        w.reload_tab(tab)

    def _conflict(self, path):
        if path in self._asking:
            return
        self._asking.add(path)
        name = os.path.basename(path)
        box = QMessageBox(QMessageBox.Question, "Sync Conflict",
                          f"{name} was changed here and on Google Drive since the last sync. Which version do you "
                          "want to keep?", parent=self.window)
        mine = box.addButton("Keep Mine", QMessageBox.AcceptRole)
        theirs = box.addButton("Use Google Drive's", QMessageBox.DestructiveRole)
        both = box.addButton("Keep Both", QMessageBox.ActionRole)
        box.addButton("Decide Later", QMessageBox.RejectRole)
        box.exec()
        self._asking.discard(path)
        clicked = box.clickedButton()
        if clicked is mine:
            self.sync.resolve_conflict(path, "mine")
        elif clicked is theirs:
            self.sync.resolve_conflict(path, "theirs")
        elif clicked is both:
            self.sync.resolve_conflict(path, "both")

    # ---- signing -----------------------------------------------------------------------
    def show_signing_setup(self):
        SigningSetupDialog(self, self.window).exec()

    def show_requests(self):
        RequestsDialog(self, self.window).exec()

    def _pdf_ready_for_signing(self, title):
        """The current PDF tab, saved (the signature goes into the saved file), or None."""
        w = self.window
        tab = w.current_tab()
        if tab is None or not tab.document.is_open:
            QMessageBox.information(w, title, "Open the PDF to be signed first.")
            return None
        if not tab.document.path or tab.document.dirty:
            QMessageBox.information(w, title, "Save the PDF first: the signature is added to the saved file when "
                                    "it comes back.")
            w.save_document()
            if not tab.document.path or tab.document.dirty:
                return None
        return tab

    def show_sign_account(self):
        SignAccountDialog(self, self.window).exec()

    def sign_service_changed(self):
        self.service.check()

    def request_signature(self):
        """By email through the signature service when signed in; otherwise
        offer to sign in (or set one up), or make a signing file."""
        w = self.window
        if w.current_tab() is None or not w.current_tab().document.is_open:
            QMessageBox.information(w, "Request Signature", "Open the PDF to be signed first.")
            return
        if sign_service.current_client() is None:
            box = QMessageBox(QMessageBox.Question, "Request Signature",
                              "Send it by email, and get the signature back automatically? That needs you signed in "
                              "to a signature service (free; set up once).\n\nOr make a signing file to send "
                              "yourself (WhatsApp, email...), with nothing to sign in to.", parent=w)
            sign_in = box.addButton("Sign In by Email...", QMessageBox.AcceptRole)
            as_file = box.addButton("Make a Signing File", QMessageBox.ActionRole)
            box.addButton(QMessageBox.Cancel)
            box.exec()
            if box.clickedButton() is as_file:
                self.request_signature_file()
                return
            if box.clickedButton() is not sign_in:
                return
            self.show_sign_account()
            if sign_service.current_client() is None:
                return
        self.request_signature_service()

    def _signed_in_client(self):
        """The signature service client, asking to sign in first (once) if needed."""
        client = sign_service.current_client()
        if client is None:
            self.show_sign_account()
            client = sign_service.current_client()
        return client

    def request_signature_quick(self):
        """The easiest way: the client's email, Send; the signature comes back by itself."""
        w = self.window
        tab = self._pdf_ready_for_signing("Quick Email Request")
        if tab is None or self._signed_in_client() is None:
            return
        doc_bytes = tab.document.doc.tobytes()
        dlg = QuickEmailDialog(doc_bytes, os.path.splitext(tab.display_name())[0], w)
        if dlg.exec() != QDialog.Accepted:
            return
        v = dlg.values()
        v.update(expires_days=14, my_name=_settings().value("sign/my_name", ""))
        self._send_by_service(tab, doc_bytes, v, quick=True)

    def request_signature_email(self):
        if self._signed_in_client() is not None:
            self.request_signature_service()

    def request_signature_service(self):
        w = self.window
        tab = self._pdf_ready_for_signing("Request Signature")
        client = sign_service.current_client()
        if tab is None or client is None:
            return
        doc_bytes = tab.document.doc.tobytes()
        dlg = RequestSignatureDialog(doc_bytes, os.path.splitext(tab.display_name())[0], w, mode="service")
        if dlg.exec() != QDialog.Accepted:
            return
        self._send_by_service(tab, doc_bytes, dlg.values())

    def _send_by_service(self, tab, doc_bytes, v, quick=False):
        w = self.window
        client = sign_service.current_client()
        if client is None:
            return
        session = sign_service.load_session() or {}
        progress = _Progress(self)
        progress.text.connect(lambda text: w.statusBar().showMessage(text))
        doc_path = tab.document.path

        def job():
            return sign_service.send_request(client, doc_bytes, v["title"], doc_path, v["page_index"], v["rect"],
                                             v["signer_email"], v["signer_name"], v["message"], v["my_name"],
                                             session.get("email", ""), v["expires_days"], progress.text.emit,
                                             quick=quick)

        def done(req):
            w.statusBar().showMessage(f"Sent to {req.signer_email}. The signature is added here when they sign.", 10000)
            self.signing.store.add(req)
            how = ("They click the link in the email and sign." if quick else
                   "They get an email with a link, confirm it's their inbox with a code, and sign.")
            QMessageBox.information(w, "Request Signature", f"Sent to {req.signer_email}.\n\n{how} You get an email "
                                    "when they have signed, and Aupedean adds the signature to this PDF by itself "
                                    "(it checks every minute while it's open).")

        def failed(exc):
            w.statusBar().clearMessage()
            if isinstance(exc, sign_service.ServiceError) and exc.code == "signed-out":
                sign_service.sign_out()
            QMessageBox.warning(w, "Request Signature", str(exc) if isinstance(exc, sign_service.ServiceError)
                                else friendly(exc))

        worker.run(job, done, failed)

    def request_signature_file(self):
        """A signing file: free, no accounts, sent any way you like."""
        w = self.window
        tab = self._pdf_ready_for_signing("Request Signature")
        if tab is None:
            return
        doc_bytes = tab.document.doc.tobytes()
        dlg = RequestSignatureDialog(doc_bytes, os.path.splitext(tab.display_name())[0], w)
        if dlg.exec() != QDialog.Accepted:
            return
        v = dlg.values()
        if v["signer_email"] and self._send_directly(tab, doc_bytes, v):
            return
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            page_html, req = signing_file.make_signing_file(
                doc_bytes, v["title"], tab.document.path, v["page_index"], v["rect"], v["signer_name"],
                v["signer_email"], v["message"], v["my_name"], v["my_email"])
            req.file_path = signing_file.save_signing_file(page_html, v["title"])
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(w, "Request Signature", f"The signing file couldn't be made:\n{e}")
            return
        finally:
            QGuiApplication.restoreOverrideCursor()
        self.signing.store.add(req)
        self.returns.update()
        SigningFileDialog(req.file_path, req, w).exec()

    def _send_directly(self, tab, doc_bytes, v):
        """Email the request to the signer from here (a quick request: they
        sign from the link, and it comes back by itself). False when there's
        no signature service to send it through: a signing file is made instead."""
        if not sign_service.service_url():
            QMessageBox.information(self.window, "Request Signature", "Sending straight from Aupedean needs the "
                                    "signature service, which isn't set up yet. A signing file is made instead: "
                                    "use Email It to send it.")
            return False
        if self._signed_in_client() is None:
            return False
        self._send_by_service(tab, doc_bytes, v, quick=True)
        return True

    def add_returned(self):
        path, _ = QFileDialog.getOpenFileName(self.window, "Add a Signed Copy", os.path.expanduser("~/Downloads"),
                                              "PDF Files (*.pdf)")
        if path and not self.take_returned(path):
            QMessageBox.information(self.window, "Add a Signed Copy", "That PDF isn't a signed copy of one of your "
                                    "signature requests (or it was already added).")

    def take_returned(self, path, automatic=False):
        """If `path` is a signed copy of a waiting signing file, stamp the
        signature into the original. Returns True when it was one of ours."""
        receipt = signing_file.read_receipt(path)
        if receipt is None:
            return False
        req = signing_file.match(self.signing.store, receipt)
        if req is None:
            return False
        if req.status == signing.APPLIED:
            if not automatic:
                QMessageBox.information(self.window, "Signed Copy", f"{receipt.get('name') or 'The signer'}'s "
                                        f"signature is already in {os.path.basename(req.doc_path)}.")
                self.window.open_files_as_tabs([req.doc_path], check_signed=False)
            return True
        if req.status != signing.WAITING:
            return False
        png, result = signing_file.signature_of(receipt)
        self._apply_signature(req, png, result)
        if req.status == signing.APPLIED:
            req.signed_name, req.signed_email = result["name"], result["email"]
            req.signed_at = result["signed_at"]
            self.signing.store.save()
            self.returns.update()
            self.window.open_files_as_tabs([req.doc_path], check_signed=False)
            if automatic:
                QMessageBox.information(self.window, "Signed", f"{result['name']} signed \"{req.title}\". The "
                                        f"signature is now in {os.path.basename(req.doc_path)}.")
        return True

    def request_signature_google(self):
        w = self.window
        tab = w.current_tab()
        if tab is None or not tab.document.is_open:
            QMessageBox.information(w, "Request Signature", "Open the PDF to be signed first.")
            return
        session = self._need_session()
        if session is None:
            return
        config = signing.load_config()
        if not config or not config.get("approved"):
            QMessageBox.information(w, "Request Signature", "Set up signing links first (one time).")
            self.show_signing_setup()
            config = signing.load_config()
            if not config or not config.get("approved"):
                return
        if not tab.document.path or tab.document.dirty:
            QMessageBox.information(w, "Request Signature", "Save the PDF first: the signature is added to the "
                                    "saved file when it comes back.")
            w.save_document()
            if not tab.document.path or tab.document.dirty:
                return
        dlg = RequestSignatureDialog(tab.document.doc.tobytes(), os.path.splitext(tab.display_name())[0], w,
                                     google=True)
        if dlg.exec() != QDialog.Accepted:
            return
        v = dlg.values()
        entry = self.sync.entry(tab.document.path)
        progress = _Progress(self)
        progress.text.connect(lambda text: w.statusBar().showMessage(text))
        doc_bytes, doc_path = tab.document.doc.tobytes(), tab.document.path

        def job():
            return signing.create_request(session, config, doc_bytes, v["title"], doc_path, v["page_index"],
                                          v["rect"], v["signer_email"], v["signer_name"], v["message"],
                                          v["expires_days"], entry.file_id if entry else "", progress.text.emit)

        def done(req):
            w.statusBar().clearMessage()
            self.signing.store.add(req)
            LinkDialog(signing.link(config, req), req, w).exec()

        worker.run(job, done, lambda exc: (w.statusBar().clearMessage(),
                                           QMessageBox.warning(w, "Request Signature", friendly(exc))))

    def _apply_signature(self, req, png, result):
        w = self.window
        tab = w.tab_for_path(req.doc_path)
        try:
            if tab is not None and hasattr(tab, "document"):
                was_clean = not tab.document.dirty
                signing.stamp_signature(tab.document.doc, req, png, result)
                tab.document.snapshot()
                tab.document.invalidate_page_cache()
                tab.rebuild_viewer()
                if was_clean:
                    tab.save()
                w.on_tab_content_changed(tab)
            elif os.path.isfile(req.doc_path):
                doc = fitz.open(req.doc_path)
                signing.stamp_signature(doc, req, png, result)
                doc.saveIncr() if doc.can_save_incrementally() else doc.save(req.doc_path + ".tmp")
                doc.close()
                if os.path.exists(req.doc_path + ".tmp"):
                    os.replace(req.doc_path + ".tmp", req.doc_path)
            else:
                req.error = f"The PDF isn't at {req.doc_path} any more."
                self.signing.store.save()
                return
        except Exception as e:  # noqa: BLE001
            req.error = str(e)
            self.signing.store.save()
            QMessageBox.warning(w, "Signature", f"The signature from {req.signer_email} came back, but it couldn't "
                                f"be added to the PDF:\n{e}")
            return
        self.signing.mark_applied(req)
        if req.kind == "service":
            self.service.acknowledge(req)
        when = time.strftime("%H:%M")
        w.statusBar().showMessage(f"{result.get('name') or req.signer_email} signed \"{req.title}\" ({when}); "
                                  "the signature is in the PDF.", 15000)
