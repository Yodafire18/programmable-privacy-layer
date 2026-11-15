"""Eval CLI.

  python -m eval run   [--config f.yaml] [--set a.b=v ...] [--clips smoke|id1,id2] [--name x] [--vs baseline] [--video]
  python -m eval check [--name x]           # exit 1 if a gate fails
  python -m eval baseline --name x          # mark a run as the baseline
  python -m eval sweep --param consent.match_thresh=0.4,0.5,0.6 [--clips ...]
  python -m eval enroll NAME DIR
  python -m eval synth                      # make synthetic smoke clips (no camera needed)
  python -m eval clips                      # list corpus
"""
import argparse
import json
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import cv2
import numpy as np

from privacy_filter import config as C
from privacy_filter import render as R
from privacy_filter.process import CORPUS, ROOT, frame_at, load_labels, process, write_video

from . import metrics as M
from . import teacher as T

RUNS = ROOT / "runs"
GATES = {"leak_frames": 0, "false_visible_frames": 0}


def list_clips(sel=None):
    clips = []
    for d in sorted(CORPUS.iterdir()) if CORPUS.exists() else []:
        if not (d / "raw.mp4").exists():
            continue
        lab = load_labels(d.name)
        if sel:
            want = set(sel.split(","))
            if d.name not in want and not (want & set(lab.get("tags", []))):
                continue
        clips.append(d.name)
    return clips


def warm_caches(clips, cfg, workers=2):
    """Run slow model stages (perception + teacher) in parallel; no-op when cached."""
    from privacy_filter.process import CACHE
    key = C.stable_hash({k: cfg[k] for k in C.PERCEPTION_KEYS})
    todo = [c for c in clips if not (CACHE / c / f"perception-{key}.pkl").exists()
            or not list((CACHE / c).glob("teacher-*.pkl"))]
    if not todo:
        return 0
    print(f"  running models on {len(todo)} clip(s) (cached afterwards)...", flush=True)
    if len(todo) == 1 or workers == 1:
        for c in todo:
            print(T.warm((c, cfg)), end="")
    else:
        with ProcessPoolExecutor(workers) as ex:
            for out in ex.map(T.warm, [(c, cfg) for c in todo]):
                print(out, end="")
    return len(todo)


