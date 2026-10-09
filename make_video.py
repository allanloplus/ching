#!/usr/bin/env python3
"""婚禮開場影片產生器

把 photos/ 內的相片做成含片頭、Ken Burns 推移、交叉淡化、片尾的 1080p MP4。
用法：
    python3 make_video.py                 # 依 config.json 輸出正式版
    python3 make_video.py --preview       # 960x540 / 15fps 快速預覽
    python3 make_video.py --still 3.0     # 只輸出第 3 秒的單張畫面 (PNG)
"""
import argparse
import json
import math
import os
import random
import subprocess
import sys
from functools import lru_cache
from multiprocessing import Pool
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff", ".bmp"}

GOLD = (214, 182, 120)
CREAM = (248, 242, 230)

FONT_CANDIDATES = {
    "regular": [
        ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc", 3),
        ("/System/Library/Fonts/Supplemental/Songti.ttc", 0),
        ("C:/Windows/Fonts/mingliu.ttc", 0),
        ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
    ],
    "bold": [
        ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc", 3),
        ("/System/Library/Fonts/Supplemental/Songti.ttc", 1),
        ("C:/Windows/Fonts/mingliub.ttc", 0),
        ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
    ],
}

CFG = {}  # 由 main() 載入，worker 透過 initializer 取得


# ---------------------------------------------------------------- 工具函式

