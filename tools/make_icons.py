"""Regenerate app/assets/icons/*.svg (the toolbar/menu icon set).

    python tools/make_icons.py

Icons are 24x24, 2px round strokes in currentColor; the app tints them at
runtime (see app/pdfannotator/icons.py). Many shapes follow the Lucide icon
set (ISC licence, see app/assets/icons/LICENSE).
"""
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "app" / "assets" / "icons"

HEAD = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
    'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
)

ICONS = {
    # ---- tools
    "select": '<path d="M5 3l14 7.5-6.2 1.6L9.6 18z"/>',
    "extract-text": '<path d="M4 7V5h10v2"/><path d="M9 5v12"/><path d="M7 17h4"/><path d="M16 11h5"/><path d="M16 15h5"/><path d="M16 19h5"/>',
    "pan": '<path d="M18 11V6a2 2 0 0 0-4 0v5"/><path d="M14 10V4a2 2 0 0 0-4 0v6"/><path d="M10 10.5V6a2 2 0 0 0-4 0v8"/><path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15"/>',
    "zoom": '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',
    "highlight": '<path d="M9 11l-6 6v3h9l3-3"/><path d="M22 12l-4.6 4.6a2 2 0 0 1-2.8 0l-5.2-5.2a2 2 0 0 1 0-2.8L14 4"/>',
    "underline": '<path d="M6 4v6a6 6 0 0 0 12 0V4"/><path d="M4 20h16"/>',
    "strikeout": '<path d="M16 4H9a3 3 0 0 0-2.83 4"/><path d="M14 12a4 4 0 0 1 0 8H6"/><path d="M4 12h16"/>',
    "note": '<path d="M16 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8z"/><path d="M15 3v4a2 2 0 0 0 2 2h4"/><path d="M7 13h7"/><path d="M7 17h5"/>',
    "pen": '<path d="M12 20h9"/><path d="M16.4 3.6a2.1 2.1 0 1 1 3 3L7.4 18.6 3 20l1.4-4.4z"/>',
    "marker": '<path d="M15.5 3.5l4 4L10 17H6v-4z"/><path d="M13 6l4 4"/><path d="M3 21h18"/>',
    "textbox": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M8 8h8"/><path d="M12 8v9"/>',
    "formula": '<path d="M18 6V4H6l6 8-6 8h12v-2"/>',
    "stamp": '<path d="M5 22h14"/><path d="M19.27 13.73A2.5 2.5 0 0 0 17.5 13h-11A2.5 2.5 0 0 0 4 15.5V17a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-1.5c0-.66-.26-1.3-.73-1.77"/><path d="M14 13V8.5C14 7 15 7 15 5a3 3 0 0 0-6 0c0 2 1 2 1 3.5V13"/>',
    "line": '<path d="M5 19L19 5"/>',
    "arrow": '<path d="M5 19L19 5"/><path d="M9 5h10v10"/>',
    "rect": '<rect x="3" y="5" width="18" height="14" rx="2"/>',
    "ellipse": '<ellipse cx="12" cy="12" rx="9" ry="7"/>',
    "polygon": '<path d="M12 3l9 6.5-3.5 10.5h-11L3 9.5z"/>',
    "dimension": '<path d="M3 7v10"/><path d="M21 7v10"/><path d="M6 12h12"/><path d="M9 9l-3 3 3 3"/><path d="M15 9l3 3-3 3"/>',
    "eraser": '<path d="M7 21l-4.3-4.3a1 1 0 0 1 0-1.4l10-10a1 1 0 0 1 1.4 0l5.6 5.6a1 1 0 0 1 0 1.4L11 21"/><path d="M22 21H7"/><path d="M5 11l9 9"/>',
    "lasso": '<path d="M7 22a5 5 0 0 1-2-4"/><path d="M3.3 14A6.8 6.8 0 0 1 2 10c0-4.4 4.5-8 10-8s10 3.6 10 8-4.5 8-10 8a12 12 0 0 1-5-1"/><circle cx="5" cy="16" r="2"/>',
    "snapshot": '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3z"/><circle cx="12" cy="13" r="3"/>',
    "crop": '<path d="M6 2v14a2 2 0 0 0 2 2h14"/><path d="M18 22V8a2 2 0 0 0-2-2H2"/>',
    "measure": '<path d="M21.3 15.3a2.4 2.4 0 0 1 0 3.4l-2.6 2.6a2.4 2.4 0 0 1-3.4 0L2.7 8.7a2.41 2.41 0 0 1 0-3.4l2.6-2.6a2.41 2.41 0 0 1 3.4 0z"/><path d="M14.5 12.5l2-2"/><path d="M11.5 9.5l2-2"/><path d="M8.5 6.5l2-2"/><path d="M17.5 15.5l2-2"/>',
    "laser": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3" fill="currentColor"/>',
    "pointer": '<path d="M9 9l5 12 1.8-5.2L21 14z"/><path d="M7.2 2.2L8 5.1"/><path d="M5.1 8L2.2 7.2"/><path d="M14 4.1L12 6"/><path d="M6 12l-1.9 2"/>',
    "image": '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/>',
    "signature": '<path d="M3 16c3-1 4.5-9 7-9 2 0-1 9 1.5 9 1.6 0 2.5-4 4-4 1.2 0 .8 3 2.5 3 1 0 2-.7 3-1.5"/><path d="M3 21h18"/>',
    # ---- file / edit / view
    "file-new": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M12 12v6"/><path d="M9 15h6"/>',
    "open": '<path d="M6 14l1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"/>',
    "save": '<path d="M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7"/><path d="M7 3v4a1 1 0 0 0 1 1h7"/>',
    "save-all": '<path d="M10 2v3a1 1 0 0 0 1 1h5"/><path d="M18 18v-6a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6"/><path d="M18 22H4a2 2 0 0 1-2-2V6"/><path d="M8 18a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9.17a2 2 0 0 1 1.41.59l2.83 2.83A2 2 0 0 1 22 6.83V16a2 2 0 0 1-2 2z"/>',
    "print": '<path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><path d="M6 9V3a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v6"/><rect x="6" y="14" width="12" height="8" rx="1"/>',
    "undo": '<path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    "redo": '<path d="M15 14l5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
    "cut": '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4L8.12 15.88"/><path d="M14.47 14.48L20 20"/><path d="M8.12 8.12L12 12"/>',
    "copy": '<rect x="8" y="8" width="14" height="14" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
    "paste": '<rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>',
    "delete": '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><path d="M10 11v6"/><path d="M14 11v6"/>',
    "find": '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',
    "zoom-in": '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/><path d="M11 8v6"/><path d="M8 11h6"/>',
    "zoom-out": '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/><path d="M8 11h6"/>',
    "fit-width": '<path d="M3 5v14"/><path d="M21 5v14"/><path d="M7 12h10"/><path d="M10 9l-3 3 3 3"/><path d="M14 9l3 3-3 3"/>',
    "fit-page": '<path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/><path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/><rect x="8" y="7" width="8" height="10" rx="1"/>',
    "actual-size": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 9l2-1v8"/><path d="M12 11v.01"/><path d="M12 15v.01"/><path d="M15 9l2-1v8"/>',
    "chevron-left": '<path d="M15 18l-6-6 6-6"/>',
    "chevron-right": '<path d="M9 18l6-6-6-6"/>',
    "chevron-down": '<path d="M6 9l6 6 6-6"/>',
    "chevron-up": '<path d="M18 15l-6-6-6 6"/>',
    "first-page": '<path d="M11 17l-5-5 5-5"/><path d="M18 17l-5-5 5-5"/>',
    "last-page": '<path d="M6 17l5-5-5-5"/><path d="M13 17l5-5-5-5"/>',
    "close": '<path d="M18 6L6 18"/><path d="M6 6l12 12"/>',
    "sidebar": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18"/>',
    "fullscreen": '<path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/><path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/>',
    "properties": '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    "mail": '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M22 7l-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>',
    "combine": '<path d="M8 3h8"/><path d="M12 3v7"/><path d="M9 8l3 3 3-3"/><rect x="4" y="14" width="16" height="7" rx="2"/>',
    "split": '<rect x="3" y="3" width="7" height="18" rx="1.5"/><rect x="14" y="3" width="7" height="18" rx="1.5"/>',
    "rotate-left": '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>',
    "rotate-right": '<path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>',
    "page-add": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M12 12v6"/><path d="M9 15h6"/>',
    "page-extract": '<path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M4 7V4a2 2 0 0 1 2-2h9l5 5v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2"/><path d="M2 15h10"/><path d="M9 18l3-3-3-3"/>',
    "page-delete": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M9 15h6"/>',
    "layers": '<path d="M12 2l10 5-10 5L2 7z"/><path d="M2 12l10 5 10-5"/><path d="M2 17l10 5 10-5"/>',
    "clear-all": '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><path d="M9.5 11.5l5 5"/><path d="M14.5 11.5l-5 5"/>',
    "eye-off": '<path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"/><path d="M6.61 6.61A13.53 13.53 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/><path d="M2 2l20 20"/>',
    "guides": '<path d="M3 9h18"/><path d="M9 3v18"/><path d="M3 15h18" stroke-dasharray="2 3"/><path d="M15 3v18" stroke-dasharray="2 3"/>',
    "star": '<path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01z"/>',
    "settings": '<path d="M4 21v-7"/><path d="M4 10V3"/><path d="M12 21v-9"/><path d="M12 8V3"/><path d="M20 21v-5"/><path d="M20 12V3"/><path d="M1 14h6"/><path d="M9 8h6"/><path d="M17 16h6"/>',
    "bold": '<path d="M6 12h9a4 4 0 0 1 0 8H7a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h7a4 4 0 0 1 0 8"/>',
    "italic": '<path d="M19 4h-9"/><path d="M14 20H5"/><path d="M15 4L9 20"/>',
    "text-underline": '<path d="M6 4v6a6 6 0 0 0 12 0V4"/><path d="M4 20h16"/>',
    "align-left": '<path d="M15 12H3"/><path d="M17 18H3"/><path d="M21 6H3"/>',
    "align-center": '<path d="M17 12H7"/><path d="M19 18H5"/><path d="M21 6H3"/>',
    "align-right": '<path d="M21 12H9"/><path d="M21 18H7"/><path d="M21 6H3"/>',
    "exit": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/>',
    "check": '<path d="M20 6L9 17l-5-5"/>',
    "select-all": '<path d="M5 3a2 2 0 0 0-2 2"/><path d="M19 3a2 2 0 0 1 2 2"/><path d="M21 19a2 2 0 0 1-2 2"/><path d="M5 21a2 2 0 0 1-2-2"/><path d="M9 3h1"/><path d="M9 21h1"/><path d="M14 3h1"/><path d="M14 21h1"/><path d="M3 9v1"/><path d="M21 9v1"/><path d="M3 14v1"/><path d="M21 14v1"/>',
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, body in ICONS.items():
        (OUT / f"{name}.svg").write_text(HEAD + body + "</svg>\n", encoding="utf-8")
    print(f"Wrote {len(ICONS)} icons to {OUT}")


if __name__ == "__main__":
    main()
