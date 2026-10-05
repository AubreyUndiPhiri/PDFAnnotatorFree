"""AUPedea on the paper: reading pages, marking text up, writing and drawing
(each animated with the nib, then a real annotation that Undo removes), with
Claude (over a mock HTTP transport), with Hugging Face (over a fake router),
and offline; plus choosing the service and model in its settings.

    python -m pytest tests/test_aupedia_paper.py
"""
import base64
import json
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))

import pymupdf as fitz
import pytest
from PySide6.QtWidgets import QApplication

from pdfannotator.aupedia import anim, brain, page_tools
from pdfannotator.cloud.google_auth import ApiError
from test_aupedia import _claude_with, _message, bubble_text, done_talking, wait


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


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
    page.insert_text((72, 170), "Tenant: Sam Banda", fontsize=12)
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


def annots(win, kind=None):
    page = win.current_tab().document.page(0)
    return [a for a in page.annots() if kind is None or a.type[1] == kind]


# ---------------------------------------------------------------------------
# the page itself
# ---------------------------------------------------------------------------

def test_reading_a_page(win):
    tab = win.current_tab()
    text = page_tools.read_page(tab, 0)
    assert text.startswith("Page 1 of 1: 595 x 842 points")
    line = next(l for l in text.splitlines() if "Lease agreement" in l)
    x0, y0, x1, y1 = (float(v) for v in line[1:line.index("]")].split(","))
    assert 70 < x0 < 74 and 60 < y0 < 90 and x1 > 200                # where the title is, in points
    png = page_tools.page_png(tab, 0)
    assert png.startswith(b"\x89PNG")
    with pytest.raises(page_tools.PageError, match="isn't on page 1"):
        page_tools.find_text(tab, 0, "not in this lease")
    with pytest.raises(page_tools.PageError, match="no page 3"):
        page_tools.page_index(tab, 3)


def test_offline_highlight_then_undo(app, win):
    a = win.aupedia
    a.open_bubble()
    a.ask('highlight "Monthly rent"', "do")
    assert wait(app, lambda: done_talking(win) and "Done!" in bubble_text(win))
    (hl,) = annots(win, "Highlight")
    assert hl.rect.y0 > 120 and hl.rect.x0 < 80
    assert not a.ink.wet or all(w["fade"] is not None for w in a.ink.wet)    # the wet ink dried away
    win.undo()
    assert not annots(win, "Highlight")


def test_offline_circle_and_read(app, win):
    a = win.aupedia
    a.open_bubble()
    a.ask("circle Tenant", "do")
    assert wait(app, lambda: done_talking(win) and annots(win, "Ink"))
    a.ask("read this page", "do")
    assert wait(app, lambda: done_talking(win) and "This page has 3 lines" in bubble_text(win))
    a.ask("highlight Lease agreement", "show")                          # show me: explained, not done
    assert wait(app, lambda: done_talking(win) and "Do it for me" in bubble_text(win))
    assert not annots(win, "Highlight")


# ---------------------------------------------------------------------------
# with Claude
# ---------------------------------------------------------------------------

