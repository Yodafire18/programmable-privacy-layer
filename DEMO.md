# Demo flow (about 3 min)

## Setup (before judges arrive)
- Mac plugged in, well-lit spot, glasses on USB (cable looped/taped).
- Terminal 1: `./demo.sh` → full-screen filtered glasses view (auto-picks "Mentra Camera").
- Terminal 2: voice tool *(landing from the voice agent)*. Typed commands work there too.
- Browser: GUI at `http://localhost:7860` *(landing from the GUI agent)*. Fallback: a `claude` session in this folder (MCP registered).
- Make sure `default` (🔒 gold) is active: in a `claude` session, `activate_profile default` (or press Activate in the GUI).
- Phone hotspot on (only Claude needs internet).

## Flow
| # | Say / do | Screen shows | Point it makes |
|---|---|---|---|
| 1 | **Hook:** "Describe in plain English what your camera should never capture, test it, then wear it." | Glasses view: every face softly blurred, badge "PRIVACY FILTER ON" | Privacy by default |
| 2 | Teammate (≤2 m from the wearer) shows **two peace signs** | Their blur fades, green **VISIBLE** banner | Consent by gesture |
| 3 | Teammate walks out of frame and back in | Still visible (remembered this session) | AI remembers who consented |
| 4 | Teammate does **two thumbs-down** | Blurred again | Consent is revocable |
| 5 | Wearer **says**: "also blur screens" *(voice; or type it)* + someone holds up a laptop | "Heard: …" caption, change card "+ screens", laptop blurred | **Normal people change rules by voice, live** |
| 6 | Wearer says: "make it stronger" | Card "blur: medium → strong" | Rules are editable on the fly |
| 7 | Someone from the crowd suggests a rule; type it into the GUI's plain-English box | Profile card + diff; anything unsupported shown as "can't do: …" | MCP: plain English → tested rule; honest refusals |
| 8 | Wearer says "go back to default" | `PROFILE: default` banner | The gold profile is locked; voice can't change it |
| 9 | **Close:** "One engine, rules in plain English, on glasses you already wear." | — | |

## If something fails
- Voice not understood in the noise → type the same command (same pipeline).
- Claude slow or no internet → use the short commands (fast path: "also blur screens", "stronger", "undo", "go back to default").
- Glasses cable out → the screen says "WAITING FOR MENTRA CAMERA…"; plug it back in and it recovers by itself.
- Everything down → play the backup video.

## Judge Q&A (one-liners)
- **Wired?** For demo reliability; the wireless path is mapped out (`WIRELESS.md`), and the Mac side is already built.
- **Missed faces?** Fail-closed: lost faces stay blurred, and the whole frame blurs during whip pans. Crowd test: 2–3 leak frames out of 240, 0 wrongly unblurred.
- **Is consent stored?** Only in memory for the session; wiped on exit. Only consenting people's face fingerprints are kept.
- **What can it blur?** Faces and screens today; anything else is refused honestly ("not supported yet").
