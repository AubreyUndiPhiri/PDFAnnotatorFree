"""Google Drive (REST API v3): browse, download, upload.

Uploads always use the resumable protocol (one PUT of the whole file after
opening a session), which works for any size. Google Docs have no file to
download, so they are exported as .docx (and uploaded back the same way,
which Drive converts into the Google Doc).

No Qt here.
"""
import json
import os
import urllib.parse

API = "https://www.googleapis.com/drive/v3"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"

FOLDER = "application/vnd.google-apps.folder"
GOOGLE_DOC = "application/vnd.google-apps.document"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF = "application/pdf"
EXPORTS = {GOOGLE_DOC: (DOCX, ".docx")}   # Google-native type -> (download as, file extension)
FIELDS = ("id,name,mimeType,modifiedTime,size,md5Checksum,version,parents,trashed,webViewLink,"
          "capabilities(canEdit),owners(displayName,emailAddress),shortcutDetails")

MIME_BY_EXT = {
    ".pdf": PDF, ".docx": DOCX, ".tex": "text/x-tex", ".bib": "text/x-bibtex", ".txt": "text/plain",
    ".md": "text/markdown", ".html": "text/html", ".htm": "text/html", ".odt": "application/vnd.oasis.opendocument.text",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".json": "application/json",
}
OPENABLE_EXT = (".pdf", ".docx", ".tex", ".bib", ".sty", ".cls", ".txt", ".md", ".html", ".htm")


def mime_for(path):
    return MIME_BY_EXT.get(os.path.splitext(path)[1].lower(), "application/octet-stream")


def local_name(meta):
    """The file name to use on disk (Google Docs get their export extension)."""
    name = meta["name"]
    export = EXPORTS.get(meta.get("mimeType"))
    if export and not name.lower().endswith(export[1]):
        name += export[1]
    return name


def is_openable(meta):
    if meta.get("mimeType") in EXPORTS:
        return True
    return meta.get("mimeType") != FOLDER and local_name(meta).lower().endswith(OPENABLE_EXT)


def replace_file(tmp, dest):
    """os.replace, patient with Windows: antivirus or a reader holding the
    file for a moment makes it fail with "Access is denied". Falls back to
    overwriting the file in place."""
    import time

    for _ in range(25):
        try:
            os.replace(tmp, dest)
            return
        except PermissionError:
            time.sleep(0.1)
    with open(tmp, "rb") as src, open(dest, "wb") as out:
        out.write(src.read())
    os.remove(tmp)


def _q(value):
    return value.replace("\\", "\\\\").replace("'", "\\'")


