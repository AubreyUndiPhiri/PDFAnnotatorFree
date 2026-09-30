"""Run the Hugging Face OCR models (GOT-OCR 2.0 and TrOCR) from the app.

PyTorch is far too big to ship inside the exe, so the models run in a
separate Python that has torch + transformers installed. latex/hf_worker.py
is that child process; this module finds a Python that can run it and talks
to it (JSON lines over stdin/stdout, see hf_worker.py for the protocol).

Which Python is used, in order: the AUPEDEAN_MODEL_PYTHON environment
variable, this app's own Python (when run from source), the `py` launcher,
`python` on PATH, then the usual per-user install folders.
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MODELS = {
    "got": "stepfun-ai/GOT-OCR-2.0-hf",  # printed text, headings, tables, formulas
    "trocr": "microsoft/trocr-small-handwritten",  # handwriting, one line at a time
}
MODEL_LABELS = {"got": "GOT-OCR 2.0", "trocr": "TrOCR (handwriting)"}
PYTHON_ENV = "AUPEDEAN_MODEL_PYTHON"
LOG_PATH = Path(tempfile.gettempdir()) / "aupedean_ocr_worker.log"

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# The worker pins the CPU for minutes; below-normal keeps the app and Windows responsive
_BELOW_NORMAL = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
_found = None  # cached (python path or None, check info or reason)


class ModelError(RuntimeError):
    pass


def worker_script() -> Path:
    # Same folder layout from source and in the exe (the spec ships it as data)
    return Path(__file__).resolve().parent / "latex" / "hf_worker.py"


def _candidates():
    seen = set()

    def add(path):
        if not path:
            return
        path = os.path.normcase(os.path.abspath(path))
        # The WindowsApps "python.exe" is a stub that opens the Microsoft Store
        if path in seen or "windowsapps" in path or not os.path.isfile(path):
            return
        seen.add(path)
        yield path

    yield from add(os.environ.get(PYTHON_ENV))
    if not getattr(sys, "frozen", False):
        yield from add(sys.executable)
    launcher = shutil.which("py")
    if launcher:
        try:
            out = subprocess.run([launcher, "-3", "-c", "import sys; print(sys.executable)"], capture_output=True,
                                 text=True, timeout=30, creationflags=_NO_WINDOW)
            yield from add(out.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    for name in ("python", "python3"):
        yield from add(shutil.which(name))
    local = os.environ.get("LOCALAPPDATA", "")
    for pattern in (os.path.join(local, "Programs", "Python", "Python3*", "python.exe"),
                    r"C:\Program Files\Python3*\python.exe", r"C:\Python3*\python.exe"):
        for path in sorted(glob.glob(pattern), reverse=True):
            yield from add(path)


def check_python(python):
    """Run the worker's --check in `python`; returns its info dict."""
    try:
        out = subprocess.run([python, str(worker_script()), "--check"], capture_output=True, text=True,
                             timeout=180, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": str(e)}
    for line in reversed(out.stdout.splitlines()):
        try:
            return json.loads(line)
        except ValueError:
            continue
    return {"ok": False, "error": (out.stderr or "no output").strip()[-300:]}


def find_python(refresh=False):
    """(python, info) for the first Python that can run the models, or
    (None, reason). The result is cached; importing torch takes a while."""
    global _found
    if _found is not None and not refresh:
        return _found
    if not worker_script().is_file():
        _found = (None, f"The model worker is missing ({worker_script()}).")
        return _found
    errors = []
    for python in _candidates():
        info = check_python(python)
        if info.get("ok"):
            _found = (python, info)
            return _found
        errors.append(f"{python}: {info.get('error', 'not usable')}")
    reason = ("No Python with PyTorch and transformers was found. Install them with "
              "\"pip install torch transformers sentencepiece pillow\", or set "
              f"{PYTHON_ENV} to a Python that has them.")
    if errors:
        reason += "\n\nChecked:\n" + "\n".join(errors[:5])
    _found = (None, reason)
    return _found


class ModelClient:
    """One model loaded in a worker process; read pages with read_page().
    close() (or kill() from another thread, to cancel) ends the process."""

    def __init__(self, kind, python=None, status=None):
        if kind not in MODELS:
            raise ValueError(f"Unknown model: {kind}")
        if python is None:
            python, info = find_python()
            if python is None:
                raise ModelError(info)
        else:
            info = check_python(python)
        missing = (info.get("missing") or {}).get(kind) if isinstance(info, dict) else None
        if missing:
            raise ModelError(f"{MODEL_LABELS[kind]} needs {', '.join(missing)}: run "
                             f"\"{python} -m pip install {' '.join(missing)}\".")
        self.kind = kind
        self.status = status or (lambda text: None)
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self._log = open(LOG_PATH, "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(
            [python, "-u", str(worker_script())], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self._log, text=True, encoding="utf-8", errors="replace", env=env,
            creationflags=_NO_WINDOW | _BELOW_NORMAL,
        )
        try:
            self._send(cmd="load", kind=kind, model=MODELS[kind])
            self.device = self._wait("ready").get("device", "cpu")
        except BaseException:
            self.close()
            raise

    def read_page(self, image_path, prompt="") -> str:
        self._send(cmd="page", image=str(image_path), prompt=prompt)
        return self._wait("result").get("text", "")

    def _send(self, **msg):
        try:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise ModelError(f"The OCR model stopped unexpectedly. {self._log_tail()}") from e

    def _wait(self, event):
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise ModelError(f"The OCR model stopped unexpectedly. {self._log_tail()}")
            try:
                msg = json.loads(line)
            except ValueError:
                continue  # a library printed to stdout
            if msg.get("event") == "status":
                self.status(msg.get("text", ""))
            elif msg.get("event") == "error":
                raise ModelError(msg.get("text", "The OCR model failed."))
            elif msg.get("event") == event:
                return msg

    def _log_tail(self):
        try:
            self._log.flush()
            lines = [ln for ln in LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        except OSError:
            return ""
        return ("\n" + "\n".join(lines[-4:])) if lines else ""

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()

    def close(self):
        if self.proc.poll() is None:
            try:
                self._send(cmd="quit")
                self.proc.wait(timeout=10)
            except (ModelError, subprocess.TimeoutExpired):
                self.proc.kill()
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                stream.close()
            except OSError:
                pass
        self._log.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
