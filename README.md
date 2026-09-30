# Aupedean Annotator

A Windows PDF annotation app for reviewing and marking up documents: notes,
highlights, freehand ink, shapes, stamps, signatures, measurements and page
editing. Annotations are written as standard PDF annotations with PyMuPDF,
so the files open correctly in any PDF viewer.

## Download

The latest packaged Windows build is on the
[Releases page](https://github.com/AubreyUndiPhiri/PDFAnnotatorFree/releases).
The most recent zip published there is
[PDFAnnotatorFree-v2.0.0-win64.zip](https://github.com/AubreyUndiPhiri/PDFAnnotatorFree/releases/download/v2.0.0/PDFAnnotatorFree-v2.0.0-win64.zip).
It predates the redesign; the current build is in `dist/AupedeanAnnotator/`.

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
  stroke. Two buttons next to the colour and width (also in the Tools menu,
  remembered between sessions):
  - **Smooth handwriting** (on by default, Pen and Marker): steadies the
    line while you draw and smooths the finished stroke, keeping its ends.
  - **Pressure sensitivity** (Pen): the stroke gets thicker and thinner
    along its length. With a pen tablet or a Windows stylus it follows how
    hard you press; with a mouse it follows speed (slow is thick, quick is
    thin) and tapers at the ends like real ink. Other PDF viewers show the
    same shape; the stroke is still an ordinary ink annotation.
  Strokes, lines, arrows and polygons can be dragged to a new place with
  Select.
- **Text** (T): the cursor becomes a text cursor. Click on the
  page and type straight into a dashed box that grows as you type, or drag
  first to set the box width so the text wraps. The font, size and colour in
  the toolbar apply live. Press Esc (or Ctrl+Enter) or click elsewhere on the
  page to finish; the box is then selected, ready to move or resize. Click an
  existing text box with the Text tool to edit it.
- **Formula** (Σ): click on the page and type LaTeX maths (`E = mc^2`,
  `\frac{a}{b}`, `\sqrt{x}`, `\sum_{i=1}^{n}`, `\begin{cases}...`; `$...$`
  delimiters are optional). The rendered formula previews under the box as
  you type; **Enter** places it on the page, **Shift+Enter** starts a new
  line (end lines with `\\`; `&` aligns them), **Esc** cancels. The size and
  colour in the toolbar apply. A placed formula is an annotation you can
  move and resize; double-click it (or click it with the Formula tool) to
  change its LaTeX. It is rendered with your LaTeX installation (MiKTeX or
  TeX Live) when there is one, so any amsmath works, and otherwise with the
  built-in matplotlib renderer (common maths, one line, no environments).
  The LaTeX source is kept in the PDF with the picture.
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

- **File**: New, New Word Document, New LaTeX Document, Open (PDF, .docx,
  .tex and text files; each file gets its own tab), Save / Save As / Save All
  / Save as Template, Combine Files, Split Every Page to Separate Files,
  Convert to Word, Convert to LaTeX,
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
  alignment lines), Hide Annotations, Page Thumbnails (F4; also the button
  at the top of the slim rail beside the thumbnails), Toolbars.
- **Window**: switch between open documents.

## Signatures

**Request Signature** (the button on the main toolbar, also in the File
menu) on a saved PDF: enter the signer's email, drag the "Sign here" box on
the page preview, and **Send for Signature**. That's it:

- The signer gets an email with a link. Only they can open it: when they
  do, a 6-digit code is emailed to them, and without it the document can't
  be seen or signed. They read the document in their browser (no app or
  account; phones work), tap **Sign here**, draw or type their signature,
  tick "I agree" and tap **Finish and send**. They can download a copy of
  what they signed.
- Aupedean notices by itself (it checks every minute while it's open),
  stamps the signature into your original with a line saying who signed,
  that their email was verified, and when, and saves it. If the PDF is in
  Google Drive, Google Drive for desktop syncs it. You also get a "Signed"
  email. *Signature Requests* lists everything you've sent.
- The document is encrypted on your PC (AES-256-GCM) before it's uploaded,
  and the key is only in the signer's emailed link, after the "#" (which
  browsers never send to servers), so what the service stores can't be read.
  The service deletes it once the signature is collected, or when the link
  expires.

This goes through **Aupedean Sign**, a small service that runs in *your own*
free Cloudflare account and sends email through *your own* free Brevo
account (300 emails a day; each request uses about three, so roughly 100
requests a day). No card is needed for either.

- **Using it** (everyone): File > Signature Account..., type your email,
  enter the code you're emailed. Done. Only the addresses the owner allowed
  can sign in.
- **Setting it up** (once, by whoever runs it): Signature Account... > Set
  Up a Service... Create a free Cloudflare account and an API token (Create
  Token > Create Custom Token with Account > Workers Scripts > Edit, Account
  > D1 > Edit and Account > Account Settings > Read), a free Brevo account
  and an API key (SMTP & API > API Keys), paste both, choose who may send
  requests (only you, your email's domain, a list, or anyone), and
  **Install**. Aupedean creates the database and installs the service; it
  gets an address like `https://aupedean-sign.<name>.workers.dev`. Give that
  address to anyone else who uses it. Copying it into
  `app/assets/sign_service.json` as `{"url": "..."}` builds it into the exe.
  The code is in `app/pdfannotator/cloud/sign_service/` (`worker.js`,
  `page.html`).

Without a service there's also the **signing file**: Request Signature >
Make a Signing File. Aupedean makes one web page with the document in it
(Documents > Aupedean Signing Files); send it yourself (*Copy File* and paste
it into WhatsApp or an email). The signer opens it in a browser, signs on the
document and sends the signed PDF back; open it in Aupedean, use *Add a
Signed Copy*, or save it to Downloads, and the signature goes into your
original. Nothing to set up, but it comes back by hand and the email isn't
verified. The signed PDF is made in the browser with pdf-lib (MIT licence,
`app/assets/js`).

## Google Drive and signing links

The **Google Drive** menu (code in `app/pdfannotator/cloud/`):

- **Syncing uses Google Drive for desktop**, Google's free app
  (https://www.google.com/drive/download/). Once it's installed and signed
  in, your Drive is on the PC as a "Google Drive" drive (usually G:), and
  Google syncs every save. Nothing needs setting up in Aupedean for this.
  - **Open from Google Drive** (Ctrl+Shift+O, also in the File menu): the
    usual Open dialog, starting in My Drive.
  - **Save to Google Drive**: Save As, starting in My Drive. Files already
    in Drive just save as normal.
  - The status bar says when the current file is in Google Drive. If Google
    Drive for desktop isn't installed or running, the menu says so and
    offers the download or starts it. Both streaming (G:) and mirror mode (a
    "My Drive" folder in your user folder) are found.
- **Google Account** is only needed for signing links: a one-time setup.
  Google needs a free OAuth client for this app: create a Google Cloud
  project, enable the Google Drive API and the Apps Script API, set up the
  consent screen (External, add yourself as a test user, and *Publish app*
  so the sign-in doesn't expire every 7 days), create an OAuth client ID of
  type *Desktop app* and choose its JSON file in the dialog (each step has a
  button that opens the right Google page). Then *Sign In with Google*.
  Google warns that the app isn't verified: expected for your own private
  app. The sign-in is stored encrypted for your Windows account (DPAPI) in
  `%APPDATA%\AupedeanAnnotator\google`.
- **Without Google Drive for desktop** (a submenu): browse Drive inside the
  app, upload a file, and sync it in the app instead (needs the Google
  account). Every save is uploaded as a new revision; while a file is open,
  Drive is checked every minute; if both changed you choose keep mine, use
  Google Drive's, or keep both; offline changes upload later.
- **Signing links**: *Set Up Signing Links* (once) creates two small Google
  Apps Script web apps in your own Google account: a *service* that runs as
  you and only uses the "Aupedean Signing" folder in your Drive, and the
  *page* signers open. Turn on "Google Apps Script API" in your Apps Script
  settings first, then approve the service once in the browser. After that,
  *Request Signature* (on a saved PDF): enter the signer's Google email, drag
  the signature box on the page preview, and share the link (copy it or send
  it with Gmail). The signer signs in with Google (so their email is
  verified), reads the pages, types their name, draws their signature and
  agrees. Within a couple of minutes the signature is stamped into the PDF
  with a line recording who signed, their Google email and the time, and
  the PDF is saved (and synced, if it's a Drive file). *Signature Requests*
  lists them, with copy link, cancel and check now. Links expire after the
  days you choose and work once.

## Word and LaTeX editors

Word documents and LaTeX sources open in tabs next to the PDFs. While one
is active the PDF tools are hidden, and Save, Undo, Cut/Copy/Paste, Find,
Print and zoom act on it.

**Word** (`word_editor.py`, `word_io.py`): opens and saves `.docx` (also
saves `.odt`, `.html`, `.md`, `.txt`; opens `.html`, `.md`, `.txt`). Styles
(Title, Heading 1-4, Quote, Code), fonts, sizes, bold / italic / underline /
strike-through, super- and subscript, text and highlight colour, alignment,
bulleted and numbered lists, indents, tables (right-click to add or delete
rows and columns, merge and split cells), pictures (insert, paste, resize),
links (Ctrl+click opens them), page breaks, find and replace (Ctrl+H), Print
and Export as PDF (which offers to open the PDF in a tab for annotating).
Saving builds on the file that was opened, so its styles, numbering,
headers, footers and page setup are kept. Pages are shown as one continuous
sheet; page breaks are marked with a dashed line. Not supported: old `.doc`
files, editing headers/footers, comments, tracked changes, text boxes.

**LaTeX** (`latex/editor.py`, `latex/engines.py`):
- Editor: highlighting in both themes, line numbers, `\begin{…}` gets its
  `\end{…}` on Enter, completion of commands, `\ref` labels and `\cite` keys
  (from `\bibitem` and the `.bib` files), Ctrl+/ comments lines, snippet
  menus (structure, formatting, lists, maths, Greek letters, figures, tables).
- Compile (F5 or Ctrl+Enter) with XeLaTeX, pdfLaTeX, LuaLaTeX, latexmk or
  Tectonic (found automatically in MiKTeX / TeX Live), or a custom command
  (LaTeX Settings; `{file}`, `{stem}` and `{dir}` are filled in). Errors and
  warnings are listed under the editor and marked in the margin; click one
  to jump to its line. `% !TEX root = main.tex` compiles the main file from
  a chapter file.
- Preview: the PDF appears beside the source. Double-click in it to jump to
  that line (SyncTeX); Ctrl+J shows the current line in the PDF.
- Open With: TeXworks, TeXstudio, VS Code (whichever are installed), the
  default program or any editor you choose; changes saved there reload here.
  "Package for Overleaf" zips the project for Overleaf's Upload Project.

## Look and feel

- Light mode mixes glassmorphism and claymorphism: frosted, see-through
  panels over a pastel gradient, shaped like soft clay (lit from the top,
  soft underside, real drop shadows under the floating bars, raised pill
  buttons, pressed and active controls sink in). Dark mode is claymorphism.
  Switch with View → Dark Mode (Ctrl+Shift+D) or the moon button.
- Colours, fonts and the style sheet are defined once in
  `app/pdfannotator/theme.py`; dialogs and painted overlays use the same
  values.
- Icons are SVG files in `app/assets/icons/`, drawn in currentColor and tinted
  at runtime (`app/pdfannotator/icons.py`). To change or add icons, edit
  `tools/make_icons.py` and run `python tools\make_icons.py`.
- The app icon is `app/assets/aupedean_annotator.svg`. After editing it,
  run `python tools\make_app_icon.py` to regenerate the Windows `.ico` used
  by the exe.

## Fonts (including your AUPedean handwriting font)

The toolbar **Font** box and **Tools > Tool Styles** list, in labelled sections:

1. **Your handwriting fonts** (such as AUPedean), from `app/assets/fonts/` and
   `%APPDATA%\AupedeanAnnotator\fonts\`.
2. **Helvetica, Times, Courier**: the standard PDF fonts, saved as plain
   text annotations.
3. **84 bundled font families (209 styles)** that ship with the app, so they
   work on every PC: handwriting and script (Caveat, Dancing Script, Great
   Vibes, Pacifico, Permanent Marker...), sans serif (Roboto, Open Sans, Lato,
   Montserrat, Poppins, Inter...), serif (Merriweather, Lora, Playfair
   Display, EB Garamond...), monospace (JetBrains Mono, Fira Code, Source
   Code Pro...) and display (Bebas Neue, Lobster, Abril Fatface...). They are
   open-licence Google Fonts; see `app/assets/fonts/library/README.md` for
   the full list and licences, and run `python toolsetch_fonts.py` to
   refresh them.
4. **Every font installed in Windows** that allows embedding (about 600 on a
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

### The AUPedean font

AUPedean ships with the app (`app/assets/fonts/AUPedean.ttf`) and is first in
every font list. It's the AUPedean script, a letter-for-letter transcription
of the English alphabet: typing `A` writes the AUPedean A, and so on.
Lowercase uses the same letters. The 26 letters are traced from the
handwritten original (`tools/font_builder/aupedean/source_photo.png`); see
`tools/font_builder/aupedean/letter_key.png` for which letter is which.
The script has no digits or punctuation, so those are simple strokes drawn
at the same pen weight.

To rebuild it (e.g. after changing the photo or the letter grouping):

```
venv\Scripts\python.exe tools\font_builder\aupedean\build_aupedean.py --preview tools\font_builder\aupedean\letter_key.png
```

To use AUPedean in other programs (Word and so on), double-click
`AUPedean.ttf` and choose **Install**.

### Creating a handwriting font from a glyph sheet

In the app: **Tools > Handwriting Font > Create Font from Your
Handwriting...**, or pick **Create AUPedean from your handwriting...** at the
top of the font list. The window walks you through it:

1. **Save Glyph Sheet...**, then print it at 100% (actual size).
2. Write one character per box in dark pen, sitting on the lower dashed line.
   Leave a box empty to skip it.
3. Scan it, or photograph it flat and in focus with all four black corner
   squares visible, then **Choose Scan or Photo...** and **Create Font**.

The font is saved to `%APPDATA%\AupedeanAnnotator\fonts\AUPedean.ttf` and
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
- Measure only reports straight-line distance (no perimeter, area or angle
  modes). Formulas are pictures (600 dpi) rather than vector text, so their
  maths can't be selected or searched in other PDF viewers.
- There are no separate bold, italic or underline buttons: PDF text
  annotations can't carry mixed styling. Pick a font's Bold or Italic style
  from the font list instead (for example "Arial Bold").

## Building a standalone Windows .exe

```
venv\Scripts\pyinstaller.exe --noconfirm AupedeanAnnotator.spec
```

The spec bundles `app/assets` (icons, fonts, app icon) and sets the exe icon.
The result is `dist\AupedeanAnnotator\AupedeanAnnotator.exe`, a folder you
can zip and share; no installation or licence needed.
