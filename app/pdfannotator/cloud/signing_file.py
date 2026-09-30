"""Signing files: ask for a signature with no accounts, servers or setup.

make_signing_file() writes one self-contained web page (HTML) holding the
document: the pages as pictures to read, the PDF itself, pdf-lib (a small
MIT-licensed PDF library) and a signature pad. It is sent to the signer any
way you like (WhatsApp, email...). They open it in a browser, tap the Sign
here box, draw or type their signature, agree, and send back the signed PDF
the page makes. That PDF carries a receipt (an attached
aupedean-signature.json: the request id and its secret token, name, email,
time and the signature picture).

read_receipt() finds that receipt in a returned PDF; the signature is then
stamped into the *original* document (signing.stamp_signature), so nothing
the signer's copy might have changed matters.

Free forever: nothing runs anywhere but on the two computers. The signer's
email is what they typed (unlike Google signing links, it isn't verified).
"""
import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import time
from pathlib import Path

import pymupdf as fitz

from . import signing

FORMAT = "aupedean-signature/1"
RECEIPT_NAME = "aupedean-signature.json"
TEMPLATE = Path(__file__).resolve().parent / "signing_file" / "template.html"
PDFLIB = Path(__file__).resolve().parents[2] / "assets" / "js" / "pdf-lib.min.js"
PAGE_DPI = 110
MAX_PAGES = 40
FILES_DIR = Path(os.path.expanduser("~")) / "Documents" / "Aupedean Signing Files"


def _script_safe(text):
    """Text that can sit inside a <script> element without ending it early."""
    return text.replace("</", "<\\/").replace("<!--", "<\\!--")


def document_data(pdf_bytes, page_index, rect):
    """What a signing page needs to show and sign the document: the pages as
    pictures, the PDF, and the signature box (as fractions of the page shown,
    and in PDF user space for drawing into the PDF)."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[page_index]
    user_space = fitz.Rect(rect) * ~page.transformation_matrix   # pdf-lib draws in PDF user space
    user_space.normalize()
    pages = []
    for i in range(min(doc.page_count, MAX_PAGES)):
        pix = doc[i].get_pixmap(dpi=PAGE_DPI, alpha=False)
        pages.append({"w": pix.width, "h": pix.height,
                      "img": base64.b64encode(pix.tobytes("jpeg", jpg_quality=82)).decode()})
    return {"field": signing.field_fractions(page, rect),
            "pdf_rect": [user_space.x0, user_space.y0, user_space.x1, user_space.y1],
            "pages": pages, "pdf": base64.b64encode(pdf_bytes).decode(),
            "original_sha256": hashlib.sha256(pdf_bytes).hexdigest()}


def new_request_id():
    return secrets.token_urlsafe(18).replace("-", "a").replace("_", "b")[:24]


def make_signing_file(pdf_bytes, title, doc_path, page_index, rect, signer_name="", signer_email="", message="",
                      requester_name="", requester_email=""):
    """(html text, SignRequest) for a signing file. rect: the signature box in
    unrotated PDF coordinates (as the annotation code uses)."""
    request_id = new_request_id()
    token = secrets.token_urlsafe(24)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    doc_data = document_data(pdf_bytes, page_index, rect)
    req = signing.SignRequest(
        id=request_id, token=token, title=title, signer_email=signer_email.strip().lower(),
        signer_name=signer_name.strip(), message=message.strip(), page=page_index, rect=list(fitz.Rect(rect)),
        field=doc_data["field"], folder_id="", doc_path=doc_path, created=now, kind="file")
    data = {"id": request_id, "token": token, "title": title, "message": req.message,
            "requester_name": requester_name.strip(), "requester_email": requester_email.strip(),
            "signer_name": req.signer_name, "signer_email": req.signer_email, "created": now, **doc_data}
    page_html = TEMPLATE.read_text(encoding="utf-8")
    page_html = page_html.replace("{{TITLE}}", html.escape(title))
    page_html = page_html.replace("{{DATA}}", _script_safe(json.dumps(data)))
    page_html = page_html.replace("/*{{PDFLIB}}*/", _script_safe(PDFLIB.read_text(encoding="utf-8")))
    return page_html, req


def save_signing_file(page_html, title, folder=None):
    folder = Path(folder or FILES_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c not in '<>:"/\\|?*' else " " for c in title).strip()[:80] or "document"
    path = folder / f"Sign - {safe}.html"
    n = 2
    while path.exists():
        path = folder / f"Sign - {safe} ({n}).html"
        n += 1
    path.write_text(page_html, encoding="utf-8")
    return str(path)


def read_receipt(pdf):
    """The signature receipt in a signed PDF (a path or bytes), or None."""
    try:
        doc = fitz.open(pdf) if isinstance(pdf, (str, Path)) else fitz.open(stream=pdf, filetype="pdf")
    except Exception:  # noqa: BLE001 - not a PDF we can read
        return None
    try:
        if RECEIPT_NAME not in doc.embfile_names():
            return None
        receipt = json.loads(doc.embfile_get(RECEIPT_NAME))
    except (ValueError, RuntimeError):
        return None
    finally:
        doc.close()
    if receipt.get("format") != FORMAT or not receipt.get("request_id") or not receipt.get("signature_png"):
        return None
    return receipt


def match(store, receipt):
    """The waiting request this receipt answers (its secret token must match), or None."""
    req = store.get(receipt.get("request_id", ""))
    if req is None or not hmac.compare_digest(str(req.token), str(receipt.get("token", ""))):
        return None
    return req


def signature_of(receipt):
    """(png bytes, result dict for signing.stamp_signature)."""
    png = base64.b64decode(receipt["signature_png"])
    result = {"name": receipt.get("name", ""), "email": receipt.get("email", ""),
              "signed_at": receipt.get("signed_at", ""), "verified_by": "signing file"}
    return png, result
