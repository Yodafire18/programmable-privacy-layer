"""Live app: webcam / Mentra stream / video file -> privacy filter -> side-by-side window.

  capture thread    reads every frame, stamps arrival time
  perception thread runs faces+hands on the newest frame, then tracker + consent (skips frames it can't keep up with)
  main loop         outputs frame j once it is `buffer_s` old, so the timeline can see `buffer_s` of the future
                    (smooth + fail closed); unperceived frames are interpolated like detection gaps.

Run:
  python -m privacy_filter.live                         # webcam 0
  python -m privacy_filter.live --source rtmp://localhost:1935/live/mentra
  python -m privacy_filter.live --source corpus/synth_slow_pan/raw.mp4 --headless --out output/test.mp4
Keys: q quit · f forget everyone · space save photo · r record on/off · d toggle debug panel
"""
import argparse
import os
import subprocess
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from . import config as C
from . import profiles as PR
from . import render as R
from . import timeline
from .consent import ConsentEngine
from .tracker import Tracker, select_embeds
from .voice_hud import VoiceHUD   # voice hook

ROOT = Path(__file__).resolve().parent.parent
# live detector input long side (1280x720 -> 1280x736 input) and lazy-embedding policy
DET_LONG = 1280
EMBED = {"refresh_n": 5, "max_per_frame": 4, "conf_iou": 0.5}
WIN = "privacy filter  (left: debug · right: output)"
OUT = ROOT / "output"


def find_camera(name):
    """AVFoundation index of the first video device whose name contains `name` (case-insensitive), else None.
    Re-run on every (re)connect: indices shift when devices are plugged/unplugged."""
    import re
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                             capture_output=True, text=True, timeout=5).stderr
    except Exception:
        return None
    video = out.split("video devices:")[-1].split("audio devices:")[0]
    for m in re.finditer(r"\[(\d+)\] (.+)", video):
        if name.lower() in m.group(2).lower():
            return int(m.group(1))
    return None


