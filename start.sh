#!/usr/bin/env bash
# Start the WebBridge server.
cd "$(dirname "$0")"
exec python3 server.py 9876
