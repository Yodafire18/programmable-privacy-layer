#!/bin/zsh
# One command for the demo. Never a blank screen: glasses stream -> webcam -> recorded clip.
#
#   ./demo.sh                 auto: glasses if streaming, else webcam
#   ./demo.sh glasses         wait for the glasses stream (reconnects automatically if it drops)
#   ./demo.sh webcam          Mac / iPhone (Continuity Camera) webcam
#   ./demo.sh clip [file]     recorded clip (backup)
#   extra flags pass through, e.g.  ./demo.sh webcam --profile thumbs-up-consent --delay 1.5
cd "$(dirname "$0")"
PY=.venv/bin/python
URL_IN=rtsp://localhost:8554/live/mentra
MODE=${1:-auto}; [[ $# -gt 0 ]] && shift
CLIP=corpus/synth_slow_pan/raw.mp4
if [[ "$MODE" == clip && -n "$1" && "$1" != --* ]]; then CLIP=$1; shift; fi

# streaming server for the glasses (idempotent)
if ! pgrep -qf "mediamtx .*mediamtx.yml"; then
  nohup stream/server.sh >/tmp/mediamtx.log 2>&1 &
  sleep 1
fi
IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
echo "glasses should stream to:  rtmp://${IP:-<mac-ip>}:1935/live/mentra"

stream_up() { ffprobe -v quiet -rtsp_transport tcp -rw_timeout 3000000 -i "$URL_IN" -show_entries stream=codec_type >/dev/null 2>&1 }

case $MODE in
  auto)
    if stream_up; then echo "→ glasses stream found"; SRC=$URL_IN
    else echo "→ no glasses stream, using webcam (run './demo.sh glasses' to wait for them)"; SRC=0; fi ;;
  glasses) SRC=$URL_IN ;;
  webcam)  SRC=${WEBCAM:-0} ;;
  clip)    SRC=$CLIP ;;
  *) echo "usage: ./demo.sh [auto|glasses|webcam|clip [file]] [flags]"; exit 1 ;;
esac

echo "keys: q quit · f forget everyone · space photo · r record · d debug overlay"
exec $PY -m privacy_filter.live --source "$SRC" --demo --delay ${DELAY:-0.75} "$@"
