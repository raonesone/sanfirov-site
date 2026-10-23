#!/usr/bin/env python3
"""Сборка сайта sanfirov.art.

Читает content/site.json и content/projects.json, картинки из media/,
и собирает готовый статический сайт в папку _site/.

    python3 build.py            собрать сайт
    python3 build.py --serve    собрать и открыть на http://localhost:8000
"""
import base64
import functools
import io
import hashlib
import html
import http.server
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONTENT = ROOT / "content"
MEDIA = ROOT / "media"
STATIC = ROOT / "static"
OUT = ROOT / "_site"
CACHE = ROOT / ".cache" / "img"

LANGS = ("en", "ru")
PREFIX = {"en": "", "ru": "/ru"}
PLACEHOLDER_COLORS = ["#1F2B34", "#3A2B23", "#2B3227", "#262236", "#3D3423", "#1E302F",
                      "#35221E", "#2F2F2C", "#252B37", "#362A2F", "#1D2630", "#312A1F"]
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")

try:
    from PIL import Image, ImageOps
except ImportError:  # local preview still works, just without resizing
    Image = None

warnings = []


def esc(s):
    return html.escape(str(s), quote=True)


def fail(msg):
    sys.exit("ОШИБКА: " + msg)


# ---------------------------------------------------------------- content

def load_json(name):
    path = CONTENT / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        fail(f"{path.relative_to(ROOT)}, строка {e.lineno}: {e.msg}")


def kinescope_id(value):
    """Accept an ID, a share link or an embed link."""
    value = (value or "").strip()
    if not value:
        return ""
    value = value.split("?")[0].split("#")[0].rstrip("/")
    return value.rsplit("/", 1)[-1]


def load_projects():
    projects = load_json("projects.json")
    seen = set()
    out = []
    for i, p in enumerate(projects, 1):
        where = f"projects.json, проект №{i}"
        slug = p.get("slug", "")
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug):
            fail(f"{where}: slug «{slug}» — только латиница в нижнем регистре, цифры и дефис")
        if slug in seen:
            fail(f"{where}: slug «{slug}» уже занят другим проектом")
        seen.add(slug)
        for key in ("title", "type"):
            for lang in LANGS:
                if not (p.get(key) or {}).get(lang):
                    fail(f"{where} ({slug}): нет поля {key}.{lang}")
        if p.get("hidden"):
            continue
        p["kinescope_id"] = kinescope_id(p.get("kinescope"))
        p["color"] = PLACEHOLDER_COLORS[(len(out)) % len(PLACEHOLDER_COLORS)]
        if p.get("placeholder"):
            warnings.append(f"{slug}: это заглушка (placeholder: true)")
        elif not p["kinescope_id"]:
            warnings.append(f"{slug}: нет ссылки на Kinescope")
        out.append(p)
    return out


# ---------------------------------------------------------------- images

@functools.lru_cache(maxsize=None)
def image(src, widths, quality=80, lossless=False):
    """Resize `src` to each width (never upscaling), encode WebP.

    Returns {"src", "srcset", "w", "h"} with URLs under /img/.
    Encoded files are cached in .cache/img by content hash.
    """
    src = Path(src)
    digest = hashlib.sha1(src.read_bytes() + f"{widths}{quality}{lossless}".encode()).hexdigest()[:10]
    stem = re.sub(r"[^a-z0-9-]+", "-", src.stem.lower())
    (OUT / "img").mkdir(parents=True, exist_ok=True)

    if Image is None:
        name = f"{stem}.{digest}{src.suffix.lower()}"
        shutil.copyfile(src, OUT / "img" / name)
        return {"src": f"/img/{name}", "srcset": "", "w": 1600, "h": 900}

    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGBA" if lossless else "RGB")
        targets = sorted({min(w, im.width) for w in widths})
        variants = []
        for w in targets:
            h = round(im.height * w / im.width)
            name = f"{stem}-{w}.{digest}.webp"
            cached = CACHE / name
            if not cached.exists():
                CACHE.mkdir(parents=True, exist_ok=True)
                small = im.resize((w, h), Image.LANCZOS) if w != im.width else im
                if lossless:
                    small.save(cached, "WEBP", lossless=True, quality=100, method=6)
                else:
                    small.save(cached, "WEBP", quality=quality, method=6)
            shutil.copyfile(cached, OUT / "img" / name)
            variants.append((f"/img/{name}", w, h))
        tiny = im.resize((24, max(1, round(24 * im.height / im.width))), Image.LANCZOS)
        buf = io.BytesIO()
        tiny.convert("RGB").save(buf, "WEBP", quality=50)
        lqip = "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()
    url, w, h = variants[-1]
    return {
        "lqip": lqip,
        "src": url,
        "srcset": ", ".join(f"{u} {vw}w" for u, vw, _ in variants),
        "w": w,
        "h": h,
    }


def img_tag(info, alt="", sizes="100vw", cls="", eager=False, high=False, style=""):
    attrs = [f'src="{info["src"]}"']
    if info["srcset"]:
        attrs.append(f'srcset="{info["srcset"]}" sizes="{sizes}"')
    attrs.append(f'width="{info["w"]}" height="{info["h"]}" alt="{esc(alt)}"')
    if cls:
        attrs.append(f'class="{cls}"')
    if style:
        attrs.append(f'style="{style}"')
    attrs.append('decoding="async"')
    attrs.append('loading="eager"' if eager else 'loading="lazy"')
    if high:
        attrs.append('fetchpriority="high"')
    return "<img " + " ".join(attrs) + ">"


def find_poster(p):
    if p.get("poster"):
        path = MEDIA / "posters" / p["poster"]
        if not path.exists():
            fail(f"{p['slug']}: нет файла media/posters/{p['poster']}")
        return path
    for ext in IMAGE_EXT:
        path = MEDIA / "posters" / (p["slug"] + ext)
        if path.exists():
            return path
    if not p.get("placeholder"):
        warnings.append(f"{p['slug']}: нет обложки в media/posters/")
    return None


def find_stills(p):
    folder = MEDIA / "stills" / p["slug"]
    if not folder.is_dir():
        return []
    return sorted(f for f in folder.iterdir() if f.suffix.lower() in IMAGE_EXT)


