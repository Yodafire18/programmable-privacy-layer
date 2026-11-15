"""Voice pipeline self-test without a human: macOS `say` speaks the commands into files, ffmpeg mixes in noise
(pink noise, and crowd babble made of other `say` voices), faster-whisper transcribes, and the fast parser's
intent on the transcript is compared with its intent on the true text.

  .venv/bin/python scripts/voice_selftest.py [--model base.en] [--out /tmp/voice_selftest] [--e2e]

--e2e also runs every clean clip through the whole pipeline (handle(), no speech) and reports
audio-in -> profile-live latency. Audio is written outside the repo (default: $TMPDIR).
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from privacy_filter import profiles as PR          # noqa: E402
from privacy_filter import voice as V              # noqa: E402

COMMANDS = [
    "Also blur screens", "Stop blurring screens", "Make the blur stronger", "Make it lighter",
    "Use thumbs up", "Use a peace sign", "Use an open palm", "One hand", "Two hands",
    "Go back to default", "Undo", "What's on?", "Also blur screens and make it stronger",
    "Blur license plates",
]
BABBLE = ["So I was telling him about the conference yesterday and he just laughed",
          "Did you see the demo over there, the one with the robot arm",
          "We should grab coffee after this talk if you have time"]


def sh(*a):
    subprocess.run(a, check=True, capture_output=True)


def words(t):
    return re.sub(r"[^a-z0-9' ]", " ", V.normalize(t)).split()


def wer(ref, hyp):
    r, h = words(ref), words(hyp)
    d = [[i + j if i * j == 0 else 0 for j in range(len(h) + 1)] for i in range(len(r) + 1)]
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (r[i - 1] != h[j - 1]))
    return d[-1][-1] / max(1, len(r))


def make_clips(out):
    out.mkdir(parents=True, exist_ok=True)
    bab = out / "babble.wav"
    if not bab.exists():
        parts = []
        for k, (voice, txt) in enumerate(zip(["Daniel", "Karen", "Fred"], BABBLE)):
            p = out / f"bab{k}.aiff"
            r = subprocess.run(["say", "-v", voice, "-o", str(p), txt], capture_output=True)
            if r.returncode:
                sh("say", "-o", str(p), txt)
            parts.append(str(p))
        sh("ffmpeg", "-y", "-v", "error", *sum([["-stream_loop", "-1", "-i", p] for p in parts], []),
           "-filter_complex", f"amix=inputs={len(parts)}:duration=longest,atrim=0:20", "-ar", "16000", "-ac", "1",
           str(bab))
    clips = []
    for i, cmd in enumerate(COMMANDS):
        clean = out / f"c{i:02d}_clean.wav"
        if not clean.exists():
            a = out / f"c{i:02d}.aiff"
            sh("say", "-o", str(a), cmd)
            # 0.4 s lead-in / 0.6 s tail, like a real press-and-talk
            sh("ffmpeg", "-y", "-v", "error", "-i", str(a), "-af", "adelay=400,apad=pad_dur=0.6", "-ar", "16000",
               "-ac", "1", str(clean))
        clips.append((cmd, "clean", clean))
        pink = out / f"c{i:02d}_pink.wav"
        if not pink.exists():   # steady hall noise, roughly 10 dB below the voice
            sh("ffmpeg", "-y", "-v", "error", "-i", str(clean), "-f", "lavfi", "-i",
               "anoisesrc=color=pink:amplitude=0.06:sample_rate=16000", "-filter_complex",
               "[0][1]amix=inputs=2:duration=first:normalize=0", "-ar", "16000", "-ac", "1", str(pink))
        clips.append((cmd, "pink", pink))
        crowd = out / f"c{i:02d}_crowd.wav"
        if not crowd.exists():  # other people talking nearby (babble at ~0.5x the voice level)
            sh("ffmpeg", "-y", "-v", "error", "-i", str(clean), "-i", str(bab), "-filter_complex",
               "[1]volume=0.5[b];[0][b]amix=inputs=2:duration=first:normalize=0", "-ar", "16000", "-ac", "1",
               str(crowd))
        clips.append((cmd, "crowd", crowd))
    return clips


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="base.en")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "voice_selftest"))
    ap.add_argument("--e2e", action="store_true")
    a = ap.parse_args()
    clips = make_clips(Path(a.out))
    stt = V.STT(a.model, threads=a.threads)
    print(f"faster-whisper {a.model}: load {stt.load_s:.1f}s, {len(clips)} clips")
    names = PR.names()
    stats = {}
    for cmd, cond, p in clips:
        audio = V.load_audio_file(str(p))
        t0 = time.monotonic()
        hyp = stt(audio)
        dt = time.monotonic() - t0
        ok = V.parse_fast(hyp, names) == V.parse_fast(cmd, names)
        s = stats.setdefault(cond, {"n": 0, "ok": 0, "wer": 0.0, "t": []})
        s["n"] += 1
        s["ok"] += ok
        s["wer"] += wer(cmd, hyp)
        s["t"].append(dt)
        print(f"  {cond:5} {'OK ' if ok else 'BAD'} {dt:5.2f}s  {len(audio) / 16000:4.1f}s audio  "
              f"{cmd!r:44} -> {hyp!r}")
    print("\nsummary (intent = fast parser gives the same command as for the true text):")
    for cond, s in stats.items():
        t = sorted(s["t"])
        print(f"  {cond:5}  intent {s['ok']}/{s['n']}  WER {100 * s['wer'] / s['n']:4.1f}%  "
              f"stt median {t[len(t) // 2]:.2f}s  max {t[-1]:.2f}s")
    if a.e2e:
        print("\nend-to-end (audio file -> STT -> intent -> profile saved+active), clean clips:")
        PR.ensure_default()
        for cmd, cond, p in clips:
            if cond != "clean":
                continue
            t0 = time.monotonic()
            text = stt(V.load_audio_file(str(p)))
            res = V.handle(text, speak=False, log=lambda *_: None)
            print(f"  {time.monotonic() - t0:5.2f}s [{res['path']}] {cmd!r:44} -> {res['spoken']}")


if __name__ == "__main__":
    main()
