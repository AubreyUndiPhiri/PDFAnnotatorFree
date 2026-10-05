"""AUPedia, the scribble helper: the command catalog, the offline finder,
pointing and clicking (menus, toolbar, risky commands, dialogs), the mouse on
the scribble, and the Claude loop, run through the real Anthropic SDK over a
mock HTTP transport (no network, no key needed).

    python -m pytest tests/test_aupedia.py
"""
import json
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pdfannotator.aupedia import anim, brain, catalog
from pdfannotator.aupedia.director import describe_dialog


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app, tmp_path, monkeypatch):
    from pdfannotator.main_window import MainWindow
    from pdfannotator.tools import Tool

    monkeypatch.setattr(anim, "SPEED", 25.0)
    monkeypatch.setattr(brain, "KEY_FILE", tmp_path / "key.bin")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    w.aupedia.set_shown(True)
    app.processEvents()
    w.Tool = Tool
    yield w
    w.aupedia.stop()
    if w.aupedia.bubble is not None:
        w.aupedia.bubble.close()
    w.close()


def wait(app, cond, timeout=8.0):
    deadline = time.time() + timeout
    while not cond() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    return cond()


def bubble_text(w):
    return w.aupedia.bubble.view.toPlainText()


def done_talking(w):
    b = w.aupedia.bubble
    return b is not None and not b.busy and not b.is_typing()


# ---------------------------------------------------------------------------
# the catalog and the offline finder
# ---------------------------------------------------------------------------

def test_catalog_lists_every_command_once(win):
    cat = catalog.Catalog(win, exclude=win.aupedia.own_actions)
    ids = [c.id for c in cat.commands]
    assert len(ids) == len(set(ids)) > 80
    assert cat.get("file/convert_to_word").where() == "File > Convert to Word"
    assert "help/ask_aupedia" not in ids                       # it doesn't list itself
    assert not any(i.startswith("window/") for i in ids)        # the tab list changes all the time
    pen = next(c for c in cat.commands if c.action is win.tool_actions[win.Tool.INK])
    assert pen.toolbars                                          # on the ribbon
    state = cat.state()
    assert "Open tabs:" in state and "Current tool: select" in state
    assert os.sep not in cat.state(brief=True).split("file ")[-1]   # file names only, never folders


@pytest.mark.parametrize("question, expected", [
    ("convert this to word", "file/convert_to_word"),
    ("merge two pdfs", "file/combine_files"),
    ("I want to highlight something", "tools/highlight"),
    ("rotate the page", "edit/rotate_left"),
])
def test_finder_matches_words(win, question, expected):
    cat = catalog.Catalog(win)
    assert catalog.find(cat, question)[0].id == expected


def test_offline_do_it_selects_the_pen(app, win):
    a = win.aupedia
    a.open_bubble()
    assert "offline" in a.bubble.status.text()
    a.ask("pick the pen so I can draw", "do")
    assert wait(app, lambda: win.current_tool == win.Tool.INK and done_talking(win))
    assert "Done" in bubble_text(win) and "Pen" in bubble_text(win)


def test_offline_show_me_points_and_writes_a_note(app, win):
    a = win.aupedia
    a.open_bubble()
    a.ask("where is highlight", "show")
    assert wait(app, lambda: bool(a.ink.captions) and done_talking(win))
    assert win.current_tool != win.Tool.HIGHLIGHT                # shown, not clicked
    rect = a.ink.circles[-1]["pts"]
    btn = win.tool_toolbar.widgetForAction(win.tool_actions[win.Tool.HIGHLIGHT])
    centre = btn.mapTo(win, btn.rect().center())
    xs, ys = [p[0] for p in rect], [p[1] for p in rect]
    assert min(xs) < centre.x() < max(xs) and min(ys) < centre.y() < max(ys)   # circled the right button
    assert a.mascot.look_at is not None


def test_risky_commands_ask_first(app, win):
    a = win.aupedia
    a.open_bubble()
    tabs = win.tabs.count()
    a.ask("close all tabs", "do")
    assert wait(app, lambda: a.bubble.confirm_bar.isVisible())
    assert "Close All" in a.bubble.confirm_text.text()
    a.bubble._answer_confirm(False)
    assert wait(app, lambda: done_talking(win))
    assert win.tabs.count() == tabs and "left it alone" in bubble_text(win)


def test_unknown_words_get_a_kind_answer(app, win):
    a = win.aupedia
    a.open_bubble()
    a.ask("bake me a cake", "do")
    assert wait(app, lambda: done_talking(win) and "couldn't find" in bubble_text(win))


