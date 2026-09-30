"""Keep local files and their Google Drive copies in step, in the background.

A Drive file opened in the app is downloaded to a cache folder and opened
from there. Every save (by any tab, or another program) is noticed by a file
watcher and uploaded a moment later as a new revision. While a synced file is
open, Drive is checked every minute: a newer Drive version is downloaded and
the tab reloads, unless there are unsaved or un-uploaded local changes too,
which is a conflict the user resolves (keep mine / use Drive's / keep both).
Offline, uploads wait and are retried.

The list of synced files is kept in %APPDATA%/AupedeanAnnotator/google/sync.json.
"""
import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

from . import worker
from .drive import DOCX, EXPORTS, DriveClient, local_name, mime_for
from .google_auth import GOOGLE_DIR, ApiError, AuthError, Offline

CACHE_DIR = GOOGLE_DIR / "cache"
REGISTRY_FILE = GOOGLE_DIR / "sync.json"

SYNCED, UPLOADING, DOWNLOADING, OFFLINE, CONFLICT, ERROR, SIGNED_OUT = (
    "synced", "uploading", "downloading", "offline", "conflict", "error", "signed out")
STATE_TEXT = {
    SYNCED: "Synced with Google Drive",
    UPLOADING: "Uploading to Google Drive...",
    DOWNLOADING: "Downloading from Google Drive...",
    OFFLINE: "Offline: changes will upload when you're back online",
    CONFLICT: "Changed here and on Google Drive",
    ERROR: "Google Drive sync problem",
    SIGNED_OUT: "Not signed in to Google: changes will upload after you connect",
}


def file_md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def norm(path):
    return os.path.normcase(os.path.abspath(path))


def cache_path(meta):
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", local_name(meta)).strip() or "file"
    return str(CACHE_DIR / meta["id"] / safe)


@dataclass
class SyncEntry:
    file_id: str
    name: str
    mime: str
    local_path: str
    remote_version: str = ""
    local_md5: str = ""
    parent_id: str = ""
    web_link: str = ""
    state: str = SYNCED
    message: str = ""
    pending: bool = False          # local changes not uploaded yet
    synced_at: float = field(default_factory=time.time)

    @property
    def exported(self):
        return self.mime in EXPORTS


