"""Privacy dashboard: a tiny local web UI for profiles, tests and plain-English rules.

  .venv/bin/python -m gui.server            # http://localhost:7860
  .venv/bin/python gui/server.py --port 8000

Stdlib only. Reads/writes profiles through privacy_filter.profiles, so the live camera app
(which polls profiles/ACTIVE) follows whatever is changed here within about a second.
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from privacy_filter import profiles as PR  # noqa: E402

HERE = Path(__file__).resolve().parent
VOICE_STATE = PR.DIR / "VOICE_STATE.json"
RUNS = ROOT / "runs"
CLAUDE_LOCK = threading.Lock()


# ---------- helpers ----------

def raw(name):
    """The profile file as written (keeps extra keys such as `locked` that validate() drops)."""
    try:
        return yaml.safe_load(PR.path(name).read_text()) or {}
    except Exception:
        return {}


def is_locked(name):
    try:
        return PR.path(name).exists() and bool(raw(name).get("locked"))
    except ValueError:
        return False


def profile_info(name):
    r = raw(name)
    try:
        clean = PR.load(name)
        _, problems = PR.validate(r)
    except Exception as e:  # unreadable file: show it, don't crash the page
        clean, problems = {"name": name}, [f"could not load: {e}"]
    p = PR.path(name)
    return {**clean, "locked": bool(r.get("locked")), "problems": problems,
            "active": PR.active_name() == name, "modified": p.stat().st_mtime if p.exists() else None}


def all_profiles():
    return [profile_info(n) for n in PR.names()]


def status():
    active = PR.active_name()
    out = {"active": active,
           "activated_at": PR.ACTIVE.stat().st_mtime if PR.ACTIVE.exists() else None,
           "now": time.time(),
           "voice": None,
           "fingerprint": None}
    if VOICE_STATE.exists():
        try:
            v = json.loads(VOICE_STATE.read_text())
            out["voice"] = v if isinstance(v, dict) else {"state": v}
            out["voice"].setdefault("_mtime", VOICE_STATE.stat().st_mtime)
        except Exception as e:
            out["voice"] = {"error": f"unreadable: {e}", "_mtime": VOICE_STATE.stat().st_mtime}
    # cheap change detector so the page refreshes the list when any profile file changes
    files = sorted(PR.DIR.glob("*.yaml")) if PR.DIR.exists() else []
    out["fingerprint"] = "|".join(f"{f.name}:{f.stat().st_mtime_ns}" for f in files) + f"|{active}"
    return out


def test_results(name):
    d = RUNS / f"profile-{PR.slug(name)}"
    m = d / "metrics.json"
    if not m.exists():
        return {"exists": False}
    try:
        data = json.loads(m.read_text())
    except Exception as e:
        return {"exists": False, "error": str(e)}
    clips = []
    for cname, c in (data.get("clips") or {}).items():
        clips.append({"clip": cname, "frames": c.get("frames"), "duration_s": c.get("duration_s"),
                      "leak_frames": c.get("leak_frames"), "false_visible_frames": c.get("false_visible_frames"),
                      "leak_by_cause": c.get("leak_by_cause")})
    cfg = (data.get("config") or {})
    return {"exists": True, "ran_at": m.stat().st_mtime, "totals": data.get("totals", {}), "clips": clips,
            "tested_with": {"consent": cfg.get("consent", {}).get("consent_gesture"),
                            "revoke": cfg.get("consent", {}).get("revoke_gesture"),
                            "hands": cfg.get("consent", {}).get("hands_required")},
            "summary": (d / "summary.txt").read_text() if (d / "summary.txt").exists() else ""}


def run_test(name):
    from privacy_filter import mcp_server  # imported lazily (pulls in the mcp package)
    t0 = time.time()
    r = mcp_server.test_profile(name)
    return {**r, "seconds": round(time.time() - t0, 1), "results": test_results(name)}


def diff(before, after):
    """Field-level before/after of every profile, plus the active switch."""
    changes = []
    for n in sorted(set(before["profiles"]) | set(after["profiles"])):
        a, b = before["profiles"].get(n), after["profiles"].get(n)
        if a == b:
            continue
        if a is None:   # new profile: show how it differs from what was live before
            base = before["profiles"].get(before["active"]) or {}
            fa, fb = flatten(base), flatten(b)
            changes.append({"profile": n, "kind": "created", "compared_with": before["active"], "fields": [
                {"field": k, "before": fa.get(k), "after": v} for k, v in fb.items()
                if k != "name" and fa.get(k) != v]})
        elif b is None:
            changes.append({"profile": n, "kind": "deleted", "fields": []})
        else:
            fa, fb = flatten(a), flatten(b)
            changes.append({"profile": n, "kind": "modified", "fields": [
                {"field": k, "before": fa.get(k), "after": fb.get(k)}
                for k in list(dict.fromkeys([*fa, *fb])) if fa.get(k) != fb.get(k)]})
    return {"changes": changes, "active_before": before["active"], "active_after": after["active"]}


def flatten(d, prefix=""):
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(flatten(v, f"{prefix}{k}."))
        else:
            out[f"{prefix}{k}"] = v
    return out


def snapshot():
    return {"active": PR.active_name(), "profiles": {n: raw(n) for n in PR.names()}}


def ask_claude(text):
    """Plain English -> `claude -p` driving our MCP server. Returns Claude's reply and what changed."""
    if not CLAUDE_LOCK.acquire(blocking=False):
        return 409, {"error": "Claude is already working on a request"}
    try:
        names = PR.names()
        locked = [n for n in names if is_locked(n)]
        prompt = (
            "You are configuring a privacy filter for camera glasses through the privacy-filter MCP tools.\n"
            f"The user said: \"{text}\"\n\n"
            f"Existing profiles: {', '.join(names) or 'none'}. Active: {PR.active_name()}.\n"
            f"LOCKED profiles (never save over these): {', '.join(sorted(set(locked + ['default'])))}.\n"
            "Steps: call list_capabilities; then save_profile; then activate_profile on it. Do NOT run test_profile.\n"
            "Naming: if the request tweaks the active profile (e.g. 'make it stronger', 'actually use two hands') "
            "and the active profile is not locked, save over it with the SAME name and keep its other settings. "
            "Otherwise create a new profile with a short descriptive kebab-case name. Never save over a locked one.\n"
            "Finish with one or two short plain sentences for a non-technical audience: what the glasses do now, "
            "and anything you could not do because the engine doesn't support it. Don't mention tests, tools or files."
        )
        mcp = {"mcpServers": {"privacy-filter": {
            "command": sys.executable, "args": ["-m", "privacy_filter.mcp_server"],
            "env": {"PYTHONPATH": str(ROOT)}}}}
        cmd = ["claude", "-p", prompt, "--output-format", "json",
               "--mcp-config", json.dumps(mcp), "--strict-mcp-config",
               "--allowedTools", "mcp__privacy-filter",
               "--tools", "",                       # no built-in tools (Bash/Edit/...): only the MCP
               "--no-session-persistence"]
        before = snapshot()
        t0 = time.time()
        try:
            r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300,
                               stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            return 500, {"error": "the `claude` CLI is not on PATH"}
        except subprocess.TimeoutExpired:
            return 504, {"error": "Claude took longer than 5 minutes", "diff": diff(before, snapshot())}
        secs = round(time.time() - t0, 1)
        reply, err = None, None
        try:
            j = json.loads(r.stdout)
            reply = j.get("result")
            if j.get("is_error"):
                err = reply or "Claude reported an error"
        except Exception:
            err = (r.stderr or r.stdout)[-1500:] or f"claude exited with {r.returncode}"
        return 200, {"reply": reply, "error": err, "seconds": secs, "diff": diff(before, snapshot())}
    finally:
        CLAUDE_LOCK.release()


