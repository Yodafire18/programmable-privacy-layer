"""Privacy profiles: the rules (what to blur, what lets someone be seen) live in profiles/<name>.yaml,
not in code. The live app follows profiles/ACTIVE and reloads on change; the MCP server writes them.

profiles/<name>.yaml
  name: default
  description: Blur every face; two peace signs to be seen, two thumbs down to go back.
  blur_targets: [faces]
  consent: {gesture: peace_sign, hands: 2, hold_s: 0.75}
  revoke:  {gesture: thumbs_down, hands: 2}
  blur_strength: medium          # light | medium | strong
  locked: true                   # optional: save() refuses to overwrite it (the "gold" default)

Every save keeps the previous version in profiles/.history/<name>.json (newest last), so edits can be undone.
"""
import json
import os
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "profiles"
ACTIVE = DIR / "ACTIVE"

# friendly name -> MediaPipe Gesture Recognizer class
GESTURES = {
    "peace_sign": "Victory",
    "thumbs_up": "Thumb_Up",
    "thumbs_down": "Thumb_Down",
    "open_palm": "Open_Palm",
    "fist": "Closed_Fist",
    "pointing_up": "Pointing_Up",
    "i_love_you": "ILoveYou",
}
SUPPORTED_TARGETS = {
    "faces": "every human face in view (detected with SCRFD, tracked, remembered on consent)",
    "screens": "phones, laptops, monitors/TVs (YOLOv8n COCO classes; rectangular blur, always on, never consentable; "
               "detector only runs when a profile lists screens)",
}
STRENGTH = {"light": {"pixel_cells": 14, "feather": 0.3},
            "medium": {"pixel_cells": 10, "feather": 0.25},
            "strong": {"pixel_cells": 6, "feather": 0.2}}

DEFAULT = {
    "name": "default",
    "description": "Blur every face; two peace signs to be seen, two thumbs down to go back.",
    "blur_targets": ["faces"],
    "consent": {"gesture": "peace_sign", "hands": 2, "hold_s": 0.75},
    "revoke": {"gesture": "thumbs_down", "hands": 2},
    "blur_strength": "medium",
    "locked": True,
}
HISTORY = DIR / ".history"
HISTORY_N = 20


class LockedProfile(PermissionError):
    pass


def slug(name):
    s = re.sub(r"[^a-z0-9_-]+", "-", name.strip().lower()).strip("-")
    if not s:
        raise ValueError("profile name is empty")
    return s


def path(name):
    return DIR / f"{slug(name)}.yaml"


def validate(p):
    """Returns (clean_profile, problems). Problems are things the engine cannot do; they are NOT silently dropped."""
    problems = []
    out = {"name": slug(p.get("name", "")), "description": str(p.get("description", "")).strip()}
    targets = [str(t).strip().lower() for t in p.get("blur_targets", ["faces"])]
    bad = [t for t in targets if t not in SUPPORTED_TARGETS]
    if bad:
        problems.append(f"blur target(s) not supported yet: {', '.join(bad)} (supported: {', '.join(SUPPORTED_TARGETS)})")
    out["blur_targets"] = [t for t in targets if t in SUPPORTED_TARGETS] or ["faces"]
    if "faces" not in out["blur_targets"]:
        problems.append("faces are always blurred (they can't be turned off); added 'faces'")
        out["blur_targets"].insert(0, "faces")
    for key, default in (("consent", DEFAULT["consent"]), ("revoke", DEFAULT["revoke"])):
        r = {**default, **(p.get(key) or {})}
        if r["gesture"] not in GESTURES:
            problems.append(f"{key} gesture '{r['gesture']}' not recognized (options: {', '.join(GESTURES)})")
            r["gesture"] = default["gesture"]
        r["hands"] = int(r.get("hands", 2))
        if r["hands"] not in (1, 2):
            problems.append(f"{key} hands must be 1 or 2")
            r["hands"] = 2
        if key == "consent":
            r["hold_s"] = float(r.get("hold_s", 0.75))
        out[key] = r
    if out["consent"]["gesture"] == out["revoke"]["gesture"]:
        problems.append("consent and revoke gestures must differ")
    st = p.get("blur_strength", "medium")
    if st not in STRENGTH:
        problems.append(f"blur_strength '{st}' not one of {', '.join(STRENGTH)}")
        st = "medium"
    out["blur_strength"] = st
    if p.get("locked"):
        out["locked"] = True
    return out, problems


