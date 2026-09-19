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


# Tools that are created via click-drag (start point -> end point)
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
