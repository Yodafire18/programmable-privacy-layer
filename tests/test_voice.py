"""Voice commands: locked default, voice profile + undo, fast parser, template read-back (no audio, no Claude)."""
import pytest

from privacy_filter import mcp_server as M
from privacy_filter import profiles as PR
from privacy_filter import voice as V
from privacy_filter import voice_state as VS


@pytest.fixture(autouse=True)
def tmp_profiles(tmp_path, monkeypatch):
    d = tmp_path / "profiles"
    monkeypatch.setattr(PR, "DIR", d)
    monkeypatch.setattr(PR, "ACTIVE", d / "ACTIVE")
    monkeypatch.setattr(PR, "HISTORY", d / ".history")
    monkeypatch.setattr(VS, "STATE", d / "VOICE_STATE.json")
    PR.ensure_default()
    return d


def run(text):
    return V.handle(text, use_claude=False, speak=False, log=lambda *_: None)


def test_default_is_locked_and_voice_exists():
    assert PR.is_locked("default") and not PR.is_locked("voice")
    assert PR.same_rules(PR.load("default"), PR.load("voice"))
    with pytest.raises(PR.LockedProfile):
        PR.save({**PR.load("default"), "blur_strength": "strong"})
    r = M.save_profile("default", "x", blur_targets=["faces", "screens"])
    assert r["refused"] and "locked" in r["error"]
    assert PR.load("default")["blur_targets"] == ["faces"]


def test_voice_edit_goes_to_voice_and_activates():
    r = run("also blur screens and make it stronger")
    assert r["profile"] == "voice" and PR.active_name() == "voice"
    p = PR.load("voice")
    assert p["blur_targets"] == ["faces", "screens"] and p["blur_strength"] == "strong"
    assert PR.load("default")["blur_targets"] == ["faces"]
    assert r["spoken"] == "Done. Now blurring screens and using a strong blur."
    assert VS.read_state()["lines"] == [["+", "blur screens"], ["~", "blur medium -> strong"]]


def test_refusal_spoken_first_and_no_change():
    run("also blur screens")
    r = run("blur license plates and use one thumbs up")
    assert "I can't blur license plates yet." in r["spoken"]
    assert "one thumbs up to be seen" in r["spoken"].lower()
    r = run("blur license plates")
    assert r["refusals"] == ["license plates"] and r["changes"] == []


def test_undo_and_back_to_default():
    run("also blur screens")
    run("use one thumbs up")
    r = run("undo")
    assert PR.load("voice")["consent"]["gesture"] == "peace_sign" and "screens" in PR.load("voice")["blur_targets"]
    assert r["spoken"].startswith("Undone.")
    run("undo")
    assert PR.load("voice")["blur_targets"] == ["faces"]
    assert run("undo")["spoken"] == "Nothing to undo."
    run("also blur screens")
    r = run("go back to default")
    assert PR.active_name() == "default" and r["action"] == "switch"
    assert PR.load("default")["blur_targets"] == ["faces"]
    assert run("what's on")["spoken"].startswith("You're on the default profile.")


@pytest.mark.parametrize("text,want", [
    ("Stop blurring screens.", ("edit", [("screens", False)])),
    ("Make it lighter", ("edit", [("strength", -1)])),
    ("News thumbs up.", ("edit", [("consent", "thumbs_up", None)])),
    ("Two hands", ("edit", [("consent", None, 2)])),
    ("Undo.", "undo"),
    ("What's on?", "status"),
    ("Only blur my coworkers", None),            # not understood locally -> Claude
])
def test_fast_parser(text, want):
    assert V.parse_fast(text, PR.names()) == want
