"""AUPedea's offline brain: a model running on this computer with Ollama,
played here by a fake Ollama (its real chat API shapes: tool calls without
ids, arguments as objects, tool results by name, thinking switched off).

    python -m pytest tests/test_aupedia_local.py
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))

import pymupdf as fitz
import pytest
from PySide6.QtWidgets import QApplication

from pdfannotator.aupedia import anim, brain, catalog
from pdfannotator.cloud.google_auth import ApiError, Offline
from test_aupedia import bubble_text, done_talking, wait


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class FakeOllama:
    """Answers like Ollama on localhost:11434."""

    def __init__(self, chats=(), models=(("qwen3:4b", 2.5e9),), caps=("completion", "tools", "thinking"),
                 running=True):
        self.chats, self.models, self.caps, self.is_running = list(chats), list(models), list(caps), running
        self.calls = []

    def __call__(self, method, url, params=None, data=None, headers=None, timeout=None):
        path = url.split("11434", 1)[1]
        body = json.loads(data) if data else None
        self.calls.append({"method": method, "path": path, "body": body, "timeout": timeout})
        if not self.is_running:
            raise Offline("Can't reach localhost")
        if path == "/api/version":
            reply = {"version": "0.12.0"}
        elif path == "/api/tags":
            reply = {"models": [{"name": n, "model": n, "size": size} for n, size in self.models]}
        elif path == "/api/show":
            reply = {"capabilities": self.caps}
        elif path == "/api/generate":
            reply = {"done": True}
        elif path == "/api/chat":
            reply = self.chats.pop(0)
            if isinstance(reply, Exception):
                raise reply
        else:
            raise ApiError(404, "not found")
        return 200, {}, json.dumps(reply).encode()

    def chat_bodies(self):
        return [c["body"] for c in self.calls if c["path"] == "/api/chat"]


def said(content="", tool_calls=None, done_reason="stop"):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = [{"function": {"name": n, "arguments": a}} for n, a in tool_calls]
    return {"model": "qwen3:4b", "message": msg, "done": True, "done_reason": done_reason}


@pytest.fixture
def win(app, tmp_path, monkeypatch):
    from pdfannotator.main_window import MainWindow

    monkeypatch.setattr(anim, "SPEED", 25.0)
    monkeypatch.setattr(brain, "KEY_FILE", tmp_path / "key.bin")
    for var in ("ANTHROPIC_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    src = tmp_path / "lease.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 140), "Monthly rent is 500 dollars.", fontsize=12)
    doc.save(src)
    w = MainWindow()
    w.resize(1200, 860)
    w.show()
    w.current_tab().load(str(src))
    w.aupedia.set_shown(True)
    app.processEvents()
    yield w
    w.aupedia.stop()
    if w.aupedia.bubble is not None:
        w.aupedia.bubble.close()
    w.current_tab().document._dirty = False
    w.close()


def test_a_lean_prompt_for_a_small_model(win):
    cat = catalog.Catalog(win)
    full, lean = brain.system_prompt(cat), brain.system_prompt(cat, lean=True)
    assert "# The guide" in full and "# The guide" not in lean                 # the long guide stays out
    assert "file/convert_to_word" not in lean and len(lean) < 1500            # no list of 130 commands to get lost in
    assert "only tool names" in lean and "never a tool name" in lean
    found = brain.shortlist(cat, "convert this to word")
    assert found[0].id == "file/convert_to_word" and len(found) <= brain.LEAN_SHORTLIST
    assert "file/convert_to_word: File > Convert to Word" in brain.lean_commands(found)
    assert brain.lean_commands(brain.shortlist(cat, "hello, who are you?")) == ""
    turn = brain.user_turn("convert this to word", "show", "state", commands=brain.lean_commands(found))
    assert "<commands>\nfile/convert_to_word: File > Convert to Word" in turn


def test_a_small_model_that_answers_in_words_still_gets_it_done(win):
    """Measured with qwen3:1.7b: it often writes the call out ("point_at view/zoom_in") or says "Pointed at
    View > Zoom In." without calling anything, or gives nothing at all (an id used as a tool name, which
    Ollama drops)."""
    cat = catalog.Catalog(win)
    ops = brain.recover(cat, "How do I highlight text?", "show", "Pointed at Tools > Highlight.")
    assert ops[0][0] == "point" and ops[0][1].id == "tools/highlight" and ops[-1] == ("say", "It's Tools > Highlight.")
    ops = brain.recover(cat, "Save the file", "do", "clicked file/save")
    assert ops[0][0] == "click" and ops[0][1].id == "file/save" and ops[-1] == ("say", "Done: **File > Save**")
    ops = brain.recover(cat, "Zoom in", "do", "point_at view/zoom_in")                  # a call written out
    assert ops[0][0] == "click" and ops[0][1].id == "view/zoom_in" and "point_at" not in ops[-1][1]
    ops = brain.recover(cat, "convert this to word", "show", "")                       # nothing: the finder answers
    assert ops[0][0] == "point" and ops[0][1].id == "file/convert_to_word"
    ops = brain.recover(cat, "Save the file", "do", "Sure! Edit > Redo will do it.")   # not on its shortlist
    assert all(op[0] == "say" for op in ops)
    dark = cat.get("view/dark_mode")
    if not dark.action.isChecked():
        ops = brain.recover(cat, "turn off dark mode", "do", "click view/dark_mode")    # already off: not pressed
        assert ops[0][0] == "point" and "already off" in ops[-1][1]
    assert brain.tidy(cat, "Read it.\n[72,127,215,144] Monthly rent\nNow: Open tabs: [a.pdf]") == "Read it.\nMonthly rent"


def test_a_small_model_doesnt_claim_what_it_didnt_do():
    assert "couldn't mark that up" in brain.honest('The word "rent" was highlighted.', changed=False)
    assert brain.honest('The word "rent" was highlighted.', changed=True) == 'The word "rent" was highlighted.'
    assert brain.honest("It's under Tools > Highlight.", changed=False) == "It's under Tools > Highlight."


def test_a_small_model_leaves_plain_markup_to_the_word_finder(app, win):
    a = win.aupedia
    fake = FakeOllama()
    a.provider = brain.Ollama("qwen3:1.7b", transport=fake)
    a.open_bubble()
    a.ask("highlight 500 dollars", "do")
    assert wait(app, lambda: done_talking(win) and "Marked" in bubble_text(win), 20)
    assert [x.type[1] for x in win.current_tab().document.page(0).annots()] == ["Highlight"]
    assert fake.chat_bodies() == []                                          # quick, and the words are exact


def test_a_small_model_only_presses_what_was_shortlisted(app, win):
    a = win.aupedia
    fake = FakeOllama([said("", [("click", {"target": "edit/redo", "note": "Redo"})]), said("Done.")])
    a.provider = brain.Ollama("qwen3:1.7b", transport=fake)
    pressed = []
    a.press = lambda gen, cmd, note, then: (pressed.append(cmd.id), then("Clicked."))
    a.open_bubble()
    a.ask("Save the file", "do")
    assert wait(app, lambda: done_talking(win) and len(fake.chat_bodies()) == 2, 20)
    first, second = fake.chat_bodies()
    assert first["messages"][1:5] == brain.LEAN_EXAMPLE and "<commands>\nfile/save" in first["messages"][-1]["content"]
    assert pressed == [] and "isn't one of the commands listed" in second["messages"][-1]["content"]


def test_works_on_the_page_with_a_model_on_this_computer(app, win):
    a = win.aupedia
    fake = FakeOllama([
        said("", [("mark_text", {"page": 1, "text": "500 dollars", "style": "highlight"})]),
        said("Highlighted the rent for you."),
    ])
    a.provider = brain.Ollama("qwen3:4b", transport=fake)
    a.open_bubble()
    assert "on this computer: qwen3:4b" in a.bubble.status.toolTip()
    a.ask("mark the rent in yellow", "do")
    assert wait(app, lambda: a.mini.text.startswith("Thinking on this computer"), 5)
    assert wait(app, lambda: done_talking(win) and "Highlighted the rent" in bubble_text(win), 20)
    page = win.current_tab().document.page(0)
    assert [x.type[1] for x in page.annots()] == ["Highlight"]
    first, second = fake.chat_bodies()
    assert first["model"] == "qwen3:4b" and first["stream"] is False and first["think"] is False
    assert first["options"]["num_ctx"] == brain.OLLAMA_CONTEXT and first["keep_alive"]
    assert first["messages"][0]["role"] == "system" and "# The guide" not in first["messages"][0]["content"]
    assert {t["function"]["name"] for t in first["tools"]} >= {"click", "mark_text", "draw"}
    assert second["messages"][-2]["tool_calls"][0]["function"]["arguments"] == {
        "page": 1, "text": "500 dollars", "style": "highlight"}                 # its turn went back as it was
    result = second["messages"][-1]
    assert result["role"] == "tool" and result["tool_name"] == "mark_text" and "Marked" in result["content"]
    assert [c["timeout"] for c in fake.calls if c["path"] == "/api/chat"] == [900, 900]   # a laptop can be slow


def test_no_thinking_switch_for_models_that_dont_think():
    fake = FakeOllama([said("Hi!")], caps=("completion", "tools"))
    prov = brain.Ollama("llama3.2:3b", transport=fake)
    reply = prov.call("system", [{"role": "user", "content": "hi"}])
    assert reply.texts == ["Hi!"] and reply.stop == "end"
    assert "think" not in fake.chat_bodies()[0]


def test_gpt_oss_thinks_low_and_gemma_not_at_all():
    """gpt-oss ignores "think": false (it always reasons), so it's asked for "low", with room in the reply for
    that reasoning; Gemma 4 switches thinking off like Qwen."""
    fake = FakeOllama([said("", [("point_at", {"target": "view/zoom_in"})])])
    brain.Ollama("gpt-oss:20b", transport=fake).call("system", [{"role": "user", "content": "zoom?"}])
    body = fake.chat_bodies()[0]
    assert body["think"] == "low" and body["options"]["num_predict"] > brain.OLLAMA_MAX_REPLY
    fake = FakeOllama([said("Hi!")])
    brain.Ollama("gemma4:e2b-it-qat", transport=fake).call("system", [{"role": "user", "content": "hi"}])
    body = fake.chat_bodies()[0]
    assert body["think"] is False and body["options"]["num_predict"] == brain.OLLAMA_MAX_REPLY


def test_gemma_and_gpt_models_are_offered(app, tmp_path, monkeypatch):
    from pdfannotator.aupedia.panel import AupediaSettingsDialog

    monkeypatch.setattr(brain, "KEY_FILE", tmp_path / "key.bin")
    names = [m for m, _d in brain.OLLAMA_MODELS]
    assert {"gemma4:e2b-it-qat", "gemma4:e4b", "gpt-oss:20b"} <= set(names)
    assert all(m in brain.OLLAMA_NEEDS_GB for m in names)
    try:
        dlg = AupediaSettingsDialog(True, local_fetch=lambda: [], local_caps=lambda m: {"completion", "tools"})
        dlg.use_local.setChecked(True)
        assert wait(app, lambda: dlg.local_running is True)
        labels = [dlg.local_model.itemText(i) for i in range(dlg.local_model.count())]
        assert "gemma4:e2b-it-qat  (download, 4.3 GB)" in labels and "gpt-oss:20b  (download, 14 GB)" in labels
        dlg.local_model.setCurrentIndex(dlg.local_model.findData("gpt-oss:20b"))
        assert "OpenAI" in dlg.local_note.text() and "reasons a little" in dlg.local_note.text()
        assert dlg.pull_btn.isEnabled()
    finally:
        brain.save_choices(provider=brain.CLAUDE, local_model=brain.OLLAMA_DEFAULT, local_vision=False)


def test_downloading_ollamas_installer(tmp_path):
    class Stream:
        headers = {"Content-Length": "3000000"}

        def __init__(self, size):
            self.left = size

        def read(self, n):
            n = min(n, self.left)
            self.left -= n
            return b"x" * n

    seen = []
    path = brain.download_ollama_setup(seen.append, folder=str(tmp_path), open_stream=lambda: Stream(3000000))
    assert path.endswith("OllamaSetup.exe") and os.path.getsize(path) == 3000000 and seen[-1] == 1.0
    with pytest.raises(Offline):                      # cut off part way: no half installer left to run
        brain.download_ollama_setup(folder=str(tmp_path / "x"), open_stream=lambda: Stream(1000))


def test_get_ollama_installs_it_then_notices(app, tmp_path, monkeypatch):
    from pdfannotator.aupedia.panel import AupediaSettingsDialog

    monkeypatch.setattr(brain, "KEY_FILE", tmp_path / "key.bin")
    monkeypatch.setattr(sys, "platform", "win32")
    state = {"installed": False}
    ran = []

    def fetch():
        if not state["installed"]:
            raise brain.OllamaNotRunning()
        return []

    def setup_download(progress):
        progress(0.5)
        progress(1.0)
        return str(tmp_path / "OllamaSetup.exe")

    try:
        dlg = AupediaSettingsDialog(True, local_fetch=fetch, setup_download=setup_download, run_setup=ran.append)
        dlg.use_local.setChecked(True)
        assert wait(app, lambda: dlg.local_running is False)
        dlg.install_btn.click()
        assert wait(app, lambda: ran == [str(tmp_path / "OllamaSetup.exe")])
        assert "Installing Ollama" in dlg.ollama_state.text() and not dlg.install_btn.isEnabled()
        state["installed"] = True                        # the installer finished and started Ollama
        assert wait(app, lambda: dlg.local_running is True, 15)
        assert "Running" in dlg.ollama_state.text() and dlg._setup_timer is None
    finally:
        brain.save_choices(provider=brain.CLAUDE, local_model=brain.OLLAMA_DEFAULT, local_vision=False)


def test_says_when_it_isnt_running_or_is_too_slow(app, win):
    a = win.aupedia
    a.provider = brain.Ollama("qwen3:4b", transport=FakeOllama(running=False))
    a.open_bubble()
    a.ask("hello", "do")
    assert wait(app, lambda: done_talking(win) and "Install Ollama from ollama.com" in bubble_text(win), 15)
    slow = FakeOllama([Offline("timed out")])
    a.provider = brain.Ollama("qwen3:4b", transport=slow)
    a.ask("hello again", "do")
    assert wait(app, lambda: done_talking(win) and "took too long" in bubble_text(win), 15)
    missing = FakeOllama([ApiError(404, "model 'qwen3:4b' not found, try pulling it first")])
    a.provider = brain.Ollama("qwen3:4b", transport=missing)
    a.ask("and again", "do")
    assert wait(app, lambda: done_talking(win) and "isn't downloaded yet" in bubble_text(win), 15)


def test_chosen_without_any_key(tmp_path, monkeypatch):
    monkeypatch.setattr(brain, "KEY_FILE", tmp_path / "key.bin")
    for var in ("ANTHROPIC_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    try:
        brain.save_choices(provider=brain.OLLAMA, local_model="llama3.2:3b", local_vision=False)
        prov = brain.make_provider()
        assert isinstance(prov, brain.Ollama) and prov.model == "llama3.2:3b" and prov.lean
    finally:
        brain.save_choices(provider=brain.CLAUDE, local_model=brain.OLLAMA_DEFAULT, local_vision=False)


def test_downloading_a_model_reports_progress():
    lines = [b'{"status":"pulling manifest"}\n',
             b'{"status":"pulling abc","digest":"abc","total":1000,"completed":250}\n',
             b'{"status":"pulling abc","digest":"abc","total":1000,"completed":1000}\n',
             b'{"status":"success"}\n']
    seen = []
    brain.ollama_pull("qwen3:4b", lambda f, s: seen.append((f, s)), open_stream=lambda: iter(lines))
    assert seen[0] == (None, "pulling manifest") and (0.25, "pulling abc") in seen and seen[-1][1] == "success"
    with pytest.raises(ApiError):
        brain.ollama_pull("nope:1b", open_stream=lambda: iter([b'{"error":"pull model manifest: file does not exist"}']))


def test_settings_find_download_and_choose_a_local_model(app, tmp_path, monkeypatch):
    from pdfannotator.aupedia.panel import AupediaSettingsDialog

    monkeypatch.setattr(brain, "KEY_FILE", tmp_path / "key.bin")
    installed = [("qwen3:4b", 2.5)]
    pulled = []

    def pull(name, progress):
        progress(0.5, "pulling")
        progress(1.0, "pulling")
        pulled.append(name)
        installed.append((name, 2.0))

    try:
        dlg = AupediaSettingsDialog(True, local_fetch=lambda: list(installed),
                                    local_caps=lambda m: {"completion", "tools", "vision"} if m == "qwen3:4b" else
                                    {"completion", "tools"}, local_pull=pull)
        dlg.use_local.setChecked(True)
        assert dlg.local_box.isVisibleTo(dlg) and not dlg.hf_box.isVisibleTo(dlg)
        assert wait(app, lambda: dlg.local_running is True)
        assert "Running, with 1 model" in dlg.ollama_state.text() and not dlg.install_btn.isVisibleTo(dlg)
        dlg.local_model.setCurrentIndex(dlg.local_model.findData("qwen3:4b"))      # the one that's downloaded
        assert wait(app, lambda: dlg.local_vision.get("qwen3:4b") is True)
        assert dlg.local_model.itemText(0).startswith("✓ qwen3:4b")
        assert not dlg.pull_btn.isEnabled() and "see pictures" in dlg.local_note.text()
        dlg.local_model.setCurrentIndex(dlg.local_model.findData("llama3.2:3b"))
        assert dlg.pull_btn.isEnabled() and "Not downloaded yet" in dlg.local_note.text()
        dlg.download_local()
        assert wait(app, lambda: pulled == ["llama3.2:3b"] and dlg._is_installed("llama3.2:3b"))
        assert wait(app, lambda: dlg.local_model_id() == "llama3.2:3b" and not dlg.pull_btn.isEnabled())
        dlg.accept()
        c = brain.choices()
        assert c["provider"] == brain.OLLAMA and c["local_model"] == "llama3.2:3b"

        def missing():
            raise brain.OllamaNotRunning()

        dlg = AupediaSettingsDialog(True, local_fetch=missing)
        assert dlg.use_local.isChecked()                                       # it remembers the choice
        assert wait(app, lambda: dlg.local_running is False)
        assert "isn't installed" in dlg.ollama_state.text() and dlg.install_btn.isVisibleTo(dlg)
        assert not dlg.pull_btn.isEnabled() and "Click Get Ollama" in dlg.local_note.text()
    finally:
        brain.save_choices(provider=brain.CLAUDE, local_model=brain.OLLAMA_DEFAULT, local_vision=False)


# ---------------------------------------------------------------------------
# keeping it light: a laptop's memory is the limit
# ---------------------------------------------------------------------------

def test_warming_up_asks_for_the_same_context_as_answering():
    """A different context size makes Ollama load the whole model again: slow, and slower when memory's tight."""
    fake = FakeOllama([said(""), said("Hi!")])
    prov = brain.Ollama("qwen3:4b", transport=fake)
    prov.warm_up()
    prov.call(brain.LEAN_PERSONA, brain.LEAN_EXAMPLE + [{"role": "user", "content": "hi"}])
    warm, ask = fake.chat_bodies()
    assert warm["options"] == {**ask["options"], "num_predict": 1} and warm["options"]["num_ctx"] == brain.OLLAMA_CONTEXT
    # ...and it reads what every question starts with (instructions, tools, example), so the first is quick too
    assert ask["messages"][:len(warm["messages"])] == warm["messages"] and warm["tools"] == ask["tools"]


