"""Synthetic screens clip: the InsightFace sample group photo (slow pan, like synth_slow_pan) with a
code-drawn phone and laptop moving across it. No real screen content: icons/text bars are flat colour blocks.

  python scripts/make_synth_screens.py          -> corpus/synth_screens/{raw.mp4, labels.json}

labels.json carries per-frame ground-truth screen boxes ("screens": [[{"id","kind","box"}...] per frame]);
the box is the device's display panel (what must be blurred). raw.mp4 is gitignored; this script regenerates it.
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "corpus" / "synth_screens"
W, H, FPS, N = 1280, 720, 30, 240


def rrect(img, x1, y1, x2, y2, r, col):
    x1, y1, x2, y2, r = int(x1), int(y1), int(x2), int(y2), int(r)
    cv2.rectangle(img, (x1 + r, y1), (x2 - r, y2), col, -1)
    cv2.rectangle(img, (x1, y1 + r), (x2, y2 - r), col, -1)
    for cx, cy in ((x1 + r, y1 + r), (x2 - r, y1 + r), (x1 + r, y2 - r), (x2 - r, y2 - r)):
        cv2.circle(img, (cx, cy), r, col, -1, cv2.LINE_AA)


def ui(img, x1, y1, x2, y2, seed, dense):
    """Fake UI: light background, a header bar, rows of colour blocks (no text, no real data)."""
    rng = np.random.default_rng(seed)
    cv2.rectangle(img, (int(x1), int(y1)), (int(x2), int(y2)), (245, 240, 235), -1)
    w, h = x2 - x1, y2 - y1
    cv2.rectangle(img, (int(x1), int(y1)), (int(x2), int(y1 + h * 0.08)), (200, 120, 40), -1)
    if dense:       # phone: gradient wallpaper + app-icon grid + dock
        g = np.linspace(0, 1, max(1, int(y2) - int(y1)))[:, None, None]
        top, bot = np.array([140, 70, 30]), np.array([90, 40, 120])
        img[int(y1):int(y2), int(x1):int(x2)] = (top * (1 - g) + bot * g).astype(np.uint8)
        rrect(img, x1 + w * 0.05, y2 - h * 0.13, x2 - w * 0.05, y2 - h * 0.02, w * 0.06, (170, 150, 150))
        for r in range(5):
            for c in range(4):
                cx, cy = x1 + w * (0.15 + c * 0.233), y1 + h * (0.18 + r * 0.15)
                col = tuple(int(v) for v in rng.integers(40, 230, 3))
                rrect(img, cx - w * 0.08, cy - w * 0.08, cx + w * 0.08, cy + w * 0.08, w * 0.03, col)
    else:           # laptop: sidebar + text-ish grey bars + an image block
        cv2.rectangle(img, (int(x1), int(y1 + h * 0.08)), (int(x1 + w * 0.2), int(y2)), (225, 220, 215), -1)
        for r in range(9):
            y = y1 + h * (0.15 + r * 0.08)
            cv2.rectangle(img, (int(x1 + w * 0.25), int(y)), (int(x1 + w * (0.4 + 0.5 * rng.random())), int(y + h * 0.03)),
                          (90, 90, 90), -1)
        cv2.rectangle(img, (int(x1 + w * 0.62), int(y1 + h * 0.15)), (int(x1 + w * 0.95), int(y1 + h * 0.5)),
                      (60, 150, 90), -1)


def draw_phone(img, cx, cy, h, seed):
    w = h * 0.49
    x1, y1, x2, y2 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    rrect(img, x1, y1, x2, y2, w * 0.14, (25, 25, 28))                    # body / bezel
    b = w * 0.05
    sx1, sy1, sx2, sy2 = x1 + b, y1 + b * 1.6, x2 - b, y2 - b * 1.6
    ui(img, sx1, sy1, sx2, sy2, seed, dense=True)
    return [x1, y1, x2, y2]


def draw_laptop(img, cx, cy, w, seed):
    h = w * 0.64
    x1, y1, x2, y2 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    rrect(img, x1, y1, x2, y2, w * 0.02, (30, 30, 32))                    # lid / bezel
    b = w * 0.03
    ui(img, x1 + b, y1 + b * 1.3, x2 - b, y2 - b, seed, dense=False)
    # keyboard deck (perspective trapezoid) below the lid
    deck = np.array([[x1 - w * 0.02, y2], [x2 + w * 0.02, y2], [x2 + w * 0.12, y2 + h * 0.22],
                     [x1 - w * 0.12, y2 + h * 0.22]], np.int32)
    cv2.fillPoly(img, [deck], (175, 175, 180), cv2.LINE_AA)
    for r in range(4):
        yy = y2 + h * (0.03 + r * 0.04)
        cv2.line(img, (int(x1 + w * 0.02), int(yy)), (int(x2 - w * 0.02), int(yy)), (140, 140, 145), 2)
    return [x1, y1, x2, y2]


def main():
    import insightface
    src = cv2.imread(str(Path(insightface.__file__).parent / "data" / "images" / "t1.jpg"))
    canvas = np.zeros((H * 2, W * 2, 3), np.uint8) + 40
    sh, sw = src.shape[:2]
    s = min(H * 2 / sh, W * 2 / sw) * 0.9
    im = cv2.resize(src, None, fx=s, fy=s)
    y0, x0 = (H * 2 - im.shape[0]) // 2, (W * 2 - im.shape[1]) // 2
    canvas[y0:y0 + im.shape[0], x0:x0 + im.shape[1]] = im
    OUT.mkdir(parents=True, exist_ok=True)
    vw = cv2.VideoWriter(str(OUT / "raw.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    gt = []
    for i in range(N):
        t = i / FPS
        cx, cy, z = 0.5 + 0.12 * np.sin(t), 0.5, 1.6                       # same camera as synth_slow_pan
        ww, hh = W * 2 / z, H * 2 / z
        M_ = np.float32([[W / ww, 0, -(cx * W * 2 - ww / 2) * W / ww], [0, H / hh, -(cy * H * 2 - hh / 2) * H / hh]])
        fr = cv2.warpAffine(canvas, M_, (W, H), borderValue=(40, 40, 40))
        boxes = []
        # laptop: on screen for frames 40-200, slides right->left along the top, width 420->300 px
        if 40 <= i < 200:
            v = (i - 40) / 159
            boxes.append({"id": "laptop", "kind": "laptop",
                          "box": draw_laptop(fr, W * (0.78 - 0.5 * v), H * 0.28, 420 - 120 * v, seed=2)})
        # phone: held up in front (drawn last), sweeps left->right low in frame, 180->300->180 px tall
        u = i / (N - 1)
        boxes.append({"id": "phone", "kind": "phone",
                      "box": draw_phone(fr, W * (0.1 + 0.8 * u), H * (0.74 + 0.04 * np.sin(t * 2)),
                                        180 + 120 * np.sin(np.pi * u), seed=1)})
        for b in boxes:
            b["box"] = [round(float(np.clip(b["box"][0], 0, W)), 1), round(float(np.clip(b["box"][1], 0, H)), 1),
                        round(float(np.clip(b["box"][2], 0, W)), 1), round(float(np.clip(b["box"][3], 0, H)), 1)]
        gt.append(boxes)
        vw.write(fr)
    vw.release()
    (OUT / "labels.json").write_text(json.dumps({
        "events": [], "favorites": [], "tags": ["synth", "screens"],
        "notes": "slow pan of the group photo + code-drawn phone and laptop (fake UI blocks); every face and "
                 "every screen should stay blurred. Regenerate: python scripts/make_synth_screens.py",
        "synthetic": True, "screens": gt}, separators=(",", ":")))
    print(f"wrote {OUT.relative_to(ROOT)} ({N} frames)", file=sys.stderr)


if __name__ == "__main__":
    main()
