"""Offline / replay pipeline with stage caching.

  perceive (slow, cached by perception config)  ->  logic (tracker+consent, fast)
  -> timeline (buffer/smoothing, fast)  ->  render (only when video output is wanted)
"""
import json
import pickle
import time
from pathlib import Path

import cv2
import numpy as np

from . import config as C
from . import render as R
from . import timeline
from .consent import ConsentEngine
from .sources import file_frames, video_info
from .tracker import Tracker

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"
CACHE = ROOT / "cache"


def clip_path(clip_id):
    return CORPUS / clip_id / "raw.mp4"


def perceive(clip_id, cfg, log=print):
    key = C.stable_hash({k: cfg[k] for k in C.PERCEPTION_KEYS})
    cp = CACHE / clip_id / f"perception-{key}.pkl"
    if cp.exists():
        with open(cp, "rb") as f:
            return pickle.load(f)
    from .perception import Perception
    P = Perception(cfg["perception"], video=True)
    frames = []
    t0 = time.perf_counter()
    for i, (t, img) in enumerate(file_frames(clip_path(clip_id))):
        frames.append(P(img, t))
    P.close()
    log(f"  perceived {clip_id}: {len(frames)} frames in {time.perf_counter() - t0:.1f}s")
    cp.parent.mkdir(parents=True, exist_ok=True)
    with open(cp, "wb") as f:
        pickle.dump(frames, f)
    return frames


def run_logic(perc, cfg):
    tracker = Tracker(cfg["tracker"])
    consent = ConsentEngine(cfg["consent"], video=True)
    out = []
    tids_per_frame = []
    for fr in perc:
        faces = fr["faces"]
        tids = tracker.update(faces)
        states = consent.step(fr["t"], faces, tids, fr["hands"], tracker)
        dets = []
        for f, tid in zip(faces, tids):
            st = states[tid]
            dets.append({"tid": tid, "box": f["box"], "visible": st["visible"], "sim": st["sim"], "hold": st["hold"]})
        out.append(dets)
        tids_per_frame.append(tids)

    if cfg["mode"] == "recording":
        # final consent memory applies to the whole clip
        mem = [e for _, e in consent.memory]
        for fr, dets in zip(perc, out):
            for f, d in zip(fr["faces"], dets):
                sim = max((float(f["emb"].astype(np.float32) @ e) for e in mem), default=0.0)
                d["visible"] = sim >= cfg["consent"]["match_thresh"]
                d["sim"] = sim
    return out, consent.events


def process(clip_id, cfg, log=print):
    info = video_info(clip_path(clip_id))
    perc = perceive(clip_id, cfg, log)
    t0 = time.perf_counter()
    logic, events = run_logic(perc, cfg)
    regions = timeline.build(logic, info["fps"], cfg["timeline"], cfg["render"], (info["width"], info["height"]))
    ms_logic = (time.perf_counter() - t0) * 1000
    return {"clip": clip_id, "info": info, "perc": perc, "logic": logic, "events": events,
            "regions": regions, "ms_logic_total": ms_logic}


def write_video(clip_id, res, cfg, out_path, debug=True):
    """Side-by-side: raw+debug | filtered output."""
    info = res["info"]
    w, h = info["width"], info["height"]
    out_w = w * 2 if debug else w
    vw = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), info["fps"], (out_w, h))
    for i, (t, img) in enumerate(file_frames(clip_path(clip_id))):
        if i >= len(res["regions"]):
            break
        filt = R.render(img, res["regions"][i], cfg["render"])
        if debug:
            dbg = R.draw_debug(img.copy(), res["logic"][i], res["regions"][i], res["perc"][i]["hands"])
            cv2.putText(dbg, f"{t:6.2f}s f{i}", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            filt = np.hstack([dbg, filt])
        vw.write(filt)
    vw.release()


def frame_at(clip_id, idx):
    cap = cv2.VideoCapture(str(clip_path(clip_id)))
    cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
    ok, img = cap.read()
    cap.release()
    return img if ok else None


def load_labels(clip_id):
    p = CORPUS / clip_id / "labels.json"
    return json.loads(p.read_text()) if p.exists() else {}
