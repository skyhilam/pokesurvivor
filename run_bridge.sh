#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$DIR:$PYTHONPATH"

echo "Starting PokéSurvivor TypeSafe Jev Strategic Bridge..."
python3 "$DIR/bridge/jev_bridge.py"
