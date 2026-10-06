"""AUPedea's thinking. It can think with Claude (Anthropic) or with a model
on Hugging Face, using the person's own key and the model they choose; the
same tools work with both: point_at and click (the app), read_page, draw,
shape, mark_text and write_text (the paper), and go_to_page. Without a key, a
local finder matches the question's words to a command, and can mark text up.

Keys are kept encrypted for this Windows user. Questions, the list of
commands and what's open (file names, page, tool) are sent to the chosen
service; a page's text (and, for models that can see, a picture of it) is
sent only when AUPedea reads or marks up that page."""
import base64
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .. import theme
from ..cloud.google_auth import ApiError, Offline, http_request, load_secret, save_secret
from ..fonts import APP_DATA
from . import catalog as catalog_mod

KEY_FILE = Path(os.environ.get("AUPEDEAN_AUPEDIA_DIR") or APP_DATA / "aupedia") / "key.bin"
MAX_ROUNDS = 14            # tool rounds per question (drawing can take a few)
MAX_HISTORY = 80           # messages remembered before starting a fresh conversation
_HERE = Path(__file__).resolve()
GUIDES = [_HERE.parents[2] / "README.md", _HERE.parents[3] / "README.md"]   # built app; source tree

CLAUDE = "claude"
HUGGINGFACE = "huggingface"
OLLAMA = "ollama"                  # a model running on this computer (Ollama): no key, nothing leaves it

CLAUDE_KEYS_URL = "https://console.anthropic.com/settings/keys"
CLAUDE_MODELS = [
    ("claude-opus-5-5", "Claude Opus 5.5 (recommended)"),
    ("claude-sonnet-5-5", "Claude Sonnet 5.5 (faster, cheaper)"),
    ("claude-haiku-4-5", "Claude Haiku 4.5 (fastest, cheapest)"),
    ("claude-fable-5-1", "Claude Fable 5.1 (most capable, priciest)"),
]
MODEL = CLAUDE_MODELS[0][0]
# what each model accepts: effort, and the API's own retry on another model after a refusal
CLAUDE_OPTIONS = {
    "claude-opus-5-5": {"effort": "medium", "fallbacks": True},
    "claude-sonnet-5-5": {"effort": "medium", "fallbacks": True},
    "claude-fable-5-1": {"effort": "medium", "fallbacks": True},
    "claude-haiku-4-5": {},
}

HF_ROUTER = "https://router.huggingface.co/v1"
HF_TOKENS_URL = "https://huggingface.co/settings/tokens/new?ownUserPermissions=inference.serverless.write&tokenType=fineGrained"
HF_DEFAULT = "openai/gpt-oss-120b"
# used until the live list is fetched (id, can see pictures)
HF_MODELS = [
    ("openai/gpt-oss-120b", False),
    ("Qwen/Qwen3.6-35B-A3B", True),
    ("zai-org/GLM-5.3", False),
    ("deepseek-ai/DeepSeek-V4.1-Flash", True),
    ("moonshotai/Kimi-K3", True),
    ("meta-llama/Llama-3.3-70B-Instruct", False),
    ("google/gemma-4-31B-it", True),
]


OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
if not OLLAMA_URL.startswith("http"):
    OLLAMA_URL = "http://" + OLLAMA_URL
OLLAMA_DOWNLOAD_URL = "https://ollama.com/download/windows"
OLLAMA_DEFAULT = "qwen3:4b-instruct"
# small models that use tools and fit an ordinary laptop (name, what it's like). Instruct models answer
# straight away; "thinking" ones (plain qwen3:4b is one now) write pages of reasoning first: minutes on a laptop
OLLAMA_MODELS = [
    ("qwen3:4b-instruct", "Qwen3 4B Instruct: about 2.5 GB, the best of the small ones, answers straight away"),
    ("llama3.2:3b", "Llama 3.2 3B: about 2 GB, a little quicker"),
    ("qwen3:1.7b", "Qwen3 1.7B: about 1.4 GB, quickest, simple jobs only"),
    ("qwen3:8b", "Qwen3 8B: about 5 GB, cleverer, needs 16 GB of memory"),
]
OLLAMA_CONTEXT = 4096              # tokens the model keeps in mind: each one costs memory a laptop may not have
OLLAMA_PAGE_TEXT = 2500            # characters of a page handed to a model on this computer
OLLAMA_MAX_REPLY = 320             # tokens a reply may run to: a laptop writes about 10 a second


# ---------------------------------------------------------------------------
# keys and choices
# ---------------------------------------------------------------------------

def _secrets():
    data = load_secret(KEY_FILE) or {}
    if "key" in data and CLAUDE not in data:          # saved before Hugging Face was added
        data = {CLAUDE: data["key"]}
    return data


def _save_secrets(data):
    data = {k: v for k, v in data.items() if v}
    if data:
        save_secret(KEY_FILE, data)
    else:
        try:
            KEY_FILE.unlink()
        except OSError:
            pass


def saved_key(provider=CLAUDE):
    return _secrets().get(provider, "")


def api_key(provider=CLAUDE):
    """The saved key, else the usual environment variable, else ""."""
    env = "ANTHROPIC_API_KEY" if provider == CLAUDE else "HF_TOKEN"
    return saved_key(provider) or os.environ.get(env, "").strip()


def save_key(key, provider=CLAUDE):
    data = _secrets()
    data[provider] = key.strip()
    _save_secrets(data)


def forget_key(provider=CLAUDE):
    data = _secrets()
    data.pop(provider, None)
    _save_secrets(data)


