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
