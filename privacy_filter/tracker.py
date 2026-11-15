"""IoU + embedding tracker. Gives stable ids to faces across frames."""
import numpy as np


def iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


class Track:
    __slots__ = ("id", "box", "emb_sum", "n", "misses", "last_emb")

    def __init__(self, tid, box, emb):
        self.id = tid
        self.box = box
        e = emb.astype(np.float32)
        self.emb_sum = e.copy()
        self.n = 1
        self.misses = 0
        self.last_emb = e

    @property
    def emb(self):
        v = self.emb_sum / max(self.n, 1)
        return v / (np.linalg.norm(v) + 1e-9)


class Tracker:
    def __init__(self, cfg):
        self.cfg = cfg
        self.tracks = {}
        self.next_id = 1

    def reset(self):
        self.tracks.clear()

    def update(self, faces):
        """faces: list of {"box","emb"}. Returns list of track ids aligned with faces."""
        cands = []
        tids = list(self.tracks)
        for i, f in enumerate(faces):
            fe = f["emb"].astype(np.float32)
            for tid in tids:
                t = self.tracks[tid]
                o = iou(t.box, f["box"])
                s = float(fe @ t.emb)
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
            e = f["emb"].astype(np.float32)
            if i in assigned:
                t = self.tracks[assigned[i]]
                t.box = f["box"]
                t.emb_sum += e
                t.n += 1
                t.misses = 0
                t.last_emb = e
            else:
                t = Track(self.next_id, f["box"], e)
                self.tracks[t.id] = t
                self.next_id += 1
            ids.append(t.id)
        for tid in tids:
            if tid not in used:
                self.tracks[tid].misses += 1
                if self.tracks[tid].misses > self.cfg["max_misses"]:
                    del self.tracks[tid]
        return ids
