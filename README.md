# AUPedean Annotator

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
  existing text box with the Text tool to edit it. The list button offers
  filled, hollow and square bullets and numbering as 1. / 1) / (1), a. /
  a), A., i. and I.
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
- **Eraser** (E): two erasers, picked from the arrow on its button (or the
  Tools menu; Shift+E switches between them). The **Eraser** works like a
  pencil eraser: its round tip (Small, Medium, Large or Extra Large, or any
  size in the toolbar) rubs out only the parts of pen and marker strokes it
  passes over, as you drag, cutting them cleanly at its edge; shapes, notes
  and text boxes it touches are removed. The **Stroke Eraser** draws a line
  as you drag; when you let go, every annotation the line crossed is
  removed. One drag is one undo.
- **Lasso Select** (S): draw a loop to select everything inside it.
- **Snapshot** (H): drag a region to copy it to the clipboard as an image.
- **Crop** (C): drag a region to crop the page to it.
- **Measure** (B): live distance readout while dragging; nothing is added.
- **Geometry tools** (the arrow on the Measure button, or Tools > Geometry
  Tools): a **Ruler** (30 cm / 12 in), **Set Squares** (45° and 30°/60°), a
  **Protractor** and a **Compass**, frosted like acrylic and drawn to the
  page's own scale: a centimetre on the ruler is a centimetre on the page at
  every zoom (View > **Zoom to Real Size** makes it a real centimetre on
  your screen too). They lie on the paper: drag to move, turn by the round
  knob or the mouse wheel (Shift: 15° steps; they click into 0°, 45°, 90°),
  double-click to straighten, right-click for more; centimetres or inches.
  Start a Pen or Marker stroke along a straight edge (or the protractor's
  curve) and the line follows it exactly, with its length shown as you
  draw. The protractor's two arms measure an angle. The compass: drag the
  needle to place it, the pencil to set the radius (or turn the wheel), the
  knob on top to draw an arc in the pen's colour; double-click the knob for
  a full circle.
- **Calculator** (Ctrl+Alt+K, the calculator button): a scientific
  calculator at the side: trigonometry in degrees or radians and the
  inverses (2nd), hyperbolic functions, logs, powers and roots, factorials,
  nCr / nPr, mod, %, π and e, EXP, Ans, memory (MC MR M+ M−), a live result
  as you type, a history to click back, and the keyboard.
- **Clock and Timer** (Ctrl+Alt+T, the clock button): an analogue clock with
  the date and world clocks; a timer on a ring with presets, +1 min and its
  end time; a stopwatch with laps (fastest and slowest marked); alarms that
  repeat once, every day, on weekdays or at weekends, with snooze. Timers
  and alarms keep running with the panel closed (the status bar shows a
  running timer); when one is up a card says so and a soft chime plays.
- **Laser Pointer** (O) / **Pointer** (V): presentation aids that never
  change the PDF.
- **Insert Image / Draw Signature**: place a logo or a hand-drawn signature.
  *My Signatures* keeps up to 5 signatures to use again (tick *Save to My
  Signatures*; right-click one to remove it). No pen tablet? **Sign on My
  Phone...** emails you a link: open it on your phone, sign with your finger,
  tap *Send to my computer*, and the signature appears in the dialog (needs
  the signature service below; the link works for 30 minutes and the
  signature travels encrypted).
- Right-click any tool button to pin it under **Tools > Favorites**.
  **Tools > Tool Styles** edits every tool's default colour, width, opacity,
  font and font size in one table.

## Menus

- **File**: New, New Word Document, New LaTeX Document, Open (PDF, .docx,
  .tex and text files; each file gets its own tab; double-click a tab, or
  right-click it > Rename, to rename the document: a saved file is renamed
  on disk, an unsaved one keeps the name for Save), Save / Save As / Save All
  / Save as Template, Combine Files, Split Every Page to Separate Files,
  Convert to Word, Convert to LaTeX (see "Converting PDFs" below),
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
- **Help**: Ask AUPedia (F1), Show AUPedia, AUPedia Settings, About.

## AUPedia, the scribble helper

The little ink scribble in the corner is **AUPedia**. Click it (or press
**F1**), type a question or a job, like "how do I sign this?", "convert this
to Word" or "make it dark", and pick:

