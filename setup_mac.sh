#!/bin/zsh
# One-shot setup for a fresh Apple Silicon Mac. Safe to re-run: every step skips if already done.
#
#   ./setup_mac.sh             asks before installing Homebrew packages
#   ./setup_mac.sh --yes       no questions
#   ./setup_mac.sh --dry-run   only shows what it would do (changes nothing)
#
# First run downloads ~0.8 GB of pip packages + ~0.55 GB of models (InsightFace, Whisper).
set -e -o pipefail
cd "$(dirname "$0")"
ROOT=$PWD
PY=$ROOT/.venv/bin/python

YES=0 DRY=0
for a in "$@"; do
  case $a in
    -y|--yes) YES=1 ;;
    -n|--dry-run) DRY=1 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "unknown option: $a (try --help)"; exit 1 ;;
  esac
done

step() { print -P "\n%B==> $1%b" }
skip() { print "    ok, skipping: $1" }
warn() { print -P "    %F{yellow}warning:%f $1" }
run()  { print "    \$ $*"; (( DRY )) || "$@" }
have() { (( $# > 0 )) }   # have glob(N) -> true if the glob matched something
ask()  {   # ask "question" -> 0 if yes
  (( YES || DRY )) && return 0
  read -q "?    $1 [y/N] " && { echo; return 0 } || { echo; return 1 }
}

(( DRY )) && print -P "%BDry run: nothing will be changed.%b"
[[ $(uname -m) == arm64 ]] || warn "this was built and tested on Apple Silicon (arm64); you are on $(uname -m)"

# 1. Xcode Command Line Tools (insightface compiles a small C extension)
step "Xcode Command Line Tools"
if xcode-select -p >/dev/null 2>&1; then
  skip "installed at $(xcode-select -p)"
else
  run xcode-select --install
  (( DRY )) || { echo "    Finish the installer window, then re-run ./setup_mac.sh"; exit 1 }
fi

# 2. Homebrew + packages
step "Homebrew"
[[ -x /opt/homebrew/bin/brew ]] && eval "$(/opt/homebrew/bin/brew shellenv)"
if command -v brew >/dev/null; then
  skip "$(brew --version | head -1)"
else
  if ask "Install Homebrew (https://brew.sh)?"; then
    print '    $ /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    if (( ! DRY )); then
      NONINTERACTIVE=$YES /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
      eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
  else
    echo "    Homebrew is required. Stopping."; exit 1
  fi
fi

step "Homebrew packages: python@3.12, ffmpeg"
missing=()
for f in python@3.12 ffmpeg; do
  command -v brew >/dev/null && brew list --versions $f >/dev/null 2>&1 || missing+=$f
done
if (( ${#missing} == 0 )); then
  skip "python@3.12 and ffmpeg installed"
elif ask "brew install ${missing[*]}?"; then
  run brew install ${missing[@]}
else
  echo "    python@3.12 and ffmpeg are required. Stopping."; exit 1
fi
command -v mediamtx >/dev/null || print "    (optional, wireless streaming only: brew install mediamtx)"

# 3. Python virtualenv
step "Python 3.12 virtualenv (.venv)"
if [[ -x $PY ]] && [[ $($PY -c 'import sys;print(sys.version_info[:2]==(3,12))') == True ]]; then
  skip ".venv is $($PY --version)"
else
  [[ -e .venv ]] && { echo "    .venv exists but is not Python 3.12; move it away and re-run"; exit 1 }
  PY312=python3.12
  command -v brew >/dev/null && [[ -x $(brew --prefix)/opt/python@3.12/bin/python3.12 ]] && PY312=$(brew --prefix)/opt/python@3.12/bin/python3.12
  run $PY312 -m venv .venv
fi

# 4. pip packages
step "Python packages (requirements.txt)"
pins_ok() {
  [[ -x $PY ]] && $PY - <<'EOF'
import sys, importlib.metadata as m
bad = []
for line in open("requirements.txt"):
    line = line.split("#")[0].strip()
    if not line:
        continue
    name, ver = line.split("==")
    try:
        if m.version(name) != ver:
            bad.append(line)
    except m.PackageNotFoundError:
        bad.append(line)
sys.exit(1 if bad else 0)
EOF
}
if pins_ok; then
  skip "all pinned versions installed"
else
  run $PY -m pip install -q --upgrade pip
  run $PY -m pip install -r requirements.txt
fi

# 5. Models (models/gesture_recognizer.task and models/yolov8n.onnx are already in git)
step "Face models: InsightFace buffalo_s (live app, ~120 MB) and buffalo_l (tests, ~275 MB)"
for m in buffalo_s buffalo_l; do
  if have ~/.insightface/models/$m/*.onnx(N); then
    skip "$m in ~/.insightface/models"
  else
    run $PY -c "from insightface.app import FaceAnalysis; FaceAnalysis(name='$m', allowed_modules=['detection','recognition'], providers=['CPUExecutionProvider'])"
  fi
done

step "Speech model: faster-whisper base.en (~145 MB)"
if have ${HF_HOME:-$HOME/.cache/huggingface}/hub/models--Systran--faster-whisper-base.en/snapshots/*/model.bin(N); then
  skip "base.en in the Hugging Face cache"
else
  run $PY -c "from faster_whisper import WhisperModel; WhisperModel('base.en', device='cpu', compute_type='int8')"
fi

# 6. Synthetic test clips (raw.mp4 files are gitignored; labels are in git)
step "Synthetic test clips (corpus/)"
if [[ -f corpus/synth_slow_pan/raw.mp4 && -f corpus/synth_zoom/raw.mp4 && -f corpus/synth_whip/raw.mp4 && -f corpus/synth_crowd/raw.mp4 ]]; then
  skip "slow_pan, zoom, whip, crowd exist"
else
  run $PY -m eval synth
fi
if [[ -f corpus/synth_screens/raw.mp4 ]]; then
  skip "synth_screens exists"
else
  run $PY scripts/make_synth_screens.py
fi

# 7. Warm the test cache, so test_profile (dashboard / MCP) answers in seconds
step "Warm the test cache (runs the face models over the clips once; a few minutes)"
if [[ -f runs/profile-default/metrics.json ]] && have cache/synth_slow_pan/teacher-*.pkl(N) \
   && have cache/synth_whip/teacher-*.pkl(N) && have cache/synth_zoom/teacher-*.pkl(N); then
  skip "cache/ and runs/profile-default exist"
else
  run $PY -c "from privacy_filter import mcp_server as S; r = S.test_profile('default'); print('   ', r.get('summary') or r)"
fi

# 8. Register the MCP server with Claude Code (for this folder)
step "Claude Code MCP server: privacy-filter"
mcp_registered() {
  [[ -x $PY && -f ~/.claude.json ]] && $PY - "$ROOT" <<'EOF'
import json, os, sys
d = json.load(open(os.path.expanduser("~/.claude.json")))
names = set(d.get("mcpServers", {})) | set(d.get("projects", {}).get(sys.argv[1], {}).get("mcpServers", {}))
sys.exit(0 if "privacy-filter" in names else 1)
EOF
}
if ! command -v claude >/dev/null; then
  warn "'claude' is not on PATH. Install Claude Code, log in, then re-run this script."
elif mcp_registered; then
  skip "privacy-filter already registered"
else
  run claude mcp add privacy-filter -- "$PY" -m privacy_filter.mcp_server
fi

# 9. Unit tests (always run; a few seconds)
step "Unit tests"
if (( DRY )); then
  print "    \$ $PY -m pytest -q tests"
else
  $PY -m pytest -q tests || warn "some tests failed (see above); the demo may still run"
fi

step "Done. Next steps"
cat <<'EOF'
    1. Log in to Claude Code once:   claude      (the dashboard and voice use `claude -p`)
    2. Plug the Mentra Live glasses in over USB.
    3. Start everything:             ./start.sh
       -> allow Camera + Microphone for Terminal, and allow Terminal to control Terminal (Automation)
    No glasses?  ./demo.sh webcam    (or ./demo.sh clip for a recorded clip)
    Help: SETUP.md
EOF
