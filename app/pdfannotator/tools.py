from enum import Enum, auto


class Tool(Enum):
    SELECT = auto()
    HIGHLIGHT = auto()
    UNDERLINE = auto()
    STRIKEOUT = auto()
    NOTE = auto()
    INK = auto()
    RECT = auto()
    ELLIPSE = auto()
    LINE = auto()
    ARROW = auto()
    TEXTBOX = auto()
    STAMP = auto()
    IMAGE_STAMP = auto()
    # Extended tool set (parity with the reference app's Tool menu)
    EXTRACT_TEXT = auto()
    PAN = auto()
    ZOOM = auto()
    MARKER = auto()
    POLYGON = auto()
    DIMENSION = auto()
    ERASER = auto()
    LASSO = auto()
    SNAPSHOT = auto()
    CROP = auto()
    MEASURE = auto()
    FORMULA = auto()
    LASER_POINTER = auto()
    POINTER = auto()


# Tools that are created via a single click-drag (start point -> end point)
DRAG_TOOLS = {
    Tool.HIGHLIGHT,
    Tool.UNDERLINE,
    Tool.STRIKEOUT,
    Tool.INK,
    Tool.RECT,
    Tool.ELLIPSE,
    Tool.LINE,
    Tool.ARROW,
    Tool.TEXTBOX,
    Tool.STAMP,
    Tool.IMAGE_STAMP,
    Tool.MARKER,
    Tool.DIMENSION,
    Tool.ERASER,
    Tool.LASSO,
    Tool.SNAPSHOT,
    Tool.CROP,
    Tool.MEASURE,
    Tool.EXTRACT_TEXT,
    Tool.FORMULA,
}

# Tools that never mutate the PDF (view/inspection/clipboard helpers only)
NON_MUTATING_TOOLS = {
    Tool.PAN,
    Tool.ZOOM,
    Tool.MEASURE,
    Tool.EXTRACT_TEXT,
    Tool.SNAPSHOT,
    Tool.LASER_POINTER,
    Tool.POINTER,
}

# Tools that get their own remembered style (color/width/fontsize/opacity)
STYLED_TOOLS = [
    Tool.HIGHLIGHT, Tool.UNDERLINE, Tool.STRIKEOUT, Tool.NOTE, Tool.INK,
    Tool.RECT, Tool.ELLIPSE, Tool.LINE, Tool.ARROW, Tool.TEXTBOX,
    Tool.MARKER, Tool.POLYGON, Tool.DIMENSION, Tool.FORMULA,
]

DEFAULT_TOOL_STYLE = {"color": (255, 210, 0), "width": 2.0, "fontsize": 12, "opacity": 1.0}

# Per-tool style overrides seeded on top of DEFAULT_TOOL_STYLE
TOOL_STYLE_OVERRIDES = {
    Tool.MARKER: {"color": (255, 235, 59), "width": 10.0, "opacity": 0.35},
    Tool.INK: {"color": (20, 20, 20), "width": 2.0},
    Tool.HIGHLIGHT: {"color": (255, 235, 59)},
    Tool.UNDERLINE: {"color": (220, 30, 30)},
    Tool.STRIKEOUT: {"color": (220, 30, 30)},
    Tool.DIMENSION: {"color": (0, 90, 200), "width": 1.5},
}

# Named stamp icons supported natively by PyMuPDF's add_stamp_annot
STAMP_NAMES = [
    "Approved",
    "AsIs",
    "Confidential",
    "Departmental",
    "Draft",
    "Experimental",
    "Expired",
    "Final",
    "ForComment",
    "ForPublicRelease",
    "NotApproved",
    "NotForPublicRelease",
    "Rejected",
    "Sold",
    "TopSecret",
    "Void",
]

# Units available for the Dimension / Measure tools (name, points-per-unit)
UNITS = [
    ("in", 72.0),
    ("cm", 72.0 / 2.54),
    ("mm", 72.0 / 25.4),
    ("pt", 1.0),
]

