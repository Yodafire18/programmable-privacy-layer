# Brief: evaluate creating and modifying privacy profiles from the Mentra glasses

**You are an evaluator, not a builder.** Assess feasibility and, above all, the **user experience** of letting someone create and modify privacy profiles **from Mentra Live glasses**, with the results viewed on this Mac by the wearer or a friend. **The Quest is out of scope.**

## Hard rules
- **Do not modify any code or config, do not run the app, and do not install anything.** You may read files in this repo and read web docs. The only file you write is your report: `briefs/voice_profiles_eval_REPORT.md`.
- **Don't invent facts.** Clearly separate *verified* (with a doc URL or file:line) from *assumed*. The user strongly dislikes made-up capabilities.
- Be concise and concrete. No filler, no sponsor-pleasing (neither Meta nor Mentra is a sponsor).
- Don't commit anything. Other sessions are working in this repo.

## Context
- **Product (see `VISION.md`, the user's own words):** a *programmable privacy layer* for camera glasses. You describe in plain English what your camera should never capture, test it, then wear it. **The MCP is the differentiator.**
- **Hackathon:** Stanford XR "Immerse the Bay", ManageXR track. Judged on glasses-native, AI that responds to what the user does/says/sees, and experience. A live demo in a **crowded, loud hall**.
- **What exists today** (read the code):
  - `privacy_filter/profiles.py`: the profile schema (blur targets `faces`, `screens`; consent/revoke gesture from `peace_sign, thumbs_up, thumbs_down, open_palm, fist, pointing_up, i_love_you`; 1 or 2 hands; hold time; blur strength light/medium/strong). `validate()` reports unsupported requests as `problems` instead of silently dropping them.
  - `privacy_filter/mcp_server.py`: tools `list_capabilities`, `save_profile`, `test_profile` (synthetic clips, about 1 s, reports leak frames and false-visible), `activate_profile`, `get_profile`. Used today from Claude Code on the Mac.
  - `privacy_filter/live.py`: the live app hot-reloads the active profile within about 1 s, shows a "PROFILE: …" banner, speaks it via macOS `say`, and shows a big "VISIBLE" banner on consent. `--demo` gives a full-screen filtered output with a status badge.
- **Glasses today:** Mentra Live is connected **over USB** as a webcam ("Mentra Camera", 1280×720 at 30 fps; also a "Mentra Microphone" audio device and an ADB interface). See `mentra/STATUS.md` and `MENTRA_HANDOFF.md`. Wireless streaming (RTMP via MediaMTX) is ready on the Mac but is a stretch goal. Mentra Live has **no display**; it has mics, speakers and two buttons/touch areas. Check their docs for what apps can access: https://docs.mentraglass.com/mentra-live/overview and https://docs.mentraglass.com/bluetooth-sdk/overview.
- **Idea parked earlier, not built:** press-to-talk on the glasses, then speech-to-text, then Claude with our MCP tools, then a **spoken read-back** of exactly what the rule became (including anything unsupported), then "yes" to activate.

## Questions to answer

1. **Interaction design, from the wearer's view, step by step:** creating a profile, modifying one ("also blur screens", "make it stronger"), switching between profiles, undoing a change, and checking "what's active?".
   - With no display, how does the wearer know what the rule became?
   - Where does the Mac screen help, for the wearer glancing at it or a friend watching?
   - Where does it hurt (the wearer can't see it while walking)?
2. **Confirmation and error handling:**
   - How to read back profiles concisely.
   - Handling "can't do that" (unsupported targets such as license plates, cards, children).
   - Mishearing in a loud hall.
   - Accidental activation.
   - What should need explicit confirmation and what shouldn't.
3. **Input options, compared:**
   - voice through the **Mentra mic over USB** (it appears as a Mac audio device, so speech-to-text could run on the Mac)
   - voice through a Mentra SDK path
   - glasses buttons/touch
   - gestures in front of the camera (the engine already recognizes 7 gestures)
   - the friend typing on the Mac

   For each: feasibility (verified vs. assumed), reliability in a loud hall, latency, and demo impact.
4. **The friend/viewer on the Mac:** what should they see while profiles change? A profile card, a diff from the previous profile, the test results? Is this a strength for the demo ("someone in the crowd suggests a rule and watches it appear")?
5. **Trust and safety UX:**
   - Should changes take effect instantly or after a test passes?
   - How to show that a new profile was tested.
   - The risk of someone loosening privacy by voice (e.g. "show everyone"). Should loosening need extra confirmation?
6. **Feasibility and effort:** the smallest version that delivers the core experience, with a rough conservative estimate. Note the dependencies on what exists and the main risks.
7. **Verdict:** is this worth building for the demo, versus keeping profile creation in Claude on the Mac? If yes, what's the minimal version? If no, what's a 1-line pitch mention?

## Report format (`briefs/voice_profiles_eval_REPORT.md`)
- **Verdict first** (3–5 lines).
- Then sections 1–6 above, concise. Use tables where they help.
- A list of what's **verified vs. assumed**.
- Under about 1,200 words.
