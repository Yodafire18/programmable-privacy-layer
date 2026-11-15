"""Model stage: faces (SCRFD + ArcFace via InsightFace) and hand gestures (MediaPipe).

Output per frame is plain data so it can be cached and replayed:
  {"t": sec, "faces": [{"box": [x1,y1,x2,y2], "score": f, "emb": float16[512]}],
   "hands": [{"gesture": str, "score": f, "center": [x,y], "box": [...]}],
   "ms": {"faces": f, "hands": f}}
"""
import os
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
GESTURE_MODEL = ROOT / "models" / "gesture_recognizer.task"


def limit_ort_threads(n):
    """onnxruntime defaults to one busy-spinning thread per core, which starves the display/capture threads.
    Inject SessionOptions with n threads and no spinning into every session InsightFace creates."""
    import onnxruntime as ort
    if getattr(ort, "_pf_threads", None) == n:
        return
    Orig = getattr(ort, "_pf_orig_session", ort.InferenceSession)

    class Session(Orig):
        def __init__(self, path_or_bytes, sess_options=None, *a, **k):
            if sess_options is None:
                sess_options = ort.SessionOptions()
                sess_options.intra_op_num_threads = n
                sess_options.inter_op_num_threads = 1
                sess_options.add_session_config_entry("session.intra_op.allow_spinning", "0")
            super().__init__(path_or_bytes, sess_options, *a, **k)

    ort._pf_orig_session = Orig
    ort.InferenceSession = Session
    ort._pf_threads = n


class FaceEngine:
    def __init__(self, det_size=640, det_thresh=0.5, providers=("CPUExecutionProvider",), model="buffalo_l",
                 threads=None):
        if threads:
            limit_ort_threads(threads)
        import contextlib
        import io

        from insightface.app import FaceAnalysis

        with contextlib.redirect_stdout(io.StringIO()):
            self.app = FaceAnalysis(name=model, allowed_modules=["detection", "recognition"],
                                    providers=list(providers))
            self.app.prepare(ctx_id=-1, det_size=(det_size, det_size), det_thresh=det_thresh)

    def __call__(self, bgr):
        out = []
        for f in self.app.get(bgr):
            out.append({
                "box": [float(v) for v in f.bbox],
                "score": float(f.det_score),
                "emb": f.normed_embedding.astype(np.float16),
            })
        return out


class HandEngine:
    def __init__(self, num_hands=6, min_conf=0.5, video=True, max_w=None):
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        self.mp = mp
        self.video = video
        mode = vision.RunningMode.VIDEO if video else vision.RunningMode.IMAGE
        opts = vision.GestureRecognizerOptions(
            base_options=BaseOptions(model_asset_path=str(GESTURE_MODEL)),
            running_mode=mode,
            num_hands=num_hands,
            min_hand_detection_confidence=min_conf,
            min_hand_presence_confidence=min_conf,
            min_tracking_confidence=min_conf,
        )
        self.rec = vision.GestureRecognizer.create_from_options(opts)
        self._last_ms = -1
        self.max_w = max_w

    def __call__(self, bgr, t=0.0):
        h, w = bgr.shape[:2]
        if self.max_w and w > self.max_w:   # gestures don't need full res; coords below are normalized
            import cv2
            bgr = cv2.resize(bgr, (self.max_w, int(h * self.max_w / w)), interpolation=cv2.INTER_AREA)
        rgb = np.ascontiguousarray(bgr[:, :, ::-1])
        img = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)
        if self.video:
            ms = max(int(t * 1000), self._last_ms + 1)  # must be strictly increasing
            self._last_ms = ms
            res = self.rec.recognize_for_video(img, ms)
        else:
            res = self.rec.recognize(img)
        out = []
        for gs, lms in zip(res.gestures, res.hand_landmarks):
            xs = np.array([p.x for p in lms]) * w
            ys = np.array([p.y for p in lms]) * h
            g = gs[0] if gs else None
            out.append({
                "gesture": g.category_name if g else "None",
                "score": float(g.score) if g else 0.0,
                "center": [float(xs.mean()), float(ys.mean())],
                "box": [float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())],
            })
        return out

    def close(self):
        self.rec.close()


class Perception:
    def __init__(self, pcfg, video=True):
        self.faces = FaceEngine(pcfg["det_size"], pcfg["det_thresh"], pcfg["providers"], pcfg["face_model"],
                                threads=pcfg.get("ort_threads"))
        self.hands = HandEngine(pcfg["num_hands"], pcfg["hand_min_conf"], video=video,
                                max_w=pcfg.get("hand_max_w"))
        self.pool = None
        if pcfg.get("parallel"):
            from concurrent.futures import ThreadPoolExecutor
            self.pool = ThreadPoolExecutor(max_workers=1)   # one fixed thread for MediaPipe

    def _timed_hands(self, bgr, t):
        t0 = time.perf_counter()
        return self.hands(bgr, t), (time.perf_counter() - t0) * 1000

    def __call__(self, bgr, t):
        if self.pool is not None:      # faces and hands concurrently (both release the GIL in native code)
            fut = self.pool.submit(self._timed_hands, bgr, t)
            t0 = time.perf_counter()
            faces = self.faces(bgr)
            ms_f = (time.perf_counter() - t0) * 1000
            hands, ms_h = fut.result()
            return {"t": t, "faces": faces, "hands": hands, "ms": {"faces": ms_f, "hands": ms_h}}
        t0 = time.perf_counter()
        faces = self.faces(bgr)
        t1 = time.perf_counter()
        hands = self.hands(bgr, t)
        t2 = time.perf_counter()
        return {"t": t, "faces": faces, "hands": hands,
                "ms": {"faces": (t1 - t0) * 1000, "hands": (t2 - t1) * 1000}}

    def close(self):
        if self.pool is not None:
            self.pool.shutdown()
        self.hands.close()


os.environ.setdefault("GLOG_minloglevel", "2")