def test_small_models_get_slim_tools():
    full, lean = brain.TOOLS, brain.lean_tools()
    assert [t["name"] for t in lean] == [t["name"] for t in full]
    assert len(json.dumps(lean)) < len(json.dumps(full)) * 0.75
    assert all("description" not in p for t in lean for p in t["parameters"]["properties"].values())
    shape = next(t for t in lean if t["name"] == "shape")
    assert shape["parameters"]["properties"]["kind"]["enum"] == ["rect", "ellipse", "line", "arrow"]   # rules kept


def test_each_local_question_starts_afresh_and_reads_less(app, win, monkeypatch):
    a = win.aupedia
    monkeypatch.setattr(brain, "memory_gb", lambda: (16.0, 9.0))
    fake = FakeOllama([said("", [("read_page", {"page": 1})]), said("It's about rent."), said("You're welcome!")])
    a.provider = brain.Ollama("qwen3:4b", transport=fake)
    a.open_bubble()
    a.ask("what's this page about?", "show")
    assert wait(app, lambda: done_talking(win) and "about rent" in bubble_text(win), 20)
    a.ask("thanks", "show")
    assert wait(app, lambda: done_talking(win) and "welcome" in bubble_text(win), 20)
    last = fake.chat_bodies()[-1]["messages"]
    assert [m["role"] for m in last] == ["system", "user"] and "thanks" in last[1]["content"]   # no old turns
    tab = win.current_tab()
    from pdfannotator.aupedia import page_tools
    assert len(page_tools.read_page(tab, 0, limit=200)) < 400