def test_menu_items_open_their_menu(app, win):
    a = win.aupedia
    a.open_bubble()
    cat = catalog.Catalog(win)
    cmd = cat.get("file/save_as")
    seen = []
    a.catalog = cat
    a.show(a.gen, cmd, "here", seen.append)
    assert wait(app, lambda: bool(seen))
    assert "menu is open" in seen[0]
    popup = QApplication.activePopupWidget()
    assert popup is win._file_menu and popup.activeAction() is cmd.action
    a._close_menus()
    assert QApplication.activePopupWidget() is None


# ---------------------------------------------------------------------------
# the scribble itself
# ---------------------------------------------------------------------------

def test_click_opens_the_bubble_and_drag_moves_it(app, win):
    a = win.aupedia
    a.mascot.place(600, 400)
    wait(app, lambda: False, 0.2)
    hit = a.hit
    assert hit.isVisible() and hit.geometry().contains(QPoint(600, 400 + int(a.mascot._bob())))   # on its body
    centre = hit.rect().center()
    QTest.mouseClick(hit, Qt.LeftButton, pos=centre)
    assert wait(app, lambda: a.bubble is not None and a.bubble.isVisible())
    x0, y0 = a.mascot.pos_f
    start = hit.mapTo(win, centre)

    def at(dx, dy):                       # where the cursor is, as the (moving) hit area sees it
        return hit.mapFrom(win, start + QPoint(dx, dy))

    QTest.mousePress(hit, Qt.LeftButton, pos=at(0, 0))
    QTest.mouseMove(hit, at(-120, -60))
    QTest.mouseMove(hit, at(-200, -100))
    QTest.mouseRelease(hit, Qt.LeftButton, pos=at(-200, -100))
    assert abs(a.mascot.pos_f[0] - (x0 - 200)) < 3 and abs(a.mascot.pos_f[1] - (y0 - 100)) < 3
    home = a.home()
    assert abs(home[0] - a.mascot.pos_f[0]) < 3                  # remembered as its new corner
    assert a.bubble.isVisible()                                   # a drag isn't a click


def test_flies_on_an_arc_and_lands(app, win, monkeypatch):
    monkeypatch.setattr(anim, "SPEED", 2.0)                      # slow enough to see the trail frame by frame
    m = win.aupedia.mascot
    m.t = win.aupedia.ink.t = anim.now()
    m.place(200, 600)
    landed = []
    m.fly_to(900, 300, lambda: landed.append(True))
    highest, trail = 600, 0
    deadline = time.time() + 5
    while not landed and time.time() < deadline:
        app.processEvents()
        highest = min(highest, m.pos_f[1])
        trail = max(trail, len(win.aupedia.ink.trail))
        time.sleep(0.002)
    assert landed and (m.pos_f[0], m.pos_f[1]) == (900, 300)
    assert highest < 300                                          # bowed upwards on the way
    assert trail >= 3                                             # and left an ink trail behind it


def test_dialogs_are_described(app):
    from pdfannotator.dialogs import PropertiesDialog

    dlg = PropertiesDialog({"title": "Lease", "_page_count": 3})
    dlg.show()
    text = describe_dialog(dlg)
    assert '"Document Properties"' in text and "Lease" in text and "[Cancel]" in text
    dlg.close()


# ---------------------------------------------------------------------------
# Claude, through the real SDK over a mock transport
# ---------------------------------------------------------------------------

