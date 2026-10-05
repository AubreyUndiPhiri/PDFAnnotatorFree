"""Talking to AUPedea: the speech model (downloaded once, run locally),
the audio conversion, live words while you speak, and spoken questions,
answers and "stop"s, all the way to a highlight on the page. Speech comes
from Windows' own voice saying a sentence into a WAV file (no microphone).

    python -m pytest tests/test_aupedia_voice.py
"""
import os
import subprocess
import sys
import time
import urllib.request
import wave
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pymupdf as fitz
import pytest
from PySide6.QtWidgets import QApplication

from pdfannotator.aupedia import anim, brain, listen
from test_aupedia import _claude_with, _message, bubble_text, done_talking, wait

CACHE = os.path.join(os.path.dirname(__file__), ".cache", "speech")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def speech_dir():
    """The real speech model, downloaded once into tests/.cache."""
    old = listen.SPEECH_DIR
    listen.SPEECH_DIR = type(old)(CACHE)
    try:
        if not listen.model_ready():
            try:
                listen.download_model()
            except listen.ListenError:
                pytest.skip("the speech model couldn't be downloaded (offline)")
        yield listen.SPEECH_DIR
    finally:
        listen.SPEECH_DIR = old


def say(text, path):
    """Windows' voice saying `text`, as a 16 kHz mono WAV (skips where there's no Windows voice)."""
    script = (f"Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              f"$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, "
              f"[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono); "
              f"$s.SetOutputToWaveFile('{path}', $f); $s.Speak('{text}'); $s.Dispose()")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("no Windows voice to speak the test sentence")
    with wave.open(str(path)) as w:
        return w.readframes(w.getnframes())


def chunks(audio, ms=100):
    step = int(listen.RATE * 2 * ms / 1000)
    return [audio[i:i + step] for i in range(0, len(audio), step)]


SILENCE = b"\x00\x00" * listen.RATE      # a second of quiet: the end of a sentence


@pytest.fixture
def quiet_mic(monkeypatch):
    """No real microphone in tests: the listener opens nothing and is fed WAV audio instead."""
    monkeypatch.setattr(listen.Listener, "_open_mic", lambda self: None)
    monkeypatch.setattr(listen.Listener, "_close_mic", lambda self: None)


# ---------------------------------------------------------------------------
# hearing
# ---------------------------------------------------------------------------

def test_recognises_speech_on_this_computer(speech_dir, tmp_path):
    audio = say("Please highlight the monthly rent and convert this document to word", tmp_path / "s.wav")
    rec = listen.Recognizer()
    partials, finals = [], []
    for c in chunks(audio):
        partial, final = rec.feed(c)
        if final:
            finals.append(final)
        elif partial and (not partials or partials[-1] != partial):
            partials.append(partial)
    finals.append(rec.flush())
    text = " ".join(f for f in finals if f)
    assert "highlight the monthly rent" in text and "word" in text
    assert len(partials) >= 5 and partials[0].split()[0] == "please"          # words arrive as they're said


def test_microphone_audio_becomes_what_the_model_hears():
    from PySide6.QtMultimedia import QAudioFormat

    t = np.arange(48000) / 48000.0
    tone = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    stereo = np.stack([tone, tone], axis=1).ravel().tobytes()                # a second at 48 kHz, stereo float
    audio, level = listen.to_model_audio(stereo, 48000, 2, QAudioFormat.Float)
    assert len(audio) == listen.RATE * 2 and level > 0.5                     # a second at 16 kHz, mono 16-bit
    quiet, level = listen.to_model_audio(b"\x00\x00" * 1600, 16000, 1, QAudioFormat.Int16)
    assert quiet == b"\x00\x00" * 1600 and level == 0.0


def test_listener_streams_words_then_the_sentence(app, speech_dir, tmp_path, quiet_mic):
    audio = say("Turn on dark mode", tmp_path / "s.wav")
    listener = listen.Listener()
    partials, finals, states = [], [], []
    listener.partial.connect(partials.append)
    listener.final.connect(finals.append)
    listener.state.connect(states.append)
    listener.start()
    assert wait(app, lambda: listener.status == "listening", 20)
    for c in chunks(audio + SILENCE):          # as fast as a microphone gives it (sped up a little)
        listener.feed(c)
        wait(app, lambda: False, 0.03)
    assert wait(app, lambda: finals, 10)
    assert finals == ["turn on dark mode"] and partials and partials[-1].startswith("turn on")
    listener.stop()
    assert states[0] == "loading" and states[-1] == "off"