def test_warns_when_memory_is_too_tight(app, win, monkeypatch):
    a = win.aupedia
    monkeypatch.setattr(brain, "memory_gb", lambda: (8.0, 0.7))
    a.provider = brain.Ollama("qwen3:4b", transport=FakeOllama([said("Done.")]))
    a.open_bubble()
    a.ask("hi", "show")
    assert wait(app, lambda: "memory's tight" in a.mini.text, 5)


def test_recommends_a_model_that_fits_this_computer():
    assert brain.recommended_local_model(8.0) == "qwen3:1.7b"
    assert brain.recommended_local_model(12.0) == "qwen3:4b-instruct"
    assert brain.recommended_local_model(32.0) == "qwen3:8b"
    total, free = brain.memory_gb()
    assert total is None or total > free >= 0


def test_settings_point_out_a_better_fit(app, monkeypatch):
    from pdfannotator.aupedia.panel import AupediaSettingsDialog

    monkeypatch.setattr(brain, "memory_gb", lambda: (8.0, 1.0))
    try:
        dlg = AupediaSettingsDialog(True, local_fetch=lambda: [("qwen3:4b", 2.5)], local_caps=lambda m: {"tools"})
        dlg.use_local.setChecked(True)
        assert wait(app, lambda: dlg.local_running is True)
        dlg.local_model.setCurrentIndex(dlg.local_model.findData("qwen3:4b"))
        assert "8 GB of memory: qwen3:1.7b will be much quicker here" in dlg.local_note.text()
        dlg.local_model.setCurrentIndex(dlg.local_model.findData("qwen3:1.7b"))
        assert "A good fit for this computer" in dlg.local_note.text()
    finally:
        brain.save_choices(provider=brain.CLAUDE, local_model=brain.OLLAMA_DEFAULT, local_vision=False)


