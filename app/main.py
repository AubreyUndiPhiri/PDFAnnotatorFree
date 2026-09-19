import sys
from PySide6.QtWidgets import QApplication

from pdfannotator.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PDF Annotator Free")
    app.setOrganizationName("PDFAnnotatorFree")
    window = MainWindow()
    window.resize(1400, 900)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
