"""In-memory PDF document wrapper with linear undo/redo via byte snapshots."""
import fitz  # PyMuPDF


class PDFDocument:
    MAX_HISTORY = 30

    def __init__(self):
        self.doc: fitz.Document | None = None
        self.path: str | None = None
        self._history: list[bytes] = []
        self._history_index: int = -1
        self._dirty = False
        # fitz.Annot only holds a WEAK reference to its parent fitz.Page, so a
        # freshly-fetched `self.doc[index]` proxy can be garbage collected out
        # from under an annotation the UI is still holding onto (e.g. between
        # a mouse press and the matching release). Caching one Page object per
        # index keeps it alive for as long as the document itself is unchanged.
        self._page_cache: dict = {}

    @property
    def is_open(self) -> bool:
        return self.doc is not None

    @property
    def page_count(self) -> int:
        return self.doc.page_count if self.doc else 0

    @property
    def dirty(self) -> bool:
        return self._dirty

    def new(self):
        self.doc = fitz.open()
        self.doc.new_page()
        self.path = None
        self._reset_history()
        self._dirty = False
        self.invalidate_page_cache()

    def load(self, path: str):
        with open(path, "rb") as f:
            data = f.read()
        doc = fitz.open(stream=data, filetype="pdf")
        self.doc = doc
        self.path = path
        self._reset_history()
        self._dirty = False
        self.invalidate_page_cache()

    def page(self, index: int) -> fitz.Page:
        cached = self._page_cache.get(index)
        if cached is None:
            cached = self.doc[index]
            self._page_cache[index] = cached
        return cached

    def invalidate_page_cache(self):
        """Call after any structural change (page add/remove/reorder) or
        whenever self.doc is replaced, so stale Page proxies are dropped."""
        self._page_cache = {}

    def _reset_history(self):
        self._history = [self.doc.tobytes()]
        self._history_index = 0

    def snapshot(self):
        """Call after any committed edit to push a new undo state."""
        if self.doc is None:
            return
        data = self.doc.tobytes()
        self._history = self._history[: self._history_index + 1]
        self._history.append(data)
        if len(self._history) > self.MAX_HISTORY:
            self._history.pop(0)
        self._history_index = len(self._history) - 1
        self._dirty = True

    def can_undo(self) -> bool:
        return self._history_index > 0

    def can_redo(self) -> bool:
        return self._history_index < len(self._history) - 1

    def undo(self):
        if not self.can_undo():
            return
        self._history_index -= 1
        if self.doc:
            self.doc.close()
        self.doc = fitz.open(stream=self._history[self._history_index], filetype="pdf")
        self._dirty = True
        self.invalidate_page_cache()

    def redo(self):
        if not self.can_redo():
            return
        self._history_index += 1
        if self.doc:
            self.doc.close()
        self.doc = fitz.open(stream=self._history[self._history_index], filetype="pdf")
        self._dirty = True
        self.invalidate_page_cache()

    def save(self, path: str | None = None):
        target = path or self.path
        if target is None:
            raise ValueError("No path to save to")
        data = self.doc.tobytes(garbage=4, deflate=True)
        with open(target, "wb") as f:
            f.write(data)
        self.path = target
        self._dirty = False

    @property
    def metadata(self) -> dict:
        return dict(self.doc.metadata) if self.doc else {}

    def set_metadata(self, data: dict):
        self.doc.set_metadata(data)
        self.snapshot()

    def remove_all_annotations(self):
        from . import pdf_ops
        pdf_ops.remove_all_annotations(self.doc)
        self.snapshot()
        self.invalidate_page_cache()

    def melt_all_annotations(self):
        from . import pdf_ops
        pdf_ops.melt_all_annotations(self.doc)
        self.snapshot()
        self.invalidate_page_cache()

    def close(self):
        if self.doc:
            self.doc.close()
        self.doc = None
        self.path = None
        self._history = []
        self._history_index = -1
        self._dirty = False
        self.invalidate_page_cache()
