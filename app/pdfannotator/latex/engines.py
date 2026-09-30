"""LaTeX compilers and external editors: finding them, building the
compile command, and reading the log into a list of problems.

Compilers: XeLaTeX, pdfLaTeX, LuaLaTeX and latexmk from MiKTeX or TeX Live,
Tectonic, or any command the user types (with {file}, {stem} and {dir}
placeholders). External editors: TeXworks, TeXstudio, VS Code, Notepad++,
the file's default program, or any program the user picks.

No Qt here, so it can be tested on its own.
"""
import glob
import os
import re
import shlex
import shutil
from dataclasses import dataclass

LATEX_ARGS = ["-interaction=nonstopmode", "-file-line-error", "-synctex=1"]


@dataclass(frozen=True)
class Engine:
    key: str
    label: str
    exe: str
    args: tuple = ()

    def command(self, path_to_exe, tex_file):
        return [path_to_exe, *self.args, os.path.basename(tex_file)]


ENGINES = [
    Engine("xelatex", "XeLaTeX", "xelatex", tuple(LATEX_ARGS)),
    Engine("pdflatex", "pdfLaTeX", "pdflatex", tuple(LATEX_ARGS)),
    Engine("lualatex", "LuaLaTeX", "lualatex", tuple(LATEX_ARGS)),
    Engine("latexmk", "latexmk (XeLaTeX, runs until references settle)", "latexmk",
           ("-xelatex", "-interaction=nonstopmode", "-file-line-error", "-synctex=1")),
    Engine("tectonic", "Tectonic", "tectonic", ("-X", "compile", "--synctex", "--keep-logs")),
]
ENGINE_BY_KEY = {e.key: e for e in ENGINES}
CUSTOM = "custom"


def _tex_dirs():
    """Folders where MiKTeX / TeX Live / Tectonic are usually installed."""
    local = os.environ.get("LOCALAPPDATA", "")
    home = os.path.expanduser("~")
    dirs = [
        os.path.join(local, "Programs", "MiKTeX", "miktex", "bin", "x64"),
        r"C:\Program Files\MiKTeX\miktex\bin\x64",
        r"C:\Program Files (x86)\MiKTeX\miktex\bin",
        os.path.join(home, ".cargo", "bin"),
        os.path.join(local, "AupedeanAnnotator", "tectonic"),
    ]
    for pattern in (r"C:\texlive\*\bin\windows", r"C:\texlive\*\bin\win64", r"C:\texlive\*\bin\win32"):
        dirs.extend(sorted(glob.glob(pattern), reverse=True))
    return [d for d in dirs if os.path.isdir(d)]


def find_program(name):
    """Full path of `name` (without .exe) on PATH or in the usual TeX folders."""
    found = shutil.which(name)
    if found:
        return found
    for folder in _tex_dirs():
        for candidate in (name + ".exe", name):
            path = os.path.join(folder, candidate)
            if os.path.isfile(path):
                return path
    return None


def available_engines():
    """[(Engine, path)] for the compilers found on this PC."""
    return [(engine, path) for engine in ENGINES if (path := find_program(engine.exe))]


def custom_command(template, tex_file):
    """Split a user's command line, filling in {file}, {stem} and {dir}."""
    values = {
        "file": os.path.basename(tex_file),
        "stem": os.path.splitext(os.path.basename(tex_file))[0],
        "dir": os.path.dirname(os.path.abspath(tex_file)),
    }
    parts = shlex.split(template, posix=False)
    if "{file}" not in template and "{stem}" not in template:
        parts.append("{file}")
    return [p.strip('"').format(**values) for p in parts]


def build_command(engine_key, tex_file, custom_template=""):
    """(argv, error message). argv is None when the compiler isn't found."""
    if engine_key == CUSTOM:
        if not custom_template.strip():
            return None, "No custom compile command is set. Enter one in LaTeX Settings."
        argv = custom_command(custom_template, tex_file)
        exe = argv[0] if os.path.isfile(argv[0]) else find_program(argv[0])
        if not exe:
            return None, f"The program {argv[0]} was not found."
        return [exe, *argv[1:]], None
    engine = ENGINE_BY_KEY.get(engine_key) or ENGINES[0]
    exe = find_program(engine.exe)
    if not exe:
        return None, (f"{engine.label} was not found on this PC. Install MiKTeX (miktex.org) or TeX Live, "
                      "choose another compiler, or use Overleaf.")
    return engine.command(exe, tex_file), None


# --------------------------------------------------------------------------
# External editors
# --------------------------------------------------------------------------

