#!/usr/bin/env bash
# Start the WebBridge server.
# Usage: ./start.sh [PORT]
cd "$(dirname "$0")"
PORT="${1:-9876}"
exec python3 server.py --port "$PORT"
