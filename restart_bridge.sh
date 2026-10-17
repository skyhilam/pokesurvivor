#!/usr/bin/env bash
# One-click bridge restart: stop the old bridge, start the new one, verify it answers.
#   ./restart_bridge.sh          restart and verify
#   ./restart_bridge.sh check    only report what is running (no changes)
set -u

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${JEV_BRIDGE_PORT:-8765}"
LOG="$DIR/logs/bridge.log"
PIDF="$DIR/logs/bridge.pid"
cd "$DIR"

bridge_pids() {
  local pids=""
  if command -v lsof >/dev/null 2>&1; then
    pids=$(lsof -ti "tcp:$PORT" 2>/dev/null || true)
  fi
  if [ -z "$pids" ]; then
    pids=$(pgrep -f "jev_bridge.py" 2>/dev/null || true)
  fi
  echo "$pids"
}

healthy() { curl -s -m 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; }

if [ "${1:-}" = "check" ]; then
  echo "bridge pids on port $PORT: $(bridge_pids || echo none)"
  if healthy; then
    echo "health: $(curl -s -m 3 "http://127.0.0.1:$PORT/health")"
  else
    echo "health: no response"
  fi
  exit 0
fi

# ---- stop the old bridge (only if the port really belongs to the bridge)
pids=$(bridge_pids)
if [ -n "$pids" ]; then
  for pid in $pids; do
    cmd=$(ps -p "$pid" -o command= 2>/dev/null || true)
    case "$cmd" in
      *jev_bridge.py*)
        echo "Stopping old bridge (pid $pid)..."
        kill "$pid" 2>/dev/null || true
        ;;
      *)
        echo "Port $PORT is held by a non-bridge process $pid ($cmd); aborting."
        exit 1
        ;;
    esac
  done
  for _ in $(seq 1 20); do
    [ -z "$(bridge_pids)" ] && break
    sleep 0.5
  done
  left=$(bridge_pids)
  if [ -n "$left" ]; then
    echo "Force stopping: $left"
    kill -9 $left 2>/dev/null || true
    sleep 1
  fi
fi

# ---- start the new bridge in the background, logging to logs/bridge.log
echo "Starting bridge on port $PORT (log: $LOG)..."
mkdir -p "$DIR/logs"
JEV_BRIDGE_PORT="$PORT" nohup python3 "$DIR/bridge/jev_bridge.py" >>"$LOG" 2>&1 &
echo $! >"$PIDF"

for _ in $(seq 1 40); do
  if healthy; then
    echo "Bridge is up (pid $(cat "$PIDF"))."
    echo "Health: $(curl -s -m 3 "http://127.0.0.1:$PORT/health")"
    echo "Next: reload the extension in your browser, then refresh the game page."
    exit 0
  fi
  sleep 0.5
done

echo "Bridge did not answer on port $PORT within 20s. Last log lines:"
tail -n 20 "$LOG" 2>/dev/null || true
exit 1
