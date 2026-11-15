#!/usr/bin/env python3
"""Render the vertical sizzle-reel closer (1080x1920, 30 fps) with code.

Outputs (next to this script):
  reel_animation.mp4  ~13 s: blur-by-default -> peace-sign consent -> "also blur screens"
                      via MCP -> many front doors / one privacy layer -> tested -> end card
  end_card.mp4        the 3 s end card alone
  end_card.png        last frame of the end card

Usage:  python media/make_reel_animation.py            # render everything
        python media/make_reel_animation.py --preview 1.0 3.5 ...   # dump PNG stills
Needs numpy, opencv-python, Pillow, and ffmpeg on PATH.
"""
import os
import re
import subprocess
import sys
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
W, H, FPS = 1080, 1920, 30
END_START, END_LEN = 10.0, 3.0
TOTAL = END_START + END_LEN

# Safe area for TikTok/Reels UI: text stays inside x<=920, 220<=y<=1500.
SAFE_L, SAFE_R, SAFE_T, SAFE_B = 60, 900, 230, 1490

BG = (10, 12, 18)
WHITE = (245, 247, 250)
MUTED = (148, 156, 172)
GREEN = (74, 222, 128)
GOLD = (251, 191, 36)

SF = "/System/Library/Fonts/SFNS.ttf"
MONO = "/System/Library/Fonts/SFNSMono.ttf"
EMOJI = "/System/Library/Fonts/Apple Color Emoji.ttc"
EMOJI_RE = re.compile("(✌️|\U0001F5E3|⌨️|\U0001F916|\U0001F512)")


# ---------------------------------------------------------------- easing
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def p(t, a, b):
    return clamp((t - a) / (b - a)) if b > a else float(t >= a)


def out_cubic(x):
    return 1 - (1 - x) ** 3


def in_out_cubic(x):
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def out_back(x, s=1.7):
    x -= 1
    return 1 + (s + 1) * x ** 3 + s * x ** 2


def lerp(a, b, x):
    return a + (b - a) * x


# ---------------------------------------------------------------- fonts / layers
@lru_cache(None)
def font(size, weight="Bold", mono=False):
    f = ImageFont.truetype(MONO if mono else SF, size)
    f.set_variation_by_name(weight)
    return f


@lru_cache(None)
def emoji(ch, size, flip=False):
    f = ImageFont.truetype(EMOJI, 160)
    im = Image.new("RGBA", (220, 220))
    ImageDraw.Draw(im).text((10, 10), ch, font=f, embedded_color=True)
    im = im.crop(im.getbbox()).resize((size, size), Image.LANCZOS)
    return im.transpose(Image.FLIP_LEFT_RIGHT) if flip else im


@lru_cache(None)
def rich(s, size, fill=WHITE, weight="Bold", mono=False):
    """One line of text that may contain emoji -> tight RGBA layer."""
    f = font(size, weight, mono)
    parts = [x for x in EMOJI_RE.split(s) if x]
    es = int(size * 1.0)
    widths = [es + 6 if EMOJI_RE.fullmatch(x) else f.getlength(x) for x in parts]
    w, h = int(sum(widths)) + 8, int(size * 1.32)
    im = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(im)
    x, base = 2, int(size * 1.02)
    for part, pw in zip(parts, widths):
        if EMOJI_RE.fullmatch(part):
            im.alpha_composite(emoji(part, es), (int(x + 2), int(base - es * 0.9)))
        else:
            d.text((x, base), part, font=f, fill=fill, anchor="ls")
        x += pw
    return im


@lru_cache(None)
def rrect(w, h, r, fill=None, outline=None, width=0):
    s = 3
    im = Image.new("RGBA", (w * s, h * s))
    ImageDraw.Draw(im).rounded_rectangle(
        [0, 0, w * s - 1, h * s - 1], r * s, fill=fill, outline=outline, width=width * s)
    return im.resize((w, h), Image.LANCZOS)


@lru_cache(None)
def glow_of(layer_id, radius, color):
    layer = _GLOW_SRC[layer_id]
    a = layer.split()[3].filter(ImageFilter.GaussianBlur(radius))
    g = Image.new("RGBA", layer.size, color + (0,))
    g.putalpha(a)
    return g


_GLOW_SRC = {}


def padded(layer, pad):
    im = Image.new("RGBA", (layer.width + 2 * pad, layer.height + 2 * pad))
    im.alpha_composite(layer, (pad, pad))
    return im


def with_alpha(layer, a):
    if a >= 0.999:
        return layer
    out = layer.copy()
    out.putalpha(out.split()[3].point(lambda v: int(v * a)))
    return out


