#!/usr/bin/env python3
"""Render the profile README as a stack of animated SVG cards.

    python scripts/build_cards.py            # animated cards -> cards/ + README.md
    STATIC=1 python scripts/build_cards.py   # final frame, no animation -> cards-static/ (+ preview.html)

Everything here must survive being shown through <img>: no scripts, no external
requests, fonts embedded as subset base64 WOFF, motion via CSS keyframes and SMIL.
SMIL is used only for decoration, because CSS cannot switch it off under
prefers-reduced-motion; those groups carry class "smil" and are hidden there.
Content motion (entrances, bars, graph) is CSS and is cancelled in the same media query.
"""
import base64
import calendar
import datetime as dt
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
STATIC = os.environ.get("STATIC") == "1"
OUT = ROOT / ("cards-static" if STATIC else "cards")
DATA = ROOT / "data" / "contributions.json"

# ------------------------------------------------------------------ config
CFG = dict(
    name="Abhimanyu Sharma",
    tagline="Full-stack AI developer: ML, systems and the web.",
    portrait="assets/portrait.jpg",  # optional; hero falls back to a monogram
    max_repos=4,
    tiles=[  # label, sub line, href, icon
        ("LinkedIn", "in/abhimanyu254a", "https://www.linkedin.com/in/abhimanyu254a/", "in"),
        ("Email", "abhimanyu254a@gmail.com", "mailto:abhimanyu254a@gmail.com", "mail"),
        ("GitHub", "@Abhimanyu254a", "https://github.com/Abhimanyu254a", "git"),
    ],
    quote='"That which does not kill us makes us stronger." Friedrich Nietzsche',
)

# Taken from the old README: tokyonight card theme + the #00FFFF accent.
P = dict(
    bg="#1a1b27", surface="#1f2335", line="#2f334d", text="#c0caf5",
    muted="#8b91b5", brand="#00ffff", violet="#bf91f3",
)

FONTS = {  # key -> (file, family, weight range, style)
    "serif": ("fonts/InstrumentSerif-Regular.ttf", "Instrument Serif", "400", "normal"),
    "serifi": ("fonts/InstrumentSerif-Italic.ttf", "Instrument Serif", "400", "italic"),
    "sans": ("fonts/Geist[wght].ttf", "Geist", "100 900", "normal"),
}
FAMCLS = {"serif": "s", "serifi": "si", "sans": "g"}

# Seconds. Each card starts later than the one above, so the page plays top to bottom.
DELAY = dict(hero=0.0, tiles=0.75, activity=1.35, repos=2.05, graph=2.95)

# ------------------------------------------------------------------ helpers
def mix(a, b, t):
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(pa, pb))


_metrics = {}


def adv(fam, s, size, wt=400):
    """Text width in px, from the font's own advance widths (no kerning)."""
    if fam not in _metrics:
        from fontTools.ttLib import TTFont
        f = TTFont(ROOT / FONTS[fam][0])
        _metrics[fam] = (f.getBestCmap(), f["hmtx"], f["head"].unitsPerEm)
    cmap, hmtx, upm = _metrics[fam]
    w = sum(hmtx[cmap.get(ord(ch), ".notdef")][0] for ch in s) / upm * size
    return w * (1 + max(0, wt - 400) * 0.00035) if fam == "sans" else w


def wrap(s, fam, size, maxw, maxlines, wt=400):
    lines, cur = [], ""
    for word in s.split():
        trial = (cur + " " + word).strip()
        if adv(fam, trial, size, wt) <= maxw or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    if len(lines) > maxlines:
        lines = lines[:maxlines]
        lines[-1] = trunc(lines[-1] + " …", fam, size, maxw, wt)
    return lines


def trunc(s, fam, size, maxw, wt=400):
    if adv(fam, s, size, wt) <= maxw:
        return s
    s = s.rstrip("… ")
    while s and adv(fam, s + "…", size, wt) > maxw:
        s = s[:-1]
    return s.rstrip() + "…"


def rr(x, y, w, h, r):
    return (f"M{x + r:g},{y:g} H{x + w - r:g} A{r},{r} 0 0 1 {x + w:g},{y + r:g} V{y + h - r:g} "
            f"A{r},{r} 0 0 1 {x + w - r:g},{y + h:g} H{x + r:g} A{r},{r} 0 0 1 {x:g},{y + h - r:g} "
            f"V{y + r:g} A{r},{r} 0 0 1 {x + r:g},{y:g} Z")


