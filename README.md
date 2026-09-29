# Aupedian Annotators

A Windows PDF annotation app for reviewing and marking up documents: notes,
highlights, freehand ink, shapes, stamps, signatures, measurements and page
editing. Annotations are written as standard PDF annotations with PyMuPDF,
so the files open correctly in any PDF viewer.

## Download

The latest packaged Windows build is on the
[Releases page](https://github.com/AubreyUndiPhiri/PDFAnnotatorFree/releases).
The most recent zip published there is
[PDFAnnotatorFree-v2.0.0-win64.zip](https://github.com/AubreyUndiPhiri/PDFAnnotatorFree/releases/download/v2.0.0/PDFAnnotatorFree-v2.0.0-win64.zip).
It predates the redesign; the current build is in `dist/AupedianAnnotators/`.

## Run from source

```
python -m venv venv
venv\Scripts\pip.exe install -r requirements.txt
venv\Scripts\python.exe app\main.py
```

## Tests

```
venv\Scripts\python.exe tests\test_smoke.py
venv\Scripts\python.exe -m pytest tests
```

`test_smoke.py` drives the real widgets with simulated mouse drags, clicks and
keystrokes (every tool, multi-select, cut/copy/paste, flatten/remove
annotations, multi-tab isolation, print-to-PDF, save/reload) and needs no
display. The pytest suite adds checks for fonts and icons.

## Interface

- **Main toolbar**: open, save, print, undo/redo, insert image, draw
  signature, find, page thumbnails toggle; zoom (presets, fit width, fit
  page) and page navigation on the right.
- **Tools toolbar**: every tool as an icon (hover for its name, shortcut and
  how to use it), followed by the options for the active tool. It only shows
  what that tool uses: colour for all styled tools, width for pens and
  shapes, font and size for text, stamp type for stamps, unit for
  measurements.
- **Status bar**: what the active tool does, the tool name, the current page
  and the zoom level.
- **Page thumbnails** (left): click to jump, drag to reorder, right-click to
  rotate, insert, extract or delete a page.

## Tools

Single-letter shortcuts are shown in brackets.

- **Select** (U): click to select (Ctrl+click for more). A single selected
  text box, rectangle, ellipse or stamp shows 8 handles: drag the body to
  move it, drag a handle to resize it, use the arrow keys to nudge it (Shift
  for 10 pt steps), `Delete`/`Backspace` to remove it. Double-click a text
  box to edit it in place, or a note to edit its comment.
- **Extract Text** (X): drag across text to copy it to the clipboard.
- **Pan** (N): drag to scroll.
- **Zoom** (Z): left-click to zoom in, right-click to zoom out, centred on the
  cursor.
- **Highlight / Underline / Strikeout**: drag across a line of text.
- **Note**: click to add a sticky-note comment.
- **Pen** (P): freehand drawing. **Marker** (M): translucent highlighter
  stroke.
- **Text** (T) / **Formula**: the cursor becomes a text cursor. Click on the
  page and type straight into a dashed box that grows as you type, or drag
  first to set the box width so the text wraps. The font, size and colour in
  the toolbar apply live. Press Esc (or Ctrl+Enter) or click elsewhere on the
  page to finish; the box is then selected, ready to move or resize. Click an
  existing text box with the Text tool to edit it. Formula text is plain
  text, not rendered LaTeX.
- **Stamp** (A): named stamps (Approved, Draft, Confidential, ...).
- **Line** (L) / **Arrow** (W) / **Rectangle** (R) / **Ellipse** (I): drag to
  draw.
- **Polygon** (G): click to add points, double-click or Enter to finish,
  Escape to cancel.
- **Dimension** (D): drag to measure and permanently label a distance.
- **Eraser** (E): drag over annotations to delete them.
- **Lasso Select** (S): draw a loop to select everything inside it.
- **Snapshot** (H): drag a region to copy it to the clipboard as an image.
- **Crop** (C): drag a region to crop the page to it.
- **Measure** (B): live distance readout while dragging; nothing is added.
- **Laser Pointer** (O) / **Pointer** (V): presentation aids that never
  change the PDF.
- **Insert Image / Draw Signature**: place a logo or a hand-drawn signature.
- Right-click any tool button to pin it under **Tools > Favorites**.
  **Tools > Tool Styles** edits every tool's default colour, width, opacity,
  font and font size in one table.

## Menus

- **File**: New, Open (each file gets its own tab), Save / Save As / Save All
  / Save as Template, Combine Files, Split Every Page to Separate Files,
  Properties (metadata), Send Mail (opens your mail client; attach the file
  yourself, Windows can't do it automatically), Print, Close / Close All,
  Exit.
- **Edit**: Undo/Redo, Cut/Copy/Paste/Paste Without Formatting (works across
  tabs), Delete, Find (Ctrl+F, wraps across pages; Enter for next,
  Shift+Enter for previous), Insert Image, Draw Signature, Selection, Page
  (insert, delete, rotate, extract the current page), Flatten All
  Annotations (burns them into the page with PyMuPDF's `bake()`), Remove All
  Annotations.
- **Tools**: every tool, Handwriting Font (create a font from your handwriting, save the glyph sheet), Favorites, Tool Styles.
- **View**: zoom, Actual Size / Fit Page / Fit Width, Full Screen, Page
  Layout (Single Page / Continuous), Go To, Guides (draggable non-printing
  alignment lines), Hide Annotations, Page Thumbnails (F4), Toolbars.
- **Window**: switch between open documents.

## Look and feel

- Colours, fonts and the style sheet are defined once in
  `app/pdfannotator/theme.py`; dialogs and painted overlays use the same
  values.
- Icons are SVG files in `app/assets/icons/`, drawn in currentColor and tinted
  at runtime (`app/pdfannotator/icons.py`). To change or add icons, edit
  `tools/make_icons.py` and run `python tools\make_icons.py`.
- The app icon is `app/assets/aupedian_annotators.svg`. After editing it,
  run `python tools\make_app_icon.py` to regenerate the Windows `.ico` used
  by the exe.

## Fonts (including your AUPedean handwriting font)

The toolbar **Font** box and **Tools > Tool Styles** list, in this order:

1. **Your handwriting fonts** (such as AUPedean), from `app/assets/fonts/` and
   `%APPDATA%\AupedianAnnotators\fonts\`.
2. **Helvetica, Times, Courier**: the standard PDF fonts, saved as plain
   text annotations.
3. **Every font installed in Windows** that allows embedding (about 600 on a
   typical PC, including the Bold and Italic styles, and handwriting-style
   fonts such as Ink Free, Segoe Print and Segoe Script). Fonts whose licence
   forbids embedding, symbol fonts and very large fonts (over 15 MB) are left
   out.

Each font is previewed in its own typeface, and you can type in the box to
search (for example "bold" or "script"). Every font other than the standard
three is embedded in the PDF, cut down to just the characters each text box
uses, so the text looks the same in any PDF viewer and files stay small. The
text can still be moved, resized and edited in this app. Other PDF editors
that rewrite a box may fall back to Helvetica.

### Creating AUPedean from your handwriting

In the app: **Tools > Handwriting Font > Create Font from Your
Handwriting...**, or pick **Create AUPedean from your handwriting...** at the
top of the font list. The window walks you through it:

1. **Save Glyph Sheet...**, then print it at 100% (actual size).
2. Write one character per box in dark pen, sitting on the lower dashed line.
   Leave a box empty to skip it.
3. Scan it, or photograph it flat and in focus with all four black corner
   squares visible, then **Choose Scan or Photo...** and **Create Font**.

The font is saved to `%APPDATA%\AupedianAnnotators\fonts\AUPedean.ttf` and
appears in every font list straight away, with no restart or rebuild. Doing
it again with the same name replaces it. Lowercase letters reuse the
capitals. A copy of the sheet is also at
`tools/font_builder/AUPedean_glyph_sheet.pdf`.

The same builder works from the command line:

```
venv\Scripts\python.exe tools\font_builder\build_font.py path\to\scan.jpg [--debug out_dir]
```

`--debug` saves the straightened sheet and each character's ink mask, to
check what went wrong with a difficult photo. Use `--out app\assets\fonts\AUPedean.ttf`
to ship the font inside the app and exe for everyone.

## Known gaps / manual-only

A few things can't be meaningfully checked by the automated tests:

- The laser pointer glow and dragging guides are visual only; check them by
  eye.
- Send Mail opens a real mail client, which can't be automated.
- Formula produces plain text (not a rendered LaTeX equation), and Measure
  only reports straight-line distance (no perimeter, area or angle modes).
- There are no separate bold, italic or underline buttons: PDF text
  annotations can't carry mixed styling. Pick a font's Bold or Italic style
  from the font list instead (for example "Arial Bold").

## Building a standalone Windows .exe

```
venv\Scripts\pyinstaller.exe --noconfirm PDFAnnotatorFree.spec
```

The spec bundles `app/assets` (icons, fonts, app icon) and sets the exe icon.
The result is `dist\AupedianAnnotators\AupedianAnnotators.exe`, a folder you
can zip and share; no installation or licence needed.