POSTER_WIDTHS = (480, 800, 1200, 1600, 1920)
TILE_SIZES = "(max-width: 640px) calc(100vw - 32px), (max-width: 1000px) 50vw, 33vw"


# ---------------------------------------------------------------- page parts

ICONS = {
    "Vimeo": '<path d="M2.5 8.5C4 7 5.5 6 6.5 6c1.5 0 1.8 2 2.3 4.5.7 3.5 1.4 7 2.5 7 1.7 0 5.7-5 7.5-8.5 1.5-3 .7-4.7-1.3-4.5-1.7.1-3 1.3-3.7 3.1"/>',
    "Instagram": '<rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="12" cy="12" r="4"/><circle cx="17.3" cy="6.7" r="0.8" fill="currentColor" stroke="none"/>',
    "Arrow": '<path d="M7 17 17 7M8 7h9v9"/>',
    "Mail": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3.5 6.5 8.5 7 8.5-7"/>',
    "Telegram": '<path d="M21.5 4.5 2.5 11.8l6.3 2.2 2.2 6 3.2-3.9 4.6 3.4z"/><path d="m8.8 14 9.4-7.1"/>',
}
PLAY_ICON = '<svg width="28" height="28" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13l10.5-6.5z" fill="currentColor"/></svg>'


def icon(name, size=24, stroke=1.9):
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            f'{ICONS[name]}</svg>')


UI = {
    "menu": {"ru": "Меню", "en": "Menu"},
    "close": {"ru": "Закрыть", "en": "Close"},
    "main_menu": {"ru": "Основное меню", "en": "Main menu"},
    "language": {"ru": "Язык", "en": "Language"},
    "social": {"ru": "Соцсети", "en": "Social"},
    "home": {"ru": "на главную", "en": "home"},
    "portraits": {"ru": "Фотографии", "en": "Photos"},
    "portfolio": {"ru": "Портфолио", "en": "Portfolio"},
    "prev_work": {"ru": "Предыдущая работа", "en": "Previous work"},
    "next_work": {"ru": "Следующая работа", "en": "Next work"},
    "prev_photo": {"ru": "Предыдущее фото", "en": "Previous photo"},
    "next_photo": {"ru": "Следующее фото", "en": "Next photo"},
    "next_portrait": {"ru": "Следующее фото", "en": "Next photo"},
    "watch": {"ru": "Смотреть", "en": "Watch"},
    "all_work": {"ru": "← Все работы", "en": "← All work"},
    "next": {"ru": "Следующий проект →", "en": "Next project →"},
    "works": {"ru": "Работы", "en": "Work"},
}


class Page:
    """Everything a page needs to know about where it lives."""

    def __init__(self, site, lang, key, path):
        self.site, self.lang, self.key, self.path = site, lang, key, path

    def t(self, value):
        return value[self.lang] if isinstance(value, dict) else value

    def url(self, key, lang=None):
        lang = lang or self.lang
        return PREFIX[lang] + key_to_path(key)


def key_to_path(key):
    if key == "work":
        return "/"
    if key.startswith("project:"):
        return f"/work/{key.split(':', 1)[1]}/"
    return f"/{key}/"


def lang_switch(pg):
    links = []
    for lang in LANGS:
        cur = ' aria-current="true"' if lang == pg.lang else f' lang="{lang}"'
        links.append(f'<a class="nav" href="{pg.url(pg.key, lang)}" hreflang="{lang}"{cur}>{lang.upper()}</a>')
    return f'<div class="lang" role="group" aria-label="{UI["language"][pg.lang]}">{"".join(links)}</div>'


def social_icons(pg, cls="icons"):
    links = "".join(f'<a class="icon" href="{esc(s["url"])}" aria-label="{esc(s["name"])}">{icon(s["name"])}</a>'
                    for s in pg.site["social"])
    return f'<div class="{cls}">{links}</div>'


def nav_links(pg, cls):
    current = "work" if pg.key.startswith("project:") else pg.key
    out = []
    for key in ("work", "about", "contact"):
        cur = ' aria-current="page"' if key == current else ""
        out.append(f'<a class="{cls}" href="{pg.url(key)}"{cur}>{esc(pg.t(pg.site["nav"][key]))}</a>')
    return "".join(out)


def brand(pg, tag="a"):
    href = f' href="{pg.url("work")}"' if tag == "a" else ""
    label = f'{pg.t(pg.site["name"])} — {pg.t(pg.site["role"])}'
    return (f'<{tag} class="brand"{href} aria-label="{esc(label)}">'
            f'<b>{esc(pg.t(pg.site["brand"]))}</b></{tag}>')


WORDMARK = {}


def wordmark(pg):
    name = f"portfolio-{pg.lang}"
    w, h = ANIM[name]
    return (f'<a class="mark" href="{pg.url("work")}" aria-label="{esc(UI["portfolio"][pg.lang])}">'
            f'{anim(name, w, h, 30)}</a>')


# Hand-drawn loops: 32 frames in one horizontal strip; static first frame until hovered.
ANIM = {"eye": (166, 84), "portfolio-ru": (271, 60), "portfolio-en": (237, 60)}


def anim(name, fw, fh, height):
    width = round(height * fw / fh)
    return (f'<span class="anim" data-sprite="/anim/{name}.webp" style="width:{width}px;height:{height}px">'
            f'<img src="/anim/{name}-0.webp" alt="" width="{width}" height="{height}"><span class="sp"></span></span>')


def header(pg):
    return f"""<header class="hdr">
{brand(pg)}
{wordmark(pg)}
<nav class="menu" aria-label="{UI["main_menu"][pg.lang]}">{nav_links(pg, "nav")}</nav>
<button class="m-btn" type="button" data-open aria-controls="menu" aria-expanded="false">{UI["menu"][pg.lang]}</button>
</header>
<div class="overlay" id="menu" role="dialog" aria-modal="true" aria-label="{UI["menu"][pg.lang]}" hidden>
<div class="hdr">{brand(pg, "div")}<button class="m-btn" type="button" data-close>{UI["close"][pg.lang]}</button></div>
<nav class="m-list" aria-label="{UI["main_menu"][pg.lang]}">{nav_links(pg, "m-nav")}</nav>
<div class="m-foot">{lang_switch(pg)}{social_icons(pg, "icons icons-start")}</div>
</div>"""


LOGO = {}