def choices():
    s = theme._settings()
    return {"provider": s.value("aupedia/provider", CLAUDE),
            "claude_model": s.value("aupedia/claude_model", MODEL),
            "hf_model": s.value("aupedia/hf_model", HF_DEFAULT),
            "hf_vision": str(s.value("aupedia/hf_vision", "false")) == "true",
            "local_model": s.value("aupedia/local_model", OLLAMA_DEFAULT),
            "local_vision": str(s.value("aupedia/local_vision", "false")) == "true"}


def save_choices(provider=None, claude_model=None, hf_model=None, hf_vision=None, local_model=None,
                 local_vision=None):
    s = theme._settings()
    for key, value in (("provider", provider), ("claude_model", claude_model), ("hf_model", hf_model),
                       ("local_model", local_model)):
        if value:
            s.setValue(f"aupedia/{key}", value)
    for key, value in (("hf_vision", hf_vision), ("local_vision", local_vision)):
        if value is not None:
            s.setValue(f"aupedia/{key}", "true" if value else "false")


def make_provider():
    """The chosen service (one on this computer needs no key), else an online one that has a key, else None."""
    c = choices()
    if c["provider"] == OLLAMA:
        return Ollama(c["local_model"], c["local_vision"])
    order = [c["provider"], HUGGINGFACE if c["provider"] == CLAUDE else CLAUDE]
    for kind in order:
        key = api_key(kind)
        if key and kind == CLAUDE:
            return Claude(key, c["claude_model"])
        if key and kind == HUGGINGFACE:
            return HuggingFace(key, c["hf_model"], c["hf_vision"])
    return None


# ---------------------------------------------------------------------------
# what the AI is told
# ---------------------------------------------------------------------------

PERSONA = """You are AUPedea, the helper who lives inside AUPedean Annotator, a Windows app for reading, \
annotating, signing and converting PDFs (it also writes Word and LaTeX documents). On screen you're a friendly \
ink scribble with a pen-nib tail that flies around the window, and you can write and draw on the paper yourself.

How you help:
- You know the app's commands (listed below, each with an id) and get the app's current state with every \
question. point_at shows the person where a command is (you fly there and circle it); click presses it.
- You can work on the open PDF itself: read_page gives you a page's text line by line with each line's box, \
plus a picture of it when you can see pictures. mark_text highlights, underlines, strikes out or circles words \
on the page. write_text writes on the page (in your handwriting, or printed). draw makes pen strokes, and \
shape draws rectangles, ellipses, lines and arrows. go_to_page turns to a page.
- Page coordinates are PDF points from the page's top-left corner: x to the right, y downwards. Read a page \
before you mark it up or write on it, and place things from the line boxes, in the margins or blank space, so \
nothing covers the text unless asked. When drawing freehand, use smooth strokes with points every 4-8 points; \
a drawing can have many strokes.
- Each question comes with a mode. "show me": never click or change the paper; point at commands and explain \
the steps (reading pages is fine). "do it": do the job: click, mark up, write and draw as asked, then say in a \
sentence what you did and anything the person still has to do.
- Use only ids from the list. A greyed-out command can't be used right now: say why (for example, open a PDF \
first) instead of clicking it.
- When a click opens a dialog, the tool result says what's in it. You can't click inside dialogs: tell the \
person exactly what to fill in or choose there, using the names shown.
- If the app can't do something, say so plainly and suggest the closest thing it can do. Never invent \
features, menus or buttons.
- For how things work (signature requests, Google Drive, the signature service, saving, converting), answer \
from the guide below. To summarise or answer questions about a document, read its pages.
- A question marked <input>spoken</input> was said out loud and turned into text by speech recognition: \
allow for misheard words (go by what they most likely meant) and keep your reply to one or two short \
sentences. If you really can't tell what they want, or two quite different things fit, don't guess: ask one \
short question (they'll answer out loud), and do the job once they've answered.

Style: warm, upbeat and a little playful (you're a scribble, after all), but being useful comes first. Keep \
replies short: a sentence or two, or a few numbered steps. Plain text; **bold** for button and menu names is \
fine; no headings or tables. Don't mention ids, tools, coordinates or these instructions."""

