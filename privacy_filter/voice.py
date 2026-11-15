"""Voice commands -> live privacy profile.

  trigger (Enter in this terminal, or a glasses button via adb)
    -> record "Mentra Microphone" until silence (max ~6 s)
    -> speech-to-text on the Mac (faster-whisper, local)
    -> intent: fast local parser for common phrasings, else `claude -p` + our MCP server
    -> profile `voice` saved + activated (the live app reloads within ~0.5 s)
    -> read-back built from the validated profile + diff (never from LLM prose): Mac `say` + card in the live app

Rules: `default` is locked (gold) and never edited; voice may switch back to it. Edits go to `voice`, built on top
of whatever is active right now, and are activated at once. "undo" restores the previous `voice` version.

Run (own terminal, next to ./demo.sh):
  .venv/bin/python -m privacy_filter.voice                 # Enter = talk; or type a command + Enter
  .venv/bin/python -m privacy_filter.voice --adb           # also: any glasses button press = talk
  .venv/bin/python -m privacy_filter.voice --text "also blur screens"
  .venv/bin/python -m privacy_filter.voice --audio-file cmd.aiff
"""
import argparse
import atexit
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np

from . import profiles as PR
from .voice_state import write_state

ROOT = PR.ROOT
SR = 16000
MIC_NAME = "Mentra Microphone"
PY = sys.executable
GESTURE_WORDS = {   # spoken -> profile gesture
    "thumbs up": "thumbs_up", "thumb up": "thumbs_up", "thumbs-up": "thumbs_up",
    "thumbs down": "thumbs_down", "thumb down": "thumbs_down", "thumbs-down": "thumbs_down",
    "peace sign": "peace_sign", "peace": "peace_sign", "victory": "peace_sign", "v sign": "peace_sign",
    "open palm": "open_palm", "open hand": "open_palm", "palm": "open_palm", "high five": "open_palm",
    "wave": "open_palm", "fist": "fist", "closed fist": "fist",
    "pointing up": "pointing_up", "point up": "pointing_up", "finger up": "pointing_up",
    "i love you": "i_love_you", "rock on": "i_love_you",
}
SAY_GESTURE = {"peace_sign": ("peace sign", "peace signs"), "thumbs_up": ("thumbs up", "thumbs up"),
               "thumbs_down": ("thumbs down", "thumbs down"), "open_palm": ("open palm", "open palms"),
               "fist": ("fist", "fists"), "pointing_up": ("finger pointing up", "fingers pointing up"),
               "i_love_you": ("I love you sign", "I love you signs")}
SCREEN = r"(screens?|phones?|laptops?|monitors?|tvs?|televisions?|computers?|displays?)"
UNSUPPORTED = {  # spoken pattern -> canonical target name (validate() reports it as "can't do")
    r"licen[cs]e ?plates?|number ?plates?|plates?": "license plates",
    r"(credit |bank )?cards?": "cards", r"documents?|papers?|paperwork": "documents",
    r"x-? ?rays?": "x-rays", r"brands?|logos?|labels?": "brands",
    r"children|kids?|child": "children", r"tattoos?": "tattoos", r"bod(y|ies)": "bodies",
}
NUM = {"one": 1, "single": 1, "1": 1, "two": 2, "both": 2, "2": 2, "double": 2}
FILLER = set("also please and ok okay now hey could can you would i want to like let's lets the it just "
             "then so um uh glasses filter privacy profile camera me for my a an too as well".split())


# ---------------------------------------------------------------- read-back (template over validated profiles)
def _gesture_phrase(r):
    one, many = SAY_GESTURE.get(r["gesture"], (r["gesture"], r["gesture"]))
    return f"{'one' if r['hands'] == 1 else 'two'} {one if r['hands'] == 1 else many}"


def summary(p):
    t = " and ".join(p["blur_targets"]).capitalize()
    return (f"{t}, {p['blur_strength']} blur. {_gesture_phrase(p['consent']).capitalize()} to be seen, "
            f"{_gesture_phrase(p['revoke'])} to hide.")


def summary_lines(p):
    """Short card rows for the whole profile."""
    return [["=", f"blur: {', '.join(p['blur_targets'])} ({p['blur_strength']})"],
            ["=", f"to be seen: {_gesture_phrase(p['consent'])}"],
            ["=", f"to hide: {_gesture_phrase(p['revoke'])}"]]