def footer(pg, logo=None, icons=True):
    logo = logo or LOGO or None
    middle = ""
    if logo:
        label = f'{esc(pg.t(pg.site["name"]))} — {UI["home"][pg.lang]}'
        middle = f'<a class="logo" href="{pg.url("work")}" aria-label="{label}">{anim("eye", *ANIM["eye"], 28)}</a>'
    right = social_icons(pg) if icons else ""
    return f'<footer class="ftr">{lang_switch(pg)}{middle or "<span></span>"}{right}</footer>'


# ---------------------------------------------------------------- styles & script

FONT_FACES = "".join(
    f'@font-face{{font-family:"{family}";font-style:normal;font-weight:{weight};font-display:swap;'
    f'src:url(/fonts/{file}-{subset}.woff2) format("woff2");unicode-range:{rng}}}'
    for family, file, weight in (("Sofia Sans", "sofia-sans", "100 900"),
                                 ("Sofia Sans Extra Condensed", "sofia-xcond", "900"))
    for subset, rng in (
        ("cyrillic", "U+0301,U+0400-045F,U+0490-0491,U+04B0-04B1,U+2116"),
        ("latin", "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,"
                  "U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD"),
    )
)

CSS = FONT_FACES + """
:root{--bg:#0B0B0B;--fg:#EDEDE6;--mute:#8A8A85;--soft:#D2D2CB;--accent:#EDEDE6;--line:#1C1C1A;--pad:clamp(16px,2.4vw,32px);--cond:"Sofia Sans Extra Condensed","Sofia Sans",sans-serif}
*{box-sizing:border-box}
html{background:var(--bg);color-scheme:dark;-webkit-text-size-adjust:100%}
body{margin:0;min-height:100vh;min-height:100svh;display:flex;flex-direction:column;padding:0 max(var(--pad),env(safe-area-inset-right)) env(safe-area-inset-bottom) max(var(--pad),env(safe-area-inset-left));background:var(--bg);color:var(--fg);font:300 16px/1.5 "Sofia Sans",system-ui,sans-serif;-webkit-font-smoothing:antialiased}
a{color:var(--fg)}
img{display:block;max-width:100%}
main{flex:1 0 auto}
:focus-visible{outline:1px solid var(--fg);outline-offset:3px}
.sr{position:absolute;width:1px;height:1px;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.hdr{display:grid;grid-template-columns:minmax(0,1fr) auto minmax(0,1fr);align-items:center;gap:16px 32px;padding:24px 0 32px}
.hdr>.brand{justify-self:start}
.hdr>.menu,.hdr>.m-btn{justify-self:end}
.mark{display:block}
@keyframes jitter{0%{transform:translate(0,0) rotate(0)}25%{transform:translate(-1.5px,1px) rotate(-1.2deg)}50%{transform:translate(1.5px,-1px) rotate(.9deg)}75%{transform:translate(-1px,-1.5px) rotate(.6deg)}100%{transform:translate(1px,1.5px) rotate(-.7deg)}}
.anim{position:relative;display:block}
.anim>*{position:absolute;inset:0;width:100%;height:100%}
.anim .sp{background-repeat:no-repeat;background-size:3200% 100%;opacity:0}
@keyframes play{from{background-position:0 0}to{background-position:103.2258% 0}}
.mark:hover .anim,.mark:focus-visible .anim,.logo:hover .anim,.logo:focus-visible .anim{animation:jitter .32s steps(1,end) infinite}
.anim.ready:hover .sp,a:focus-visible .anim.ready .sp{opacity:1;animation:play 3.2s steps(32) infinite}
.anim.ready:hover img,a:focus-visible .anim.ready img{opacity:0}
.brand{display:flex;flex-direction:column;gap:4px;padding:6px 0;text-decoration:none}
.brand b{margin-right:-.6em;font-size:14px;font-weight:300;line-height:1;letter-spacing:.6em;text-transform:uppercase}
.brand small{font-size:12px;line-height:1.2;letter-spacing:.08em;text-transform:uppercase;color:var(--mute)}
.menu{display:flex;gap:32px}
.nav{display:inline-flex;align-items:center;min-height:44px;font-size:14px;letter-spacing:.08em;text-transform:uppercase;text-decoration:none;color:var(--fg);transition:opacity .2s}
.nav:hover{opacity:.7}
.nav[aria-current]::before{content:"";width:6px;height:6px;border-radius:50%;background:var(--accent);margin-right:8px}
.m-btn{display:none;align-items:center;min-height:44px;padding:0 2px;background:none;border:0;color:var(--fg);font:inherit;font-size:14px;letter-spacing:.08em;text-transform:uppercase;cursor:pointer}
.overlay{position:fixed;inset:0;z-index:20;padding:env(safe-area-inset-top) max(16px,env(safe-area-inset-right)) env(safe-area-inset-bottom) max(16px,env(safe-area-inset-left));background:var(--bg);display:flex;flex-direction:column;overflow:auto}
.overlay[hidden]{display:none}
.overlay .hdr{display:flex;justify-content:space-between;flex-wrap:nowrap}
.m-list{display:flex;flex-direction:column;gap:4px;padding:40px 0 48px}
.m-nav{display:flex;align-items:center;gap:14px;min-height:60px;font:900 68px/.95 var(--cond);text-transform:uppercase;text-decoration:none;color:var(--fg)}
.m-nav[aria-current]::before{content:"";flex:none;width:10px;height:10px;border-radius:50%;background:var(--accent)}
.m-foot{display:flex;flex-direction:column;gap:4px;padding-top:16px;border-top:1px solid #2A2A28}
.lang{display:flex;gap:24px;justify-self:start}
.icons{display:flex;align-items:center;justify-self:end;margin-right:-12px}
.icons-start{margin:0 0 0 -12px}
.icon{display:inline-flex;align-items:center;justify-content:center;width:44px;height:44px;color:var(--fg);transition:opacity .2s}
.icon:hover{opacity:.7}
.ftr{display:grid;grid-template-columns:minmax(0,1fr) auto minmax(0,1fr);align-items:center;gap:8px 24px;padding:40px 0 20px}
.logo{display:inline-flex;align-items:center;justify-content:center;min-height:44px}


.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:28px 8px}
.tile{position:relative;display:flex;flex-direction:column;gap:10px;text-decoration:none;color:#fff}
.frame{position:relative;aspect-ratio:16/9;overflow:hidden;background:#151514 center/cover no-repeat}
.frame>*{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;transition:transform .7s cubic-bezier(.2,.7,.2,1),filter .4s,opacity .6s ease}
.js .frame>img:not(.ld){opacity:0}
.frame::before{content:"";position:absolute;inset:-24px;background:inherit;filter:blur(16px)}
.frame>.ov{inset:auto 0 0;height:auto;padding:16px;opacity:0;transform:translateY(4px);transition:opacity .3s,transform .3s;pointer-events:none}
.ov p{margin:0;max-width:36em;font-size:14px;line-height:1.45;font-weight:400;color:rgba(255,255,255,.92)}
.cap{display:flex;flex-direction:column;gap:6px}
.cap-row{display:flex;justify-content:space-between;align-items:baseline;gap:12px;font-size:13px;letter-spacing:.06em;text-transform:uppercase}
.cap-row b{font-family:var(--cond);font-size:15px;font-weight:900}
.cap-row span{flex:none;color:var(--mute)}
.cap-d{margin:0;font-size:14px;line-height:1.45;font-weight:400;color:#A9A9A3}
@media (hover:hover) and (min-width:641px){
.cap-d{display:none}
.tile:hover .frame>img,.tile:focus-visible .frame>img{transform:scale(1.035);filter:brightness(.4)}
.tile:hover .ov,.tile:focus-visible .ov{opacity:1;transform:none}
}
@media not all and (hover:hover){.frame>.ov{display:none}}
@media (max-width:1000px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:640px){.grid{grid-template-columns:minmax(0,1fr);gap:28px}}

.about{display:flex;flex-wrap:wrap;gap:48px 64px;padding:24px 0 88px}
.portrait{flex:1 1 340px;max-width:520px;display:flex;flex-direction:column;gap:8px}
.pframe{position:relative;display:block;width:100%;aspect-ratio:2/3;padding:0;border:0;background:var(--bg);cursor:pointer;overflow:hidden}
.pimg{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;object-position:left top;opacity:0;transition:opacity .6s ease}
.pimg.on{opacity:1}
.pbtns{display:flex;align-items:center;gap:8px;margin-left:-10px}
.pbtn{display:inline-flex;align-items:center;justify-content:center;width:44px;height:44px;padding:0;background:none;border:0;color:var(--fg);cursor:pointer;transition:opacity .2s}
.pbtn:hover{opacity:.6}
.pcount{margin-left:10px;font-size:13px;letter-spacing:.12em;color:var(--mute);font-variant-numeric:tabular-nums}
.bio{flex:999 1 480px;min-width:0;max-width:760px;display:flex;flex-direction:column;gap:18px}
.quote{margin:0 0 22px;max-width:700px;font:900 clamp(44px,4.6vw,72px)/.95 var(--cond);text-transform:uppercase}
.bio p{margin:0;max-width:620px;font-size:18px;line-height:1.6;font-weight:300;color:var(--soft)}

.contact{position:relative;overflow-x:hidden}
.contact>.hdr,.contact>main,.contact>.ftr{position:relative;z-index:1}
.contact>.bg{position:absolute;inset:0;z-index:0}
.bg img{width:100%;height:100%;object-fit:cover;object-position:center 40%}
.bg::after{content:"";position:absolute;inset:0;background:rgba(11,11,11,.42)}
.contact .brand small{color:#CFCFC8}
.contact main{display:flex;align-items:flex-end;justify-content:flex-end;padding:48px 0 40px}
.contact .ftr{padding:16px 0 20px}
.cbox{width:min(560px,100%);display:flex;flex-direction:column;gap:28px}
.label{font-size:13px;letter-spacing:.14em;text-transform:uppercase;color:#CFCFC8}
.socials{display:flex;flex-direction:column}
.socials .label{padding-bottom:10px}
.soc{display:flex;align-items:center;justify-content:space-between;gap:24px;padding:12px 0 8px;border-bottom:1px solid rgba(237,237,230,.3);font:900 clamp(56px,6.4vw,96px)/.9 var(--cond);text-transform:uppercase;text-decoration:none;color:var(--fg);transition:padding-left .35s ease}
.soc:hover{padding-left:16px}
.soc svg{flex:none;width:40px;height:40px}
.soc-mail{font-size:clamp(44px,5vw,76px)}
.agency{display:flex;flex-wrap:wrap;align-items:center;gap:0 24px;font-size:17px}
.c-link{display:inline-flex;align-items:center;min-height:44px;text-decoration:none;transition:opacity .2s}
.c-link:hover{opacity:.7}

.proj{padding:8px 0 24px}
.player{position:relative;width:min(100%,calc((100vh - 140px)*16/9));margin:0 auto;aspect-ratio:16/9;overflow:hidden;border-radius:14px;background:#000;isolation:isolate}
.player>*{position:absolute;inset:0;width:100%;height:100%;border:0;background:#000;color-scheme:dark}
.player img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;transition:filter .4s}
.play{display:block;padding:0;background:none;cursor:pointer;color:var(--fg)}
.play:hover img{filter:brightness(.7)}
.play-ic{position:absolute;left:50%;top:50%;display:flex;align-items:center;justify-content:center;width:88px;height:88px;margin:-44px 0 0 -44px;padding-left:4px;border:1px solid rgba(237,237,230,.7);border-radius:50%;background:rgba(11,11,11,.25);transition:transform .3s}
.play:hover .play-ic{transform:scale(1.06)}
.pinfo{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr);gap:32px 64px;padding-top:40px}
.ptitle{margin:0;font:900 clamp(56px,7vw,112px)/.9 var(--cond);text-transform:uppercase}
.pmeta{margin:14px 0 0;font-size:14px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute)}
.ptext{margin:24px 0 0;max-width:620px;font-size:18px;line-height:1.6;font-weight:300;color:var(--soft)}
.credits{margin:0;display:grid;grid-template-columns:auto minmax(0,1fr);gap:10px 24px;align-content:start;padding-top:8px;font-size:16px}
.credits dt{font-size:12px;line-height:24px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute)}
.credits dd{margin:0}
.stills{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:64px}
.stills img{width:100%;height:auto}
.pnav{display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px 24px;margin-top:64px;padding-top:12px;border-top:1px solid var(--line)}

.e404{display:flex;flex-direction:column;justify-content:center;gap:24px}
.e404 h1{margin:0;font:900 clamp(96px,18vw,240px)/.85 var(--cond)}

@media (max-width:720px){
body{padding-left:max(16px,env(safe-area-inset-left));padding-right:max(16px,env(safe-area-inset-right))}
.hdr{display:flex;justify-content:space-between;flex-wrap:nowrap;padding:14px 0 18px}
.hdr>.mark{display:none}
.brand{gap:3px;padding:4px 0}
.brand b{font-size:14px;letter-spacing:.5em;margin-right:-.5em}
.brand small{font-size:11px}
.hdr .menu{display:none}
.m-btn{display:inline-flex}
.ftr{margin-top:32px;padding:20px 0 16px;border-top:1px solid var(--line)}

.about{gap:16px;padding:8px 0 48px}
.portrait{gap:4px;margin-bottom:12px}
.quote{margin-bottom:8px;font-size:46px}
.bio{gap:16px}
.bio p{font-size:17px}
.bg img{object-position:10% center}
.contact .ftr{margin-top:0;border-top:0}
.soc{gap:16px;padding:10px 0 6px;font-size:64px}
.soc svg{width:34px;height:34px}
.soc-mail{font-size:min(64px,calc((100vw - 32px)/6.9))}
.socials .label{padding-bottom:8px;font-size:12px}
.agency{gap:0 20px}
.agency .label{flex:1 0 100%;font-size:12px}
.pinfo{grid-template-columns:minmax(0,1fr);padding-top:24px}
.ptitle{font-size:56px}
.ptext{font-size:17px}
.play-ic{width:64px;height:64px;margin:-32px 0 0 -32px}
.stills{grid-template-columns:minmax(0,1fr);margin-top:40px}
.pnav{margin-top:40px}
}
.lb{position:fixed;inset:0;z-index:30;display:flex;align-items:center;justify-content:center;padding:calc(64px + env(safe-area-inset-top)) max(var(--pad),env(safe-area-inset-right)) calc(32px + env(safe-area-inset-bottom)) max(var(--pad),env(safe-area-inset-left));background:rgba(11,11,11,.96);opacity:0;transition:opacity .25s}
.lb.on{opacity:1}
.lb-box{width:min(100%,calc((100vh - 170px)*16/9))}
.lb-video{position:relative;aspect-ratio:16/9;overflow:hidden;border-radius:14px;background:#000;isolation:isolate}
.lb-video iframe{position:absolute;inset:0;width:100%;height:100%;border:0;background:#000;color-scheme:dark}
.lb-cap{display:flex;align-items:center;gap:8px;margin-top:10px;font-size:14px;letter-spacing:.06em;text-transform:uppercase}
.lb-t{flex:1;min-width:0;display:flex;justify-content:space-between;align-items:baseline;gap:16px}
.lb-nav{flex:none;display:inline-flex;align-items:center;justify-content:center;width:44px;height:44px;padding:0;background:none;border:0;color:var(--fg);cursor:pointer;transition:opacity .2s}
.lb-nav:hover{opacity:.6}
.lb-cap b{font:900 20px/1 var(--cond)}
.lb-cap span{color:var(--mute)}
.lb-x{position:absolute;top:calc(12px + env(safe-area-inset-top));right:var(--pad);display:inline-flex;align-items:center;min-height:44px;padding:0;background:none;border:0;color:var(--fg);font:inherit;font-size:14px;letter-spacing:.08em;text-transform:uppercase;cursor:pointer;transition:opacity .2s}
.lb-x:hover{opacity:.7}
@media (max-width:720px){.lb{padding:64px 16px 24px}.lb-cap b{font-size:18px}}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
"""

