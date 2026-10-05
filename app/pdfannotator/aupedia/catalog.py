"""Every command AUPedea can show or click: each menu item (with its path,
like File > Convert to Word) and each toolbar button, read from the window
as it is right now. Each gets a short stable id, like file/convert_to_word."""
import os
import re
from dataclasses import dataclass, field

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QToolBar, QWidgetAction

SKIP_MENUS = {"window", "favorites"}         # tab lists and pins: they change all the time


def clean(text):
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&").replace("...", "").replace("…", "").strip()


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", clean(text).lower()).strip("_") or "item"


@dataclass
class Command:
    id: str
    action: QAction
    label: str
    path: list = field(default_factory=list)       # menu titles down to the item: ["File", "Convert"]
    menus: list = field(default_factory=list)      # the QMenus along that path
    toolbars: list = field(default_factory=list)
    tip: str = ""
    keys: str = ""

    def where(self):
        if self.path:
            return " > ".join(self.path + [self.label])
        return f"the toolbar ({self.label})"

    def line(self):
        out = f"{self.id}: {self.where()}"
        if self.keys:
            out += f" [{self.keys}]"
        tip = clean(self.tip.split("  (")[0]) if self.tip else ""
        if tip and tip.lower() != self.label.lower():
            out += f" - {tip}"
        if not self.path and self.toolbars:
            out += " (toolbar)"
        elif self.toolbars:
            out += " (also on the toolbar)"
        return out


class Catalog:
    def __init__(self, window, exclude=()):
        self.window = window
        self.commands = []
        self.by_id = {}
        self._by_action = {}
        exclude = set(exclude)
        for top in window.menuBar().actions():
            menu = top.menu()
            if menu is not None and slug(top.text()) not in SKIP_MENUS:
                self._walk(menu, [clean(top.text())], [menu], exclude)
        for bar in window.findChildren(QToolBar):
            for act in bar.actions():
                if act.isSeparator() or isinstance(act, QWidgetAction) or act in exclude or not clean(act.text()):
                    continue
                cmd = self._by_action.get(act)
                if cmd is None:
                    cmd = self._add(act, [], [])
                cmd.toolbars.append(bar)

    def _walk(self, menu, path, menus, exclude):
        for act in menu.actions():
            if act.isSeparator() or act in exclude or not clean(act.text()):
                continue
            sub = act.menu()
            if sub is not None:
                if slug(act.text()) not in SKIP_MENUS:
                    self._walk(sub, path + [clean(act.text())], menus + [sub], exclude)
                continue
            if act not in self._by_action:
                self._add(act, path, menus)

    def _add(self, act, path, menus):
        label = clean(act.text())
        base = "/".join(slug(p) for p in path[:1] + [label]) if path else "toolbar/" + slug(label)
        cid, n = base, 2
        while cid in self.by_id:
            cid, n = f"{base}_{n}", n + 1
        cmd = Command(cid, act, label, list(path), list(menus), [], act.toolTip() or "",
                      act.shortcut().toString(QKeySequence.NativeText))
        self.commands.append(cmd)
        self.by_id[cid] = cmd
        self._by_action[act] = cmd
        return cmd

    def get(self, cid):
        return self.by_id.get(str(cid).strip())

    def describe(self):
        """For Claude: one line per command (stable between questions, so it can be cached)."""
        return "\n".join(cmd.line() for cmd in self.commands)

    def state(self, brief=False):
        """What's going on right now: open documents, page, tool, and (unless brief) which commands are
        greyed out or ticked. File names only, never their folders."""
        w = self.window
        lines = []
        tabs = getattr(w, "tabs", None)
        if tabs is not None:
            names = [tabs.tabText(i).lstrip("● ").strip() for i in range(tabs.count())]
            current = tabs.currentIndex()
            lines.append("Open tabs: " + (", ".join(f"[{n}]" if i == current else n for i, n in enumerate(names))
                                          or "none") + " (current in brackets)")
        tab = w.current_tab() if hasattr(w, "current_tab") else None
        if tab is not None and getattr(tab, "document", None) is not None and tab.document.is_open:
            try:
                lines.append(f"Current PDF: page {tab.current_page_index() + 1} of {tab.document.page_count}, "
                             + (f"file {os.path.basename(tab.document.path)}" if tab.document.path else "not saved yet"))
            except Exception:  # noqa: BLE001 - state is a courtesy; never fail a question over it
                pass
        elif hasattr(w, "current_editor") and w.current_editor() is not None:
            lines.append("Current tab is a text document (Word or LaTeX editor), not a PDF.")
        tool = getattr(w, "current_tool", None)
        if tool is not None:
            lines.append("Current tool: " + str(getattr(tool, "name", tool)).replace("_", " ").lower())
        if hasattr(w, "ribbon_shown"):
            lines.append("The ribbon (toolbars) is " + ("shown." if w.ribbon_shown else "hidden."))
        if brief:
            return "\n".join(lines)
        off = [c.id for c in self.commands if not c.action.isEnabled()]
        on = [c.id for c in self.commands if c.action.isCheckable() and c.action.isChecked()]
        if off:
            lines.append("Greyed out right now: " + ", ".join(off))
        if on:
            lines.append("Ticked / on: " + ", ".join(on))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# finding a command by words, for when there's no AI to ask
