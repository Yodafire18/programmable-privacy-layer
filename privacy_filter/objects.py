"""Object blur targets (currently: screens = phones, laptops, monitors/TVs).

Screens are always blurred and never consentable. Nothing here is imported, loaded or run unless the
active profile lists `screens` in blur_targets (cfg["objects"]["screens"]).

  ScreenDetector  YOLOv8n (COCO) ONNX on onnxruntime, letterboxed, NMS, screen classes only
  ObjectTracker   IoU tracker; ids live in their own space (OBJ_TID0 + n) so they never collide with faces;
                  a missed track is re-emitted (held + grown) for `max_misses` detector runs (fail closed)
  ScreenStage     the hook used by the perception worker: lazy model load, runs every `every_n` perceived frames
  object_regions  per-frame object detections -> timeline.build -> rectangular blur regions
"""
import ast
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "models" / "yolov8n.onnx"
OBJ_TID0 = 1_000_000                    # object track ids start here (face tids are small positive ints)
SCREEN_NAMES = {"tv": "screen", "laptop": "laptop", "cell phone": "phone"}   # COCO name -> our label

DEFAULTS = {
    "screens": False,          # set by the profile (profiles.to_cfg); False = detector never loaded
    "model": str(MODEL),
    "imgsz": 640,              # detector input (dynamic-shape export; 480/416 are faster, less recall on small phones)
    "conf": 0.25,              # low on purpose: fail closed (a false blur on a box is cheap)
    "nms_iou": 0.5,
    "every_n": 3,              # run on every n-th perceived frame
    "threads": 2,              # onnxruntime intra-op threads (faces use perception.ort_threads)
    "parallel": True,          # run concurrently with face/hand perception instead of after it
    "iou_match": 0.2,
    "max_misses": 4,           # detector runs a lost screen is held (and grown) before it is dropped
    "hold_grow": 0.08,         # held box grows by this fraction per missed run
    "expand": 1.15,            # render box scale (faces use render.expand)
    "feather": 0.08,           # rect edge feather (fraction of half-size)
}


def ocfg(cfg):
    """cfg["objects"] with defaults filled in (config.DEFAULTS doesn't need to know about objects)."""
    o = dict(DEFAULTS)
    o.update(cfg.get("objects") or {})
    return o


class ScreenDetector:
    def __init__(self, model=MODEL, imgsz=640, conf=0.25, nms_iou=0.5, threads=2):
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = int(threads)
        so.inter_op_num_threads = 1
        so.add_session_config_entry("session.intra_op.allow_spinning", "0")
        Sess = getattr(ort, "_pf_orig_session", ort.InferenceSession)   # bypass the face-side thread patch
        self.sess = Sess(str(model), so, providers=["CPUExecutionProvider"])
        names = ast.literal_eval(self.sess.get_modelmeta().custom_metadata_map["names"])
        # resolve class indices from the model itself (COCO: 62 tv, 63 laptop, 67 cell phone)
        self.classes = {int(k): SCREEN_NAMES[v] for k, v in names.items() if v in SCREEN_NAMES}
        if len(self.classes) != len(SCREEN_NAMES):
            raise RuntimeError(f"{model}: screen classes missing from model names")
        self.cls_idx = np.array(sorted(self.classes))
        self.imgsz, self.conf, self.nms_iou = int(imgsz), float(conf), float(nms_iou)
        self.inp = self.sess.get_inputs()[0].name

    def __call__(self, bgr):
        import cv2
        H, W = bgr.shape[:2]
        s = self.imgsz / max(H, W)
        nw, nh = int(round(W * s)), int(round(H * s))
        # pad to a multiple of 32 (smaller than a square letterbox for 16:9 frames)
        pw, ph = (nw + 31) // 32 * 32, (nh + 31) // 32 * 32
        img = np.full((ph, pw, 3), 114, np.uint8)
        img[:nh, :nw] = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)
        x = img[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        out = self.sess.run(None, {self.inp: np.ascontiguousarray(x)})[0][0]   # (84, anchors)
        scores = out[4 + self.cls_idx]                                          # (n_cls, anchors)
        best = scores.argmax(0)
        conf = scores[best, np.arange(scores.shape[1])]
        keep = conf >= self.conf
        if not keep.any():
            return []
        cx, cy, w, h = out[:4, keep]
        conf, cls = conf[keep], self.cls_idx[best[keep]]
        boxes = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], 1) / s
        boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, W)
        boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, H)
        # class-agnostic NMS: a phone/laptop/tv confusion on one object must not yield two boxes
        xywh = [[float(b[0]), float(b[1]), float(b[2] - b[0]), float(b[3] - b[1])] for b in boxes]
        idx = cv2.dnn.NMSBoxes(xywh, conf.astype(float).tolist(), self.conf, self.nms_iou)
        return [{"box": [float(v) for v in boxes[i]], "score": float(conf[i]), "label": self.classes[int(cls[i])]}
                for i in np.array(idx).reshape(-1)]


def _iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def _grow(box, f):
    x1, y1, x2, y2 = box
    dw, dh = (x2 - x1) * f / 2, (y2 - y1) * f / 2
    return [x1 - dw, y1 - dh, x2 + dw, y2 + dh]