JS = """(function(){var d=document;
var m=d.getElementById('menu'),o=d.querySelector('[data-open]'),c=d.querySelector('[data-close]');
function shut(){m.hidden=true;d.body.style.overflow='';o.setAttribute('aria-expanded','false');o.focus()}
if(m&&o&&c){o.onclick=function(){m.hidden=false;d.body.style.overflow='hidden';o.setAttribute('aria-expanded','true');c.focus()};c.onclick=shut;
d.addEventListener('keydown',function(e){if(e.key==='Escape'&&!m.hidden)shut()})}
d.querySelectorAll('.player[data-kinescope]').forEach(function(p){var b=p.querySelector('button'),warm=0;
function pre(){if(warm)return;warm=1;var l=d.createElement('link');l.rel='preconnect';l.href='https://kinescope.io';d.head.appendChild(l)}
b.addEventListener('pointerenter',pre);b.addEventListener('focus',pre);
b.onclick=function(){var f=d.createElement('iframe');f.src='https://kinescope.io/embed/'+p.dataset.kinescope+'?autoplay=1';
f.allow='autoplay; fullscreen; picture-in-picture; encrypted-media; gyroscope; accelerometer; clipboard-write';f.allowFullscreen=true;
f.title=b.getAttribute('aria-label');p.replaceChildren(f)}});
var pf=d.querySelector('.pframe');if(pf){var im=pf.querySelectorAll('.pimg'),n=im.length,cur=0;
function load(k){var x=im[(k+n)%n];if(x.dataset.src){if(x.dataset.srcset)x.srcset=x.dataset.srcset;x.src=x.dataset.src;delete x.dataset.src;delete x.dataset.srcset}}
var pc=d.querySelector('.pcount');
function show(k){cur=(k+n)%n;load(cur);load(cur+1);load(cur-1);im.forEach(function(x,q){x.classList.toggle('on',q===cur)});if(pc)pc.textContent=(cur+1)+' / '+n}
pf.onclick=function(){if(sw){sw=0;return}show(cur+1)};d.querySelectorAll('.pbtn').forEach(function(x){x.onclick=function(){show(cur+(+x.dataset.step))}});
var sx=0,sw=0;pf.addEventListener('touchstart',function(e){sx=e.touches[0].clientX},{passive:true});
pf.addEventListener('touchend',function(e){var dx=e.changedTouches[0].clientX-sx;if(Math.abs(dx)>40){sw=1;show(cur+(dx<0?1:-1))}},{passive:true});
addEventListener('load',function(){load(1);load(n-1)})}
var g=d.querySelector('.grid[data-lb]');if(g){var lb=null,last=null,pushed=0,hot=0,idx=0,tiles=[].slice.call(g.querySelectorAll('a.tile[data-kinescope]'));
var AR='<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="';
function heat(){if(hot)return;hot=1;var l=d.createElement('link');l.rel='preconnect';l.href='https://kinescope.io';d.head.appendChild(l)}
g.addEventListener('pointerover',heat);g.addEventListener('focusin',heat);
function close(back){if(!lb)return;lb.remove();lb=null;d.body.style.overflow='';if(last)last.focus({preventScroll:true});if(back&&pushed){pushed=0;history.back()}}
function show(i){idx=(i+tiles.length)%tiles.length;var a=tiles[idx],f=lb.querySelector('iframe');last=a;
lb.setAttribute('aria-label',a.dataset.title);lb.querySelector('.lb-t b').textContent=a.dataset.title;lb.querySelector('.lb-t span').textContent=a.dataset.meta;
f.title=a.dataset.title;f.src='https://kinescope.io/embed/'+a.dataset.kinescope+'?autoplay=1';if(pushed)history.replaceState({lb:1},'',a.href)}
function open(a){close(0);lb=d.createElement('div');lb.className='lb';lb.setAttribute('role','dialog');lb.setAttribute('aria-modal','true');
lb.innerHTML='<button class="lb-x" type="button"></button><div class="lb-box"><div class="lb-video"><iframe allow="autoplay; fullscreen; picture-in-picture; encrypted-media; gyroscope; accelerometer; clipboard-write" allowfullscreen></iframe></div><div class="lb-cap"><button class="lb-nav" type="button" data-d="-1">'+AR+'M19 12H5m6-6-6 6 6 6"/></svg></button><div class="lb-t"><b></b><span></span></div><button class="lb-nav" type="button" data-d="1">'+AR+'M5 12h14m-6-6 6 6-6 6"/></svg></button></div></div>';
var x=lb.querySelector('.lb-x'),nv=lb.querySelectorAll('.lb-nav');x.textContent=g.dataset.close;
nv[0].setAttribute('aria-label',g.dataset.prev);nv[1].setAttribute('aria-label',g.dataset.next);
nv.forEach(function(b){b.onclick=function(){show(idx+(+b.dataset.d))}});
x.onclick=function(){close(1)};lb.addEventListener('click',function(ev){if(ev.target===lb||ev.target.classList.contains('lb-box'))close(1)});
var tx=0;lb.addEventListener('touchstart',function(e){tx=e.touches[0].clientX},{passive:true});
lb.addEventListener('touchend',function(e){var dx=e.changedTouches[0].clientX-tx;if(Math.abs(dx)>50)show(idx+(dx<0?1:-1))},{passive:true});
d.body.appendChild(lb);d.body.style.overflow='hidden';history.pushState({lb:1},'',a.href);pushed=1;show(tiles.indexOf(a));
requestAnimationFrame(function(){lb&&lb.classList.add('on')});x.focus()}
g.addEventListener('click',function(e){var a=e.target.closest('a.tile[data-kinescope]');
if(!a||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey||e.button)return;e.preventDefault();open(a)});
addEventListener('popstate',function(){pushed=0;close(0)});
d.addEventListener('keydown',function(e){if(!lb)return;
if(e.key==='Escape')close(1);else if(e.key==='ArrowRight')show(idx+1);else if(e.key==='ArrowLeft')show(idx-1);
else if(e.key==='Tab'){var fs=[].slice.call(lb.querySelectorAll('button,iframe')),k=fs.indexOf(d.activeElement);
if(e.shiftKey&&k<=0){e.preventDefault();fs[fs.length-1].focus()}else if(!e.shiftKey&&(k===fs.length-1||k<0)){e.preventDefault();fs[0].focus()}}})}
function warmAnim(){d.querySelectorAll('.anim[data-sprite]').forEach(function(a){var sp=a.querySelector('.sp'),u=a.dataset.sprite,i=new Image();i.onload=function(){sp.style.backgroundImage='url('+u+')';a.classList.add('ready')};i.src=u})}
addEventListener('load',function(){setTimeout(warmAnim,800)});
d.querySelectorAll('.frame>img').forEach(function(i){function ok(){i.classList.add('ld')}if(i.complete&&i.naturalWidth)ok();else{i.addEventListener('load',ok);i.addEventListener('error',ok)}});
})();"""