def put(canvas, layer, cx, cy, alpha=1.0, scale=1.0, anchor="c"):
    """Composite layer at (cx,cy) (center, or left-center with anchor='l')."""
    if alpha <= 0.003 or scale <= 0.01:
        return
    if abs(scale - 1) > 0.002:
        layer = layer.resize((max(1, int(layer.width * scale)), max(1, int(layer.height * scale))),
                             Image.BICUBIC)
    layer = with_alpha(layer, alpha)
    x = int(round(cx - (0 if anchor == "l" else layer.width / 2)))
    y = int(round(cy - layer.height / 2))
    # crop to canvas
    sx, sy = max(0, -x), max(0, -y)
    ex, ey = min(layer.width, W - x), min(layer.height, H - y)
    if ex <= sx or ey <= sy:
        return
    if sx or sy or ex < layer.width or ey < layer.height:
        layer = layer.crop((sx, sy, ex, ey))
    canvas.alpha_composite(layer, (x + sx, y + sy))


def put_glow(canvas, layer, key, cx, cy, alpha, radius=18, color=GREEN, strength=1.0, scale=1.0,
             anchor="c"):
    pad = radius * 3
    if key not in _GLOW_SRC:
        _GLOW_SRC[key] = padded(layer, pad)
    g = glow_of(key, radius, color)
    off = 0 if anchor == "c" else -pad * scale
    put(canvas, g, cx + off, cy, alpha * strength, scale, anchor)
    put(canvas, layer, cx, cy, alpha, scale, anchor)


