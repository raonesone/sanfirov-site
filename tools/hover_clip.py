#!/usr/bin/env python3
"""Make the silent hover preview for a project: static/hover/<slug>.mp4.

    python3 tools/hover_clip.py <slug>            pick the most dynamic 4.4 s automatically
    python3 tools/hover_clip.py <slug> 0:12       start at a given timecode
    python3 tools/hover_clip.py <slug> 0:10 10    start + length in seconds

Downloads the 480p rendition from the project's public Kinescope stream into
.cache/hover/, finds a lively window (skips intro/outro, black and cut-heavy
parts), crops black bars to 16:9 and encodes a ~200–500 KB H.264 loop.
Needs ffmpeg (/opt/homebrew/bin/ffmpeg).
"""
import json, re, subprocess, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FF = "/opt/homebrew/bin/ffmpeg"
H = {"User-Agent": "Mozilla/5.0", "Referer": "https://kinescope.io/"}
LEN = 4.4


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=60).read().decode("utf-8", "ignore")


def source(slug):
    p = next(x for x in json.loads((ROOT / "content/projects.json").read_text()) if x["slug"] == slug)
    kid = p["kinescope"].rstrip("/").rsplit("/", 1)[-1]
    master = re.search(r"https://kinescope\.io/[0-9a-f-]{36}/master\.m3u8", get(f"https://kinescope.io/embed/{kid}")).group(0)
    mp4 = re.search(r'URI="([^"]+\.mp4)"', get(master.rsplit("/", 1)[0] + "/media.m3u8?quality=480&type=video")).group(1)
    dst = ROOT / ".cache/hover" / f"{slug}.mp4"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        subprocess.run(["curl", "-sf", "-A", H["User-Agent"], "-e", H["Referer"], "-o", str(dst), mp4], check=True)
    return dst


def pick(src):
    out = subprocess.run([FF, "-nostdin", "-i", str(src), "-vf",
                          "fps=5,scale=160:-2,signalstats,select='gte(scene\\,0)',metadata=print:file=-",
                          "-an", "-f", "null", "-"], capture_output=True, text=True).stdout
    frames, cur = [], {}
    for line in out.splitlines():
        m = re.match(r"frame:\d+\s+pts:\d+\s+pts_time:([\d.]+)", line)
        if m:
            if cur: frames.append(cur)
            cur = {"t": float(m.group(1))}
        m = re.search(r"lavfi\.(scene_score|signalstats\.YAVG)=([\d.]+)", line)
        if m: cur[m.group(1)] = float(m.group(2))
    frames.append(cur)
    dur, n, best = frames[-1]["t"], int(LEN * 5), None
    for i in range(len(frames) - n):
        win = frames[i:i + n]
        if win[0]["t"] < dur * .08 or win[-1]["t"] > dur * .88: continue
        if sum(x.get("signalstats.YAVG", 0) for x in win) / n < 28: continue
        cuts = sum(1 for x in win if x.get("scene_score", 0) > .35)
        score = sum(min(x.get("scene_score", 0), .25) for x in win) - .4 * max(0, cuts - 1)
        if best is None or score > best[0]: best = (score, win[0]["t"])
    return best[1] if best else dur * .3


def encode(slug, src, t, big=False, length=LEN):
    gray = subprocess.run([FF, "-nostdin", "-ss", str(t + 1), "-i", str(src), "-frames:v", "1", "-vf", "scale=854:480,format=gray",
                           "-f", "rawvideo", "-"], capture_output=True).stdout
    w, h = 854, 480
    cols = [sum(gray[y * w + x] for y in range(0, h, 4)) / (h / 4) for x in range(w)]
    rows = [sum(gray[y * w + x] for x in range(0, w, 4)) / (w / 4) for y in range(h)]
    l = next((i for i, v in enumerate(cols) if v > 20), 0); r = w - next((i for i, v in enumerate(reversed(cols)) if v > 20), 0)
    tp = next((i for i, v in enumerate(rows) if v > 20), 0); b = h - next((i for i, v in enumerate(reversed(rows)) if v > 20), 0)
    cw, ch = r - l, b - tp
    if cw / ch > 16 / 9: nw = int(ch * 16 / 9) // 2 * 2; l += (cw - nw) // 2; cw = nw
    else: nh = int(cw * 9 / 16) // 2 * 2; tp += (ch - nh) // 2; ch = nh
    size = "854:480" if big else "640:360"
    dst = ROOT / "static/hover" / f"{slug}.mp4"
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FF, "-nostdin", "-y", "-loglevel", "error", "-ss", str(t), "-i", str(src), "-t", str(length), "-an", "-vf",
                    f"crop={cw}:{ch}:{l}:{tp},scale={size}:flags=lanczos,fps=25,format=yuv420p",
                    "-c:v", "libx264", "-preset", "slow", "-crf", "27", "-profile:v", "main", "-movflags", "+faststart", str(dst)], check=True)
    print(f"{dst.relative_to(ROOT)}  start {t:.1f}s  {dst.stat().st_size // 1024} KB")


if __name__ == "__main__":
    slug = sys.argv[1]
    src = source(slug)
    if len(sys.argv) > 2:
        mm, ss = (["0"] + sys.argv[2].split(":"))[-2:]
        t = int(mm) * 60 + float(ss)
    else:
        t = pick(src)
    length = float(sys.argv[3]) if len(sys.argv) > 3 else LEN
    first = json.loads((ROOT / "content/projects.json").read_text())[0]["slug"]
    encode(slug, src, t, big=(slug == first), length=length)