def circ(cx, cy, r):
    return f"M{cx - r:g},{cy:g} a{r},{r} 0 1,0 {2 * r:g},0 a{r},{r} 0 1,0 {-2 * r:g},0"


def star(cx, cy, r):
    pts = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rad = r if i % 2 == 0 else r * 0.45
        pts.append(f"{cx + rad * math.cos(a):.1f},{cy + rad * math.sin(a):.1f}")
    return " ".join(pts)


def kfmt(n):
    return f"{n / 1000:.1f}k".replace(".0k", "k") if n >= 1000 else str(n)


def dstr(d):
    return f"{d.day} {calendar.month_abbr[d.month]} {d.year}"


# ------------------------------------------------------------------ fonts
def font_face(key, chars):
    src, family, weight, style = FONTS[key]
    tmp = Path(tempfile.mkdtemp()) / f"{key}.woff"
    exe = shutil.which("pyftsubset")
    cmd = [exe] if exe else [sys.executable, "-m", "fontTools.subset"]
    cmd += [str(ROOT / src), f"--text={chars}", "--flavor=woff", "--no-hinting",
            "--layout-features=kern", f"--output-file={tmp}"]
    subprocess.run(cmd, check=True, capture_output=True)
    b64 = base64.b64encode(tmp.read_bytes()).decode()
    shutil.rmtree(tmp.parent, ignore_errors=True)
    return (f"@font-face{{font-family:'{family}';font-style:{style};font-weight:{weight};"
            f"src:url(data:font/woff;base64,{b64}) format('woff')}}")


BASE_CSS = (
    ".s{font-family:'Instrument Serif',Georgia,serif}"
    ".si{font-family:'Instrument Serif',Georgia,serif;font-style:italic}"
    ".g{font-family:'Geist',system-ui,-apple-system,'Segoe UI',sans-serif}"
)

ANIM_CSS = (
    ".fu{animation:fu .8s cubic-bezier(.2,.7,.2,1) var(--d,0s) both}"
    "@keyframes fu{from{opacity:0;transform:translateY(10px)}}"
    ".fi{animation:fi .9s ease-out var(--d,0s) both}"
    "@keyframes fi{from{opacity:0}}"
    ".dr{stroke-dasharray:1;animation:dr 1.2s cubic-bezier(.4,0,.2,1) var(--d,0s) both}"
    "@keyframes dr{from{stroke-dashoffset:1}to{stroke-dashoffset:0}}"
    ".gr{transform-box:fill-box;transform-origin:50% 100%;animation:gr .9s cubic-bezier(.2,.8,.2,1) var(--d,0s) both}"
    "@keyframes gr{from{transform:scaleY(0)}}"
    ".pop{transform-box:fill-box;transform-origin:50% 50%;animation:pop .5s ease-out var(--d,0s) both}"
    "@keyframes pop{from{opacity:0;transform:scale(.3)}}"
    "@media (prefers-reduced-motion:reduce){*{animation:none!important}.smil{display:none}}"
)