def external_editors():
    """[(label, path)] of LaTeX-capable editors found on this PC."""
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        ("TeXworks", [find_program("miktex-texworks"), find_program("texworks")]),
        ("TeXstudio", [r"C:\Program Files\texstudio\texstudio.exe",
                       os.path.join(local, "Programs", "texstudio", "texstudio.exe"), shutil.which("texstudio")]),
        ("Visual Studio Code", [os.path.join(local, "Programs", "Microsoft VS Code", "Code.exe"),
                                r"C:\Program Files\Microsoft VS Code\Code.exe"]),
        ("Notepad++", [r"C:\Program Files\Notepad++\notepad++.exe", r"C:\Program Files (x86)\Notepad++\notepad++.exe"]),
    ]
    found = []
    for label, paths in candidates:
        path = next((p for p in paths if p and os.path.isfile(p)), None)
        if path:
            found.append((label, path))
    return found


# --------------------------------------------------------------------------
# Log -> problems
# --------------------------------------------------------------------------

@dataclass
class Problem:
    kind: str       # "error", "warning" or "badbox"
    message: str
    file: str = ""
    line: int = 0


_FILE_LINE = re.compile(r"^(?:error: )?(.+?\.(?:tex|sty|cls|bib|bbl|ltx|dtx)):(\d+): (.+)$")
_BANG = re.compile(r"^! (.+)$")
_LINE_NO = re.compile(r"^l\.(\d+)")
_WARNING = re.compile(r"^(?:LaTeX|Package [\w@-]+|Class [\w@-]+|pdfTeX|LaTeX Font) Warning: (.+)$")
_ON_LINE = re.compile(r"on input line (\d+)")
_BADBOX = re.compile(r"^((?:Over|Under)full \\[hv]box .*?)(?: (?:in paragraph |detected )?at lines? (\d+)(?:--\d+)?)?$")
_TECTONIC_WARNING = re.compile(r"^warning: (?:(.+?\.tex):(\d+): )?(.+)$")


def parse_log(text, main_file=""):
    """Problems from a TeX log (or Tectonic's output), errors first."""
    problems = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _FILE_LINE.match(line)
        if m:
            problems.append(Problem("error", m.group(3).strip(), os.path.basename(m.group(1)), int(m.group(2))))
            i += 1
            continue
        m = _BANG.match(line)
        if m:
            number = 0
            for follow in lines[i + 1:i + 12]:
                n = _LINE_NO.match(follow)
                if n:
                    number = int(n.group(1))
                    break
            problems.append(Problem("error", m.group(1).strip(), main_file, number))
            i += 1
            continue
        m = _WARNING.match(line)
        if m:
            message = m.group(1)
            j = i + 1
            while j < len(lines) and lines[j].startswith(" ") and lines[j].strip() and len(message) < 400:
                message += " " + lines[j].strip()
                j += 1
            n = _ON_LINE.search(message)
            problems.append(Problem("warning", message.strip(), main_file, int(n.group(1)) if n else 0))
            i = j
            continue
        m = _BADBOX.match(line)
        if m:
            problems.append(Problem("badbox", m.group(1).strip(), main_file, int(m.group(2) or 0)))
            i += 1
            continue
        m = _TECTONIC_WARNING.match(line)
        if m:
            problems.append(Problem("warning", m.group(3).strip(), os.path.basename(m.group(1) or main_file),
                                    int(m.group(2) or 0)))
        i += 1
    # the same error is often reported in both styles; keep one of each
    unique, seen = [], set()
    for p in problems:
        key = (p.kind, p.message[:80], p.line)
        if key not in seen:
            seen.add(key)
            unique.append(p)
    order = {"error": 0, "warning": 1, "badbox": 2}
    return sorted(unique, key=lambda p: order[p.kind])


def synctex_edit_command(pdf, page, x, y):
    """argv for SyncTeX's inverse search (PDF position -> source line), or None."""
    exe = find_program("synctex")
    if not exe:
        return None
    return [exe, "edit", "-o", f"{page}:{x:.1f}:{y:.1f}:{pdf}"]


def parse_synctex_edit(output):
    """(input file, line) from `synctex edit` output, or (None, 0)."""
    file = re.search(r"^Input:(.+)$", output, re.M)
    line = re.search(r"^Line:(\d+)$", output, re.M)
    if not line:
        return None, 0
    return (file.group(1).strip() if file else None), int(line.group(1))


def synctex_view_command(tex_file, line, pdf):
    """argv for SyncTeX's forward search (source line -> PDF position), or None."""
    exe = find_program("synctex")
    if not exe:
        return None
    return [exe, "view", "-i", f"{line}:0:{os.path.basename(tex_file)}", "-o", pdf]


def parse_synctex_view(output):
    """(page, y in PDF points from the top) of the first match, or (0, 0)."""
    page = re.search(r"^Page:(\d+)$", output, re.M)
    y = re.search(r"^y:([\d.]+)$", output, re.M)
    if not page:
        return 0, 0.0
    return int(page.group(1)), float(y.group(1)) if y else 0.0