- **Show me**: it flies to the right button or menu, circles it, writes a
  note beside it (menus open with the item highlighted), and tells you the
  steps.
- **Do it for me**: it clicks the buttons for you, then tells you what's left
  (for example, what to fill in on a dialog it opened). Anything that closes,
  deletes or removes asks you first.

Drag the scribble to give it a new corner; Help > Show AUPedia hides it.

**On the paper.** AUPedia can also work on the open PDF itself, holding its
nib like a pen: it **reads** pages ("summarise this page", "what's the
deposit?"), **marks text** (highlights, underlines, strikes out or circles
words), **writes** notes and answers in its handwriting (or in print), and
**draws**: freehand doodles, ticks and stars, plus rectangles, ellipses,
lines and arrows. You watch it fly to the spot and draw or write it, then it
becomes a normal annotation: move it, edit it, or take it back with Undo.
Only in **Do it for me**; **Show me** never changes the paper.

**Its brain.** On its own, AUPedia finds buttons by their names and can
highlight, underline, strike out or circle words you name ("highlight
'monthly rent'"). For everything else, give it an AI in AUPedia Settings
(the gear in its bubble) and choose the model:

- **Claude** (Anthropic): an API key from console.anthropic.com; Claude Opus
  5.5 (recommended), Sonnet 5.5, Haiku 4.5 or Fable 5.1. Claude can also see
  a picture of each page it reads.
- **Hugging Face**: a fine-grained access token with "Make calls to
  Inference Providers" (huggingface.co/settings/tokens); pick a model from
  the live list of models that can use tools (**Refresh List**; 👁 marks the
  ones that can see pictures of pages), or type any model id.

Keys are your own and stored encrypted for your Windows account; each
question costs a little on that account. Questions, the list of menus, and
what's open (file names, page, tool) go to the service you choose. When
AUPedia reads or marks up a page, that page's text (and a picture of it, for
models that can see) goes too. The code is in `app/pdfannotator/aupedia/`.

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
- AUPedean notices by itself (it checks every minute while it's open),
  stamps the signature into your original with a line saying who signed,
  that their email was verified, and when, and saves it. If the PDF is in
  Google Drive, Google Drive for desktop syncs it. You also get a "Signed"
  email. *Signature Requests* lists everything you've sent.
- The document is encrypted on your PC (AES-256-GCM) before it's uploaded,
  and the key is only in the signer's emailed link, after the "#" (which
  browsers never send to servers), so what the service stores can't be read.
  The service deletes it once the signature is collected, or when the link
  expires.

This goes through **AUPedean Sign**, a small service that runs in *your own*
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
  **Install**. AUPedean creates the database and installs the service; it
  gets an address like `https://aupedean-sign.<name>.workers.dev`. Give that
  address to anyone else who uses it. Copying it into
  `app/assets/sign_service.json` as `{"url": "..."}` builds it into the exe.
  The code is in `app/pdfannotator/cloud/sign_service/` (`worker.js`,
  `page.html`, and `capture.html` for signing on a phone). A service
  installed before Sign on My Phone existed needs **Install** run again to
  get it.

Without a service there's also the **signing file**: Request Signature >
Make a Signing File. AUPedean makes one web page with the document in it
(Documents > Aupedean Signing Files); send it yourself (*Copy File* and paste
it into WhatsApp or an email). The signer opens it in a browser, signs on the
document and sends the signed PDF back; open it in AUPedean, use *Add a
Signed Copy*, or save it to Downloads, and the signature goes into your
original. Nothing to set up, but it comes back by hand and the email isn't
verified. The signed PDF is made in the browser with pdf-lib (MIT licence,
`app/assets/js`).

## Google Drive and signing links

The **Google Drive** menu (code in `app/pdfannotator/cloud/`):