def to_cfg(profile, cfg):
    """Apply a (validated) profile onto an engine config dict in place."""
    c, r = profile["consent"], profile["revoke"]
    cfg["consent"]["consent_gesture"] = GESTURES[c["gesture"]]
    cfg["consent"]["revoke_gesture"] = GESTURES[r["gesture"]]
    cfg["consent"]["hands_required"] = c["hands"]
    cfg["consent"]["hold_s"] = c["hold_s"]
    cfg["render"].update(STRENGTH[profile["blur_strength"]])
    cfg.setdefault("objects", {})["screens"] = "screens" in profile["blur_targets"]
    return cfg


def to_overrides(profile):
    """Same as to_cfg, as dotted overrides for `python -m eval run --set ...`."""
    c, r = profile["consent"], profile["revoke"]
    o = [f"consent.consent_gesture={GESTURES[c['gesture']]}", f"consent.revoke_gesture={GESTURES[r['gesture']]}",
         f"consent.hands_required={c['hands']}", f"consent.hold_s={c['hold_s']}"]
    o += [f"render.{k}={v}" for k, v in STRENGTH[profile["blur_strength"]].items()]
    o.append(f"objects.screens={'true' if 'screens' in profile['blur_targets'] else 'false'}")
    return o


def _write_atomic(p, text):
    """The live app polls these files; never let it read a half-written one."""
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, p)


def is_locked(name):
    p = path(name)
    if not p.exists():
        return False
    try:
        return bool((yaml.safe_load(p.read_text()) or {}).get("locked"))
    except Exception:
        return False


def history(name):
    try:
        return json.loads((HISTORY / f"{slug(name)}.json").read_text())
    except (FileNotFoundError, ValueError):
        return []


def _set_history(name, items):
    HISTORY.mkdir(parents=True, exist_ok=True)
    _write_atomic(HISTORY / f"{slug(name)}.json", json.dumps(items[-HISTORY_N:], indent=1))


def save(profile, keep_history=True):
    """Validate and write. Refuses (LockedProfile) to overwrite a profile marked `locked: true`.
    The version being replaced is pushed onto its history (see undo)."""
    DIR.mkdir(exist_ok=True)
    clean, problems = validate(profile)
    p = path(clean["name"])
    if is_locked(clean["name"]):
        raise LockedProfile(f"profile '{clean['name']}' is locked and can't be changed; "
                            f"save your edits under another name (e.g. 'voice') instead")
    if keep_history and p.exists():
        try:
            prev = validate(yaml.safe_load(p.read_text()) or {})[0]
        except Exception:
            prev = None
        if prev and not same_rules(prev, clean):
            _set_history(clean["name"], history(clean["name"]) + [prev])
    _write_atomic(p, yaml.safe_dump(clean, sort_keys=False))
    return clean, problems


def same_rules(a, b):
    """Equal apart from name/description/lock (a description-only change is not worth an undo step)."""
    skip = ("name", "description", "locked")
    return {k: v for k, v in a.items() if k not in skip} == {k: v for k, v in b.items() if k not in skip}


def undo(name):
    """Restore the previous saved version of `name` that differs from the current one.
    Returns it, or None if there is no history."""
    if is_locked(name):
        raise LockedProfile(f"profile '{slug(name)}' is locked")
    items = history(name)
    cur = load(name) if path(name).exists() else None
    while items and cur is not None and same_rules(validate(items[-1])[0], cur):
        items.pop()
    if not items:
        _set_history(name, items)
        return None
    prev = items.pop()
    _write_atomic(path(name), yaml.safe_dump(validate(prev)[0], sort_keys=False))
    _set_history(name, items)
    return load(name)


def load(name):
    p = path(name)
    if not p.exists():
        raise FileNotFoundError(f"no profile '{name}' (have: {', '.join(names()) or 'none'})")
    return validate(yaml.safe_load(p.read_text()) or {})[0]


def names():
    return sorted(p.stem for p in DIR.glob("*.yaml")) if DIR.exists() else []


def activate(name):
    load(name)                     # must exist and parse
    DIR.mkdir(exist_ok=True)
    _write_atomic(ACTIVE, slug(name) + "\n")


def active_name():
    return ACTIVE.read_text().strip() if ACTIVE.exists() else None


def ensure_default():
    if not path("default").exists():
        save(DEFAULT)
    if not path("voice").exists():          # voice edits go here; default stays untouched
        save({**load("default"), "name": "voice", "locked": False,
              "description": "Voice-edited copy of default."}, keep_history=False)
    if not active_name():
        activate("default")