def diff(old, new):
    """[(sign, card_text, spoken)] of what changed old -> new. sign: + added, - removed, ~ changed."""
    out = []
    if old is None:
        return out
    for t in new["blur_targets"]:
        if t not in old["blur_targets"]:
            out.append(("+", f"blur {t}", f"now blurring {t}"))
    for t in old["blur_targets"]:
        if t not in new["blur_targets"]:
            out.append(("-", f"blur {t}", f"no longer blurring {t}"))
    if old["blur_strength"] != new["blur_strength"]:
        out.append(("~", f"blur {old['blur_strength']} -> {new['blur_strength']}", f"using a {new['blur_strength']} blur"))
    for key, verb in (("consent", "to be seen"), ("revoke", "to hide")):
        a, b = old[key], new[key]
        if (a["gesture"], a["hands"]) != (b["gesture"], b["hands"]):
            out.append(("~", f"{verb}: {_gesture_phrase(a)} -> {_gesture_phrase(b)}",
                        f"{_gesture_phrase(b)} {verb}"))  # e.g. "one thumbs up to be seen"
    if old["consent"].get("hold_s") != new["consent"].get("hold_s"):
        out.append(("~", f"hold {old['consent']['hold_s']:g} s -> {new['consent']['hold_s']:g} s",
                    f"hold it for {new['consent']['hold_s']:g} second{'' if new['consent']['hold_s'] == 1 else 's'}"))
    return out


def short_refusal(problem):
    m = re.match(r"blur target\(s\) not supported yet: (.*?) \(supported", problem)
    if m:
        return m.group(1)
    if problem.startswith("faces are always blurred"):
        return "turning faces off (faces always stay blurred)"
    m = re.match(r"(consent|revoke) gesture '(.*?)' not recognized", problem)
    if m:
        return f"gesture '{m.group(2)}'"
    if "must differ" in problem:
        return "same gesture to be seen and to hide"
    return problem[:60]


def _join(xs):
    xs = list(xs)
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def readback(action, name, prof, changes, refusals, reply=""):
    """What the Mac says out loud: short and natural. Facts come from the validated profile, not the LLM."""
    pname = "the default" if name == "default" else f"the {name.replace('-', ' ')}"
    parts = []
    if action == "status":
        parts.append(f"You're on {pname} profile. {summary(prof)}")
    elif action == "switch":
        parts.append("Back to default." if name == "default" else f"Switched to {pname} profile.")
    elif action == "undo":
        parts.append("Undone." if changes else "Nothing to undo.")
    elif changes:
        parts.append("Done. " + _join(c[2] for c in changes).capitalize() + ".")
    elif reply and action == "none":
        parts.append(reply)                      # Claude's own short answer (a question, a hello, a clarification)
    elif not refusals:
        parts.append("Sorry, I didn't catch a change. Try: also blur screens.")
    if refusals:
        parts.append(f"I can't blur {_join(refusals)} yet.")
    return " ".join(parts)


# ---------------------------------------------------------------- fast local intent path
def normalize(text):
    t = text.lower().replace("’", "'")
    t = re.sub(r"[^a-z0-9' .,-]", " ", t)
    t = re.sub(r"\b2\b", "two", re.sub(r"\b1\b", "one", t))
    return re.sub(r"\s+", " ", t).strip(" .,")


def _find_gesture(c):
    for w in sorted(GESTURE_WORDS, key=len, reverse=True):
        m = re.search(rf"\b(?:(one|two|single|both|double)\s+)?{w}s?\b", c)
        if m:
            return GESTURE_WORDS[w], (NUM.get(m.group(1)) if m.group(1) else None)
    return None, None


