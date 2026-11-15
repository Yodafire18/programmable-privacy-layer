"""Score the LIVE app (threads, separate detection process, frame skipping, real-time pacing)
on the synthetic corpus with the same ground truth + metrics as the offline eval.

  python -m eval.live_eval [--delay 0.75] [--clips a,b] [--set k=v ...]
"""
import argparse

from privacy_filter import config as C
from privacy_filter.live import Live
from privacy_filter.process import clip_path, load_labels
from privacy_filter.sources import video_info

from . import metrics as M
from . import teacher as T
from .__main__ import list_clips


def run_clip(clip, cfg):
    info = video_info(clip_path(clip))
    live = Live(cfg, str(clip_path(clip)), realtime_file=True)
    live.trace = {}
    live.run(headless=True, debug=False)
    W, H = info["width"], info["height"]
    full = [{"tid": -1, "box": [-W, -H, 2 * W, 2 * H], "blur": 1.0}]   # skipped frame = never shown
    n = info["frames"]
    regions = [live.trace[i][0] if i in live.trace else full for i in range(n)]
    logic = [live.trace[i][1] if i in live.trace else [] for i in range(n)]
    res = {"info": info, "regions": regions, "logic": logic, "perc": [], "events": live.events,
           "ms_logic_total": 0.0}
    m = M.evaluate(res, T.teacher(clip, log=lambda *x: None), load_labels(clip), cfg)
    m["skipped"] = n - len(live.trace)
    m["_runs"] = [f"f{r[0]['frame']}-{r[-1]['frame']} {r[0]['person']} {r[0]['cause']} cov={min(x['cov'] for x in r):.2f}"
                  for r in M.runs_of(m["_leaks"])]
    return m


def main():
    ap = argparse.ArgumentParser(prog="python -m eval.live_eval")
    ap.add_argument("--delay", type=float, default=0.75)
    ap.add_argument("--clips")
    ap.add_argument("--set", nargs="*", default=[])
    ap.add_argument("-v", "--verbose", action="store_true", help="list each leak")
    a = ap.parse_args()
    cfg = C.load(None, a.set)
    cfg["timeline"]["buffer_s"] = a.delay
    cfg["perception"].setdefault("hand_max_w", 640)
    cfg["perception"].setdefault("ort_threads", 3)
    cfg["perception"].setdefault("parallel", True)
    cfg["timeline"]["hold_miss_s"] = max(cfg["timeline"]["hold_miss_s"], 1.0)
    if not any(o.startswith("perception.det_thresh=") for o in a.set):
        cfg["perception"]["det_thresh"] = 0.35          # catch half-faces at frame edges (fail closed)
    rows, tot = [], {"leak": 0, "fvis": 0, "over": 0}
    for c in list_clips(a.clips):
        m = run_clip(c, cfg)
        tot["leak"] += m["leak_frames"]; tot["fvis"] += m["false_visible_frames"]; tot["over"] += m["overblur_frames"]
        rows.append(f"{c:24} leak {m['leak_frames']:>4} {m['leak_by_cause']}  fvis {m['false_visible_frames']}  "
                    f"over {m['overblur_frames']}  flicker/min {m['flicker_per_min']}  skipped {m['skipped']}")
        if a.verbose:
            rows += ["    " + r for r in m["_runs"]]
    print(f"LIVE EVAL delay={a.delay}s")
    print("\n".join(rows))
    print(f"TOTAL leak_frames={tot['leak']}  false_visible={tot['fvis']}  overblur={tot['over']}")


if __name__ == "__main__":
    main()