class DriveClient:
    def __init__(self, session):
        self.session = session

    # ---- listing ---------------------------------------------------------------
    def list(self, query, order="folder,name_natural", limit=1000, corpora=None):
        files, token = [], None
        while True:
            params = {"q": query, "fields": f"nextPageToken,files({FIELDS})", "pageSize": 200, "orderBy": order,
                      "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
            if corpora:
                params["corpora"] = corpora
            if token:
                params["pageToken"] = token
            page = self.session.json("GET", f"{API}/files", params)
            files.extend(page.get("files", []))
            token = page.get("nextPageToken")
            if not token or len(files) >= limit:
                return files[:limit]

    def children(self, folder_id="root"):
        return self.list(f"'{_q(folder_id)}' in parents and trashed = false")

    def shared_with_me(self):
        return self.list("sharedWithMe = true and trashed = false", order="folder,modifiedTime desc")

    def recent(self):
        return self.list(f"trashed = false and mimeType != '{FOLDER}'", order="modifiedTime desc", limit=100)

    def starred(self):
        return self.list("starred = true and trashed = false")

    def search(self, text):
        return self.list(f"name contains '{_q(text)}' and trashed = false", order="folder,modifiedTime desc",
                         limit=200)

    def get(self, file_id):
        return self.session.json("GET", f"{API}/files/{urllib.parse.quote(file_id)}",
                                 {"fields": FIELDS, "supportsAllDrives": "true"})

    def find_child(self, parent_id, name, folder=None):
        query = f"'{_q(parent_id)}' in parents and name = '{_q(name)}' and trashed = false"
        if folder is True:
            query += f" and mimeType = '{FOLDER}'"
        found = self.list(query, order="modifiedTime desc", limit=1)
        return found[0] if found else None

    # ---- content -----------------------------------------------------------------
    def download(self, meta_or_id, dest_path):
        """Save the file's content to dest_path (Google Docs are exported).
        Returns the file's metadata."""
        meta = meta_or_id if isinstance(meta_or_id, dict) else self.get(meta_or_id)
        file_id = urllib.parse.quote(meta["id"])
        export = EXPORTS.get(meta.get("mimeType"))
        if export:
            _s, _h, body = self.session.request("GET", f"{API}/files/{file_id}/export", {"mimeType": export[0]},
                                                timeout=300)
        else:
            _s, _h, body = self.session.request("GET", f"{API}/files/{file_id}",
                                                {"alt": "media", "supportsAllDrives": "true"}, timeout=300)
        os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
        tmp = dest_path + ".download"
        with open(tmp, "wb") as f:
            f.write(body)
        replace_file(tmp, dest_path)
        return meta

    def download_bytes(self, file_id):
        _s, _h, body = self.session.request("GET", f"{API}/files/{urllib.parse.quote(file_id)}",
                                            {"alt": "media", "supportsAllDrives": "true"}, timeout=120)
        return body

    def _resumable(self, method, url, metadata, content: bytes, mime):
        _s, headers, _b = self.session.request(
            method, url, {"uploadType": "resumable", "fields": FIELDS, "supportsAllDrives": "true"},
            data=json.dumps(metadata).encode(),
            headers={"Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": mime,
                     "X-Upload-Content-Length": str(len(content))})
        location = next((v for k, v in headers.items() if k.lower() == "location"), None)
        if not location:
            raise OSError("Google Drive didn't start the upload.")
        # The session URL carries its own authorisation, but the token doesn't hurt
        _s, _h, body = self.session.request("PUT", location, data=content, headers={"Content-Type": mime},
                                            timeout=600)
        return json.loads(body)

    def update_content(self, file_id, path=None, content=None, mime=None):
        """Replace a file's content (a new revision). Returns its metadata."""
        if content is None:
            with open(path, "rb") as f:
                content = f.read()
        return self._resumable("PATCH", f"{UPLOAD_API}/files/{urllib.parse.quote(file_id)}", {}, content,
                               mime or mime_for(path or ""))

    def create(self, name, parent_id="root", path=None, content=None, mime=None, description=None):
        """Upload a new file into the folder parent_id. Returns its metadata."""
        if content is None:
            with open(path, "rb") as f:
                content = f.read()
        metadata = {"name": name, "parents": [parent_id]}
        if description:
            metadata["description"] = description
        return self._resumable("POST", f"{UPLOAD_API}/files", metadata, content, mime or mime_for(path or name))

    def create_folder(self, name, parent_id="root", description=None):
        body = {"name": name, "mimeType": FOLDER, "parents": [parent_id]}
        if description:
            body["description"] = description
        return self.session.json("POST", f"{API}/files", {"fields": FIELDS, "supportsAllDrives": "true"}, body)

    def ensure_folder(self, name, parent_id="root", description=None):
        return self.find_child(parent_id, name, folder=True) or self.create_folder(name, parent_id, description)

    def delete(self, file_id):
        self.session.request("DELETE", f"{API}/files/{urllib.parse.quote(file_id)}", {"supportsAllDrives": "true"})

    def trash(self, file_id):
        self.session.json("PATCH", f"{API}/files/{urllib.parse.quote(file_id)}", {"supportsAllDrives": "true"},
                          {"trashed": True})