# ---------------------------------------------------------------------------

SYNONYMS = {
    "sign": "signature", "signing": "signature", "autograph": "signature",
    "merge": "combine", "join": "combine", "glue": "combine",
    "split": "split", "separate": "split",
    "night": "dark", "darkmode": "dark", "theme": "dark",
    "docx": "word", "msword": "word", "tex": "latex",
    "turn": "rotate", "spin": "rotate",
    "bigger": "zoom in", "larger": "zoom in", "smaller": "zoom out", "magnify": "zoom",
    "search": "find", "look": "find",
    "picture": "image", "photo": "image", "logo": "image",
    "write": "text", "type": "text", "typing": "text",
    "draw": "pen", "ink": "pen", "scribble": "pen", "freehand": "pen",
    "highlighter": "highlight", "mark": "highlight",
    "rubber": "eraser", "erase": "eraser",
    "undo": "undo", "back": "undo",
    "email": "mail", "send": "send",
    "calc": "calculator", "maths": "calculator", "math": "calculator",
    "timer": "clock", "stopwatch": "clock", "alarm": "clock",
    "print": "print", "printer": "print",
    "remove": "delete", "trash": "delete",
    "insert": "add", "new": "new",
    "open": "open", "load": "open",
    "save": "save", "store": "save",
}
STOP = {"a", "an", "the", "to", "i", "my", "me", "how", "do", "can", "you", "please", "this", "that", "it", "of",
        "in", "on", "for", "and", "is", "where", "want", "would", "like", "make", "with", "what", "show", "help",
        "doc", "document", "pdf", "file", "page", "some", "need"}


def words(text, expand=False):
    """The words that matter; with expand, each one's everyday synonyms too (for questions, not labels)."""
    out = []
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if w in STOP:
            continue
        for x in [w] + (SYNONYMS.get(w, "").split() if expand else []):
            if x not in out:
                out.append(x)
    return out


def _close(a, b):
    return len(a) > 2 and len(b) > 2 and (a.startswith(b) or b.startswith(a))


def find(catalog, question, limit=3):
    """The commands that best match the question's words (best first)."""
    want = words(question, expand=True)
    if not want:
        return []
    scored = []
    for cmd in catalog.commands:
        label = set(words(cmd.label))
        context = set(words(" ".join(cmd.path) + " " + cmd.tip))
        score = 0.0
        for w in want:
            if w in label:
                score += 3
            elif any(_close(w, lw) for lw in label):
                score += 1.6
            elif w in context:
                score += 1
        if score:
            unmatched = [lw for lw in label if not any(lw == w or _close(w, lw) for w in want)]
            score -= 0.4 * len(unmatched)                           # tighter labels first
            score += 0.2 if cmd.action.isEnabled() else 0
            scored.append((score, cmd))
    scored.sort(key=lambda sc: -sc[0])
    return [cmd for score, cmd in scored[:limit] if score >= 2.5]