# ------------------------------------------------------------------ card builder
class Card:
    def __init__(self, name, w, h, label):
        self.name, self.w, self.h, self.label = name, w, h, label
        self.defs, self.body, self.chars, self.fams = [], [], set(), set()

    def add(self, s):
        self.body.append(s)

    def frame(self, d, r=18, fill=None):
        self.add(f'<path d="{rr(.5, .5, self.w - 1, self.h - 1, r)}" fill="{fill or P["bg"]}"/>')
        self.add(f'<path class="dr" pathLength="1" style="--d:{d:.2f}s" d="{rr(.5, .5, self.w - 1, self.h - 1, r)}" '
                 f'fill="none" stroke="{P["line"]}" stroke-width="1.2"/>')

    def t(self, x, y, s, size, fam="sans", wt=400, fill=None, anchor="start", d=None, anim="fu"):
        self.chars.update(s)
        self.fams.add(fam)
        cls = FAMCLS[fam] + (f" {anim}" if d is not None else "")
        a = f' text-anchor="{anchor}"' if anchor != "start" else ""
        w = f' font-weight="{wt}"' if fam == "sans" else ""
        st = f' style="--d:{d:.2f}s"' if d is not None else ""
        self.add(f'<text x="{x:g}" y="{y:g}" class="{cls}" font-size="{size:g}"{w} '
                 f'fill="{fill or P["text"]}"{a}{st}>{escape(s)}</text>')

    def smil(self, svg):
        if not STATIC:
            self.add(f'<g class="smil">{svg}</g>')

    def render(self):
        faces = "".join(font_face(k, "".join(sorted(self.chars))) for k in sorted(self.fams))
        css = faces + BASE_CSS + ("" if STATIC else ANIM_CSS)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" '
                f'width="{self.w}" height="{self.h}" role="img" aria-label="{escape(self.label)}">'
                f'<title>{escape(self.label)}</title><style>{css}</style>'
                f'<defs>{"".join(self.defs)}</defs>{"".join(self.body)}</svg>')


# ------------------------------------------------------------------ data
def load_data():
    raw = json.loads(DATA.read_text())
    days = sorted((dt.date.fromisoformat(d["date"]), int(d["count"])) for d in raw["days"])
    last = days[-1][0]
    y, m = last.year, last.month - 11
    while m < 1:
        m += 12
        y -= 1
    start = dt.date(y, m, 1)
    days = [d for d in days if d[0] >= start]
    return raw, days


def streaks(days):
    longest = cur = 0
    for _, c in days:
        cur = cur + 1 if c > 0 else 0
        longest = max(longest, cur)
    i = len(days) - 1
    if days[i][1] == 0:  # today may not be counted yet
        i -= 1
    now = 0
    while i >= 0 and days[i][1] > 0:
        now += 1
        i -= 1
    return now, longest


# ------------------------------------------------------------------ cards
def card_hero(raw):
    W, H = 900, 290
    name = CFG["name"]
    c = Card("hero", W, H, f"{name}: {CFG['tagline']}")
    b = DELAY["hero"]
    cx, cy, R = 770, 145, 84

    c.defs.append(f'<clipPath id="cl"><path d="{rr(.5, .5, W - 1, H - 1, 18)}"/></clipPath>')
    c.defs.append(f'<radialGradient id="g1" cx=".12" cy=".05" r=".75"><stop offset="0" stop-color="{P["brand"]}" stop-opacity=".16"/>'
                  f'<stop offset="1" stop-color="{P["brand"]}" stop-opacity="0"/></radialGradient>')
    c.defs.append(f'<radialGradient id="g2" cx=".88" cy=".5" r=".45"><stop offset="0" stop-color="{P["violet"]}" stop-opacity=".16"/>'
                  f'<stop offset="1" stop-color="{P["violet"]}" stop-opacity="0"/></radialGradient>')
    c.defs.append(f'<linearGradient id="gb" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{P["brand"]}"/>'
                  f'<stop offset="1" stop-color="{P["violet"]}"/></linearGradient>')
    c.defs.append(f'<linearGradient id="sh" x1="0" x2="1"><stop offset="0" stop-color="{P["brand"]}" stop-opacity="0"/>'
                  f'<stop offset=".5" stop-color="{P["brand"]}"/><stop offset="1" stop-color="{P["brand"]}" stop-opacity="0"/></linearGradient>')

    c.frame(b)
    c.add(f'<g clip-path="url(#cl)"><rect width="{W}" height="{H}" fill="url(#g1)"/><rect width="{W}" height="{H}" fill="url(#g2)"/></g>')
    c.smil(f'<g clip-path="url(#cl)"><rect y="0" width="180" height="2" x="-180" fill="url(#sh)">'
           f'<animate attributeName="x" from="-180" to="{W}" dur="6s" begin="{b + 2.5:.1f}s" repeatCount="indefinite"/></rect></g>')

    # accent stroke draws first, then name, then tagline
    c.add(f'<path class="dr" pathLength="1" style="--d:{b + .25:.2f}s" d="M56 92 H108" stroke="{P["brand"]}" '
          f'stroke-width="3" stroke-linecap="round" fill="none"/>')
    size = min(84, 584 / (adv("serif", name, 1)))
    c.t(54, 92 + 20 + size * .72, name, size, "serif", fill="#e6e9ff", d=b + .45)
    ty = 92 + 20 + size * .72 + 52
    for i, line in enumerate(wrap(CFG["tagline"], "serifi", 28, 584, 2)):
        c.t(56, ty + i * 34, line, 28, "serifi", fill=P["muted"], d=b + .75 + i * .12)

    # portrait (optional) or monogram
    uri = portrait_uri()
    c.add(f'<path class="dr" pathLength="1" style="--d:{b + .5:.2f}s" d="{circ(cx, cy, R + 10)}" fill="none" '
          f'stroke="url(#gb)" stroke-width="2.5" stroke-linecap="round"/>')
    if uri:
        c.defs.append(f'<clipPath id="pt"><circle cx="{cx}" cy="{cy}" r="{R}"/></clipPath>')
        c.add(f'<image href="{uri}" x="{cx - R}" y="{cy - R}" width="{2 * R}" height="{2 * R}" '
              f'preserveAspectRatio="xMidYMid slice" clip-path="url(#pt)" class="fi" style="--d:{b + .8:.2f}s"/>')
    else:
        c.add(f'<circle cx="{cx}" cy="{cy}" r="{R}" fill="{P["surface"]}" stroke="{P["line"]}"/>')
        ini = "".join(w[0] for w in name.split()[:2]).upper()
        c.t(cx, cy + 24, ini, 76, "serif", fill=P["brand"], anchor="middle", d=b + .85, anim="fi")
    c.smil(f'<circle cx="{cx}" cy="{cy}" r="{R + 22}" fill="none" stroke="{P["violet"]}" stroke-opacity=".5" '
           f'stroke-width="1.2" stroke-dasharray="2 9" stroke-linecap="round">'
           f'<animateTransform attributeName="transform" type="rotate" from="0 {cx} {cy}" to="360 {cx} {cy}" '
           f'dur="70s" repeatCount="indefinite"/></circle>')
    return c


