#!/bin/zsh
# Streaming server on the Mac. Glasses publish to rtmp://<mac-ip>:1935/live/mentra ; the app reads it locally.
cd "$(dirname "$0")"
exec mediamtx mediamtx.yml
