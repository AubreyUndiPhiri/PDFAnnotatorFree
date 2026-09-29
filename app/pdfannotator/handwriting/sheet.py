"""The printable glyph sheet people fill in to make a handwriting font."""
import fitz

from . import layout as L

GUIDE = (0.78, 0.80, 0.84)   # light enough that the builder ignores it
LABEL = (0.55, 0.58, 0.64)


def make_sheet(out_path, family="AUPedean"):
    doc = fitz.open()
    page = doc.new_page(width=L.PAGE_W, height=L.PAGE_H)

    for cx, cy in L.MARKER_CENTERS:
        h = L.MARKER_SIZE / 2
        page.draw_rect(fitz.Rect(cx - h, cy - h, cx + h, cy + h), color=(0, 0, 0), fill=(0, 0, 0))

    page.insert_text((70, 44), f"{family} glyph sheet", fontsize=16, fontname="hebo")
    page.insert_text(
        (70, 62),
        "Write one character per box in dark pen. Sit it on the lower dashed line (baseline);",
        fontsize=8.5, color=LABEL,
    )
    page.insert_text(
        (70, 74),
        "capitals reach the upper line. Leave a box empty to skip it. Keep the 4 black squares visible.",
        fontsize=8.5, color=LABEL,
    )

    for i, ch in enumerate(L.CHARACTERS):
        x0, y0, x1, y1 = L.cell_rect(i)
        page.draw_rect(fitz.Rect(x0, y0, x1, y1), color=GUIDE, width=0.6)
        page.draw_line((x0, y0 + L.LABEL_H), (x1, y0 + L.LABEL_H), color=GUIDE, width=0.4)
        page.insert_text((x0 + 3, y0 + L.LABEL_H - 3), ch, fontsize=8, color=LABEL)
        for y in (L.cap_y(i), L.baseline_y(i)):
            page.draw_line((x0 + 4, y), (x1 - 4, y), color=GUIDE, width=0.5, dashes="[2 2] 0")

    doc.save(str(out_path))
    doc.close()