- **Syncing uses Google Drive for desktop**, Google's free app
  (https://www.google.com/drive/download/). Once it's installed and signed
  in, your Drive is on the PC as a "Google Drive" drive (usually G:), and
  Google syncs every save. Nothing needs setting up in AUPedean for this.
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

## Converting PDFs to Word and LaTeX

File > Convert to Word / Convert to LaTeX. The **Exact** layout (the
default) keeps the document looking just as it does in the PDF:

- The page's lines, table borders, charts and pictures are kept as they are:
  for LaTeX as a vector copy of the page with its text taken out
  (`background.pdf`), for Word as a sharp picture behind the text.
- Every line of text is retyped over it, at its original position, in a
  matching font: Helvetica / Nimbus Sans, Times and Courier become TeX Gyre
  Heros, Termes and Cursor (LaTeX) or Arial, Times New Roman and Courier New
  (Word), which have the same letter widths; Computer Modern documents use
  Latin Modern in the right design size, with Latin Modern Math (LaTeX) or
  Cambria Math (Word) for the maths; other fonts are used by name when
  Windows has them. Justified lines are spread to their original width
  (LaTeX) or each word is placed where it was (Word).
- Maths keeps its letters and symbols (math italic, Greek, blackboard bold
  ℝ ℕ ℤ, operators); the few glyphs Unicode has no code for at their size
  (display integrals, big brackets) are kept as sharp pictures. Links stay
  clickable (LaTeX).
- LaTeX: `\PT{x}{y}{text}` puts text with its baseline at (x, y) points from
  the page's lower-left corner (`\PTW` also gives the width): edit the text
  in place and compile with XeLaTeX. The project opens and compiles at once
  in the LaTeX tab (F5 compiles from anywhere in the tab).
- Word: each line is a frame placed on the page; open the file in Microsoft
  Word to see it exactly (the Word editor here shows the text in order).

The **Flowing** layout rebuilds headings, paragraphs and tables that reflow
as you type (easier to rewrite, only roughly like the PDF). Scanned pages are
read with OCR in both.

## Word and LaTeX editors

Word documents and LaTeX sources open in tabs next to the PDFs. While one
is active the PDF tools are hidden, and Save, Undo, Cut/Copy/Paste, Find,
Print and zoom act on it.

**Word** (`word_editor.py`, `word_io.py`, `word_dialogs.py`): opens and
saves `.docx` (also saves `.odt`, `.html`, `.md`, `.txt`; opens `.html`,
`.md`, `.txt`). A Word-style ribbon:

- **Home**: Undo / Redo; Paste (or Paste Text Only), Cut, Copy, Format
  Painter; styles (Normal, No Spacing, Title, Subtitle, Heading 1-6, Quote,
  Intense Quote, Caption, Code); font, size, Increase / Decrease Font Size
  (Ctrl+] / Ctrl+[), Change Case (Sentence case, lowercase, UPPERCASE,
  Capitalize Each Word, tOGGLE cASE; Shift+F3 cycles), Small Caps / All
  Caps, Font... (Ctrl+D), Clear Formatting; bold, italic, underline (single,
  dotted, dashed, dot-dash, dot-dot-dash, wave), strike-through, sub- and
  superscript; font colour and highlight palettes; alignment; Bullets
  (filled, hollow, square) and Numbering (1. 1) (1) a. a) A. i. I., Restart
  at 1, Continue Numbering, Set Numbering Value), multilevel lists (Tab /
  Shift+Tab change the level), indents; line spacing (1 to 3, Ctrl+1 / 2 /
  5) and space before / after; paragraph shading; Paragraph...; show
  formatting marks (Ctrl+Shift+8); find and replace (Ctrl+H).
- **Insert**: tables (quick sizes, add / delete rows and columns, merge and
  split, cell shading; also on right-click), pictures (insert, paste,
  resize), links (Ctrl+click opens them), horizontal lines, symbols and
  special characters, the date and time, page breaks.
- **Layout**: margins (Normal, Narrow, Moderate, Wide, custom), portrait /
  landscape, paper size (Letter, Legal, A3, A4, A5, B5, Executive),
  Page Setup..., left / right indents and spacing before / after.
- **Review**: Word Count (pages, words, characters, paragraphs, lines; of
  the selection when there is one), find and replace, Select All.

Print and Export as PDF sit at the right of the ribbon (Export offers to open
the PDF in a tab for annotating).
Saving builds on the file that was opened, so its styles, numbering,
headers and footers are kept; lists made here get real Word numbering, and
the page size, orientation and margins set here are saved. Pages are shown as one continuous
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
