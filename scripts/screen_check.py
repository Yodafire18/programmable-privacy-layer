"""Feasibility check for screen blurring: webcam -> YOLOv8n screen detector on EVERY frame -> labeled boxes.

Hold a phone / laptop / monitor up at 1-3 m, tilt it, turn it off and on, and watch how often the box drops out.
The HUD shows ms/frame and the share of recent frames with at least one screen detected.

  .venv/bin/python scripts/screen_check.py [--cam 0] [--imgsz 640] [--conf 0.25] [--threads 2] [--blur]
Keys: q quit · b toggle the rectangular blur preview
"""
import argparse
import collections
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from privacy_filter import render as R                       # noqa: E402
from privacy_filter.objects import ScreenDetector, ocfg      # noqa: E402

COL = {"phone": (0, 200, 255), "laptop": (255, 160, 0), "screen": (0, 220, 0)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cam", type=int, default=0)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--blur", action="store_true", help="also show the blur the filter would apply")
    a = ap.parse_args()
    det = ScreenDetector(imgsz=a.imgsz, conf=a.conf, threads=a.threads)
    cap = cv2.VideoCapture(a.cam)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    if not cap.isOpened():
        sys.exit(f"could not open camera {a.cam}")
    o = ocfg({})
    rcfg = {"pixel_cells": 10, "feather": 0.25}
    ms_hist, seen = collections.deque(maxlen=30), collections.deque(maxlen=90)
    blur = a.blur
    while True:
        ok, img = cap.read()
        if not ok:
            break
        t0 = time.perf_counter()
        dets = det(img)
        ms_hist.append((time.perf_counter() - t0) * 1000)
        seen.append(bool(dets))
        if blur:
            regs = []
            for d in dets:
                x1, y1, x2, y2 = d["box"]
                cx, cy, w, h = (x1 + x2) / 2, (y1 + y2) / 2, (x2 - x1) * o["expand"], (y2 - y1) * o["expand"]
                regs.append({"box": [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], "blur": 1.0,
                             "shape": "rect", "feather": o["feather"]})
            img = R.render(img, regs, rcfg)
        for d in dets:
            x1, y1, x2, y2 = [int(v) for v in d["box"]]
            c = COL.get(d["label"], (255, 255, 255))
            cv2.rectangle(img, (x1, y1), (x2, y2), c, 2)
            cv2.putText(img, f"{d['label']} {d['score']:.2f}  {x2 - x1}x{y2 - y1}px", (x1, max(18, y1 - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, c, 2, cv2.LINE_AA)
        hud = (f"{sum(ms_hist) / len(ms_hist):4.0f} ms/frame  imgsz {a.imgsz}  conf>={a.conf}  "
               f"screen seen in {100 * sum(seen) / len(seen):3.0f}% of last {len(seen)} frames  "
               f"[b] blur {'on' if blur else 'off'}  [q] quit")
        cv2.rectangle(img, (0, 0), (img.shape[1], 34), (0, 0, 0), -1)
        cv2.putText(img, hud, (10, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.imshow("screen check (YOLOv8n)", img)
        k = cv2.waitKey(1) & 0xFF
        if k == ord("q"):
            break
        if k == ord("b"):
            blur = not blur
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
