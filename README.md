# PDF Annotator Free

A free, open Windows desktop PDF annotator built to match the full UI of the
commercial "PDF Annotator" app — multi-document tabs, the full tool palette,
and File/Edit/Tool/View menu parity. All edits are written as standard PDF
annotations via PyMuPDF, so the resulting files open correctly in any PDF
viewer.

## Run from source

```
venv\Scripts\python.exe app\main.py

https://github.com/AubreyUndiPhiri/PDFAnnotatorFree/releases/download/v2.0.0/PDFAnnotatorFree-v2.0.0-win64.zip
```

## Run the automated smoke test

Simulates real mouse drags/clicks/keystrokes against the actual widgets (every
tool, multi-select, cut/copy/paste, melt/remove annotations, multi-tab
isolation, print-to-PDF, save/reload), with no display required:

```
venv\Scripts\python.exe tests\test_smoke.py
```

## Features

**Tabs**: multiple PDFs open at once (File > New Document / Open), each with
its own undo history, zoom, and page layout; Window menu lists open tabs.

**Tools** (toolbar buttons + single-letter shortcuts):
- **Select** (U) — click to select (Ctrl+click to multi-select), drag to
  move, `Delete`/`Backspace` to remove, double-click text/notes to edit.
- **Extract Text** (X) — drag across text to copy it to the clipboard.
- **Pan** (N) — drag to scroll.
- **Zoom** (Z) — left-click to zoom in, right-click to zoom out, centered on
  the cursor.
- **Highlight / Underline / Strikeout** — drag across a line of text.
- **Note** — click to drop a sticky-note comment.
- **Pen** (P) — freehand drawing. **Marker** (M) — translucent highlighter
  stroke.
- **Text** (T) / **Formula** — drag to size a box, then type (Formula labels
  the prompt for equations; both currently produce a plain text annotation).
- **Stamp** (A) — named stamps (Approved, Draft, Confidential, ...).
- **Line** (L) / **Arrow** (W) / **Rectangle** (R) / **Ellipse** (I) —
  drag to draw.
- **Polygon** (G) — click to add points, double-click or Enter to finish,
  Escape to cancel.
- **Dimension** (D) — drag to measure and permanently label a distance.
- **Eraser** (E) — drag over annotations to delete them.
- **Lasso Select** (S) — drag a freehand loop to multi-select everything
  inside it, then move or delete them together.
- **Snapshot** (H) — drag a region to copy it to the clipboard as an image.
- **Crop** (C) — drag a region to crop the page to it.
- **Measure** (B) — live distance readout while dragging; nothing is added
  to the page.
- **Laser Pointer** (O) / **Pointer** (V) — presentation aids; Laser Pointer
  shows a glowing dot, Pointer is a read-only inspector. Neither touches the
  PDF.
- **Insert Image... / Draw Signature...** — place a logo or a mouse-drawn
  signature.
- Right-click any tool button to pin/unpin it under **Tool > Favorites**.
  **Tool > Tool Styles...** edits every tool's remembered color/width/
  opacity/font size in one table.

**Edit menu**: Undo/Redo, Cut/Copy/Paste/Paste Without Formatting (works
across tabs), Delete, Find (Ctrl+F, wraps across pages), Insert Image,
Selection (Select All on Page / Deselect / Invert), Page (insert/delete/
rotate/extract), Document (combine/split/properties), Melt All Annotations
(permanently flattens annotations into the page via PyMuPDF's `bake()`),
Remove All Annotations.

**View menu**: Zoom (editable presets), Full Screen / Full Screen in Window,
Actual Size / Fit to Size / Fit to Width, Page Layout (Single Page /
Continuous), Auxiliary Lines (draggable non-printing guides), Go to
(first/prev/next/last/page number), Hide Annotations, Sidebar toggle,
Toolbars toggle.

**File menu**: New Document, Combine Files (merge PDFs in), Open (each file
becomes its own tab), Save/Save As/Save as Template/Save All, Close/Close
All, Properties (metadata editor), Send Mail (opens your mail client — it
cannot auto-attach the file on Windows, so attach it manually), Print
(via Qt's print pipeline), Split Every Page to Separate Files, Exit.

**Page panel** (left sidebar): drag thumbnails to reorder pages; right-click
for rotate / insert / extract / delete.

## Known gaps / manual-only

A few things can't be meaningfully exercised by the automated test and are
noted here instead:
- Laser Pointer's glow and Auxiliary Lines dragging are visual-only — check
  them by eye.
- Send Mail opens a real mail client, which isn't something to automate.
- Formula is currently a plain-text annotation (not a rendered LaTeX
  equation), and Measure only reports straight-line distance (no
  perimeter/area/angle modes yet).

## Building a standalone Windows .exe

```
venv\Scripts\pyinstaller.exe --noconfirm --windowed --name "PDFAnnotatorFree" ^
    --paths app app\main.py
```

The build output will be in `dist\PDFAnnotatorFree\PDFAnnotatorFree.exe`
(a folder you can zip and share — no installation or license required).

For a single-file .exe instead (slower to start, one file to distribute),
add `--onefile` to the command above.
