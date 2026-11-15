"""profiles/VOICE_STATE.json: written by the voice tool, polled by the live app (voice_hud).
No cv2 here: the voice process also loads PyAV (faster-whisper), and both bundle libavdevice.

  {"seq": 3, "t": 1731670000.0, "phase": "done", "heard": "also blur screens", "source": "voice",
   "profile": "voice", "lines": [["+", "blur screens"], ["~", "blur medium -> strong"]],
   "refusals": ["license plates"], "note": ""}
phase: listening | thinking | done | error. `t` is wall-clock (writer and reader are different processes).
"""
import json
import time

from . import profiles as PR

STATE = PR.DIR / "VOICE_STATE.json"


def write_state(**kw):
    """Fields not given are cleared. Atomic, so the live app never reads half a file."""
    prev = read_state() or {}
    st = {"seq": prev.get("seq", 0) + 1, "t": time.time(), **kw}
    PR.DIR.mkdir(exist_ok=True)
    PR._write_atomic(STATE, json.dumps(st))
    return st


def read_state():
    try:
        return json.loads(STATE.read_text())
    except (FileNotFoundError, ValueError):
        return None