def test_claude_reads_then_writes_draws_and_marks(app, win):
    a = win.aupedia
    seen = []
    a.provider = _claude_with([
        _message([{"type": "tool_use", "id": "toolu_r", "name": "read_page", "input": {"page": 0}}], "tool_use"),
        _message([
            {"type": "text", "text": "Let me mark it up!"},
            {"type": "tool_use", "id": "toolu_m", "name": "mark_text",
             "input": {"page": 1, "text": "500 dollars", "style": "underline"}},
            {"type": "tool_use", "id": "toolu_w", "name": "write_text",
             "input": {"page": 1, "x": 380, "y": 130, "text": "Due on the 1st", "size": 16}},
            {"type": "tool_use", "id": "toolu_d", "name": "draw",
             "input": {"page": 1, "strokes": [[[500, 60], [510, 75], [530, 45]]], "color": "green"}},
            {"type": "tool_use", "id": "toolu_s", "name": "shape",
             "input": {"page": 1, "kind": "rect", "x0": 66, "y0": 70, "x1": 260, "y1": 100}},
        ], "tool_use"),
        _message([{"type": "text", "text": "All marked up for you."}], "end_turn"),
    ], seen)
    a.open_bubble()
    wet_seen, moods = [], set()
    poll = lambda: wet_seen.append(len(a.ink.wet)) or moods.add(a.mascot.mood) or done_talking(win)  # noqa: E731
    a.ask("mark up the rent and add a note", "do")
    assert wait(app, lambda: poll() and "All marked up" in bubble_text(win), 25)
    assert max(wet_seen) >= 1 and "drawing" in moods and "reading" in moods   # it drew, and read, on screen
    # the page picture went back with the text
    read_result = seen[1]["body"]["messages"][-1]["content"][0]
    assert read_result["tool_use_id"] == "toolu_r"
    kinds = [c["type"] for c in read_result["content"]]
    assert kinds == ["text", "image"] and "Lease agreement" in read_result["content"][0]["text"]
    # everything is really on the page
    assert len(annots(win, "Underline")) == 1
    (note,) = annots(win, "FreeText")
    assert "Due on the 1st" in note.info["content"]
    assert annots(win, "Ink") and annots(win, "Square")
    results = {r["tool_use_id"]: r for r in seen[2]["body"]["messages"][-1]["content"]}
    assert set(results) == {"toolu_m", "toolu_w", "toolu_d", "toolu_s"} and not any(r.get("is_error") for r in results.values())
    assert "Marked" in results["toolu_m"]["content"] and "Wrote" in results["toolu_w"]["content"]
    for _ in range(4):                                                  # four undo steps, one per call
        win.undo()
    assert not annots(win)


def test_show_me_never_changes_the_paper(app, win):
    a = win.aupedia
    seen = []
    a.provider = _claude_with([
        _message([{"type": "tool_use", "id": "toolu_x", "name": "draw",
                   "input": {"page": 1, "strokes": [[[10, 10], [50, 50]]]}}], "tool_use"),
        _message([{"type": "text", "text": "Pick the **Pen** tool and draw."}], "end_turn"),
    ], seen)
    a.open_bubble()
    a.ask("how would I draw a line?", "show")
    assert wait(app, lambda: done_talking(win) and "Pen" in bubble_text(win), 15)
    result = seen[1]["body"]["messages"][-1]["content"][0]
    assert result.get("is_error") and "Explain how instead" in result["content"]
    assert not annots(win)


def test_stop_while_drawing_leaves_nothing(app, win, monkeypatch):
    monkeypatch.setattr(anim, "SPEED", 1.0)
    a = win.aupedia
    a.mascot.t = a.ink.t = anim.now()
    tab = win.current_tab()
    results = []
    a.draw(a.gen, tab, 0, [[(100, 300), (500, 300), (500, 700), (100, 700)]], None, 2, lambda *r, **k: results.append(r))
    assert wait(app, lambda: a.mascot.tracing is not None, 5)
    a.stop()
    wait(app, lambda: False, 0.4)
    assert not results and not annots(win) and not a.mascot.pen


# ---------------------------------------------------------------------------
# with Hugging Face
# ---------------------------------------------------------------------------

class FakeRouter:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, method, url, params=None, data=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "body": json.loads(data) if data else None,
                           "headers": headers or {}})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return 200, {}, json.dumps(reply).encode()


def _chat(content=None, tool_calls=None, finish="stop"):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = [{"id": f"call_{i}", "type": "function",
                              "function": {"name": name, "arguments": json.dumps(args)}}
                             for i, (name, args) in enumerate(tool_calls)]
    return {"id": "x", "object": "chat.completion", "model": "m",
            "choices": [{"index": 0, "finish_reason": finish, "message": msg}]}


