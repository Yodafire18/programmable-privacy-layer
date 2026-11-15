# Brief: help the user record the 30-second reel, shot by shot (interactive)

You're coaching the user **live** while they record footage for a 30 s vertical TikTok/Reels sizzle reel of their hackathon project (a programmable privacy layer for camera glasses; see `VISION.md` and `DEMO.md`). Be quick, friendly and concrete: one shot at a time. Tell them exactly what to do, wait for them to say they're done, check the file, then move on.

## How the user wants you to work
- Short instructions. KISS.
- Confident tone. **No "refusal" / "not supported" moment in the reel.**
- Don't invent capabilities. Only show what works: faces blurred by default, two peace signs to be seen, two thumbs-down to hide, voice "also blur screens" (laptop/phone gets blurred), the GUI plain-English box switching profiles, the locked gold default.

## What's already made
`media/reel_animation.mp4` (13 s, 1080×1920, 30 fps): abstract figures + MCP beat + architecture + "live in ~1 s" + end card. Also `media/end_card.mp4` (3 s) and `media/end_card.png`. Generator: `media/make_reel_animation.py`. The reel = **about 17 s of real footage + this animation** (or trim the animation).

## Shot list (about 17 s of real footage; record each 2–3×)
| # | Length | Shot | How to record |
|---|---|---|---|
| 1 | 3 s | **Hook:** glasses view, everyone blurred | In the glasses view window (`./demo.sh`, already `--demo`), press **`r`** to start/stop recording → `output/clip_HHMMSS.mp4` (filtered output only; raw faces are never saved) |
| 2 | 5 s | **Consent:** a teammate about 1 m away shows ✌️✌️, fades in (green VISIBLE), then 👎👎, blurred again | Same `r` recording |
| 3 | 5 s | **Voice:** wearer says *"also blur screens"*, someone holds up a laptop/phone and it blurs; "Heard: …" caption + change card | `r` recording **plus** a phone video of the wearer talking (cutaway). Only film people who are OK with it |
| 4 | 4 s | **GUI:** type *"Blur faces and screens, strong"* (or click an example chip), watch the profile switch | macOS screen recording: **Cmd-Shift-5** → "Record Selected Portion" around the browser → stop in the menu bar. Saves to Desktop (`.mov`) |
| (opt) | 2 s | Glasses on someone's face, a quick b-roll of the hardware | Phone |

Tips:
- Good light (the glasses camera drops to 18–20 fps in dim light).
- Before shots 1–3, check that the glasses view shows "PRIVACY FILTER ON" and the right profile.
- Put the profile back with "go back to default" afterwards.

## Starting things (if not running)
- Everything: `./start.sh` (opens the GUI, the glasses view and the voice tool), or the GUI's Start buttons at http://localhost:7860.
- Voice: press Enter in the voice terminal, speak, and stop talking.

## After each shot
- Find the newest file (`ls -t output/*.mp4 | head -3`, or `~/Desktop/*.mov`), check it with `ffprobe` (duration, size), and extract 1–2 frames with ffmpeg to look at (Read the PNG): **check that no unconsented face is visible**, the action is in frame, and it's in focus. If bad, say what to fix and re-record.

## At the end: rough cut (optional, if the user wants)
- Convert each 16:9 clip to vertical **1080×1920**: scale to 1080 wide and center it on a dark background (or blur-fill the background), with the shot trimmed to its best seconds (`-ss`/`-t`).
- Concatenate shots 1→2→3→4 + `media/reel_animation.mp4` into `media/reel_rough_cut.mp4` (H.264, yuv420p, 30 fps, no audio; the user adds music in TikTok/CapCut). Target about 30 s.
- Look at frames from the result before handing it over.

## Rules
- **Don't edit code** in `privacy_filter/`, `gui/`, `eval/` (other agents are working). You may create files in `media/` and read `output/`.
- **Never commit videos of people.** (`output/` is gitignored; don't add raw or rough-cut footage to git.) Committing a small helper script in `media/` is fine: `git -c user.name="ani" -c user.email="anu.srini21@gmail.com" commit -m "..." -- media/<script>`, the message ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
