"""My Signatures: up to MAX_SIGNATURES signature pictures (PNG) kept on this
computer, so a signature drawn once (here, or on a phone) can be used again."""
import os
import time
import uuid
from pathlib import Path

from .fonts import APP_DATA

MAX_SIGNATURES = 5
LIBRARY_DIR = Path(os.environ.get("AUPEDEAN_SIGNATURES_DIR") or APP_DATA / "signatures")   # tests use a temp folder


class LibraryFull(Exception):
    def __init__(self):
        super().__init__(f"You can keep up to {MAX_SIGNATURES} signatures. Remove one to save another.")


def signatures():
    """The saved signatures' files, oldest first."""
    try:
        files = [p for p in LIBRARY_DIR.glob("*.png") if p.is_file()]
    except OSError:
        return []
    return sorted(files, key=lambda p: (p.stat().st_mtime, p.name))


def is_full():
    return len(signatures()) >= MAX_SIGNATURES


def add(png_bytes: bytes) -> Path:
    """Save a signature; raises LibraryFull when there are already MAX_SIGNATURES."""
    if is_full():
        raise LibraryFull()
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    path = LIBRARY_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.png"
    path.write_bytes(png_bytes)
    return path


def remove(path):
    path = Path(path)
    if path.parent.resolve() == LIBRARY_DIR.resolve():
        try:
            path.unlink()
        except OSError:
            pass