class Registry:
    def __init__(self, path=None):
        self.path = path or REGISTRY_FILE
        self.entries = {}
        try:
            with open(self.path, encoding="utf-8") as f:
                for item in json.load(f):
                    entry = SyncEntry(**{k: v for k, v in item.items() if k in SyncEntry.__dataclass_fields__})
                    self.entries[norm(entry.local_path)] = entry
        except (OSError, ValueError, TypeError):
            pass

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = str(self.path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump([asdict(e) for e in self.entries.values()], f, indent=1)
        os.replace(tmp, self.path)

    def get(self, path):
        return self.entries.get(norm(path)) if path else None

    def by_id(self, file_id):
        return next((e for e in self.entries.values() if e.file_id == file_id), None)

    def add(self, entry):
        self.entries[norm(entry.local_path)] = entry
        self.save()

    def remove(self, path):
        self.entries.pop(norm(path), None)
        self.save()


class SyncManager(QObject):
    state_changed = Signal(str)      # local path whose sync state changed
    remote_updated = Signal(str)     # the local file now holds a newer Drive version: reload it
    conflict = Signal(str)           # changed here and on Drive
    notice = Signal(str)             # a short message for the status bar

    POLL_MS = 60_000
    DEBOUNCE_MS = 1500

    def __init__(self, session_factory, open_paths=None, parent=None, registry=None):
        """session_factory(): a google_auth.Session, or None when not
        connected. open_paths(): the paths open in tabs (checked on Drive
        every minute; other synced files are checked when opened)."""
        super().__init__(parent)
        self._session_factory = session_factory
        self._open_paths = open_paths or (lambda: [])
        self.registry = registry or Registry()
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self._local_changed)
        self._timers = {}
        self._busy = set()
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(self.POLL_MS)
        self.poll_timer.timeout.connect(self.poll)
        self.poll_timer.start()
        for entry in self.registry.entries.values():
            self._watch(entry.local_path)

    # ---- helpers --------------------------------------------------------------
    def _drive(self):
        session = self._session_factory()
        if session is None:
            raise AuthError("Not connected to a Google account.")
        return DriveClient(session)

    def entry(self, path):
        return self.registry.get(path)

    def status_text(self, path):
        entry = self.entry(path)
        if entry is None:
            return ""
        text = STATE_TEXT.get(entry.state, entry.state)
        return f"{text}: {entry.message}" if entry.state == ERROR and entry.message else text

    def _set_state(self, entry, state, message=""):
        entry.state, entry.message = state, message
        self.registry.save()
        self.state_changed.emit(entry.local_path)

    def _watch(self, path):
        if os.path.isfile(path) and path not in self.watcher.files():
            self.watcher.addPath(path)

    def _fail(self, entry, exc):
        if isinstance(exc, Offline):
            entry.pending = True
            self._set_state(entry, OFFLINE)
        elif isinstance(exc, AuthError):
            entry.pending = True
            self._set_state(entry, SIGNED_OUT)
        elif isinstance(exc, ApiError) and exc.status == 404:
            self._set_state(entry, ERROR, "the file was deleted from Google Drive, or you no longer have access")
        else:
            self._set_state(entry, ERROR, getattr(exc, "message", None) or str(exc))

    # ---- opening ----------------------------------------------------------------
    def open_remote(self, meta, on_opened, on_error):
        """Download (if needed) a Drive file and call on_opened(local_path)."""
        existing = self.registry.by_id(meta["id"])
        if existing and os.path.isfile(existing.local_path):
            local_changed = file_md5(existing.local_path) != existing.local_md5
            if local_changed or str(meta.get("version", "")) == existing.remote_version:
                on_opened(existing.local_path)   # up to date, or has changes of its own still to upload
                if local_changed:
                    self.schedule_upload(existing.local_path)
                return
        path = existing.local_path if existing else cache_path(meta)

        def job():
            drive = self._drive()
            fresh = drive.download(meta, path)
            return fresh, file_md5(path)

        def done(result):
            fresh, md5 = result
            entry = SyncEntry(file_id=fresh["id"], name=fresh["name"], mime=fresh.get("mimeType", ""),
                              local_path=path, remote_version=str(fresh.get("version", "")), local_md5=md5,
                              parent_id=(fresh.get("parents") or [""])[0], web_link=fresh.get("webViewLink", ""))
            self.registry.add(entry)
            self._watch(path)
            on_opened(path)
            self.state_changed.emit(path)

        worker.run(job, done, on_error)

    def add_local(self, path, parent_id, name, on_done, on_error):
        """Upload a local file to Drive and keep it synced from now on."""
        def job():
            drive = self._drive()
            meta = drive.create(name, parent_id, path=path)
            return meta, file_md5(path)

        def done(result):
            meta, md5 = result
            entry = SyncEntry(file_id=meta["id"], name=meta["name"], mime=meta.get("mimeType", ""),
                              local_path=path, remote_version=str(meta.get("version", "")), local_md5=md5,
                              parent_id=parent_id, web_link=meta.get("webViewLink", ""))
            self.registry.add(entry)
            self._watch(path)
            self.state_changed.emit(path)
            on_done(meta)

        worker.run(job, done, on_error)

    def forget(self, path):
        if self.entry(path):
            self.watcher.removePath(path)
            self.registry.remove(path)
            self.state_changed.emit(path)

    # ---- uploading --------------------------------------------------------------
    def _local_changed(self, path):
        self._watch(path)  # editors that replace the file drop it from the watcher
        timer = self._timers.get(path)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(self.DEBOUNCE_MS)
            timer.timeout.connect(lambda p=path: self.schedule_upload(p))
            self._timers[path] = timer
        timer.start()

    def schedule_upload(self, path, force=False):
        """Upload the file if it changed since the last sync. force: even if
        Drive has a newer version (the user chose to keep theirs)."""
        entry = self.entry(path)
        if entry is None or not os.path.isfile(path):
            return
        try:
            md5 = file_md5(path)
        except OSError:
            QTimer.singleShot(1000, lambda: self.schedule_upload(path, force))  # still being written
            return
        if md5 == entry.local_md5 and not entry.pending:
            return
        if path in self._busy:
            entry.pending = True
            return
        self._busy.add(path)
        entry.pending = True
        self._set_state(entry, UPLOADING)
        mime = DOCX if entry.exported else mime_for(path)

        def job():
            drive = self._drive()
            if not force and entry.remote_version:
                remote = drive.get(entry.file_id)
                if str(remote.get("version", "")) != entry.remote_version:
                    return "conflict", remote, md5
            return "uploaded", drive.update_content(entry.file_id, path, mime=mime), md5

        def done(result):
            self._busy.discard(path)
            outcome, meta, sent_md5 = result
            if outcome == "conflict":
                self._set_state(entry, CONFLICT)
                self.conflict.emit(path)
                return
            entry.remote_version = str(meta.get("version", ""))
            entry.local_md5 = sent_md5
            entry.synced_at = time.time()
            entry.pending = False
            self._set_state(entry, SYNCED)
            if os.path.isfile(path) and file_md5(path) != sent_md5:
                self.schedule_upload(path)  # saved again while uploading

        def failed(exc):
            self._busy.discard(path)
            self._fail(entry, exc)

        worker.run(job, done, failed)

    # ---- checking Drive for changes made elsewhere ----------------------------------
    def poll(self, paths=None):
        open_now = {norm(p) for p in (paths if paths is not None else self._open_paths()) if p}
        for key, entry in list(self.registry.entries.items()):
            if entry.local_path in self._busy or not os.path.isfile(entry.local_path):
                continue
            if entry.pending:
                self.schedule_upload(entry.local_path)
            elif key in open_now and entry.state != CONFLICT:
                self._check_remote(entry)

    def _check_remote(self, entry):
        path = entry.local_path
        self._busy.add(path)

        def job():
            drive = self._drive()
            meta = drive.get(entry.file_id)
            if str(meta.get("version", "")) == entry.remote_version:
                return "same", meta
            if file_md5(path) != entry.local_md5:
                return "conflict", meta
            drive.download(meta, path)
            return "downloaded", meta

        def done(result):
            self._busy.discard(path)
            outcome, meta = result
            if outcome == "same":
                if entry.state in (OFFLINE, SIGNED_OUT, ERROR):
                    self._set_state(entry, SYNCED)
                return
            if outcome == "conflict":
                self._set_state(entry, CONFLICT)
                self.conflict.emit(path)
                return
            entry.remote_version = str(meta.get("version", ""))
            entry.local_md5 = file_md5(path)
            entry.synced_at = time.time()
            self._set_state(entry, SYNCED)
            self.notice.emit(f"{entry.name} was changed on Google Drive; the newer version is open now.")
            self.remote_updated.emit(path)

        def failed(exc):
            self._busy.discard(path)
            self._fail(entry, exc)

        worker.run(job, done, failed)

    def resolve_conflict(self, path, choice, on_done=None):
        """choice: "mine" (upload over Drive's), "theirs" (take Drive's) or
        "both" (Drive's version stays; mine is uploaded as a copy next to it)."""
        entry = self.entry(path)
        if entry is None:
            return
        if choice == "mine":
            entry.pending = True
            self.schedule_upload(path, force=True)
            return
        self._busy.add(path)
        self._set_state(entry, DOWNLOADING)

        def job():
            drive = self._drive()
            copy = None
            if choice == "both":
                stem, ext = os.path.splitext(local_name({"name": entry.name, "mimeType": entry.mime}))
                copy = drive.create(f"{stem} (my copy {time.strftime('%Y-%m-%d %H.%M')}){ext}",
                                    entry.parent_id or "root", path=path)
            meta = drive.download(entry.file_id, path)
            return meta, copy

        def done(result):
            self._busy.discard(path)
            meta, copy = result
            entry.remote_version = str(meta.get("version", ""))
            entry.local_md5 = file_md5(path)
            entry.pending = False
            self._set_state(entry, SYNCED)
            if copy:
                self.notice.emit(f"Your version was saved on Google Drive as \"{copy['name']}\".")
            self.remote_updated.emit(path)
            if on_done:
                on_done()

        def failed(exc):
            self._busy.discard(path)
            self._fail(entry, exc)

        worker.run(job, done, failed)
