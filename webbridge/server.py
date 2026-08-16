#!/usr/bin/env python3
"""
WebBridge server (v4) — production-ready, stdlib-only.

Endpoints:
  GET  /                   health
  GET  /state              last page state from the extension
  POST /state              extension -> server: push page state
  GET  /poll?ext=<id>      extension long-polls for the next command
  POST /cmd                agent -> server: enqueue {id, type, args}
  GET  /result?id=<id>&wait=<ms>  agent waits for a result
  POST /result             extension -> server: post result {id, ok, value|error}
  GET  /log?tail=N         recent log lines
  GET  /commands           list available command types
  POST /screenshot         extension posts {tabId, png_b64} -> saved file path
  POST /trace              extension posts trace bundle -> saved dir
  POST /shutdown           graceful remote shutdown

Command types: ping, eval, navigate, click, type, key, scroll, html, url,
title, snippet, query, screenshot, tabs, active_tab, attach, detach, reload,
see, axtree, axquery, expect, move, trace, hover, drag, select, cookies,
upload, back, forward, refresh, console.

Environment variables:
  WEBBRIDGE_HOST       bind address (default: 127.0.0.1)
  WEBBRIDGE_PORT       bind port (default: 9876)
  WEBBRIDGE_DATA_DIR   base directory for screenshots/traces (default: webbridge/)
  WEBBRIDGE_LOG_FILE   optional path to a log file
"""

import argparse
import base64
import http.server
import json
import logging
import os
import secrets
import signal
import socketserver
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

try:
    import pyautogui  # optional — only needed for /os endpoint
except Exception:  # pragma: no cover
    pyautogui = None  # type: ignore

# Single source of truth for the package version.
try:
    from webbridge._version import __version__ as _PKG_VERSION  # type: ignore
except Exception:  # pragma: no cover
    _PKG_VERSION = "4.1.0"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LOG_MAX: int = 300
RESULT_TTL: int = 300  # seconds
POLL_TIMEOUT_MS: int = 25000  # how long /poll will block waiting for a command
MAX_QUEUE_SIZE: int = 1000  # max commands waiting in the queue (reject with 429 when full)
MAX_REQUEST_BYTES: int = 50 * 1024 * 1024  # 50 MB cap on any single request body
GC_INTERVAL_S: int = 60  # how often the background GC thread sweeps stale results
COMMAND_TYPES: list[str] = [
    "ping", "tabs", "active_tab", "attach", "detach", "reload",
    "navigate", "eval", "click", "type", "key", "scroll",
    "html", "url", "title", "snippet", "readable", "query", "screenshot",
    "see", "axtree", "axquery", "expect", "move", "trace",
    "hover", "drag", "select", "cookies", "upload",
    "back", "forward", "refresh", "console",
    # Vision / VLM-friendly (screenshot is reused; these are hints to the
    # caller, but having explicit types lets agents auto-discover them).
    "vision",
]

# Endpoints that DON'T require auth. /health and /version are public so the
# popup can show status before the user has set a token. Everything else
# (including /poll, /cmd, /result, /state, /screenshot, /trace, /os, /shutdown)
# requires the bearer token when WEBBRIDGE_TOKEN is set.
PUBLIC_ENDPOINTS: frozenset[str] = frozenset({"/", "/health", "/version"})

# Extra origins allowed to talk to the bridge via CORS, beyond the
# chrome-extension:// / moz-extension:// schemes which are always allowed.
# Set e.g. WEBBRIDGE_EXTRA_ORIGINS="http://localhost:5173,http://localhost:3000"
# for local dev tooling. Do NOT add plain http(s) web origins in production —
# this server runs local automation and OS commands.
_EXTRA_ORIGINS: frozenset[str] = frozenset(
    o.strip() for o in os.environ.get("WEBBRIDGE_EXTRA_ORIGINS", "").split(",") if o.strip()
)