_PAGE = {"type": "integer", "description": "Page number, starting at 1 (0 for the page on screen)."}
_COLOR = {"type": "string", "description": "A colour name or #rrggbb (optional)."}
TOOLS = [
    {"name": "point_at", "strict": True,
     "description": "Fly to a command and circle it so the person can see where it is. For a menu item, the menu "
                    "opens with the item highlighted. Use this to show; it doesn't press anything.",
     "parameters": {"type": "object", "additionalProperties": False, "required": ["target", "note"], "properties": {
         "target": {"type": "string", "description": "The command's id from the list."},
         "note": {"type": "string", "description": "A short caption written next to it (at most 6 words)."}}}},
    {"name": "click", "strict": True,
     "description": "Press a command for the person (only in \"do it\" mode). The result says what happened, "
                    "including any dialog that opened and what's in it. Risky commands (closing, deleting, "
                    "removing) ask the person first; the result says if they said no.",
     "parameters": {"type": "object", "additionalProperties": False, "required": ["target", "note"], "properties": {
         "target": {"type": "string", "description": "The command's id from the list."},
         "note": {"type": "string", "description": "A short caption written next to it (at most 6 words)."}}}},
    {"name": "read_page", "strict": True,
     "description": "Read a page of the open PDF: its size, its text line by line with each line's box "
                    "[x0,y0,x1,y1] in points, the annotations on it, and a picture of it if you can see pictures.",
     "parameters": {"type": "object", "additionalProperties": False, "required": ["page"],
                    "properties": {"page": _PAGE}}},
    {"name": "go_to_page", "strict": True,
     "description": "Turn the open PDF to a page.",
     "parameters": {"type": "object", "additionalProperties": False, "required": ["page"],
                    "properties": {"page": _PAGE}}},
    {"name": "mark_text",
     "description": "Mark words on the page as a person would with a pen: highlight, underline, strikeout, or "
                    "circle. Give the exact words as they appear on the page (from read_page).",
     "parameters": {"type": "object", "additionalProperties": False, "required": ["page", "text", "style"],
                    "properties": {
                        "page": _PAGE,
                        "text": {"type": "string", "description": "The exact words to mark."},
                        "style": {"type": "string", "enum": ["highlight", "underline", "strikeout", "circle"]},
                        "every": {"type": "boolean", "description": "Mark every place it appears, not just the first."},
                        "color": _COLOR}}},
    {"name": "write_text",
     "description": "Write text on the page with its top-left corner at (x, y): a note in the margin, an "
                    "answer, a label. Handwriting by default.",
     "parameters": {"type": "object", "additionalProperties": False, "required": ["page", "x", "y", "text"],
                    "properties": {
                        "page": _PAGE,
                        "x": {"type": "number"}, "y": {"type": "number"},
                        "text": {"type": "string"},
                        "size": {"type": "number", "description": "Font size in points (default 14)."},
                        "style": {"type": "string", "enum": ["handwriting", "print"]},
                        "width": {"type": "number", "description": "Wrap the text at this width in points."},
                        "color": _COLOR}}},
    {"name": "draw",
     "description": "Draw freehand pen strokes on the page: a doodle, a tick, a star, a signature-like flourish, "
                    "a sketch. Each stroke is a list of [x, y] points the pen moves through without lifting.",
     "parameters": {"type": "object", "additionalProperties": False, "required": ["page", "strokes"],
                    "properties": {
                        "page": _PAGE,
                        "strokes": {"type": "array", "items": {"type": "array", "items": {
                            "type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}}},
                        "width": {"type": "number", "description": "Pen width in points (default 2)."},
                        "color": _COLOR}}},
    {"name": "shape",
     "description": "Draw a shape on the page: rect or ellipse inside the box (x0,y0)-(x1,y1), or a line or "
                    "arrow from (x0,y0) to (x1,y1).",
     "parameters": {"type": "object", "additionalProperties": False,
                    "required": ["page", "kind", "x0", "y0", "x1", "y1"], "properties": {
                        "page": _PAGE,
                        "kind": {"type": "string", "enum": ["rect", "ellipse", "line", "arrow"]},
                        "x0": {"type": "number"}, "y0": {"type": "number"},
                        "x1": {"type": "number"}, "y1": {"type": "number"},
                        "width": {"type": "number", "description": "Line width in points (default 2)."},
                        "color": _COLOR}}},
]
PAPER_TOOLS = {"mark_text", "write_text", "draw", "shape"}


def lean_tools():
    """The tools with one-line descriptions and no parameter notes (a small model reads every word, slowly)."""
    out = []
    for t in TOOLS:
        params = {k: {kk: vv for kk, vv in v.items() if kk != "description"} for k, v in t["parameters"]["properties"].items()}
        out.append({**t, "description": t["description"].split(". ")[0].rstrip(".") + ".",
                    "parameters": {**t["parameters"], "properties": params}})
    return out


def memory_gb():
    """(total, free) memory of this computer in GB, or (None, None) if Windows won't say."""
    try:
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        st = Status()
        st.dwLength = ctypes.sizeof(Status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return None, None
        return st.ullTotalPhys / 1e9, st.ullAvailPhys / 1e9
    except (AttributeError, OSError):
        return None, None


# what each suggested model needs in memory while it answers (the model plus 4K tokens of context, roughly)
OLLAMA_NEEDS_GB = {"qwen3:1.7b": 2.0, "llama3.2:3b": 3.0, "qwen3:4b": 3.4, "qwen3:4b-instruct": 3.4, "qwen3:8b": 6.0}


def recommended_local_model(total_gb=None):
    """The best suggested model for this computer's memory."""
    total = total_gb if total_gb is not None else memory_gb()[0]
    if total is None or total >= 15:
        return "qwen3:8b" if total else OLLAMA_DEFAULT
    if total >= 11:
        return "qwen3:4b-instruct"
    return "qwen3:1.7b"


def guide_text():
    for path in GUIDES:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            continue
    return "(The guide isn't available.)"


KEPT_RESULT = 600                  # characters of an old tool result kept for follow-ups


def compact_history(conversation):
    """A finished conversation, lighter for the next question: pictures of pages dropped and long tool results
    (whole pages of text, guide sections) cut short. They mattered while answering; resent with every later
    request they'd cost a lot (a picture of a page is well over a thousand tokens)."""
    out = []
    for msg in conversation:
        if msg.get("role") == "user" and isinstance(msg.get("content"), list):
            continue                                        # "Here is what the page looks like." + pictures
        if msg.get("role") == "tool" and len(str(msg.get("content", ""))) > KEPT_RESULT:
            msg = {**msg, "content": msg["content"][:KEPT_RESULT] + " ...(cut short; read it again if needed)"}
        out.append(msg)
    return out


def guide_sections():
    """[(title, text)]: the guide cut at its ## and ### headings."""
    out = []
    for part in re.split(r"(?m)^(?=#{2,3} )", guide_text()):
        title = part.split("\n", 1)[0].lstrip("#").strip()
        if part.startswith("#") and title:
            out.append((title, part.strip()))
    return out


GUIDE_TOOL = {
    "name": "read_guide",
    "description": "Look up how part of the app works in its guide (the section titles are listed in the "
                   "instructions). Returns the best-matching sections.",
    "parameters": {"type": "object", "additionalProperties": False, "required": ["topic"], "properties": {
        "topic": {"type": "string", "description": "A section title, or a few words about what you need."}}}}
GUIDE_LIMIT = 6000                 # characters of the guide one look-up returns


def read_guide(topic):
    """The guide's sections that best match the topic (for a service without prompt caching, where sending
    the whole 32,000-character guide with every request would cost on every request)."""
    want = set(catalog_mod.words(str(topic), expand=True))
    scored = []
    for title, text in guide_sections():
        in_title = set(catalog_mod.words(title))
        in_text = set(catalog_mod.words(text))
        score = sum(3 if w in in_title else 1 if w in in_text else 0 for w in want)
        if str(topic).strip().lower() == title.lower():
            score += 100
        if score:
            scored.append((score, title, text))
    scored.sort(key=lambda s: -s[0])
    out = ""
    for _score, _title, text in scored[:2]:
        out += text[:GUIDE_LIMIT - len(out)] + "\n\n"
        if len(out) >= GUIDE_LIMIT:
            break
    titles = ", ".join(t for t, _x in guide_sections())
    return out.strip() or f"Nothing in the guide matches {topic!r}. Its sections: {titles}."


# A small model on this computer can't pick from 130 commands (measured with qwen3:1.7b: it named the wrong one,
# or used an id as a tool name, which Ollama drops). So the word finder shortlists a few for each question, and
# the instructions say exactly how a tool is called. No example ids in them: a small model copies those.
LEAN_PERSONA = """You are AUPedea, the friendly helper inside AUPedean Annotator, a PDF app.

Your tools (these are the only tool names): point_at, click, read_page, go_to_page, mark_text, write_text, draw, shape.
- To show where a command is, call the point_at tool with its command id as the target.
- To press a command, call the click tool with its command id as the target (only in "do it" mode).
- A command id is never a tool name: use it only as the target.
- Use only command ids listed under <commands> in the message. If none fits, say so in a sentence.
- In "show me" mode, point_at the command, then say where it is. In "do it" mode, click it, then say what you did.
- To change the page (highlight, underline, write, draw), read_page first, then mark_text, write_text, draw or shape.
- If the question was spoken (<input>spoken</input>), allow for misheard words.
After using tools, reply in one or two short, warm sentences."""
LEAN_SHORTLIST = 6                 # commands offered to a small model for each question


def system_prompt(catalog, lean=False, guide=True):
    """Stable between questions (the command list and the guide), so it's cached. Lean (for a small model on
    this computer, where every token is read by a laptop's processor): just how to use the tools; the commands
    that might fit come with each question (lean_commands). Without the guide (for a service that charges for
    every token of every request): just its section titles, and read_guide to look one up."""
    if lean:
        return LEAN_PERSONA
    head = PERSONA + "\n\n# Commands (id: where it is [shortcut] - what it does)\n" + catalog.describe()
    if guide:
        return head + "\n\n# The guide\n" + guide_text()
    return (head + "\n\n# The guide\nIt isn't included here: call read_guide with a section title (or a few words) "
            "to read the part you need. Sections: " + "; ".join(t for t, _x in guide_sections()) + ".")


NO_COMMANDS = "(none match: answer in words, or use the page tools)"


def shortlist(catalog, question):
    """The few commands whose words match the question, for a small model to choose from."""
    return catalog_mod.find(catalog, question, LEAN_SHORTLIST)


def lean_commands(commands):
    """The shortlist as the model reads it ("" if it's empty)."""
    lines = []
    for cmd in commands:
        note = (" (greyed out)" if not cmd.action.isEnabled()
                else " (on now)" if cmd.action.isCheckable() and cmd.action.isChecked() else "")
        lines.append(f"{cmd.id}: {cmd.where()}{note}")
    return "\n".join(lines)


def user_turn(question, mode, state, spoken=False, commands=None):
    return (question.strip() + "\n\n<mode>" + ("do it" if mode == "do" else "show me") + "</mode>\n"
            + ("<input>spoken</input>\n" if spoken else "")
            + ("<commands>\n" + commands + "\n</commands>\n" if commands is not None else "")
            + "<app_state>\n" + state + "\n</app_state>")


# shown to a small model before each question: one call done right teaches it more than any instruction
LEAN_EXAMPLE = [
    {"role": "user", "content": user_turn("Where is undo?", "show", "Current tool: select",
                                          commands="edit/undo: Edit > Undo\nedit/redo: Edit > Redo")},
    {"role": "assistant", "content": "",
     "tool_calls": [{"function": {"name": "point_at", "arguments": {"target": "edit/undo", "note": "Undo"}}}]},
    {"role": "tool", "tool_name": "point_at", "content": "Pointed at Edit > Undo."},
    {"role": "assistant", "content": "It's **Edit > Undo** (or press Ctrl+Z)."},
]
_CALL_LINE = re.compile(r"^\W*(?:point_at|click|read_page|go_to_page|mark_text|write_text|draw|shape)\b(?=.*[/(=])"
                        r"[\s(:]*[\w/\"'=,. ]*\)?\s*$", re.I | re.M)       # "point_at view/zoom_in", "click(target=...)"
_BOX = re.compile(r"\[-?\d+(?:\.\d+)?(?:,\s*-?\d+(?:\.\d+)?){3}\]\s*")      # read_page's line boxes, repeated
_STATE_ECHO = re.compile(r"\s*(?:Now: )?Open tabs:[\s\S]*$")                 # the app's state, repeated
_TURN_ON = re.compile(r"\b(turn on|switch on|enable|show|use)\b", re.I)
_TURN_OFF = re.compile(r"\b(turn off|switch off|disable|hide|stop)\b", re.I)


def tidy(catalog, text):
    """A small model's reply as the person should see it: tool calls it wrote out as text taken out,
    and command ids turned into where they are."""
    text = _CALL_LINE.sub("", text)
    text = _BOX.sub("", _STATE_ECHO.sub("", text))
    for cmd in sorted(catalog.commands, key=lambda c: -len(c.id)):
        text = re.sub(rf"(?<![\w/])(?:\w+/)?{re.escape(cmd.id)}(?![\w/])", f"**{cmd.where()}**", text)
    return re.sub(r"\*\*\*\*", "**", re.sub(r"\n{3,}", "\n\n", text)).strip()


_CLAIM = re.compile(r"\b(highlighted|underlined|circled|struck|crossed out|marked|wrote|written|drew|drawn|"
                    r"added (?:a|the) (?:note|box|circle|line|arrow))\b", re.I)


def lean_shortcut(catalog, question, mode):
    """For a small model: "highlight/underline/circle <words>" is done by the word finder, which marks the
    exact words reliably (the model tends to leave the words out, then say it's done). None otherwise."""
    return local_answer(catalog, question, mode) if _MARK.match(question) else None


def honest(text, changed):
    """A small model's last word, unless it says it marked up the page when nothing on it changed."""
    if changed or not _CLAIM.search(text):
        return text
    return ("I couldn't mark that up, sorry. Tell me the exact words, like **highlight 500 dollars**, or try "
            "the pen tools yourself.")


def lean_wrapup(catalog, calls, dialog=None):
    """What a small model would have said after pointing or pressing, without asking it: "It's **View >
    Zoom In**." / "Done: **File > Save**." (and the dialog to fill in, if one opened)."""
    said = []
    for _id, name, args in calls:
        cmd = catalog.get(args.get("target", ""))
        if cmd is not None:
            said.append((f"Done: **{cmd.where()}**." if name == "click" else
                         f"It's **{cmd.where()}**" + (f" (shortcut **{cmd.keys}**)." if cmd.keys else ".")))
    if dialog:
        said.append(f"Fill in the **{dialog}** window that opened.")
    return " ".join(dict.fromkeys(said)) or "Done."


def recover(catalog, question, mode, text):
    """[(op, ...)] for the director (as local_answer) when a small model answered in words instead of calling
    a tool: the shortlisted command its reply names is pointed at or pressed, as it meant to; when it gave
    nothing useful, the word finder answers instead."""
    candidates = shortlist(catalog, question)
    named = []
    for cmd in candidates:
        hits = [m.start() for pat in (rf"(?<!\w){re.escape(cmd.id)}(?!\w)", re.escape(cmd.where()))
                for m in [re.search(pat, text, re.I)] if m]
        if hits:
            named.append((min(hits), -len(cmd.where()), cmd))
    said = tidy(catalog, text)
    if not named:
        return [("say", said)] if said else local_answer(catalog, question, mode)
    cmd = min(named, key=lambda n: n[:2])[2]
    said = re.sub(r"^(?:pointed at|pointing at|point at)\s+", "It's ", said, flags=re.I)
    if mode == "do":
        said = re.sub(r"^(?:clicked|click|pressed|press)\s+", "Done: ", said, flags=re.I)
    ops = []
    if not cmd.action.isEnabled():
        ops.append(("point", cmd, "greyed out for now"))
        said = said or f"That's **{cmd.where()}**, but it's greyed out right now."
    elif mode == "do" and cmd.action.isCheckable() and (
            (cmd.action.isChecked() and _TURN_ON.search(question) and not _TURN_OFF.search(question))
            or (not cmd.action.isChecked() and _TURN_OFF.search(question))):
        ops.append(("point", cmd, cmd.label))           # already the way they want it: pressing would undo it
        said = f"**{cmd.where()}** is already " + ("on." if cmd.action.isChecked() else "off.")
    elif mode == "do":
        ops.append(("click", cmd, cmd.label))
        said = said or f"Done: **{cmd.where()}**."
    else:
        ops.append(("point", cmd, cmd.label))
        said = said or f"It's **{cmd.where()}**" + (f" (shortcut **{cmd.keys}**)." if cmd.keys else ".")
    return ops + [("say", said)]


# ---------------------------------------------------------------------------
# the services: the same few steps for each
# ---------------------------------------------------------------------------

@dataclass
class Reply:
    texts: list = field(default_factory=list)
    calls: list = field(default_factory=list)     # (id, name, args dict)
    stop: str = "end"                              # end, tool, max_tokens, refusal
    raw: object = None


@dataclass
class Result:
    call_id: str
    text: str
    error: bool = False
    image: bytes = None                            # a PNG to show the model, if it can see


class Claude:
    kind = CLAUDE
    vision = True

    def __init__(self, key=None, model=None, client=None):
        self.key = key if key is not None else api_key(CLAUDE)
        self.model = model or MODEL
        self._client = client

    def label(self):
        return dict(CLAUDE_MODELS).get(self.model, self.model).split(" (")[0]

    def available(self):
        return bool(self._client is not None or self.key)

    def client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=self.key, timeout=120.0, max_retries=2)
        return self._client

    def call(self, system, conversation):
        """One request (in a worker thread). The command list and guide are cached."""
        options = CLAUDE_OPTIONS.get(self.model, {})
        extra = {}
        if options.get("effort"):
            extra["output_config"] = {"effort": options["effort"]}
        if options.get("fallbacks"):
            extra["betas"] = ["server-side-fallback-2026-07-01"]
            extra["fallbacks"] = "default"
        resp = self.client().beta.messages.create(
            model=self.model, max_tokens=16000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=[_claude_tool(t) for t in TOOLS], messages=conversation, **extra)
        reply = Reply(raw=resp.content)
        for block in resp.content:
            if block.type == "text" and block.text.strip():
                reply.texts.append(block.text)
            elif block.type == "tool_use":
                reply.calls.append((block.id, block.name, dict(block.input or {})))
        reply.stop = {"tool_use": "tool", "refusal": "refusal", "max_tokens": "max_tokens"}.get(resp.stop_reason, "end")
        if reply.stop == "tool" and not reply.calls:
            reply.stop = "end"
        return reply

    def add_user(self, conversation, text):
        conversation.append({"role": "user", "content": text})

    def add_reply(self, conversation, reply):
        conversation.append({"role": "assistant", "content": reply.raw})    # unchanged, thinking and all

    def add_results(self, conversation, results):
        blocks = []
        for r in results:
            content = r.text
            if r.image:
                content = [{"type": "text", "text": r.text},
                           {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                        "data": base64.b64encode(r.image).decode()}}]
            blocks.append({"type": "tool_result", "tool_use_id": r.call_id, "content": content,
                           **({"is_error": True} if r.error else {})})
        conversation.append({"role": "user", "content": blocks})

    def check(self):
        self.client().models.retrieve(self.model)


def _claude_tool(t):
    out = {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
    if t.get("strict"):
        out["strict"] = True
    return out


class HuggingFace:
    """A chat model on Hugging Face Inference Providers (the OpenAI-style chat API, with tools). Providers there
    charge for every token of every request (no cheap cached prompt as with Claude), and each tool round sends
    the whole conversation again: so the guide is looked up rather than sent, and the conversation kept short."""
    kind = HUGGINGFACE
    guide_inline = False            # read_guide instead of the whole guide in every request
    history_limit = 24              # messages kept for follow-ups (Claude: MAX_HISTORY)

    def __init__(self, token, model=None, vision=False, transport=None):
        self.key = token
        self.model = model or HF_DEFAULT
        self.vision = bool(vision)
        self.transport = transport or http_request

    def label(self):
        return self.model.split("/")[-1]

    def available(self):
        return bool(self.key)

    def _post(self, path, body, timeout=180):
        _s, _h, raw = self.transport("POST", HF_ROUTER + path, data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {self.key}",
                                              "Content-Type": "application/json"}, timeout=timeout)
        return json.loads(raw)

    def call(self, system, conversation):
        data = self._post("/chat/completions", {
            "model": self.model, "max_tokens": 4096, "tool_choice": "auto",
            "messages": [{"role": "system", "content": system}] + conversation,
            "tools": [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                        "parameters": t["parameters"]}} for t in TOOLS + [GUIDE_TOOL]]})
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        reply = Reply()
        if (msg.get("content") or "").strip():
            reply.texts.append(msg["content"])
        calls = []
        for call in msg.get("tool_calls") or []:
            fn = call.get("function") or {}
            call_id = call.get("id") or "call_" + uuid.uuid4().hex[:12]
            raw_args = fn.get("arguments") or "{}"
            try:
                args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args)
            except ValueError:
                args = {"__invalid__": str(raw_args)[:200]}
            reply.calls.append((call_id, fn.get("name", ""), args if isinstance(args, dict) else {}))
            calls.append({"id": call_id, "type": "function",
                          "function": {"name": fn.get("name", ""), "arguments": raw_args if isinstance(raw_args, str)
                                       else json.dumps(raw_args)}})
        reply.raw = {"role": "assistant", "content": msg.get("content") or ""}
        if calls:
            reply.raw["tool_calls"] = calls
        reply.stop = "tool" if reply.calls else ("max_tokens" if choice.get("finish_reason") == "length" else "end")
        return reply

    def add_user(self, conversation, text):
        conversation.append({"role": "user", "content": text})

    def add_reply(self, conversation, reply):
        conversation.append(reply.raw)

    def add_results(self, conversation, results):
        images = []
        for r in results:
            conversation.append({"role": "tool", "tool_call_id": r.call_id,
                                 "content": ("Error: " if r.error else "") + r.text})
            if r.image and self.vision:
                images.append(r.image)
        if images:   # tool messages can't hold pictures: they follow, as from the person
            conversation.append({"role": "user", "content": [{"type": "text", "text": "Here is what the page looks like."}]
                                 + [{"type": "image_url", "image_url": {
                                     "url": "data:image/png;base64," + base64.b64encode(png).decode()}}
                                    for png in images]})

    def check(self):
        self.transport("GET", "https://huggingface.co/api/whoami-v2",
                       headers={"Authorization": f"Bearer {self.key}"}, timeout=30)