def save_still(clip, res, inc, cfg, path):
    worst = min(inc, key=lambda x: x["cov"])
    i = worst["frame"]
    img = frame_at(clip, i)
    if img is None:
        return
    out = R.render(img, res["regions"][i], cfg["render"])
    dbg = R.draw_debug(img.copy(), res["logic"][i], res["regions"][i], res["perc"][i]["hands"])
    for im in (dbg, out):
        x1, y1, x2, y2 = [int(v) for v in worst["box"]]
        cv2.rectangle(im, (x1, y1), (x2, y2), (0, 255, 255), 2)
    cv2.putText(dbg, f"{clip} f{i} t={i / res['info']['fps']:.2f}s {worst['person']} cov={worst['cov']:.2f}",
                (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    both = np.hstack([dbg, out])
    s = 1600 / both.shape[1]
    cv2.imwrite(str(path), cv2.resize(both, None, fx=s, fy=s), [cv2.IMWRITE_JPEG_QUALITY, 85])


def fmt_delta(cur, base):
    if base is None:
        return ""
    d = cur - base
    return " (=)" if d == 0 else f" ({'+' if d > 0 else ''}{d:g})"


def cmd_run(a, cfg=None, quiet=False):
    t0 = time.perf_counter()
    cfg = cfg or C.load(a.config, a.set or [])
    clips = list_clips(a.clips)
    if not clips:
        sys.exit("no clips in corpus/ (try: python -m eval synth)")
    name = a.name or time.strftime("r%m%d-%H%M%S")
    rd = RUNS / name
    if rd.exists():
        shutil.rmtree(rd)
    (rd / "fails").mkdir(parents=True)
    n_model = warm_caches(clips, cfg, a.workers)

    per_clip, incidents = {}, []
    for c in clips:
        res = process(c, cfg, log=lambda *x: None)
        gt = T.teacher(c)
        m = M.evaluate(res, gt, load_labels(c), cfg)
        for kind, items in (("leak", m.pop("_leaks")), ("overblur", m.pop("_overblur"))):
            for inc in M.runs_of(items):
                incidents.append((kind, c, inc, res))
        per_clip[c] = m
        if a.video:
            write_video(c, res, cfg, rd / f"{c}.mp4")

    # stills for worst incidents (leaks first, then longest)
    incidents.sort(key=lambda x: (x[0] != "leak", -len(x[2])))
    inc_rows = []
    for k, (kind, c, inc, res) in enumerate(incidents[:12]):
        p = rd / "fails" / f"{kind}_{c}_f{inc[0]['frame']}.jpg"
        save_still(c, res, inc, cfg, p)
        causes = sorted({x.get("cause", "") for x in inc} - {""})
        inc_rows.append(f"  {kind.upper():8} {c} f{inc[0]['frame']}-{inc[-1]['frame']} "
                        f"({(inc[-1]['frame'] - inc[0]['frame'] + 1) / res['info']['fps']:.2f}s) {inc[0]['person']} "
                        f"min_cov={min(x['cov'] for x in inc):.2f} {','.join(causes)} -> {p.relative_to(ROOT)}")
    if len(incidents) > 12:
        inc_rows.append(f"  ... {len(incidents) - 12} more incidents")

    tot = {k: sum(m[k] for m in per_clip.values()) for k in ("leak_frames", "false_visible_frames", "overblur_frames")}
    tot["flicker_per_min"] = round(float(np.mean([m["flicker_per_min"] for m in per_clip.values()])), 2)
    lats = [x for m in per_clip.values() for x in m["consent_latency_s"]]
    tot["missed_consents"] = sum(1 for x in lats if x is None)
    good = [x for x in lats if x is not None]
    tot["consent_latency_mean_s"] = round(float(np.mean(good)), 2) if good else None
    tot["ms_faces_p50"] = round(float(np.median([m["ms_faces_p50"] for m in per_clip.values()])), 1)
    gates = {k: tot[k] <= v for k, v in GATES.items()}
    wall = time.perf_counter() - t0
    out = {"name": name, "config": cfg, "totals": tot, "gates": gates, "clips": per_clip,
           "wall_s": round(wall, 1), "model_runs": n_model}
    (rd / "metrics.json").write_text(json.dumps(out, indent=1, default=str))

    base = None
    vs = a.vs or ("baseline" if (RUNS / "baseline").exists() else None)
    if vs and (RUNS / vs / "metrics.json").exists() and vs != name:
        base = json.loads((RUNS / vs / "metrics.json").read_text())

    L = [f"RUN {name}  mode={cfg['mode']}  clips={len(clips)}  wall={wall:.1f}s  "
         f"(models ran on {n_model}, rest cached){'  vs ' + vs if base else ''}"]
    L.append("GATES " + "  ".join(f"{k}={tot[k]} {'PASS' if ok else 'FAIL'}" for k, ok in gates.items()))
    L.append(f"{'clip':28} {'dur':>6} {'leak':>5} {'fvis':>5} {'over':>5} {'flick/m':>8} {'latency':>12} {'face_ms':>8}")
    for c, m in per_clip.items():
        bm = base["clips"].get(c) if base else None
        lat = ",".join("miss" if x is None else f"{x}" for x in m["consent_latency_s"]) or "-"
        L.append(f"{c[:28]:28} {m['duration_s']:>5}s {m['leak_frames']:>5}{fmt_delta(m['leak_frames'], bm and bm['leak_frames']):6}"
                 f"{m['false_visible_frames']:>5} {m['overblur_frames']:>5}{fmt_delta(m['overblur_frames'], bm and bm['overblur_frames']):6}"
                 f"{m['flicker_per_min']:>8} {lat:>12} {m['ms_faces_p50']:>8}")
    bt = base["totals"] if base else {}
    L.append("TOTAL " + "  ".join(f"{k}={v}{fmt_delta(v, bt.get(k)) if isinstance(v, (int, float)) and isinstance(bt.get(k), (int, float)) else ''}"
                                   for k, v in tot.items()))
    if inc_rows:
        L.append("INCIDENTS (worst first; stills = debug | output, yellow = GT face):")
        L += inc_rows
    summary = "\n".join(L)
    (rd / "summary.txt").write_text(summary + "\n")
    if not quiet:
        print(summary)
    return out


def cmd_check(a):
    rd = RUNS / (a.name or sorted(RUNS.iterdir(), key=lambda p: p.stat().st_mtime)[-1].name)
    m = json.loads((rd / "metrics.json").read_text())
    ok = all(m["gates"].values())
    print(f"{rd.name}: {'PASS' if ok else 'FAIL'} {m['gates']}")
    sys.exit(0 if ok else 1)


def cmd_baseline(a):
    src = RUNS / a.name
    dst = RUNS / "baseline"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    print(f"baseline <- {a.name}")


def cmd_sweep(a):
    key, vals = a.param.split("=", 1)
    print(f"{key:>32} {'leak':>6} {'fvis':>6} {'over':>6} {'flick':>6} {'lat':>6} {'miss':>5}")
    for v in vals.split(","):
        cfg = C.load(a.config, (a.set or []) + [f"{key}={v}"])
        a.name = f"sweep_{key}_{v}"
        out = cmd_run(a, cfg, quiet=True)
        t = out["totals"]
        print(f"{v:>32} {t['leak_frames']:>6} {t['false_visible_frames']:>6} {t['overblur_frames']:>6} "
              f"{t['flicker_per_min']:>6} {str(t['consent_latency_mean_s']):>6} {t['missed_consents']:>5}")


def cmd_synth(a):
    """Synthetic smoke clips from InsightFace's sample group photo: pans, zooms, a fast whip, dropouts."""
    import insightface
    src = cv2.imread(str(Path(insightface.__file__).parent / "data" / "images" / "t1.jpg"))
    H, W = 720, 1280
    fps = 30

    def make(cid, n, cam, tags, note, src=src):
        if getattr(a, "only", None) and a.only not in cid:
            return
        d = CORPUS / cid
        d.mkdir(parents=True, exist_ok=True)
        vw = cv2.VideoWriter(str(d / "raw.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
        canvas = np.zeros((H * 2, W * 2, 3), np.uint8) + 40
        sh, sw = src.shape[:2]
        s = min(H * 2 / sh, W * 2 / sw) * 0.9
        im = cv2.resize(src, None, fx=s, fy=s)
        y0, x0 = (H * 2 - im.shape[0]) // 2, (W * 2 - im.shape[1]) // 2
        canvas[y0:y0 + im.shape[0], x0:x0 + im.shape[1]] = im
        for i in range(n):
            cx, cy, z, blur = cam(i / fps)
            ww, hh = W * 2 / z, H * 2 / z
            M_ = np.float32([[W / ww, 0, -(cx * W * 2 - ww / 2) * W / ww], [0, H / hh, -(cy * H * 2 - hh / 2) * H / hh]])
            fr = cv2.warpAffine(canvas, M_, (W, H), borderValue=(40, 40, 40))
            if blur > 1:
                k = np.zeros((1, int(blur)), np.float32) + 1 / int(blur)
                fr = cv2.filter2D(fr, -1, k)
            vw.write(fr)
        vw.release()
        (d / "labels.json").write_text(json.dumps({"events": [], "favorites": [], "tags": tags, "notes": note,
                                                   "synthetic": True}, indent=1))
        print(f"  wrote corpus/{cid}")

    make("synth_slow_pan", 240, lambda t: (0.5 + 0.12 * np.sin(t), 0.5, 1.6, 0), ["smoke", "synth"],
         "group photo, slow pan; everyone should stay blurred")
    make("synth_zoom", 240, lambda t: (0.5, 0.5, 1.0 + 1.5 * (0.5 - 0.5 * np.cos(t * 0.8)), 0), ["smoke", "synth"],
         "zoom from far (small faces) to close")
    make("synth_whip", 180, lambda t: (0.5 + (0.25 if 2.0 < t < 2.4 else 0) * np.sin((t - 2) * 7.8), 0.5, 1.8,
                                       40 if 2.0 < t < 2.4 else 0), ["smoke", "synth"],
         "fast head whip with motion blur at 2.0-2.4s")
    # crowded hall: the group photo tiled 3x3 (~50 small faces), slow pan
    crowd = np.vstack([np.hstack([src] * 3)] * 3)
    make("synth_crowd", 240, lambda t: (0.5 + 0.08 * np.sin(t * 0.8), 0.5, 1.3, 0), ["smoke", "synth", "crowd"],
         "crowded hall: ~50 small faces, slow pan; everyone should stay blurred", src=crowd)


def main():
    ap = argparse.ArgumentParser(prog="python -m eval")
    sp = ap.add_subparsers(dest="cmd", required=True)
    for n in ("run", "sweep"):
        p = sp.add_parser(n)
        p.add_argument("--config")
        p.add_argument("--set", nargs="*")
        p.add_argument("--clips")
        p.add_argument("--name")
        p.add_argument("--vs")
        p.add_argument("--video", action="store_true")
        p.add_argument("--workers", type=int, default=2)
        if n == "sweep":
            p.add_argument("--param", required=True)
    p = sp.add_parser("check"); p.add_argument("--name")
    p = sp.add_parser("baseline"); p.add_argument("--name", required=True)
    p = sp.add_parser("enroll"); p.add_argument("person"); p.add_argument("dir")
    p = sp.add_parser("synth"); p.add_argument("--only")
    sp.add_parser("clips")
    a = ap.parse_args()
    if a.cmd == "run":
        cmd_run(a)
    elif a.cmd == "sweep":
        cmd_sweep(a)
    elif a.cmd == "check":
        cmd_check(a)
    elif a.cmd == "baseline":
        cmd_baseline(a)
    elif a.cmd == "enroll":
        T.enroll(a.person, a.dir)
    elif a.cmd == "synth":
        cmd_synth(a)
    elif a.cmd == "clips":
        for c in list_clips():
            lab = load_labels(c)
            print(f"{c:30} tags={lab.get('tags', [])} events={len(lab.get('events', []))}  {lab.get('notes', '')}")


if __name__ == "__main__":
    main()