class NamedCamera:
    """cv2.VideoCapture-like reader for a camera selected by its exact AVFoundation name, via ffmpeg."""

    def __init__(self, name, w=1280, h=720, fps=30):
        self.w, self.h, self.fps = w, h, fps
        exact = self._exact_name(name)
        self.proc = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "avfoundation", "-framerate", str(fps),
             "-video_size", f"{w}x{h}", "-pixel_format", "nv12", "-i", exact,
             "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=w * h * 3)

    @staticmethod
    def _exact_name(name):
        import re
        out = subprocess.run(["ffmpeg", "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                             capture_output=True, text=True, timeout=5).stderr
        video = out.split("video devices:")[-1].split("audio devices:")[0]
        for m in re.finditer(r"\[\d+\] (.+)", video):
            if name.lower() in m.group(1).lower():
                return m.group(1).strip()
        return name

    def isOpened(self):
        return self.proc.poll() is None

    def read(self):
        n = self.w * self.h * 3
        buf = self.proc.stdout.read(n)
        if not buf or len(buf) < n:
            return False, None
        return True, np.frombuffer(buf, np.uint8).reshape(self.h, self.w, 3)

    def get(self, prop):
        return float(self.fps) if prop == cv2.CAP_PROP_FPS else 0.0

    def set(self, *a):
        return False

    def release(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def say(text):
    try:
        subprocess.Popen(["say", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        pass


def drop_faces_on_hands(faces, hands, f):
    """Low-confidence 'faces' sitting on a hand are hands (a fist/palm can fool SCRFD at the live 0.35 threshold).
    Real faces score >= 0.5 (0 of 545 overlapped a hand in testing), so they are never dropped."""
    if not f or not hands:
        return faces
    keep = []
    for fc in faces:
        x1, y1, x2, y2 = fc["box"]
        area = max(1.0, (x2 - x1) * (y2 - y1))
        covered = max((max(0.0, min(x2, h["box"][2]) - max(x1, h["box"][0])) *
                       max(0.0, min(y2, h["box"][3]) - max(y1, h["box"][1])) for h in hands), default=0.0) / area
        if fc["score"] < f["max_score"] and covered >= f["min_cover"]:
            continue
        keep.append(fc)
    return keep


def _perception_worker(cfg, req_ev, in_q, out_q, cmd_q):
    """Separate process: models + tracker + consent. Own GIL, so it can't stall capture/output."""
    import queue

    from .perception import Perception
    tracker = Tracker(cfg["tracker"])
    consent = ConsentEngine(cfg["consent"], video=True)
    select = None
    if cfg["perception"].get("embed") is not None:     # lazy ArcFace: only faces whose identity is needed
        def select(faces):
            return select_embeds(tracker, faces, consent.priority_tids(tracker), cfg["perception"]["embed"])
    P = Perception(cfg["perception"], video=True, select=select)
    from .objects import ScreenStage
    screens = ScreenStage(cfg)            # screens hook: no model loaded / run unless the profile enables it
    out_q.put(("ready",))
    n_ev = 0
    while True:
        try:
            while True:
                c = cmd_q.get_nowait()
                if c == "forget":
                    consent.forget_all()
                elif isinstance(c, tuple) and c[0] == "config":
                    consent.cfg = c[1]            # new profile; consented people stay remembered
                    if len(c) > 2:
                        screens.configure(c[2])   # objects cfg (screens on/off)
        except queue.Empty:
            pass
        req_ev.set()                      # ask for the newest frame
        item = in_q.get()
        if item is None:
            break
        i, t, img = item
        screens.submit(img)               # runs alongside P() when objects.parallel
        fr = P(img, t)
        fr["faces"] = drop_faces_on_hands(fr["faces"], fr["hands"], cfg["perception"].get("hand_face_filter"))
        objs = screens.result()
        if objs is not None:
            out_q.put(("obj", i, objs, screens.ms))
        faces = fr["faces"]
        tids = tracker.update(faces)
        states = consent.step(t, faces, tids, fr["hands"], tracker)
        dets = [{"tid": tid, "box": f["box"], "visible": states[tid]["visible"],
                 "sim": states[tid]["sim"], "hold": states[tid]["hold"]} for f, tid in zip(faces, tids)]
        new_ev = consent.events[n_ev:]
        n_ev = len(consent.events)
        out_q.put(("res", i, dets, fr["hands"], fr["ms"], new_ev, len(consent.memory)))
    P.close()


class Live:
    def __init__(self, cfg, source, realtime_file=False):
        self.cfg = cfg
        self.source = source
        self.realtime_file = realtime_file
        self.lock = threading.Lock()
        self.frames = {}      # idx -> (t, img, arrival)
        self.logic = {}       # idx -> dets   (only for perceived frames)
        self.hands = {}       # idx -> hands
        self.objs = {}        # idx -> object dets (screens), only for frames the object detector ran on
        self.obj_ms, self.n_obj = 0.0, 0
        self.latest = -1      # newest captured idx
        self.done = -1        # newest perceived idx
        self.ended = False    # source exhausted
        self.stop = False
        self.ms = {"faces": 0.0, "hands": 0.0}
        self.fps_in = 30.0
        self.ready = threading.Event()
        import multiprocessing as mp
        ctx = mp.get_context("spawn")
        self.req_ev = ctx.Event()
        self.in_q, self.out_q, self.cmd_q = ctx.Queue(maxsize=2), ctx.Queue(), ctx.Queue()
        self.proc = ctx.Process(target=_perception_worker,
                                args=(cfg, self.req_ev, self.in_q, self.out_q, self.cmd_q), daemon=True)
        self.events = []      # (t, kind, tid) from the worker
        self.mem_n = 0        # people remembered (worker side)
        self.skipped = 0
        self.banner = None    # (text, until)
        self.status = None    # persistent status (e.g. reconnecting)
        self.profile = None   # (name, mtime)
        self.trace = None
        self.demo = False     # full-screen, filtered-only output for public demos     # optional dict j -> (regions, dets) for live evaluation
        self.lag = 0.0
        self.vhud = VoiceHUD()   # voice hook: listening / heard / change-card overlay from profiles/VOICE_STATE.json

    # ---------- threads ----------
    def _open(self):
        src = str(self.source)
        if src.startswith("cam:"):                 # camera by name, e.g. cam:Mentra Camera — never falls back
            if find_camera(src[4:]) is None:
                return None
            # open BY NAME via ffmpeg: OpenCV's camera numbering can differ from ffmpeg's after a replug,
            # which once opened the FaceTime camera instead of the glasses
            return NamedCamera(src[4:])
        cap = cv2.VideoCapture(int(src)) if src.isdigit() else cv2.VideoCapture(src, cv2.CAP_FFMPEG)
        if src.isdigit():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        return cap

    def is_stream(self):
        return "://" in str(self.source)

    def is_live(self):
        """Sources that should reconnect forever instead of ending: network streams and cameras."""
        s = str(self.source)
        return self.is_stream() or s.isdigit() or s.startswith("cam:")

    def _waiting_text(self, again):
        if self.is_stream():
            return "RECONNECTING TO GLASSES…" if again else "WAITING FOR GLASSES STREAM…"
        name = str(self.source)[4:] if str(self.source).startswith("cam:") else "camera"
        return f"RECONNECTING TO {name.upper()}…" if again else f"WAITING FOR {name.upper()}…"

    def capture(self):
        """Reads frames forever. Network streams (glasses) reconnect automatically when they drop."""
        file_fps = 30.0
        t0 = time.monotonic()
        idx = 0
        stamps = []
        cap = None
        while not self.stop:
            if cap is None:
                cap = self._open()
                if cap is None or not cap.isOpened():
                    if cap is not None:
                        cap.release()
                    cap = None
                    if self.is_live():
                        self.status = self._waiting_text(idx > 0)
                        time.sleep(1.0)
                        continue
                    print(f"!! could not open source {self.source}")
                    break
                file_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
                if self.is_live():
                    print("source connected" if idx == 0 else "source reconnected")
                stamps.clear()                  # outage must not drag the fps estimate (and memory cap) down
                self.status = None
            ok, img = cap.read()
            if not ok:
                cap.release()
                cap = None
                if self.is_live():
                    print("source dropped — reconnecting")
                    self.status = self._waiting_text(True)
                    time.sleep(0.3)
                    continue
                break
            now = time.monotonic()
            if self.realtime_file:                     # pace a video file like a live camera
                due = t0 + idx / file_fps
                if due > now:
                    time.sleep(due - now)
                now = time.monotonic()
            stamps.append(now)
            self.n_captured = idx + 1
            if idx == 0:
                self.cap_t0 = now
            self.cap_t1 = now
            if len(stamps) > 30:
                stamps.pop(0)
                self.fps_in = (len(stamps) - 1) / max(1e-6, stamps[-1] - stamps[0])
            with self.lock:
                self.frames[idx] = (now - t0, img, now)
                self.latest = idx
                # hard memory cap: never hold more than buffer + 2 s of images
                cap_n = int(self.fps_in * (self.cfg["timeline"]["buffer_s"] + 2.0)) + 10
                while len(self.frames) > cap_n:
                    del self.frames[min(self.frames)]
            if self.req_ev.is_set():
                self.req_ev.clear()
                self.in_q.put((idx, now - t0, img))
            idx += 1
        if cap is not None:
            cap.release()
        self.ended = True

    def receive(self):
        import queue
        while not self.stop:
            try:
                msg = self.out_q.get(timeout=0.2)
            except queue.Empty:
                if self.ended and not self.proc.is_alive():
                    break
                continue
            if msg[0] == "ready":
                self.ready.set()
                continue
            if msg[0] == "obj":
                self.obj_ms = 0.8 * self.obj_ms + 0.2 * msg[3] if self.n_obj else msg[3]
                self.n_obj += 1
                with self.lock:
                    self.objs[msg[1]] = msg[2]
                continue
            _, i, dets, hands, ms, new_ev, mem_n = msg
            self.ms = {k: 0.8 * self.ms[k] + 0.2 * v for k, v in ms.items()}
            for t, kind, tid in new_ev:
                say("Got it, you're visible" if kind == "consent" else "Blurred again")
                self.banner = ("VISIBLE - consent received" if kind == "consent" else "BLURRED AGAIN",
                               time.monotonic() + 2.5)
            self.events += new_ev
            self.mem_n = mem_n
            self.n_perceived = getattr(self, "n_perceived", 0) + 1
            with self.lock:
                self.logic[i] = dets
                self.hands[i] = hands
                self.done = i

    # ---------- output ----------
    def regions_for(self, j, size):
        fps = self.fps_in
        tl = self.cfg["timeline"]
        back = int(fps * (tl["hold_miss_s"] + tl["fade_s"] + 1.0))
        ahead = int(fps * tl["buffer_s"]) + 1
        with self.lock:
            a = max(0, j - back)
            b = min(max(j, self.done), j + ahead)   # never look further than the buffer (bounded cost)
            win = [self.logic.get(i, []) for i in range(a, b + 1)]
            perceived = {i: len(self.logic[i]) for i in range(a, b + 1) if i in self.logic}
            owin = [self.objs.get(i, []) for i in range(a, b + 1)] if self.objs else None
        regs = timeline.build(win, fps, tl, self.cfg["render"], size, only=j - a)[j - a]
        if owin:
            from .objects import object_regions
            regs = regs + object_regions(owin, fps, self.cfg, size)[j - a]
        if self._dropout(j, perceived, fps):
            W, H = size
            regs = regs + [{"tid": -1, "box": [-W, -H, 2 * W, 2 * H], "blur": 1.0}]
        return regs

    def _dropout(self, j, perceived, fps):
        """Fail closed on whip pans / motion blur: every face vanished at once but faces are seen again
        within the buffer -> it was the detector, not an empty scene. Blur the whole frame meanwhile."""
        span = int(fps * self.cfg["timeline"].get("dropout_max_s", 0.6))
        before = [i for i in perceived if i <= j]
        if not before or perceived[max(before)] > 0:
            return False                               # faces visible right now (or nothing seen yet)
        prev = [i for i in perceived if j - span <= i < j and perceived[i] > 0]
        nxt = [i for i in perceived if j < i <= j + span and perceived[i] > 0]
        return bool(prev) and bool(nxt)

    def _badge(self, img, j):
        with self.lock:
            k = j
            while k >= 0 and k not in self.logic and j - k < 15:
                k -= 1
            dets = self.logic.get(k, [])
        vis = sum(1 for d in dets if d["visible"])
        prof = self.profile[0] if self.profile else "default"
        txt = f"PRIVACY FILTER ON  |  profile: {prof}  |  {len(dets) - vis} blurred, {vis} visible"
        (tw, th), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)
        ov = img.copy()
        cv2.rectangle(ov, (16, 16), (16 + tw + 44, 16 + th + 24), (20, 20, 20), -1)
        img = cv2.addWeighted(ov, 0.65, img, 0.35, 0)
        cv2.circle(img, (34, 16 + (th + 24) // 2), 8, (60, 200, 60), -1)
        cv2.putText(img, txt, (50, 16 + th + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
        return img

    def _show_status(self):
        """Window stays alive while the stream is down; nothing new is shown, so nothing can leak."""
        img = getattr(self, "_status_img", None)
        if img is None:
            img = np.zeros((360, 900, 3), np.uint8)
        img = img.copy()
        cv2.rectangle(img, (0, 0), (img.shape[1], 70), (0, 0, 160), -1)
        cv2.putText(img, self.status, (20, 48), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3, cv2.LINE_AA)
        cv2.imshow(WIN, img)

    def check_profile(self, force=False):
        """Follow profiles/ACTIVE; apply on change (the MCP server's activate_profile lands here)."""
        name = PR.active_name()
        if not name:
            return
        try:
            mt = PR.path(name).stat().st_mtime
        except FileNotFoundError:
            return
        if not force and self.profile == (name, mt):
            return
        try:
            prof = PR.load(name)
        except Exception as e:
            print("!! bad profile", name, e)
            self.profile = (name, mt)
            return
        PR.to_cfg(prof, self.cfg)
        self.cmd_q.put(("config", dict(self.cfg["consent"]), dict(self.cfg.get("objects", {}))))
        first = self.profile is None
        self.profile = (name, mt)
        print(f"profile: {name} — {prof['description']}")
        if not first and not self.vhud.recent():   # voice hook: the voice card + read-back replace banner/say
            self.banner = (f"PROFILE: {name}", time.monotonic() + 4)
            say(f"Profile {name.replace('-', ' ')} active")

    def run(self, headless=False, out_path=None, debug=True, seconds=None):
        delay = self.cfg["timeline"]["buffer_s"]
        self.check_profile(force=True)       # apply active profile before the worker reads its queue
        self.proc.start()
        threading.Thread(target=self.receive, daemon=True).start()
        print("loading models…")
        self.ready.wait()
        if not headless and self.demo:
            cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
            cv2.setWindowProperty(WIN, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
        threading.Thread(target=self.capture, daemon=True).start()
        print(f"running · delay {delay:.1f}s · keys: q quit, f forget, space photo, r record, d debug")
        OUT.mkdir(exist_ok=True)
        writer, rec_path = None, None
        if out_path:
            rec_path = Path(out_path)
        j = 0
        shown = 0
        out_times = []
        work_ms = []
        t_start = time.monotonic()
        while True:
            if self.stop or (seconds and time.monotonic() - t_start > seconds):
                break
            if os.environ.get("PF_TRACE") and time.monotonic() - getattr(self, "_last_tr", 0) > 1:
                self._last_tr = time.monotonic()
                print(f"[trace] t={time.monotonic() - t_start:5.1f} j={j} latest={self.latest} done={self.done} "
                      f"frames={len(self.frames)} shown={shown} status={self.status}")
            if time.monotonic() - getattr(self, "_last_pcheck", 0) > 0.5:
                self._last_pcheck = time.monotonic()
                self.check_profile()
            with self.lock:
                item = self.frames.get(j)
                latest = self.latest
            if item is None:
                if self.ended and j > latest:
                    break
                if latest > j:                 # frame j was dropped (memory cap / reconnect): resume at oldest kept
                    with self.lock:
                        later = [k for k in self.frames if k > j]
                    if later:
                        j = min(later)
                        self.skipped += 1
                        continue
                if not headless and self.status:
                    self._show_status()
                    if cv2.waitKey(30) & 0xFF == ord("q"):
                        break
                else:
                    time.sleep(0.003)
                continue
            t, img, arrival = item
            # falling behind? jump to the newest frame that is due (drop, never let delay grow)
            now = time.monotonic()
            if now > arrival + delay + 0.15 and not self.ended:
                with self.lock:
                    due = [k for k, v in self.frames.items() if k > j and v[2] + delay <= now]
                if due:
                    j = max(due)
                    self.skipped += 1
                    continue
            # wait until frame j is `delay` old (or source ended and perception caught up)
            if time.monotonic() < arrival + delay and not (self.ended and self.done >= latest):
                if not headless and cv2.waitKey(1) & 0xFF == ord("q"):
                    break
                time.sleep(0.002)
                continue
            self.lag = time.monotonic() - arrival
            w0 = time.perf_counter()
            H, W = img.shape[:2]
            regs = self.regions_for(j, (W, H))
            filt = R.render(img, regs, self.cfg["render"])
            if self.trace is not None:
                with self.lock:
                    k = j
                    while k >= 0 and k not in self.logic and j - k < 15:
                        k -= 1
                    self.trace[j] = (regs, self.logic.get(k, []))
            frame_out = filt
            if debug:
                with self.lock:
                    # nearest perceived frame at or before j for the debug overlay
                    k = j
                    while k >= 0 and k not in self.logic and j - k < 15:
                        k -= 1
                    dets, hands = self.logic.get(k, []), self.hands.get(k, [])
                # demo mode: the debug overlay is drawn on the FILTERED frame, so raw faces never reach the screen
                dbg = R.draw_debug((filt if self.demo else img).copy(), dets, regs, hands)
                vis = sum(1 for d in dets if d["visible"])
                hud = (f"in {self.fps_in:4.1f}fps  faces {self.ms['faces']:4.0f}ms  hands {self.ms['hands']:4.0f}ms  "
                       f"lag {self.lag:.1f}s (target {delay:.1f})  skips {self.skipped}  visible {vis}/{len(dets)}  remembered {self.mem_n}")
                cv2.putText(dbg, hud, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA)
                if writer is not None:
                    cv2.circle(dbg, (W - 20, 20), 8, (0, 0, 255), -1)
                frame_out = np.hstack([dbg, filt])

            if rec_path and writer is None:
                writer = cv2.VideoWriter(str(rec_path), cv2.VideoWriter_fourcc(*"mp4v"),
                                         round(self.fps_in) or 30, (frame_out.shape[1], frame_out.shape[0]))
            if writer is not None:
                writer.write(frame_out)

            if headless:
                self.vhud.snap(frame_out)   # voice hook: PF_VOICE_SNAP=<dir> saves overlay stills
            if not headless:
                disp = frame_out
                if self.demo and not debug:
                    disp = self._badge(disp.copy(), j)
                if self.banner and time.monotonic() < self.banner[1]:
                    disp = disp.copy()
                    txt = self.banner[0]
                    (tw, th), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 1.6, 4)
                    x = disp.shape[1] - W // 2 - tw // 2 if debug else (W - tw) // 2
                    cv2.rectangle(disp, (x - 20, H - th - 60), (x + tw + 20, H - 20), (40, 160, 40), -1)
                    cv2.putText(disp, txt, (x, H - 40), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (255, 255, 255), 4, cv2.LINE_AA)
                disp = self.vhud.draw(disp)   # voice hook
                if disp.shape[1] > 1800 and not self.demo:
                    s = 1800 / disp.shape[1]
                    disp = cv2.resize(disp, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
                cv2.imshow(WIN, disp)
                self._status_img = disp
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q") or cv2.getWindowProperty(WIN, cv2.WND_PROP_VISIBLE) < 1:
                    break
                if key == ord("f"):
                    self.cmd_q.put("forget")
                    say("Forgot everyone")
                if key == ord(" "):
                    p = OUT / f"photo_{time.strftime('%H%M%S')}.jpg"
                    cv2.imwrite(str(p), filt)
                    say("Saved")
                    print("saved", p)
                if key == ord("r"):
                    if writer is None:
                        rec_path = OUT / f"clip_{time.strftime('%H%M%S')}.mp4"
                        print("recording", rec_path)
                    else:
                        writer.release()
                        writer, rec_path = None, None
                        print("recording stopped")
                if key == ord("d"):
                    debug = not debug
                    if writer is not None:      # frame size changes; start a new file
                        writer.release()
                        writer = None
                        rec_path = OUT / f"clip_{time.strftime('%H%M%S')}.mp4"

            work_ms.append((time.perf_counter() - w0) * 1000)
            # images are only needed until shown; detections are kept for the smoothing window
            with self.lock:
                for k in [k for k in self.frames if k <= j]:
                    del self.frames[k]
                cutoff = j - int(self.fps_in * (self.cfg["timeline"]["hold_miss_s"] + self.cfg["timeline"]["fade_s"] + 1.5))
                for d in (self.logic, self.hands, self.objs):
                    for k in [k for k in d if k < cutoff]:
                        del d[k]
            j += 1
            shown += 1
            out_times.append(time.monotonic())
        self.stop = True
        if writer is not None:
            writer.release()
            print("wrote", rec_path)
        if not headless:
            cv2.destroyAllWindows()
        if len(out_times) > 60:
            d = np.diff(out_times[30:]) * 1000
            print(f"output: {1000 / d.mean():.1f} fps · frame gap p50 {np.percentile(d, 50):.0f}ms "
                  f"p95 {np.percentile(d, 95):.0f}ms max {d.max():.0f}ms · gaps >50ms: {(d > 50).sum()} · "
                  f"work p50 {np.percentile(work_ms, 50):.0f}ms p95 {np.percentile(work_ms, 95):.0f}ms · skips {self.skipped} · "
                  f"faces {self.ms['faces']:.0f}ms hands {self.ms['hands']:.0f}ms · "
                  f"detection updates {getattr(self, 'n_perceived', 0) / max(1e-6, out_times[-1] - t_start):.1f}/s")
        if self.n_obj:
            print(f"screens: detector {self.obj_ms:.0f}ms · {self.n_obj / max(1e-6, time.monotonic() - t_start):.1f} runs/s")
        if getattr(self, "cap_t0", None):
            print(f"capture: {self.n_captured / max(1e-6, self.cap_t1 - self.cap_t0):.1f} fps")
        n_c = sum(1 for e in self.events if e[1] == "consent")
        print(f"session: {shown} frames out, {n_c} consents, {self.mem_n} remembered -> wiped")
        try:
            self.in_q.put_nowait(None)    # worker exits; its memory dies with the process
        except Exception:
            pass
        self.proc.join(timeout=3)
        if self.proc.is_alive():
            self.proc.terminate()
        for q in (self.in_q, self.out_q, self.cmd_q):   # don't block interpreter exit on undelivered frames
            q.cancel_join_thread()


def live_settings(cfg, overrides=()):
    """Live-only perception/timeline settings (offline DEFAULTS and their caches stay untouched).
    Shared with eval.live_eval so it scores exactly what runs live."""
    p = cfg["perception"]
    p.setdefault("hand_max_w", 640)
    p.setdefault("ort_threads", 3)       # leave cores for capture + display
    p.setdefault("parallel", True)       # faces + hands at the same time
    p.setdefault("det_long", DET_LONG)   # aspect-preserving detector input: small faces in crowds
    p.setdefault("embed", dict(EMBED))   # lazy ArcFace (see tracker.select_embeds)
    cfg["timeline"]["hold_miss_s"] = max(cfg["timeline"]["hold_miss_s"], 1.0)   # live sees fewer frames
    if not any(o.startswith("perception.det_thresh=") for o in overrides):
        p["det_thresh"] = 0.35           # catch half-faces at frame edges (fail closed)
    p.setdefault("hand_face_filter", {"max_score": 0.5, "min_cover": 0.3})   # ...but not hands
    return cfg


def main():
    import os
    import sys
    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay")
    sys.stdout.reconfigure(line_buffering=True)   # stats survive window-close / Ctrl-C when piped
    ap = argparse.ArgumentParser(prog="python -m privacy_filter.live")
    ap.add_argument("--source", default="0", help="webcam index, rtmp/srt URL, or video file")
    ap.add_argument("--config")
    ap.add_argument("--set", nargs="*", default=[], help="dotted overrides, e.g. timeline.buffer_s=1.5")
    ap.add_argument("--delay", type=float, help="output delay / lookahead in seconds (default from config)")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--out", help="record output video to this path")
    ap.add_argument("--seconds", type=float)
    ap.add_argument("--no-debug", action="store_true")
    ap.add_argument("--demo", action="store_true", help="full-screen, filtered-only output (safe for a big screen)")
    ap.add_argument("--profile", help="activate this profile (default: profiles/ACTIVE)")
    a = ap.parse_args()
    cfg = live_settings(C.load(a.config, a.set), a.set)
    if a.delay is not None:
        cfg["timeline"]["buffer_s"] = a.delay
    PR.ensure_default()
    if a.profile:
        PR.activate(a.profile)
    is_file = Path(a.source).exists()
    live = Live(cfg, a.source, realtime_file=is_file)
    import signal
    signal.signal(signal.SIGINT, lambda *_: setattr(live, "stop", True))   # Ctrl-C -> clean shutdown + stats
    live.demo = a.demo
    live.run(a.headless, a.out, not (a.no_debug or a.demo), a.seconds)


if __name__ == "__main__":
    main()
