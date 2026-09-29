"""Build a handwriting font from a filled-in glyph sheet (command-line
version of Tools > Handwriting Font > Create Font from Scan... in the app).

    python tools/font_builder/build_font.py scan.jpg
        [--out app/assets/fonts/AUPedean.ttf] [--family AUPedean] [--debug debug_dir]

By default the font is saved to the per-user fonts folder the app reads
(%APPDATA%\\AupedeanAnnotator\\fonts). Use --out app/assets/fonts/... to ship
it inside the app instead.
"""
import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))

from PySide6.QtGui import QGuiApplication  # noqa: E402

from pdfannotator import fonts  # noqa: E402
from pdfannotator.handwriting.builder import BuildError, build_font  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", help="photo or scan of the filled-in glyph sheet")
    ap.add_argument("--family", default=fonts.HANDWRITING_FONT)
    ap.add_argument("--out", help="output .ttf (default: user fonts folder)")
    ap.add_argument("--debug", help="folder to write the straightened sheet and per-glyph masks")
    args = ap.parse_args()
    out = Path(args.out) if args.out else fonts.USER_FONTS_DIR / f"{args.family}.ttf"

    app = QGuiApplication(sys.argv)  # noqa: F841 - Qt image code needs an application
    try:
        result = build_font(args.image, out, args.family, args.debug)
    except BuildError as e:
        sys.exit(str(e))
    print(f"Wrote {Path(result['path']).resolve()}")
    print(f"  {len(result['found'])} glyphs: {result['found']}")
    if result["skipped"]:
        print(f"  empty boxes skipped: {result['skipped']}")


if __name__ == "__main__":
    main()
