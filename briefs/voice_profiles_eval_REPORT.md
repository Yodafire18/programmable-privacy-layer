# Report: creating and modifying privacy profiles from Mentra Live

## Verdict
**Build a small version. Skip the full voice-from-the-glasses loop.** Over USB, the only glasses input we have verified is the **"Mentra Microphone"**. We have not verified that buttons or the glasses speaker work over USB. Speech in a loud hall is the riskiest part of the demo.
**Build these:** (1) a **profile card with a diff** on the Mac. It helps every path, including today's Claude-on-Mac flow. (2) An optional **hold-a-key-to-talk** path: Mentra mic → speech-to-text on the Mac → Claude → read-back → confirm. The **friend or crowd typing into Claude** stays the fallback the demo depends on.
**Fix two safety gaps first, whichever path you pick** (see §5): saving over the active profile makes the change live without activation, and `ok: true` from `test_profile` does not mean the test passed.

## 1. Interaction design (the wearer's view)
| Action | Flow | How the wearer knows the result |
|---|---|---|
| Create | Hold key → "Blur faces and screens, one thumbs-up to be seen" → read-back → "yes" | Spoken read-back; card on the Mac |
| Modify | "Also blur screens" / "make it stronger" → read-back **of only what changed** ("Added: screens. Strength: medium → strong") → "yes" | Diff on the card |
| Switch | "Use thumbs-up consent" → "Switching to thumbs-up consent: …one-line summary" → no confirm unless it loosens (§5) | Existing `PROFILE:` banner + `say` (`live.py:351-352`) |
| Undo | "Undo" → re-activate the previous profile | Same banner. **Needs new code:** `profiles.save` overwrites the file (`profiles.py:117-121`) and keeps no history |
| What's active? | "What's on?" → the one-line summary | Built from the profile, not from the LLM |

- **No display:** the wearer's only channel is audio. The read-back must come from a **template over the validated profile**, not from Claude's wording. Then what's spoken is exactly what the engine does.
- **The Mac screen helps** the friend, audience and judges (card, diff, test result, refusals), and a wearer standing still. **It hurts** a wearer who is walking or facing a subject: no screen in view, and the Mac speaker is drowned out.

## 2. Confirmation and errors
- **Read-back**, fixed order (targets, strength, consent, revoke, refusals): *"Faces and screens, strong. Two peace signs to be seen, two thumbs-down to hide. Can't do: license plates."*
- **"Can't do that":** `validate()` already returns `problems` (`profiles.py:66-68`). `list_capabilities` already lists plates, cards, documents, children-only rules and turning faces off as unsupported (`mcp_server.py:31-33`). Always speak the refusal **first**, before any confirmation.
- **Mishearing in a loud hall:** the read-back plus a confirmation catches it. A spoken "yes" can be misheard too, so confirm with a **key press on the Mac** (wearer or friend). A spoken yes/no can be a secondary option.
- **Accidental activation:** don't use a wake word. Use hold-to-talk only, and never activate without a confirmation.
- **What needs confirmation:**

| Change | Confirmation |
|---|---|
| New or tighter rule (add screens, stronger blur, 2 hands) | One confirmation |
| Switch to a profile already saved | None, just announce it |
| Undo | None |
| Looser rule (§5) | Explicit confirmation, with the loosening named |