def _origin_allowed(origin: str) -> bool:
    """True if *origin* may receive CORS access to this server.

    Extension origins (chrome-extension://..., moz-extension://...) are
    always allowed since that's the bridge's intended client. Anything else
    must be explicitly opted in via WEBBRIDGE_EXTRA_ORIGINS — an empty
    Origin header (non-browser clients, curl, the Python client) is not
    subject to CORS at all and isn't affected by this check.
    """
    if not origin:
        return False
    if origin.startswith("chrome-extension://") or origin.startswith("moz-extension://"):
        return True
    return origin in _EXTRA_ORIGINS


def _default_data_dir() -> str:
    """Return a sensible cross-platform default for the data directory.

    Respects WEBBRIDGE_DATA_DIR if set. Otherwise uses a per-user dir:
      - Linux:   ~/.local/share/webbridge
      - macOS:   ~/Library/Application Support/webbridge
      - Windows: %LOCALAPPDATA%\\webbridge
    Falls back to ./webbridge if HOME/LOCALAPPDATA can't be resolved.
    """
    env = os.environ.get("WEBBRIDGE_DATA_DIR")
    if env:
        return env
    # Cross-platform app-data dir without a third-party dependency.
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "webbridge")
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "webbridge")
    # linux / other unix
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return os.path.join(xdg, "webbridge")
    return os.path.join(os.path.expanduser("~"), ".local", "share", "webbridge")


def _file_url(path: str) -> str:
    """Convert an absolute filesystem path to a file:// URL cross-platform.

    On Windows, `C:\\Users\\foo\\bar.png` → `file:///C:/Users/foo/bar.png`.
    On Unix, `/home/foo/bar.png` → `file:///home/foo/bar.png`.
    """
    # os.path.normpath cleans up any mixed separators, then we convert to
    # forward slashes for the URL. The `file:///` prefix with three slashes
    # is correct on both platforms (Windows gets an extra slash for the
    # drive letter, producing file:///C:/...).
    norm = os.path.normpath(path)
    if sys.platform == "win32":
        return "file:///" + norm.replace("\\", "/")
    return "file://" + norm

# ---------------------------------------------------------------------------
# Logger setup
# ---------------------------------------------------------------------------

logger: logging.Logger = logging.getLogger("webbridge")


def setup_logging(level_name: str, log_file: Optional[str] = None) -> None:
    """Configure root logger with console and optional file handler."""
    level = getattr(logging, level_name.upper(), logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S")
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(console)
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)


# ---------------------------------------------------------------------------
# Bridge — shared state for the server
# ---------------------------------------------------------------------------


