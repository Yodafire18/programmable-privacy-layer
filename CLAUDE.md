# Programmable Privacy Layer

Describe, in plain English, what a camera should never capture; test it on synthetic footage; then wear it.
Runs live on Mentra Live glasses (USB) on a Mac. Every face is blurred by default (fail-closed); people
consent with two peace signs and revoke with two thumbs down. Rules are YAML profiles, written and tested
through MCP tools by Claude, the web dashboard, or the wearer's voice. Story: `README.md`, `VISION.md`;
demo flow and Q&A: `DEMO.md`; new-Mac setup: `SETUP.md`.

## Architecture
```
Claude / dashboard (gui/) / voice (privacy_filter/voice.py) / typing
        -> MCP tools (privacy_filter/mcp_server.py): list_capabilities, save_profile,
           test_profile, activate_profile, get_profile
        -> profiles/*.yaml (validated in privacy_filter/profiles.py; profiles/ACTIVE hot-reloads in ~1 s)
        -> privacy_filter/live.py: detect (perception.py, objects.py) -> track (tracker.py)
           -> consent memory (consent.py) -> fail-closed blur (render.py)
in: Mentra Camera over USB, webcam, RTSP stream, clip      out: screen, recording
```
- Faces: InsightFace `buffalo_s` live; `buffalo_l` is the offline "teacher" for evals (`eval/teacher.py`).
- Gestures: MediaPipe (`models/gesture_recognizer.task`). Screens: YOLOv8n (`models/yolov8n.onnx`).
- Voice: local faster-whisper `base.en` -> `claude -p` + MCP (offline fast path for short commands).
- Unsupported requests are reported as "not supported yet", never faked.

## Key commands
```
./setup_mac.sh [--dry-run|--yes]          # one-time setup, idempotent
./start.sh                                # dashboard :7860 + glasses view + voice tool (Terminal windows)
./demo.sh [usb|webcam|glasses|clip]       # glasses view only; picks "Mentra Camera" by name
.venv/bin/python -m gui.server            # dashboard, http://localhost:7860
.venv/bin/python -m privacy_filter.voice  # voice tool (Enter = talk, or type)
.venv/bin/python -m pytest -q tests       # unit tests
.venv/bin/python -m eval synth            # regenerate synthetic clips (corpus/)
.venv/bin/python -m eval.live_eval        # score the live pipeline on the synthetic clips
claude mcp list                           # privacy-filter should be connected
```
Live view keys: q quit, f forget everyone, space photo, r record, d debug overlay.

## Rules
- The `default` profile is **locked** (`locked: true`): never edit or overwrite `profiles/default.yaml`.
  Faces are always blurred; no rule may turn privacy off.
- `profiles/ACTIVE` is runtime state. Changing it switches the live demo; don't touch it while a demo runs.
- Python 3.12 only (MediaPipe). Use `.venv/bin/python`; versions are pinned in `requirements.txt`.
- InsightFace models are non-commercial research use; YOLOv8 is AGPL-3.0 (see README).

## How the user wants you to work
- **Keep it simple.** The smallest change that does the job.
- **Don't invent facts or features.** If it isn't built or measured, say so (roadmap items are "not built").
- **Be concise.**
- **Verify with measurements.** Run the tests or evals and quote real numbers (leak frames, latency),
  not guesses. A test result is never a "guarantee".
- **Pitch material** (README, DEMO, VISION, briefs): bold, confident tone, still only true claims.
