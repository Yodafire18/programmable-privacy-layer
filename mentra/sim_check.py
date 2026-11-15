"""Per-frame face similarity on a raw clip, to tune consent.grace_floor / drop_thresh.

Uses the same model/settings as the live app (buffalo_s, det 640, thresh 0.35). The reference
embedding is the mean of the first N frontal frames (like the snapshot taken at consent).
Prints per-second min/median/max similarity of the largest face, and frames with no face.

  .venv/bin/python mentra/sim_check.py clip.mp4 [--ref-s 2]
"""
import argparse
import cv2
import numpy as np
from insightface.app import FaceAnalysis

ap = argparse.ArgumentParser()
ap.add_argument("clip")
ap.add_argument("--ref-s", type=float, default=2.0, help="seconds at the start used as the reference")
a = ap.parse_args()

app = FaceAnalysis(name="buffalo_s", allowed_modules=["detection", "recognition"],
                   providers=["CPUExecutionProvider"])
app.prepare(ctx_id=-1, det_size=(640, 640), det_thresh=0.35)

cap = cv2.VideoCapture(a.clip)
fps = cap.get(cv2.CAP_PROP_FPS) or 30
rows = []  # (t, sim or None)
ref, refs = None, []
i = 0
while True:
    ok, img = cap.read()
    if not ok:
        break
    t = i / fps
    i += 1
    faces = app.get(img)
    if not faces:
        rows.append((t, None))
        continue
    f = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
    e = f.normed_embedding.astype(np.float32)
    if t < a.ref_s:
        refs.append(e)
        rows.append((t, 1.0))
        continue
    if ref is None:
        ref = np.mean(refs, axis=0)
        ref /= np.linalg.norm(ref)
    rows.append((t, float(e @ ref)))

print(f"{i} frames, {fps:.1f} fps, reference from {len(refs)} frames")
print(" sec   n  noface   min   med   max   <0.3  <0.2")
for s in range(int(rows[-1][0]) + 1):
    r = [x for t, x in rows if int(t) == s and t >= a.ref_s]
    if not r:
        continue
    v = np.array([x for x in r if x is not None])
    nf = sum(x is None for x in r)
    if len(v):
        print(f"{s:4d} {len(r):3d} {nf:6d}  {v.min():.2f}  {np.median(v):.2f}  {v.max():.2f}  {int((v<0.3).sum()):4d}  {int((v<0.2).sum()):4d}")
    else:
        print(f"{s:4d} {len(r):3d} {nf:6d}    -     -     -")
allv = np.array([x for t, x in rows if x is not None and t >= a.ref_s])
print(f"overall: min {allv.min():.2f}  p5 {np.percentile(allv,5):.2f}  median {np.median(allv):.2f}  "
      f"frames <0.3: {(allv<0.3).mean()*100:.0f}%  <0.2: {(allv<0.2).mean()*100:.0f}%  "
      f"no-face frames: {sum(x is None for t, x in rows)}")
