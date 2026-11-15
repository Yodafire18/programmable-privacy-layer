# Going wireless: what would have to change

The demo runs **over USB** (glasses = webcam + mic). These are the notes for cutting the cable later.
*Verified* = checked in docs or tested. *Unverified* = still to check.

| Piece | USB today | Wireless | Status |
|---|---|---|---|
| **Video** | "Mentra Camera" webcam → `./demo.sh usb` | Glasses publish RTMP/SRT to `rtmp://<mac-ip>:1935/live/mentra` → MediaMTX → `./demo.sh glasses` | **Mac side done and tested** (fake stream, auto-reconnect). Glasses side: Bluetooth SDK `startStream()` with an rtmp/rtmps/srt/WHIP URL + `video {width,height,bitrate,fps}` (*verified in docs*); never tried on the glasses |
| **Mic (voice commands)** | "Mentra Microphone" USB audio device | Options: (a) audio inside the RTMP stream, extracted from MediaMTX; (b) Bluetooth SDK `mic_pcm` / `mic_lc3` events (*verified in docs*); (c) glasses paired to the Mac as a Bluetooth headset | Unverified. (a) is simplest if the stream carries audio |
| **Buttons** | ADB over USB (being tested) | Bluetooth SDK `button_press` / `touch_event` (*verified in docs*), or ADB over Wi-Fi (`adb tcpip`) | Unverified |
| **Spoken read-back** | Mac speaker (no glasses speaker over USB, *verified*) | Glasses speaker via Bluetooth audio, or keep the Mac speaker | Unverified |
| **Power** | USB powers the glasses | About 45 min of streaming per charge (spec) | Charge between demos |
| **Network** | none | Private hotspot/travel router; macOS firewall must allow `mediamtx`; Mac IP changes on the hotspot (`./demo.sh` prints it) | Mac side ready |
| **SDK setup** | none | Bluetooth SDK and glasses software must be the same release (3.4.0 in current docs); macOS support only on the starter kit's `dev` branch, so a phone app may be easier | *Verified in docs* |

**Code changes needed on our side:** probably none for video (`./demo.sh glasses` exists). For voice: make the recorder read audio from the stream or the SDK instead of the USB mic device (one input function). For buttons: swap the ADB listener for SDK events.

**Delay:** USB measured 0.96 s glasses-to-screen at `--delay 0.75`. Wireless adds encode + Wi-Fi + MediaMTX; measure with `mentra/latency_clock.html`.
