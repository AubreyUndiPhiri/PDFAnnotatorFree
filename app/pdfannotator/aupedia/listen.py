"""Active listening: the microphone, turned into text as you speak.

Speech is recognised on this computer by Vosk (a small English model,
downloaded once): the audio never leaves the machine. Words appear as
they're heard (`partial`), and when you pause, the sentence is done
(`final`). Recognition runs in its own thread so the scribble keeps
moving smoothly; `level` (0..1) is how loud the microphone is, for it to
react to."""
import json
import os
import queue
import shutil
import tempfile
import threading
import urllib.request
import zipfile
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..fonts import APP_DATA

MODEL_NAME = "vosk-model-small-en-us-0.15"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"
MODEL_MB = 40
SPEECH_DIR = Path(os.environ.get("AUPEDEAN_SPEECH_DIR") or APP_DATA / "aupedia" / "speech")
RATE = 16000                    # what the model hears: 16 kHz, mono, 16-bit
_DONE = object()


class ListenError(Exception):
    pass


def model_path():
    return SPEECH_DIR / MODEL_NAME


def model_ready():
    return (model_path() / "am").is_dir() or (model_path() / "conf").is_dir()


def download_model(progress=lambda fraction: None, url=MODEL_URL):
    """Fetch and unpack the speech model (once). Runs in a worker thread."""
    SPEECH_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".zip", dir=SPEECH_DIR)
    os.close(fd)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AUPedean-Annotator"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as out:
                total = int(resp.headers.get("Content-Length") or MODEL_MB * 1024 * 1024)
                got = 0
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    got += len(chunk)
                    progress(min(1.0, got / total))
        except OSError as exc:
            raise ListenError("I couldn't download the speech model. Check the internet connection and try "
                              "again.") from exc
        unpack = Path(tempfile.mkdtemp(dir=SPEECH_DIR))
        try:
            with zipfile.ZipFile(tmp) as z:
                z.extractall(unpack)
            found = next((p for p in unpack.rglob("conf") if p.is_dir()), None)
            if found is None:
                raise ListenError("The speech model download was damaged. Try again.")
            if model_path().exists():
                shutil.rmtree(model_path(), ignore_errors=True)
            shutil.move(str(found.parent), str(model_path()))
        except zipfile.BadZipFile as exc:
            raise ListenError("The speech model download was damaged. Try again.") from exc
        finally:
            shutil.rmtree(unpack, ignore_errors=True)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


class Recognizer:
    """Vosk, fed 16 kHz mono 16-bit audio; returns (partial, final) for each chunk."""

    def __init__(self, path=None):
        try:
            import vosk
        except ImportError as exc:
            raise ListenError("Speech recognition isn't installed in this copy of the app.") from exc
        vosk.SetLogLevel(-1)
        self._vosk = vosk
        self.model = vosk.Model(str(path or model_path()))
        self.reset()

    def reset(self):
        self.rec = self._vosk.KaldiRecognizer(self.model, RATE)

    def feed(self, data):
        if self.rec.AcceptWaveform(data):
            return "", json.loads(self.rec.Result()).get("text", "")
        return json.loads(self.rec.PartialResult()).get("partial", ""), None

    def flush(self):
        text = json.loads(self.rec.FinalResult()).get("text", "")
        self.reset()
        return text