# ---------------------------------------------------------------- document

def document(pg, title, body, body_class="", description=None, og_image=None, preload_img=None):
    site = pg.site
    domain = site["domain"].rstrip("/")
    canonical = domain + pg.url(pg.key)
    desc = description or pg.t(site["description"])
    subset = "cyrillic" if pg.lang == "ru" else "latin"
    alternates = "".join(f'<link rel="alternate" hreflang="{lang}" href="{domain}{pg.url(pg.key, lang)}">'
                         for lang in LANGS)
    alternates += f'<link rel="alternate" hreflang="x-default" href="{domain}{pg.url(pg.key, "en")}">'
    og = (f'<meta property="og:image" content="{domain}/og.jpg"><meta property="og:image:width" content="1200">'
          f'<meta property="og:image:height" content="630"><meta name="twitter:card" content="summary_large_image">')
    preload = ""
    if preload_img:
        srcset = f' imagesrcset="{preload_img["srcset"]}" imagesizes="{preload_img.get("sizes", "100vw")}"' \
            if preload_img["srcset"] else ""
        preload = f'<link rel="preload" as="image" href="{preload_img["src"]}"{srcset} fetchpriority="high">'
    cls = f' class="{body_class}"' if body_class else ""
    return f"""<!doctype html>
<html lang="{pg.lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<script>document.documentElement.className+=" js"</script>
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<meta name="theme-color" content="#0B0B0B">
<link rel="canonical" href="{canonical}">
{alternates}
<meta property="og:type" content="website">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{canonical}">
{og}
<link rel="icon" href="/favicon.png" type="image/png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="preload" href="/fonts/sofia-sans-{subset}.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="/fonts/sofia-xcond-{subset}.woff2" as="font" type="font/woff2" crossorigin>
{preload}
<style>{CSS}</style>
</head>
<body{cls}>
{body}
<script>{JS}</script>
</body>
</html>
"""