def test_huggingface_reads_and_marks_up(app, win):
    a = win.aupedia
    router = FakeRouter([
        _chat(None, [("read_page", {"page": 1})], "tool_calls"),
        _chat("On it.", [("mark_text", {"page": 1, "text": "Sam Banda", "style": "highlight", "color": "#a3e635"})],
              "tool_calls"),
        _chat("Highlighted the tenant's name."),
    ])
    a.provider = brain.HuggingFace("hf_test", "Qwen/Qwen3.6-35B-A3B", vision=True, transport=router)
    a.open_bubble()
    assert "Hugging Face" in a.bubble.status.toolTip() and "Qwen3.6" in a.bubble.status.toolTip()
    a.ask("highlight who the tenant is", "do")
    assert wait(app, lambda: done_talking(win) and "tenant's name" in bubble_text(win), 20)
    assert len(annots(win, "Highlight")) == 1
    first = router.calls[0]
    assert first["url"] == "https://router.huggingface.co/v1/chat/completions"
    assert first["headers"]["Authorization"] == "Bearer hf_test"
    body = first["body"]
    assert body["model"] == "Qwen/Qwen3.6-35B-A3B" and body["messages"][0]["role"] == "system"
    assert "file/convert_to_word" in body["messages"][0]["content"]
    assert {t["function"]["name"] for t in body["tools"]} >= {"read_page", "mark_text", "draw"}
    assert all(t["type"] == "function" for t in body["tools"])
    second = router.calls[1]["body"]["messages"]
    assert second[-3]["role"] == "assistant" and second[-3]["tool_calls"][0]["function"]["name"] == "read_page"
    assert second[-2] == {"role": "tool", "tool_call_id": "call_0", "content": second[-2]["content"]}
    assert "Monthly rent" in second[-2]["content"]
    picture = second[-1]                                               # it can see: the page follows as a picture
    assert picture["role"] == "user" and picture["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert router.calls[2]["body"]["messages"][-1]["role"] == "tool"
    assert [m["role"] for m in a.history][-1] == "assistant"


def test_huggingface_errors_and_bad_arguments(app, win):
    a = win.aupedia
    router = FakeRouter([ApiError(401, "Unauthorized", body=b'{"error": "Invalid credentials"}')])
    a.provider = brain.HuggingFace("hf_bad", transport=router)
    a.open_bubble()
    a.ask("hi", "do")
    assert wait(app, lambda: done_talking(win) and "didn't accept the token" in bubble_text(win), 15)
    router = FakeRouter([
        {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": "",
                      "tool_calls": [{"id": "c1", "type": "function",
                                      "function": {"name": "draw", "arguments": "{not json"}}]}}]},
        _chat("Sorry about that."),
    ])
    a.provider = brain.HuggingFace("hf_ok", transport=router)
    a.ask("draw something", "do")
    assert wait(app, lambda: done_talking(win) and "Sorry about that" in bubble_text(win), 15)
    tool_msg = router.calls[1]["body"]["messages"][-1]
    assert tool_msg["role"] == "tool" and "valid JSON" in tool_msg["content"]


def test_huggingface_model_list():
    listing = {"object": "list", "data": [
        {"id": "org/sees", "architecture": {"input_modalities": ["text", "image"]},
         "providers": [{"provider": "a", "status": "live", "supports_tools": True}]},
        {"id": "org/reads", "architecture": {"input_modalities": ["text"]},
         "providers": [{"provider": "b", "status": "live", "supports_tools": True}]},
        {"id": "org/no-tools", "architecture": {"input_modalities": ["text"]},
         "providers": [{"provider": "c", "status": "live", "supports_tools": False}]},
    ]}
    router = FakeRouter([listing])
    assert brain.hf_models(router) == [("org/sees", True), ("org/reads", False)]
    assert router.calls[0]["url"] == "https://router.huggingface.co/v1/models"


# ---------------------------------------------------------------------------
# choosing the service and model
# ---------------------------------------------------------------------------

