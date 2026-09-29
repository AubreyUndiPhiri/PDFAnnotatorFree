import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

from pdfannotator import theme
from pdfannotator.main_window import MainWindow, resource_path


def main():
    if sys.platform == "win32":
        # Own taskbar identity, so Windows shows our icon instead of Python's
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AupedianAnnotators.App")

    app = QApplication(sys.argv)
    app.setApplicationName("Aupedian Annotators")
    app.setOrganizationName("AupedianAnnotators")
    app.setWindowIcon(QIcon(resource_path("assets", "aupedian_annotators.svg")))
    theme.apply(app)

    window = MainWindow()
    window.resize(1440, 900)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
