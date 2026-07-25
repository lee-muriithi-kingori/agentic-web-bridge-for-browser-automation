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
    python client.py --tab tab123 screenshot
    python client.py cookies get
    python client.py upload "input[type=file]" /tmp/a.txt /tmp/b.pdf
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_config(cli_config_path=None):
    """Merge config from files, env, and CLI flags."""
    cfg = {
        "server": "http://127.0.0.1:9876",
        "tab": None,
        "wait": 15000,
    }
    for path in [os.path.expanduser("~/.webbridge"), ".webbridge"]:
        if os.path.isfile(path):
            try:
                with open(path) as f:
                    data = json.load(f)
                cfg.update({k: v for k, v in data.items() if v is not None})
            except Exception:
                pass
    if cli_config_path and os.path.isfile(cli_config_path):
        try:
            with open(cli_config_path) as f:
                data = json.load(f)
            cfg.update({k: v for k, v in data.items() if v is not None})
        except Exception:
            pass
    if os.environ.get("WEBBRIDGE_URL"):
        cfg["server"] = os.environ["WEBBRIDGE_URL"]
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
    cid = "cli-" + str(int(time.time() * 1000))
    body = {"id": cid, "type": cmd_type, "args": args or {}}
    if tab_id:
        body["tabId"] = tab_id
    _http_post(server, "/cmd", body)
    res = _http_get(server, f"/result?id={cid}&wait={wait_ms}")
    if not res.get("ok"):
        return res
    r = res.get("result") or {}
    if r.get("ok"):
        return {"ok": True, "value": r.get("value")}
    return {"ok": False, "error": r.get("error")}


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _print_result(data, raw_json=False):
    if raw_json:
        print(json.dumps(data, indent=2, default=str))
        return

    if not data.get("ok"):
        err = data.get("error", "unknown error")
        print(_color(f"Error: {err}", _RED))
        return

    val = data.get("value")

    if isinstance(val, dict):
        print(json.dumps(val, indent=2, default=str))
    elif isinstance(val, list):
        _print_tabs_table(val)
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
    header = f"{'#':<4} {'Title':<45} {'URL':<60} {'ID'}"
    print(_color(header, _BOLD))
    print("-" * 140)
    for i, tab in enumerate(tabs):
        tid = tab.get("id", tab.get("tabId", ""))
        title = tab.get("title", "")[:44]
        url = tab.get("url", "")[:59]
        print(f"{i:<4} {title:<45} {url:<60} {tid}")


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
    print(_color(f"Error: {msg}", _RED))


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_ping(args, cfg):
    return send_command(cfg["server"], "ping", wait_ms=cfg["wait"])


def cmd_tabs(args, cfg):
    return _http_get(cfg["server"], "/tabs")


def cmd_active_tab(args, cfg):
    return send_command(cfg["server"], "activeTab", wait_ms=cfg["wait"])


def cmd_title(args, cfg):
    return send_command(cfg["server"], "title", wait_ms=cfg["wait"])


def cmd_url(args, cfg):
    return send_command(cfg["server"], "url", wait_ms=cfg["wait"])


def cmd_html(args, cfg):
    return send_command(cfg["server"], "html", wait_ms=cfg["wait"])


def cmd_snippet(args, cfg):
    return send_command(cfg["server"], "snippet", wait_ms=cfg["wait"])


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
        body["cookies"] = json.loads(args.cookie_data)
    elif sub == "delete" and args.cookie_data:
        body["name"] = args.cookie_data
    return send_command(cfg["server"], "cookies", body, wait_ms=cfg["wait"])


def cmd_upload(args, cfg):
    return send_command(cfg["server"], "upload",
                        {"selector": args.selector, "files": args.files}, wait_ms=cfg["wait"])


def cmd_trace(args, cfg):
    return send_command(cfg["server"], "trace", wait_ms=cfg["wait"])


def cmd_tabs_focus(args, cfg):
    return send_command(cfg["server"], "tabs.focus", {"tabId": args.tab_id}, wait_ms=cfg["wait"])


def cmd_log(args, cfg):
    body = {}
    if args.count:
        body["count"] = args.count
    return send_command(cfg["server"], "log", body, wait_ms=cfg["wait"])


# ---------------------------------------------------------------------------
# Backward compatibility shim
# ---------------------------------------------------------------------------

