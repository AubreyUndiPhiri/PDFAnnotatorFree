# PDF Annotator Free

A free, open Windows desktop PDF annotator — highlight, underline, strikeout,
sticky notes, freehand drawing, shapes, arrows, typed text boxes, stamps,
drawn/image signatures, and page tools (merge, split, reorder, rotate,
insert, delete, extract). All edits are written as standard PDF annotations
via PyMuPDF, so the resulting files open correctly in any PDF viewer.

## Run from source

```
venv\Scripts\python.exe app\main.py
```

## Run the automated smoke test

Simulates real mouse drags (highlight, ink, shapes, select/move/delete,
undo/redo, page rotate/insert/delete, save) against the actual widgets, with
no display required:

```
venv\Scripts\python.exe tests\test_smoke.py
```

## Features

- **Select tool**: click an annotation to select it, drag to move it,
  `Delete`/`Backspace` to remove it, double-click text/notes to edit them.
- **Highlight / Underline / Strikeout**: drag across a line of text.
- **Note**: click to drop a sticky-note comment.
- **Pen**: freehand drawing.
- **Rect / Ellipse / Line / Arrow**: drag to draw.
- **Text Box**: drag to size, then type.
- **Stamp**: choose a named stamp (Approved, Draft, Confidential, ...) and
  drag to place it.
- **Insert Image...**: place a logo/scanned signature image on the page.
- **Draw Signature...**: draw a signature with the mouse and place it.
- **Page panel** (left sidebar): drag thumbnails to reorder pages;
  right-click for rotate / insert / extract / delete.
- **File menu**: New, Open, Save, Save As, Merge PDFs in, Split every page
  to separate files.
- **Undo/Redo**: full history across all edits (Ctrl+Z / Ctrl+Y).

## Building a standalone Windows .exe

```
venv\Scripts\pyinstaller.exe --noconfirm --windowed --name "PDFAnnotatorFree" ^
    --paths app app\main.py
```

The build output will be in `dist\PDFAnnotatorFree\PDFAnnotatorFree.exe`
(a folder you can zip and share — no installation or license required).

For a single-file .exe instead (slower to start, one file to distribute),
add `--onefile` to the command above.