def parse_fast(text, names=()):
    """Returns ('undo'|'status'|('switch', name)|('edit', [edits])) or None (-> Claude).
    An utterance is handled locally only if EVERY clause is understood; otherwise Claude gets all of it."""
    t = normalize(text)
    if not t:
        return None
    if re.fullmatch(r"(please )?(undo|undo that|undo it|revert( that)?|take (that|it) back|go back one|never ?mind)( please)?", t):
        return "undo"
    if re.search(r"what'?s (on|active|running)|what (profile|is on|are you (blurring|hiding))|which profile|^status$", t):
        return "status"
    if re.search(r"\b(go )?back to (the )?default\b|\b(use|switch to|activate|reset to) (the )?default\b|^reset$|^default( profile)?$", t):
        return ("switch", "default")
    m = re.fullmatch(r"(?:please )?(?:use|switch to|activate|turn on|load) (?:the )?(.+?)(?: profile)?", t)
    if m:
        try:
            cand = PR.slug(m.group(1))
        except ValueError:
            cand = ""
        if cand in names:
            return ("switch", cand)
    edits = []
    for c in re.split(r",|\band\b|\bthen\b|\balso\b|\bplus\b", t):
        c = c.strip()
        if not c:
            continue
        e = _parse_clause(c)
        if e is None:
            if all(w in FILLER for w in c.split()):
                continue
            return None          # something we don't understand -> Claude
        edits += e
    return ("edit", edits) if edits else None


def _parse_clause(c):
    neg = re.search(r"\b(stop|don'?t|do not|no longer|remove|unblur|quit|not|show|without|off)\b", c)
    if re.search(rf"\b{SCREEN}\b", c):
        return [("screens", not neg)]
    if re.search(r"\bfaces?\b|\beveryone\b|\bpeople\b", c) and neg and not re.search(r"\bstop blurring\b.*\bscreen", c):
        return [("faces", False)]
    for pat, name in UNSUPPORTED.items():
        if re.search(rf"\b(blur|hide|cover|mask|block|add|censor)\s+(?:(?:all|the|any|every|of)\s+)*({pat})$", c):
            return [("target", name)]
    if re.search(r"\b(stronger|more blur|blur more|heavier|harder|more)\b", c) and not re.search(r"\bhold\b", c):
        return [("strength", +1)]
    if re.search(r"\b(lighter|weaker|softer|less blur|blur less|less)\b", c):
        return [("strength", -1)]
    m = re.search(r"\b(light|medium|strong|maximum|max|minimum|min)\b", c)
    if m and re.search(r"\b(blur|strength|make|set)\b", c):
        return [("strength", {"maximum": "strong", "max": "strong", "minimum": "light", "min": "light"}
                 .get(m.group(1), m.group(1)))]
    out = []
    m = re.search(r"\b(\d+(?:\.\d+)?|one|two|three|half a) seconds?\b", c)
    if m and re.search(r"\b(hold|held|for|keep)\b", c):
        v = {"one": 1, "two": 2, "three": 3, "half a": 0.5}.get(m.group(1)) or float(m.group(1))
        out.append(("hold", float(v)))
        c = c[:m.start()] + c[m.end():]
    g, n = _find_gesture(c)
    hands = None
    m = re.search(r"\b(one|single|two|both)[ -]hand(ed)?s?\b|\bhands? (one|two)\b", c)
    if m:
        hands = NUM.get(m.group(1) or m.group(3))
    if g or hands:
        key = "revoke" if re.search(r"\b(hide|revoke|blur (me )?again|go back|withdraw|disappear|stop)\b", c) else "consent"
        out.append((key, g, n or hands))
    return out or None


def apply_edits(base, edits):
    p = json.loads(json.dumps(base))
    p.pop("locked", None)
    order = list(PR.STRENGTH)
    for e in edits:
        k = e[0]
        if k == "screens":
            if e[1] and "screens" not in p["blur_targets"]:
                p["blur_targets"].append("screens")
            if not e[1]:
                p["blur_targets"] = [t for t in p["blur_targets"] if t != "screens"]
        elif k == "faces":
            p["blur_targets"] = [t for t in p["blur_targets"] if t != "faces"]      # validate() puts it back + says so
        elif k == "target":
            p["blur_targets"].append(e[1])                                          # validate() reports it
        elif k == "strength":
            if isinstance(e[1], int):
                i = order.index(p["blur_strength"]) + e[1]
                p["blur_strength"] = order[max(0, min(len(order) - 1, i))]
            else:
                p["blur_strength"] = e[1]
        elif k == "hold":
            p["consent"]["hold_s"] = e[1]
        elif k in ("consent", "revoke"):
            if e[1]:
                p[k]["gesture"] = e[1]
            if e[2]:
                p[k]["hands"] = e[2]
    return p


