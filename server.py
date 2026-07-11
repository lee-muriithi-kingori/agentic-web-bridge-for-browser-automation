#!/usr/bin/env python3
"""
WebBridge server (v2) — simple, robust, stdlib-only.

Endpoints:
  GET  /                   health
  GET  /state              last page state from the extension
  POST /state              extension -> server: push page state
  GET  /poll?ext=<id>      extension long-polls for the next command
  POST /cmd                agent -> server: enqueue {id, type, args}
  GET  /result?id=<id>&wait=<ms>  agent waits for a result
  POST /result             extension -> server: post result {id, ok, value|error}
  GET  /log?tail=N         recent log lines

Command types: ping, eval, navigate, click, type, html, url, title,
screenshot, tabs, active_tab.
"""

import argparse
import base64
import http.server
import json
import os
import socketserver
import threading
import time
import uuid
from collections import deque
from urllib.parse import urlparse, parse_qs

HOST = "127.0.0.1"
DEFAULT_PORT = 9876
LOG_MAX = 300
RESULT_TTL = 300  # seconds
SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")


class Bridge:
    def __init__(self):
        self.lock = threading.RLock()  # reentrant for safety
        self.cmd_queue = deque()
        self.results = {}     # id -> {ok, value, error, ts}
        self.state = {
            "url": None, "title": None, "tabId": None,
            "extId": None, "snippet": None, "ts": 0,
        }
        self.log_lines = deque(maxlen=LOG_MAX)
        self.cond = threading.Condition(self.lock)  # waiters for /result

    def log(self, who, msg):
        line = f"{time.strftime('%H:%M:%S')} {who:>8} | {msg}"
        with self.lock:
            self.log_lines.append(line)
        print(line, flush=True)

    def enqueue(self, cmd):
        with self.cond:
            self.cmd_queue.append(cmd)
            self.log("agent", f"queued {cmd['type']} id={cmd['id']}")
            self.cond.notify_all()
            return True

    def dequeue(self, ext_id):
        with self.cond:
            self.state["extId"] = ext_id
            if not self.cmd_queue:
                return None
            cmd = self.cmd_queue.popleft()
            return cmd

    def post_result(self, rid, ok, value=None, error=None):
        with self.cond:
            self.results[rid] = {
                "ok": ok, "value": value, "error": error, "ts": time.time(),
            }
            self.log("ext", f"result id={rid} ok={ok}")
            self.cond.notify_all()

    def wait_for_result(self, rid, wait_ms):
        """Block until a result is available or timeout. Returns the result
        dict or None if timed out."""
        deadline = time.time() + (wait_ms / 1000.0)
        with self.cond:
            while True:
                r = self.results.get(rid)
                if r is not None:
                    return r
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self.cond.wait(timeout=remaining)

    def post_state(self, ext_id, payload):
        with self.lock:
            self.state.update(payload)
            self.state["extId"] = ext_id
            self.state["ts"] = time.time()
            self.log("ext", f"state url={payload.get('url')!r}")
            return True

    def get_state(self):
        with self.lock:
            return dict(self.state)

    def tail_log(self, n=50):
        with self.lock:
            return list(self.log_lines)[-n:]

    def gc_results(self):
        now = time.time()
        with self.lock:
            stale = [k for k, v in self.results.items() if now - v["ts"] > RESULT_TTL]
            for k in stale:
                del self.results[k]


BRIDGE = Bridge()