class OllamaNotRunning(Exception):
    """Ollama isn't installed, or isn't running."""


class OllamaSlow(Exception):
    """The model took longer than we wait."""


class Ollama:
    """A model running on this computer with Ollama (its own chat API: tools, pictures, and thinking off,
    which a laptop needs). No key, nothing leaves the computer; slower, and best at simpler jobs."""
    kind = OLLAMA
    lean = True                     # a short system prompt: a laptop reads every token of it

    def __init__(self, model=None, vision=False, url=None, transport=None):
        self.key = ""
        self.model = model or OLLAMA_DEFAULT
        self.vision = bool(vision)
        self.url = (url or OLLAMA_URL).rstrip("/")
        self.transport = transport or http_request
        self._caps = None
        self._names = {}             # call id -> tool name (Ollama answers tool results by name)
        self.thinks_anyway = False   # it reasoned out loud though told not to (a "thinking" model)

    def label(self):
        return self.model

    def available(self):
        return True

    def _req(self, method, path, body=None, timeout=30):
        data = json.dumps(body).encode() if body is not None else None
        _s, _h, raw = self.transport(method, self.url + path, data=data,
                                     headers={"Content-Type": "application/json"} if data else None, timeout=timeout)
        return json.loads(raw) if raw else {}

    def running(self):
        try:
            self._req("GET", "/api/version", timeout=3)
            return True
        except (Offline, ApiError, ValueError):
            return False

    def capabilities(self):
        if self._caps is None:
            try:
                self._caps = set(self._req("POST", "/api/show", {"model": self.model}).get("capabilities") or [])
            except (Offline, ApiError, ValueError):
                self._caps = set()
        return self._caps

    def _options(self):
        # warming up and answering must ask for the same context, or Ollama loads the whole model again
        return {"num_ctx": OLLAMA_CONTEXT, "temperature": 0.3, "num_predict": OLLAMA_MAX_REPLY}

    def warm_up(self):
        """Load the model into memory now, and have it read the instructions, tools and example (which every
        question starts with, and Ollama keeps), so the first question doesn't wait for either. Any other model
        still in memory (one chosen before, kept for 30 minutes) is let go first: two at once don't fit a laptop,
        and Windows then swaps them to the disk (measured here: minutes an answer instead of seconds)."""
        try:
            loaded = [m.get("name") for m in self._req("GET", "/api/ps", timeout=5).get("models", [])]
        except (Offline, ApiError, ValueError):
            loaded = []
        for other in loaded:
            if other and other not in (self.model, self.model + ":latest"):
                try:
                    self._req("POST", "/api/generate", {"model": other, "keep_alive": 0}, timeout=30)
                except (Offline, ApiError, ValueError):
                    pass
        body = self._body(LEAN_PERSONA, list(LEAN_EXAMPLE))
        body["options"] = {**body["options"], "num_predict": 1}
        self._req("POST", "/api/chat", body, timeout=600)

    def memory_tight(self):
        """True when this computer hasn't the free memory the model needs (so it'll crawl)."""
        _total, free = memory_gb()
        need = OLLAMA_NEEDS_GB.get(self.model)
        return bool(free is not None and need and not self._loaded() and free < need)

    def _loaded(self):
        try:
            return any(m.get("name") == self.model for m in self._req("GET", "/api/ps", timeout=3).get("models", []))
        except (Offline, ApiError, ValueError):
            return False

    def _body(self, system, conversation):
        body = {"model": self.model, "stream": False, "keep_alive": "30m",
                "messages": [{"role": "system", "content": system}] + conversation,
                "tools": [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                            "parameters": t["parameters"]}} for t in lean_tools()],
                "options": self._options()}
        if "thinking" in self.capabilities():
            body["think"] = False    # thinking out loud takes minutes on a laptop's processor
        return body

    def call(self, system, conversation):
        body = self._body(system, conversation)
        try:
            data = self._req("POST", "/api/chat", body, timeout=900)
        except Offline:
            raise (OllamaSlow() if self.running() else OllamaNotRunning()) from None
        msg = data.get("message") or {}
        reply = Reply()
        content, thought = strip_thinking(msg.get("content") or "")
        self.thinks_anyway = self.thinks_anyway or thought
        if content:
            reply.texts.append(content)
        calls = []
        for i, call in enumerate(msg.get("tool_calls") or []):
            fn = call.get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {"__invalid__": args[:200]}
            call_id = call.get("id") or f"call_{len(self._names)}_{i}"
            self._names[call_id] = fn.get("name", "")
            reply.calls.append((call_id, fn.get("name", ""), args if isinstance(args, dict) else {}))
            calls.append({"function": {"name": fn.get("name", ""), "arguments": args}})
        reply.raw = {"role": "assistant", "content": content}      # its reasoning isn't worth reading again
        if calls:
            reply.raw["tool_calls"] = calls
        reply.stop = "tool" if reply.calls else ("max_tokens" if data.get("done_reason") == "length" else "end")
        return reply

    def add_user(self, conversation, text):
        conversation.append({"role": "user", "content": text})

    def add_reply(self, conversation, reply):
        conversation.append(reply.raw)

    def add_results(self, conversation, results):
        images = []
        for r in results:
            conversation.append({"role": "tool", "tool_name": self._names.get(r.call_id, ""),
                                 "content": ("Error: " if r.error else "") + r.text})
            if r.image and self.vision:
                images.append(base64.b64encode(r.image).decode())
        if images:
            conversation.append({"role": "user", "content": "Here is what the page looks like.", "images": images})

    def check(self):
        if not self.running():
            raise OllamaNotRunning()
        names = [n for n, _size in ollama_models(self.transport, self.url)]
        if self.model not in names and f"{self.model}:latest" not in names:
            raise ApiError(404, f"model '{self.model}' not found")


