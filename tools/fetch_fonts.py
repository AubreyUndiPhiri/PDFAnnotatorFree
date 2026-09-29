"""Download the open-licence font library bundled with the app.

    python tools/fetch_fonts.py            # fetch every family in FAMILIES
    python tools/fetch_fonts.py Lora Caveat   # just these

Fonts come from the Google Fonts repository (github.com/google/fonts) and are
licensed under the SIL Open Font License, Apache 2.0 or the Ubuntu Font
Licence, all of which allow bundling and embedding in PDFs. Each family's
licence file is saved next to its fonts.

Variable fonts are cut into static Regular / Bold / Italic / Bold Italic
files (PDF viewers can't use variable fonts reliably), and every file is
trimmed to Latin, Latin Extended and common punctuation to stay small.
Output: app/assets/fonts/library/<Category>/<Family>/<Family>-<Style>.ttf
"""
import io
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "app" / "assets" / "fonts" / "library"
RAW = "https://raw.githubusercontent.com/google/fonts/main"
LICENCE_DIRS = ("ofl", "apache", "ufl")
LICENCE_FILES = ("OFL.txt", "LICENSE.txt", "UFL.txt")

# Category -> families. "text" families get all four styles; the rest just
# the styles they have among Regular/Bold.
FAMILIES = {
    "Sans Serif": ["Roboto", "Open Sans", "Lato", "Montserrat", "Poppins", "Inter", "Nunito", "Raleway",
                   "Work Sans", "Source Sans 3", "PT Sans", "Fira Sans", "Ubuntu", "Oswald", "Quicksand",
                   "Josefin Sans", "Mulish", "Rubik", "Barlow", "Karla"],
    "Serif": ["Merriweather", "Lora", "Playfair Display", "EB Garamond", "Libre Baskerville", "Crimson Text",
              "PT Serif", "Source Serif 4", "Cormorant Garamond", "Noto Serif", "Bitter", "Libre Caslon Text",
              "Spectral", "Zilla Slab"],
    "Monospace": ["Roboto Mono", "Source Code Pro", "JetBrains Mono", "Fira Code", "Inconsolata", "IBM Plex Mono",
                  "Space Mono", "Courier Prime"],
    "Handwriting": ["Caveat", "Dancing Script", "Pacifico", "Great Vibes", "Satisfy", "Indie Flower",
                    "Shadows Into Light", "Patrick Hand", "Kalam", "Homemade Apple", "Permanent Marker",
                    "Sacramento", "Allura", "Parisienne", "Amatic SC", "Architects Daughter", "Gloria Hallelujah",
                    "Rock Salt", "Kaushan Script", "Courgette", "Covered By Your Grace", "Nothing You Could Do",
                    "Reenie Beanie", "Just Another Hand", "Mr Dafoe", "Alex Brush", "Yellowtail", "Cookie"],
    "Display": ["Bebas Neue", "Abril Fatface", "Lobster", "Righteous", "Alfa Slab One", "Anton", "Cinzel",
                "Comfortaa", "Bungee", "Staatliches", "Fredoka", "Press Start 2P", "Special Elite", "Monoton"],
}
TEXT_CATEGORIES = {"Sans Serif", "Serif", "Monospace"}
STYLES = {("normal", 400): "Regular", ("normal", 700): "Bold", ("italic", 400): "Italic", ("italic", 700): "Bold Italic"}

# Latin, Latin-1, Latin Extended-A/B, general punctuation, currency, letterlike, arrows, math basics
KEEP_UNICODES = [*range(0x20, 0x250), *range(0x2000, 0x2070), *range(0x20A0, 0x20C1), *range(0x2100, 0x2150),
                 *range(0x2190, 0x2200), 0x2212, 0x2215, 0x2219, 0x221E, 0x2248, 0x2260, 0x2264, 0x2265, 0xFB01, 0xFB02]


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def find_family(name):
    slug = re.sub(r"[^a-z0-9]", "", name.lower())
    for lic in LICENCE_DIRS:
        meta = get(f"{RAW}/{lic}/{slug}/METADATA.pb")
        if meta:
            return f"{RAW}/{lic}/{slug}", meta.decode("utf-8")
    return None, None


def parse_fonts(meta):
    """[(filename, style, weight)] from METADATA.pb."""
    fonts = []
    for block in re.findall(r"fonts\s*\{(.*?)\}", meta, re.S):
        fn = re.search(r'filename:\s*"([^"]+)"', block)
        st = re.search(r'style:\s*"([^"]+)"', block)
        wt = re.search(r"weight:\s*(\d+)", block)
        if fn:
            fonts.append((fn.group(1), st.group(1) if st else "normal", int(wt.group(1)) if wt else 400))
    return fonts


