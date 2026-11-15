# Demo + pitch (about 3 min)

## 1. Hook + why (20 s)
**"Describe, in plain English, what your camera should never capture. Test it. Then wear it."**

> Finding the story was our biggest challenge. Even the three of us saw it differently, and everyone we showed it to wanted something different. A vlogger wants bystanders gone. A doctor wants documents and X-rays gone. A streamer wants a drink brand gone. That told us it shouldn't be one filter. **It should be a programmable privacy layer.** And because of MCP, the rules are written in natural language, in ways that are beautiful and testable.

## 2. The architecture: one engine, many front doors, many devices
```
 FRONT DOORS  (how rules get written)
   Claude / any MCP client   web GUI :7860   voice on the glasses   typing
            \                    |                 |               /
             +------------- MCP tools (one interface) -----------+
               list_capabilities  save_profile  test_profile
               activate_profile   get_profile
                                   |
 POLICY  (what the rules are)      v
   profiles/*.yaml: targets, consent + revoke gesture, hands, hold, strength
   validated  |  unsupported asks come back as "problems", never faked
   tested on synthetic clips  |  "default" = locked gold profile
                                   |  hot-reload, about 1 s
 ENGINE  (how rules are enforced)  v
   detect -> track -> consent memory (session only) -> fail-closed blur
                                   |
 DEVICES   in:  Mentra glasses (USB today; wireless Mac side built), webcam, any stream
           out: screen, recording, livestream
```
**What MCP buys us:**
- **Any AI agent or app is a front door.** Claude, our GUI and the glasses voice path all go through the same tools. Nobody gets a private back door.
- **Rules are testable before they go live.** `test_profile` runs them on synthetic clips and reports leak frames.
- **The policy layer is honest.** It says "not supported yet" instead of pretending.
- **New detectors become new capabilities.** Add a detector, list it as a target, and every front door can use it with no changes.

## 3. The flow
**Roles:** **W** = wearer (glasses on). **T** = teammate at the Mac (GUI + Claude). **C** = crowd member / judge.

| # | Who: say / do | Screen shows | The point |
|---|---|---|---|
| 1 | **W:** hook line (section 1), looks at the crowd | Every face softly blurred, "PRIVACY FILTER ON" | Private by default (10 s) |
| 2 | **C** (within 2 m) shows **two peace signs**, then two thumbs-down | Blur fades, green VISIBLE; then blurred again | Consent is a gesture, and it's revocable (20 s) |
| 3 | **W says:** *"Also blur screens."* C holds up a phone or laptop | "Heard: also blur screens" + change card "+ screens"; the device blurs | **A normal person changes the rule by voice, live** |
| 4 | **T to C:** *"What should my glasses never capture? Tell me in one sentence."* T types it into the GUI's plain-English box (or Claude) | **The tool calls on screen:** `list_capabilities` → `save_profile` → `test_profile` (leak numbers) → `activate_profile` | **The climax.** T says: *"This is the same interface any app or agent can use. Claude is just one client."* |
| 5 | (automatic) | Glasses view switches about 1 s later; `PROFILE: <name>` banner | Written in English, enforced on the glasses |
| 6 | **T types the vlogger's rule:** *"Blur bystanders and license plates."* | Faces: done. **"License plates: not supported yet"** in the problems list | The policy layer tells the truth. That's what makes it safe to automate |
| 7 | **W says:** *"Go back to default."* | `PROFILE: default` (locked gold) | Experiments are cheap; the safe baseline is locked |
| 8 | **W, close:** *"One privacy layer. Rules in plain English, from any front door, tested, then worn."* | | |

**If the crowd's rule is mostly unsupported** (likely), that's step 6 happening early. Say: *"It just told you what it can't do instead of pretending. Here's what it can."* Then use a backup rule: *"Blur faces and screens, strong, one thumbs-up to be seen."*

