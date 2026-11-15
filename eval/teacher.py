"""Ground-truth faces: slow, high-recall offline pass (bigger detector input, lower threshold,
track interpolation) + identity from enrolled people. Cached per clip.

Enrollment: eval/people/<name>/*.jpg (3-5 clear photos) -> eval/people/<name>.npy
"""
import pickle
from pathlib import Path

import cv2
import numpy as np

from privacy_filter.config import stable_hash
from privacy_filter.process import CACHE, clip_path
from privacy_filter.sources import file_frames
from privacy_filter.tracker import Tracker

PEOPLE = Path(__file__).resolve().parent / "people"
TEACHER_CFG = {"det_size": 960, "det_thresh": 0.35, "interp_gap": 15, "id_thresh": 0.4, "v": 1}


def people():
    out = {}
    for p in sorted(PEOPLE.glob("*.npy")):
        out[p.stem] = np.load(p)
    return out


def enroll(name, img_dir, engine=None):
    from privacy_filter.perception import FaceEngine
    eng = engine or FaceEngine(det_size=640, model="buffalo_l")
    embs = []
    for p in sorted(Path(img_dir).iterdir()):
        img = cv2.imread(str(p))
        if img is None:
            continue
        faces = eng(img)
        if not faces:
            print(f"  no face in {p.name}")
            continue
        f = max(faces, key=lambda f: (f["box"][2] - f["box"][0]) * (f["box"][3] - f["box"][1]))
        embs.append(f["emb"].astype(np.float32))
    if not embs:
        raise SystemExit("no faces found")
    PEOPLE.mkdir(exist_ok=True)
    np.save(PEOPLE / f"{name}.npy", np.stack(embs))
    print(f"enrolled {name}: {len(embs)} embeddings")


def teacher(clip_id, log=print):
    key = stable_hash({"t": TEACHER_CFG, "people": sorted(people())})
    cp = CACHE / clip_id / f"teacher-{key}.pkl"
    if cp.exists():
        with open(cp, "rb") as f:
            return pickle.load(f)
    from privacy_filter.perception import FaceEngine
    eng = FaceEngine(det_size=TEACHER_CFG["det_size"], det_thresh=TEACHER_CFG["det_thresh"], model="buffalo_l")
    tr = Tracker({"iou_match": 0.2, "emb_match": 0.4, "max_misses": TEACHER_CFG["interp_gap"]})
    per_frame = []
    track_embs = {}
    for t, img in file_frames(clip_path(clip_id)):
        faces = eng(img)
        tids = tr.update(faces)
        per_frame.append([(tid, f["box"]) for tid, f in zip(tids, faces)])
        for tid, f in zip(tids, faces):
            track_embs.setdefault(tid, []).append(f["emb"].astype(np.float32))

    # identity per track
    ppl = people()
    ident = {}
    for tid, es in track_embs.items():
        m = np.mean(es, axis=0)
        m /= np.linalg.norm(m) + 1e-9
        best, bs = f"unknown{tid}", TEACHER_CFG["id_thresh"]
        for name, pe in ppl.items():
            s = float(np.max(pe @ m))
            if s > bs:
                best, bs = name, s
        ident[tid] = best

    # interpolate short gaps per track
    seqs = {}
    for i, dets in enumerate(per_frame):
        for tid, box in dets:
            seqs.setdefault(tid, []).append((i, box))
    gt = [[] for _ in per_frame]
    for tid, seq in seqs.items():
        if len(seq) < 2:   # single-frame detections are likely false positives; keep but mark
            pass
        for j, (i, box) in enumerate(seq):
            gt[i].append({"person": ident[tid], "box": box, "interp": False, "tid": tid})
            if j + 1 < len(seq):
                i2, b2 = seq[j + 1]
                if 1 < i2 - i <= TEACHER_CFG["interp_gap"]:
                    for g in range(i + 1, i2):
                        a = (g - i) / (i2 - i)
                        gt[g].append({"person": ident[tid], "box": [p * (1 - a) + q * a for p, q in zip(box, b2)],
                                      "interp": True, "tid": tid})
    cp.parent.mkdir(parents=True, exist_ok=True)
    with open(cp, "wb") as f:
        pickle.dump(gt, f)
    log(f"  teacher {clip_id}: {len(seqs)} tracks, ids={sorted(set(ident.values()))}")
    return gt


def warm(args):
    """Process-pool worker: fill perception + teacher caches for one clip."""
    import contextlib
    import io

    from privacy_filter.process import perceive
    clip, cfg = args
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        perceive(clip, cfg, log=print)
        teacher(clip, log=print)
    return buf.getvalue()
