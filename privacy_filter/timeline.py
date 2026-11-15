"""Turns per-frame track states into per-frame blur regions, using up to `buffer_s` of lookahead.

Input  `logic[i]` = list of {"tid", "box", "visible"} for frame i (detections only).
Output `regions[i]` = list of {"tid", "box", "blur"} (blur 0..1 strength, box already expanded).

Rules (fail closed):
 - gaps between detections are interpolated if the buffer can see the far side, else held and grown
 - a new face is blurred `backfill_s` before its first detection (limited by the buffer)
 - a lost face keeps its blur for `hold_miss_s`
 - box = union of neighbouring boxes (never shrinks below what was seen nearby)
 - consent fades blur out over `fade_s`; blur in is instant
"""
import numpy as np


def _grow(box, f):
    x1, y1, x2, y2 = box
    cx, cy, w, h = (x1 + x2) / 2, (y1 + y2) / 2, (x2 - x1) * (1 + f), (y2 - y1) * (1 + f)
    return [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]


def _extend_edges(box, W, H, margin=4, f=0.6):
    """Detectors clip boxes at the frame border, so a half-visible face gets a box centred too far
    inside and the oval blur misses the part at the edge. Push such sides outward."""
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    if x1 <= margin:
        x1 -= f * w
    if y1 <= margin:
        y1 -= f * h
    if x2 >= W - margin:
        x2 += f * w
    if y2 >= H - margin:
        y2 += f * h
    return [x1, y1, x2, y2]


def build(logic, fps, cfg_t, cfg_r, size=None, only=None):
    """Regions for every frame, or (only=i) the same result for frame i alone (live: one frame per call,
    frames after i don't affect it except through the interpolation that is computed anyway)."""
    n = len(logic)
    L = int(round(cfg_t["buffer_s"] * fps))
    hold = int(round(cfg_t["hold_miss_s"] * fps))
    back = min(int(round(cfg_t["backfill_s"] * fps)), L)
    k = min(cfg_t["smooth_frames"], L)
    grow = cfg_t["grow_per_s"] / fps
    fade = max(1, int(round(cfg_t["fade_s"] * fps)))

    # per-track detections
    tracks = {}
    for i, dets in enumerate(logic):
        for d in dets:
            tracks.setdefault(d["tid"], []).append((i, d["box"], d["visible"]))

    raw = [dict() for _ in range(n)]   # frame -> tid -> (box, visible)
    for tid, seq in tracks.items():
        for j, (i, box, vis) in enumerate(seq):
            raw[i][tid] = (box, vis)
            if j + 1 < len(seq):
                i2, box2, vis2 = seq[j + 1]
                gap = i2 - i - 1
                if gap <= 0:
                    continue
                if gap <= L:   # can see the other side: interpolate
                    for g in range(1, gap + 1):
                        a = g / (gap + 1)
                        b = [p * (1 - a) + q * a for p, q in zip(box, box2)]
                        raw[i + g][tid] = (_grow(b, 0.1), vis and vis2)
                else:          # causal: hold & grow, then drop
                    for g in range(1, min(gap, hold) + 1):
                        raw[i + g][tid] = (_grow(box, grow * g), False if not vis else vis)
        i0, box0, _ = seq[0]
        for g in range(1, back + 1):
            if i0 - g >= 0:
                raw[i0 - g].setdefault(tid, (_grow(box0, grow * g), False))
        iN, boxN, visN = seq[-1]
        for g in range(1, hold + 1):
            if iN + g < n:
                raw[iN + g].setdefault(tid, (_grow(boxN, grow * g), visN))

    # blur strength per track over time: instant on, fade off
    regions = [[] for _ in range(n)]
    strength = {}
    for i in range(n if only is None else only + 1):
        for tid, (box, vis) in raw[i].items():
            s = strength.get(tid, 1.0)
            s = max(0.0, s - 1.0 / fade) if vis else 1.0
            strength[tid] = s
            if only is not None and i != only:
                continue
            # smooth: union of this track's boxes within +-k frames
            x1, y1, x2, y2 = box
            for j in range(max(0, i - k), min(n, i + k + 1)):
                if tid in raw[j]:
                    b = raw[j][tid][0]
                    x1, y1, x2, y2 = min(x1, b[0]), min(y1, b[1]), max(x2, b[2]), max(y2, b[3])
            if s > 0:
                b = [x1, y1, x2, y2]
                if size:
                    b = _extend_edges(b, *size)
                regions[i].append({"tid": tid, "box": _grow(b, cfg_r["expand"] - 1), "blur": s})
    return regions
