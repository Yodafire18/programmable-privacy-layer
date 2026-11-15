# Mentra handoff: get the Mentra Live glasses streaming into the privacy filter

You're helping the user, hands-on, connect **Mentra Live** smart glasses to an already-working privacy-filter pipeline on this Mac (an M1 with 8 GB). This is for a hackathon demo in a crowded hall. Expect the user to be doing physical steps (hotspot, glasses, phone app); keep instructions short and concrete, and check results yourself where you can.

## How the user wants you to work
- Be direct and concise. No filler, no sucking up to sponsors.
- **Don't invent facts** about Mentra's APIs. Read their docs (links below) and say what you verified vs. what you're guessing.
- Measure and verify rather than assume.

## Background: the project and how we got here

**Product (see `VISION.md`, in the user's own words):** a *programmable privacy layer* for camera glasses. You describe in plain English what your camera should never capture, test it, then wear it.
- Default demo profile: every face is blurred; a person who shows **two peace signs** (held about 0.75 s) is unblurred and remembered for the session; two thumbs-down blurs them again.
- Rules live in profiles (`profiles/*.yaml`) that Claude creates through our **MCP server** (`privacy_filter/mcp_server.py`, tools: `list_capabilities`, `save_profile`, `test_profile`, `activate_profile`, `get_profile`).
- The running live app hot-reloads the active profile within a second and shows a "PROFILE: …" banner.
- The **MCP is the differentiator**; the user considers it what excuses minor flaws elsewhere.

**Hackathon:** Stanford XR "Immerse the Bay", ManageXR track ("Bring AI to the physical world w/ smart glasses"). Judged on glasses-native, AI that responds to what the user does/says/sees, and experience. The demo is **live in a crowded hall** of technical people who want to see it actually work.

**Hardware decisions:**
- **Mentra Live is the live-demo device.** It streams RTMP/SRT/WebRTC to your own endpoint over Wi-Fi.
- **Ray-Bans are not used** (Meta's toolkit forces an iPhone relay and was flaky).
- **Neither Meta nor Mentra is a sponsor.** Don't pander to either.

**What I already found out about Mentra Live** (from their docs and press, Jan 2026; verify current details yourself):
- **Hardware:** MediaTek MT8766 (weak, can't run our models on-device), 12 MP camera, 1080p video, mics + speakers, no display, about 45 min of continuous video/streaming on a 260 mAh battery.
- **SDKs:**
  - A **Bluetooth SDK** for phone apps (Android/iOS/React Native): scanning, pairing, camera capture, streaming, Wi-Fi config, mic/speaker, events. The Bluetooth SDK version must match the glasses software version (the latest matching pair was listed as 3.1.0).
  - A **MentraOS Cloud SDK** (TypeScript) with a camera stream call taking an RTMP URL (something like `session.camera.startStream({rtmpUrl})`), plus a **managed stream** option returning HLS/DASH URLs.
  - Docs mention **photo upload to your own webhook** and **RTMP/SRT/WebRTC to your own ingest endpoint**.
  - A phone-side SDK, "MentraOS 3.0", was announced for summer 2026.

**Engineering decisions already made (don't undo them):**
- **Smoothness first, latency second.** Viewers must see a steady 30 fps with no jitter or flicker. Delay is a dial: `--delay` / `timeline.buffer_s` is how far the app looks ahead before showing a frame. The demo uses 0.75 s; up to 3 s is acceptable for livestreams.
- **Live app architecture** (`privacy_filter/live.py`):
  - The capture thread keeps the newest frames.
  - Face + hand detection, tracking and consent run in a **separate process** (this avoids Python's global lock starving the display).
  - The main loop outputs frame j once it's `delay` old, using `timeline.build` over a sliding window (gap interpolation, hold/grow, fade on consent, fail closed).
  - Frames the detector skipped are interpolated.
  - Memory is hard-capped; the app skips ahead instead of letting delay grow.
- **Performance fixes already in:**
  - onnxruntime capped at 3 threads with spinning off (it was starving capture/display)
  - faces and hands run concurrently
  - blur rendered at small scale, then upscaled (about 23× faster)
  - hands detected at 640 px width
  - result on the webcam: 30.0 fps out, about 3 ms of render work per frame, about 26 detection updates/s
- **Privacy tuning for the live path:**
  - detection threshold 0.35 (catches half-faces at frame edges)
  - lost faces stay blurred 1 s
  - whole-frame blur when every face vanishes at once mid-stream (whip pans)
  - result: the live eval on the synthetic clips gives 5–14 leak frames out of 660, with **0 false-visible** (nobody ever wrongly unblurred)
- **Crowds are currently bad:** about 54 small faces → about 430 ms per detection (3 updates/s), every frame leaks. The crowd agent is fixing this right now (see below). **If you test the glasses facing a crowd before that lands, expect leaks.** That's known, not your bug.

**Tools you can use to verify things:**
- `.venv/bin/python -m eval.live_eval --delay 0.75 [--clips …] [-v]` scores the live app on the synthetic clips (`corpus/`). Results are timing-dependent and noisy; repeat runs.
- `.venv/bin/python -m privacy_filter.live --source <src> --headless --seconds N` runs without a window and prints stats.
- `stream/fake_mentra.sh [clip]` acts as the glasses.

**Demo-hall considerations already decided:**
- Use `--demo` (full-screen, filtered output only, so raw bystander faces never hit the big screen; the debug overlay in demo mode is drawn on the blurred frame).
- Use a private hotspot.
- A big green "VISIBLE" banner appears on consent (the spoken cue via macOS `say` won't be heard in a loud hall).
- The person giving consent should stand close to the camera and a bit apart from others: in a crowd, ambiguous gestures are ignored (fail closed).

**Parked ideas: don't build unless the user asks:**
- voice-authored profiles from the glasses (mic → speech-to-text → Claude + MCP → spoken confirmation)
- blur styles (frosted, mosaic, solid oval, emoji)
- opt-out/strict/expiring-consent profiles
- text/badge blurring

## Video pipeline deep dive (read this before touching anything video-related)

### End-to-end path with Mentra
```
Mentra glasses ──(Wi-Fi, RTMP publish, H.264)──► MediaMTX on the Mac (:1935 RTMP in)
   MediaMTX ──(RTSP out :8554, TCP)──► OpenCV/FFmpeg VideoCapture in live.py (capture thread)
   capture thread ──newest frame on request──► detection process (faces+hands+tracker+consent)
   detection process ──per-frame detections──► main process (timeline smoothing → blur render)
   main loop ──► full-screen window (--demo) [+ optional recording to output/*.mp4]
```
Expected delay sources: the glasses' encode and Wi-Fi (unknown, **measure it**), MediaMTX relay (small), RTSP read (OpenCV options `rtsp_transport;tcp|fflags;nobuffer|flags;low_delay` are set in `live.main()`), plus **our deliberate look-ahead `--delay`** (0.75 s in the demo). Total delay to the screen ≈ glasses latency + about 0.1–0.3 s + `--delay`.

### Threads, processes and data (`privacy_filter/live.py`)
- **`capture()` thread**
  - opens the source: a digit means webcam index (AVFoundation, requests 1280×720); anything with `://` is a stream (CAP_FFMPEG); otherwise a file (paced to real time)
  - stores `self.frames[idx] = (t, img, arrival_monotonic)`
  - when the detection process has signalled it's idle (`req_ev`), sends `(idx, t, img)` through `in_q`, so **detection always works on the newest frame and skips the rest**
  - keeps `fps_in` as a rolling estimate over 30 frames
  - **hard memory cap:** at most `fps_in × (buffer_s + 2 s)` frames held
- **Streams reconnect forever:**
  - if the stream can't open, it shows "WAITING FOR GLASSES STREAM…" and retries every 1 s
  - if a read fails mid-stream, it shows "RECONNECTING TO GLASSES…" and reopens
  - frame indices keep increasing across reconnects, and the fps estimate resets on reconnect (an earlier bug: the outage dragged fps down, the memory cap evicted the frame the display was waiting for, and output froze forever)
- **`_perception_worker` process** (spawned, so it has its own Python lock)
  - InsightFace SCRFD+ArcFace (buffalo_s, det_size 640) and MediaPipe Gesture Recognizer, concurrently
  - then tracker and consent
  - returns `("res", idx, dets, hands, ms, new_events, n_remembered)` on `out_q`
  - takes commands on `cmd_q`: `"forget"`, `("config", consent_cfg)` for profile hot-reload
- **`receive()` thread:** stores detections per idx, triggers the voice cue and banner on consent/revoke.
- **Main loop (`run()`)**
  - shows frame j only when `now ≥ arrival_j + delay`
  - if more than 0.15 s behind, it jumps to the newest frame that's due (counted as `skips`); delay must never grow
  - if frame j was evicted, it resumes at the oldest frame still kept
  - each frame: `regions_for(j)` runs `timeline.build` over a window of about 1.9 s back and `delay` ahead (bounded cost), adds a whole-frame blur on "dropout" frames, then `render.render` draws the blur, plus demo badge, banners and display
  - images are deleted right after being shown; detections are kept for the smoothing window

### Key settings (live defaults are set in `live.main()`)
| setting | live value | effect |
|---|---|---|
| `--delay` / `timeline.buffer_s` | 0.75 (demo) | look-ahead: more = smoother/safer, more delay |
| `perception.det_thresh` | 0.35 | face detection recall vs. occasionally blurring a non-face |
| `timeline.hold_miss_s` | 1.0 | how long a lost face stays blurred |
| `perception.ort_threads` | 3 | CPU threads for the face model (the rest go to capture/display) |
| `perception.hand_max_w` | 640 | hand detection resolution |
| `perception.parallel` | true | faces and hands at the same time |
Override any of them with `--set key=value`.

### How to read the stats the app prints on exit
- `capture: X fps`: what actually arrived from the source. **This is the number to measure for Mentra.** If it's low, the problem is upstream (glasses, Wi-Fi, MediaMTX), not us.
- `output: X fps · frame gap p50/p95/max · gaps >50ms · work p50/p95 · skips`: what the viewer saw. `work` is the main-loop time per frame (it must stay well under 33 ms). `skips` counts catch-ups.
- `faces Xms hands Yms · detection updates N/s`: detection speed. Fewer updates means more interpolation and more risk of leaks on fast motion.
- `PF_TRACE=1` prints once per second: `j` (frame being shown), `latest` (newest captured), `done` (newest detected), frames held, status.

### Measured so far
- Webcam (1280×720 at 30): capture 30.0, output 30.0 fps, longest gap about 38 ms, render work about 3 ms, about 26 detection updates/s with one face.
- Fake glasses stream through MediaMTX (720p, 30 fps H.264 zerolatency): RTSP opens in about 1.9 s and is steady. **Reading RTMP directly with OpenCV was worse** (about 5 s to open, a 281 ms burst gap), which is why the app reads RTSP.
- Drop test (stream killed for 5 s, then restarted): the app reconnected by itself and was back to a steady 30 fps output.

### Things likely to matter with Mentra specifically (unverified, check them)
- **1080p** from the glasses: about 2.25× the pixels of 720p. Rendering and detection cost more. If fps drops, ask the glasses for 720p, or downscale in the app (a change under `privacy_filter/`: relay it to the main session).
- **Glasses fps below 30 or bursty over Wi-Fi:** the output can't be smoother than the input. The `capture:` line will show it.
- **Keyframe interval / GOP** on the glasses encoder affects how fast RTSP reconnects (MediaMTX waits for a keyframe).
- **Mac IP changes** on the hotspot; `./demo.sh` prints it. The macOS firewall may need to allow incoming connections for `mediamtx`.
- **Rotation/orientation:** check the feed isn't sideways or mirrored.

### Bugs already fixed (recognize the symptoms; don't reintroduce them)
| symptom | cause | fix |
|---|---|---|
| delay keeps growing, Mac runs out of memory | the smoothing window grew with the backlog (feedback loop), no frame skipping | window capped at `delay` ahead, skip-ahead, hard frame cap |
| choppy output with fast detection | onnxruntime spun threads on every core; the display thread was starved | 3 threads, no spinning |
| output about 13–20 fps while capture was 30 | Python lock contention between the detection thread and the display loop | detection moved to a separate process |
| close faces slow to render | full-resolution per-pixel mask math + huge Gaussian kernel | blur and mask at small scale, then upscale |
| frozen after a stream reconnect | fps estimate dragged down by the outage → memory cap evicted the waited-for frame | reset fps on reconnect + resume at the oldest kept frame |
| app hangs on exit | a pending frame in the multiprocessing queue blocked interpreter exit | `cancel_join_thread()` on the queues |
| stats lost when closing the window | stdout buffering when piped | line-buffered stdout, clean Ctrl-C/close handling |

## What already works (don't rebuild it)
- `./demo.sh [auto|glasses|webcam|clip]` is the one-command launcher. It starts the streaming server, prints the URL the glasses should publish to, and runs the live app full-screen (`--demo`).
- **Receiving side, tested with a fake glasses stream:**
  - `stream/server.sh` runs MediaMTX with `stream/mediamtx.yml`. The glasses **publish RTMP** to `rtmp://<mac-ip>:1935/live/mentra` (SRT is also enabled on port 8890 if Mentra prefers it).
  - The app **reads RTSP** locally: `rtsp://localhost:8554/live/mentra`. RTSP connects in about 2 s and is steadier than reading RTMP.
  - `stream/fake_mentra.sh` pushes a looping clip exactly as the glasses would. Use it to verify the Mac side whenever in doubt.
  - The live app **auto-reconnects** when the stream drops ("WAITING FOR GLASSES STREAM…" / "RECONNECTING…" screen), so nothing leaks while it's down.
- Mac IP on the current Wi-Fi was `10.0.0.38`. **It will change on a hotspot.** `./demo.sh` prints the current one.
- Live app: `.venv/bin/python -m privacy_filter.live --source <url|0|file> [--delay 0.75] [--demo] [--profile NAME]`. It prints `output: … fps … frame gap …` and `capture: … fps` on exit, and `PF_TRACE=1` prints per-second state.
- MCP server (profiles) is registered with Claude Code for this folder. It's not your concern unless asked.

## Your job
1. Figure out from Mentra's docs how to make the glasses stream to a **custom RTMP (or SRT) URL**. Likely options:
   - the MentraOS cloud SDK: a TypeScript app with a camera-stream call taking an RTMP URL; "managed streaming" also exists
   - the Mentra Bluetooth SDK (phone side)
   - a built-in option in the Mentra phone app
   Pick the fastest path to a working stream and tell the user exactly what to do.
2. Networking: glasses and Mac on the **same private hotspot or travel router** (venue Wi-Fi is unreliable). Check that the glasses can reach the Mac's port 1935. The macOS firewall may prompt.
3. Get `./demo.sh glasses` showing the glasses feed, filtered.
4. **Measure and report:** resolution, real fps (`capture:` line), smoothness (`output:` line), end-to-end delay (wave at the glasses and time it), and stability over 5+ minutes. Note battery and heat (about 45 min of streaming is expected).
5. If fps or delay is bad, try SRT vs RTMP, lower resolution or bitrate on the glasses side, and MediaMTX settings.

## Docs
- Mentra Live overview: https://docs.mentraglass.com/mentra-live/overview
- Mentra Bluetooth SDK: https://docs.mentraglass.com/bluetooth-sdk/overview
- MentraOS Cloud SDK, hardware/camera: https://cloud-docs.mentra.glass/sdk/hardware-modules

## Don't collide with the other work going on right now
Two background agents are working in parallel:
- **Crowd-performance agent, in this same folder:** editing `privacy_filter/perception.py`, `tracker.py`, `consent.py`, the worker in `privacy_filter/live.py`, and `eval/`. It will commit when done.
- **Screens agent, in a separate git worktree:** adding phone/laptop blurring. It'll be merged later.

So:
- **Don't edit anything under `privacy_filter/` or `eval/`.** If the live app needs a change for Mentra (e.g. a new source type), write down exactly what and why, and tell the user to relay it to the main session.
- Put any new code or config under `mentra/` (e.g. a Mentra app) or `stream/`. Editing `stream/mediamtx.yml` is fine.
- When committing, commit **only your paths**:
  `git add mentra stream && git -c user.name="ani" -c user.email="anu.srini21@gmail.com" commit -m "..." -- mentra stream`
  End commit messages with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Don't persist git config, and don't commit `stream/auto.crt` or `stream/auto.key` (gitignored).
- The CPU is shared. Avoid long benchmark loops while the other agents run unless the user asks.

## When done
Write a short `mentra/STATUS.md`: how to start streaming (the exact steps on the glasses/phone), the URL and protocol used, measured fps/resolution/delay/stability, and known issues.
