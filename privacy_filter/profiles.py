"""Privacy profiles: the rules (what to blur, what lets someone be seen) live in profiles/<name>.yaml,
not in code. The live app follows profiles/ACTIVE and reloads on change; the MCP server writes them.

profiles/<name>.yaml
  name: default
  description: Blur every face; two peace signs to be seen, two thumbs down to go back.
  blur_targets: [faces]
  consent: {gesture: peace_sign, hands: 2, hold_s: 0.75}
  revoke:  {gesture: thumbs_down, hands: 2}
  blur_strength: medium          # light | medium | strong
"""
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
}


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


def save(profile):
    DIR.mkdir(exist_ok=True)
    clean, problems = validate(profile)
    path(clean["name"]).write_text(yaml.safe_dump(clean, sort_keys=False))
    return clean, problems


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
    ACTIVE.write_text(slug(name) + "\n")


def active_name():
    return ACTIVE.read_text().strip() if ACTIVE.exists() else None


def ensure_default():
    if not path("default").exists():
        save(DEFAULT)
    if not active_name():
        activate("default")