def _message(content, stop_reason, n=[0]):
    n[0] += 1
    return {"id": f"msg_{n[0]}", "type": "message", "role": "assistant", "model": brain.MODEL, "content": content,
            "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5}}


def _claude_with(replies, seen):
    import anthropic
    import httpx2

    def handler(request):
        body = json.loads(request.content)
        seen.append({"body": body, "headers": dict(request.headers)})
        reply = replies.pop(0)
        if isinstance(reply, tuple):
            return httpx2.Response(reply[0], json=reply[1])
        return httpx2.Response(200, json=reply)

    client = anthropic.Anthropic(api_key="sk-ant-test", max_retries=0,
                                 http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    return brain.Claude(client=client)


def test_claude_clicks_then_answers(app, win):
    a = win.aupedia
    seen = []
    cat = catalog.Catalog(win)
    pen_id = next(c.id for c in cat.commands if c.action is win.tool_actions[win.Tool.INK])
    a.claude = _claude_with([
        _message([{"type": "text", "text": "On it!"},
                  {"type": "tool_use", "id": "toolu_1", "name": "click",
                   "input": {"target": pen_id, "note": "your pen"}}], "tool_use"),
        _message([{"type": "text", "text": "Your **pen** is ready. Draw away!"}], "end_turn"),
    ], seen)
    a.open_bubble()
    assert "Claude" in a.bubble.status.text()
    a.ask("I want to draw on the page", "do")
    assert wait(app, lambda: done_talking(win) and "Draw away" in bubble_text(win), 15)
    assert win.current_tool == win.Tool.INK
    first, second = seen
    body = first["body"]
    assert body["model"] == "claude-opus-5-5" and body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in first["headers"].get("anthropic-beta", "")
    assert body["output_config"] == {"effort": "low"}
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "file/convert_to_word: File > Convert to Word" in body["system"][0]["text"]
    assert {t["name"] for t in body["tools"]} == {"point_at", "click"} and all(t["strict"] for t in body["tools"])
    question = body["messages"][0]["content"]
    assert "I want to draw" in question and "<mode>do it</mode>" in question and "Open tabs:" in question
    result = second["body"]["messages"][-1]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "toolu_1"
    assert result["content"].startswith("Clicked ")
    assert second["body"]["messages"][1]["content"][1]["type"] == "tool_use"   # its turn went back unchanged
    assert [m["role"] for m in a.history] == ["user", "assistant", "user", "assistant"]   # kept for follow-ups


def test_claude_show_me_never_clicks(app, win):
    a = win.aupedia
    seen = []
    a.claude = _claude_with([
        _message([{"type": "tool_use", "id": "toolu_9", "name": "click",
                   "input": {"target": "file/close_all", "note": "x"}}], "tool_use"),
        _message([{"type": "text", "text": "It's under **File > Close All**."}], "end_turn"),
    ], seen)
    a.open_bubble()
    tabs = win.tabs.count()
    a.ask("how do I close everything", "show")
    assert wait(app, lambda: done_talking(win) and "Close All" in bubble_text(win), 15)
    result = seen[1]["body"]["messages"][-1]["content"][0]
    assert result.get("is_error") and "Point at it instead" in result["content"]
    assert win.tabs.count() == tabs


def test_claude_reads_the_dialog_it_opened(app, win):
    a = win.aupedia
    seen = []
    a.claude = _claude_with([
        _message([{"type": "tool_use", "id": "toolu_2", "name": "click",
                   "input": {"target": "file/properties", "note": "properties"}}], "tool_use"),
        _message([{"type": "text", "text": "Type the title in the **Title** box, then **Save**."}], "end_turn"),
    ], seen)
    a.open_bubble()
    closer = QTimer()
    closer.timeout.connect(lambda: done_talking(win) and QApplication.activeModalWidget() is not None
                           and QApplication.activeModalWidget().reject())
    closer.start(50)
    a.ask("let me change the title", "do")
    assert wait(app, lambda: done_talking(win) and "Title" in bubble_text(win), 15)
    closer.stop()
    result = seen[1]["body"]["messages"][-1]["content"][0]["content"]
    assert 'A dialog opened: "Document Properties"' in result and "Buttons:" in result


def test_claude_bad_key_and_unknown_ids(app, win):
    a = win.aupedia
    a.claude = _claude_with([(401, {"type": "error", "error": {"type": "authentication_error",
                                                                 "message": "invalid x-api-key"}})], [])
    a.open_bubble()
    a.ask("hello", "do")
    assert wait(app, lambda: done_talking(win) and "didn't accept the API key" in bubble_text(win), 15)
    assert a.history == []

    seen = []
    a.claude = _claude_with([
        _message([{"type": "tool_use", "id": "toolu_3", "name": "point_at",
                   "input": {"target": "file/teleport", "note": "?"}}], "tool_use"),
        _message([{"type": "text", "text": "Sorry, the app can't teleport."}], "end_turn"),
    ], seen)
    a.ask("teleport me", "do")
    assert wait(app, lambda: done_talking(win) and "teleport" in bubble_text(win), 15)
    result = seen[1]["body"]["messages"][-1]["content"][0]
    assert result.get("is_error") and "no command" in result["content"]


def test_claude_refusal_is_not_kept(app, win):
    a = win.aupedia
    a.claude = _claude_with([_message([], "refusal")], [])
    a.open_bubble()
    a.ask("something odd", "do")
    assert wait(app, lambda: done_talking(win) and "not something I can help with" in bubble_text(win), 15)
    assert a.history == []


def test_key_is_saved_encrypted(tmp_path, monkeypatch):
    monkeypatch.setattr(brain, "KEY_FILE", tmp_path / "key.bin")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert brain.api_key() == ""
    brain.save_key("sk-ant-secret-123")
    assert brain.api_key() == "sk-ant-secret-123"
    assert b"sk-ant" not in (tmp_path / "key.bin").read_bytes()
    brain.forget_key()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    assert brain.api_key() == "sk-ant-env"
