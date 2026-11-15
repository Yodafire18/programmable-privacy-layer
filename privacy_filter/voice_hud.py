"""Voice overlay for the live app. The voice tool (privacy_filter.voice) writes profiles/VOICE_STATE.json;
the live app polls it (like profiles/ACTIVE) and draws:

  listening  pulsing red dot + "LISTENING"                  while the mic records
  thinking   "You: '...'" caption + "THINKING" with animated dots
  done       change card (only when something changed / was refused) + the assistant's spoken reply as a
             caption ("You: ..." / "Glasses: ...") for CARD_S seconds
  idle       nothing

State file format: see voice_state.py.
"""
import math
import os
import time

import cv2

from .voice_state import STATE, read_state
CARD_S = 8.0
STALE_S = {"listening": 12.0, "thinking": 40.0, "done": CARD_S, "error": 4.0}
ACCENT = (255, 200, 120)        # BGR: soft blue for the assistant
FONT = cv2.FONT_HERSHEY_DUPLEX
COL = {"+": (90, 220, 90), "-": (90, 90, 255), "~": (60, 210, 255), "=": (230, 230, 230)}
RED = (60, 60, 235)


class VoiceHUD:
    """Tiny poller + renderer; all the live app does is call draw(), recent() and snap()."""

    def __init__(self, path=STATE):
        self.path = path
        self.state = None
        self._mt = None
        self._last = 0.0
        self._snapped = set()
        self.snap_dir = os.environ.get("PF_VOICE_SNAP")     # headless test hook: save overlay stills here

    def poll(self):
        now = time.monotonic()
        if now - self._last < 0.2:
            return self.state
        self._last = now
        try:
            mt = os.stat(self.path).st_mtime
        except FileNotFoundError:
            self.state = None
            return None
        if mt != self._mt:
            self._mt = mt
            self.state = read_state()
        return self.state

    def visible(self):
        st = self.poll()
        if not st:
            return None
        age = time.time() - st.get("t", 0)
        return st if age < STALE_S.get(st.get("phase"), 0) else None

    def recent(self, s=3.0):
        """True right after a voice change: the live app then skips its own 'Profile X active' banner/speech
        (the voice tool already speaks a read-back and the card shows the change)."""
        self._last = 0.0                     # fresh read: the profile change and the state write are ~ms apart
        st = self.poll()
        return bool(st) and st.get("phase") in ("done", "thinking") and time.time() - st.get("t", 0) < s

    def draw(self, img):
        st = self.visible()
        if not st:
            return img
        img = img.copy()
        ph = st.get("phase")
        if ph == "listening":
            _pill(img, "LISTENING", dot=True)
        elif ph == "thinking":
            dots = "." * (1 + int(time.time() * 3) % 3)
            _pill(img, "THINKING" + dots.ljust(3), dot=False, spinner=True)
            _caption(img, st)
        elif ph == "error":
            _pill(img, st.get("note") or "TRY AGAIN", dot=False, col=(40, 40, 160))
        elif ph == "done":
            if st.get("lines") or st.get("refusals") or "reply" not in st:
                _card(img, st)
            _caption(img, st, reply=True)
        return img

    def snap(self, img):
        """PF_VOICE_SNAP=<dir>: write one still per (seq, phase) with the overlay drawn (headless verification)."""
        if not self.snap_dir:
            return
        st = self.visible()
        if not st or (st["seq"], st.get("phase")) in self._snapped:
            return
        self._snapped.add((st["seq"], st.get("phase")))
        os.makedirs(self.snap_dir, exist_ok=True)
        p = os.path.join(self.snap_dir, f"voice_{st['seq']:03d}_{st.get('phase')}.jpg")
        cv2.imwrite(p, self.draw(img))
        print("voice overlay still:", p)


def _scale(img):
    return max(0.6, img.shape[1] / 1280)


def _box(img, x0, y0, x1, y1, col=(25, 25, 25), alpha=0.78):
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(img.shape[1], x1), min(img.shape[0], y1)
    roi = img[y0:y1, x0:x1]
    if roi.size:
        bg = roi.copy()
        bg[:] = col
        img[y0:y1, x0:x1] = cv2.addWeighted(bg, alpha, roi, 1 - alpha, 0)


def _text(img, txt, x, y, s, col=(255, 255, 255), th=2):
    cv2.putText(img, txt, (x, y), FONT, s, col, max(1, int(th)), cv2.LINE_AA)


def _fit(txt, s, maxw, th):
    """Shorten text so it fits maxw pixels."""
    while txt and cv2.getTextSize(txt, FONT, s, th)[0][0] > maxw:
        txt = txt[:-4] + "..." if len(txt) > 4 else ""
    return txt


def _wrap(txt, s, maxw, th, max_lines=2):
    """Word-wrap into at most max_lines lines (the last one shortened with ...)."""
    lines, cur = [], ""
    for w in txt.split(" "):
        cand = f"{cur} {w}" if cur else w
        if cur and cv2.getTextSize(cand, FONT, s, th)[0][0] > maxw:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines - 1] + [" ".join(lines[max_lines - 1:])]
    return [_fit(ln, s, maxw, th) for ln in lines]


