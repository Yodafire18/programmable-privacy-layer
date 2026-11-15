"""Screens: detector recall on labeled clips, and the live app's screen + face coverage with screens on.

  python -m eval.screen_eval --detect [--clips synth_screens] [--imgsz 640]
        raw detector, every frame: per-kind recall (IoU > 0.3 with a GT screen), false positives, ms/frame
  python -m eval.screen_eval [--profile faces-and-screens] [--clips synth_screens,synth_slow_pan] [--delay 0.75]
        LIVE app (real-time paced) with that profile: face leaks (same metric as eval.live_eval) plus
        screen_leak_frames = frames where a labeled screen is not >= 90% covered at blur >= 0.9.

Ground-truth screens live in corpus/<clip>/labels.json under "screens" (per frame: [{"id","kind","box"}]);
make the synthetic clip with: python scripts/make_synth_screens.py
The profile is activated for the run (profiles/ACTIVE) and the previous one is restored afterwards.
"""
import argparse
import time

import numpy as np

from privacy_filter import config as C
from privacy_filter import profiles as PR
from privacy_filter.objects import ScreenDetector, _iou, ocfg, rect_coverage
from privacy_filter.process import clip_path, load_labels
from privacy_filter.sources import file_frames, video_info

from .__main__ import list_clips

SCREEN_OK = 0.9

def detect(clip, imgsz):
    gt = load_labels(clip).get("screens")
    d = ScreenDetector(imgsz=imgsz, threads=ocfg({})["threads"])
    hit, tot, fp, ms = {}, {}, 0, []
    for i, (_, img) in enumerate(file_frames(clip_path(clip))):
        t0 = time.perf_counter()
        dets = d(img)
        ms.append((time.perf_counter() - t0) * 1000)
        g = gt[i] if gt and i < len(gt) else []
        for s in g:
            tot[s["kind"]] = tot.get(s["kind"], 0) + 1
            if any(_iou(x["box"], s["box"]) > 0.3 for x in dets):
                hit[s["kind"]] = hit.get(s["kind"], 0) + 1
        fp += sum(1 for x in dets if not any(_iou(x["box"], s["box"]) > 0.3 for s in g))
    rec = "  ".join(f"{k} {hit.get(k, 0)}/{n} ({100 * hit.get(k, 0) / n:.0f}%)" for k, n in tot.items()) or "no GT screens"
    print(f"{clip:24} recall: {rec}  unmatched dets: {fp}  ms/frame p50 {np.median(ms[5:]):.0f} "
          f"p95 {np.percentile(ms[5:], 95):.0f}  (imgsz {imgsz})")

def live(clip, cfg):
    from privacy_filter.live import Live

    from . import metrics as M
    from . import teacher as T
    info = video_info(clip_path(clip))
    lv = Live(cfg, str(clip_path(clip)), realtime_file=True)
    lv.trace = {}
    lv.run(headless=True, debug=False)
    W, H = info["width"], info["height"]
    full = [{"tid": -1, "box": [-W, -H, 2 * W, 2 * H], "blur": 1.0}]
    n = info["frames"]
    regions = [lv.trace[i][0] if i in lv.trace else full for i in range(n)]
    logic = [lv.trace[i][1] if i in lv.trace else [] for i in range(n)]
    res = {"info": info, "regions": regions, "logic": logic, "perc": [], "events": lv.events, "ms_logic_total": 0.0}
    labels = load_labels(clip)
    # face leaks: score with face-only regions so screen rectangles can't hide a face leak
    face_res = dict(res, regions=[[r for r in rr if r.get("shape") != "rect"] for rr in regions])
    m = M.evaluate(face_res, T.teacher(clip, log=lambda *x: None), labels, cfg)
    out = {"face_leak": m["leak_frames"], "fvis": m["false_visible_frames"], "skipped": n - len(lv.trace),
           "screen_runs": lv.n_obj, "screen_ms": round(lv.obj_ms)}
    gt = labels.get("screens")
    if gt:
        bad = {}
        for i, screens in enumerate(gt[:n]):
            for s in screens:
                if rect_coverage(s["box"], regions[i], cfg["render"]["feather"]) < SCREEN_OK:
                    bad.setdefault(s["kind"], set()).add(i)
        out["screen_leak_frames"] = len(set().union(*bad.values())) if bad else 0
        out["screen_leak_by_kind"] = {k: len(v) for k, v in bad.items()}
        out["screen_frames"] = sum(1 for s in gt[:n] if s)
    return out

def main():
    ap = argparse.ArgumentParser(prog="python -m eval.screen_eval")
    ap.add_argument("--detect", action="store_true", help="raw detector recall, every frame")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--clips", default="synth_screens")
    ap.add_argument("--profile", default="faces-and-screens")
    ap.add_argument("--delay", type=float, default=0.75)
    ap.add_argument("--set", nargs="*", default=[])
    a = ap.parse_args()
    clips = list_clips(a.clips)
    if not clips:
        raise SystemExit("no such clip (python scripts/make_synth_screens.py makes synth_screens)")
    if a.detect:
        for c in clips:
            detect(c, a.imgsz)
        return
    # same live settings as eval.live_eval
    cfg = C.load(None, a.set)
    cfg["timeline"]["buffer_s"] = a.delay
    cfg["perception"].setdefault("hand_max_w", 640)
    cfg["perception"].setdefault("ort_threads", 3)
    cfg["perception"].setdefault("parallel", True)
    cfg["timeline"]["hold_miss_s"] = max(cfg["timeline"]["hold_miss_s"], 1.0)
    if not any(o.startswith("perception.det_thresh=") for o in a.set):
        cfg["perception"]["det_thresh"] = 0.35
    prev = PR.active_name()
    PR.activate(a.profile)
    try:
        print(f"SCREEN EVAL profile={a.profile} delay={a.delay}s")
        for c in clips:
            print(f"{c:24} {live(c, cfg)}", flush=True)
    finally:
        if prev:
            PR.activate(prev)
        else:
            PR.ACTIVE.unlink(missing_ok=True)

if __name__ == "__main__":
    main()
