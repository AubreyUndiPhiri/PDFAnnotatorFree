import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

from pdfannotator import theme
from pdfannotator.main_window import MainWindow, resource_path


def main():
    if sys.platform == "win32":
        # Own taskbar identity, so Windows shows our icon instead of Python's
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AupedeanAnnotator.App")

    app = QApplication(sys.argv)
    app.setApplicationName("Aupedean Annotator")
    app.setOrganizationName("AupedeanAnnotator")
    app.setWindowIcon(QIcon(resource_path("assets", "aupedean_annotator.svg")))
    theme.apply(app)

    window = MainWindow()
    window.resize(1440, 900)
    window.show()
    import os

    # files passed on the command line (e.g. "Open with" in Explorer)
    files = [path for path in sys.argv[1:] if os.path.isfile(path)]
    if files:
        window.open_files_as_tabs(files)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