class ObjectTracker:
    """Greedy IoU tracker. Output includes held (missed) tracks, grown, so a flicker of the detector never
    unblurs a screen. Everything is visible=False: objects are never consentable."""

    def __init__(self, iou_match=0.2, max_misses=4, hold_grow=0.08):
        self.iou_match, self.max_misses, self.hold_grow = iou_match, max_misses, hold_grow
        self.tracks = {}      # tid -> {"box", "label", "misses"}
        self.next = OBJ_TID0

    def update(self, dets):
        pairs = sorted(((_iou(t["box"], d["box"]), tid, k) for tid, t in self.tracks.items()
                        for k, d in enumerate(dets)), reverse=True)
        used_t, used_d = set(), set()
        for v, tid, k in pairs:
            if v < self.iou_match:
                break
            if tid in used_t or k in used_d:
                continue
            used_t.add(tid); used_d.add(k)
            self.tracks[tid].update(box=dets[k]["box"], label=dets[k]["label"], misses=0)
        for k, d in enumerate(dets):
            if k not in used_d:
                self.tracks[self.next] = {"box": d["box"], "label": d["label"], "misses": 0}
                used_t.add(self.next)
                self.next += 1
        for tid in list(self.tracks):
            if tid in used_t:
                continue
            t = self.tracks[tid]
            t["misses"] += 1
            if t["misses"] > self.max_misses:
                del self.tracks[tid]
            else:
                t["box"] = _grow(t["box"], self.hold_grow)
        return [{"tid": tid, "box": list(t["box"]), "visible": False, "label": t["label"], "held": t["misses"] > 0}
                for tid, t in self.tracks.items()]


class ScreenStage:
    """Perception-worker hook. Cheap no-op while screens are off; the model loads on first enable.

        stage = ScreenStage(cfg)          # in the worker, once
        stage.configure(objects_cfg)      # on profile change
        stage.submit(img)                 # before face perception (runs concurrently if parallel)
        objs = stage.result()             # after: None = not run this frame, else list of object dets
    """

    def __init__(self, cfg):
        self.o = ocfg(cfg)
        self.det = None
        self.tracker = None
        self.pool = None
        self.k = 0
        self._job = None
        self.ms = 0.0

    @property
    def on(self):
        return bool(self.o.get("screens"))

    def configure(self, objects_cfg):
        was = self.on
        self.o.update(objects_cfg or {})
        if was and not self.on:
            self.tracker = None              # stale screens must not reappear when re-enabled later

    def _load(self):
        if self.det is None:
            o = self.o
            self.det = ScreenDetector(o["model"], o["imgsz"], o["conf"], o["nms_iou"], o["threads"])
        if self.tracker is None:
            o = self.o
            self.tracker = ObjectTracker(o["iou_match"], o["max_misses"], o["hold_grow"])

    def _run(self, img):
        t0 = time.perf_counter()
        objs = self.tracker.update(self.det(img))
        return objs, (time.perf_counter() - t0) * 1000

    def submit(self, img):
        self._job = None
        if not self.on:
            return
        self.k += 1
        if (self.k - 1) % max(1, int(self.o["every_n"])):
            return
        self._load()
        if self.o.get("parallel"):
            if self.pool is None:
                from concurrent.futures import ThreadPoolExecutor
                self.pool = ThreadPoolExecutor(max_workers=1)
            self._job = self.pool.submit(self._run, img)
        else:
            self._job = self._run(img)

    def result(self):
        job, self._job = self._job, None
        if job is None:
            return None
        objs, ms = job if isinstance(job, tuple) else job.result()
        self.ms = ms
        return objs

    def __call__(self, img):
        self.submit(img)
        return self.result()


# ---------------- rendering side ----------------

def object_regions(win, fps, cfg, size):
    """win: per-frame object dets (lists, [] where not perceived/not run). Returns per-frame regions with
    shape=rect, built by the same timeline as faces (gap interpolation, hold/grow, backfill, smoothing)."""
    from . import timeline
    o = ocfg(cfg)
    rcfg = dict(cfg["render"], expand=o["expand"])
    if not any(win):
        return [[] for _ in win]
    regs = timeline.build(win, fps, cfg["timeline"], rcfg, size)
    for frame in regs:
        for r in frame:
            r["shape"] = "rect"
            r["feather"] = o["feather"]
            r["blur"] = 1.0                      # never consentable: no fades
    return regs


def rect_coverage(gt_box, regions, feather, thresh=0.9, grid=9):
    """Fraction of a GT screen rectangle blurred at >= thresh (honours each region's shape)."""
    from .render import mask_for
    if not regions:
        return 0.0
    x1, y1, x2, y2 = gt_box
    u, v = np.meshgrid(np.linspace(x1, x2, grid), np.linspace(y1, y2, grid))
    xs, ys = u.ravel(), v.ravel()
    m = np.zeros_like(xs)
    for r in regions:
        m = np.maximum(m, mask_for(xs, ys, r, feather))
    return float((m >= thresh).mean())
