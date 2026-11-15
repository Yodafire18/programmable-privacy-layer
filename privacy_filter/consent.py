"""Consent engine: double peace sign -> visible, double thumbs-down -> blurred again.

Fail closed everywhere: ambiguous hands are ignored, unknown faces are blurred,
a consented track whose current embedding stops matching is blurred instantly.
Only consenting people's embeddings are stored (RAM only).
"""
import numpy as np

CONSENT, REVOKE = "Victory", "Thumb_Down"


def assign_hands(faces, hands, cfg):
    """Map each hand to at most one face index (or None). Fail closed on ambiguity."""
    out = []
    for h in hands:
        hx, hy = h["center"]
        cands = []
        for i, f in enumerate(faces):
            x1, y1, x2, y2 = f["box"]
            w, hh = x2 - x1, y2 - y1
            cx = (x1 + x2) / 2
            if abs(hx - cx) <= cfg["win_x"] * w and y1 - cfg["win_up"] * hh <= hy <= y2 + cfg["win_down"] * hh:
                # distance normalized by face size (bigger/closer face => larger reach)
                cy = (y1 + y2) / 2
                d = np.hypot((hx - cx) / w, (hy - cy) / hh)
                cands.append((d, i))
        cands.sort()
        if not cands:
            out.append(None)
        elif len(cands) > 1 and cands[1][0] < cands[0][0] * cfg["ambiguity"]:
            out.append(None)
        else:
            out.append(cands[0][1])
    return out


class ConsentEngine:
    def __init__(self, cfg, video=True):
        self.cfg = cfg
        self.video = video
        self.memory = []          # list of (person_key, emb)
        self.consented = set()    # track ids currently consented
        self.hold = {}            # (track_id, kind) -> start time
        self.events = []          # (t, kind, track_id)
        self._next_key = 1

    @property
    def consent_g(self):
        return self.cfg.get("consent_gesture", CONSENT)

    @property
    def revoke_g(self):
        return self.cfg.get("revoke_gesture", REVOKE)

    def forget_all(self):
        self.memory.clear()
        self.consented.clear()
        self.hold.clear()

    def best_match(self, emb):
        if not self.memory:
            return None, 0.0
        sims = [float(emb @ e) for _, e in self.memory]
        j = int(np.argmax(sims))
        return self.memory[j][0], sims[j]

    def step(self, t, faces, track_ids, hands, tracker):
        """Returns {track_id: {"visible": bool, "sim": float, "hold": {kind: progress}}}."""
        cfg = self.cfg
        gestures = {}
        for h, fi in zip(hands, assign_hands(faces, hands, cfg)):
            if fi is None or h["score"] < cfg["gesture_min_score"]:
                continue
            if h["gesture"] in (self.consent_g, self.revoke_g):
                gestures.setdefault((track_ids[fi], h["gesture"]), 0)
                gestures[(track_ids[fi], h["gesture"])] += 1

        active = {k for k, n in gestures.items() if n >= cfg.get("hands_required", 2)}
        for k in list(self.hold):
            if k not in active:
                del self.hold[k]
        progress = {}
        for k in active:
            start = self.hold.setdefault(k, t)
            need = cfg["hold_s"] if self.video else 0.0
            p = 1.0 if need == 0 else min(1.0, (t - start) / need)
            progress[k] = p
            if p >= 1.0:
                tid, kind = k
                tr = tracker.tracks.get(tid)
                if tr is None:
                    continue
                if kind == self.consent_g and tid not in self.consented:
                    self.consented.add(tid)
                    self.memory.append((self._next_key, tr.emb.copy()))
                    self._next_key += 1
                    self.events.append((t, "consent", tid))
                elif kind == self.revoke_g and (tid in self.consented or self.best_match(tr.emb)[1] >= cfg["match_thresh"]):
                    self.consented.discard(tid)
                    self.memory = [(pk, e) for pk, e in self.memory if float(tr.emb @ e) < cfg["drop_thresh"]]
                    self.events.append((t, "revoke", tid))
                del self.hold[k]

        states = {}
        for f, tid in zip(faces, track_ids):
            tr = tracker.tracks[tid]
            cur = f["emb"].astype(np.float32)
            _, sim_track = self.best_match(tr.emb)
            _, sim_cur = self.best_match(cur)
            if tid not in self.consented and sim_track >= cfg["match_thresh"] and tr.n >= 3:
                self.consented.add(tid)        # remembered person re-appeared
            visible = tid in self.consented and sim_cur >= cfg["drop_thresh"]
            states[tid] = {
                "visible": visible,
                "sim": sim_cur,
                "hold": {kind: p for (k_tid, kind), p in progress.items() if k_tid == tid},
            }
        # forget consent flag for dead tracks (memory keeps the embedding)
        self.consented &= set(tracker.tracks)
        return states
