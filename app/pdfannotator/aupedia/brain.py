"""AUPedia's thinking. It can think with Claude (Anthropic) or with a model
on Hugging Face, using the person's own key and the model they choose; the
same tools work with both: point_at and click (the app), read_page, draw,
shape, mark_text and write_text (the paper), and go_to_page. Without a key, a
local finder matches the question's words to a command, and can mark text up.

Keys are kept encrypted for this Windows user. Questions, the list of
commands and what's open (file names, page, tool) are sent to the chosen
service; a page's text (and, for models that can see, a picture of it) is
sent only when AUPedia reads or marks up that page."""
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
            "hf_vision": str(s.value("aupedia/hf_vision", "false")) == "true"}


def save_choices(provider=None, claude_model=None, hf_model=None, hf_vision=None):
    s = theme._settings()
    for key, value in (("provider", provider), ("claude_model", claude_model), ("hf_model", hf_model)):
        if value:
            s.setValue(f"aupedia/{key}", value)
    if hf_vision is not None:
        s.setValue("aupedia/hf_vision", "true" if hf_vision else "false")


def make_provider():
    """The chosen service if it has a key, else the other one if that has a key, else None."""
    c = choices()
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

PERSONA = """You are AUPedia, the helper who lives inside AUPedean Annotator, a Windows app for reading, \
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


def user_turn(question, mode, state, spoken=False):
    return (question.strip() + "\n\n<mode>" + ("do it" if mode == "do" else "show me") + "</mode>\n"
            + ("<input>spoken</input>\n" if spoken else "") + "<app_state>\n" + state + "\n</app_state>")


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
    """A chat model on Hugging Face Inference Providers (the OpenAI-style chat API, with tools)."""
    kind = HUGGINGFACE

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
            "model": self.model, "max_tokens": 8192, "tool_choice": "auto",
            "messages": [{"role": "system", "content": system}] + conversation,
            "tools": [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                        "parameters": t["parameters"]}} for t in TOOLS]})
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
            return "Hugging Face didn't accept the token. Check it in AUPedia Settings (the gear)."
        if exc.status == 402:
            return "The Hugging Face account behind this token is out of credit for this month."
        if exc.status == 403:
            return ("That Hugging Face token can't call models: make a fine-grained token with the "
                    "\"Make calls to Inference Providers\" permission.")
        if exc.status == 429:
            return "Hugging Face is getting a lot of questions from this token. Try again in a minute."
        if exc.status in (400, 404, 422) and detail and "tool" in str(detail).lower():
            return "That Hugging Face model can't use tools. Pick another model in AUPedia Settings."
        return f"Hugging Face said: {detail}"
    try:
        import anthropic
    except ImportError:
        return "AUPedia's Claude part isn't installed in this copy of the app."
    if isinstance(exc, anthropic.AuthenticationError):
        return "Claude didn't accept the API key. Check it in AUPedia Settings (the gear)."
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