## 3. Input options
| Input | Feasibility | Loud hall | Latency | Demo impact |
|---|---|---|---|---|
| Mentra mic over USB, speech-to-text on the Mac | Mic device **verified** (`mentra/STATUS.md`). Speech-to-text engine, mic quality and noise rejection **assumed**. Nothing is installed today. Calling Claude from a script needs an API client and key (assumed, not set up) | Unknown; biggest risk | Assumed 2–5 s (speech-to-text + Claude call + `test_profile`) | High: "talk to your glasses" |
| Mentra Bluetooth SDK mic | Events `mic_pcm`/`mic_lc3`, VAD and loudness gate **verified in docs** ([API ref](https://docs.mentraglass.com/bluetooth-sdk/api-reference.md)). On native macOS, support exists only on the **`dev` branch**, and "local STT/TTS … not included" ([macOS](https://docs.mentraglass.com/bluetooth-sdk/macos.md)). Whether it can run alongside USB is unknown | Loudness gate may help (assumed) | Unknown | Same as above, much more effort |
| Glasses buttons/touch | `button_press` (`buttonId`, `pressType`) and `touch_event` are **verified, via the Bluetooth SDK only**. Over USB: **unknown**. Press types and button IDs are not documented | Excellent | Low | The best push-to-talk, if the Bluetooth path works |
| Gestures to the glasses camera | Engine recognizes 7 gestures (`profiles.py:22-30`, verified). Reusing them for commands is **assumed** | Silent, so unaffected | About 1 s | **Conflicts with consent:** a bystander's thumbs-up could switch the profile. Avoid |
| Friend typing on the Mac | **Works today** (Claude Code + MCP) | Unaffected | Claude's turn time | Strong and reliable; shows off the MCP |

## 4. The friend or viewer on the Mac
Show a **profile card** (name, targets, strength, consent/revoke, refusals in red), a **diff** from the previous profile, and test results (leaks / false-visible, plus what wasn't tested). Yes, it's a demo strength: "someone in the crowd suggests a rule and watches it appear and get refused or applied" shows natural language → validated rule → live filter. It works with the friend typing, so the demo doesn't depend on speech-to-text.

## 5. Trust and safety
- **Test before activating, as a hard gate? No.** The leak gate is 0 (`eval/__main__.py:30`). The saved run `runs/profile-thumbs-up-consent/summary.txt` shows **leak_frames=7 FAIL**, so a hard gate would block every profile. The test also skips gestures and screens (`mcp_server.py:77-81`), so it barely reflects voice edits. **Run it and show the numbers, but don't block on them.** Fail-closed comes from the engine, not from the test.
- **Show that it was tested:** a badge "tested 3 synthetic clips: N leak frames, 0 false-visible", with a timestamp, on the card.
- **Gap: `ok` means "the test ran".** `test_profile` sets `ok = returncode == 0` (`mcp_server.py:75`), and `eval run` exits 0 even when a gate fails. Don't show `ok` as "passed".
- **Gap: saving can go live without activation.** The live app reloads when the active profile's file changes (`live.py:328-352`). So `save_profile` on the active name goes live immediately, even though its note says "Saved but not active" (`mcp_server.py:62`). Voice edits should save under a new name, or the note should be fixed.
- **Loosening:** "show everyone" can't happen, because faces are always added back (`profiles.py:70-72`). Real loosening: removing screens, 2 → 1 hands, lighter blur, a common gesture (open_palm, thumbs_up), and a short `hold_s`. **`hold_s` has no bounds check** (`profiles.py:82-83`), so 0 or a negative value passes. Loosening should need explicit confirmation and be named in the read-back. Add a floor on `hold_s`.

## 6. Smallest version and effort (conservative)
| Piece | Builds on | Estimate |
|---|---|---|
| Template read-back + diff (pure function over profile dicts) | `validate()` | 1–2 h |
| Mac card/diff panel (overlay in `--demo` or a separate window) | `live.py` banner code | 2–3 h |
| Save each edit under a new name + "undo" = activate previous | `profiles.activate` | 1 h |
| Hold-key recorder from "Mentra Microphone" → speech-to-text | needs an STT engine (not installed) | 3–5 h incl. noise testing |
| Claude call with the profile functions as tools, confirm key | needs API access (not set up) | 3–4 h |
| **Total** | | **About 1.5 days for everything; about 0.5 day for the first three rows** |

**Risks:** speech-to-text in the hall; read-back inaudible (glasses speaker over USB unverified); CPU contention on the 8 GB M1 if speech-to-text runs locally.

## 7. Recommendation
Build rows 1–3 now, plus the safety fixes. Add voice only if a noise test with the Mentra mic in a loud room passes. If it doesn't pass, use this one-liner in the pitch: *"Rules come from plain English through our MCP: today typed, next spoken straight into the glasses, with a read-back of exactly what the rule became."*

## Verified vs. assumed
**Verified (code/docs):**
- Profile schema, refusals and faces-always rule: `profiles.py:22-38, 61-92`
- Saving overwrites: `profiles.py:117-121`
- Live hot-reload on file change, banner and `say`: `live.py:55-57, 328-352`
- `test_profile`'s `ok` comes from the exit code: `mcp_server.py:75`
- Leak gate is 0; the saved profile test failed with 7 leaks; the eval part took 0.4 s with cached detections: `runs/profile-thumbs-up-consent/`
- USB devices "Mentra Camera" and "Mentra Microphone": `mentra/STATUS.md`
- Bluetooth SDK: button/touch/mic events, VAD, loudness gate, no display; macOS only on `dev`, no local STT/TTS (docs above)

**Assumed or unknown:** Mentra mic quality in the hall; buttons/speaker over USB; Bluetooth SDK alongside USB; speech-to-text choice and latency; Claude API access from a script; `test_profile` subprocess startup time; all estimates.
