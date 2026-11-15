"""MCP server: describe privacy rules in plain English to Claude; it turns them into profiles,
tests them on synthetic footage, and activates them on the live camera.

Register with Claude Code:
  claude mcp add privacy-filter -- /Users/ani/Documents/meta-privacy/.venv/bin/python -m privacy_filter.mcp_server
"""
import subprocess
import sys

from mcp.server.mcpserver import MCPServer

from . import profiles as PR

server = MCPServer(
    name="privacy-filter",
    instructions=(
        "Programmable privacy layer for camera glasses. Turn the user's plain-English rules into a profile with "
        "save_profile, run test_profile on it, then activate_profile to apply it to the live camera. "
        "Call list_capabilities first. If the user asks for something the engine can't do, say so plainly; "
        "never claim an unsupported target or gesture is handled."
    ),
)


@server.tool()
def list_capabilities() -> dict:
    """What the engine can blur, which gestures can grant/revoke consent, and blur strengths."""
    PR.ensure_default()
    return {
        "blur_targets": PR.SUPPORTED_TARGETS,
        "not_supported_yet": "objects (license plates, cards, documents, X-rays, brands), children-only rules, "
                             "per-person rules other than gesture consent",
        "gestures": list(PR.GESTURES),
        "gesture_hands": [1, 2],
        "blur_strengths": list(PR.STRENGTH),
        "behaviour": "Faces are blurred by default. A person who makes the consent gesture (held hold_s seconds) "
                     "is unblurred and remembered for the session; the revoke gesture blurs them again. "
                     "Uncertain matches stay blurred.",
        "profiles": PR.names(),
        "active_profile": PR.active_name(),
    }


@server.tool()
def save_profile(name: str, description: str, consent_gesture: str = "peace_sign", consent_hands: int = 2,
                 hold_seconds: float = 0.75, revoke_gesture: str = "thumbs_down", revoke_hands: int = 2,
                 blur_strength: str = "medium", blur_targets: list[str] | None = None) -> dict:
    """Create or overwrite a privacy profile. `description` should restate the user's rule in plain English.
    Unsupported targets/gestures are reported in `problems` and not applied."""
    clean, problems = PR.save({
        "name": name, "description": description,
        "blur_targets": blur_targets or ["faces"],
        "consent": {"gesture": consent_gesture, "hands": consent_hands, "hold_s": hold_seconds},
        "revoke": {"gesture": revoke_gesture, "hands": revoke_hands},
        "blur_strength": blur_strength,
    })
    return {"saved": clean, "file": str(PR.path(clean["name"])), "problems": problems,
            "note": "Saved but not active. Run test_profile, then activate_profile."}


@server.tool()
def test_profile(name: str) -> dict:
    """Run the profile through the synthetic test clips (moving-camera group shots) and report privacy leaks.
    Detection results are cached, so this takes seconds."""
    prof = PR.load(name)
    cmd = [sys.executable, "-m", "eval", "run", "--name", f"profile-{prof['name']}", "--set", *PR.to_overrides(prof)]
    r = subprocess.run(cmd, cwd=PR.ROOT, capture_output=True, text=True, timeout=600)
    lines = [l for l in r.stdout.splitlines() if l.startswith(("GATES", "TOTAL", "synth", "clip", "  LEAK"))]
    return {
        "profile": prof["name"],
        "ok": r.returncode == 0,
        "report": "\n".join(lines) or r.stderr[-1500:],
        "what_was_tested": "Blur coverage on synthetic clips (slow pan, whip pan, zoom): every face must stay blurred. "
                           "leak_frames = frames where a face was not fully covered; false_visible = someone unblurred "
                           "without consent. The synthetic clips contain no gestures, so the consent gesture itself "
                           "is not exercised here; try it live after activating.",
    }


@server.tool()
def activate_profile(name: str) -> dict:
    """Make this profile live. The running camera app picks it up within a second."""
    PR.activate(name)
    return {"active_profile": PR.active_name(), "profile": PR.load(name)}


@server.tool()
def get_profile(name: str) -> dict:
    """Show a saved profile."""
    return PR.load(name)


def main():
    PR.ensure_default()
    server.run("stdio")


if __name__ == "__main__":
    main()
