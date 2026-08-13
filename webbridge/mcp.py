#!/usr/bin/env python3
"""
webbridge.mcp — Model Context Protocol server for the Agentic Web Bridge.

Exposes the bridge as an MCP server so any MCP-compatible client (Claude
Desktop, Cursor, Cline, VS Code, etc.) can drive the pinned browser tab
as a set of tools.

Protocol: stdio (JSON-RPC 2.0). The MCP client spawns this process and
talks to it over stdin/stdout. No HTTP server is needed on the MCP side —
this module talks HTTP to the webbridge server (default 127.0.0.1:9876),
and exposes the bridge's commands as MCP tools.

Usage (Claude Desktop config example):

    {
      "mcpServers": {
        "webbridge": {
          "command": "python",
          "args": ["-m", "webbridge.mcp"],
          "env": {
            "WEBBRIDGE_URL": "http://127.0.0.1:9876",
            "WEBBRIDGE_TOKEN": "your-secret-token-here"
          }
        }
      }
    }

Or via the installed entry point:

    "command": "webbridge-mcp"

The MCP server is stdlib-only (no `mcp` package required) — it implements
the subset of the MCP spec needed for tools/list and tools/call over
stdio. If you have the `mcp` package installed and want the full MCP
feature set (resources, prompts, sampling), you can swap this for a
thin wrapper that delegates to `mcp.server.Server`.
"""

import json
import os
import sys
from typing import Any, Optional

# Reuse the bridge client — it already knows how to talk HTTP to the server.
from webbridge.client import send_command, _os_command, BridgeError, _load_config


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

