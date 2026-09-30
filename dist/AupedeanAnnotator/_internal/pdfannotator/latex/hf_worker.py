"""Hugging Face recognition worker.

Runs in a Python environment that has PyTorch + transformers (the app itself
ships without them, they are far too large for the exe). The app starts it
as a child process and talks JSON lines over stdin/stdout:

    -> {"cmd": "load", "kind": "got"|"trocr"|"vlm", "model": "<repo id>", "device": "auto"|"cpu"|"cuda"}
    <- {"event": "status", "text": "..."}          (any number of times)
    <- {"event": "ready", "device": "cpu"}
    -> {"cmd": "page", "image": "<png path>", "prompt": "..."}
    <- {"event": "result", "text": "<recognised text / LaTeX>"}
    -> {"cmd": "quit"}
    <- {"event": "error", "text": "..."}          (instead of ready/result on failure)

The model stays loaded between pages. Performance choices: inference_mode,
half the CPU threads (see cpu_threads), float16 on CUDA and float32 on CPU (bfloat16 is slow on most
laptop CPUs), images resized to what each model actually uses, and, for
TrOCR, line-by-line recognition of the handwriting.

    python hf_worker.py --check     # prints {"ok": true, ...} if usable
"""
import json
import os
import sys
import traceback

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
# PyTorch only: don't let transformers import TensorFlow / JAX if they happen
# to be installed (slow start-up, noisy logs, extra memory)
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("USE_FLAX", "0")
os.environ.setdefault("USE_TORCH", "1")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

# Extra packages each model family needs beyond torch + transformers
MODEL_REQUIREMENTS = {"trocr": ["sentencepiece"], "got": [], "vlm": []}


def _guard_torchvision():
    """A torchvision built for a different PyTorch breaks transformers' image
    processors ("operator torchvision::nms does not exist"). If it can't be
    imported cleanly, hide it so transformers uses its plain PIL processors."""
    try:
        import torchvision  # noqa: F401
        from torchvision.ops import nms  # noqa: F401
    except Exception:  # noqa: BLE001 - any failure means "unusable"
        for name in [m for m in sys.modules if m == "torchvision" or m.startswith("torchvision.")]:
            del sys.modules[name]
        sys.modules["torchvision"] = None
        return False
    return True


def emit(event, **fields):
    sys.stdout.write(json.dumps({"event": event, **fields}) + "\n")
    sys.stdout.flush()


