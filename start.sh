#!/bin/zsh
# Opens everything for the demo in 3 Terminal windows + the browser.
#   ./start.sh
DIR="$(cd "$(dirname "$0")" && pwd)"
osascript <<EOF
tell application "Terminal"
  activate
  do script "cd '$DIR' && .venv/bin/python -m gui.server"
  do script "cd '$DIR' && ./demo.sh"
  do script "cd '$DIR' && sleep 6 && .venv/bin/python -m privacy_filter.voice --adb"
end tell
EOF
sleep 3 && open http://localhost:7860