def write(path, text):
    out = OUT / path.lstrip("/")
    if path.endswith("/"):
        out = out / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- pages

def frame(p, poster, alt, eager=False, high=False, overlay=""):
    if not poster:
        return f'<div class="frame"><div style="background:{p["color"]}"></div>{overlay}</div>'
    style = f'object-position:{esc(p["focus"])}' if p.get("focus") else ""
    bg = f' style="background-image:url({poster["lqip"]})"' if poster.get("lqip") else ""
    return (f'<div class="frame"{bg}>{img_tag(poster, alt, TILE_SIZES, eager=eager, high=high, style=style)}'
            f'{overlay}</div>')


def meta_line(pg, p):
    return esc(pg.t(p["type"]))


def lightbox_attrs(pg, p):
    """Data for opening the video in place, without leaving the grid."""
    if not p["kinescope_id"]:
        return ""
    meta = pg.t(p["type"])
    return (f' data-kinescope="{esc(p["kinescope_id"])}" data-title="{esc(pg.t(p["title"]))}"'
            f' data-meta="{esc(meta)}"')


def work_page(pg, projects, posters, logo):
    tiles = []
    for i, p in enumerate(projects):
        title = pg.t(p["title"])
        text = pg.t(p.get("text") or {"ru": "", "en": ""})
        overlay = f'<div class="ov"><p>{esc(text)}</p></div>' if text else ""
        desc = f'<p class="cap-d">{esc(text)}</p>' if text else ""
        tiles.append(
            f'<a class="tile" href="{pg.url("project:" + p["slug"])}"{lightbox_attrs(pg, p)}>'
            f'{frame(p, posters[p["slug"]], title, eager=i < 3, high=i == 0, overlay=overlay)}'
            f'<div class="cap"><div class="cap-row"><b>{esc(title)}</b><span>{meta_line(pg, p)}</span></div>{desc}</div></a>')
    body = f"""{header(pg)}
<main>
<h1 class="sr">{esc(pg.t(pg.site["name"]))} — {UI["works"][pg.lang]}</h1>
<div class="grid" data-lb data-close="{UI["close"][pg.lang]}" data-prev="{UI["prev_work"][pg.lang]}" data-next="{UI["next_work"][pg.lang]}">
{chr(10).join(tiles)}
</div>
</main>
{footer(pg, logo=logo)}"""
    first = posters[projects[0]["slug"]] if projects else None
    preload = dict(first, sizes=TILE_SIZES) if first else None
    title = f'{pg.t(pg.site["name"])} — {pg.t(pg.site["role"])}'
    write(pg.url("work"), document(pg, title, body, og_image=first and first["src"], preload_img=preload))


