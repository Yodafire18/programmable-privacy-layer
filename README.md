# Programmable Privacy Layer

**Describe, in plain English, what your camera should never capture. Test it. Then wear it.**

A privacy filter for camera glasses (built for Mentra Live at the Immerse the Bay hackathon).
- Every face is blurred by default.
- People who show **two peace signs** ✌️✌️ become visible; **two thumbs-down** hides them again.
- Rules live in **profiles** that anyone can write in plain English through an **MCP server**, from Claude, a web dashboard, or by talking to the glasses.

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
