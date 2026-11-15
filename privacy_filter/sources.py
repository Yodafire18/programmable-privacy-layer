"""Frame sources. All yield (t_seconds, bgr_frame). Live and replay share the same pipeline."""
import time
from pathlib import Path

import cv2

IMG_EXT = {".jpg", ".jpeg", ".png", ".heic", ".webp"}


def video_info(path):
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return {"fps": fps, "frames": n, "width": w, "height": h}


def file_frames(path):
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    i = 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        yield i / fps, f
        i += 1
    cap.release()


def webcam_frames(index=0, width=1280, height=720):
    cap = cv2.VideoCapture(index)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    t0 = time.monotonic()
    try:
        while True:
            ok, f = cap.read()
            if not ok:
                break
            yield time.monotonic() - t0, f
    finally:
        cap.release()


def stream_frames(url):
    """RTMP/RTSP/HTTP stream (e.g. Mentra Live -> local RTMP server)."""
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    t0 = time.monotonic()
    while True:
        ok, f = cap.read()
        if not ok:
            break
        yield time.monotonic() - t0, f
    cap.release()


def folder_images(path):
    for p in sorted(Path(path).iterdir()):
        if p.suffix.lower() in IMG_EXT:
            img = cv2.imread(str(p))
            if img is not None:
                yield p, img
