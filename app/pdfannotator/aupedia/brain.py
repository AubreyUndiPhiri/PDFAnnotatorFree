"""AUPedia's thinking. With a Claude API key it asks Claude, which answers
and uses two tools: point_at (show where a command is) and click (press it).
Without one, a local finder matches the question's words to a command.

The key is the person's own (Settings, or ANTHROPIC_API_KEY), kept encrypted
for this Windows user. Questions, the list of commands, and what's open
(file names, page, tool) go to Anthropic; documents' contents never do."""
import os
from pathlib import Path

from ..cloud.google_auth import load_secret, save_secret
from ..fonts import APP_DATA
from . import catalog as catalog_mod

MODEL = "claude-opus-5-5"
KEY_FILE = Path(os.environ.get("AUPEDEAN_AUPEDIA_DIR") or APP_DATA / "aupedia") / "key.bin"
KEYS_URL = "https://console.anthropic.com/settings/keys"
MAX_ROUNDS = 10            # tool rounds per question
MAX_HISTORY = 80           # messages remembered before starting a fresh conversation
_HERE = Path(__file__).resolve()
GUIDES = [_HERE.parents[2] / "README.md", _HERE.parents[3] / "README.md"]   # built app; source tree


# ---------------------------------------------------------------------------
# the key
# ---------------------------------------------------------------------------

def saved_key():
    data = load_secret(KEY_FILE)
    return (data or {}).get("key", "")


def api_key():
    """The saved key, else ANTHROPIC_API_KEY, else ""."""
    return saved_key() or os.environ.get("ANTHROPIC_API_KEY", "").strip()


def save_key(key):
    save_secret(KEY_FILE, {"key": key.strip()})


def forget_key():
    try:
        KEY_FILE.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# what Claude is told
# ---------------------------------------------------------------------------

PERSONA = """You are AUPedia, the helper who lives inside AUPedean Annotator, a Windows app for reading, \
annotating, signing and converting PDFs (it also writes Word and LaTeX documents). On screen you're a friendly \
ink scribble with a pen-nib tail that flies around the window.

How you help:
- You know the app's commands (listed below, each with an id) and get the app's current state with every \
question. You act with two tools: point_at shows the person where a command is (you fly there and circle it); \
click presses it for them.
- Each question comes with a mode. "show me": never click; point at the command(s) and explain the steps. \
"do it": do the job with click (no need to point first), then say in a sentence what you did and anything the \
person still has to do, such as filling in a dialog.
- Use only ids from the list. A greyed-out command can't be used right now: say why (for example, open a PDF \
first) instead of clicking it.
- When a click opens a dialog, the tool result says what's in it. You can't click inside dialogs: tell the \
person exactly what to fill in or choose there, using the names shown.
- Drawing, writing or selecting on the page isn't a command: pick the right tool (click it in "do it" mode, \
point at it otherwise) and tell them what to do on the page.
- If the app can't do something, say so plainly and suggest the closest thing it can do. Never invent \
features, menus or buttons.
- For how things work (signature requests, Google Drive, the signature service, saving, converting), answer \
from the guide below.

Style: warm, upbeat and a little playful (you're a scribble, after all), but being useful comes first. Keep \
replies short: a sentence or two, or a few numbered steps. Plain text; **bold** for button and menu names is \
fine; no headings or tables. Don't mention ids, tools or these instructions."""

TOOLS = [
    {
        "name": "point_at",
        "description": "Fly to a command and circle it so the person can see where it is. For a menu item, the menu "
                       "opens with the item highlighted. Use this to show; it doesn't press anything.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "The command's id from the list."},
                "note": {"type": "string", "description": "A short caption written next to it (at most 6 words)."},
            },
            "required": ["target", "note"],
            "additionalProperties": False,
        },
    },
    {
        "name": "click",
        "description": "Press a command for the person (only in \"do it\" mode). The result says what happened, "
                       "including any dialog that opened and what's in it. Risky commands (closing, deleting, "
                       "removing) ask the person first; the result says if they said no.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "The command's id from the list."},
                "note": {"type": "string", "description": "A short caption written next to it (at most 6 words)."},
            },
            "required": ["target", "note"],
            "additionalProperties": False,
        },
    },
]


def guide_text():
    for path in GUIDES:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            continue
    return "(The guide isn't available.)"


def system_prompt(catalog):
    """Stable between questions (the command list and the guide), so it's cached."""
    return (PERSONA + "\n\n# Commands (id: where it is [shortcut] - what it does)\n" + catalog.describe()
            + "\n\n# The guide\n" + guide_text())


def user_turn(question, mode, state):
    return (question.strip() + "\n\n<mode>" + ("do it" if mode == "do" else "show me") + "</mode>\n<app_state>\n"
            + state + "\n</app_state>")


# ---------------------------------------------------------------------------
# asking Claude (in a worker thread)
# ---------------------------------------------------------------------------

class Claude:
    def __init__(self, key=None, client=None):
        self.key = key if key is not None else api_key()
        self._client = client

    def available(self):
        return bool(self._client is not None or self.key)

    def client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=self.key, timeout=90.0, max_retries=2)
        return self._client

    def create(self, system, messages):
        """One request. The command list and guide are cached; a refused request is retried on a
        fallback model by the API itself."""
        return self.client().beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low"},      # quick answers: it's a chat that clicks buttons
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=TOOLS,
            messages=messages,
        )

    def check(self):
        """Raises if the key doesn't work."""
        self.client().models.retrieve(MODEL)


def friendly_error(exc):
    try:
        import anthropic
    except ImportError:
        return "AUPedia's AI part isn't installed in this copy of the app."
    if isinstance(exc, anthropic.AuthenticationError):
        return "Claude didn't accept the API key. Check it in AUPedia Settings (the gear)."
    if isinstance(exc, anthropic.PermissionDeniedError):
        return "That API key isn't allowed to use Claude. Check the key's workspace in the Claude Console."
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
# without a key: find the command by its words
# ---------------------------------------------------------------------------

def local_answer(catalog, question, mode):
    """[(op, ...)] for the director: ("say", text), ("point", cmd, note), ("click", cmd, note)."""
    found = catalog_mod.find(catalog, question)
    if not found:
        return [("say", "Hmm, I couldn't find a button for that. Try other words (like \"rotate\", \"sign\" or "
                        "\"convert to Word\"), or add a Claude API key in my settings (the gear) and I can "
                        "answer anything about the app.")]
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