def strip_thinking(text):
    """(the answer, whether it reasoned out loud first): "<think>...</think>" (or a "thinking" model's
    reasoning that only ends in "</think>") is taken off the front."""
    if "</think>" in text:
        return text.rsplit("</think>", 1)[1].strip(), True
    if text.lstrip().startswith("<think>"):
        return "", True                  # cut off while still thinking
    return text.strip(), False


def ollama_models(transport=None, url=None):
    """[(model name, size in GB)] downloaded into Ollama on this computer."""
    _s, _h, raw = (transport or http_request)("GET", (url or OLLAMA_URL) + "/api/tags", timeout=5)
    return [(m.get("name") or m.get("model"), (m.get("size") or 0) / 1e9) for m in json.loads(raw).get("models", [])]


def ollama_pull(model, progress=lambda fraction, status: None, url=None, open_stream=None):
    """Download a model into Ollama (in a worker thread), reporting progress as it goes."""
    import urllib.error
    import urllib.request

    url = (url or OLLAMA_URL) + "/api/pull"
    body = json.dumps({"model": model, "stream": True}).encode()
    if open_stream is None:
        def open_stream():
            req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
            try:
                return urllib.request.urlopen(req, timeout=60)
            except (urllib.error.URLError, OSError):
                raise OllamaNotRunning() from None
    stream = open_stream()
    try:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            if event.get("error"):
                raise ApiError(404 if "not found" in event["error"] else 500, event["error"])
            total, done = event.get("total"), event.get("completed")
            progress(min(1.0, done / total) if total and done else None, event.get("status", ""))
            if event.get("status") == "success":
                return
    finally:
        close = getattr(stream, "close", None)
        if close:
            close()