def _pill(img, txt, dot=True, col=(30, 30, 30), spinner=False):
    s = _scale(img)
    fs, th = 1.3 * s, max(2, int(3 * s))
    (tw, tht), _ = cv2.getTextSize(txt, FONT, fs, th)
    pad = int(22 * s)
    r = int(14 * s)
    w = tw + 2 * pad + (2 * r + pad // 2 if dot or spinner else 0)
    x0 = (img.shape[1] - w) // 2
    y0 = int(90 * s)
    _box(img, x0, y0, x0 + w, y0 + tht + 2 * pad, col=col, alpha=0.85)
    x = x0 + pad
    c = (x + r, y0 + pad + tht // 2)
    if dot:                                            # breathing dot with a soft expanding ring
        ph = (time.time() * 1.6) % 1.0
        k = 0.5 + 0.5 * math.sin(time.time() * 2 * math.pi * 1.2)
        cv2.circle(img, c, int(r * (1.0 + 1.2 * ph)), (60, 60, int(255 * (1 - ph))), max(1, int(2 * s)), cv2.LINE_AA)
        cv2.circle(img, c, int(r * (0.75 + 0.25 * k)), (40, 40, int(170 + 85 * k)), -1, cv2.LINE_AA)
        x += 2 * r + pad // 2
    elif spinner:                                      # rotating arc
        a0 = int(time.time() * 360) % 360
        cv2.ellipse(img, c, (r, r), a0, 0, 270, ACCENT, max(2, int(3 * s)), cv2.LINE_AA)
        x += 2 * r + pad // 2
    _text(img, txt, x, y0 + pad + tht, fs, th=th)


def _caption(img, st, reply=False):
    """Bottom-centre subtitles: what you said, and (once done) what the glasses answered."""
    heard = st.get("heard") or ""
    s = _scale(img)
    fs, th = 1.1 * s, max(2, int(2.4 * s))
    W, H = img.shape[1], img.shape[0]
    maxw = W - int(160 * s)
    rows = []
    if heard:
        rows += [(ln, (255, 255, 200)) for ln in _wrap(f"You: \"{heard}\"", fs, maxw, th, 1)]
    if reply and st.get("reply"):
        rows += [(ln, ACCENT) for ln in _wrap(f"Glasses: {st['reply']}", fs, maxw, th, 3)]
    if not rows:
        return
    lh = int(cv2.getTextSize("Ag", FONT, fs, th)[0][1] * 1.7)
    tw = max(cv2.getTextSize(t, FONT, fs, th)[0][0] for t, _ in rows)
    pad = int(18 * s)
    x0 = (W - tw) // 2 - pad
    y1 = H - int(110 * s)
    y0 = y1 - lh * len(rows) - 2 * pad + int(lh * 0.3)
    _box(img, x0, y0, x0 + tw + 2 * pad, y1, alpha=0.85)
    y = y0 + pad + int(lh * 0.7)
    for t, col in rows:
        _text(img, t, x0 + pad, y, fs, col, th)
        y += lh


def _card(img, st):
    """Change card, right side, large type: readable from a few metres on a projector."""
    s = _scale(img)
    W, H = img.shape[1], img.shape[0]
    fh, fs, fb = 1.15 * s, 1.0 * s, 0.85 * s
    th = max(2, int(2.4 * s))
    pad = int(20 * s)
    lh = int(46 * s)
    cw = min(int(620 * s), W - 2 * pad)
    rows = []
    rows.append((f"{'TYPED' if st.get('source') == 'typed' else 'VOICE'}  >  profile: {st.get('profile', '?')}",
                 fh, (255, 255, 255)))
    if st.get("heard") and not st.get("reply"):          # with a reply, the caption shows it
        rows.append((f"\"{st['heard']}\"", fb, (200, 200, 170)))
    for r in st.get("refusals") or []:
        rows.append((f"x  can't do: {r}", fs, RED))
    for sign, t in st.get("lines") or []:
        mark = {"+": "+ ", "-": "- ", "~": "~ ", "=": "  "}.get(sign, "  ")
        rows.append((mark + t, fs, COL.get(sign, (230, 230, 230))))
    if not st.get("lines"):
        rows.append((st.get("note") or ("no other change" if st.get("refusals") else "no change"), fs, (200, 200, 200)))
    rows = [(ln, f, col) for txt, f, col in rows for ln in _wrap(txt, f, cw - 3 * pad, th)][:10]
    x0 = W - cw - pad
    y0 = int(80 * s)
    hgt = pad * 2 + lh * len(rows)
    _box(img, x0, y0, x0 + cw, y0 + hgt, alpha=0.82)
    cv2.rectangle(img, (x0, y0), (x0 + int(8 * s), y0 + hgt),
                  RED if st.get("refusals") and not st.get("lines") else (90, 200, 90), -1)
    y = y0 + pad + int(lh * 0.72)
    for txt, f, col in rows:
        _text(img, _fit(txt, f, cw - 3 * pad, th), x0 + 2 * pad, y, f, col, th)
        y += lh
    # remaining-time bar
    left = max(0.0, 1 - (time.time() - st.get("t", 0)) / CARD_S)
    cv2.rectangle(img, (x0, y0 + hgt - int(5 * s)), (x0 + int(cw * left), y0 + hgt), (120, 120, 120), -1)
