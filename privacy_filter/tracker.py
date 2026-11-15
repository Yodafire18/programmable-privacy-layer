"""IoU + embedding tracker. Gives stable ids to faces across frames.

Faces may arrive without an embedding ("emb": None, live lazy embedding). Those are matched by IoU only,
and a track that has never received an embedding has `emb` None (unknown identity: never visible).
"""
import numpy as np


def iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


class Track:
    __slots__ = ("id", "box", "emb_sum", "n", "misses", "last_emb", "emb_k")

    def __init__(self, tid, box, emb, k=0):
        self.id = tid
        self.box = box
        self.emb_sum, self.n, self.last_emb, self.emb_k = None, 0, None, None
        self.misses = 0
        if emb is not None:
            self.add_emb(emb, k)

    def add_emb(self, emb, k):
        e = emb.astype(np.float32)
        if self.emb_sum is None:
            self.emb_sum = e.copy()
        else:
            self.emb_sum += e
        self.n += 1
        self.last_emb = e
        self.emb_k = k            # perceived-frame counter of the newest embedding

    @property
    def emb(self):
        if self.emb_sum is None:
            return None
        v = self.emb_sum / max(self.n, 1)
        return v / (np.linalg.norm(v) + 1e-9)


class Tracker:
    def __init__(self, cfg):
        self.cfg = cfg
        self.tracks = {}
        self.next_id = 1
        self.k = 0                # perceived frames seen

    def reset(self):
        self.tracks.clear()

    def plan(self, faces, conf_iou=0.5, margin=0.5):
        """Before embedding: which track does each face confidently continue (by IoU alone)?
        Returns (tid or None, confident) per face. Confident = mutual best IoU >= conf_iou, the runner-up
        (other track for this face, other face for this track) below margin * best, track seen last frame."""
        tids = list(self.tracks)
        if not faces or not tids:
            return [(None, False)] * len(faces)
        O = np.array([[iou(self.tracks[t].box, f["box"]) for t in tids] for f in faces])
        out = []
        for i in range(len(faces)):
            j = int(np.argmax(O[i]))
            best = O[i, j]
            if best < self.cfg["iou_match"]:
                out.append((None, False))
                continue
            row = np.delete(O[i], j)
            col = np.delete(O[:, j], i)
            tr = self.tracks[tids[j]]
            ok = (best >= conf_iou and int(np.argmax(O[:, j])) == i and tr.misses == 0
                  and (row.size == 0 or row.max() < margin * best)
                  and (col.size == 0 or col.max() < margin * best))
            out.append((tids[j], ok))
        return out

    def update(self, faces):
        """faces: list of {"box","emb"} (emb may be None). Returns list of track ids aligned with faces."""
        self.k += 1
        cands = []
        tids = list(self.tracks)
        temb = {tid: self.tracks[tid].emb for tid in tids}
        for i, f in enumerate(faces):
            fe = None if f["emb"] is None else f["emb"].astype(np.float32)
            for tid in tids:
                t = self.tracks[tid]
                o = iou(t.box, f["box"])
                te = temb[tid]
                if fe is None or te is None:
                    # no identity evidence on one side: IoU only (IoU counted twice in place of the similarity)
                    if o >= self.cfg["iou_match"]:
                        cands.append((2 * o, i, tid))
                    continue
                s = float(fe @ te)
                # IoU match must not contradict identity; emb match rescues fast motion
                if (o >= self.cfg["iou_match"] and s > 0.2) or s >= self.cfg["emb_match"]:
                    cands.append((o + s, i, tid))
        cands.sort(reverse=True)
        assigned, used = {}, set()
        for _, i, tid in cands:
            if i in assigned or tid in used:
                continue
            assigned[i] = tid
            used.add(tid)
        ids = []
        for i, f in enumerate(faces):
            e = f["emb"]
            if i in assigned:
                t = self.tracks[assigned[i]]
                t.box = f["box"]
                t.misses = 0
                if e is not None:
                    t.add_emb(e, self.k)
            else:
                t = Track(self.next_id, f["box"], e, self.k)
                self.tracks[t.id] = t
                self.next_id += 1
            ids.append(t.id)
        for tid in tids:
            if tid not in used:
                self.tracks[tid].misses += 1
                if self.tracks[tid].misses > self.cfg["max_misses"]:
                    del self.tracks[tid]
        return ids


def select_embeds(tracker, faces, priority=(), cfg=None):
    """Lazy embedding (live): which faces get ArcFace this frame. In priority order:
      0  faces touching a priority track (consented / gesture hold / matches memory): identity must be
         fresh every frame before anything is shown, so a stranger can't inherit someone's consent
      1  new faces (no track to continue)
      2  faces of tracks that were never embedded
      3  faces that don't confidently continue their track (ambiguous, jumped, fast motion), stalest first
      4  periodic refresh once `refresh_n` perceived frames old, stalest first (round-robin)
    Confident continuations that aren't due reuse their track's embedding. At most `max_per_frame`
    (priority faces always fit), so cost stays bounded in a crowd."""
    cfg = cfg or {}
    refresh_n, cap = cfg.get("refresh_n", 5), cfg.get("max_per_frame", 4)
    plan = tracker.plan(faces, cfg.get("conf_iou", 0.5))
    pboxes = [tracker.tracks[t].box for t in priority if t in tracker.tracks]
    keyed = []
    for i, (f, (tid, ok)) in enumerate(zip(faces, plan)):
        tr = tracker.tracks.get(tid)
        if tid in priority or any(iou(b, f["box"]) > 0 for b in pboxes):
            keyed.append((0, 0, i))
        elif tr is None:
            keyed.append((1, 0, i))
        elif tr.emb_k is None:
            keyed.append((2, 0, i))
        elif not ok:
            keyed.append((3, tr.emb_k, i))
        elif tracker.k + 1 - tr.emb_k >= refresh_n:       # this frame is k+1
            keyed.append((4, tr.emb_k, i))
    keyed.sort()
    n0 = sum(1 for k in keyed if k[0] == 0)
    return [i for _, _, i in keyed[:max(cap, n0)]]