# ---------------------------------------------------------------- Claude + MCP path
SYSTEM = """You turn one spoken command into an edit of the camera privacy profile named 'voice'.
You are given the profile that is active now. Call save_profile exactly once with name='voice' and ALL fields
(start from the active profile, change only what the user asked). Never use any other profile name: 'default'
is locked. If the user asks to blur something that is not 'faces' or 'screens', still put it in blur_targets
(the engine reports what it can't do). Gestures: peace_sign, thumbs_up, thumbs_down, open_palm, fist,
pointing_up, i_love_you; hands 1 or 2; blur_strength light|medium|strong; hold_seconds = how long the consent
gesture must be held. Do not call any other tool unless the user asks to undo (undo_profile name='voice').
If the user is not asking for a profile change (a greeting, a question, small talk), call nothing and reply
naturally and warmly in at most 15 words, ideally suggesting one example command (e.g. "also blur screens").
After a tool call, reply with at most 6 words. Use the recent conversation for follow-ups like "yes, do that"."""

HISTORY = []          # last few (user, reply) exchanges, so follow-ups make sense


def mcp_config_path():
    cfg = {"mcpServers": {"privacy-filter": {
        "type": "stdio", "command": PY, "args": ["-m", "privacy_filter.mcp_server"],
        "env": {"PYTHONPATH": str(ROOT)}}}}
    p = os.path.join(tempfile.gettempdir(), f"pf_voice_mcp_{os.getuid()}.json")
    with open(p, "w") as f:
        json.dump(cfg, f)
    return p


def run_claude(text, active_prof, model="haiku", timeout=60):
    """Returns (problems, claude_reply, seconds). The saved/activated profile is read back from disk afterwards."""
    convo = "".join(f"User: {u}\nYou: {r}\n" for u, r in HISTORY[-3:])
    prompt = (f"Active profile now:\n{json.dumps(active_prof)}\n\n"
              + (f"Recent conversation:\n{convo}\n" if convo else "")
              + f"User said (speech-to-text, may contain recognition errors): \"{text}\"")
    tools = ["mcp__privacy-filter__save_profile", "mcp__privacy-filter__undo_profile"]
    cmd = ["claude", "-p", prompt, "--mcp-config", mcp_config_path(), "--strict-mcp-config",
           "--tools", "", "--allowedTools", *tools, "--system-prompt", SYSTEM, "--model", model,
           "--output-format", "stream-json", "--verbose", "--no-session-persistence"]
    t0 = time.monotonic()
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    problems, reply = [], ""
    for line in r.stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "result":
            reply = ev.get("result") or ""
        if ev.get("type") == "user":
            for c in ev.get("message", {}).get("content", []):
                if c.get("type") != "tool_result":
                    continue
                body = c.get("content")
                if isinstance(body, list):
                    body = " ".join(x.get("text", "") for x in body if isinstance(x, dict))
                try:
                    d = json.loads(body)
                except (TypeError, ValueError):
                    continue
                problems += d.get("problems") or []
    if r.returncode != 0 and not reply:
        reply = (r.stderr or r.stdout)[-300:]
    elif reply:
        HISTORY.append((text, reply.strip()))
        del HISTORY[:-3]
    return problems, reply.strip(), time.monotonic() - t0


# ---------------------------------------------------------------- audio
def find_mic(name=MIC_NAME):
    """sounddevice index of the first input device whose name contains `name`, else the default input."""
    import sounddevice as sd
    devs = sd.query_devices()
    for i, d in enumerate(devs):
        if d["max_input_channels"] > 0 and name.lower() in d["name"].lower():
            return i, d["name"], int(d["default_samplerate"]), d["max_input_channels"]
    i = sd.default.device[0]
    if i is None or i < 0:
        i = next(k for k, d in enumerate(devs) if d["max_input_channels"] > 0)
    d = devs[i]
    return i, d["name"] + " (fallback: default input)", int(d["default_samplerate"]), d["max_input_channels"]


def resample(x, sr_in, sr_out=SR):
    if sr_in == sr_out:
        return x.astype(np.float32)
    n = int(len(x) * sr_out / sr_in)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