def about_page(pg, portraits):
    about = pg.site["about"]
    sizes = "(max-width: 720px) calc(100vw - 32px), 520px"
    imgs = []
    for i, (meta, info) in enumerate(portraits):
        tag = img_tag(info, pg.t(meta["alt"]), sizes, cls="pimg on" if i == 0 else "pimg", eager=True, high=i == 0)
        if i:  # later photos load only when they are about to be shown
            tag = tag.replace(' src="', ' data-src="', 1).replace(' srcset="', ' data-srcset="', 1)
        imgs.append(tag)
    imgs = "".join(imgs)
    arrow = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="{}"/></svg>'
    btns = (f'<button class="pbtn" type="button" data-step="-1" aria-label="{UI["prev_photo"][pg.lang]}">{arrow.format("M19 12H5m6-6-6 6 6 6")}</button>'
            f'<button class="pbtn" type="button" data-step="1" aria-label="{UI["next_photo"][pg.lang]}">{arrow.format("M5 12h14m-6-6 6 6-6 6")}</button>')
    paras = "".join(f"<p>{esc(t)}</p>" for t in about["paragraphs"][pg.lang])
    body = f"""{header(pg)}
<main class="about">
<div class="portrait">
<button class="pframe" type="button" aria-label="{UI["next_portrait"][pg.lang]}">{imgs}</button>
<div class="pbtns" role="group" aria-label="{UI["portraits"][pg.lang]}">{btns}<span class="pcount" aria-live="polite">1 / {len(portraits)}</span></div>
</div>
<div class="bio">
<blockquote class="quote">{esc(pg.t(about["quote"]))}</blockquote>
{paras}
</div>
</main>
{footer(pg)}"""
    title = f'{pg.t(pg.site["nav"]["about"])} — {pg.t(pg.site["name"])}'
    write(pg.url("about"), document(pg, title, body, og_image=portraits[0][1]["src"]))


def contact_page(pg, eyes):
    site = pg.site
    socials = "".join(f'<a class="soc" href="{esc(s["url"])}">{esc(s["name"])}{icon(s["name"], 40, 1.6)}</a>'
                      for s in site["social"])
    ag = site["agency"]
    body = f"""<div class="bg" aria-hidden="true">{img_tag(eyes, "", "100vw", eager=True, high=True)}</div>
{header(pg)}
<main>
<div class="cbox">
<nav class="socials" aria-label="{UI["social"][pg.lang]}"><span class="label">{UI["social"][pg.lang]}</span>{socials}</nav>
<nav class="socials" aria-label="{esc(pg.t(ag["label"]))}"><span class="label">{esc(pg.t(ag["label"]))}</span>
<a class="soc" href="{esc(ag["url"])}">{esc(ag["name"])}{icon("Arrow", 40, 1.6)}</a>
<a class="soc soc-mail" href="mailto:{esc(ag["email"])}">{esc(ag["email"])}{icon("Mail", 40, 1.6)}</a></nav>
</div>
</main>
{footer(pg, icons=False)}"""
    title = f'{pg.t(site["nav"]["contact"])} — {pg.t(site["name"])}'
    write(pg.url("contact"), document(pg, title, body, body_class="contact", og_image=eyes["src"],
                                      preload_img=eyes))


