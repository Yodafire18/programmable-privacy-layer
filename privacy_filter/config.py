"""Config: nested dict defaults, optional YAML overlay, dotted CLI overrides (a.b=1)."""
import copy
import hashlib
import json

import yaml

DEFAULTS = {
    "perception": {
        "face_model": "buffalo_s", # buffalo_s: ~12ms detect + ~7ms/face embed on M1 CPU. buffalo_l: ~10x slower
        "det_size": 640,          # SCRFD input size
        "det_thresh": 0.5,
        "providers": ["CPUExecutionProvider"],
        "num_hands": 6,
        "hand_min_conf": 0.5,
        "every_n": 1,             # run perception every n-th frame (live speed knob)
    },
    "tracker": {
        "iou_match": 0.25,
        "emb_match": 0.45,        # cosine sim to rescue a match when IoU is low (fast motion)
        "max_misses": 15,         # frames a track survives without detections
    },
    "consent": {
        "hold_s": 0.75,           # gesture must be held this long (video)
        "match_thresh": 0.5,      # memory match to become visible
        "drop_thresh": 0.3,       # per-frame sim below this -> blur instantly (identity switch guard)
        "grace_s": 0.5,           # ...except a consented track may ride out a dip this long (head tilt/turn)
        "grace_iou": 0.5,         #    if its box is continuous (IoU with previous frame) and no face overlaps it
        "grace_floor": 0.2,       #    and its per-frame sim stays >= this (strangers / missing embeddings ~0)
        "win_x": 2.5,             # hand window: +-win_x face widths
        "win_up": 1.0,            # face heights above top
        "win_down": 3.0,          # face heights below bottom
        "ambiguity": 1.25,        # 2nd-nearest face must be this much farther, else fail closed
        "gesture_min_score": 0.5,
    },
    "timeline": {
        "buffer_s": 3.0,          # lookahead available (0 = live preview, causal)
        "hold_miss_s": 0.5,       # keep blurring a lost blurred track this long (fail closed)
        "backfill_s": 0.5,        # blur a new face this long before its first detection (needs buffer)
        "smooth_frames": 2,       # union-of-boxes window radius
        "grow_per_s": 1.0,        # held boxes grow by this fraction per second
        "fade_s": 0.4,            # consent fade-out of blur
    },
    "render": {
        "style": "soft",
        "expand": 1.4,
        "pixel_cells": 10,        # blur resolution across the face (lower = stronger)
        "feather": 0.25,          # fraction of mask radius that is feathered
    },
    "mode": "live",               # live: consent applies from when it happens. recording: final memory applies to whole clip
}

# keys that affect perception output (cache key)
PERCEPTION_KEYS = ("perception",)


def deep_update(d, u):
    for k, v in u.items():
        if isinstance(v, dict) and isinstance(d.get(k), dict):
            deep_update(d[k], v)
        else:
            d[k] = v
    return d


def parse_value(s):
    try:
        return yaml.safe_load(s)
    except Exception:
        return s


def load(path=None, overrides=()):
    cfg = copy.deepcopy(DEFAULTS)
    if path:
        with open(path) as f:
            deep_update(cfg, yaml.safe_load(f) or {})
    for o in overrides:
        k, v = o.split("=", 1)
        node = cfg
        parts = k.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = parse_value(v)
    return cfg


def stable_hash(obj, n=10):
    return hashlib.sha1(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:n]
