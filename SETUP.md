# Setup on a new Mac (Apple Silicon)

Needs: macOS on Apple Silicon, Claude Code installed and logged in, internet for the first run.

## 1. Install
```
git clone https://github.com/Yodafire18/programmable-privacy-layer && cd programmable-privacy-layer && ./setup_mac.sh
```
`setup_mac.sh` checks the Xcode Command Line Tools, installs `python@3.12` and `ffmpeg` with Homebrew (it asks first; `--yes` skips the questions), creates `.venv`, installs `requirements.txt`, downloads the face and speech models, makes the synthetic test clips, warms the test cache, registers the `privacy-filter` MCP server with Claude Code, and runs the unit tests. Re-running it is safe: finished steps are skipped. `./setup_mac.sh --dry-run` shows what it would do.

## 2. Run
1. Log in to Claude Code once: `claude` (the dashboard and voice tool call `claude -p`).
2. Plug the Mentra Live glasses in over USB. They show up as "Mentra Camera" and "Mentra Microphone".
3. `./start.sh` opens the dashboard (http://localhost:7860), the glasses view and the voice tool.
4. Allow the macOS prompts: **Camera** and **Microphone** for Terminal, and **Automation** (Terminal controlling Terminal; the dashboard's Start buttons open Terminal windows). If you missed one: System Settings → Privacy & Security.

No glasses: `./demo.sh webcam` (FaceTime camera) or `./demo.sh clip` (recorded synthetic clip). Demo flow: `DEMO.md`.

## 3. Troubleshooting
| Problem | Fix |
|---|---|
| Wrong camera | List devices: `ffmpeg -f avfoundation -list_devices true -i ""`. `./demo.sh usb` waits for "Mentra Camera" and never falls back. For another camera: `WEBCAM="cam:<name>" ./demo.sh webcam`. |
| No mic / voice hears nothing | Allow Microphone for Terminal, then restart the voice tool. It uses "Mentra Microphone" and falls back to the default input; pick one with `.venv/bin/python -m privacy_filter.voice --mic "<name>"`. Typing a command into the voice tool works too. |
| MCP not connected | Run `claude mcp list` in the repo folder. If `privacy-filter` is missing or failing: `claude mcp remove privacy-filter`, then re-run `./setup_mac.sh`. Start `claude` from this folder (the server is registered per folder). |
| Slow first run | Normal: about 1 GB of downloads, then the test cache runs the face models over the clips once. Later runs skip all of it. A `test_profile` call is slow only until the cache exists. |
| Memory / swap | Close heavy apps (browsers with many tabs, video editors). Check Activity Monitor → Memory Pressure. Run only what you need, e.g. `./demo.sh` without the dashboard. |
| Disk space | Needs about 3 GB free. Pip's download cache can go afterwards: `.venv/bin/python -m pip cache purge`. Models live in `~/.insightface/models` and `~/.cache/huggingface/hub`. |

## 4. Time and download size (estimates)
- **Download:** about 1 GB on the first run: about 0.45 GB of Python packages plus about 0.55 GB of models (InsightFace `buffalo_s` 122 MB and `buffalo_l` 275 MB, Whisper `base.en` about 145 MB). Homebrew packages are extra if they are missing.
- **Time:** about 10 to 20 minutes on a fast connection, mostly downloads plus a few minutes warming the test cache. Add time for the Xcode tools or Homebrew if they are not already installed.
- **Disk:** about 3 GB once installed (`.venv` is about 1 GB).
