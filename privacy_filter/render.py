"""Blur rendering + the analytic mask used by both the renderer and the metrics."""
import cv2
import numpy as np


def mask_at(xs, ys, region, feather):
    """Blur weight (0..1) of an elliptical feathered region at points xs, ys."""
    x1, y1, x2, y2 = region["box"]
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    a, b = max((x2 - x1) / 2, 1), max((y2 - y1) / 2, 1)
    r = np.sqrt(((xs - cx) / a) ** 2 + ((ys - cy) / b) ** 2)
    inner = 1 - feather
    m = np.clip((1 - r) / max(feather, 1e-6), 0, 1)
    m[r <= inner] = 1
    return m * region["blur"]


def coverage(gt_box, regions, feather, thresh=0.9, grid=9):
    """Fraction of a GT face box that is blurred at >= thresh."""
    if not regions:
        return 0.0
    # sample the face ellipse inscribed in the GT box (box corners are background)
    x1, y1, x2, y2 = gt_box
    u, v = np.meshgrid(np.linspace(-1, 1, grid), np.linspace(-1, 1, grid))
    inside = u ** 2 + v ** 2 <= 1
    xs = (x1 + x2) / 2 + u[inside] * (x2 - x1) / 2
    ys = (y1 + y2) / 2 + v[inside] * (y2 - y1) / 2
    m = np.zeros_like(xs)
    for r in regions:
        m = np.maximum(m, mask_at(xs, ys, r, feather))
    return float((m >= thresh).mean())


def render(frame, regions, rcfg):
    """Cost is independent of face size: blur and mask are computed on a small grid, then upscaled."""
    out = frame
    if not regions:
        return out
    out = frame.copy()
    H, W = frame.shape[:2]
    for r in regions:
        x1, y1, x2, y2 = [int(round(v)) for v in r["box"]]
        x1, y1, x2, y2 = max(0, x1), max(0, y1), min(W, x2), min(H, y2)
        rw, rh = x2 - x1, y2 - y1
        if rw < 2 or rh < 2:
            continue
        crop = out[y1:y2, x1:x2]
        cells = rcfg["pixel_cells"]
        cw = max(2, cells)
        ch = max(2, int(cells * rh / rw))
        small = cv2.resize(crop, (cw, ch), interpolation=cv2.INTER_AREA)
        # soften at an intermediate size (same look as blurring at full size with k = width/6)
        mw, mh = min(rw, cw * 8), min(rh, ch * 8)
        mid = cv2.resize(small, (mw, mh), interpolation=cv2.INTER_CUBIC)
        k = max(3, (mw // 6) | 1)
        mid = cv2.GaussianBlur(mid, (k, k), 0)
        blurred = cv2.resize(mid, (rw, rh), interpolation=cv2.INTER_LINEAR)
        # feathered ellipse mask on a small grid (it's smooth), upscaled
        gw, gh = min(rw, 96), min(rh, max(2, int(96 * rh / rw)))
        gx = x1 + (np.arange(gw, dtype=np.float32) + 0.5) * rw / gw
        gy = y1 + (np.arange(gh, dtype=np.float32) + 0.5) * rh / gh
        m = mask_at(gx[None, :], gy[:, None], r, rcfg["feather"]).astype(np.float32)
        m = cv2.resize(m, (rw, rh), interpolation=cv2.INTER_LINEAR)
        out[y1:y2, x1:x2] = cv2.blendLinear(blurred, crop, m, 1.0 - m)
    return out


def draw_debug(img, logic_dets, regions, hands=()):
    for d in logic_dets:
        x1, y1, x2, y2 = [int(v) for v in d["box"]]
        col = (0, 200, 0) if d["visible"] else (0, 0, 255)
        cv2.rectangle(img, (x1, y1), (x2, y2), col, 2)
        label = f"#{d['tid']} {'VIS' if d['visible'] else 'BLUR'} s={d.get('sim', 0):.2f}"
        for kind, p in d.get("hold", {}).items():
            label += f" {kind[:3]}:{int(p * 100)}%"
        cv2.putText(img, label, (x1, max(12, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1, cv2.LINE_AA)
    for r in regions:
        x1, y1, x2, y2 = [int(v) for v in r["box"]]
        cv2.rectangle(img, (x1, y1), (x2, y2), (255, 200, 0), 1)
    for h in hands:
        x, y = [int(v) for v in h["center"]]
        cv2.circle(img, (x, y), 5, (255, 0, 255), -1)
        cv2.putText(img, h["gesture"], (x + 6, y), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 0, 255), 1)
    return img