def to_model_audio(data, fmt_rate, channels, sample_format):
    """Microphone bytes in whatever format it gives -> 16 kHz mono 16-bit, and its loudness (0..1)."""
    import numpy as np
    from PySide6.QtMultimedia import QAudioFormat

    if sample_format == QAudioFormat.Float:
        x = np.frombuffer(data, dtype=np.float32)
    elif sample_format == QAudioFormat.Int32:
        x = np.frombuffer(data, dtype=np.int32).astype(np.float32) / 2147483648.0
    elif sample_format == QAudioFormat.UInt8:
        x = (np.frombuffer(data, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        x = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        x = x[: len(x) - len(x) % channels].reshape(-1, channels).mean(axis=1)
    if fmt_rate != RATE and len(x):
        n = max(1, int(round(len(x) * RATE / fmt_rate)))
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
    level = float(min(1.0, np.sqrt(np.mean(x * x)) * 6)) if len(x) else 0.0
    return (np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes(), level


class Listener(QObject):
    """The microphone and the recogniser. start() / stop(); pause() while the app is in the background."""
    partial = Signal(str)
    final = Signal(str)
    level = Signal(float)
    state = Signal(str)            # off, loading, listening, paused
    failed = Signal(str)

    def __init__(self, parent=None, recognizer_factory=None):
        super().__init__(parent)
        self.recognizer_factory = recognizer_factory or Recognizer
        self.recognizer = None
        self.source = None
        self.device_io = None
        self.fmt = None
        self.queue = None
        self.thread = None
        self.status = "off"
        self.paused = False

    def is_on(self):
        return self.status in ("loading", "listening")

    def _set(self, status):
        self.status = status
        self.state.emit(status)

    # ---- on and off
    def start(self):
        """Load the model (in the background, the first time) and open the microphone."""
        from ..cloud import worker

        if self.is_on():
            return
        self._set("loading")

        def ready(rec):
            if self.status != "loading":          # turned off while loading
                return
            self.recognizer = rec
            try:
                self._open_mic()
            except ListenError as exc:
                self._set("off")
                self.failed.emit(str(exc))
                return
            self._start_thread()
            self._set("listening")

        def broke(exc):
            self._set("off")
            self.failed.emit(str(exc) if isinstance(exc, ListenError) else f"Listening didn't start: {exc}")

        if self.recognizer is not None:
            ready(self.recognizer)
        else:
            worker.run(self.recognizer_factory, ready, broke)

    def stop(self):
        if self.status == "off":
            return
        self._close_mic()
        self._stop_thread()
        self.paused = False
        self._set("off")

    def pause(self, paused):
        """Stop hearing (the app is in the background) without forgetting it's on."""
        if self.status != "listening" or paused == self.paused:
            return
        self.paused = paused
        if paused:
            self._close_mic()
            if self.queue is not None:
                self.queue.put(b"__flush__")
        else:
            try:
                self._open_mic()
            except ListenError as exc:
                self.stop()
                self.failed.emit(str(exc))

    # ---- the microphone
    def _open_mic(self):
        from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices

        device = QMediaDevices.defaultAudioInput()
        if device.isNull():
            raise ListenError("I can't find a microphone. Plug one in (or allow microphone access in Windows "
                              "Settings > Privacy > Microphone) and try again.")
        fmt = QAudioFormat()
        fmt.setSampleRate(RATE)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.Int16)
        if not device.isFormatSupported(fmt):
            fmt = device.preferredFormat()
        self.fmt = fmt
        self.source = QAudioSource(device, fmt, self)
        self.source.setBufferSize(int(fmt.bytesForDuration(400_000)))
        self.device_io = self.source.start()
        if self.device_io is None:
            self.source = None
            raise ListenError("The microphone wouldn't open. Is another app using it?")
        self.device_io.readyRead.connect(self._read)

    def _close_mic(self):
        if self.source is not None:
            self.source.stop()
            self.source.deleteLater()
        self.source = self.device_io = None
        self.level.emit(0.0)

    def _read(self):
        if self.device_io is None:
            return
        data = bytes(self.device_io.readAll())
        if data:
            self.feed_raw(data, self.fmt.sampleRate(), self.fmt.channelCount(), self.fmt.sampleFormat())

    def feed_raw(self, data, rate, channels, sample_format):
        audio, level = to_model_audio(data, rate, channels, sample_format)
        self.level.emit(level)
        self.feed(audio)

    def feed(self, audio):
        """16 kHz mono 16-bit audio to recognise (from the microphone, or a test)."""
        if self.queue is not None and audio:
            self.queue.put(audio)

    # ---- recognising, in its own thread
    def _start_thread(self):
        self.queue = queue.Queue()
        self.thread = threading.Thread(target=self._run, args=(self.queue, self.recognizer), daemon=True)
        self.thread.start()

    def _stop_thread(self):
        if self.queue is not None:
            self.queue.put(_DONE)
        if self.thread is not None:
            self.thread.join(timeout=3)
        self.queue = self.thread = None

    def _run(self, q, rec):
        last = ""
        while True:
            item = q.get()
            if item is _DONE or item == b"__flush__":
                text = rec.flush()
                if text:
                    self.final.emit(text)
                last = ""
                if item is _DONE:
                    return
                continue
            # catch up: recognise everything waiting in one go, so words never lag behind
            chunks = [item]
            while not q.empty():
                nxt = q.queue[0]
                if nxt is _DONE or nxt == b"__flush__":
                    break
                chunks.append(q.get())
            partial, final = rec.feed(b"".join(chunks))
            if final is not None:
                if final:
                    self.final.emit(final)
                last = ""
            elif partial != last:
                last = partial
                self.partial.emit(partial)
