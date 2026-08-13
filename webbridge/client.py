#!/usr/bin/env python3
"""
webbridge CLI — command-line interface for the agentic web bridge.

Examples:
    python client.py ping
    python client.py --server http://host:9876 tabs
    python client.py eval "document.title"
    python client.py navigate https://example.com
    python client.py click "#submit"
    python client.py type "#q" "search text"
    python client.py screenshot
    python client.py cookies get
    python client.py upload "input[type=file]" /tmp/a.txt /tmp/b.pdf

New in v4:
    python client.py readable                 # LLM-optimized text dump of the page
    python client.py readable --text-only     # one big text block (paste into any LLM)
    python client.py vision "describe this page"
    python client.py osclick 100 200          # OS-level mouse click via pyautogui
    python client.py ostype "hello world"     # OS-level keyboard
    python client.py osscreenshot             # OS-level screenshot (whole desktop)
    python client.py oshotkey ctrl c          # OS-level hotkey
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_SERVER = "http://127.0.0.1:9876"


def _load_config(cli_config_path=None):
    """Merge config from files, env, and CLI flags.

    Precedence (lowest to highest): defaults < ~/.webbridge < ./.webbridge
    < --config file < env vars < CLI flags (applied in main()).
    """
    cfg = {
        "server": DEFAULT_SERVER,
        "tab": None,
        "wait": 15000,
    }
    for path in [os.path.expanduser("~/.webbridge"), ".webbridge"]:
        if os.path.isfile(path):
            try:
                with open(path) as f:
                    data = json.load(f)
                cfg.update({k: v for k, v in data.items() if v is not None})
            except Exception as e:
                # Warn loudly — silently swallowing config errors bites users later.
                print(f"warning: failed to parse {path}: {e}", file=sys.stderr)
    if cli_config_path and os.path.isfile(cli_config_path):
        try:
            with open(cli_config_path) as f:
                data = json.load(f)
            cfg.update({k: v for k, v in data.items() if v is not None})
        except Exception as e:
            print(f"warning: failed to parse {cli_config_path}: {e}", file=sys.stderr)
    # Env vars override config files but NOT CLI flags.
    if os.environ.get("WEBBRIDGE_URL"):
        cfg["server"] = os.environ["WEBBRIDGE_URL"]
    if os.environ.get("WEBBRIDGE_TAB"):
        cfg["tab"] = os.environ["WEBBRIDGE_TAB"]
    if os.environ.get("WEBBRIDGE_WAIT"):
        try:
            cfg["wait"] = int(os.environ["WEBBRIDGE_WAIT"])
        except ValueError:
            pass
    return cfg


# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------

_RED = "\033[91m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _color(text, code):
    if not sys.stdout.isatty():
        return text
    return f"{code}{text}{_RESET}"


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only)
# ---------------------------------------------------------------------------

class BridgeError(Exception):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


def _http_post(server, path, body, timeout=30):
    data = json.dumps(body).encode("utf-8")
    url = server.rstrip("/") + path
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise BridgeError(f"Connection failed: {exc.reason}") from exc
    except urllib.error.HTTPError as exc:
        raise BridgeError(f"HTTP {exc.code}: {exc.reason}", status=exc.code) from exc


def _http_get(server, path, timeout=30):
    url = server.rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise BridgeError(f"Connection failed: {exc.reason}") from exc
    except urllib.error.HTTPError as exc:
        raise BridgeError(f"HTTP {exc.code}: {exc.reason}", status=exc.code) from exc


def send_command(server, cmd_type, args=None, tab_id=None, wait_ms=15000):
    """Two-phase: POST /cmd to enqueue, then GET /result?id=&wait= to block."""
    # Use UUID for cid — the old "cli-<millis>" scheme collided when a
    # script issued >1 command per millisecond.
    cid = "cli-" + uuid.uuid4().hex[:12]
    body = {"id": cid, "type": cmd_type, "args": args or {}}
    if tab_id:
        body["tabId"] = tab_id
    # HTTP timeout must be >= server wait so the long-poll can actually return.
    http_timeout = max(30, (wait_ms / 1000.0) + 5.0)
    _http_post(server, "/cmd", body, timeout=http_timeout)
    res = _http_get(server, f"/result?id={cid}&wait={wait_ms}", timeout=http_timeout)
    if not res.get("ok"):
        return res
    r = res.get("result") or {}
    if r.get("ok"):
        return {"ok": True, "value": r.get("value")}
    return {"ok": False, "error": r.get("error")}


def _os_command(server, action, args=None, timeout=30):
    """Synchronous /os endpoint — no queue round-trip."""
    return _http_post(server, "/os", {"action": action, "args": args or {}}, timeout=timeout)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _print_result(data, raw_json=False, command=None):
    if raw_json:
        print(json.dumps(data, indent=2, default=str))
        return

    if not data.get("ok"):
        err = data.get("error", "unknown error")
        print(_color(f"Error: {err}", _RED), file=sys.stderr)
        return

    val = data.get("value")

    # Special-case `readable` so the textBlock is printed as plain text
    # (much friendlier for piping to grep / less / an LLM prompt file).
    if command == "readable" and isinstance(val, dict):
        if val.get("textBlock"):
            print(val["textBlock"])
            return
        print(json.dumps(val, indent=2, default=str))
        return

    # Special-case `vision` so the base64 screenshot is suppressed in pretty mode.
    if command == "vision" and isinstance(val, dict):
        # Don't dump raw b64 — just show the description and any structured fields.
        clean = {k: v for k, v in val.items() if k not in ("screenshot_b64", "screenshot_png_b64", "png_b64")}
        if val.get("screenshot_path"):
            clean["screenshot_path"] = val["screenshot_path"]
        print(json.dumps(clean, indent=2, default=str))
        return

    # Special-case `osscreenshot` so we don't dump raw b64.
    if command == "osscreenshot" and isinstance(val, dict):
        clean = {k: v for k, v in val.items() if k != "png_b64"}
        print(json.dumps(clean, indent=2, default=str))
        return

    if isinstance(val, dict):
        print(json.dumps(val, indent=2, default=str))
    elif isinstance(val, list):
        # Only treat as tabs table if entries look like tab objects.
        if val and all(isinstance(t, dict) and ("url" in t or "title" in t or "tabId" in t) for t in val[:3]):
            _print_tabs_table(val)
        else:
            print(json.dumps(val, indent=2, default=str))
    elif isinstance(val, str):
        if len(val) > 1000 and ("html" in val.lower() or "<" in val[:200]):
            _print_html(val)
        else:
            print(val)
    elif val is None:
        print(_color("OK", _GREEN))
    else:
        print(val)


def _print_tabs_table(tabs):
    if not tabs:
        print("No tabs open.")
        return
    header = f"{'#':<4} {'Title':<45} {'URL':<55} {'ID':<10} {'Pinned'}"
    print(_color(header, _BOLD))
    print("-" * 130)
    for i, tab in enumerate(tabs):
        tid = tab.get("id", tab.get("tabId", ""))
        title = (tab.get("title", "") or "")[:44]
        url = (tab.get("url", "") or "")[:54]
        pinned = "*" if tab.get("pinned") else ""
        print(f"{i:<4} {title:<45} {url:<55} {tid:<10} {pinned}")


def _print_html(html_str):
    lines = html_str.count("\n") + 1
    if lines > 1000 or len(html_str) > 8000:
        try:
            import subprocess
            proc = subprocess.Popen(["less", "-R"], stdin=subprocess.PIPE)
            proc.communicate(html_str.encode("utf-8"))
            return
        except Exception:
            pass
    print(html_str)


def _print_error(msg):
    print(_color(f"Error: {msg}", _RED), file=sys.stderr)


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_ping(args, cfg):
    return send_command(cfg["server"], "ping", wait_ms=cfg["wait"])


def cmd_tabs(args, cfg):
    # FIX: previously did GET /tabs which isn't a route. Go through /cmd.
    return send_command(cfg["server"], "tabs", wait_ms=cfg["wait"])


def cmd_active_tab(args, cfg):
    # FIX: previously sent "activeTab" (camelCase) which the server rejected.
    return send_command(cfg["server"], "active_tab", wait_ms=cfg["wait"])


def cmd_title(args, cfg):
    return send_command(cfg["server"], "title", wait_ms=cfg["wait"])


def cmd_url(args, cfg):
    return send_command(cfg["server"], "url", wait_ms=cfg["wait"])


def cmd_html(args, cfg):
    return send_command(cfg["server"], "html", wait_ms=cfg["wait"])


def cmd_snippet(args, cfg):
    return send_command(cfg["server"], "snippet", wait_ms=cfg["wait"])


def cmd_readable(args, cfg):
    body = {}
    if args.max_chars is not None:
        body["maxChars"] = args.max_chars
    if args.no_a11y:
        body["includeA11y"] = False
    if args.no_forms:
        body["includeForms"] = False
    if args.console:
        body["includeConsole"] = True
    return send_command(cfg["server"], "readable", body, wait_ms=cfg["wait"])


def cmd_eval(args, cfg):
    return send_command(cfg["server"], "eval", {"code": args.code}, wait_ms=cfg["wait"])


def cmd_navigate(args, cfg):
    return send_command(cfg["server"], "navigate", {"url": args.url}, wait_ms=cfg["wait"])


def cmd_click(args, cfg):
    return send_command(cfg["server"], "click", {"selector": args.selector}, wait_ms=cfg["wait"])


def cmd_type(args, cfg):
    return send_command(cfg["server"], "type",
                        {"selector": args.selector, "text": args.text}, wait_ms=cfg["wait"])


def cmd_key(args, cfg):
    return send_command(cfg["server"], "key", {"key": args.key}, wait_ms=cfg["wait"])


def cmd_hover(args, cfg):
    return send_command(cfg["server"], "hover", {"selector": args.selector}, wait_ms=cfg["wait"])


def cmd_drag(args, cfg):
    return send_command(cfg["server"], "drag",
                        {"from": args.from_sel, "to": args.to_sel}, wait_ms=cfg["wait"])


def cmd_select(args, cfg):
    return send_command(cfg["server"], "select",
                        {"selector": args.selector, "value": args.value}, wait_ms=cfg["wait"])


def cmd_screenshot(args, cfg):
    return send_command(cfg["server"], "screenshot", wait_ms=cfg["wait"])


def cmd_vision(args, cfg):
    """Take a screenshot + readable dump in one call.

    The extension returns both — the agent (or caller) can then forward the
    screenshot to any vision model (GPT-4V, Claude with vision, GLM-4V, etc.)
    and use the readable text as context for a text model. The bridge itself
    does NOT call any VLM — that's the agent's job.
    """
    body = {"prompt": args.prompt or ""}
    if args.include_a11y:
        body["includeA11y"] = True
    return send_command(cfg["server"], "vision", body, wait_ms=cfg["wait"])


def cmd_scroll(args, cfg):
    return send_command(cfg["server"], "scroll", {"direction": args.direction}, wait_ms=cfg["wait"])


def cmd_back(args, cfg):
    return send_command(cfg["server"], "back", wait_ms=cfg["wait"])


def cmd_forward(args, cfg):
    return send_command(cfg["server"], "forward", wait_ms=cfg["wait"])


def cmd_refresh(args, cfg):
    return send_command(cfg["server"], "refresh", {"hard": args.hard}, wait_ms=cfg["wait"])


def cmd_cookies(args, cfg):
    sub = args.cookie_action or "get"
    body = {"action": sub}
    if sub == "set" and args.cookie_data:
        try:
            body["cookies"] = json.loads(args.cookie_data)
        except json.JSONDecodeError as e:
            return {"ok": False, "error": f"invalid JSON for cookies set: {e}"}
    elif sub == "delete" and args.cookie_data:
        body["name"] = args.cookie_data
    return send_command(cfg["server"], "cookies", body, wait_ms=cfg["wait"])


def cmd_upload(args, cfg):
    return send_command(cfg["server"], "upload",
                        {"selector": args.selector, "files": args.files}, wait_ms=cfg["wait"])


def cmd_trace(args, cfg):
    return send_command(cfg["server"], "trace", wait_ms=cfg["wait"])


def cmd_tabs_focus(args, cfg):
    # FIX: previously sent "tabs.focus" which wasn't in COMMAND_TYPES.
    # The server has no separate "focus" command type — we use `active_tab`
    # to get info, and switching tabs is done by clicking the tab in the
    # browser UI (or by the user). For agent-driven tab switching, the
    # extension's "attach" command (gated by the pinned-tab rule) is the
    # path. We return a clear error explaining this.
    return {
        "ok": False,
        "error": (
            "tabs-focus is no longer needed — the bridge now operates on the pinned tab only. "
            "To switch tabs, click the WebBridge popup and pin a different tab."
        ),
    }


def cmd_log(args, cfg):
    # FIX: previously sent "log" which wasn't in COMMAND_TYPES (server has "console").
    body = {}
    if args.count:
        body["count"] = args.count
    return send_command(cfg["server"], "console", body, wait_ms=cfg["wait"])


# --- OS-level commands (pyautogui hybrid mode) ---

def cmd_osclick(args, cfg):
    return _os_command(cfg["server"], args.button, {
        "x": args.x, "y": args.y, "button": "left",
    } if args.button == "click" else {
        "x": args.x, "y": args.y,
    })


def cmd_ostype(args, cfg):
    return _os_command(cfg["server"], "typewrite", {"text": args.text, "interval": args.interval})


def cmd_osshot(args, cfg):
    return _os_command(cfg["server"], "screenshot", timeout=15)


def cmd_oshotkey(args, cfg):
    return _os_command(cfg["server"], "hotkey", {"keys": args.keys})


def cmd_osmove(args, cfg):
    return _os_command(cfg["server"], "moveTo", {"x": args.x, "y": args.y, "duration": args.duration})


def cmd_ospress(args, cfg):
    return _os_command(cfg["server"], "press", {"key": args.key})


def cmd_ossize(args, cfg):
    return _os_command(cfg["server"], "size", {})


def cmd_osposition(args, cfg):
    return _os_command(cfg["server"], "position", {})


# ---------------------------------------------------------------------------
# Build parser
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="webbridge",
        description="CLI for the agentic web bridge (v4)",
    )
    p.add_argument("--server", help=f"Server URL (default: $WEBBRIDGE_URL or {DEFAULT_SERVER})")
    p.add_argument("--tab", dest="tab_id", help="Target tab by ID (NOTE: bridge now operates on pinned tab only)")
    p.add_argument("--wait", type=int, help="Result wait timeout in ms (default: 15000)")
    p.add_argument("--json", dest="raw_json", action="store_true", help="Output raw JSON")
    p.add_argument("--quiet", action="store_true", help="Suppress info, only output result")
    p.add_argument("--config", help="Path to config file")

    subs = p.add_subparsers(dest="command")

    # --- simple commands ---
    subs.add_parser("ping", help="Ping the server")
    subs.add_parser("tabs", help="List open tabs (shows which is pinned)")
    subs.add_parser("active-tab", help="Get active tab")
    subs.add_parser("title", help="Get page title")
    subs.add_parser("url", help="Get page URL")
    subs.add_parser("html", help="Get full HTML")
    subs.add_parser("snippet", help="Get visible text snippet (2000 chars)")

    # --- readable (NEW: LLM-optimized text dump) ---
    sp = subs.add_parser("readable",
                         help="LLM-optimized text dump of the page (URL, text, headings, "
                              "interactive elements, a11y tree). Designed for text-only AIs.")
    sp.add_argument("--max-chars", type=int, default=20000, dest="max_chars",
                    help="Max chars per text field (default: 20000)")
    sp.add_argument("--no-a11y", action="store_true", help="Skip accessibility tree")
    sp.add_argument("--no-forms", action="store_true", help="Skip interactive-elements list")
    sp.add_argument("--console", action="store_true", help="Include last 30 console messages")

    # --- eval ---
    sp = subs.add_parser("eval", help="Evaluate JavaScript (bypasses page CSP via CDP)")
    sp.add_argument("code", help="JavaScript code to evaluate")

    # --- navigate ---
    sp = subs.add_parser("navigate", help="Navigate to URL")
    sp.add_argument("url", help="URL to navigate to")

    # --- click ---
    sp = subs.add_parser("click", help="Click an element (humanized by default)")
    sp.add_argument("selector", help="CSS selector")

    # --- type ---
    sp = subs.add_parser("type", help="Type text into an element (humanized by default)")
    sp.add_argument("selector", help="CSS selector")
    sp.add_argument("text", help="Text to type")

    # --- key ---
    sp = subs.add_parser("key", help="Press a key")
    sp.add_argument("key", help="Key name (e.g. Enter, Tab, Escape)")

    # --- hover ---
    sp = subs.add_parser("hover", help="Hover over an element")
    sp.add_argument("selector", help="CSS selector")

    # --- drag ---
    sp = subs.add_parser("drag", help="Drag from one element to another")
    sp.add_argument("from_sel", help="Source CSS selector")
    sp.add_argument("to_sel", help="Destination CSS selector")

    # --- select ---
    sp = subs.add_parser("select", help="Select an option in a <select>")
    sp.add_argument("selector", help="CSS selector")
    sp.add_argument("value", help="Option value")

    # --- screenshot ---
    subs.add_parser("screenshot", help="Take a screenshot of the pinned tab")

    # --- vision (NEW: screenshot + readable, for VLM callers) ---
    sp = subs.add_parser("vision",
                         help="Take a screenshot + readable text dump in one call. "
                              "The bridge returns both — the CALLER (agent) forwards the "
                              "screenshot to a VLM (GPT-4V, Claude, GLM-4V, etc.).")
    sp.add_argument("prompt", nargs="?", default="", help="Optional prompt to tag the capture")
    sp.add_argument("--include-a11y", action="store_true", default=True,
                    help="Include a11y tree in the readable companion (default: true)")

    # --- scroll ---
    sp = subs.add_parser("scroll", help="Scroll up or down")
    sp.add_argument("direction", choices=["up", "down"], help="Scroll direction")

    # --- navigation ---
    subs.add_parser("back", help="Navigate back")
    subs.add_parser("forward", help="Navigate forward")

    # --- refresh ---
    sp = subs.add_parser("refresh", help="Refresh the page")
    sp.add_argument("--hard", action="store_true", help="Hard refresh (bypass cache)")

    # --- cookies ---
    sp = subs.add_parser("cookies", help="Cookie management")
    sp.add_argument("cookie_action", nargs="?", choices=["get", "set", "delete"],
                    default="get", help="Cookie action (default: get)")
    sp.add_argument("cookie_data", nargs="?",
                    help="JSON for set, name for delete")

    # --- upload ---
    sp = subs.add_parser("upload", help="Upload files to an input element")
    sp.add_argument("selector", help="CSS selector")
    sp.add_argument("files", nargs="+", help="File path(s)")

    # --- trace ---
    subs.add_parser("trace", help="Take a trace snapshot (screenshot + a11y + console)")

    # --- log (FIX: actually works now — was sending wrong type) ---
    sp = subs.add_parser("log", help="Get console log from the pinned tab")
    sp.add_argument("--count", type=int, help="Number of entries to return")

    # --- OS-level (NEW: pyautogui hybrid mode) ---
    sp = subs.add_parser("osclick", help="OS-level mouse click at (x, y) via pyautogui")
    sp.add_argument("x", type=int)
    sp.add_argument("y", type=int)
    sp.add_argument("--button", choices=["click", "rightClick", "doubleClick"],
                    default="click", dest="button")

    sp = subs.add_parser("ostype", help="OS-level type text via pyautogui")
    sp.add_argument("text", help="Text to type")
    sp.add_argument("--interval", type=float, default=0.0,
                    help="Seconds between keypresses (default: 0)")

    sp = subs.add_parser("osscreenshot", help="OS-level screenshot of the whole desktop")
    sp = subs.add_parser("ossize", help="Get screen size (width, height) via pyautogui")
    sp = subs.add_parser("osposition", help="Get current mouse position via pyautogui")

    sp = subs.add_parser("osmove", help="OS-level move mouse to (x, y) via pyautogui")
    sp.add_argument("x", type=int)
    sp.add_argument("y", type=int)
    sp.add_argument("--duration", type=float, default=0.0,
                    help="Seconds to animate the move (default: 0 = instant)")

    sp = subs.add_parser("ospress", help="OS-level press a single key via pyautogui")
    sp.add_argument("key", help="Key name (e.g. enter, esc, tab, f1)")

    sp = subs.add_parser("oshotkey", help="OS-level hotkey combo via pyautogui")
    sp.add_argument("keys", nargs="+", help="Keys to press together (e.g. ctrl c)")

    return p


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    # Load config: file defaults < env < CLI flags
    cfg = _load_config(getattr(args, "config", None))
    if getattr(args, "server", None):
        cfg["server"] = args.server
    if getattr(args, "tab_id", None):
        cfg["tab_id"] = args.tab_id
    if getattr(args, "wait", None) is not None:
        cfg["wait"] = args.wait

    raw_json = getattr(args, "raw_json", False)
    quiet = getattr(args, "quiet", False)
    if quiet:
        # Quiet mode: suppress only info messages. Errors still go to stderr.
        pass

    dispatch = {
        "ping": cmd_ping,
        "tabs": cmd_tabs,
        "active-tab": cmd_active_tab,
        "title": cmd_title,
        "url": cmd_url,
        "html": cmd_html,
        "snippet": cmd_snippet,
        "readable": cmd_readable,
        "eval": cmd_eval,
        "navigate": cmd_navigate,
        "click": cmd_click,
        "type": cmd_type,
        "key": cmd_key,
        "hover": cmd_hover,
        "drag": cmd_drag,
        "select": cmd_select,
        "screenshot": cmd_screenshot,
        "vision": cmd_vision,
        "scroll": cmd_scroll,
        "back": cmd_back,
        "forward": cmd_forward,
        "refresh": cmd_refresh,
        "cookies": cmd_cookies,
        "upload": cmd_upload,
        "trace": cmd_trace,
        "log": cmd_log,
        # OS-level
        "osclick": cmd_osclick,
        "ostype": cmd_ostype,
        "osscreenshot": cmd_osshot,
        "ossize": cmd_ossize,
        "osposition": cmd_osposition,
        "osmove": cmd_osmove,
        "ospress": cmd_ospress,
        "oshotkey": cmd_oshotkey,
    }

    handler = dispatch.get(args.command)
    if not handler:
        _print_error(f"Unknown command: {args.command}")
        return 1

    try:
        result = handler(args, cfg)
    except BridgeError as exc:
        if raw_json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        else:
            _print_error(str(exc))
        return 1

    _print_result(result, raw_json=raw_json, command=args.command)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