def project_page(pg, p, nxt, poster, big_poster, stills):
    title = pg.t(p["title"])
    if big_poster:
        style = f' style="object-position:{esc(p["focus"])}"' if p.get("focus") else ""
        pic = img_tag(big_poster, "" if p["kinescope_id"] else title, "100vw", eager=True, high=True)
        if style:
            pic = pic.replace("<img ", f"<img{style} ", 1)
    else:
        pic = f'<div style="background:{p["color"]}"></div>'
    if p["kinescope_id"]:
        watch = f'{UI["watch"][pg.lang]}: {title}'
        player = (f'<div class="player" data-kinescope="{esc(p["kinescope_id"])}">'
                  f'<button class="play" type="button" aria-label="{esc(watch)}">{pic}'
                  f'<span class="play-ic">{PLAY_ICON}</span></button></div>')
    else:
        player = f'<div class="player">{pic}</div>'
    text = pg.t(p.get("text") or {"ru": "", "en": ""})
    credits = (p.get("credits") or {}).get(pg.lang) or []
    dl = ""
    if credits:
        dl = '<dl class="credits">' + "".join(f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in credits) + "</dl>"
    gallery = ""
    if stills:
        gallery = '<div class="stills">' + "".join(
            img_tag(s, f"{title} — кадр {i + 1}" if pg.lang == "ru" else f"{title} — still {i + 1}",
                    "(max-width: 720px) calc(100vw - 32px), 50vw")
            for i, s in enumerate(stills)) + "</div>"
    nav = (f'<nav class="pnav"><a class="nav" href="{pg.url("work")}">{UI["all_work"][pg.lang]}</a>'
           f'<a class="nav" href="{pg.url("project:" + nxt["slug"])}">{UI["next"][pg.lang]}</a></nav>')
    body = f"""{header(pg)}
<main class="proj">
{player}
<div class="pinfo">
<div>
<h1 class="ptitle">{esc(title)}</h1>
<p class="pmeta">{meta_line(pg, p)}</p>
{f'<p class="ptext">{esc(text)}</p>' if text else ""}
</div>
{dl}
</div>
{gallery}
{nav}
</main>
{footer(pg)}"""
    page_title = f'{title} — {pg.t(pg.site["name"])}'
    write(pg.url(pg.key), document(pg, page_title, body, description=text or None,
                                   og_image=poster and poster["src"],
                                   preload_img=big_poster))


def not_found_page(site):
    pg = Page(site, "en", "work", "/")
    body = f"""{header(pg)}
<main class="e404">
<h1>404</h1>
<p>This page doesn’t exist. <a href="/">Home</a></p>
<p lang="ru">Такой страницы нет. <a href="/ru/">На главную</a></p>
</main>"""
    (OUT / "404.html").write_text(document(pg, "404 — " + site["name"]["en"], body), encoding="utf-8")


def icons_and_meta(site, logo_src, pages):
    if Image is not None:
        # link preview (Telegram, WhatsApp, etc.): the eye on black
        with Image.open(logo_src) as eye:
            eye = eye.convert("RGBA")
            card = Image.new("RGBA", (1200, 630), (11, 11, 11, 255))
            w = 560
            mark = eye.resize((w, round(eye.height * w / eye.width)), Image.LANCZOS)
            card.alpha_composite(mark, ((1200 - mark.width) // 2, (630 - mark.height) // 2))
            card.convert("RGB").save(OUT / "og.jpg", quality=88)
        with Image.open(logo_src) as eye:
            eye = eye.convert("RGBA")
            for name, size in (("favicon.png", 64), ("apple-touch-icon.png", 180)):
                canvas = Image.new("RGBA", (size, size), (11, 11, 11, 255))
                w = round(size * 0.78)
                mark = eye.resize((w, round(eye.height * w / eye.width)), Image.LANCZOS)
                canvas.alpha_composite(mark, ((size - mark.width) // 2, (size - mark.height) // 2))
                canvas.convert("RGB").save(OUT / name)
    domain = site["domain"].rstrip("/")
    urls = "".join(f"<url><loc>{domain}{p}</loc></url>" for p in pages)
    (OUT / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n',
        encoding="utf-8")
    (OUT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {domain}/sitemap.xml\n", encoding="utf-8")
    (OUT / "CNAME").write_text(domain.split("://", 1)[1] + "\n", encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")


# ---------------------------------------------------------------- main

def build():
    site = load_json("site.json")
    projects = load_projects()
    if not projects:
        fail("в projects.json нет ни одного видимого проекта")

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    shutil.copytree(STATIC, OUT, dirs_exist_ok=True)

    photos = MEDIA / "photos"
    logo_src = photos / "eye-white.png"
    logo = image(logo_src, (112, 168), lossless=True)
    LOGO.update(logo)
    eyes = image(photos / "contact-eyes.jpg", (800, 1280, 1920, 2560), quality=76)
    portraits = [(m, image(photos / m["file"], (520, 800, 1040), quality=74)) for m in site["about"]["portraits"]]

    posters, big_posters, stills = {}, {}, {}
    for p in projects:
        src = find_poster(p)
        posters[p["slug"]] = image(src, POSTER_WIDTHS) if src else None
        big_posters[p["slug"]] = image(src, POSTER_WIDTHS + (2560,)) if src else None
        stills[p["slug"]] = [image(s, (800, 1200, 1600)) for s in find_stills(p)]

    pages = []
    for lang in LANGS:
        mk = lambda key: Page(site, lang, key, None)
        work_page(mk("work"), projects, posters, logo)
        about_page(mk("about"), portraits)
        contact_page(mk("contact"), eyes)
        for i, p in enumerate(projects):
            nxt = projects[(i + 1) % len(projects)]
            project_page(mk("project:" + p["slug"]), p, nxt, posters[p["slug"]],
                         big_posters[p["slug"]], stills[p["slug"]])
        keys = ["work", "about", "contact"] + ["project:" + p["slug"] for p in projects]
        pages += [PREFIX[lang] + key_to_path(k) for k in keys]

    not_found_page(site)
    icons_and_meta(site, logo_src, pages)

    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"Готово: {len(pages)} страниц, {len(projects)} проектов, {size / 1024 / 1024:.1f} МБ → _site/")
    if Image is None:
        print("Внимание: Pillow не установлен, картинки скопированы без сжатия "
              "(на сайте после публикации всё сожмётся). Установить: python3 -m pip install --user pillow")
    for w in warnings:
        print("  · " + w)


def serve(port=8000):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(OUT))
    print(f"Сайт открыт на http://localhost:{port} (остановить: Ctrl+C)")
    http.server.ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()


if __name__ == "__main__":
    build()
    if "--serve" in sys.argv:
        serve()
