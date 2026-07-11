#!/usr/bin/env python3
"""
webbridge client — helper CLI for the agent to send commands to the bridge.

Usage:
  python client.py ping
  python client.py eval "document.title"
  python client.py navigate https://example.com
  python client.py click "#submit"
  python client.py type "#q" "search text"
  python client.py html
  python client.py url
  python client.py title
  python client.py state
  python client.py tabs
  python client.py screenshot

Or via stdin JSON:
  echo '{"type":"eval","args":{"code":"1+2"}}' | python client.py
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error

SERVER = "http://127.0.0.1:9876"


def post(path, body=None):
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(SERVER + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def get(path):
    with urllib.request.urlopen(SERVER + path, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def send_and_wait(cmd_type, args=None, wait_ms=10000):
    cid = "cli-" + str(int(time.time() * 1000))
    post("/cmd", {"id": cid, "type": cmd_type, "args": args or {}})
    res = get(f"/result?id={cid}&wait={wait_ms}")
    if not res.get("ok"):
        return res
    r = res.get("result") or {}
    if r.get("ok"):
        return {"ok": True, "value": r.get("value")}
    return {"ok": False, "error": r.get("error")}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("type", help="command type (eval, navigate, click, type, html, url, title, state, tabs, ping, screenshot, query, key, attach, detach)")
    p.add_argument("arg1", nargs="?", help="selector / url / code / key")
    p.add_argument("arg2", nargs="?", help="text (for type)")
    p.add_argument("--wait", type=int, default=10000, help="ms to wait for result")
    p.add_argument("--raw", action="store_true", help="pass arg1 as raw args.code (for eval)")
    args = p.parse_args()

    # read JSON from stdin if no type
    if args.type == "-":
        body = json.loads(sys.stdin.read())
        out = send_and_wait(body["type"], body.get("args", {}), args.wait)
    else:
        cmd_args = {}
        if args.type == "eval":
            cmd_args["code"] = args.arg1 or ""
        elif args.type == "navigate":
            cmd_args["url"] = args.arg1 or ""
        elif args.type == "click":
            cmd_args["selector"] = args.arg1 or ""
        elif args.type == "type":
            cmd_args["selector"] = args.arg1 or ""
            cmd_args["text"] = args.arg2 or ""
        elif args.type == "key":
            cmd_args["key"] = args.arg1 or ""
        elif args.type in ("query", "submit"):
            cmd_args = args.arg1 or ""
        elif args.type in ("ping", "html", "url", "title", "tabs", "screenshot", "state", "attach", "detach"):
            pass
        else:
            print(json.dumps({"ok": False, "error": "unknown type: " + args.type}, indent=2))
            return 1

        if args.type == "state":
            out = get("/state")
        else:
            out = send_and_wait(args.type, cmd_args, args.wait)

    print(json.dumps(out, indent=2, default=str))
    return 0 if out.get("ok") or out.get("url") is not None else 1


if __name__ == "__main__":
    sys.exit(main())
