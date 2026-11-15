"""Lazy embedding (live): tracker + consent with faces that carry no fresh embedding.

  .venv/bin/python -m pytest tests -q
"""
import numpy as np

from privacy_filter import config as C
from privacy_filter import timeline
from privacy_filter.consent import ConsentEngine
from privacy_filter.tracker import Tracker, select_embeds

CFG = C.load()
EMB = {"refresh_n": 3, "max_per_frame": 4, "conf_iou": 0.5}
rng = np.random.default_rng(0)


def ident():
    v = rng.normal(size=512).astype(np.float32)
    return v / np.linalg.norm(v)


def noisy(e, s=0.02):
    v = e + rng.normal(scale=s, size=512).astype(np.float32)
    return (v / np.linalg.norm(v)).astype(np.float16)


def box(x, y=100, w=40):
    return [float(x), float(y), float(x + w), float(y + w * 1.2)]


class World:
    """Runs select -> 'embed' (from ground-truth identities) -> tracker -> consent, like the live worker."""

    def __init__(self):
        self.tr = Tracker(CFG["tracker"])
        self.ce = ConsentEngine(CFG["consent"], video=True)
        self.t = 0.0
        self.embedded = []

    def step(self, people, hands=()):
        """people: list of (box, identity_emb). Returns (faces, tids, states)."""
        faces = [{"box": b, "score": 0.9, "emb": None} for b, _ in people]
        idx = select_embeds(self.tr, faces, self.ce.priority_tids(self.tr), EMB)
        for i in idx:
            faces[i]["emb"] = noisy(people[i][1])
        self.embedded.append(sorted(idx))
        tids = self.tr.update(faces)
        states = self.ce.step(self.t, faces, tids, list(hands), self.tr)
        self.t += 0.1
        return faces, tids, states


def test_confident_continuations_reuse_track_embedding():
    w = World()
    people = [(box(100 + 100 * k), ident()) for k in range(3)]
    _, tids0, _ = w.step(people)
    assert w.embedded[-1] == [0, 1, 2]                 # new faces: embedded
    for f in range(1, 3):                              # small motion: same tracks, no ArcFace
        _, tids, _ = w.step([((b[0] + 2 * f, b[1], b[2] + 2 * f, b[3]), e) for b, e in people])
        assert tids == tids0
        assert w.embedded[-1] == []
    w.step(people)
    assert w.embedded[-1] == [0, 1, 2]                 # refresh after refresh_n perceived frames


def test_cap_round_robin_and_unembedded_never_visible():
    w = World()
    people = [(box(10 + 50 * k, w=30), ident()) for k in range(12)]
    w.ce.memory.append((99, people[5][1]))             # someone in the crowd is remembered
    seen = set()
    for _ in range(4):
        faces, tids, states = w.step(people)
        assert len(w.embedded[-1]) <= EMB["max_per_frame"] + 1   # +1: the remembered face is priority
        seen |= set(w.embedded[-1])
        for f, tid in zip(faces, tids):
            if f["emb"] is None:
                assert not states[tid]["visible"]      # unknown identity -> blurred
    assert seen == set(range(12))                      # everyone gets embedded eventually
    # once matched, the remembered person is embedded every frame and becomes visible; nobody else does
    for _ in range(4):
        faces, tids, states = w.step(people)
        assert 5 in w.embedded[-1]
    vis = [i for i, tid in enumerate(tids) if states[tid]["visible"]]
    assert vis == [5]


def test_stranger_cannot_inherit_consent():
    w = World()
    alice, bob = ident(), ident()
    w.ce.memory.append((1, alice))
    for _ in range(4):
        faces, tids, states = w.step([(box(300), alice)])
    a_tid = tids[0]
    assert states[a_tid]["visible"]
    # Bob steps into Alice's spot (high IoU): her track is priority, so his face is re-embedded
    faces, tids, states = w.step([(box(302), bob)])
    assert w.embedded[-1] == [0]
    assert not any(s["visible"] for s in states.values())
    # even if a consented track's face somehow came without a fresh embedding, it is not shown
    faces = [{"box": box(300), "score": 0.9, "emb": None}]
    tids = w.tr.update(faces)
    states = w.ce.step(w.t, faces, tids, [], w.tr)
    assert not states[tids[0]]["visible"]