def test_model_download_unpacks_and_is_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(listen, "SPEECH_DIR", tmp_path / "speech")
    z = tmp_path / "m.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("some-model-name/conf/model.conf", "x")
        f.writestr("some-model-name/am/final.mdl", "x")
    seen = []
    listen.download_model(seen.append, url=z.as_uri())
    assert listen.model_ready() and seen and seen[-1] == 1.0
    assert not list((tmp_path / "speech").glob("*.zip"))                     # the download is tidied away
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(listen.ListenError, match="damaged"):
        listen.download_model(url=bad.as_uri())


# ---------------------------------------------------------------------------
# talking to AUPedea
# ---------------------------------------------------------------------------

@pytest.fixture
def win(app, tmp_path, monkeypatch):
    from pdfannotator.main_window import MainWindow

    monkeypatch.setattr(anim, "SPEED", 25.0)
    monkeypatch.setattr(brain, "KEY_FILE", tmp_path / "key.bin")
    for var in ("ANTHROPIC_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    src = tmp_path / "lease.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 90), "Lease agreement", fontsize=20)
    page.insert_text((72, 140), "Monthly rent is 500 dollars.", fontsize=12)
    doc.save(src)
    w = MainWindow()
    w.resize(1200, 860)
    w.show()
    w.current_tab().load(str(src))
    w.aupedia.set_shown(True)
    app.processEvents()
    yield w
    w.aupedia.stop_listening()
    w.aupedia.stop()
    if w.aupedia.bubble is not None:
        w.aupedia.bubble.close()
    w.current_tab().document._dirty = False
    w.close()


def test_say_it_and_it_happens(app, win, speech_dir, tmp_path, quiet_mic):
    """Mic on, "highlight monthly rent" said out loud, and it's highlighted (offline: no AI needed)."""
    audio = say("Highlight monthly rent", tmp_path / "s.wav")
    a = win.aupedia
    a.toggle_listening()
    assert wait(app, lambda: a.listening() and a.listener.status == "listening", 20)
    assert a.bubble.mic_btn.isChecked() and a.mascot.listening and win.act_talk_aupedia.isChecked()
    assert not a.chat_open()                                                  # listening doesn't pop the chat up
    heard = []
    a.listener.partial.connect(heard.append)
    for c in chunks(audio):
        a.listener.feed(c)
    assert wait(app, lambda: a.mini.text.startswith("🎤 highlight"), 10)   # live, over its head
    for c in chunks(SILENCE):
        a.listener.feed(c)
    page = win.current_tab().document.page(0)
    assert wait(app, lambda: done_talking(win) and any(x.type[1] == "Highlight" for x in page.annots()), 20)
    assert "\U0001F3A4 highlight monthly rent" in bubble_text(win) and a.bubble.input.text() == ""
    a.toggle_listening()
    assert not a.listening() and not a.bubble.mic_btn.isChecked() and not a.mascot.listening


def test_spoken_questions_tell_the_ai_they_were_spoken(app, win):
    a = win.aupedia
    seen = []
    a.provider = _claude_with([_message([{"type": "text", "text": "Which page: this one, or all of them?"}],
                                        "end_turn")], seen)
    a.open_bubble()
    a._heard("highlight the important bits")
    assert wait(app, lambda: done_talking(win) and "Which page" in bubble_text(win), 15)
    question = seen[0]["body"]["messages"][0]["content"]
    assert "highlight the important bits" in question and "<input>spoken</input>" in question
    assert "misheard" in seen[0]["body"]["system"][0]["text"]                   # it knows to allow for that


def test_answering_and_stopping_out_loud(app, win):
    a = win.aupedia
    a.open_bubble()
    tabs = win.tabs.count()
    a.ask("close all tabs", "do")
    assert wait(app, lambda: a.bubble.waiting_for_answer())
    a._heard("no please don't")                                               # "no" wins over "please"
    assert wait(app, lambda: done_talking(win) and "left it alone" in bubble_text(win))
    assert win.tabs.count() == tabs
    a._heard("the")                                                           # what silence sounds like: ignored
    assert "\U0001F3A4 the" not in bubble_text(win)

    a.bubble.show_btn.setChecked(True)                                        # held questions follow the mode
    a.ask("where is the pen", "show")
    assert a.bubble.busy
    a._heard("convert this to word")                                          # said while it's busy: held...
    assert a._pending_spoken == "convert this to word"
    assert wait(app, lambda: "\U0001F3A4 convert this to word" in bubble_text(win) and done_talking(win), 15)
    a.ask("where is the highlighter", "show")                                 # ...and "stop" stops it
    a._heard("stop")
    assert not a.bubble.busy
    assert wait(app, lambda: "OK, stopped." in bubble_text(win))