def form_to_profile(body):
    keep = ("name", "description", "blur_targets", "consent", "revoke", "blur_strength")
    return {k: body[k] for k in keep if k in body}


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if not self.path.startswith("/api/status"):
            sys.stderr.write("  %s %s\n" % (self.command, self.path))

    def send(self, code, obj, ctype="application/json"):
        data = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        p = self.path.split("?")[0]
        try:
            if p in ("/", "/index.html"):
                return self.send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
            if p == "/api/profiles":
                return self.send(200, {"profiles": all_profiles(), "status": status(),
                                       "options": {"targets": PR.SUPPORTED_TARGETS, "gestures": list(PR.GESTURES),
                                                   "strengths": list(PR.STRENGTH)}})
            if p == "/api/status":
                return self.send(200, status())
            if p.startswith("/api/profiles/"):
                name = p.split("/")[3]
                if not PR.path(name).exists():
                    return self.send(404, {"error": f"no profile '{name}'"})
                return self.send(200, {"profile": profile_info(name), "test": test_results(name)})
            return self.send(404, {"error": "not found"})
        except Exception as e:
            return self.send(500, {"error": str(e)})

    def do_POST(self):
        p = self.path.split("?")[0]
        try:
            b = self.body()
            if p == "/api/validate":
                clean, problems = PR.validate(form_to_profile(b))
                return self.send(200, {"profile": clean, "problems": problems})
            if p == "/api/save":           # overwrite an existing profile (refused if locked)
                name = PR.slug(b.get("name", ""))
                if is_locked(name):
                    return self.send(403, {"error": f"'{name}' is locked and can't be edited. Use Save as new."})
                return self._save(b, activate=bool(b.get("activate")))
            if p == "/api/save_new":       # create; never overwrite
                name = PR.slug(b.get("name", ""))
                if PR.path(name).exists():
                    return self.send(409, {"error": f"a profile called '{name}' already exists; pick another name"})
                return self._save(b, activate=bool(b.get("activate")))
            if p == "/api/activate":
                PR.activate(b["name"])
                return self.send(200, {"status": status()})
            if p == "/api/test":
                return self.send(200, run_test(b["name"]))
            if p == "/api/ask":
                text = str(b.get("text", "")).strip()
                if not text:
                    return self.send(400, {"error": "say what you want first"})
                code, out = ask_claude(text)
                return self.send(code, out)
            return self.send(404, {"error": "not found"})
        except (ValueError, KeyError, FileNotFoundError) as e:
            return self.send(400, {"error": str(e)})
        except Exception as e:
            return self.send(500, {"error": f"{type(e).__name__}: {e}"})

    def _save(self, b, activate=False):
        try:
            clean, problems = PR.save(form_to_profile(b))
        except PermissionError as e:   # in case profiles.save() itself refuses (e.g. locked)
            return self.send(403, {"error": str(e)})
        if activate:
            PR.activate(clean["name"])
        return self.send(200, {"profile": profile_info(clean["name"]), "problems": problems})


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 7860)))
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    PR.ensure_default()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"Privacy dashboard: http://localhost:{a.port}   (profiles in {PR.DIR})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