_LEGACY_COMMANDS = {
    "ping", "tabs", "active-tab", "title", "url", "html", "snippet", "eval",
    "navigate", "click", "type", "key", "hover", "drag", "select", "screenshot",
    "scroll", "back", "forward", "refresh", "cookies", "upload", "trace",
}


def _rewrite_legacy_args(argv):
    """If argv[1] looks like a known command (not starting with '-'), inject
    'webbridge' as argv[0] so argparse subcommands pick it up."""
    if len(argv) >= 2 and not argv[1].startswith("-") and argv[1] in _LEGACY_COMMANDS:
        return argv
    return argv


# ---------------------------------------------------------------------------
# Build parser
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="webbridge",
        description="CLI for the agentic web bridge",
    )
    p.add_argument("--server", help="Server URL (default: $WEBBRIDGE_URL or http://127.0.0.1:9876)")
    p.add_argument("--tab", dest="tab_id", help="Target a specific tab by ID")
    p.add_argument("--wait", type=int, help="Result wait timeout in ms (default: 15000)")
    p.add_argument("--json", dest="raw_json", action="store_true", help="Output raw JSON")
    p.add_argument("--quiet", action="store_true", help="Suppress info, only output result")
    p.add_argument("--config", help="Path to config file")

    subs = p.add_subparsers(dest="command")

    # --- simple commands ---
    subs.add_parser("ping", help="Ping the server")
    subs.add_parser("tabs", help="List open tabs")
    subs.add_parser("active-tab", help="Get active tab")
    subs.add_parser("title", help="Get page title")
    subs.add_parser("url", help="Get page URL")
    subs.add_parser("html", help="Get full HTML")
    subs.add_parser("snippet", help="Get visible text snippet")

    # --- eval ---
    sp = subs.add_parser("eval", help="Evaluate JavaScript")
    sp.add_argument("code", help="JavaScript code to evaluate")

    # --- navigate ---
    sp = subs.add_parser("navigate", help="Navigate to URL")
    sp.add_argument("url", help="URL to navigate to")

    # --- click ---
    sp = subs.add_parser("click", help="Click an element")
    sp.add_argument("selector", help="CSS selector")

    # --- type ---
    sp = subs.add_parser("type", help="Type text into an element")
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
    subs.add_parser("screenshot", help="Take a screenshot")

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
    sp.add_argument("cookie_data", nargs="?", help="JSON for set, name for delete")

    # --- upload ---
    sp = subs.add_parser("upload", help="Upload files to an input element")
    sp.add_argument("selector", help="CSS selector")
    sp.add_argument("files", nargs="+", help="File path(s)")

    # --- trace ---
    subs.add_parser("trace", help="Take a trace snapshot")

    # --- tabs focus ---
    sp = subs.add_parser("tabs-focus", help="Focus a specific tab")
    sp.add_argument("tab_id", help="Tab ID to focus")

    # --- log ---
    sp = subs.add_parser("log", help="Get console log")
    sp.add_argument("--count", type=int, help="Number of entries to return")

    return p


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    argv = _rewrite_legacy_args(sys.argv)
    parser = build_parser()
    args = parser.parse_args(argv[1:])

    if not args.command:
        parser.print_help()
        return 0

    # Load config: file defaults < env < CLI flags
    cfg = _load_config(args.config if hasattr(args, "config") else None)
    if hasattr(args, "server") and args.server:
        cfg["server"] = args.server
    if hasattr(args, "tab_id") and args.tab_id:
        cfg["tab_id"] = args.tab_id
    if hasattr(args, "wait") and args.wait is not None:
        cfg["wait"] = args.wait

    raw_json = getattr(args, "raw_json", False)

    dispatch = {
        "ping": cmd_ping,
        "tabs": cmd_tabs,
        "active-tab": cmd_active_tab,
        "title": cmd_title,
        "url": cmd_url,
        "html": cmd_html,
        "snippet": cmd_snippet,
        "eval": cmd_eval,
        "navigate": cmd_navigate,
        "click": cmd_click,
        "type": cmd_type,
        "key": cmd_key,
        "hover": cmd_hover,
        "drag": cmd_drag,
        "select": cmd_select,
        "screenshot": cmd_screenshot,
        "scroll": cmd_scroll,
        "back": cmd_back,
        "forward": cmd_forward,
        "refresh": cmd_refresh,
        "cookies": cmd_cookies,
        "upload": cmd_upload,
        "trace": cmd_trace,
        "tabs-focus": cmd_tabs_focus,
        "log": cmd_log,
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

    _print_result(result, raw_json=raw_json)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