**Exact backup phrases:** voice: "also blur screens", "make it stronger", "undo", "go back to default". GUI/Claude: "Blur faces and screens, strong, one thumbs-up to be seen."

## 4. Where this goes (roadmap, **not built**)
- **Next: more detectors as targets.** License plates, documents, X-rays, brand logos. Each one becomes a new `blur_target`; the MCP tools, GUI and voice don't change.
- **Next: doctors.** *"Blur documents, X-rays and children's faces"*: described in natural language on a headset, tested with synthetic data, tuned, then worn.
- **Next: vloggers and streamers.** Bystanders and license plates out of a live stream; a drink brand they don't like covered up.
- **Next: profiles that travel.** The same YAML profile on glasses, a headset, a webcam or a stream server.
- **Next: cut the cable.** Wireless video is built and tested on the Mac side (`WIRELESS.md`); the glasses side is untested.

## 5. If something fails
- **Voice misheard or not ready:** type the same phrase (same pipeline); or T says it into the GUI.
- **GUI not ready:** use the `claude` session in this folder (MCP registered); the tool calls show in the terminal anyway.
- **Claude slow / no internet:** voice short commands use the offline fast path; for step 4, T says "here's one I made earlier" and activates `faces-and-screens`.
- **Glasses cable out:** screen says "RECONNECTING TO MENTRA CAMERA…" (still no leak); replug, it recovers by itself.
- **Gesture not picked up:** step closer (within 2 m), both hands, good light, look at the camera after.
- **Everything down:** play the backup video.

## 6. Judge Q&A (one-liners)
- **Why MCP instead of an app with settings?** Everyone wanted different rules, and a settings screen only has the toggles we thought of. With MCP, any agent or app can write, test and deploy rules through the same five tools, and the policy layer refuses what the engine can't do. Today Claude, our GUI and voice all use it.
- **Is this reusable beyond glasses?** The engine already takes the glasses, a webcam or a network stream, and outputs to screen or recording. Profiles are plain files. Livestream output, headsets and new detectors are next, not done.
- **Is a test pass a guarantee?** No. It reports leak frames on synthetic clips (faces only; gestures and screens are tested live). Safety comes from the engine failing closed.
- **Missed faces?** Fail-closed: lost faces stay blurred, and the whole frame blurs during whip pans. Crowd test: 2–3 leak frames out of 240, 0 wrongly unblurred.
- **Can a rule turn privacy off?** No. Faces are always blurred; the `default` profile is locked.
- **Is consent stored?** In memory, for the session only; wiped on exit. Only consenting people's face fingerprints are kept.
- **What can it blur?** Faces and screens today. Anything else is refused honestly ("not supported yet").
- **Wired?** For demo reliability. Wireless is mapped out and the Mac side is built (`WIRELESS.md`).
- **Latency?** About 1 s glasses-to-screen, mostly a deliberate buffer so faces are found before a frame is shown.

## 7. The 30-second version (hallway)
> "Everyone we showed this to wanted different privacy rules: doctors, vloggers, streamers. So we didn't build a filter, we built a programmable privacy layer. You describe a rule in plain English, through Claude, a web page, or by talking to your glasses. Our MCP turns it into a profile, tests it on synthetic video, and the glasses enforce it within a second. Faces are blurred by default; two peace signs and you're visible. If you ask for something it can't do yet, like license plates, it tells you. Any AI agent can use the same tools, so new detectors become new rules for everyone."

## Setup (before judges arrive)
- Mac plugged in, well-lit spot, glasses on USB (cable looped/taped).
- Terminal 1: `./demo.sh` → full-screen filtered glasses view (auto-picks "Mentra Camera"); runs under `caffeinate`.
- Terminal 2: voice tool (typed commands work there too).
- Browser: GUI at `http://localhost:7860`, with the tool-call/test panel visible. Backup: a `claude` session in this folder.
- Activate `default` (GUI Activate button, or `activate_profile default` in Claude).
- Phone hotspot on (only Claude needs internet).
