#!/bin/zsh
# Pretend to be the Mentra glasses: push a looping clip to the Mac's RTMP server, same as the glasses will.
#   stream/fake_mentra.sh [clip.mp4]      (default: synthetic slow pan)
cd "$(dirname "$0")/.."
CLIP=${1:-corpus/synth_slow_pan/raw.mp4}
exec ffmpeg -loglevel error -re -stream_loop -1 -i "$CLIP" \
  -c:v libx264 -preset veryfast -tune zerolatency -g 30 -b:v 3M -an \
  -f flv rtmp://localhost:1935/live/mentra