class Bridge:
    """Thread-safe shared state for command queue, results, and page state.

    Coordinates communication between agents (producers of commands) and
    browser extensions (consumers of commands / producers of results).
    """

    def __init__(self, data_dir: str) -> None:
        self.lock: threading.RLock = threading.RLock()
        self.cmd_queue: deque[dict[str, Any]] = deque()
        self.results: dict[str, dict[str, Any]] = {}
        self.state: dict[str, Any] = {
            "url": None, "title": None, "tabId": None,
            "extId": None, "snippet": None, "ts": 0,
        }
        self.log_lines: deque[str] = deque(maxlen=LOG_MAX)
        self.cond: threading.Condition = threading.Condition(self.lock)
        self._shutting_down: bool = False
        self.data_dir: str = data_dir
        self.screenshot_dir: str = os.path.join(data_dir, "screenshots")
        self.trace_root: str = os.path.join(data_dir, "traces")
        self._gc_thread: Optional[threading.Thread] = None

    def log(self, who: str, msg: str) -> None:
        """Append a line to the in-memory log and log via stdlib logger."""
        line = f"{who:>8} | {msg}"
        with self.lock:
            self.log_lines.append(line)
        logger.info(line)

    def enqueue(self, cmd: dict[str, Any]) -> bool:
        """Add a command to the queue and wake any waiting poller.

        Returns True on success, False if the queue is full (MAX_QUEUE_SIZE).
        Caller should return 429 when this returns False.
        """
        with self.cond:
            if len(self.cmd_queue) >= MAX_QUEUE_SIZE:
                self.log("agent", f"queue full ({MAX_QUEUE_SIZE}), rejected {cmd['type']} id={cmd['id']}")
                return False
            self.cmd_queue.append(cmd)
            self.log("agent", f"queued {cmd['type']} id={cmd['id']}")
            self.cond.notify_all()
            return True

    def dequeue(self, ext_id: str, timeout_ms: int = 0) -> Optional[dict[str, Any]]:
        """Pop the next command, optionally blocking up to *timeout_ms* for one.

        With ``timeout_ms=0`` (default) this is non-blocking — returns None
        immediately if the queue is empty. With ``timeout_ms>0`` it blocks on
        the condition variable until either a command arrives or the timeout
        elapses, which lets /poll be a REAL long-poll instead of a busy-poll.
        """
        deadline = time.time() + (timeout_ms / 1000.0)
        with self.cond:
            self.state["extId"] = ext_id
            while True:
                if self.cmd_queue:
                    return self.cmd_queue.popleft()
                if self._shutting_down:
                    return None
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self.cond.wait(timeout=remaining)

    def post_result(self, rid: str, ok: bool, value: Any = None, error: Optional[str] = None) -> None:
        """Store a result for a previously submitted command."""
        with self.cond:
            self.results[rid] = {
                "ok": ok, "value": value, "error": error, "ts": time.time(),
            }
            self.log("ext", f"result id={rid} ok={ok}")
            self.cond.notify_all()

    def wait_for_result(self, rid: str, wait_ms: int) -> Optional[dict[str, Any]]:
        """Block until a result is available, the timeout elapses, OR the
        server starts shutting down. Returns the result dict, or ``None`` if
        timed out / shutting down."""
        deadline = time.time() + (wait_ms / 1000.0)
        with self.cond:
            while True:
                r = self.results.get(rid)
                if r is not None:
                    return r
                # Shutdown-aware: unblock immediately when the server is stopping.
                if self._shutting_down:
                    return None
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self.cond.wait(timeout=remaining)

    def post_state(self, ext_id: str, payload: dict[str, Any]) -> bool:
        """Merge incoming page state into the shared state dict."""
        with self.lock:
            self.state.update(payload)
            self.state["extId"] = ext_id
            self.state["ts"] = time.time()
            self.log("ext", f"state url={payload.get('url')!r}")
            return True

    def get_state(self) -> dict[str, Any]:
        """Return a snapshot of the current page state."""
        with self.lock:
            return dict(self.state)

    def tail_log(self, n: int = 50) -> list[str]:
        """Return the last *n* log lines."""
        with self.lock:
            return list(self.log_lines)[-n:]

    def gc_results(self) -> None:
        """Expire results older than RESULT_TTL seconds."""
        now = time.time()
        with self.lock:
            stale = [k for k, v in self.results.items() if now - v["ts"] > RESULT_TTL]
            for k in stale:
                del self.results[k]

    def start_gc_thread(self) -> None:
        """Start a background daemon thread that sweeps stale results every
        GC_INTERVAL_S seconds. Doesn't need to be stopped explicitly — it's
        a daemon, so it dies with the process. The thread exits early when
        ``is_shutting_down`` becomes True."""
        if self._gc_thread is not None and self._gc_thread.is_alive():
            return  # already running
        def _gc_loop():
            while not self._shutting_down:
                time.sleep(GC_INTERVAL_S)
                if self._shutting_down:
                    return
                try:
                    self.gc_results()
                except Exception as exc:
                    logger.warning("GC thread error: %s", exc)
        t = threading.Thread(target=_gc_loop, name="webbridge-gc", daemon=True)
        t.start()
        self._gc_thread = t

    def shutdown(self) -> None:
        """Signal the server to shut down and drain the command queue."""
        self._shutting_down = True
        with self.cond:
            self.cond.notify_all()

    @property
    def is_shutting_down(self) -> bool:
        return self._shutting_down


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

# Module-level singleton Bridge instance. Populated by main(); tests can
# monkey-patch it directly. Declared at module scope so `from webbridge.server
# import BRIDGE` works at import time (returns None until main() is called).
BRIDGE: Optional["Bridge"] = None


