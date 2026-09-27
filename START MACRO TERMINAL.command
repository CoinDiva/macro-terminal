#!/bin/bash
# COINDIVA MACRO TERMINAL — Desktop Launcher
echo "Starting COINDIVA MACRO TERMINAL..."

# Kill any old process on port 8765
lsof -ti:8765 | xargs kill -9 2>/dev/null
sleep 1

cd "$(dirname "$0")"

# API keys live in secrets.json (kept out of the code so the repo is publishable).
# If it goes missing, the Macro Intelligence panel dies silently — so fail loudly here.
if [ ! -f secrets.json ]; then
  echo ""
  echo "  ERROR: secrets.json not found in this folder"
  echo "  The dashboard needs it for the FRED and Reuters keys."
  echo "  Fix: cp secrets.example.json secrets.json  then add your keys."
  echo ""
  read -n 1 -s -r -p "Press any key to close..."
  exit 1
fi

# Start server — nohup + disown keeps it alive after Terminal closes
nohup python3 server.py > /tmp/macro_terminal.log 2>&1 &
disown

# Wait for server to be ready, then open Chrome
sleep 3
open -a "Google Chrome" "http://localhost:8765/MacroTerminal.html"
echo "Dashboard live — server log at /tmp/macro_terminal.log"