def test_consent_gesture_needs_fresh_embedding():
    w = World()
    carol = ident()
    b = box(300, y=100, w=60)
    hand = lambda x: {"gesture": "Victory", "score": 0.9, "center": [x, 230.0], "box": [0, 0, 1, 1]}
    hands = [hand(300), hand(360)]
    for _ in range(12):                                 # hold the gesture > hold_s
        faces, tids, states = w.step([(b, carol)], hands)
        assert faces[0]["emb"] is not None             # gesture hold = priority: embedded every frame
    assert w.ce.events and w.ce.events[0][1] == "consent"
    assert states[tids[0]]["visible"]
    assert float(w.ce.memory[0][1] @ carol) > 0.9


def test_offline_tracker_unchanged_when_all_embedded():
    tr = Tracker(CFG["tracker"])
    a, b = ident(), ident()
    ids1 = tr.update([{"box": box(100), "emb": noisy(a)}, {"box": box(300), "emb": noisy(b)}])
    ids2 = tr.update([{"box": box(305), "emb": noisy(b)}, {"box": box(104), "emb": noisy(a)}])
    assert ids2 == ids1[::-1]
    assert all(t.n == 2 for t in tr.tracks.values())


def test_timeline_only_matches_full_build():
    logic = []
    for i in range(80):
        logic.append([] if i % 3 else [{"tid": k, "box": box(50 * k + i, w=30), "visible": k == 1 and i > 20}
                                       for k in range(6) if not (k == 2 and 30 < i < 50)])
    tl = dict(CFG["timeline"], buffer_s=0.75)
    full = timeline.build(logic, 30, tl, CFG["render"], (640, 360))
    for j in range(len(logic)):
        assert timeline.build(logic, 30, tl, CFG["render"], (640, 360), only=j)[j] == full[j]


def test_round_robin_during_pan():
    """Panning crowd: no face is a confident continuation, so the cap must rotate over everyone."""
    w = World()
    ids = [ident() for _ in range(12)]
    for f in range(9):
        w.step([(box(10 + 50 * k + 12 * f, w=30), ids[k]) for k in range(12)])
    for f in range(3, 9 - 2):                          # after the first fill, every 3 frames cover all
        assert set(sum(w.embedded[f:f + 3], [])) == set(range(12))
    assert len(w.tr.tracks) == 12                      # identities kept through the pan


# ---------- consent grace (head tilt/turn dips the match score) ----------

def tilted(e, sim=0.25):
    """An embedding with roughly `sim` cosine to e: what a head tilt does to the same person."""
    o = ident()
    o = o - (o @ e) * e
    o /= np.linalg.norm(o)
    v = sim * e + np.sqrt(1 - sim ** 2) * o
    return v.astype(np.float16)


def consented_world():
    w = World()
    me = ident()
    w.ce.memory.append((1, me))
    for _ in range(4):                                   # becomes visible as a remembered person
        faces, tids, st = w.step([(box(200), me)])
    assert st[tids[0]]["visible"]
    return w, me


def step_with(w, people_embs):
    """people_embs: list of (box, emb_to_report) -> bypasses noisy() so we control similarity exactly."""
    faces = [{"box": b, "score": 0.9, "emb": e} for b, e in people_embs]
    tids = w.tr.update(faces)
    st = w.ce.step(w.t, faces, tids, [], w.tr)
    w.t += 0.1
    return tids, st


def test_grace_rides_out_short_dip():
    w, me = consented_world()
    for k in range(4):                                   # 0.4 s of low similarity, same place
        tids, st = step_with(w, [(box(201 + k), tilted(me))])
        assert st[tids[0]]["visible"], f"flickered at dip frame {k}"


def test_grace_expires():
    w, me = consented_world()
    vis = []
    for k in range(10):                                  # 1.0 s of low similarity
        tids, st = step_with(w, [(box(201), tilted(me))])
        vis.append(st[tids[0]]["visible"])
    assert vis[0] and not vis[-1]


def test_no_grace_when_another_face_overlaps():
    w, me = consented_world()
    stranger = ident()
    tids, st = step_with(w, [(box(201), tilted(me)), (box(215), stranger.astype(np.float16))])
    assert not any(s["visible"] for s in st.values())


def test_no_grace_on_box_jump():
    w, me = consented_world()
    tids, st = step_with(w, [(box(201), me.astype(np.float16))])
    assert st[tids[0]]["visible"]
    tids2, st = step_with(w, [(box(240), tilted(me))])   # big jump + low sim: could be a different person
    assert not st[tids2[0]]["visible"]


def test_no_grace_for_a_different_person_in_the_same_spot():
    w, me = consented_world()
    stranger = ident()
    tids, st = step_with(w, [(box(201), stranger.astype(np.float16))])   # continuous, alone, but sim ~0
    assert not any(s["visible"] for s in st.values())