class Handler(http.server.BaseHTTPRequestHandler):
    # Quieter default access log
    def log_message(self, fmt, *args):
        pass

    # Override to use HTTP/1.1 with explicit close after each request.
    # This avoids hangs from keep-alive confusion.
    protocol_version = "HTTP/1.1"

    def _send_json(self, status, payload):
        try:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
        except Exception as e:
            BRIDGE.log("server", f"send failed: {e}")

    def _read_json(self):
        try:
            n = int(self.headers.get("Content-Length", "0") or 0)
            if n == 0:
                return {}
            raw = self.rfile.read(n)
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))
        except Exception as e:
            return {"__parse_error__": str(e)}

    def do_OPTIONS(self):
        self._send_json(200, {"ok": True})

    def do_GET(self):
        try:
            u = urlparse(self.path)
            q = parse_qs(u.query)
            p = u.path

            if p == "/" or p == "/health":
                return self._send_json(200, {
                    "ok": True, "service": "webbridge", "version": "2.0",
                    "endpoints": ["/state", "/poll", "/cmd", "/result", "/log"],
                })
            if p == "/state":
                return self._send_json(200, BRIDGE.get_state())
            if p == "/poll":
                ext_id = (q.get("ext", ["anon"])[0])
                BRIDGE.gc_results()
                cmd = BRIDGE.dequeue(ext_id)
                if not cmd:
                    return self._send_json(200, {"id": None})
                return self._send_json(200, cmd)
            if p == "/result":
                rid = q.get("id", [None])[0]
                wait = int(q.get("wait", ["0"])[0])
                if not rid:
                    return self._send_json(400, {"ok": False, "error": "missing id"})
                r = BRIDGE.wait_for_result(rid, wait)
                if r is None:
                    return self._send_json(200, {"ok": False, "pending": True})
                return self._send_json(200, {"ok": True, "result": r})
            if p == "/log":
                n = int(q.get("tail", ["50"])[0])
                return self._send_json(200, {"ok": True, "lines": BRIDGE.tail_log(n)})
            return self._send_json(404, {"ok": False, "error": "no such path"})
        except Exception as e:
            return self._send_json(500, {"ok": False, "error": str(e)})
        finally:
            try:
                self.close_connection = True
            except Exception:
                pass

    def do_POST(self):
        try:
            u = urlparse(self.path)
            body = self._read_json()
            p = u.path

            if p == "/cmd":
                cid = body.get("id") or uuid.uuid4().hex[:12]
                ctype = body.get("type")
                if not ctype:
                    return self._send_json(400, {"ok": False, "error": "missing type"})
                BRIDGE.enqueue({"id": cid, "type": ctype, "args": body.get("args", {})})
                return self._send_json(200, {"ok": True, "id": cid})
            if p == "/result":
                rid = body.get("id")
                if not rid:
                    return self._send_json(400, {"ok": False, "error": "missing id"})
                BRIDGE.post_result(
                    rid,
                    bool(body.get("ok")),
                    value=body.get("value"),
                    error=body.get("error"),
                )
                return self._send_json(200, {"ok": True})
            if p == "/state":
                ext_id = body.get("ext") or "anon"
                return self._send_json(200, {"ok": BRIDGE.post_state(ext_id, body)})
            if p == "/screenshot":
                # Receive a base64-encoded PNG from the extension and save it
                # to webbridge/screenshots/. The file path is returned in the
                # response so the agent (or the user) can view it.
                b64 = body.get("png_b64")
                if not b64:
                    return self._send_json(400, {"ok": False, "error": "missing png_b64"})
                os.makedirs(SCREENSHOT_DIR, exist_ok=True)
                ts = time.strftime("%Y%m%d-%H%M%S")
                fname = f"shot-{ts}-{uuid.uuid4().hex[:6]}.png"
                fpath = os.path.join(SCREENSHOT_DIR, fname)
                with open(fpath, "wb") as f:
                    f.write(base64.b64decode(b64))
                BRIDGE.log("ext", f"screenshot saved {fpath} ({len(b64)//1024}KB b64)")
                return self._send_json(200, {
                    "ok": True,
                    "path": fpath,
                    "url": "file:///" + fpath.replace("\\", "/"),
                    "size": os.path.getsize(fpath),
                })
            return self._send_json(404, {"ok": False, "error": "no such path"})
        except Exception as e:
            return self._send_json(500, {"ok": False, "error": str(e)})
        finally:
            try:
                self.close_connection = True
            except Exception:
                pass


class Server(http.server.ThreadingHTTPServer):
    """ThreadingHTTPServer with allow_reuse_address."""
    allow_reuse_address = True
    daemon_threads = True


def main():
    p = argparse.ArgumentParser()
    p.add_argument("port", nargs="?", default=DEFAULT_PORT, type=int)
    args = p.parse_args()
    srv = Server((HOST, args.port), Handler)
    BRIDGE.log("server", f"webbridge v2 listening on http://{HOST}:{args.port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        BRIDGE.log("server", "shutting down")
        srv.shutdown()


if __name__ == "__main__":
    main()