def name_font(font, family, style):
    """Standard four-style naming so apps group the styles as one family."""
    bold, italic = "Bold" in style, "Italic" in style
    name = font["name"]
    for nid in (16, 17, 21, 22, 25):
        name.removeNames(nameID=nid)
    full = family if style == "Regular" else f"{family} {style}"
    ps = f"{re.sub(r'[^A-Za-z0-9]', '', family)}-{style.replace(' ', '')}"
    for nid, value in ((1, family), (2, style), (3, f"{ps};fetched"), (4, full), (6, ps)):
        name.setName(value, nid, 3, 1, 0x409)
        name.setName(value, nid, 1, 0, 0)
    os2 = font["OS/2"]
    os2.usWeightClass = 700 if bold else 400
    sel = os2.fsSelection & ~0b1100001  # clear ITALIC, BOLD, REGULAR
    sel |= (1 if italic else 0) | (1 << 5 if bold else 0) | (1 << 6 if not (bold or italic) else 0)
    os2.fsSelection = sel
    os2.fsType = 0
    font["head"].macStyle = (1 if bold else 0) | (2 if italic else 0)
    if "post" in font:
        font["post"].italicAngle = font["post"].italicAngle if italic else 0


def trim(font):
    options = subset.Options()
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.notdef_outline = True
    options.layout_features = ["kern", "liga", "clig", "calt", "ccmp", "locl", "mark", "mkmk"]
    options.hinting = False
    options.desubroutinize = True
    s = subset.Subsetter(options)
    s.populate(unicodes=KEEP_UNICODES)
    s.subset(font)


def static_instance(data, weight):
    font = TTFont(io.BytesIO(data))
    if "fvar" not in font:
        return font
    limits = {}
    for axis in font["fvar"].axes:
        if axis.axisTag == "wght":
            limits["wght"] = max(axis.minValue, min(axis.maxValue, weight))
        else:
            limits[axis.axisTag] = axis.defaultValue
    return instancer.instantiateVariableFont(font, limits, inplace=False, updateFontNames=False)


def fetch(name, category):
    base, meta = find_family(name)
    if not base:
        print(f"  !! {name}: not found in google/fonts")
        return 0
    wanted = STYLES if category in TEXT_CATEGORIES else {k: v for k, v in STYLES.items() if k[0] == "normal"}
    files = parse_fonts(meta)
    folder = OUT / category / name
    folder.mkdir(parents=True, exist_ok=True)
    written = 0
    cache = {}
    for (style_kind, weight), style in wanted.items():
        # a static file with that exact weight, else a variable file covering it
        pick = next((f for f in files if f[1] == style_kind and f[2] == weight and "[" not in f[0]), None)
        if pick is None:
            pick = next((f for f in files if f[1] == style_kind and "[" in f[0]), None)
        if pick is None:
            continue
        if pick[0] not in cache:
            cache[pick[0]] = get(f"{base}/{pick[0]}")
        data = cache[pick[0]]
        if not data:
            continue
        if "[" in pick[0]:
            font = static_instance(data, weight)
            if "fvar" in font:
                continue
            axis_ok = True
        else:
            font = TTFont(io.BytesIO(data))
            axis_ok = True
        if not axis_ok:
            continue
        name_font(font, name, style)
        trim(font)
        font.save(str(folder / f"{re.sub(r'[^A-Za-z0-9]', '', name)}-{style.replace(' ', '')}.ttf"))
        written += 1
    for lic in LICENCE_FILES:
        text = get(f"{base}/{lic}")
        if text:
            (folder / lic).write_bytes(text)
            break
    # a variable font whose weight axis doesn't reach 700 would give a "Bold"
    # identical to Regular; drop such duplicates
    reg = folder / f"{re.sub(r'[^A-Za-z0-9]', '', name)}-Regular.ttf"
    for dup in ("Bold", "BoldItalic"):
        p = folder / f"{re.sub(r'[^A-Za-z0-9]', '', name)}-{dup}.ttf"
        src = folder / f"{re.sub(r'[^A-Za-z0-9]', '', name)}-{'Regular' if dup == 'Bold' else 'Italic'}.ttf"
        if p.exists() and src.exists() and _same_outlines(p, src):
            p.unlink()
            written -= 1
    print(f"  {name}: {written} style(s)")
    return written


def _same_outlines(a, b):
    fa, fb = TTFont(str(a)), TTFont(str(b))
    ga, gb = fa.getBestCmap().get(ord("H")), fb.getBestCmap().get(ord("H"))
    if not ga or not gb or "glyf" not in fa or "glyf" not in fb:
        return False
    return fa["hmtx"][ga] == fb["hmtx"][gb] and fa["glyf"][ga].coordinates == fb["glyf"][gb].coordinates


def main():
    only = set(sys.argv[1:])
    total = 0
    for category, names in FAMILIES.items():
        print(category)
        for name in names:
            if only and name not in only:
                continue
            try:
                total += fetch(name, category)
            except Exception as e:  # keep going; report at the end
                print(f"  !! {name}: {e}")
    print(f"{total} font files in {OUT}")


if __name__ == "__main__":
    main()