# Each tool maps to a webbridge command. The MCP client discovers these via
# tools/list and calls them via tools/call.
TOOLS: list[dict[str, Any]] = [
    {
        "name": "wb_ping",
        "description": "Ping the webbridge server + extension. Returns pong + pinned tab ID. Use this first to verify the bridge is connected.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "wb_navigate",
        "description": "Navigate the pinned tab to a URL.",
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "URL to navigate to"}},
            "required": ["url"],
        },
    },
    {
        "name": "wb_readable",
        "description": "Get an LLM-optimized text dump of the pinned tab — URL, title, visible text, headings, interactive elements (with CSS selectors), accessibility tree. Designed for text-only LLMs. Returns a 'textBlock' field that's ready to paste into a prompt.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "max_chars": {"type": "integer", "default": 20000, "description": "Max chars per text field"},
                "include_a11y": {"type": "boolean", "default": True},
                "include_forms": {"type": "boolean", "default": True},
                "include_console": {"type": "boolean", "default": False},
            },
            "required": [],
        },
    },
    {
        "name": "wb_click",
        "description": "Click an element in the pinned tab by CSS selector. Humanized mouse trajectory.",
        "inputSchema": {
            "type": "object",
            "properties": {"selector": {"type": "string", "description": "CSS selector"}},
            "required": ["selector"],
        },
    },
    {
        "name": "wb_type",
        "description": "Type text into an input element in the pinned tab. Humanized per-character.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector"},
                "text": {"type": "string", "description": "Text to type"},
            },
            "required": ["selector", "text"],
        },
    },
    {
        "name": "wb_key",
        "description": "Press a single key (Enter, Tab, Escape, arrow keys, etc.) in the pinned tab.",
        "inputSchema": {
            "type": "object",
            "properties": {"key": {"type": "string", "description": "Key name (e.g. Enter, Tab, Escape)"}},
            "required": ["key"],
        },
    },
    {
        "name": "wb_screenshot",
        "description": "Take a screenshot of the pinned tab. Returns the file path on disk.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "wb_vision",
        "description": "Capture a screenshot + readable text dump in one call, for vision-model callers. Returns { screenshot_path, readable: {...} }. The bridge does NOT call any VLM — forward the screenshot to your model of choice.",
        "inputSchema": {
            "type": "object",
            "properties": {"prompt": {"type": "string", "description": "Optional prompt to tag the capture"}},
            "required": [],
        },
    },
    {
        "name": "wb_scroll",
        "description": "Scroll the pinned tab up or down. Humanized burst scrolling.",
        "inputSchema": {
            "type": "object",
            "properties": {"direction": {"type": "string", "enum": ["up", "down"]}},
            "required": ["direction"],
        },
    },
    {
        "name": "wb_back",
        "description": "Navigate the pinned tab back in history.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "wb_forward",
        "description": "Navigate the pinned tab forward in history.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "wb_refresh",
        "description": "Refresh the pinned tab.",
        "inputSchema": {
            "type": "object",
            "properties": {"hard": {"type": "boolean", "default": False, "description": "Hard refresh (bypass cache)"}},
            "required": [],
        },
    },
    {
        "name": "wb_eval",
        "description": "Evaluate JavaScript in the pinned tab. Bypasses page CSP (runs in CDP debug world). Use for extraction or page manipulation that the dedicated commands don't cover.",
        "inputSchema": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "JavaScript code to evaluate"}},
            "required": ["code"],
        },
    },
    {
        "name": "wb_title",
        "description": "Get the page title of the pinned tab.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "wb_url",
        "description": "Get the current URL of the pinned tab.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "wb_html",
        "description": "Get the full HTML of the pinned tab. Returns a large string.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "wb_snippet",
        "description": "Get a short visible-text snippet (first 2000 chars) of the pinned tab.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "wb_tabs",
        "description": "List all open browser tabs. Shows which tab is pinned (marked with *).",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "wb_console",
        "description": "Read console messages from the pinned tab. Useful for debugging SPA errors.",
        "inputSchema": {
            "type": "object",
            "properties": {"count": {"type": "integer", "default": 50}},
            "required": [],
        },
    },
    {
        "name": "wb_cookies_get",
        "description": "Get cookies for the pinned tab's current URL.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "wb_osscreenshot",
        "description": "OS-level screenshot of the WHOLE DESKTOP (not just the browser). Requires pyautogui installed on the server.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "wb_osclick",
        "description": "OS-level mouse click at (x, y) on the desktop. For native dialogs / minimized windows. Requires pyautogui.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "button": {"type": "string", "enum": ["click", "rightClick", "doubleClick"], "default": "click"},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "wb_ostype",
        "description": "OS-level type text at the desktop level. Requires pyautogui.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "interval": {"type": "number", "default": 0}},
            "required": ["text"],
        },
    },
    {
        "name": "wb_oshotkey",
        "description": "OS-level hotkey combo (e.g. ctrl+c, alt+tab). Requires pyautogui.",
        "inputSchema": {
            "type": "object",
            "properties": {"keys": {"type": "array", "items": {"type": "string"}}},
            "required": ["keys"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

def _call_tool(name: str, args: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """Dispatch an MCP tool call to the webbridge HTTP server."""
    server = cfg["server"]
    token = cfg.get("token")
    wait_ms = cfg.get("wait", 30000)

    if name == "wb_ping":
        return send_command(server, "ping", wait_ms=wait_ms, token=token)
    if name == "wb_navigate":
        return send_command(server, "navigate", {"url": args["url"]}, wait_ms=wait_ms, token=token)
    if name == "wb_readable":
        body = {
            "maxChars": args.get("max_chars", 20000),
            "includeA11y": args.get("include_a11y", True),
            "includeForms": args.get("include_forms", True),
            "includeConsole": args.get("include_console", False),
        }
        return send_command(server, "readable", body, wait_ms=wait_ms, token=token)
    if name == "wb_click":
        return send_command(server, "click", {"selector": args["selector"]}, wait_ms=wait_ms, token=token)
    if name == "wb_type":
        return send_command(server, "type", {"selector": args["selector"], "text": args["text"]}, wait_ms=wait_ms, token=token)
    if name == "wb_key":
        return send_command(server, "key", {"key": args["key"]}, wait_ms=wait_ms, token=token)
    if name == "wb_screenshot":
        return send_command(server, "screenshot", wait_ms=wait_ms, token=token)
    if name == "wb_vision":
        return send_command(server, "vision", {"prompt": args.get("prompt", "")}, wait_ms=wait_ms, token=token)
    if name == "wb_scroll":
        return send_command(server, "scroll", {"direction": args["direction"]}, wait_ms=wait_ms, token=token)
    if name == "wb_back":
        return send_command(server, "back", wait_ms=wait_ms, token=token)
    if name == "wb_forward":
        return send_command(server, "forward", wait_ms=wait_ms, token=token)
    if name == "wb_refresh":
        return send_command(server, "refresh", {"hard": args.get("hard", False)}, wait_ms=wait_ms, token=token)
    if name == "wb_eval":
        return send_command(server, "eval", {"code": args["code"]}, wait_ms=wait_ms, token=token)
    if name == "wb_title":
        return send_command(server, "title", wait_ms=wait_ms, token=token)
    if name == "wb_url":
        return send_command(server, "url", wait_ms=wait_ms, token=token)
    if name == "wb_html":
        return send_command(server, "html", wait_ms=wait_ms, token=token)
    if name == "wb_snippet":
        return send_command(server, "snippet", wait_ms=wait_ms, token=token)
    if name == "wb_tabs":
        return send_command(server, "tabs", wait_ms=wait_ms, token=token)
    if name == "wb_console":
        return send_command(server, "console", {"count": args.get("count", 50)}, wait_ms=wait_ms, token=token)
    if name == "wb_cookies_get":
        return send_command(server, "cookies", {"action": "get"}, wait_ms=wait_ms, token=token)
    if name == "wb_osscreenshot":
        return _os_command(server, "screenshot", timeout=15, token=token)
    if name == "wb_osclick":
        return _os_command(server, args.get("button", "click"), {"x": args["x"], "y": args["y"]}, token=token)
    if name == "wb_ostype":
        return _os_command(server, "typewrite", {"text": args["text"], "interval": args.get("interval", 0)}, token=token)
    if name == "wb_oshotkey":
        return _os_command(server, "hotkey", {"keys": args["keys"]}, token=token)

    return {"ok": False, "error": f"unknown tool: {name}"}


# ---------------------------------------------------------------------------
# MCP protocol (stdio JSON-RPC 2.0) — minimal subset for tools/list + tools/call
# ---------------------------------------------------------------------------

def _respond(msg: dict[str, Any]) -> None:
    """Write a JSON-RPC response to stdout (one object per line)."""
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _handle(req: dict[str, Any], cfg: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Handle a single JSON-RPC request. Returns a response or None (for notifications)."""
    method = req.get("method", "")
    req_id = req.get("id")
    params = req.get("params") or {}

    # Notifications (no id) don't get a response.
    if req_id is None and method:
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "webbridge-mcp", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            },
        }

    if method == "initialized" or method == "notifications/initialized":
        return None  # notification, no response

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments") or {}
        try:
            result = _call_tool(tool_name, tool_args, cfg)
            if result.get("ok"):
                value = result.get("value")
                # MCP expects content as a list of {type, text} blocks.
                if isinstance(value, str):
                    text = value
                elif isinstance(value, dict) and "textBlock" in value:
                    # readable command — return the text block (best for LLMs)
                    text = value["textBlock"]
                else:
                    text = json.dumps(value, indent=2, default=str)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"content": [{"type": "text", "text": text}]},
                }
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": result.get("error", "unknown error")}],
                    },
                }
        except BridgeError as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": f"BridgeError: {e}"}],
                },
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": f"internal error: {e}"},
            }

    # Unknown method
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def main() -> int:
    """Read JSON-RPC requests from stdin, one per line, until EOF."""
    cfg = _load_config()
    # MCP servers get longer default wait — agents tend to do slower things.
    cfg["wait"] = int(os.environ.get("WEBBRIDGE_WAIT", cfg.get("wait", 30000)))

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            _respond({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"parse error: {e}"}})
            continue
        resp = _handle(req, cfg)
        if resp is not None:
            _respond(resp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