def hf_models(transport=None):
    """[(model id, can see pictures)] for every live Hugging Face model whose providers do tool calls."""
    _s, _h, raw = (transport or http_request)("GET", HF_ROUTER + "/models", timeout=30)
    out = []
    for m in json.loads(raw).get("data", []):
        live = [p for p in m.get("providers") or [] if p.get("status", "live") == "live" and p.get("supports_tools")]
        if live:
            modalities = (m.get("architecture") or {}).get("input_modalities") or []
            out.append((m["id"], "image" in modalities))
    return out


def friendly_error(exc, kind=CLAUDE):
    if isinstance(exc, OllamaNotRunning):
        return ("My offline brain isn't running. Install Ollama from ollama.com (or start it), then try again; "
                "or pick another brain in my settings (the gear).")
    if isinstance(exc, OllamaSlow):
        return "The model on this computer took too long to answer. Try a shorter question, or a smaller model."
    if kind == OLLAMA and isinstance(exc, ApiError):
        if exc.status == 404 or "not found" in str(exc.message).lower():
            return "That model isn't downloaded yet: open my settings (the gear) and click Download."
        if "does not support tools" in str(exc.message).lower():
            return "That model can't use tools, so it can't click or mark up for me. Pick another in my settings."
        return f"Ollama said: {exc.message}"
    if isinstance(exc, Offline):
        return "I couldn't reach " + ("Claude" if kind == CLAUDE else "Hugging Face") + ". Check the internet connection."
    if isinstance(exc, ApiError):
        detail = exc.message
        try:
            body = json.loads(exc.body or b"{}")
            detail = (body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else body.get("error") or detail
        except (ValueError, AttributeError):
            pass
        if exc.status == 401:
            return "Hugging Face didn't accept the token. Check it in AUPedea Settings (the gear)."
        if exc.status == 402:
            return "The Hugging Face account behind this token is out of credit for this month."
        if exc.status == 403:
            return ("That Hugging Face token can't call models: make a fine-grained token with the "
                    "\"Make calls to Inference Providers\" permission.")
        if exc.status == 429:
            return "Hugging Face is getting a lot of questions from this token. Try again in a minute."
        if exc.status in (400, 404, 422) and detail and "tool" in str(detail).lower():
            return "That Hugging Face model can't use tools. Pick another model in AUPedea Settings."
        return f"Hugging Face said: {detail}"
    try:
        import anthropic
    except ImportError:
        return "AUPedea's Claude part isn't installed in this copy of the app."
    if isinstance(exc, anthropic.AuthenticationError):
        return "Claude didn't accept the API key. Check it in AUPedea Settings (the gear)."
    if isinstance(exc, anthropic.PermissionDeniedError):
        return "That API key isn't allowed to use this Claude model. Check the key in the Claude Console."
    if isinstance(exc, anthropic.RateLimitError):
        return "Claude is getting a lot of questions from this key right now. Try again in a minute."
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code == 402 or "credit" in str(exc).lower():
            return "The Claude account behind this key is out of credit. Add some in the Claude Console."
        if exc.status_code >= 500:
            return "Claude is having a moment (server error). Try again shortly."
        return f"Claude said: {exc.message}"
    if isinstance(exc, anthropic.APIConnectionError):
        return "I couldn't reach Claude. Check the internet connection."
    return f"Something went wrong: {exc}"


# ---------------------------------------------------------------------------
# without a key: find the command by its words, or mark text up
# ---------------------------------------------------------------------------

_MARK = re.compile(r"^\s*(?:please\s+|can you\s+|could you\s+)?(highlight|underline|circle|strike(?:\s*(?:out|through))?"
                   r"|cross\s+out)\s+(?:the\s+(?:words?|phrase|text|line)\s+)?[\"'“‘]?(.+?)[\"'”’]?"
                   r"\s*(?:on (?:this|the) page)?[.!?]?\s*$", re.I)
_READ = re.compile(r"\b(read|what does|what's on|whats on)\b.*\bpage\b|\bread (it|this|the document)\b", re.I)


def local_answer(catalog, question, mode):
    """[(op, ...)] for the director: ("say", text), ("point", cmd, note), ("click", cmd, note),
    ("mark", style, text), ("read",)."""
    m = _MARK.match(question)
    if m:
        verb = m.group(1).lower()
        style = ("circle" if verb == "circle" else "underline" if verb == "underline"
                 else "highlight" if verb == "highlight" else "strikeout")
        if mode != "do":
            return [("say", f"Switch to **Do it for me** and I'll {verb} it for you, or use the "
                            f"**{style.title()}** tool and drag over the words.")]
        return [("mark", style, m.group(2).strip())]
    if _READ.search(question):
        return [("read",)]
    found = catalog_mod.find(catalog, question)
    if not found:
        return [("say", "Hmm, I couldn't find a button for that. Try other words (like \"rotate\", \"sign\" or "
                        "\"convert to Word\"), ask me to \"highlight\" some words, or add a Claude or Hugging Face "
                        "key in my settings (the gear) and I can do much more.")]
    best = found[0]
    others = [c for c in found[1:3] if c is not best]
    ops = []
    if not best.action.isEnabled():
        ops.append(("point", best, "greyed out for now"))
        ops.append(("say", f"That's **{best.where()}**, but it's greyed out right now (usually that means "
                           "a PDF needs to be open first)."))
        return ops
    if mode == "do":
        ops.append(("click", best, best.label))
        text = f"Done: **{best.where()}**."
    else:
        ops.append(("point", best, best.label))
        text = f"It's **{best.where()}**" + (f" (shortcut **{best.keys}**)." if best.keys else ".")
    if others:
        text += " Not quite it? Maybe " + " or ".join(f"**{c.where()}**" for c in others) + "."
    ops.append(("say", text))
    return ops
