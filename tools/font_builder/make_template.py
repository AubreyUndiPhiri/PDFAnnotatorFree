"""Write the printable glyph sheet (command-line version of Tools >
Handwriting Font > Save Glyph Sheet... in the app).

    python tools/font_builder/make_template.py [out.pdf]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))

from pdfannotator.handwriting.sheet import make_sheet  # noqa: E402

if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "AUPedean_glyph_sheet.pdf")
    make_sheet(out)
    print(f"Wrote {out.resolve()}")