def portrait_uri():
    p = ROOT / CFG["portrait"]
    if not p.exists():
        return None
    try:
        from PIL import Image, ImageOps
        im = ImageOps.fit(Image.open(p).convert("RGB"), (360, 360), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=84, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
        return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()


def icon(c, kind, d):
    s = f'stroke="{P["brand"]}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"'
    st = f'class="fi" style="--d:{d:.2f}s"'
    if kind == "mail":
        c.add(f'<g {st} {s}><rect x="31" y="46" width="26" height="19" rx="3"/><path d="M32 48.5 L44 58 L56 48.5"/></g>')
    elif kind == "git":
        c.add(f'<g {st} {s}><circle cx="38" cy="47" r="3.5"/><circle cx="38" cy="65" r="3.5"/><circle cx="52" cy="52" r="3.5"/>'
              f'<path d="M38 50.5 V61.5 M52 55.5 C52 60 38 58 38 61.5"/></g>')
    else:
        c.t(44, 63, "in", 26, "serif", fill=P["brand"], anchor="middle", d=d, anim="fi")


def card_tile(i, tile):
    label, sub, _, kind = tile
    c = Card(f"tile-{i + 1}", 300, 112, f"{label}: {sub}")
    b = DELAY["tiles"] + i * .16
    c.frame(b, r=16)
    c.add(f'<rect x="22" y="34" width="44" height="44" rx="12" fill="{mix(P["bg"], P["brand"], .09)}" '
          f'stroke="{mix(P["bg"], P["brand"], .35)}" class="fi" style="--d:{b + .3:.2f}s"/>')
    icon(c, kind, b + .35)
    c.t(84, 54, label, 26, "serif", fill="#e6e9ff", d=b + .4)
    c.t(84, 76, trunc(sub, "sans", 12.5, 300 - 84 - 46), 12.5, "sans", fill=P["muted"], d=b + .5)
    c.add(f'<path class="dr" pathLength="1" style="--d:{b + .6:.2f}s" d="M264 42 H276 V54 M276 42 L262 56" '
          f'fill="none" stroke="{P["muted"]}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>')
    return c


def card_activity(days):
    W, H = 900, 338
    c = Card("activity", W, H, "Contributions per month, current streak, active days and busiest day")
    b = DELAY["activity"]
    c.frame(b)
    last = days[-1][0]
    first = days[0][0]
    c.t(56, 70, "Contributions per month", 34, "serif", fill="#e6e9ff", d=b + .2)
    c.t(844, 68, f"{calendar.month_abbr[first.month]} {first.year} to {calendar.month_abbr[last.month]} {last.year}",
        13, "sans", fill=P["muted"], anchor="end", d=b + .3)

    now, longest = streaks(days)
    active = sum(1 for _, c_ in days if c_ > 0)
    best = max(days, key=lambda x: (x[1], x[0]))
    boxes = [
        ("Current streak", str(now), "days", f"Longest {longest}"),
        ("Active days", str(active), f"of {len(days)}", f"{round(100 * active / len(days))}% of days"),
        ("Busiest day", f"{best[1]:,}", "contributions", dstr(best[0])),
    ]
    bw, gap = 252, 16
    for i, (lab, val, unit, sub) in enumerate(boxes):
        x, y, d = 56 + i * (bw + gap), 96, b + .45 + i * .12
        c.add(f'<rect x="{x}" y="{y}" width="{bw}" height="84" rx="12" fill="{P["surface"]}" stroke="{P["line"]}" '
              f'class="fi" style="--d:{d:.2f}s"/>')
        c.t(x + 20, y + 29, lab, 13, "sans", wt=500, fill=P["muted"], d=d + .1)
        c.t(x + bw - 20, y + 29, sub, 12.5, "sans", fill=P["muted"], anchor="end", d=d + .1)
        c.t(x + 20, y + 68, val, 46, "serif", fill=P["brand"] if i == 0 else "#e6e9ff", d=d + .15)
        c.t(x + 20 + adv("serif", val, 46) + 8, y + 67, unit, 14, "sans", fill=P["muted"], d=d + .2)

    # monthly bars
    months = []
    y_, m_ = last.year, last.month
    for _ in range(12):
        months.append((y_, m_))
        m_ -= 1
        if m_ == 0:
            m_, y_ = 12, y_ - 1
    months.reverse()
    totals = [sum(n for d_, n in days if (d_.year, d_.month) == ym) for ym in months]
    vmax = max(totals) or 1
    base, maxh = 294, 86
    c.defs.append(f'<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{P["brand"]}" stop-opacity=".9"/>'
                  f'<stop offset="1" stop-color="{P["brand"]}" stop-opacity=".22"/></linearGradient>')
    for gy in (base, base - maxh / 2, base - maxh):
        c.add(f'<path d="M56 {gy:g} H844" stroke="{P["line"]}" stroke-width="1" stroke-dasharray="3 5" fill="none"/>')
    slot, barw = (844 - 56) / 12, 36
    hi = totals.index(max(totals))
    for i, (ym, v) in enumerate(zip(months, totals)):
        x = 56 + i * slot + (slot - barw) / 2
        h = max(3, v / vmax * (maxh - 16))
        d = b + .9 + i * .07
        c.add(f'<rect x="{x:.1f}" y="{base - h:.1f}" width="{barw}" height="{h:.1f}" rx="6" '
              f'fill="{P["brand"] if i == hi else "url(#bg)"}" class="gr" style="--d:{d:.2f}s"/>')
        c.t(x + barw / 2, base - h - 8, f"{v:,}", 12.5, "sans", wt=600 if i == hi else 400,
            fill=P["brand"] if i == hi else P["muted"], anchor="middle", d=d + .35)
        c.t(x + barw / 2, base + 24, calendar.month_abbr[ym[1]], 12.5, "sans",
            fill=P["text"] if i == len(months) - 1 else P["muted"], anchor="middle", d=d)
    return c


def card_repo(i, repo):
    W, H = 410, 176
    c = Card(f"repo-{i + 1}", W, H, f"{repo['name']}: {repo.get('description') or 'repository'}")
    b = DELAY["repos"] + i * .2
    c.frame(b, r=16)
    c.t(24, 50, trunc(repo["name"], "serif", 30, W - 90), 30, "serif", fill="#e6e9ff", d=b + .3)
    c.add(f'<path class="dr" pathLength="1" style="--d:{b + .5:.2f}s" d="M{W - 40} 32 H{W - 28} V44 M{W - 28} 32 L{W - 42} 46" '
          f'fill="none" stroke="{P["muted"]}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>')
    desc = repo.get("description") or "No description yet."
    for k, line in enumerate(wrap(desc, "sans", 13.5, W - 48, 3)):
        c.t(24, 78 + k * 20, line, 13.5, "sans", fill=P["muted"], d=b + .45 + k * .08)
    fy = 152
    x = 24
    lang = repo.get("language")
    if lang:
        col = lang.get("color") or P["brand"]
        c.add(f'<circle cx="{x + 5}" cy="{fy - 4.5}" r="5" fill="{col}" class="pop" style="--d:{b + .7:.2f}s"/>')
        c.t(x + 16, fy, lang["name"], 12.5, "sans", fill=P["text"], d=b + .75)
        x += 16 + adv("sans", lang["name"], 12.5) + 22
    c.add(f'<polygon points="{star(x + 6, fy - 4.5, 6.5)}" fill="none" stroke="{P["brand"]}" stroke-width="1.4" '
          f'stroke-linejoin="round" class="fi" style="--d:{b + .8:.2f}s"/>')
    sv = kfmt(repo.get("stars", 0))
    c.t(x + 18, fy, sv, 12.5, "sans", fill=P["text"], d=b + .85)
    x += 18 + adv("sans", sv, 12.5) + 20
    c.add(f'<g class="fi" style="--d:{b + .9:.2f}s" fill="none" stroke="{P["brand"]}" stroke-width="1.4" stroke-linecap="round">'
          f'<circle cx="{x + 2}" cy="{fy - 11}" r="2"/><circle cx="{x + 10}" cy="{fy - 11}" r="2"/><circle cx="{x + 6}" cy="{fy - 1}" r="2"/>'
          f'<path d="M{x + 2} {fy - 9} V{fy - 7} Q{x + 2} {fy - 5} {x + 6} {fy - 5} Q{x + 10} {fy - 5} {x + 10} {fy - 7} V{fy - 9} M{x + 6} {fy - 5} V{fy - 3}"/></g>')
    c.t(x + 18, fy, kfmt(repo.get("forks", 0)), 12.5, "sans", fill=P["text"], d=b + .95)
    return c


def card_graph(days):
    W, H = 900, 274
    c = Card("graph", W, H, "Contribution graph for the last twelve months")
    b = DELAY["graph"]
    c.frame(b)
    total = sum(n for _, n in days)
    c.t(56, 70, "Contribution graph", 34, "serif", fill="#e6e9ff", d=b + .2)
    c.t(844, 68, f"{total:,} contributions", 13, "sans", fill=P["muted"], anchor="end", d=b + .3)

    first, last = days[0][0], days[-1][0]
    off = (first.weekday() + 1) % 7  # weeks start on Sunday
    ncols = ((last - first).days + off) // 7 + 1
    x0, y0 = 96, 116
    pitch = min(15.0, (844 - x0) / ncols)
    cell = pitch - 3.2

    nz = sorted(n for _, n in days if n > 0)
    q = [nz[int(len(nz) * f)] for f in (.25, .5, .75)] if nz else [1, 2, 3]
    ramp = [mix(P["surface"], P["line"], .55)] + [mix(P["surface"], P["brand"], t) for t in (.3, .52, .76, 1)]

    def level(n):
        return 0 if n == 0 else 1 if n <= q[0] else 2 if n <= q[1] else 3 if n <= q[2] else 4

    cols = {}
    for d_, n in days:
        idx = (d_ - first).days + off
        cols.setdefault(idx // 7, []).append((idx % 7, n, d_))
    last_xy = None
    prev_m, prev_c = None, -9
    for col in sorted(cols):
        items = cols[col]
        d = b + .55 + col * .02
        g = [f'<g style="--d:{d:.2f}s">']
        for row, n, d_ in items:
            x, y = x0 + col * pitch, y0 + row * pitch
            g.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell:.1f}" height="{cell:.1f}" rx="3" fill="{ramp[level(n)]}" class="pop"/>')
            last_xy = (x + cell / 2, y + cell / 2)
        g.append("</g>")
        c.add("".join(g))
        m = items[0][2].month
        if m != prev_m and col - prev_c >= 3 and (col > 0 or items[0][2].day <= 7):
            c.t(x0 + col * pitch, y0 - 12, calendar.month_abbr[m], 12, "sans", fill=P["muted"], d=b + .4)
            prev_c = col
        prev_m = m
    for row, name in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        c.t(56, y0 + row * pitch + cell - 1, name, 11, "sans", fill=P["muted"], d=b + .4)

    if last_xy:
        c.smil(f'<circle cx="{last_xy[0]:.1f}" cy="{last_xy[1]:.1f}" r="7" fill="none" stroke="{P["brand"]}" stroke-width="1.5" opacity="0">'
               f'<animate attributeName="r" values="7;18" dur="2.4s" begin="{b + 2.0:.1f}s" repeatCount="indefinite"/>'
               f'<animate attributeName="opacity" values="0.9;0" dur="2.4s" begin="{b + 2.0:.1f}s" repeatCount="indefinite"/></circle>')

    ly = y0 + 7 * pitch + 26
    x = 844
    c.t(x, ly, "More", 12, "sans", fill=P["muted"], anchor="end", d=b + 1.2)
    x -= adv("sans", "More", 12) + 8
    for lv in (4, 3, 2, 1, 0):
        c.add(f'<rect x="{x - 12:g}" y="{ly - 11:g}" width="12" height="12" rx="3" fill="{ramp[lv]}" class="fi" style="--d:{b + 1.2:.2f}s"/>')
        x -= 16
    c.t(x - 4, ly, "Less", 12, "sans", fill=P["muted"], anchor="end", d=b + 1.2)
    return c


# ------------------------------------------------------------------ README
def layout(prefix, repos):
    n = CFG["name"]
    out = [f'<p align="center"><img src="{prefix}hero.svg" alt="{n}: {CFG["tagline"]}" width="100%"></p>', '<p align="center">']
    for i, (label, sub, href, _) in enumerate(CFG["tiles"]):
        out.append(f'  <a href="{href}"><img src="{prefix}tile-{i + 1}.svg" alt="{label}: {sub}" width="32.5%"></a>')
    out += ["</p>", f'<p align="center"><img src="{prefix}activity.svg" alt="Contributions per month with streak, active days and busiest day" width="100%"></p>']
    for row in range(0, len(repos), 2):
        out.append('<p align="center">')
        for i in range(row, min(row + 2, len(repos))):
            r = repos[i]
            out.append(f'  <a href="{r["url"]}"><img src="{prefix}repo-{i + 1}.svg" alt="{r["name"]}" width="49%"></a>')
        out.append("</p>")
    out.append(f'<p align="center"><img src="{prefix}graph.svg" alt="Contribution graph, last twelve months" width="100%"></p>')
    if CFG.get("quote"):
        out.append(f'<p align="center"><sub>{escape(CFG["quote"])}</sub></p>')
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ main
def main():
    raw, days = load_data()
    repos = raw.get("repos", [])[: CFG["max_repos"]]
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("*.svg"):
        old.unlink()
    cards = [card_hero(raw)]
    cards += [card_tile(i, t) for i, t in enumerate(CFG["tiles"])]
    cards.append(card_activity(days))
    cards += [card_repo(i, r) for i, r in enumerate(repos)]
    cards.append(card_graph(days))
    for c in cards:
        svg = c.render()
        (OUT / f"{c.name}.svg").write_text(svg, encoding="utf-8")
        print(f"{c.name:10s} {len(svg) / 1024:6.1f} KB")
    if STATIC:
        body = layout("", repos)
        (OUT / "preview.html").write_text(
            '<!doctype html><meta charset="utf-8"><title>Profile preview (final frame)</title>'
            '<body style="margin:0;background:#0d1117;color:#8b949e;font:14px system-ui"><main style="max-width:830px;margin:0 auto;padding:24px 0">'
            f"{body}</main>", encoding="utf-8")
        print(f"final frame -> {OUT}/preview.html (screenshot it, do not commit {OUT.name}/)")
    else:
        (ROOT / "README.md").write_text(layout("cards/", repos), encoding="utf-8")
        print("README.md written")


if __name__ == "__main__":
    main()