class Handler(http.server.BaseHTTPRequestHandler):
    """Request handler for the WebBridge HTTP API."""

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # type: ignore[override]
        """Suppress default access logging (handled by our own logger)."""

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        """Serialise *payload* to JSON and send it with CORS headers."""
        try:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            # CORS — reflect the request's Origin ONLY if it's a chrome/moz
            # extension origin (or explicitly allow-listed). Never "*": this
            # server executes browser/OS automation commands on localhost,
            # so a wildcard would let ANY webpage the user visits drive it
            # via fetch() (DNS-rebinding / CSRF-style attack surface).
            origin = self.headers.get("Origin", "")
            if _origin_allowed(origin):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
        except Exception as exc:
            logger.warning("send failed: %s", exc)

    def _check_auth(self) -> bool:
        """Return True if the request is authorised (or auth is disabled).

        Auth is disabled when WEBBRIDGE_TOKEN is not set (the default — keeps
        the bridge backwards-compatible for single-user dev machines). When
        the token IS set, every non-public endpoint must present it as a
        Bearer token in the Authorization header OR as a ?token= query param
        (query param is for the extension, which can't easily set headers
        on every fetch in MV3 — actually it can, but query is friendlier).
        """
        token = os.environ.get("WEBBRIDGE_TOKEN")
        if not token:
            return True  # auth disabled
        # Bearer header
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            presented = auth[len("Bearer "):].strip()
            if secrets.compare_digest(presented, token):
                return True
        # Query param fallback (for extension / browser fetches)
        q = parse_qs(urlparse(self.path).query)
        qs_token = q.get("token", [None])[0]
        if qs_token and secrets.compare_digest(qs_token, token):
            return True
        return False

    def _unauthorized(self) -> None:
        self._send_json(401, {"ok": False, "error": "unauthorized — set WEBBRIDGE_TOKEN on the server and pass it as a Bearer token or ?token= param"})

    def _read_json(self) -> dict[str, Any]:
        """Read the request body as JSON. Returns ``{}`` on empty body or parse errors.
        Rejects bodies larger than MAX_REQUEST_BYTES (returns a sentinel
        ``{"__body_too_large__": True}`` dict so the caller can 413)."""
        try:
            n = int(self.headers.get("Content-Length", "0") or 0)
            if n == 0:
                return {}
            if n > MAX_REQUEST_BYTES:
                return {"__body_too_large__": True, "size": n, "limit": MAX_REQUEST_BYTES}
            raw = self.rfile.read(n)
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:
            return {"__parse_error__": str(exc)}

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight requests."""
        self._send_json(200, {"ok": True})

    # -----------------------------------------------------------------------
    # GET
    # -----------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: C901 — intentionally flat dispatch
        try:
            u = urlparse(self.path)
            q = parse_qs(u.query)
            p = u.path

            # Auth: public endpoints skip the check; everything else requires it.
            if p not in PUBLIC_ENDPOINTS and not self._check_auth():
                return self._unauthorized()

            if p == "/" or p == "/health":
                return self._send_json(200, {
                    "ok": True, "service": "webbridge", "version": _PKG_VERSION,
                    "pyautogui": pyautogui is not None,
                    "auth_enabled": bool(os.environ.get("WEBBRIDGE_TOKEN")),
                    "endpoints": [
                        "/state", "/poll", "/cmd", "/result", "/log",
                        "/commands", "/screenshot", "/trace", "/shutdown",
                        "/os", "/version",
                    ],
                    "commands": COMMAND_TYPES,
                })

            if p == "/version":
                return self._send_json(200, {
                    "ok": True,
                    "package": _PKG_VERSION,
                    "extension": "2.0.0",
                    "pyautogui_available": pyautogui is not None,
                })

            if p == "/state":
                return self._send_json(200, BRIDGE.get_state())

            if p == "/commands":
                return self._send_json(200, {"ok": True, "commands": COMMAND_TYPES})

            if p == "/poll":
                ext_id = q.get("ext", ["anon"])[0]
                # Real long-poll: block up to POLL_TIMEOUT_MS waiting for a
                # command. The client (extension) can pass ?wait=ms to override.
                wait_ms = int(q.get("wait", [str(POLL_TIMEOUT_MS)])[0])
                wait_ms = max(0, min(wait_ms, POLL_TIMEOUT_MS))
                BRIDGE.gc_results()
                cmd = BRIDGE.dequeue(ext_id, timeout_ms=wait_ms)
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

        except Exception as exc:
            return self._send_json(500, {"ok": False, "error": str(exc)})
        finally:
            try:
                self.close_connection = True  # type: ignore[assignment]
            except Exception:
                pass

    # -----------------------------------------------------------------------
    # POST
    # -----------------------------------------------------------------------

    def do_POST(self) -> None:  # noqa: C901 — intentionally flat dispatch
        try:
            u = urlparse(self.path)
            body = self._read_json()
            p = u.path

            # Auth: every POST endpoint requires the token when auth is enabled.
            if not self._check_auth():
                return self._unauthorized()

            if p == "/cmd":
                if "__parse_error__" in body:
                    return self._send_json(400, {"ok": False, "error": "invalid JSON"})
                if "__body_too_large__" in body:
                    return self._send_json(413, {"ok": False, "error": f"request body too large: {body['size']} bytes > {body['limit']} bytes limit"})
                cid = body.get("id") or uuid.uuid4().hex[:12]
                ctype = body.get("type")
                if not ctype:
                    return self._send_json(400, {"ok": False, "error": "missing type"})
                if ctype not in COMMAND_TYPES:
                    return self._send_json(400, {
                        "ok": False,
                        "error": f"unknown command type {ctype!r}; use GET /commands for the list",
                    })
                if not BRIDGE.enqueue({"id": cid, "type": ctype, "args": body.get("args", {})}):
                    return self._send_json(429, {"ok": False, "error": f"command queue full ({MAX_QUEUE_SIZE}); retry later"})
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
                if "__body_too_large__" in body:
                    return self._send_json(413, {"ok": False, "error": f"request body too large: {body['size']} bytes > {body['limit']} bytes limit"})
                b64 = body.get("png_b64")
                if not b64:
                    return self._send_json(400, {"ok": False, "error": "missing png_b64"})
                os.makedirs(BRIDGE.screenshot_dir, exist_ok=True)
                ts = time.strftime("%Y%m%d-%H%M%S")
                fname = f"shot-{ts}-{uuid.uuid4().hex[:6]}.png"
                fpath = os.path.join(BRIDGE.screenshot_dir, fname)
                with open(fpath, "wb") as f:
                    f.write(base64.b64decode(b64))
                BRIDGE.log("ext", f"screenshot saved {fpath} ({len(b64)//1024}KB b64)")
                return self._send_json(200, {
                    "ok": True,
                    "path": fpath,
                    "url": _file_url(fpath),
                    "size": os.path.getsize(fpath),
                })

            if p == "/trace":
                if "__body_too_large__" in body:
                    return self._send_json(413, {"ok": False, "error": f"request body too large: {body['size']} bytes > {body['limit']} bytes limit"})
                b64 = body.get("png_b64")
                if not b64:
                    return self._send_json(400, {"ok": False, "error": "missing png_b64"})
                os.makedirs(BRIDGE.trace_root, exist_ok=True)
                ts = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
                trace_dir = os.path.join(BRIDGE.trace_root, ts)
                os.makedirs(trace_dir, exist_ok=True)
                files: list[dict[str, Any]] = []
                # 1) screenshot
                shot_path = os.path.join(trace_dir, "screenshot.png")
                with open(shot_path, "wb") as f:
                    f.write(base64.b64decode(b64))
                files.append({"name": "screenshot.png", "path": shot_path, "bytes": os.path.getsize(shot_path)})
                # 2) meta
                meta = body.get("meta") or {}
                meta_path = os.path.join(trace_dir, "meta.json")
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(meta, f, indent=2, ensure_ascii=False)
                files.append({"name": "meta.json", "path": meta_path, "bytes": os.path.getsize(meta_path)})
                # 3) focus
                focus = body.get("focus")
                if focus is not None:
                    focus_path = os.path.join(trace_dir, "focus.json")
                    with open(focus_path, "w", encoding="utf-8") as f:
                        json.dump(focus, f, indent=2, ensure_ascii=False)
                    files.append({"name": "focus.json", "path": focus_path, "bytes": os.path.getsize(focus_path)})
                # 4) a11y
                ax = body.get("ax")
                if ax is not None:
                    ax_path = os.path.join(trace_dir, "a11y.json")
                    with open(ax_path, "w", encoding="utf-8") as f:
                        json.dump(ax, f, indent=2, ensure_ascii=False)
                    files.append({"name": "a11y.json", "path": ax_path, "bytes": os.path.getsize(ax_path)})
                # 5) console
                console_msgs = body.get("console") or []
                console_path = os.path.join(trace_dir, "console.log")
                with open(console_path, "w", encoding="utf-8") as f:
                    for m in console_msgs:
                        f.write(f"[{m.get('ts', 0)}] {m.get('type', 'log')}: {m.get('text', '')}\n")
                files.append({"name": "console.log", "path": console_path, "bytes": os.path.getsize(console_path)})
                # 6) optional agent note
                note = body.get("note")
                if note:
                    note_path = os.path.join(trace_dir, "note.txt")
                    with open(note_path, "w", encoding="utf-8") as f:
                        f.write(str(note))
                    files.append({"name": "note.txt", "path": note_path, "bytes": os.path.getsize(note_path)})
                BRIDGE.log("ext", f"trace saved {trace_dir} ({len(files)} files)")
                return self._send_json(200, {
                    "ok": True,
                    "dir": trace_dir,
                    "url": _file_url(trace_dir),
                    "files": files,
                })

            if p == "/shutdown":
                BRIDGE.log("server", "shutdown requested via POST /shutdown")
                threading.Thread(target=_do_shutdown, args=(self.server,), daemon=True).start()
                return self._send_json(200, {"ok": True, "message": "shutting down"})

            if p == "/os":
                # OS-level input via pyautogui — for hybrid automation when
                # CDP can't reach (minimized windows, native dialogs, file
                # pickers, OS-level mouse/keyboard that has to happen OUTSIDE
                # the browser tab). Synchronous — these commands don't need
                # to round-trip through the extension.
                action = body.get("action")
                if not action:
                    return self._send_json(400, {"ok": False, "error": "missing action"})
                args_ = body.get("args", {}) or {}
                # Allowlist of safe pyautogui actions.
                safe = {
                    "click", "rightClick", "doubleClick", "tripleClick",
                    "moveTo", "moveRel", "dragTo", "dragRel",
                    "typewrite", "press", "hotkey", "keyDown", "keyUp",
                    "screenshot", "size", "position", "scroll",
                }
                if action not in safe:
                    return self._send_json(400, {
                        "ok": False,
                        "error": f"action {action!r} not in allowlist: {sorted(safe)}",
                    })
                if pyautogui is None:
                    return self._send_json(500, {
                        "ok": False,
                        "error": "pyautogui not installed; install with: pip install webbridge[os]",
                    })
                try:
                    fn = getattr(pyautogui, action)
                    # `screenshot` returns a PIL Image — serialize as PNG b64.
                    if action == "screenshot":
                        img = fn()
                        import io
                        buf = io.BytesIO()
                        img.save(buf, format="PNG")
                        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                        # Also save to disk for convenience.
                        os.makedirs(BRIDGE.screenshot_dir, exist_ok=True)
                        ts = time.strftime("%Y%m%d-%H%M%S")
                        fpath = os.path.join(BRIDGE.screenshot_dir, f"os-shot-{ts}-{uuid.uuid4().hex[:6]}.png")
                        with open(fpath, "wb") as f:
                            f.write(base64.b64decode(b64))
                        result: Any = {"path": fpath, "url": _file_url(fpath), "png_b64": b64, "size": img.size}
                    elif action in ("size", "position"):
                        result = tuple(fn())
                    else:
                        result = fn(**args_)
                    BRIDGE.log("agent", f"os.{action}({args_}) -> ok")
                    return self._send_json(200, {"ok": True, "value": result})
                except Exception as exc:
                    BRIDGE.log("agent", f"os.{action}({args_}) -> error: {exc}")
                    return self._send_json(500, {"ok": False, "error": str(exc)})

            return self._send_json(404, {"ok": False, "error": "no such path"})

        except Exception as exc:
            return self._send_json(500, {"ok": False, "error": str(exc)})
        finally:
            try:
                self.close_connection = True  # type: ignore[assignment]
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Server and shutdown helpers
# ---------------------------------------------------------------------------

class Server(socketserver.ThreadingTCPServer):
    """Threading HTTP server with address reuse."""

    allow_reuse_address: bool = True
    daemon_threads: bool = True


def _do_shutdown(srv: Server) -> None:
    """Background thread: signal the server to stop."""
    BRIDGE.shutdown()
    srv.shutdown()
    srv.server_close()
    BRIDGE.log("server", "server stopped, queue drained")


def _signal_handler(signum: int, _frame: Any) -> None:
    """Handle SIGINT/SIGTERM by triggering a graceful shutdown."""
    signame = signal.Signals(signum).name
    BRIDGE.log("server", f"received {signame}, shutting down")
    BRIDGE.shutdown()
    sys.exit(0)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    p = argparse.ArgumentParser(
        prog="webbridge",
        description="WebBridge server — bridges AI agents to browser extensions via HTTP.",
    )
    p.add_argument("--host", default=os.environ.get("WEBBRIDGE_HOST", "127.0.0.1"),
                   help="bind address (env: WEBBRIDGE_HOST, default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=int(os.environ.get("WEBBRIDGE_PORT", "9876")),
                   help="bind port (env: WEBBRIDGE_PORT, default: 9876)")
    p.add_argument("--log-level", default=os.environ.get("WEBBRIDGE_LOG_LEVEL", "INFO"),
                   choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                   help="log level (env: WEBBRIDGE_LOG_LEVEL, default: INFO)")
    p.add_argument("--log-file", default=os.environ.get("WEBBRIDGE_LOG_FILE"),
                   help="optional log file path (env: WEBBRIDGE_LOG_FILE)")
    p.add_argument("--require-auth", action="store_true",
                   help="fail to start if WEBBRIDGE_TOKEN is not set (use in production / shared hosts)")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    """Entry point: configure logging, start the HTTP server, handle signals."""
    args = parse_args(argv)
    setup_logging(args.log_level, args.log_file)

    auth_enabled = bool(os.environ.get("WEBBRIDGE_TOKEN"))
    if args.require_auth and not auth_enabled:
        sys.stderr.write(
            "ERROR: --require-auth was passed but WEBBRIDGE_TOKEN is not set.\n"
            "Set WEBBRIDGE_TOKEN in the environment (e.g. export WEBBRIDGE_TOKEN=$(openssl rand -hex 32))\n"
            "and restart the server.\n"
        )
        sys.exit(2)

    data_dir = _default_data_dir()
    global BRIDGE  # noqa: PLW0603
    BRIDGE = Bridge(data_dir)
    BRIDGE.start_gc_thread()  # background sweep of stale results

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    srv = Server((args.host, args.port), Handler)

    # Print a clear startup banner with the data dir + auth status.
    BRIDGE.log("server", f"webbridge v{_PKG_VERSION} listening on http://{args.host}:{args.port}")
    BRIDGE.log("server", f"data_dir: {data_dir}")
    BRIDGE.log("server", f"auth: {'ENABLED (WEBBRIDGE_TOKEN set)' if auth_enabled else 'DISABLED (set WEBBRIDGE_TOKEN to enable)'}")
    BRIDGE.log("server", f"pyautogui: {'available' if pyautogui else 'not installed (pip install webbridge[os])'}")
    BRIDGE.log("server", f"limits: queue={MAX_QUEUE_SIZE}, body={MAX_REQUEST_BYTES // 1024 // 1024}MB, gc={GC_INTERVAL_S}s")

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        BRIDGE.log("server", "keyboard interrupt, shutting down")
    finally:
        BRIDGE.shutdown()
        srv.shutdown()
        srv.server_close()
        BRIDGE.log("server", "server stopped, queue drained")


if __name__ == "__main__":
    main()