def cpu_threads():
    """Threads for inference. Every logical core at 100% for minutes overheats
    thin laptops (firmware throttling, then driver crashes), so default to half
    of them, leaving headroom. AUPEDEAN_MODEL_THREADS overrides it."""
    try:
        return max(1, int(os.environ["AUPEDEAN_MODEL_THREADS"]))
    except (KeyError, ValueError):
        return max(1, (os.cpu_count() or 2) // 2)


def from_pretrained(cls, repo, **kwargs):
    """Load from the local Hugging Face cache first (works offline and skips
    the Hub check that transformers makes even for downloaded models); only
    go online when the model isn't downloaded yet."""
    try:
        return cls.from_pretrained(repo, local_files_only=True, **kwargs)
    except OSError:
        emit("status", text=f"Downloading {repo} (first time only)...")
        return cls.from_pretrained(repo, **kwargs)


class Worker:
    def __init__(self):
        self.kind = None
        self.model = None
        self.processor = None
        self.device = "cpu"
        self.dtype = None

    # ------------------------------------------------------------ loading
    def load(self, kind, repo, device="auto"):
        self.model = self.processor = None
        import torch

        torch.set_num_threads(cpu_threads())
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        self.dtype = torch.float16 if device == "cuda" else torch.float32
        self.kind = kind
        emit("status", text=f"Loading {repo} on {device.upper()}...")
        if kind == "trocr":
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel

            self.processor = from_pretrained(TrOCRProcessor, repo)
            self.model = from_pretrained(VisionEncoderDecoderModel, repo, torch_dtype=self.dtype)
        elif kind == "got":
            from transformers import AutoModelForImageTextToText, AutoProcessor

            self.processor = from_pretrained(AutoProcessor, repo)
            self.model = from_pretrained(AutoModelForImageTextToText, repo, torch_dtype=self.dtype,
                                         low_cpu_mem_usage=True)
        else:  # a general vision-language model that follows instructions
            from transformers import AutoModelForImageTextToText, AutoProcessor

            self.processor = from_pretrained(AutoProcessor, repo)
            self.model = from_pretrained(AutoModelForImageTextToText, repo, torch_dtype=self.dtype,
                                         low_cpu_mem_usage=True)
        self.model.to(device).eval()
        emit("ready", device=device)

    # ------------------------------------------------------------ pages
    def page(self, image_path, prompt=""):
        if self.model is None:
            raise RuntimeError("No model is loaded (loading failed earlier).")
        import torch
        from PIL import Image

        image = Image.open(image_path).convert("RGB")
        with torch.inference_mode():
            if self.kind == "trocr":
                return self._trocr(image)
            if self.kind == "got":
                return self._got(image)
            return self._vlm(image, prompt)

    def _got(self, image):
        inputs = self.processor(image, return_tensors="pt", format=True).to(self.device)
        if self.device == "cuda":
            inputs["pixel_values"] = inputs["pixel_values"].to(self.dtype)
        emit("status", text="Reading the page (GOT-OCR 2.0)...")
        out = self.model.generate(**inputs, do_sample=False, tokenizer=self.processor.tokenizer,
                                  stop_strings="<|im_end|>", max_new_tokens=4096)
        return self.processor.decode(out[0, inputs["input_ids"].shape[1]:], skip_special_tokens=True)

    def _trocr(self, image):
        lines = split_lines(image)
        texts = []
        for i, line in enumerate(lines, 1):
            emit("status", text=f"Reading handwriting line {i} of {len(lines)}...")
            pixel_values = self.processor(images=line, return_tensors="pt").pixel_values.to(self.device, self.dtype)
            ids = self.model.generate(pixel_values, max_new_tokens=96, num_beams=3)
            texts.append(self.processor.batch_decode(ids, skip_special_tokens=True)[0].strip())
        return "\n".join(t for t in texts if t)

    def _vlm(self, image, prompt):
        image.thumbnail((1400, 1400))
        messages = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": prompt}]}]
        text = self.processor.apply_chat_template(messages, add_generation_prompt=True)
        inputs = self.processor(images=[image], text=text, return_tensors="pt").to(self.device)
        emit("status", text="Reading the page...")
        out = self.model.generate(**inputs, do_sample=False, max_new_tokens=4096)
        return self.processor.decode(out[0, inputs["input_ids"].shape[1]:], skip_special_tokens=True)


def split_lines(image, min_gap=6):
    """Cut a page of handwriting into text-line images (TrOCR reads one line
    at a time) using the horizontal ink profile."""
    import numpy as np

    gray = np.asarray(image.convert("L"), dtype=np.float32)
    ink = gray < min(200, np.percentile(gray, 90) * 0.75)
    rows = ink.sum(axis=1) > max(2, ink.shape[1] * 0.004)
    lines, start, gap = [], None, 0
    for y, has_ink in enumerate(rows):
        if has_ink:
            if start is None:
                start = y
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= min_gap:
                end = y - gap
                if end - start > 8:
                    lines.append((start, end))
                start, gap = None, 0
    if start is not None and len(rows) - start > 8:
        lines.append((start, len(rows)))
    crops = []
    for y0, y1 in lines:
        cols = np.where(ink[y0:y1].any(axis=0))[0]
        if not len(cols):
            continue
        pad = max(4, (y1 - y0) // 4)
        box = (max(0, cols[0] - pad), max(0, y0 - pad), min(image.width, cols[-1] + pad), min(image.height, y1 + pad))
        crops.append(image.crop(box))
    return crops or [image]


def check():
    info = {"ok": False, "python": sys.executable}
    try:
        vision_ok = _guard_torchvision()
        import torch
        import transformers
        from transformers import AutoProcessor  # noqa: F401 - fails if the install is broken

        import importlib.util

        missing = {kind: [m for m in mods if importlib.util.find_spec(m) is None]
                   for kind, mods in MODEL_REQUIREMENTS.items()}
        info.update(ok=True, torchvision=vision_ok, missing=missing, torch=torch.__version__, transformers=transformers.__version__,
                    cuda=bool(torch.cuda.is_available()))
    except Exception as e:  # noqa: BLE001 - report anything that stops it working
        info["error"] = str(e)
    print(json.dumps(info))


def main():
    if "--check" in sys.argv:
        check()
        return
    _guard_torchvision()
    worker = Worker()
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
            cmd = msg.get("cmd")
            if cmd == "quit":
                break
            if cmd == "load":
                worker.load(msg["kind"], msg["model"], msg.get("device", "auto"))
            elif cmd == "page":
                emit("result", text=worker.page(msg["image"], msg.get("prompt", "")))
        except Exception as e:  # noqa: BLE001 - every failure goes back to the app
            emit("error", text=f"{type(e).__name__}: {e}", trace=traceback.format_exc(limit=3))


if __name__ == "__main__":
    main()
