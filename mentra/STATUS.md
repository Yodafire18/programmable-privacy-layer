# Mentra Live → privacy filter: status (2026-11-15)

## Demo path: USB (wireless is a stretch goal)
Plugged into the Mac by USB, Mentra Live shows up as a standard USB webcam, **"Mentra Camera"**
(it also shows up as "Mentra Microphone" and an Android debug (ADB) interface). No phone, hotspot,
SDK or RTMP needed.

1. Plug the glasses into the Mac. Check: `ffmpeg -f avfoundation -list_devices true -i ""` lists `[0] Mentra Camera`.
2. `./demo.sh webcam` (uses index 0 = the glasses; the FaceTime camera is index 1).
3. Keep the cable taped or looped so it can't be yanked out.

Caveats:
- The app picks the camera by **index**. If the glasses are unplugged, index 0 becomes the FaceTime camera
  (still filtered, but the wrong view). Main session is switching to selection by name.
- Only one camera mode is offered: **1280×720 at 30 fps**.

## Measurements (USB)
| what | result | how |
|---|---|---|
| Resolution | 1280×720 | only mode the device offers |
| Frames arriving, good light | **29–30 fps** | live app `capture:` / overlay |
| Frames arriving, dim room | **19–21 fps** after ~75 s; went back to 29 when brighter | 5-min headless run, glasses facing a dark ceiling. Likely the camera's auto-exposure. **Keep the demo scene well lit.** |
| End-to-end delay | **0.96 s at `--delay 0.75`** → about 0.21 s glasses + capture + display (±20 ms) | `mentra/latency_clock.html`, 5 screenshots: 0.97 / 0.94 / 0.94 / 0.94 / 1.02 s |
| Main-loop time per frame | about 1–4 ms with no window; 25 / 31 ms (p50 / p95) in the debug window; 29 / 32 ms in `--demo` while recording with `--out` | 33 ms is the limit at 30 fps. Clean `--demo` repeat without recording still to do. |
| Leaks before consent | none seen (every 6th frame from 4–13 s, 1 per second elsewhere; a sample, not every frame) | recording of a `--demo` run |
| Peace-sign consent | unblurs about 0.6–0.8 s after both hands are up | same recording |
| Stability | no errors or reconnects in 2.5 min of headless running | the run exited after about 150 s of the requested 300; see open issues |

## Re-test after a4d7c93 (camera by name, camera reconnect, consent grace)
Run with `--source "cam:Mentra Camera" --demo --out`, about 200 s, voice-prompted steps:
- **Unplug / replug:** PASS. Showed "RECONNECTING TO MENTRA CAMERA…" for about 13 s (10 s unplugged + about 3 s), then one 1 s drop just after replugging,
  then recovered. Every 3rd frame around the reconnect checked: no face visible, and no fallback to the FaceTime camera.
- **Consent distance:** 1 m works (unblurs about 1–2 s after both hands are up); 2 m works (one of two tries);
  **3 m fails** (held 5 s, twice). Demo rule: give consent within about 2 m. A one-hand peace sign is ignored (correct).
- **Thumbs-down:** about 2–3 s at 1 m, about 8 s at 2 m.
- **Head turn after consent:** brief turns stay visible. Looking down for a sustained stretch blurs, then comes back when facing the camera again (smooth, no flicker).
- Frames arriving 24.6 fps; main-loop time 24 / 27 ms (p50 / p95) in `--demo` while recording.
- **Display sleep blacked out the screen mid-run.** The `caffeinate` already running was `-i` only. **Use `caffeinate -d`
  for the demo** (keeps the display awake).

### Similarity during head poses (`mentra/sim_check.py`, buffalo_s, 34 s raw clip)
Reference = the first 4 s looking straight (as at consent). Per-frame similarity:
| pose | similarity |
|---|---|
| straight | 0.89–0.96 |
| turn left/right | 0.48–0.63 (never under 0.3) |
| tilt | 0.22–0.42 (about half under 0.3, none under 0.2) |
| look down | 0.00–0.29, middle about 0.1–0.2 (indistinguishable from a stranger: blurring is correct) |
Only 2 of 894 frames had no face. Best case (same light, seconds apart). Suggestion: keep `grace_floor` 0.2; a longer
`grace_s` (about 1.5–2 s) would cover sustained tilts, but only after checking stranger similarity stays well under 0.2.

## Open issues
- **Consent flicker:** a consented face re-blurs when the head tilts down or turns (per-frame match
  similarity falls under `drop_thresh` 0.3). It errs on the side of privacy, but looks glitchy. Main session is adding a short grace period.
- **Early exit:** `--seconds 300` stopped at about 150 s. Likely a single failed webcam read ends capture; only
  network streams reconnect. Main session is fixing it; until then, just relaunch.
- **Unplugging mid-demo:** not tested yet. The fix above should cover it.
- **Thumbs-down revoke:** not tested on the glasses yet. Distance tests (1 / 2 / 3 m) still to do.
- **Battery and heat over USB:** not measured yet. USB probably keeps the glasses charged.

## Wireless (stretch goal, not tried)
From the docs (verified): Bluetooth SDK `startStream()` sends to an `rtmp://`, `rtmps://`, `srt://` or WHIP URL, with
`video {width,height,bitrate,fps}`. The SDK and the glasses software must be the same release (3.4.0 in the current docs).
macOS support is only on the starter kit's `dev` branch. Target URL: `rtmp://<mac-ip>:1935/live/mentra` (MediaMTX, `stream/server.sh`).
