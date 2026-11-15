# Programmable Privacy Layer

## Describe, in plain English, what your camera should never capture. Test it. Then wear it.

Camera glasses are here, and everyone around you is in the shot. Every person we showed this to wanted different rules:
- the **vlogger** livestreaming their life wants bystanders and license plates gone
- the **doctor** wants documents, X-rays and children's faces gone
- the **streamer** wants the drink brand they hate covered up

That told us the answer isn't one more filter. **It's a programmable privacy layer:** one engine, and every rule written in natural language.

**MCP is the magic.** Rules become beautiful, testable, natural language:

> *"Blur faces, but let people who show two peace signs unblur, so they can consent to be photographed."*

That sentence becomes a live profile on the glasses in about a second. Any AI agent, app, dashboard, or the wearer's own voice speaks to it through the same MCP tools. **Describe it. Test it on synthetic footage. Tune it. Wear it, on glasses or anything with a camera.**

### Running today, live on Mentra Live glasses
- Every face blurred by default, failing closed.
- **✌️✌️ to be seen, 👎👎 to disappear again**: consent by gesture, remembered for the session, wiped on exit.
- **Talk to your glasses:** *"also blur screens"*, and it's done in about a second.
- **Type a rule into the dashboard** or ask Claude; it lands on the glasses in about a second.
- Tested on synthetic moving-camera footage, crowds included.

### Where it goes
The engine is built so every new detector is a new rule anyone can write: license plates, documents, X-rays, brand logos. The same profile follows you from glasses to headset to webcam to livestream server. Doctors author on a headset; vloggers stream clean; streamers control their frame.

## What it does
- **Faces blurred by default**, with fail-closed tracking (lost faces stay blurred; the whole frame blurs during whip pans).
- **Gesture consent**: remembered for the session only, wiped on exit. Only consenting people's face fingerprints are kept.
- **Screens** (phones, laptops, TVs) can be blurred on request.
- **Profiles** (`profiles/*.yaml`): blur targets, consent/revoke gestures, hands, hold time, blur strength. `default` is locked.
- **MCP tools** (`privacy_filter/mcp_server.py`): `list_capabilities`, `save_profile`, `test_profile`, `activate_profile`, `get_profile`. Unsupported requests are reported, not faked.
- **Voice**: speak to the glasses (mic over USB) → local Whisper → Claude + MCP → the profile switches live in about 1 s.
- **Dashboard** (`gui/`): profiles, a plain-English box, test results, start/stop, record & photo.
- **Synthetic tests** (`eval/`): moving-camera clips scored for faces left uncovered and people wrongly unblurred.

## Architecture
```
 Claude / any MCP client · web dashboard · voice on the glasses · typing
                         │
                   MCP tools (one interface)
                         │
   profiles/*.yaml  (validated, tested, hot-reloaded in ~1 s)
                         │
   detect → track → consent memory → fail-closed blur   (privacy_filter/live.py)
                         │
   in: Mentra glasses (USB), webcam, any stream    out: screen, recording
```

## Quick start (macOS, Apple Silicon)
Requires Python 3.12, ffmpeg, and Claude Code (for the plain-English and voice paths).
```
python3.12 -m venv .venv && .venv/bin/pip install opencv-python mediapipe onnxruntime insightface \
    pyyaml "mcp>=2" faster-whisper sounddevice pytest
.venv/bin/python -m eval synth                      # synthetic test clips
claude mcp add privacy-filter -- "$PWD/.venv/bin/python" -m privacy_filter.mcp_server
./start.sh                                          # dashboard + glasses view + voice tool
```
- `./demo.sh [usb|webcam|glasses|clip]` runs just the glasses view. It picks "Mentra Camera" by name.
- `.venv/bin/python -m pytest -q tests` runs the unit tests.
- `.venv/bin/python -m eval.live_eval` scores the live pipeline on the synthetic clips.

More: `DEMO.md` (demo flow, Q&A), `VISION.md`, `WIRELESS.md`, `mentra/STATUS.md`.

## Licenses of third-party models
- `models/yolov8n.onnx`: Ultralytics YOLOv8, **AGPL-3.0**.
- `models/gesture_recognizer.task`: Google MediaPipe, Apache-2.0.
- InsightFace `buffalo_s` / `buffalo_l` face models: downloaded on first run, **non-commercial research use** (not included here).
- faster-whisper `base.en`: downloaded on first run (MIT).