class Endpointer:
    """Energy VAD on mono blocks. Done after `silence_s` of quiet once speech started, at `max_s`, or after
    `no_speech_s` without speech. The noise floor is the quietest block of the first 0.3 s (so speaking right
    away doesn't raise it). Time is counted in samples, so it runs the same on a file as on the mic."""

    def __init__(self, sr, max_s=6.0, silence_s=0.8, no_speech_s=4.0):
        self.sr, self.max_s, self.silence_s, self.no_speech_s = sr, max_s, silence_s, no_speech_s
        self.n, self.floor, self.started, self.quiet, self.peak = 0, None, False, 0.0, 0.0

    def push(self, mono):
        rms = float(np.sqrt(np.mean(mono ** 2)) + 1e-9)
        self.n += len(mono)
        el = self.n / self.sr
        self.peak = max(self.peak, rms)
        if el < 0.3:
            self.floor = rms if self.floor is None else min(self.floor, rms)
        thr = max(0.01, (self.floor or 0.004) * 3.0)
        if rms > thr:
            self.started, self.quiet = True, 0.0
        elif self.started:
            self.quiet += len(mono) / self.sr
        return ((self.started and self.quiet >= self.silence_s) or el >= self.max_s
                or (not self.started and el >= self.no_speech_s))


def record(dev, max_s=6.0, silence_s=0.8, stop=None):
    """Record from `dev` (see find_mic) until the Endpointer says done or stop() returns True (a second
    Enter / button press). Returns (16 kHz mono, speech_seen)."""
    import sounddevice as sd
    idx, name, sr, ch = dev
    q = queue.Queue()
    chunks = []
    ep = Endpointer(sr, max_s, silence_s)
    with sd.InputStream(device=idx, samplerate=sr, channels=min(ch, 2), blocksize=int(sr * 0.03), dtype="float32",
                        callback=lambda d, *_: q.put(d.copy())):
        while True:
            try:
                d = q.get(timeout=0.5)
            except queue.Empty:
                continue
            mono = d.mean(axis=1)
            chunks.append(mono)
            if ep.push(mono) or (stop is not None and stop()):
                break
    return resample(np.concatenate(chunks), sr), ep.started or ep.peak > 0.02