def test_settings_choose_huggingface_and_a_model(app, tmp_path, monkeypatch):
    from pdfannotator.aupedia.panel import AupediaSettingsDialog

    monkeypatch.setattr(brain, "KEY_FILE", tmp_path / "key.bin")
    for var in ("ANTHROPIC_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    brain.save_choices(provider=brain.CLAUDE, claude_model=brain.MODEL)
    try:
        dlg = AupediaSettingsDialog(True, hf_fetch=lambda: [("org/sees", True), ("org/reads", False)])
        assert dlg.claude_box.isVisibleTo(dlg) and not dlg.hf_box.isVisibleTo(dlg)
        dlg.use_hf.setChecked(True)
        assert dlg.hf_box.isVisibleTo(dlg) and not dlg.claude_box.isVisibleTo(dlg)
        dlg.hf_token.setText("hf_secret_token")
        dlg.refresh_models()
        assert wait(app, lambda: dlg.hf_model.findData("org/sees") >= 0)
        dlg.hf_model.setCurrentIndex(dlg.hf_model.findData("org/sees"))
        assert "Can see pictures" in dlg.hf_note.text()
        dlg.claude_model.setCurrentIndex(dlg.claude_model.findData("claude-sonnet-5-5"))
        dlg.accept()
        c = {k: v for k, v in brain.choices().items() if not k.startswith("local")}
        assert c == {"provider": brain.HUGGINGFACE, "claude_model": "claude-sonnet-5-5", "hf_model": "org/sees",
                     "hf_vision": True}
        assert brain.api_key(brain.HUGGINGFACE) == "hf_secret_token" and brain.api_key(brain.CLAUDE) == ""
        assert b"hf_secret" not in (tmp_path / "key.bin").read_bytes()
        prov = brain.make_provider()
        assert isinstance(prov, brain.HuggingFace) and prov.model == "org/sees" and prov.vision

        dlg = AupediaSettingsDialog(True, hf_fetch=lambda: [])
        dlg.hf_model.setEditText("someone/custom-model")                  # any model id can be typed in
        assert dlg.hf_model_id() == "someone/custom-model" and "not in the list" in dlg.hf_note.text()
        dlg.use_claude.setChecked(True)
        dlg.claude_key.setText("sk-ant-test")
        dlg.accept()
        prov = brain.make_provider()
        assert isinstance(prov, brain.Claude) and prov.model == "claude-sonnet-5-5"
        brain.forget_key(brain.CLAUDE)
        prov = brain.make_provider()                                       # no Claude key: falls back to the other
        assert isinstance(prov, brain.HuggingFace) and prov.model == "someone/custom-model"
    finally:
        brain.save_choices(provider=brain.CLAUDE, claude_model=brain.MODEL, hf_model=brain.HF_DEFAULT,
                           hf_vision=False)


def test_old_saved_claude_key_still_works(tmp_path, monkeypatch):
    from pdfannotator.cloud.google_auth import save_secret

    monkeypatch.setattr(brain, "KEY_FILE", tmp_path / "key.bin")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    save_secret(tmp_path / "key.bin", {"key": "sk-ant-from-before"})      # how the key was kept until now
    assert brain.api_key(brain.CLAUDE) == "sk-ant-from-before"
    brain.save_key("hf_new", brain.HUGGINGFACE)
    assert brain.api_key(brain.CLAUDE) == "sk-ant-from-before" and brain.api_key(brain.HUGGINGFACE) == "hf_new"


def test_huggingface_messages_explain_themselves():
    assert "out of credit" in brain.friendly_error(ApiError(402, "Payment Required"), brain.HUGGINGFACE)
    assert "Inference Providers" in brain.friendly_error(ApiError(403, "Forbidden"), brain.HUGGINGFACE)
    assert "can't use tools" in brain.friendly_error(
        ApiError(400, "Bad", body=b'{"error": {"message": "this model does not support tools"}}'), brain.HUGGINGFACE)
