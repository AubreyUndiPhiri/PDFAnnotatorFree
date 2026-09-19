import fitz
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QAbstractItemView, QMenu
from PySide6.QtGui import QImage, QPixmap, QIcon
from PySide6.QtCore import Qt, QSize, Signal

from . import pdf_ops


class ThumbnailPanel(QListWidget):
    pageActivated = Signal(int)
    pagesReordered = Signal(list)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setViewMode(QListWidget.IconMode)
        self.setFlow(QListWidget.TopToBottom)
        self.setIconSize(QSize(120, 160))
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Snap)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setSpacing(8)
        self.setFixedWidth(160)
        self._suppress_reorder_signal = False
        self.itemClicked.connect(self._on_item_clicked)
        self.model().rowsMoved.connect(self._on_rows_moved)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

    def rebuild(self):
        self._suppress_reorder_signal = True
        self.clear()
        doc = self.controller.document
        if doc.is_open:
            for i in range(doc.page_count):
                item = QListWidgetItem(f"Page {i + 1}")
                item.setIcon(QIcon(self._thumb(i)))
                item.setData(Qt.UserRole, i)
                item.setTextAlignment(Qt.AlignHCenter)
                self.addItem(item)
        self._suppress_reorder_signal = False

    def refresh_one(self, index):
        if 0 <= index < self.count():
            self.item(index).setIcon(QIcon(self._thumb(index)))

    def _thumb(self, index) -> QPixmap:
        page = self.controller.document.page(index)
        mat = pdf_ops.render_matrix(0.2)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
        return QPixmap.fromImage(img)

    def _on_item_clicked(self, item):
        self.pageActivated.emit(item.data(Qt.UserRole))

    def _on_rows_moved(self, *args):
        if self._suppress_reorder_signal:
            return
        order = [self.item(i).data(Qt.UserRole) for i in range(self.count())]
        self.pagesReordered.emit(order)

    def _on_context_menu(self, pos):
        item = self.itemAt(pos)
        if item is None:
            return
        index = item.data(Qt.UserRole)
        menu = QMenu(self)
        rotate_left = menu.addAction("Rotate Left")
        rotate_right = menu.addAction("Rotate Right")
        menu.addSeparator()
        insert_after = menu.addAction("Insert Blank Page After")
        extract = menu.addAction("Extract to New PDF...")
        delete = menu.addAction("Delete Page")
        action = menu.exec(self.mapToGlobal(pos))
        if action == rotate_left:
            self.controller.rotate_page(index, -90)
        elif action == rotate_right:
            self.controller.rotate_page(index, 90)
        elif action == extract:
            self.controller.extract_page(index)
        elif action == delete:
            self.controller.delete_page(index)
        elif action == insert_after:
            self.controller.insert_blank_page(index + 1)