def test_reasoning_out_loud_is_kept_out_of_the_answer(app, win, monkeypatch):
    """A "thinking" model (plain qwen3:4b) writes its reasoning first, ending in </think>, even when told not to."""
    assert brain.strip_thinking("Okay, the user wants dark mode. Let me check...\n</think>\n\nIt's **View > Dark Mode**.") \
        == ("It's **View > Dark Mode**.", True)
    assert brain.strip_thinking("<think>still going") == ("", True)
    assert brain.strip_thinking("Just the answer.") == ("Just the answer.", False)
    monkeypatch.setattr(brain, "memory_gb", lambda: (16.0, 9.0))
    a = win.aupedia
    fake = FakeOllama([said("Okay, the user is asking where dark mode is. Let me look.\n</think>\n\nIt's under **View**."),
                       said("Sure!")])
    a.provider = brain.Ollama("qwen3:4b", transport=fake)
    a.open_bubble()
    a.ask("where is dark mode?", "show")
    assert wait(app, lambda: done_talking(win) and "under View" in bubble_text(win), 20)
    assert "Let me look" not in bubble_text(win)                               # its musings never reach the chat
    assert "qwen3:4b-instruct" in bubble_text(win) and "Tip:" in bubble_text(win)   # and the cure, once
    a.ask("thanks", "show")
    assert wait(app, lambda: done_talking(win) and "Sure!" in bubble_text(win), 20)
    assert bubble_text(win).count("Tip:") == 1


