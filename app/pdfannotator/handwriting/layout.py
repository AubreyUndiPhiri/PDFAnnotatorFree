"""Glyph-sheet geometry shared by make_template.py and build_font.py.
All values are PDF points on an A4 portrait page (origin top-left)."""

PAGE_W, PAGE_H = 595.28, 841.89

# Solid black squares in the corners, used to straighten photos/scans
MARKER_SIZE = 26.0
MARKER_CENTERS = [  # top-left, top-right, bottom-right, bottom-left
    (36.0, 36.0),
    (PAGE_W - 36.0, 36.0),
    (PAGE_W - 36.0, PAGE_H - 36.0),
    (36.0, PAGE_H - 36.0),
]

CHARACTERS = (
    list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    + list("0123456789")
    + list(".,!?-':;()&/")
)
COLUMNS = 6
ROWS = -(-len(CHARACTERS) // COLUMNS)

GRID_LEFT, GRID_TOP = 58.0, 96.0
GRID_RIGHT, GRID_BOTTOM = PAGE_W - 58.0, PAGE_H - 70.0
CELL_W = (GRID_RIGHT - GRID_LEFT) / COLUMNS
CELL_H = (GRID_BOTTOM - GRID_TOP) / ROWS

LABEL_H = 13.0        # strip at the top of each cell holding the printed label
CAP_OFFSET = 14.0     # cap line, measured down from the bottom of the label strip
BASELINE_OFFSET = 56.0  # baseline, measured down from the bottom of the label strip
CROP_INSET = 3.0      # ignore this much next to the cell borders when reading ink


def cell_rect(index):
    """(x0, y0, x1, y1) of the whole cell for CHARACTERS[index]."""
    row, col = divmod(index, COLUMNS)
    x0 = GRID_LEFT + col * CELL_W
    y0 = GRID_TOP + row * CELL_H
    return x0, y0, x0 + CELL_W, y0 + CELL_H


def drawing_rect(index):
    """Area of the cell where the glyph is written (below the label strip)."""
    x0, y0, x1, y1 = cell_rect(index)
    return x0, y0 + LABEL_H, x1, y1


def cap_y(index):
    return drawing_rect(index)[1] + CAP_OFFSET


def baseline_y(index):
    return drawing_rect(index)[1] + BASELINE_OFFSET