def pill(text, color, size=34, fg=None, dot=False, outline=False):
    t = rich(text, size, fg or (color if outline else (8, 12, 10)), "Heavy")
    w, h = t.width + 44 + (26 if dot else 0), int(size * 1.7)
    if outline:
        im = rrect(w, h, h // 2, fill=color + (34,), outline=color + (255,), width=3).copy()
    else:
        im = rrect(w, h, h // 2, fill=color + (255,)).copy()
    x = 22
    if dot:
        d = ImageDraw.Draw(im)
        d.ellipse([x, h / 2 - 8, x + 16, h / 2 + 8], fill=(fg or (8, 12, 10)) if not outline else color)
        x += 26
    im.alpha_composite(t, (x, (h - t.height) // 2 + 1))
    return im


# ---------------------------------------------------------------- background
def make_bg():
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    bg = np.zeros((H, W, 3), np.float32) + np.array(BG, np.float32)
    g1 = np.exp(-(((xx - 300) / 700) ** 2 + ((yy - 500) / 700) ** 2))
    g2 = np.exp(-(((xx - 900) / 800) ** 2 + ((yy - 1500) / 800) ** 2))
    bg += g1[..., None] * np.array([10, 30, 22]) + g2[..., None] * np.array([18, 14, 34])
    dots = ((xx % 54 < 2.2) & (yy % 54 < 2.2)).astype(np.float32) * 9
    bg += dots[..., None]
    rng = np.random.default_rng(3)
    bg += rng.normal(0, 1.6, (H, W, 1))
    return Image.fromarray(np.clip(bg, 0, 255).astype(np.uint8)).convert("RGBA")


# ---------------------------------------------------------------- the "glasses view" scene
LX, LY, LW, LH = 60, 440, 840, 630          # lens rect on canvas
FIG_X = [170, 420, 670]                    # figure centers (lens coords)
HEAD_Y, HEAD_R = 236, 64
SCREEN = (486, 398, 774, 556)              # laptop screen (lens coords)


def draw_scene(s=2):
    im = Image.new("RGB", (LW * s, LH * s))
    d = ImageDraw.Draw(im)
    for y in range(LH * s):  # wall gradient
        k = y / (LH * s)
        d.line([(0, y), (LW * s, y)], fill=(int(lerp(30, 40, k)), int(lerp(36, 46, k)), int(lerp(56, 70, k))))
    S = lambda *v: [int(c * s) for c in v]
    d.rectangle(S(0, 0, LW, 18), fill=(40, 48, 72))
    skins = [(241, 194, 125), (141, 85, 36), (224, 172, 105)]
    shirts = [(45, 160, 150), (139, 115, 230), (240, 140, 70)]
    hair = [(43, 29, 20), (25, 18, 14), (110, 62, 38)]
    for i, cx in enumerate(FIG_X):
        hy, r = HEAD_Y, HEAD_R
        d.rounded_rectangle(S(cx - 118, hy + 96, cx + 118, LH + 80), radius=90 * s, fill=shirts[i])
        d.rectangle(S(cx - 20, hy + 50, cx + 20, hy + 100), fill=tuple(int(c * 0.85) for c in skins[i]))
        if i == 1:
            d.ellipse(S(cx - 36, hy - r - 44, cx + 36, hy - r + 22), fill=hair[i])  # bun
        if i == 2:
            d.rounded_rectangle(S(cx - r - 12, hy - r - 6, cx + r + 12, hy + 46), radius=50 * s, fill=hair[i])
        d.ellipse(S(cx - r, hy - r, cx + r, hy + r), fill=skins[i])
        if i == 0:
            d.chord(S(cx - r - 4, hy - r - 10, cx + r + 4, hy + r - 30), 180, 360, fill=hair[i])
        if i == 1:
            d.chord(S(cx - r - 2, hy - r - 4, cx + r + 2, hy + 20), 190, 350, fill=hair[i])
        ink = (24, 20, 26)
        for ex in (-23, 23):
            d.ellipse(S(cx + ex - 8, hy - 6, cx + ex + 8, hy + 10), fill=ink)
            d.ellipse(S(cx + ex - 3, hy - 3, cx + ex + 1, hy + 1), fill=(255, 255, 255))
        d.arc(S(cx - 24, hy + 2, cx + 24, hy + 36), 20, 160, fill=ink, width=5 * s)
        for ex in (-38, 38):
            d.ellipse(S(cx + ex - 10, hy + 14, cx + ex + 10, hy + 26), fill=(240, 120, 130))
    # table
    d.rectangle(S(0, 572, LW, LH), fill=(22, 26, 38))
    d.rectangle(S(0, 572, LW, 578), fill=(52, 60, 84))
    # laptop
    x0, y0, x1, y1 = SCREEN
    d.rounded_rectangle(S(x0 - 12, y0 - 12, x1 + 12, y1 + 12), radius=14 * s, fill=(14, 16, 22))
    d.rectangle(S(x0, y0, x1, y1), fill=(236, 240, 246))
    d.rectangle(S(x0, y0, x1, y0 + 26), fill=(59, 130, 246))
    for k, c in enumerate([(239, 68, 68), (250, 204, 21), (34, 197, 94)]):
        d.ellipse(S(x0 + 10 + k * 18, y0 + 8, x0 + 20 + k * 18, y0 + 18), fill=c)
    for k, (bh, c) in enumerate([(60, (99, 102, 241)), (96, (236, 72, 153)), (40, (14, 165, 233)),
                                  (110, (34, 197, 94)), (76, (249, 115, 22))]):
        d.rectangle(S(x0 + 18 + k * 28, y1 - 14 - bh, x0 + 38 + k * 28, y1 - 14), fill=c)
    for k in range(6):
        d.rectangle(S(x0 + 168, y0 + 42 + k * 18, x1 - 16 - (k % 3) * 30, y0 + 50 + k * 18), fill=(40, 48, 64))
    d.polygon(S(x0 - 44, y1 + 12, x1 + 44, y1 + 12, x1 + 60, y1 + 30, x0 - 60, y1 + 30), fill=(70, 78, 96))
    return np.asarray(im.resize((LW, LH), Image.LANCZOS)).astype(np.float32)


def feather(mask, sigma):
    return cv2.GaussianBlur(mask, (0, 0), sigma)


SCENE = draw_scene()
# Soft, frosted face blur (like the product's feathered oval) and a rectangular screen blur.
FACE_BLUR = cv2.GaussianBlur(SCENE, (0, 0), 15) * 0.92 + 14
SCREEN_BLUR = cv2.GaussianBlur(cv2.GaussianBlur(SCENE, (0, 0), 11), (0, 0), 6)
FACE_MASKS = []
for cx in FIG_X:
    m = np.zeros((LH, LW), np.float32)
    cv2.ellipse(m, (cx, HEAD_Y + 4), (92, 104), 0, 0, 360, 1.0, -1, cv2.LINE_AA)
    FACE_MASKS.append(feather(m, 12))
SCREEN_MASK = np.zeros((LH, LW), np.float32)
SCREEN_MASK[SCREEN[1] - 4:SCREEN[3] + 5, SCREEN[0] - 4:SCREEN[2] + 5] = 1
SCREEN_MASK = feather(SCREEN_MASK, 2.5)


@lru_cache(None)
def lens_shape():
    s = 3
    m = Image.new("L", (LW * s, LH * s))
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0, 0, LW * s - 1, LH * s - 1], 56 * s, fill=255)
    d.ellipse([(LW / 2 - 78) * s, (LH - 52) * s, (LW / 2 + 78) * s, (LH + 60) * s], fill=0)  # nose bridge
    m = m.resize((LW, LH), Image.LANCZOS)
    edge = m.filter(ImageFilter.MaxFilter(5))
    ring = Image.new("RGBA", (LW + 40, LH + 40))
    o = Image.new("RGBA", (LW, LH), (255, 255, 255, 0))
    o.putalpha(Image.fromarray(np.clip(np.asarray(edge, np.int16) - np.asarray(m.filter(ImageFilter.MinFilter(5)), np.int16), 0, 255).astype(np.uint8)))
    ring.alpha_composite(o, (20, 20))
    return m, ring


def brackets(d, x0, y0, x1, y1, color, L=26, w=5):
    for (px, py, sx, sy) in [(x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)]:
        d.line([(px, py), (px + sx * L, py)], fill=color, width=w)
        d.line([(px, py), (px, py + sy * L)], fill=color, width=w)


def render_lens(t):
    # blur amounts
    a2 = 1 - in_out_cubic(p(t, 2.8, 3.35))
    sb = in_out_cubic(p(t, 6.05, 6.45))
    M = FACE_MASKS[0] + FACE_MASKS[2] + FACE_MASKS[1] * a2
    out = SCENE * (1 - np.clip(M, 0, 1))[..., None] + FACE_BLUR * np.clip(M, 0, 1)[..., None]
    sm = (SCREEN_MASK * sb)[..., None]
    out = out * (1 - sm) + SCREEN_BLUR * sm
    im = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).convert("RGBA")
    d = ImageDraw.Draw(im)
    # detection brackets
    for i, cx in enumerate(FIG_X):
        vis = (i == 1) and t >= 3.1
        col = GREEN + (255,) if vis else (255, 255, 255, 150)
        brackets(d, cx - 92, HEAD_Y - 100, cx + 92, HEAD_Y + 108, col)
    if sb > 0.01:
        x0, y0, x1, y1 = SCREEN
        brackets(d, x0 - 18, y0 - 18, x1 + 18, y1 + 18, (255, 255, 255, int(200 * sb)), L=30)
    # peace signs (consent gesture)
    up = out_back(p(t, 2.25, 2.75), 1.4) * (1 - in_out_cubic(p(t, 4.05, 4.4)))
    if up > 0.001:
        cx = FIG_X[1]
        wob = np.sin(t * 9) * 4 * p(t, 2.75, 3.0)
        for side in (-1, 1):
            e = emoji("✌️", 96, flip=(side > 0))
            put_local(im, e, cx + side * 132, HEAD_Y + 10 + lerp(260, 0, up) + side * wob, alpha=clamp(up * 1.5))
    # VISIBLE pill
    pv = p(t, 3.1, 3.45)
    if pv > 0:
        put_local(im, pill("VISIBLE", GREEN, 28), FIG_X[1], HEAD_Y - 138, out_cubic(pv),
                  scale=lerp(0.6, 1, out_back(pv)))
    ps = p(t, 6.3, 6.6)
    if ps > 0:
        put_local(im, pill("SCREEN BLURRED", WHITE, 26), (SCREEN[0] + SCREEN[2]) / 2, SCREEN[1] - 46,
                  out_cubic(ps), scale=lerp(0.6, 1, out_back(ps)))
    # HUD
    hud = pill("PRIVACY FILTER ON", GREEN, 24, dot=True, outline=True)
    put_local(im, hud, 34, 50, 1, anchor="l")
    k = in_out_cubic(p(t, 5.95, 6.25))
    if k < 1:
        put_local(im, pill("\U0001F512 DEFAULT", GOLD, 24, outline=True), LW - 34, 50, 1 - k, anchor="r")
    if k > 0:
        put_local(im, pill("+ SCREENS", GREEN, 24), LW - 34, 50, k, anchor="r",
                  scale=lerp(0.7, 1, out_back(k)))
    mask, ring = lens_shape()
    im.putalpha(Image.fromarray((np.asarray(im.split()[3], np.float32) * np.asarray(mask, np.float32) / 255).astype(np.uint8)))
    full = Image.new("RGBA", (LW + 40, LH + 40))
    full.alpha_composite(im, (20, 20))
    full.alpha_composite(with_alpha(ring, 0.35))
    return full


def put_local(im, layer, cx, cy, alpha=1.0, scale=1.0, anchor="c"):
    if alpha <= 0.003:
        return
    if abs(scale - 1) > 0.002:
        layer = layer.resize((max(1, int(layer.width * scale)), max(1, int(layer.height * scale))), Image.BICUBIC)
    layer = with_alpha(layer, alpha)
    x = cx - layer.width / 2 if anchor == "c" else (cx if anchor == "l" else cx - layer.width)
    y = cy - layer.height / 2
    tmp = Image.new("RGBA", im.size)
    tmp.paste(layer, (int(round(x)), int(round(y))))
    im.alpha_composite(tmp)


# ---------------------------------------------------------------- captions
CAPTIONS = [
    (0.0, 2.2, ["Faces blurred", "by default."], [WHITE, WHITE]),
    (2.2, 4.2, ["✌️✌️ = consent", "to be seen."], [WHITE, GREEN]),
    (4.2, 7.0, ["Change the rules", "in plain English."], [WHITE, GREEN]),
    (7.0, 8.8, ["Many front doors.", "One privacy layer."], [WHITE, GREEN]),
    (8.8, END_START, ["Tested before", "it goes live."], [WHITE, GREEN]),
]


def draw_captions(cv, t):
    for (a, b, lines, cols) in CAPTIONS:
        if not (a - 0.01 <= t < b + 0.01):
            continue
        for j, (ln, col) in enumerate(zip(lines, cols)):
            k = out_cubic(p(t, a + 0.04 + j * 0.08, a + 0.42 + j * 0.08))
            ko = 1 - in_out_cubic(p(t, b - 0.18, b))
            if a == 0.0:
                k = out_cubic(p(t, 0.1 + j * 0.08, 0.5 + j * 0.08))
            lay = rich(ln, 76, col, "Heavy")
            put(cv, lay, SAFE_L, 282 + j * 90 + (1 - k) * 40 - (1 - ko) * 20, k * ko, anchor="l")


# ---------------------------------------------------------------- beat 3: MCP panel
def draw_mcp(cv, t, alpha):
    if alpha <= 0:
        return
    kb = out_cubic(p(t, 4.25, 4.6))
    # speech bubble
    bw, bh = 840, 104
    bub = rrect(bw, bh, 52, fill=(255, 255, 255, 22), outline=(255, 255, 255, 70), width=2).copy()
    bub.alpha_composite(emoji("\U0001F5E3", 58), (28, 23))
    full = "“also blur screens”"
    n = int(len(full) * p(t, 4.45, 5.0))
    txt = full[:n] + ("|" if (t < 5.15 and int(t * 6) % 2 == 0) else "")
    if txt:
        bub.alpha_composite(rich(txt, 50, WHITE, "Bold"), (112, 20))
    put(cv, bub, SAFE_L + bw / 2, 1160 + (1 - kb) * 30, kb * alpha)
    # flow dot from bubble to MCP node
    kf = p(t, 5.0, 5.3)
    if 0 < kf < 1:
        y = lerp(1212, 1300, in_out_cubic(kf))
        dot = rrect(22, 22, 11, fill=GREEN + (255,))
        put_glow(cv, dot, "flowdot", 155, y, alpha, radius=10)
    # MCP node
    km = p(t, 5.15, 5.45)
    node = rrect(190, 110, 26, fill=GREEN + (30,), outline=GREEN + (255,), width=3).copy()
    tl = rich("MCP", 50, GREEN, "Black")
    node.alpha_composite(tl, ((190 - tl.width) // 2, (110 - tl.height) // 2 + 2))
    if km > 0:
        put_glow(cv, node, "mcpnode", SAFE_L + 95, 1340, alpha * out_cubic(km), radius=16,
                 strength=0.8 + 0.3 * np.sin(t * 8), scale=lerp(0.7, 1, out_back(km)))
    # tool calls ticking
    for j, (name, t0) in enumerate([("save_profile", 5.35), ("test_profile", 5.62), ("activate_profile", 5.89)]):
        kt = p(t, t0, t0 + 0.22)
        if kt <= 0:
            continue
        ln = rich(name + "()", 38, (210, 216, 228), "Medium", mono=True)
        y = 1282 + j * 58
        put(cv, ln, 290 + (1 - out_cubic(kt)) * 30, y, alpha * out_cubic(kt), anchor="l")
        kc = p(t, t0 + 0.15, t0 + 0.35)
        if kc > 0:
            ck = rich("✓", 42, GREEN, "Black")
            put(cv, ck, 290 + ln.width + 16 + ck.width / 2, y, alpha * kc, scale=lerp(0.4, 1, out_back(kc)))


# ---------------------------------------------------------------- beat 4: architecture
DOORS = [("\U0001F5E3", "voice", 190), ("⌨️", "type", 480), ("\U0001F916", "any AI agent", 770)]
NODE_Y, GL_Y = 900, 1190


def bez(p0, p1, p2, p3, n=48):
    ts = np.linspace(0, 1, n)[:, None]
    return ((1 - ts) ** 3 * p0 + 3 * (1 - ts) ** 2 * ts * p1 + 3 * (1 - ts) * ts ** 2 * p2 + ts ** 3 * p3)


ARCH_PATHS = [bez(np.array([x, 652.]), np.array([x, 760.]), np.array([480., 740.]), np.array([480., 822.]))
              for _, _, x in DOORS]
DOWN_PATH = bez(np.array([480., 978.]), np.array([480., 1030.]), np.array([480., 1060.]), np.array([480., 1112.]))


@lru_cache(None)
def glasses_icon(w=330):
    s = 3
    h = int(w * 0.42)
    im = Image.new("RGBA", (w * s, h * s))
    d = ImageDraw.Draw(im)
    lw = int(w * 0.40)
    st = 10 * s
    d.rounded_rectangle([st, int(h * 0.18) * s, lw * s, (h - 6) * s], 34 * s, outline=WHITE + (255,), width=st)
    d.rounded_rectangle([(w - lw) * s, int(h * 0.18) * s, w * s - st, (h - 6) * s], 34 * s, outline=WHITE + (255,), width=st)
    d.arc([(lw - 6) * s, int(h * 0.16) * s, (w - lw + 6) * s, int(h * 0.62) * s], 200, 340, fill=WHITE + (255,), width=st)
    return im.resize((w, h), Image.LANCZOS)


def draw_arch(cv, t, alpha):
    if alpha <= 0:
        return
    ov = np.zeros((H, W, 4), np.uint8)
    kl = in_out_cubic(p(t, 7.3, 7.7))
    kd = in_out_cubic(p(t, 7.8, 8.0))
    for path, k in [(pp, kl) for pp in ARCH_PATHS] + [(DOWN_PATH, kd)]:
        n = int(len(path) * k)
        if n >= 2:
            cv2.polylines(ov, [path[:n].astype(np.int32)], False, (255, 255, 255, 110), 4, cv2.LINE_AA)
    lines = Image.fromarray(ov)
    cv.alpha_composite(with_alpha(lines, alpha))
    # pulses
    dot = rrect(20, 20, 10, fill=GREEN + (255,))
    if t > 7.7:
        for i, path in enumerate(ARCH_PATHS):
            u = ((t - 7.7) * 1.6 + i * 0.33) % 1.0
            x, y = path[int(u * (len(path) - 1))]
            put_glow(cv, dot, "pdot", x, y, alpha * clamp((t - 7.7) * 5), radius=9)
    if t > 8.0:
        u = ((t - 8.0) * 1.8) % 1.0
        x, y = DOWN_PATH[int(u * (len(DOWN_PATH) - 1))]
        put_glow(cv, dot, "pdot", x, y, alpha * clamp((t - 8.0) * 5), radius=9)
    # doors
    for i, (em, label, x) in enumerate(DOORS):
        k = p(t, 7.02 + i * 0.1, 7.4 + i * 0.1)
        if k <= 0:
            continue
        card = rrect(250, 180, 32, fill=(255, 255, 255, 18), outline=(255, 255, 255, 60), width=2).copy()
        e = emoji(em, 78)
        card.alpha_composite(e, ((250 - 78) // 2, 22))
        lb = rich(label, 36, WHITE, "Bold")
        card.alpha_composite(lb, ((250 - lb.width) // 2, 108))
        put(cv, card, x, 562 + (1 - out_cubic(k)) * 50, alpha * out_cubic(k), scale=lerp(0.8, 1, out_back(k)))
    # one privacy layer
    k = p(t, 7.55, 7.9)
    if k > 0:
        node = rrect(660, 156, 40, fill=(20, 40, 30, 255), outline=GREEN + (255,), width=4).copy()
        s1 = rich("MCP TOOLS", 28, GREEN, "Bold", mono=True)
        s2 = rich("one privacy layer", 60, WHITE, "Heavy")
        node.alpha_composite(s1, ((660 - s1.width) // 2, 16))
        node.alpha_composite(s2, ((660 - s2.width) // 2, 56))
        put_glow(cv, node, "archnode", 480, NODE_Y, alpha * out_cubic(k), radius=26,
                 strength=0.7 + 0.25 * np.sin(t * 6), scale=lerp(0.75, 1, out_back(k)))
    # glasses
    k = p(t, 7.95, 8.3)
    if k > 0:
        g = glasses_icon()
        put_glow(cv, g, "glasses", 480, GL_Y, alpha * out_cubic(k), radius=14, scale=lerp(0.6, 1, out_back(k)))
        lb = rich("on the glasses", 40, MUTED, "Semibold")
        put(cv, lb, 480, GL_Y + 112, alpha * out_cubic(p(t, 8.1, 8.4)))


# ---------------------------------------------------------------- beat 5: tested on synthetic clips
def thumb(i):
    w, h = 190, 250
    s = 2
    im = Image.new("RGB", (w * s, h * s), [(34, 40, 60), (40, 34, 56), (30, 46, 52), (46, 40, 36)][i])
    d = ImageDraw.Draw(im)
    cx = [80, 120, 95, 70][i] * s
    hy = [95, 85, 110, 90][i] * s
    col = [(240, 140, 70), (45, 160, 150), (139, 115, 230), (90, 140, 230)][i]
    skin = [(224, 172, 105), (141, 85, 36), (241, 194, 125), (190, 130, 80)][i]
    d.rounded_rectangle([cx - 60 * s, hy + 48 * s, cx + 60 * s, (h + 40) * s], 44 * s, fill=col)
    d.ellipse([cx - 34 * s, hy - 34 * s, cx + 34 * s, hy + 34 * s], fill=skin)
    for ex in (-12, 12):
        d.ellipse([cx + (ex - 4) * s, hy - 4 * s, cx + (ex + 4) * s, hy + 4 * s], fill=(20, 20, 24))
    if i in (1, 3):  # a phone/laptop screen in frame
        sx, sy = (20 * s, 150 * s) if i == 1 else (110 * s, 160 * s)
        d.rectangle([sx, sy, sx + 64 * s, sy + 44 * s], fill=(230, 236, 246))
        d.rectangle([sx, sy, sx + 64 * s, sy + 9 * s], fill=(59, 130, 246))
    a = np.asarray(im.resize((w, h), Image.LANCZOS)).astype(np.float32)
    bl = cv2.GaussianBlur(a, (0, 0), 8) * 0.92 + 14
    m = np.zeros((h, w), np.float32)
    cv2.ellipse(m, (cx // s, hy // s), (46, 52), 0, 0, 360, 1.0, -1, cv2.LINE_AA)
    if i in (1, 3):
        m[sy // s - 3:sy // s + 48, sx // s - 3:sx // s + 68] = 1
    m = feather(m, 5)[..., None]
    a = a * (1 - m) + bl * m
    out = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert("RGBA")
    mask = rrect(w, h, 18, fill=(255, 255, 255, 255)).split()[3]
    out.putalpha(mask)
    return out


THUMBS = [thumb(i) for i in range(4)]


def draw_tested(cv, t, alpha):
    if alpha <= 0:
        return
    xs = [60 + 95 + i * 216.6 for i in range(4)]
    y = 720
    kin = out_cubic(p(t, 8.85, 9.2))
    strip = rrect(860, 330, 26, fill=(255, 255, 255, 14), outline=(255, 255, 255, 40), width=2).copy()
    d = ImageDraw.Draw(strip)
    for k in range(18):
        x = 22 + k * 47.5
        d.rounded_rectangle([x, 12, x + 22, 26], 4, fill=(255, 255, 255, 40))
        d.rounded_rectangle([x, 304, x + 22, 318], 4, fill=(255, 255, 255, 40))
    put(cv, strip, 480, y, alpha * kin, scale=lerp(0.94, 1, kin))
    for i, x in enumerate(xs):
        k = out_cubic(p(t, 8.9 + i * 0.06, 9.25 + i * 0.06))
        put(cv, THUMBS[i], x, y + (1 - k) * 40, alpha * k)
    # scan bar
    ks = in_out_cubic(p(t, 9.15, 9.6))
    if 0 < ks < 1:
        bar = rrect(8, 290, 4, fill=GREEN + (255,))
        put_glow(cv, bar, "scanbar", lerp(60, 900, ks), y, alpha, radius=14)
    for i, x in enumerate(xs):
        kc = p(t, 9.15 + (x - 60) / 840 * 0.45, 9.3 + (x - 60) / 840 * 0.45)
        if kc > 0:
            b = rrect(56, 56, 28, fill=GREEN + (255,)).copy()
            ck = rich("✓", 40, (8, 12, 10), "Black")
            b.alpha_composite(ck, ((56 - ck.width) // 2, (56 - ck.height) // 2 + 2))
            put(cv, b, x + 62, y - 92, alpha * kc, scale=lerp(0.3, 1, out_back(kc)))
    kl = out_cubic(p(t, 9.0, 9.35))
    lb = rich("test_profile() · synthetic clips", 36, (210, 216, 228), "Medium", mono=True)
    put(cv, lb, 480, y + 215, alpha * kl)
    kp = p(t, 9.45, 9.75)
    if kp > 0:
        pl = pill("Then live on the glasses in ~1 s", GREEN, 36)
        put_glow(cv, pl, "livepill", 480, y + 330, alpha * out_cubic(kp), radius=16, strength=0.6,
                 scale=lerp(0.7, 1, out_back(kp)))


# ---------------------------------------------------------------- end card
END_CX = 490


def draw_end(cv, lt):
    """lt = seconds since the end card started."""
    kg = out_cubic(p(lt, 0.0, 0.5))
    g = glasses_icon(260)
    put_glow(cv, g, "endglasses", END_CX, 640 - (1 - kg) * 30, kg, radius=18,
             strength=0.6 + 0.2 * np.sin(lt * 3))
    for j, (ln, col) in enumerate([("A programmable", WHITE), ("privacy layer.", GREEN)]):
        k = out_cubic(p(lt, 0.15 + j * 0.12, 0.65 + j * 0.12))
        lay = rich(ln, 104, col, "Heavy")
        y = 820 + j * 122 + (1 - k) * 50
        if col == GREEN:
            put_glow(cv, lay, "endl2", END_CX, y, k, radius=22, strength=0.45)
        else:
            put(cv, lay, END_CX, y, k)
    parts = [("Describe it.", WHITE), ("Test it.", WHITE), ("Wear it.", GREEN)]
    lays = [rich(s, 52, c, "Semibold") for s, c in parts]
    gap = 22
    total = sum(l.width for l in lays) + gap * 2
    x = END_CX - total / 2
    for i, l in enumerate(lays):
        k = p(lt, 0.75 + i * 0.16, 1.1 + i * 0.16)
        put(cv, l, x, 1080 + (1 - out_cubic(k)) * 24, out_cubic(k), scale=lerp(0.85, 1, out_back(k)), anchor="l")
        x += l.width + gap
    # underline sweep
    ku = in_out_cubic(p(lt, 1.3, 1.9))
    if ku > 0:
        wl = int(560 * ku)
        if wl > 2:
            put(cv, rrect(wl, 6, 3, fill=GREEN + (200,)), END_CX - 280 + wl / 2, 1170, 1)


# ---------------------------------------------------------------- compose
BGIMG = make_bg()


def render(t):
    cv = BGIMG.copy()
    # whole-piece fade in from black (comes after demo footage)
    # lens: in 0->0.45, out 7.0->7.35
    kin = out_cubic(p(t, 0.0, 0.45))
    kout = in_out_cubic(p(t, 6.95, 7.3))
    la = kin * (1 - kout)
    if la > 0:
        lens = render_lens(t)
        sc = lerp(1.05, 1.0, kin) * lerp(1, 0.9, kout)
        put(cv, lens, LX + LW / 2, LY + LH / 2 - kout * 120, la, scale=sc)
    draw_mcp(cv, t, 1 - kout if t >= 4.2 else 0)
    if 6.95 <= t < 8.85:
        draw_arch(cv, t, 1 - in_out_cubic(p(t, 8.6, 8.85)))
    if 8.8 <= t < END_START + 0.05:
        draw_tested(cv, t, 1 - in_out_cubic(p(t, 9.8, 10.0)))
    draw_captions(cv, t)
    if t >= END_START - 0.05:
        draw_end(cv, t - END_START)
    out = cv.convert("RGB")
    if t < 0.2:
        out = Image.blend(Image.new("RGB", (W, H), BG), out, out_cubic(p(t, 0, 0.2)))
    return out


def encode(path, frames_iter):
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "slow", "-crf", "20",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", path]
    pr = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for fr in frames_iter:
        pr.stdin.write(fr.tobytes())
    pr.stdin.close()
    pr.wait()


def end_card_frame(lt):
    cv = BGIMG.copy()
    draw_end(cv, lt)
    out = cv.convert("RGB")
    if lt < 0.15:
        out = Image.blend(Image.new("RGB", (W, H), BG), out, out_cubic(p(lt, 0, 0.15)))
    return out


def main():
    if "--preview" in sys.argv:
        outdir = os.environ.get("PREVIEW_DIR", os.path.join(HERE, "_preview"))
        os.makedirs(outdir, exist_ok=True)
        for s in sys.argv[sys.argv.index("--preview") + 1:]:
            render(float(s)).save(os.path.join(outdir, f"t{float(s):05.2f}.png"))
        return
    n = int(round(TOTAL * FPS))
    encode(os.path.join(HERE, "reel_animation.mp4"),
           (render(i / FPS) for i in range(n)))
    m = int(END_LEN * FPS)
    encode(os.path.join(HERE, "end_card.mp4"), (end_card_frame(i / FPS) for i in range(m)))
    end_card_frame(END_LEN).save(os.path.join(HERE, "end_card.png"))
    print("done")


if __name__ == "__main__":
    main()