def smooth(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def ease_sine(x):
    x = max(0.0, min(1.0, x))
    return 0.5 - 0.5 * math.cos(math.pi * x)


@lru_cache(maxsize=64)
def font(kind, size):
    custom = CFG.get("font_" + kind)
    if custom:
        return ImageFont.truetype(custom, size)
    for path, idx in FONT_CANDIDATES[kind]:
        if os.path.exists(path):
            return ImageFont.truetype(path, size, index=idx)
    sys.exit("找不到中文字型，請在 config.json 設定 font_regular / font_bold")


def text_layer(text, size, kind="regular", color=CREAM, spacing=0.0, shadow=True):
    """回傳一張已排版好的 RGBA 文字圖層 (含柔和陰影)。spacing 為字距 (字級倍數)。"""
    f = font(kind, size)
    gap = int(size * spacing)
    widths = [f.getbbox(ch)[2] if ch.strip() else size // 3 for ch in text]
    total_w = sum(widths) + gap * max(0, len(text) - 1)
    asc, desc = f.getmetrics()
    pad = size
    layer = Image.new("RGBA", (total_w + pad * 2, asc + desc + pad * 2), (0, 0, 0, 0))

    def draw_on(img, fill):
        d = ImageDraw.Draw(img)
        x = pad
        for ch, w in zip(text, widths):
            d.text((x, pad), ch, font=f, fill=fill)
            x += w + gap

    if shadow:
        sh = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        draw_on(sh, (0, 0, 0, 170))
        sh = sh.filter(ImageFilter.GaussianBlur(max(2, size // 12)))
        layer.alpha_composite(sh, (0, max(1, size // 30)))
    draw_on(layer, color + (255,))
    return layer


def paste_center(base, layer, cx, cy, opacity=1.0):
    if opacity <= 0:
        return
    if opacity < 1:
        layer = layer.copy()
        a = layer.getchannel("A").point(lambda v: int(v * opacity))
        layer.putalpha(a)
    base.alpha_composite(layer, (int(cx - layer.width / 2), int(cy - layer.height / 2)))


def cover(img, w, h):
    return ImageOps.fit(img, (w, h), Image.LANCZOS, centering=(0.5, 0.42))


def load_photo(path):
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


# ---------------------------------------------------------------- 背景與裝飾

@lru_cache(maxsize=4)
def dark_backdrop(w, h, hero):
    """片頭/片尾底圖：有主視覺相片就用模糊壓暗的相片，否則用深色漸層。"""
    if hero:
        bg = cover(load_photo(hero), w, h)
        bg = bg.filter(ImageFilter.GaussianBlur(w / 80))
        bg = Image.blend(bg, Image.new("RGB", (w, h), (12, 10, 14)), 0.62)
    else:
        bg = Image.new("RGB", (w, h), (14, 12, 18))
        grad = Image.radial_gradient("L").resize((w, h))
        warm = Image.new("RGB", (w, h), (58, 42, 40))
        bg = Image.composite(bg, warm, grad)
    vig = Image.radial_gradient("L").resize((w, h)).point(lambda v: int(min(255, v * 1.1)))
    return Image.composite(Image.new("RGB", (w, h), (0, 0, 0)), bg, vig.point(lambda v: int(v * 0.75)))


def bokeh(w, h, t, seed=7, count=46):
    """金色光點緩慢飄移，以 1/4 解析度繪製後放大以節省運算。"""
    sw, sh = w // 4, h // 4
    layer = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    rnd = random.Random(seed)
    for _ in range(count):
        x0, y0 = rnd.random() * sw, rnd.random() * sh
        r = rnd.uniform(2, 9) * sw / 480
        speed = rnd.uniform(3, 10) * sw / 480
        phase = rnd.random() * math.tau
        x = (x0 + math.sin(t * 0.3 + phase) * 6 * sw / 480) % sw
        y = (y0 - t * speed) % sh
        a = int(rnd.uniform(40, 120) * (0.6 + 0.4 * math.sin(t * 1.3 + phase)))
        d.ellipse((x - r, y - r, x + r, y + r), fill=GOLD + (a,))
    layer = layer.filter(ImageFilter.GaussianBlur(1.2 * sw / 480))
    return layer.resize((w, h), Image.BILINEAR)


def divider(w, size, opacity):
    layer = Image.new("RGBA", (w, size * 3), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cy, half = size * 1.5, w / 2
    d.line((0, cy, half - size * 1.2, cy), fill=GOLD + (int(220 * opacity),), width=max(1, size // 8))
    d.line((half + size * 1.2, cy, w, cy), fill=GOLD + (int(220 * opacity),), width=max(1, size // 8))
    r = size * 0.45
    d.polygon([(half, cy - r), (half + r, cy), (half, cy + r), (half - r, cy)], fill=GOLD + (int(255 * opacity),))
    return layer


# ---------------------------------------------------------------- 片段 (clip)

def render_card(clip, t, w, h):
    """片頭 / 章節 / 片尾的文字卡。lines 由上而下，每行依序淡入並微微上浮。"""
    base = dark_backdrop(w, h, CFG.get("_hero")).convert("RGBA")
    base.alpha_composite(bokeh(w, h, t + clip.get("seed", 0)))
    s = h / 1080
    lines = clip["lines"]
    heights = [ln["size"] * s * ln.get("lh", 1.6) for ln in lines]
    y = h / 2 - sum(heights) / 2
    for i, (ln, lh) in enumerate(zip(lines, heights)):
        start = 0.6 + i * 0.55
        p = smooth((t - start) / 1.4)
        cy = y + lh / 2 + (1 - p) * 18 * s
        if ln.get("divider"):
            paste_center(base, divider(int(w * 0.32), int(14 * s), p), w / 2, cy)
        else:
            layer = text_layer(ln["text"], int(ln["size"] * s), ln.get("weight", "regular"),
                               tuple(ln.get("color", CREAM)), ln.get("spacing", 0.1))
            paste_center(base, layer, w / 2, cy, p)
        y += lh
    return base.convert("RGB")


@lru_cache(maxsize=6)
def photo_canvas(path, w, h):
    """預先合成比畫面大 12% 的畫布供 Ken Burns 裁切；直式照片配模糊背景。"""
    m = 1.12
    cw, ch = int(w * m), int(h * m)
    img = load_photo(path)
    ratio = img.width / img.height
    if ratio >= (w / h) * 0.82:
        return cover(img, cw, ch)
    bg = cover(img, cw // 4, ch // 4).filter(ImageFilter.GaussianBlur(cw / 160)).resize((cw, ch), Image.BILINEAR)
    bg = Image.blend(bg, Image.new("RGB", bg.size, (10, 10, 12)), 0.45)
    fh = int(ch * 0.94)
    fw = int(fh * ratio)
    fg = img.resize((fw, fh), Image.LANCZOS)
    shadow = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rectangle(((cw - fw) // 2, (ch - fh) // 2 + 10, (cw + fw) // 2, (ch + fh) // 2 + 10),
                                     fill=(0, 0, 0, 160))
    shadow = shadow.filter(ImageFilter.GaussianBlur(24))
    bg = bg.convert("RGBA")
    bg.alpha_composite(shadow)
    bg = bg.convert("RGB")
    bg.paste(fg, ((cw - fw) // 2, (ch - fh) // 2))
    return bg


def render_photo(clip, t, w, h):
    canvas = photo_canvas(clip["path"], w, h)
    cw, ch = canvas.size
    p = ease_sine(t / clip["dur"])
    z0, z1, (ax, ay), (bx, by) = clip["kb"]
    z = z0 + (z1 - z0) * p
    bw, bh = cw / z, ch / z
    cx = cw / 2 + (ax + (bx - ax) * p) * (cw - bw) / 2
    cy = ch / 2 + (ay + (by - ay) * p) * (ch - bh) / 2
    frame = canvas.resize((w, h), Image.BICUBIC, box=(cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2))
    cap = clip.get("caption")
    if cap:
        s = h / 1080
        o = smooth((t - 0.8) / 0.8) * smooth((clip["dur"] - 0.6 - t) / 0.8)
        frame = frame.convert("RGBA")
        grad = Image.linear_gradient("L").resize((w, int(h * 0.28))).point(lambda v: int(v * 0.55 * o))
        shade = Image.new("RGBA", grad.size, (0, 0, 0, 0))
        shade.putalpha(grad)
        frame.alpha_composite(shade, (0, h - grad.height))
        paste_center(frame, text_layer(cap, int(46 * s), "regular", CREAM, 0.12), w / 2, h - 110 * s, o)
        frame = frame.convert("RGB")
    return frame


def render_clip(clip, t, w, h):
    return render_photo(clip, t, w, h) if clip["type"] == "photo" else render_card(clip, t, w, h)


# ---------------------------------------------------------------- 時間軸

def collect_photos(cfg):
    folder = ROOT / cfg.get("photo_dir", "photos")
    files = [p for p in folder.rglob("*") if p.suffix.lower() in PHOTO_EXT and not p.name.startswith(".")
             and p.name not in cfg.get("exclude", [])]
    if cfg.get("order", "name") == "exif":
        def key(p):
            try:
                dt = Image.open(p).getexif().get_ifd(0x8769).get(36867) or Image.open(p).getexif().get(306)
            except Exception:
                dt = None
            return (dt or "9999", p.name)
        files.sort(key=key)
    else:
        files.sort(key=lambda p: str(p.relative_to(folder)).lower())
    if cfg.get("max_photos"):
        n = cfg["max_photos"]
        if len(files) > n:  # 平均抽樣，保留首尾
            files = [files[round(i * (len(files) - 1) / (n - 1))] for i in range(n)]
    return files


def build_timeline(cfg, photos, music_len):
    g, b = cfg["groom"], cfg["bride"]
    x = cfg.get("crossfade", 1.2)
    intro = {"type": "card", "dur": cfg.get("intro_seconds", 8), "seed": 0, "lines": [
        {"text": cfg.get("intro_top", "WELCOME TO OUR WEDDING"), "size": 34, "color": GOLD, "spacing": 0.35},
        {"divider": True, "size": 30},
        {"text": f"{g}  &  {b}", "size": 112, "weight": "bold", "spacing": 0.12, "lh": 1.5},
        {"text": cfg.get("date_text", ""), "size": 40, "spacing": 0.25},
    ]}
    outro = {"type": "card", "dur": cfg.get("outro_seconds", 9), "seed": 50, "lines": [
        {"text": cfg.get("outro_title", "感謝大家今天來參加我們的婚禮"), "size": 84, "weight": "bold", "spacing": 0.2},
        {"divider": True, "size": 30},
        {"text": f"{g}  &  {b}", "size": 52, "color": GOLD, "spacing": 0.15},
        {"text": cfg.get("outro_sub", "祝大家用餐愉快"), "size": 40, "spacing": 0.2},
    ]}
    for c in (intro, outro):
        c["lines"] = [ln for ln in c["lines"] if ln.get("divider") or ln["text"]]

    chapters = {c["before"]: c for c in cfg.get("chapters", [])}
    captions = cfg.get("captions", {})
    n_ch = sum(1 for p in photos if p.name in chapters)
    ch_dur = cfg.get("chapter_seconds", 4.5)

    per = cfg.get("photo_seconds", 4.5)
    if music_len and cfg.get("fit_to_music", True) and photos:
        fixed = intro["dur"] + outro["dur"] + n_ch * ch_dur
        n_clips = 2 + n_ch + len(photos)
        per = (music_len - fixed + (n_clips - 1) * x) / len(photos)
        # 相片少、音樂長時不硬撐：每張最多 max_photo_seconds 秒，音樂於片尾淡出截斷
        per = min(cfg.get("max_photo_seconds", 6.5), max(x + 1.5, per))

    rnd = random.Random(cfg.get("seed", 2026))
    clips = [intro]
    for i, p in enumerate(photos):
        if p.name in chapters:
            c = chapters[p.name]
            clips.append({"type": "card", "dur": ch_dur, "seed": 100 + i, "lines": [
                {"text": c.get("title", ""), "size": 88, "weight": "bold", "spacing": 0.3},
                {"divider": True, "size": 26},
                {"text": c.get("subtitle", ""), "size": 40, "color": GOLD, "spacing": 0.2},
            ]})
            clips[-1]["lines"] = [ln for ln in clips[-1]["lines"] if ln.get("divider") or ln["text"]]
        zin = rnd.random() < 0.5
        z = (1.0, 1.1) if zin else (1.1, 1.0)
        a = (rnd.uniform(-0.6, 0.6), rnd.uniform(-0.5, 0.5))
        b_ = (rnd.uniform(-0.6, 0.6), rnd.uniform(-0.5, 0.5))
        clips.append({"type": "photo", "path": str(p), "dur": per, "kb": (z[0], z[1], a, b_),
                      "caption": captions.get(p.name, "")})
    clips.append(outro)

    t = 0.0
    for c in clips:
        c["start"] = t
        t += c["dur"] - x
    total = t + x
    return clips, total, x


def frame_at(T, clips, x, w, h):
    active = [c for c in clips if c["start"] <= T < c["start"] + c["dur"]]
    if not active:
        active = [clips[-1]]
    if len(active) == 1:
        c = active[0]
        img = render_clip(c, min(T - c["start"], c["dur"] - 1e-3), w, h)
    else:
        a, b = active[0], active[1]
        alpha = smooth((T - b["start"]) / x)
        img = Image.blend(render_clip(a, T - a["start"], w, h), render_clip(b, T - b["start"], w, h), alpha)
    # 全片頭尾黑場淡入淡出
    total = clips[-1]["start"] + clips[-1]["dur"]
    fade = min(smooth(T / 1.5), smooth((total - T) / 2.0))
    if fade < 1:
        img = Image.blend(Image.new("RGB", img.size, (0, 0, 0)), img, fade)
    return img


# ---------------------------------------------------------------- 輸出

_W = {}


def _init(cfg, clips, x, w, h):
    CFG.clear()
    CFG.update(cfg)
    _W.update(clips=clips, x=x, w=w, h=h)


def _render(T):
    return frame_at(T, _W["clips"], _W["x"], _W["w"], _W["h"]).tobytes()


def probe_duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser(description="婚禮開場影片產生器")
    ap.add_argument("--config", default=str(ROOT / "config.json"))
    ap.add_argument("--preview", action="store_true", help="960x540、15fps 快速預覽")
    ap.add_argument("--still", type=float, help="只輸出指定秒數的畫面 PNG")
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    CFG.update(cfg)
    w, h = (960, 540) if args.preview else tuple(cfg.get("resolution", [1920, 1080]))
    fps = 15 if args.preview else cfg.get("fps", 30)

    photos = collect_photos(cfg)
    music = cfg.get("music") and ROOT / cfg["music"]
    if music and not music.exists():
        print(f"⚠ 找不到音樂檔 {music}，將輸出無聲影片")
        music = None
    music_len = probe_duration(music) if music else None
    hero = cfg.get("hero_photo")
    CFG["_hero"] = str(ROOT / cfg.get("photo_dir", "photos") / hero) if hero else (str(photos[0]) if photos else None)

    clips, total, x = build_timeline(cfg, photos, music_len)
    n_photo = sum(c["type"] == "photo" for c in clips)
    per = next((c["dur"] for c in clips if c["type"] == "photo"), 0)
    print(f"相片 {n_photo} 張，每張 {per:.2f} 秒，影片總長 {total // 60:.0f}:{total % 60:04.1f}"
          + (f"（音樂 {music_len:.1f} 秒" + ("，片尾淡出截斷）" if music_len > total + 0.5 else "）")
             if music_len else ""))
    if not photos:
        print("⚠ photos/ 內沒有相片，只會輸出片頭與片尾")

    out_dir = ROOT / "output"
    out_dir.mkdir(exist_ok=True)
    if args.still is not None:
        _init(CFG, clips, x, w, h)
        p = out_dir / f"still_{args.still:06.2f}.png"
        frame_at(args.still, clips, x, w, h).save(p)
        print("已輸出", p)
        return

    out = out_dir / ("preview.mp4" if args.preview else cfg.get("output", "wedding_opening.mp4"))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-stats",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-"]
    if music:
        cmd += ["-i", str(music)]
    cmd += ["-c:v", "libx264", "-preset", "veryfast" if args.preview else "slow",
            "-crf", "28" if args.preview else "16", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    if music:
        cmd += ["-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "256k",
                "-af", f"afade=t=in:d=1,afade=t=out:st={max(0, total - 4):.2f}:d=4", "-t", f"{total:.3f}"]
    cmd.append(str(out))

    times = [i / fps for i in range(int(total * fps))]
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    with Pool(args.workers, initializer=_init, initargs=(CFG, clips, x, w, h)) as pool:
        for i, buf in enumerate(pool.imap(_render, times, chunksize=8)):
            ff.stdin.write(buf)
            if i % (fps * 5) == 0:
                print(f"\r渲染中 {i / len(times) * 100:5.1f}%", end="", file=sys.stderr, flush=True)
    ff.stdin.close()
    ff.wait()
    print(f"\n完成：{out}")


if __name__ == "__main__":
    main()
