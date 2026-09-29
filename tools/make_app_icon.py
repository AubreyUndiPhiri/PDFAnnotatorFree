"""Render app/assets/aupedean_annotator.svg to the multi-size Windows .ico
used by the PyInstaller build.

    python tools/make_app_icon.py
"""
import io
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ASSETS = Path(__file__).resolve().parents[1] / "app" / "assets"
SVG = ASSETS / "aupedean_annotator.svg"
ICO = ASSETS / "aupedean_annotator.ico"
SIZES = [16, 24, 32, 48, 64, 128, 256]


def render(size):
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(str(SVG)).render(painter, QRectF(0, 0, size, size))
    painter.end()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    image.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(data)))


def main():
    app = QGuiApplication(sys.argv)  # noqa: F841 - needed for QPainter
    frames = [render(s) for s in SIZES]
    frames[-1].save(ICO, format="ICO", sizes=[(s, s) for s in SIZES], append_images=frames[:-1])
    print(f"Wrote {ICO}")


if __name__ == "__main__":
    main()