def test_a_small_model_isnt_asked_again_after_pointing(app, win):
    """Once it has pointed or pressed (and all went well), the app says so itself: asking the model to write
    "It's under View" takes about as long again on a laptop."""
    a = win.aupedia
    fake = FakeOllama([said("", [("point_at", {"target": "view/zoom_in", "note": "Zoom in"})])])
    a.provider = brain.Ollama("qwen3:1.7b", transport=fake)
    a.open_bubble()
    a.ask("how do I zoom in?", "show")
    assert wait(app, lambda: done_talking(win) and "View > Zoom In" in bubble_text(win), 20)
    assert len(fake.chat_bodies()) == 1
    assert fake.chat_bodies()[0]["options"]["num_predict"] == brain.OLLAMA_MAX_REPLY      # no rambling


def test_warming_up_lets_go_of_other_models():
    """Two models at once don't fit a laptop's memory (measured: minutes an answer while qwen3:4b, chosen
    before, was still loaded beside qwen3:1.7b)."""
    class WithOthers(FakeOllama):
        def __call__(self, method, url, params=None, data=None, headers=None, timeout=None):
            if url.endswith("/api/ps"):
                self.calls.append({"method": method, "path": "/api/ps", "body": None, "timeout": timeout})
                return 200, {}, json.dumps({"models": [{"name": "qwen3:4b"}, {"name": "qwen3:1.7b"}]}).encode()
            return super().__call__(method, url, params, data, headers, timeout)

    fake = WithOthers([said("")])
    brain.Ollama("qwen3:1.7b", transport=fake).warm_up()
    unloaded = [c["body"]["model"] for c in fake.calls if c["path"] == "/api/generate" and c["body"].get("keep_alive") == 0]
    assert unloaded == ["qwen3:4b"]
