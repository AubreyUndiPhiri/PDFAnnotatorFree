"""Convert documents to editable LaTeX.

- pdf_to_latex: PDF pages -> a LaTeX project folder (main.tex + images/).
  Typed pages are rebuilt from PyMuPDF text, fonts, tables and images;
  scanned pages are read with the same OCR as the Word export.
- texutil: escaping, inline styles and the XeLaTeX (fontspec) preamble.
- hf_worker: runs the Hugging Face OCR models in a Python environment that
  has PyTorch (see ocr_models.py).
"""