TOOL_SHORTCUTS = {
    Tool.SELECT: "U",
    Tool.EXTRACT_TEXT: "X",
    Tool.PAN: "N",
    Tool.ZOOM: "Z",
    Tool.INK: "P",
    Tool.MARKER: "M",
    Tool.TEXTBOX: "T",
    Tool.STAMP: "A",
    Tool.LINE: "L",
    Tool.ARROW: "W",
    Tool.RECT: "R",
    Tool.ELLIPSE: "I",
    Tool.POLYGON: "G",
    Tool.DIMENSION: "D",
    Tool.ERASER: "E",
    Tool.LASSO: "S",
    Tool.SNAPSHOT: "H",
    Tool.CROP: "C",
    Tool.MEASURE: "B",
    Tool.LASER_POINTER: "O",
    Tool.POINTER: "V",
}

TOOL_LABELS = {
    Tool.SELECT: "Select",
    Tool.EXTRACT_TEXT: "Extract Text",
    Tool.PAN: "Pan",
    Tool.ZOOM: "Zoom",
    Tool.INK: "Pen",
    Tool.MARKER: "Marker",
    Tool.TEXTBOX: "Text",
    Tool.STAMP: "Stamp",
    Tool.LINE: "Line",
    Tool.ARROW: "Arrow",
    Tool.RECT: "Rectangle",
    Tool.ELLIPSE: "Ellipse",
    Tool.POLYGON: "Polygon",
    Tool.DIMENSION: "Dimension",
    Tool.ERASER: "Eraser",
    Tool.LASSO: "Lasso Select",
    Tool.SNAPSHOT: "Snapshot",
    Tool.CROP: "Crop",
    Tool.MEASURE: "Measure",
    Tool.FORMULA: "Formula",
    Tool.LASER_POINTER: "Laser Pointer",
    Tool.POINTER: "Pointer",
    Tool.HIGHLIGHT: "Highlight",
    Tool.UNDERLINE: "Underline",
    Tool.STRIKEOUT: "Strikeout",
    Tool.NOTE: "Note",
}

TOOL_HINTS = {
    Tool.SELECT: "Click an annotation to select it (Ctrl+click to multi-select). Drag to move, Delete to remove, double-click text to edit.",
    Tool.HIGHLIGHT: "Drag across text to highlight it.",
    Tool.UNDERLINE: "Drag across text to underline it.",
    Tool.STRIKEOUT: "Drag across text to strike it out.",
    Tool.NOTE: "Click anywhere to add a sticky note.",
    Tool.INK: "Drag to draw freehand.",
    Tool.MARKER: "Drag to draw a translucent highlighter stroke.",
    Tool.RECT: "Drag to draw a rectangle.",
    Tool.ELLIPSE: "Drag to draw an ellipse.",
    Tool.LINE: "Drag to draw a line.",
    Tool.ARROW: "Drag to draw an arrow.",
    Tool.TEXTBOX: "Drag to size a text box, then type your text.",
    Tool.FORMULA: "Drag to size a box, then type a formula/equation.",
    Tool.STAMP: "Drag to place the selected stamp.",
    Tool.IMAGE_STAMP: "Drag to place the chosen image.",
    Tool.EXTRACT_TEXT: "Drag across text to copy it to the clipboard.",
    Tool.PAN: "Drag to scroll the page.",
    Tool.ZOOM: "Left-click to zoom in, right-click to zoom out.",
    Tool.POLYGON: "Click to add points, double-click or Enter to finish, Escape to cancel.",
    Tool.DIMENSION: "Drag to measure and label a distance.",
    Tool.ERASER: "Drag over annotations to erase them.",
    Tool.LASSO: "Drag a freehand loop to select everything inside it.",
    Tool.SNAPSHOT: "Drag a region to copy it to the clipboard as an image.",
    Tool.CROP: "Drag a region to crop the page to it.",
    Tool.MEASURE: "Drag to see a live distance readout (nothing is added to the page).",
    Tool.LASER_POINTER: "Move the mouse to point at the page (nothing is saved).",
    Tool.POINTER: "Click an annotation to inspect it (read-only).",
}
