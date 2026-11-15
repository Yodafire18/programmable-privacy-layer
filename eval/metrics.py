"""Metrics on the output (blur regions) vs. teacher ground truth + labels.json.

labels.json:
  {"events": [{"t": 4.2, "person": "ani", "type": "consent"}, {"t": 9.0, "person": "ani", "type": "revoke"}],
   "favorites": [],
   "tags": ["smoke"], "notes": "..."}
"""
import numpy as np

from privacy_filter.render import coverage
from privacy_filter.tracker import iou

GRACE_S = 1.5        # after a consent/revoke event, either state is accepted for this long
BLUR_OK = 0.9        # coverage needed to count a face as blurred
VIS_OK = 0.1         # coverage below this counts as visible


def expected(person, t, labels, mode):
    """Returns True (should be visible), False (should be blurred) or None (don't care)."""
    if person in labels.get("favorites", []):
        return True
    evs = sorted([e for e in labels.get("events", []) if e["person"] == person], key=lambda e: e["t"])
    if mode == "recording":
        return bool(evs) and evs[-1]["type"] == "consent"
    state = False
    for e in evs:
        if e["t"] <= t < e["t"] + GRACE_S:
            return None
        if e["t"] <= t:
            state = e["type"] == "consent"
    return state


def evaluate(res, gt, labels, cfg):
    fps = res["info"]["fps"]
    feather = cfg["render"]["feather"]
    mode = cfg["mode"]
    leaks, overblur, rows = [], [], []
    vis_state = {}
    flips = {}
    first_visible = {}
    for i, faces in enumerate(gt):
        t = i / fps
        regs = res["regions"][i] if i < len(res["regions"]) else []
        for g in faces:
            exp = expected(g["person"], t, labels, mode)
            cov = coverage(g["box"], regs, feather)
            blurred = cov >= BLUR_OK
            visible = cov < VIS_OK
            st = "B" if blurred else ("V" if visible else "P")
            prev = vis_state.get(g["tid"])
            if prev is not None and prev != st:
                flips[g["tid"]] = flips.get(g["tid"], 0) + 1
            vis_state[g["tid"]] = st
            if visible and g["person"] not in first_visible:
                first_visible[g["person"]] = t
            if exp is False and not blurred:
                # attribute cause: did our logic mark an overlapping track visible?
                cause = "missed_detection"
                for d in res["logic"][i]:
                    if iou(d["box"], g["box"]) > 0.3:
                        cause = "false_visible" if d["visible"] else "weak_cover"
                        break
                leaks.append({"frame": i, "t": t, "person": g["person"], "cov": cov, "cause": cause,
                              "box": g["box"], "interp": g["interp"]})
            elif exp is True and not visible:
                overblur.append({"frame": i, "t": t, "person": g["person"], "cov": cov, "box": g["box"]})

    # consent latency
    lat = []
    for e in labels.get("events", []):
        if e["type"] != "consent":
            continue
        for i in range(int(e["t"] * fps), len(gt)):
            hit = [g for g in gt[i] if g["person"] == e["person"]]
            if hit and all(coverage(g["box"], res["regions"][i], feather) < VIS_OK for g in hit):
                lat.append(i / fps - e["t"])
                break
        else:
            lat.append(None)

    n_gt = sum(len(f) for f in gt)
    perc = res["perc"]
    fm = np.array([p["ms"]["faces"] for p in perc]) if perc else np.zeros(1)
    hm = np.array([p["ms"]["hands"] for p in perc]) if perc else np.zeros(1)
    duration = len(gt) / fps
    leak_frames = sorted({l["frame"] for l in leaks})
    return {
        "frames": len(gt),
        "duration_s": round(duration, 2),
        "gt_faces": n_gt,
        "leak_frames": len(leak_frames),
        "leak_by_cause": {c: sum(1 for l in leaks if l["cause"] == c) for c in
                          ("false_visible", "missed_detection", "weak_cover")},
        "false_visible_frames": len({l["frame"] for l in leaks if l["cause"] == "false_visible"}),
        "overblur_frames": len({o["frame"] for o in overblur}),
        "consent_latency_s": [None if x is None else round(x, 2) for x in lat],
        "flicker_per_min": round(sum(flips.values()) / max(duration / 60, 1e-9), 2),
        "ms_faces_p50": round(float(np.median(fm)), 1), "ms_faces_p95": round(float(np.percentile(fm, 95)), 1),
        "ms_hands_p50": round(float(np.median(hm)), 1),
        "ms_logic_per_frame": round(res["ms_logic_total"] / max(len(gt), 1), 2),
        "consent_events_detected": [(round(t, 2), k, tid) for t, k, tid in res["events"]],
        "_leaks": leaks,
        "_overblur": overblur,
    }


def runs_of(items, gap=3):
    """Group per-frame failures into contiguous incidents, per person."""
    out = []
    by = {}
    for it in items:
        by.setdefault(it["person"], []).append(it)
    for seq in by.values():
        seq.sort(key=lambda x: x["frame"])
        cur = []
        for it in seq:
            if cur and it["frame"] - cur[-1]["frame"] > gap:
                out.append(cur)
                cur = []
            cur.append(it)
        if cur:
            out.append(cur)
    return out