def load_audio_file(path):
    """Any format ffmpeg reads -> 16 kHz mono float32."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.float32)


class STT:
    PROMPT = ("Privacy glasses voice commands: blur screens, stop blurring screens, stronger blur, lighter blur, "
              "thumbs up, peace sign, open palm, one hand, two hands, go back to default, undo, what's on.")

    def __init__(self, model="base.en", threads=2):
        from faster_whisper import WhisperModel
        t0 = time.monotonic()
        self.m = WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=threads)
        self.load_s = time.monotonic() - t0
        self.name = model

    def __call__(self, audio):
        segs, _ = self.m.transcribe(audio, language="en", beam_size=1, initial_prompt=self.PROMPT,
                                    condition_on_previous_text=False, vad_filter=False)
        return " ".join(s.text.strip() for s in segs).strip()


def say(text, block=False):
    if not text or not shutil.which("say"):
        return None
    p = subprocess.Popen(["say", "-r", "200", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if block:
        p.wait()
    return p


# ---------------------------------------------------------------- the command itself
def handle(text, source="voice", use_claude=True, model="haiku", speak=True, log=print):
    """Text -> profile change -> state file + read-back. Returns a result dict (timings included)."""
    t0 = time.monotonic()
    PR.ensure_default()
    write_state(phase="thinking", heard=text, source=source)   # before any profile write (live app skips its banner)
    act_name = PR.active_name() or "default"
    try:
        active = PR.load(act_name)
    except FileNotFoundError:
        act_name, active = "default", PR.load("default")
    names = PR.names()
    intent = parse_fast(text, names)
    res = {"text": text, "path": "fast", "action": None, "profile": None, "changes": [], "refusals": [],
           "note": ""}
    if intent == "status":
        res.update(action="status", profile=act_name)
        prof = active
    elif intent == "undo":
        before = PR.load("voice")
        prof = PR.undo("voice")
        if prof is None:
            prof = before
        PR.activate("voice")
        res.update(action="undo", profile="voice", changes=diff(active if act_name == "voice" else before, prof))
    elif isinstance(intent, tuple) and intent[0] == "switch":
        PR.activate(intent[1])
        prof = PR.load(intent[1])
        res.update(action="switch", profile=intent[1], changes=diff(active, prof) if intent[1] != act_name else [])
    elif isinstance(intent, tuple) and intent[0] == "edit":
        new = apply_edits(active, intent[1])
        new.update(name="voice", description=f"Voice: {text}"[:200])
        prof, problems = PR.validate(new)
        if PR.same_rules(prof, active):          # nothing the engine can do changed (e.g. only a refusal)
            prof, name = active, act_name
        else:
            prof, problems = PR.save(new)
            PR.activate("voice")
            name = "voice"
        res.update(action="edit", profile=name, changes=diff(active, prof),
                   refusals=[short_refusal(p) for p in problems])
    elif use_claude:
        res["path"] = "claude"
        write_state(phase="thinking", heard=text, source=source, note="asking Claude")
        before = PR.load("voice")
        mt0 = PR.path("voice").stat().st_mtime
        problems, reply, cs = run_claude(text, active, model=model)
        res["claude_s"] = round(cs, 2)
        log(f"   claude ({cs:.1f}s): {reply}")
        prof = PR.load("voice")
        changed = PR.path("voice").stat().st_mtime != mt0
        if changed or prof != before:
            PR.activate("voice")
            res.update(action="edit", profile="voice", changes=diff(active, prof))
        else:
            prof = active
            res.update(action="none", profile=act_name, note="no change")
        res["refusals"] = [short_refusal(p) for p in problems]
        res["claude_reply"] = reply
    else:
        prof = active
        res.update(action="none", profile=act_name, note="not understood (fast path only)")
    res["intent_s"] = round(time.monotonic() - t0, 2)
    res["spoken"] = readback(res["action"], res["profile"], prof, res["changes"], res["refusals"],
                             reply=res.get("claude_reply", ""))
    lines = [[c[0], c[1]] for c in res["changes"]]
    if res["action"] in ("status", "switch") and not lines:
        lines = summary_lines(prof)
    if res["action"] == "switch":
        lines.insert(0, ["~", f"switched to '{res['profile']}'"])
    if res["action"] == "undo":
        lines.insert(0, ["~", "undo" if res["changes"] else "nothing to undo"])
    write_state(phase="done", heard=text, source=source, profile=res["profile"], lines=lines,
                refusals=res["refusals"], note=res["note"] or ("no change" if res["action"] == "none" else ""))
    res["live_at"] = time.time()
    if speak:
        res["say_proc"] = say(res["spoken"])
    return res


# ---------------------------------------------------------------- triggers
def adb_listener(trig_q, stop):
    """Glasses buttons over ADB (read-only `getevent`). Any key DOWN event -> start recording."""
    if not shutil.which("adb"):
        print("adb: not installed (brew install --cask android-platform-tools)")
        return
    while not stop.is_set():
        out = subprocess.run(["adb", "devices"], capture_output=True, text=True).stdout
        devs = [l.split() for l in out.splitlines()[1:] if l.strip()]
        if not any(d[1] == "device" for d in devs if len(d) > 1):
            st = ", ".join(" ".join(d) for d in devs) or "no device"
            print(f"adb: glasses not ready ({st}); retrying in 5 s")
            stop.wait(5)
            continue
        print("adb: listening for glasses button presses (getevent)")
        p = subprocess.Popen(["adb", "shell", "getevent", "-l"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True, bufsize=1)
        atexit.register(p.kill)
        last = 0.0
        for line in p.stdout:
            if stop.is_set():
                break
            m = re.search(r"(/dev/input/event\d+):\s+EV_KEY\s+(\S+)\s+DOWN", line)
            if m:
                print(f"adb: button {m.group(2)} on {m.group(1)}")
                if time.monotonic() - last > 1.5:
                    last = time.monotonic()
                    trig_q.put(("button", m.group(2)))
        p.kill()
        if not stop.is_set():
            print("adb: getevent ended (unplugged?); reconnecting")
            stop.wait(2)


def stdin_listener(trig_q):
    for line in sys.stdin:
        trig_q.put(("line", line.rstrip("\n")))
    trig_q.put(("eof", None))


def report(res, stt_s=None, log=print):
    t = f"stt {stt_s:.2f}s · " if stt_s is not None else ""
    log(f"   [{res['path']}] {res['action']} -> {res['profile']} · {t}intent {res['intent_s']:.2f}s")
    for c in res["changes"]:
        log(f"     {c[0]} {c[1]}")
    for r in res["refusals"]:
        log(f"     x can't do: {r}")
    log(f"   says: {res['spoken']}")


def main():
    ap = argparse.ArgumentParser(prog="python -m privacy_filter.voice")
    ap.add_argument("--text", help="run one typed command and exit")
    ap.add_argument("--audio-file", help="run one recorded command (any format ffmpeg reads) and exit")
    ap.add_argument("--adb", action="store_true", help="also trigger from glasses button presses (adb getevent)")
    ap.add_argument("--mic", default=MIC_NAME, help="input device name (substring); falls back to default input")
    ap.add_argument("--model", default="base.en", help="faster-whisper model (tiny.en, base.en, small.en)")
    ap.add_argument("--claude-model", default="haiku")
    ap.add_argument("--no-claude", action="store_true", help="fast local parser only")
    ap.add_argument("--no-say", action="store_true")
    ap.add_argument("--max-seconds", type=float, default=6.0)
    ap.add_argument("--list-mics", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)
    kw = dict(use_claude=not a.no_claude, model=a.claude_model, speak=not a.no_say)
    PR.ensure_default()

    if a.list_mics:
        import sounddevice as sd
        print(sd.query_devices())
        print("selected:", find_mic(a.mic))
        return
    if a.text:
        res = handle(a.text, source="typed", **kw)
        report(res)
        _wait_say(res)
        return
    stt = STT(a.model)
    print(f"speech-to-text: faster-whisper {a.model} (loaded in {stt.load_s:.1f}s)")
    if a.audio_file:
        tw0 = time.time()
        write_state(phase="listening")
        audio = load_audio_file(a.audio_file)
        t1 = time.monotonic()
        text = stt(audio)
        t2 = time.monotonic()
        print(f'heard: "{text}"')
        if not text:
            write_state(phase="error", note="DIDN'T CATCH THAT")
            return
        write_state(phase="thinking", heard=text, source="voice")
        res = handle(text, **kw)
        report(res, t2 - t1)
        print(f"   end-to-end (audio file in -> profile live): {res['live_at'] - tw0:.2f}s")
        _wait_say(res)
        return

    dev = find_mic(a.mic)
    print(f"microphone: {dev[1]} ({dev[2]} Hz, {dev[3]} ch)")
    trig_q = queue.Queue()
    stop = threading.Event()
    threading.Thread(target=stdin_listener, args=(trig_q,), daemon=True).start()
    if a.adb:
        threading.Thread(target=adb_listener, args=(trig_q, stop), daemon=True).start()
    print("\nPress Enter and speak (stops on silence, or press Enter again)  ·  or type a command + Enter  ·  q to quit"
          + ("  ·  glasses button = talk" if a.adb else ""))
    say_p = None
    while True:
        kind, val = trig_q.get()
        if kind == "eof" or (kind == "line" and val.strip().lower() in ("q", "quit", "exit")):
            break
        if say_p is not None and say_p.poll() is None:
            say_p.kill()                                        # new command interrupts the old read-back
        if kind == "line" and val.strip():
            res = handle(val.strip(), source="typed", **kw)
            report(res)
            say_p = res.get("say_proc")
            continue
        # spoken command
        print("🎙  listening…")
        write_state(phase="listening")
        t0 = time.monotonic()
        def pressed_again():
            try:
                k, v = trig_q.get_nowait()
            except queue.Empty:
                return False
            if k == "eof" or (k == "line" and v.strip()):
                trig_q.put((k, v))          # typed text / quit: handle after this recording
                return False
            print("   (stopped by press)")
            return True
        try:
            audio, spoke = record(dev, max_s=a.max_seconds, stop=pressed_again)
        except Exception as e:
            print("!! recording failed:", e)
            write_state(phase="error", note="MIC ERROR")
            continue
        t1 = time.monotonic()
        text = stt(audio) if spoke else ""
        t2 = time.monotonic()
        if not text or len(normalize(text)) < 2:
            print("   (heard nothing)")
            write_state(phase="error", note="DIDN'T CATCH THAT")
            say_p = say("Didn't catch that.") if not a.no_say else None
            continue
        print(f'   heard: "{text}"  (recorded {t1 - t0:.1f}s, stt {t2 - t1:.2f}s)')
        write_state(phase="thinking", heard=text, source="voice")
        res = handle(text, **kw)
        report(res, t2 - t1)
        print(f"   end of speech -> profile live: {time.monotonic() - t1:.2f}s")
        say_p = res.get("say_proc")
    stop.set()


def _wait_say(res):
    p = res.get("say_proc")
    if p is not None:
        p.wait()


if __name__ == "__main__":
    main()
