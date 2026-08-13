# Agentic Web Bridge for Browser Automation

A **local-first** browser automation bridge. Any AI agent (CLI, Python, LLM tool) drives your real Chrome / Edge via the Chrome DevTools Protocol — **pinned to ONE tab** for safety.

**Agent-agnostic. 100% local. Your logins stay yours.** No cloud relay, no third-party tokens, no SaaS lock-in. You drive your own browser, with your own sessions, on your own machine.

```
your agent  ──HTTP──▶  local Python server  ──CDP──▶  browser extension  ──▶  ONE pinned tab
   (CLI / Python / any HTTP tool)   (127.0.0.1:9876)         (your Chrome / Edge)
```

> ## ⚠️ Disclaimer
>
> This software gives the operator full browser-automation capability on the **pinned tab**, including on tabs where you are already authenticated. **You are responsible for everything done through the bridge.**
>
> - Only run this on machines you control and trust.
> - The server binds to `127.0.0.1` only — it is **not** reachable from your LAN or the internet.
> - Any AI agent, script, or human that can talk to `http://127.0.0.1:9876` on your box can drive **the pinned tab**. Pin a tab you're willing to automate; don't pin your banking tab.
> - **Respect the terms of service of every site you automate.** Don't use this to scrape content behind a login wall, bypass paywalls, evade rate limits, or do anything that violates the target site's ToS or applicable law.
> - The maintainers provide this code **as-is, with no warranty**, under the MIT license. You bear all responsibility for how you use it.

---

## What's new in v4

This is a **breaking** revamp. The biggest change: the bridge now operates on **exactly one pinned tab**, chosen by you in the extension popup. Previous versions silently followed your active tab (or worse, accepted any `tabId` from the server) — both of those paths are now closed.

| Change | Why |
|---|---|
| **Single-tab pinning** (security) | The agent can only ever drive the tab you explicitly pin. Switching to Gmail no longer hands Gmail to the agent. |
| **`readable` command** (text AIs) | A single command returns an LLM-optimized text dump of the page — visible text, headings, interactive elements with selectors, and the accessibility tree. Designed for **text-only LLMs** that can't see screenshots. |
| **`vision` command** (VLMs) | Returns both a screenshot (file on disk) AND a `readable` companion, so a vision-model caller gets image + text context in one round-trip. The bridge does NOT call any VLM — you pick the model and forward the bytes. |
| **`/os` endpoint + pyautogui hybrid** | Optional `pip install webbridge[os]` enables OS-level mouse/keyboard via `pyautogui`. Use it when CDP can't reach — minimized windows, native file pickers, OS-level hotkeys. |
| **4 broken CLI commands fixed** | `tabs`, `active-tab`, `log` now send the correct wire types. `tabs-focus` was removed (no longer needed — the bridge is pinned). |
| **Single source of truth for version** | One `_version.py` file; the `/health` and `/version` endpoints, `__version__`, and `pyproject.toml` all read from it. |
| **Persistent extension ID** | The extension's session ID survives service-worker restarts (MV3 kills the SW every ~30s idle). |
| **132 tests** (was 114, 6 were mislabeled integration tests) | All tests pass; new tests cover the v4 commands, the `/os` allowlist, the `/version` endpoint, and the new output-formatting branches. |

### v4.1 additions

| Change | Why |
|---|---|
| **Real long-poll on `/poll`** | The extension's `/poll` now blocks up to 25s waiting for a command (was busy-polling every 800ms). Cuts extension HTTP traffic by ~90%. The infrastructure was always there (`threading.Condition`); now it's wired up. |
| **Auth token (`WEBBRIDGE_TOKEN`)** | Set the env var on the server → every non-public endpoint requires a Bearer token (or `?token=` query param for the extension). The popup has a token field that appears automatically when auth is enabled. Defaults to off for backwards-compat. Uses `secrets.compare_digest` (constant-time). |
| **MCP server (`webbridge-mcp`)** | Exposes the bridge as a Model Context Protocol server (24 tools) so Claude Desktop / Cursor / Cline / VS Code can drive the browser as a set of tools. Stdlib-only JSON-RPC over stdio — no `mcp` package required. |
| **CI workflow** | `.github/workflows/test.yml` runs the 144 tests on Python 3.9–3.13 across Ubuntu, Windows, and macOS on every push/PR. |
| **`content.js` deleted** | The content script was dead code (no service-worker listener picked up its messages). Removed from the manifest; saves a tiny bit of CPU on every page load. |
| **Cross-platform paths** | No more hardcoded `/tmp/` in tests (uses `tempfile.mkdtemp`). Default data dir is now per-OS (`~/.local/share/webbridge` on Linux, `~/Library/Application Support/webbridge` on macOS, `%LOCALAPPDATA%\webbridge` on Windows). `file://` URLs work on Windows (`file:///C:/...`). |
| **144 tests** (was 132) | New tests cover auth (enabled/disabled, Bearer header, query param, wrong token), long-poll blocking, cross-platform path helpers. |


---

## What it does

Your AI agent can:

| Command | Description |
|---|---|
| `navigate` | Open pages with real `Page.navigate` calls |
| `eval` | Run arbitrary JS in the page — bypasses page CSP (debug world) |
| `click` | Click any element with a humanized mouse trajectory |
| `hover` | Hover over elements with human-like mouse trajectory |
| `drag` | Drag from one element to another with mouse movement |
| `type` | Type into any input with per-character keyboard events |
| `key` | Press keys (Enter, Tab, Escape, arrows, etc.) |
| `select` | Select options in `<select>` dropdowns |
| `screenshot` | Take a screenshot of the pinned tab's rendered viewport |
| `readable` | **NEW.** LLM-optimized text dump — visible text, headings, interactive elements with selectors, a11y tree |
| `vision` | **NEW.** Screenshot + `readable` companion in one call, for VLM callers |
| `upload` | Upload files to `<input type="file">` elements |
| `cookies` | Get, set, and delete browser cookies |
| `console` | Read browser console messages |
| `scroll` | Human-like burst scrolling |
| `back` / `forward` | Navigate browser history |
| `refresh` | Reload the page (with optional hard refresh) |
| `trace` | Capture screenshot + accessibility tree + console log |
| `tabs` | List all open tabs (shows which is pinned) |
| `html` / `url` / `title` / `snippet` | Read page state |
| `query` | Query elements by selector |
| `axtree` / `axquery` | Accessibility tree queries |
| `see` / `expect` | Visual assertion helpers |
| **OS-level** (`osclick`, `ostype`, `osscreenshot`, `osmove`, `ospress`, `oshotkey`, `ossize`, `osposition`) | **NEW.** OS-level input via `pyautogui` — for hybrid automation when CDP can't reach |

All while using **your existing login sessions** on the pinned tab — internal tools, dashboards, anything that's already authenticated in your browser.

---

## Architecture

Three pieces:

1. **`webbridge/server.py`** — stdlib-only HTTP server on `127.0.0.1:9876`. Queues commands, returns results, logs everything. Optional `/os` endpoint dispatches to `pyautogui` for OS-level input. Configurable via env vars.
2. **`extension/`** — Chrome / Edge Manifest V3 extension. The background service worker uses `chrome.debugger` (CDP) to drive the **pinned tab**. Anti-detect human-like mouse movement, typing, and scrolling. `chrome.storage.local` persists the pinned-tab ID and the extension's session ID.
3. **`webbridge/client.py`** — CLI with 30+ subcommands, config file support, and pretty output.

```
┌────────────┐    HTTP     ┌──────────────────┐    CDP     ┌────────────┐
│  agent     │ ─────────▶ │ server.py        │ ────────▶ │  Chrome    │
│  (CLI,     │ ◀───────── │ 127.0.0.1:9876   │ ◀──────── │  (pinned   │
│  Python,   │            │                  │            │   tab only)│
│  any HTTP) │            │ + /os → pyautogui│            │            │
└────────────┘            └──────────────────┘            └────────────┘
                                  │
                                  ▼ (optional)
                          ┌──────────────────┐
                          │ OS-level input   │
                          │ (mouse, keyboard,│
                          │  screenshots)    │
                          └──────────────────┘
```

### Why CDP, not `chrome.scripting.executeScript`?

`chrome.scripting.executeScript` runs in the page's main world, subject to CSP. Many modern sites reject `unsafe-eval`, breaking JS injection.

`chrome.debugger` + `Runtime.evaluate` runs in the browser's **debug world**, outside the page's CSP. Same mechanism Chrome DevTools uses. Trade-off: the user sees a yellow "WebBridge is debugging this tab" infobar on the pinned tab.

---

## Install

### Option 1: pip (recommended)

```bash
pip install .
# or, to enable OS-level input (pyautogui):
pip install ".[os]"
# editable/dev mode:
pip install -e ".[dev]"
```

This installs the `webbridge` command and the `webbridge` Python package.

### Option 2: standalone (no install)

Just run `server.py` and `client.py` directly — they work without installation (they re-export from the `webbridge/` package).

### 1. Start the server

```bash
# from the repo root — uses --port flag (NOT a positional port arg)
python server.py --port 9876
# or with no args (defaults to 127.0.0.1:9876):
python server.py
# or via the installed command:
webbridge-server --port 9876
# or via module:
python -m webbridge --port 9876
# Windows convenience:
start.bat
# Unix convenience:
./start.sh
```

### 2. Load the extension

1. Open `chrome://extensions` (or `edge://extensions`)
2. Toggle **Developer mode** (top right)
3. Click **Load unpacked** and pick the `extension/` folder
4. The extension's icon appears in the toolbar

### 3. Pin a tab (NEW — required)

1. Open the tab you want the agent to drive (and log in to whatever site you need)
2. Click the WebBridge extension icon
3. Click **"Pin this tab"**
4. The popup shows the pinned tab's ID, URL, and how long ago you pinned it

Until you pin a tab, every command returns: `no pinned tab — open the WebBridge popup and click "Pin this tab"`.

To switch tabs, click **"Unpin"**, switch to the new tab, click **"Pin this tab"** again.

### 4. Verify

```bash
python client.py ping
# or
webbridge ping
```

The response now includes `pinnedTabId` so you can confirm the pin took.

---

## CLI Reference

### Global options

```
--server URL    Server URL (default: http://127.0.0.1:9876, or $WEBBRIDGE_URL)
--tab TAB_ID    (deprecated — the bridge now operates on the pinned tab only)
--wait MS       Result wait timeout (default: 15000)
--json          Output raw JSON
--quiet         Suppress info messages
--config PATH   Config file (default: ~/.webbridge or .webbridge)
```

### Commands

```bash
# Navigation
webbridge navigate https://example.com
webbridge back
webbridge forward
webbridge refresh          # soft refresh
webbridge refresh --hard   # hard refresh (clear cache)

# Reading page state
webbridge title
webbridge url
webbridge html > page.html
webbridge snippet          # visible text (first 2000 chars)

# LLM-friendly page dump (NEW — for text-only AIs)
webbridge readable                       # full structured dump (JSON)
webbridge readable --max-chars 5000      # cap text field size
webbridge readable --no-a11y             # skip accessibility tree
webbridge readable --no-forms            # skip interactive-elements list
webbridge readable --console             # include last 30 console messages
# In pretty mode (no --json), `readable` prints the textBlock as plain text —
# pipe it straight into a file or an LLM prompt:
webbridge readable > page.txt

# Vision-model capture (NEW — for VLMs like GPT-4V, Claude, GLM-4V)
webbridge vision "describe this page"
# Returns { screenshot_path, screenshot_size, readable: {...} }
# The bridge does NOT call any VLM. Forward the screenshot file to your
# model of choice and use the `readable` text as context.

# Interaction
webbridge click "button.submit"
webbridge hover ".dropdown-menu"
webbridge drag "#source" "#target"
webbridge type "#q" "search query"
webbridge key Enter
webbridge select "select#country" "KE"

# File upload
webbridge upload "input[type=file]" /path/to/file.png

# Cookies
webbridge cookies get                     # all cookies
webbridge cookies get --name session_id   # specific cookie (NYI — use cookies get)
webbridge cookies set --name foo --value bar --domain .example.com
webbridge cookies delete --name foo

# Screenshots & traces
webbridge screenshot        # saved to webbridge/screenshots/
webbridge trace             # screenshot + a11y tree + console log

# JavaScript
webbridge eval "document.querySelectorAll('a').length"

# Console
webbridge log               # last 50 console messages
webbridge log --count 10

# Tabs
webbridge tabs              # list all open tabs (marks the pinned one with *)

# Scrolling
webbridge scroll down
webbridge scroll up

# OS-level input (NEW — requires `pip install webbridge[os]`)
webbridge osclick 100 200                       # left-click at (100,200)
webbridge osclick 100 200 --button rightClick   # right-click
webbridge ostype "hello world"                  # type at OS level
webbridge ostype "hello" --interval 0.05        # slow typing
webbridge osmove 500 300 --duration 0.5         # animated mouse move
webbridge ospress enter                         # press a single key
webbridge oshotkey ctrl c                       # hotkey combo
webbridge osscreenshot                          # full-desktop screenshot
webbridge ossize                                # screen dimensions
webbridge osposition                            # current mouse position
```

### Python API

```python
from webbridge import __version__
print(__version__)  # "4.0.0"

from webbridge.client import send_command

# All commands go through the pinned tab — no tabId argument needed.
result = send_command("http://127.0.0.1:9876", "eval", {"code": "document.title"})
print(result)

# The readable command returns a dict with everything a text LLM needs:
page = send_command("http://127.0.0.1:9876", "readable", {"maxChars": 10000})
print(page["value"]["textBlock"])  # ready-to-paste text

# OS-level (requires pyautogui installed)
from webbridge.client import _os_command
result = _os_command("http://127.0.0.1:9876", "click", {"x": 100, "y": 200})
```

### Config file

Create `~/.webbridge` or `.webbridge` in your project:

```json
{
  "server": "http://127.0.0.1:9876",
  "wait": 15000
}
```

Override with `--config path/to/config.json`, `$WEBBRIDGE_URL`, `$WEBBRIDGE_WAIT`, or any CLI flag. Precedence (lowest → highest): defaults → config file → env vars → CLI flags.

### Raw HTTP API

```bash
# Queue a command (the extension picks it up via /poll)
curl -X POST http://127.0.0.1:9876/cmd \
  -H "Content-Type: application/json" \
  -d '{"id":"r1","type":"readable","args":{"maxChars":5000}}'

# Get the result (blocking)
curl "http://127.0.0.1:9876/result?id=r1&wait=5000"

# List available commands
curl http://127.0.0.1:9876/commands

# Health check (includes pyautogui availability)
curl http://127.0.0.1:9876/health

# Version (single source of truth)
curl http://127.0.0.1:9876/version

# OS-level input (requires pyautogui)
curl -X POST http://127.0.0.1:9876/os \
  -H "Content-Type: application/json" \
  -d '{"action":"click","args":{"x":100,"y":200}}'

# Graceful shutdown
curl -X POST http://127.0.0.1:9876/shutdown
```

---

## Using the bridge with MCP clients (Claude Desktop, Cursor, Cline, VS Code)

The bridge ships with a **Model Context Protocol server** (`webbridge-mcp`) that exposes 24 tools — any MCP-compatible client can drive the pinned browser tab as a set of tools, no HTTP wrangling required.

### Setup

1. Install the package: `pip install -e .` (gives you the `webbridge-mcp` command)
2. Add the server to your MCP client's config. For **Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "webbridge": {
      "command": "webbridge-mcp",
      "env": {
        "WEBBRIDGE_URL": "http://127.0.0.1:9876",
        "WEBBRIDGE_TOKEN": "your-secret-token-here"
      }
    }
  }
}
```

For **Cursor** / **Cline** / **VS Code MCP**, the config shape is the same — see your client's docs for where to put it.

3. Restart Claude Desktop. You'll see `webbridge` listed under available MCP servers with 24 tools (`wb_ping`, `wb_navigate`, `wb_readable`, `wb_click`, `wb_type`, `wb_screenshot`, `wb_vision`, `wb_eval`, `wb_osscreenshot`, etc.).

### How it works

- The MCP server is a **stdio JSON-RPC 2.0** process. The client spawns it, talks to it over stdin/stdout.
- The MCP server talks **HTTP** to the webbridge server (`127.0.0.1:9876` by default).
- `wb_readable` returns its `textBlock` field as the MCP tool result text — perfect for LLM consumption (the model sees the page as text, not JSON).
- `wb_vision` returns the readable text companion (the screenshot stays on disk; you'd need a VLM-aware client to forward it — most MCP clients don't yet support image content blocks).
- The MCP server is **stdlib-only** (no `mcp` package required). It implements the subset of the spec needed for `initialize`, `tools/list`, and `tools/call`.

### Tool list

| Tool | What it does |
|---|---|
| `wb_ping` | Health check — returns pong + pinned tab ID |
| `wb_navigate` | Navigate the pinned tab to a URL |
| `wb_readable` | LLM-optimized text dump (the workhorse for text AIs) |
| `wb_click` / `wb_type` / `wb_key` | Interact with elements by CSS selector |
| `wb_screenshot` | Screenshot → file path |
| `wb_vision` | Screenshot + readable companion |
| `wb_scroll` / `wb_back` / `wb_forward` / `wb_refresh` | Navigation |
| `wb_eval` | Arbitrary JS (bypasses CSP) |
| `wb_title` / `wb_url` / `wb_html` / `wb_snippet` | Read page state |
| `wb_tabs` / `wb_console` / `wb_cookies_get` | Inspect browser |
| `wb_osscreenshot` / `wb_osclick` / `wb_ostype` / `wb_oshotkey` | OS-level input (requires `pyautogui`) |

---

## Using the bridge with LLMs

The bridge is **agent-agnostic** — it speaks plain HTTP. Here's how to use it with different kinds of AI:

### Text-only LLMs (GPT-3.5, GPT-4, Claude without vision, Llama, Mistral, etc.)

Use `readable`. It returns a deterministic text dump of the page that includes:

- URL, title, description, viewport
- Visible text (collapsed whitespace, capped at `maxChars`)
- Headings (H1–H3, up to 50)
- Interactive elements (up to 200) with stable CSS selectors — the agent can pass these directly to `click` / `type`
- Accessibility tree (semantic structure, handles shadow DOM and ARIA-only labels)
- A ready-to-paste `textBlock` field that combines all of the above into one string

```bash
# Pipe straight into an LLM prompt:
webbridge readable > /tmp/page.txt
llm "Given this page state, what should I click to log in? Respond with a CSS selector." < /tmp/page.txt
```

### Vision LLMs (GPT-4V, Claude 3 with vision, GLM-4V, Gemini Vision, etc.)

Use `vision`. It returns both a screenshot (saved on disk) AND a `readable` companion. Forward the screenshot bytes to your VLM and use the text as grounding context.

```python
import base64, json, urllib.request

# 1. Capture
req = urllib.request.Request(
    "http://127.0.0.1:9876/cmd",
    data=json.dumps({"id": "v1", "type": "vision", "args": {"prompt": "find the login button"}}).encode(),
    headers={"Content-Type": "application/json"},
)
urllib.request.urlopen(req)
r = urllib.request.urlopen("http://127.0.0.1:9876/result?id=v1&wait=15000")
result = json.loads(r.read())["result"]["value"]

# 2. Read the screenshot bytes
with open(result["screenshot_path"], "rb") as f:
    img_b64 = base64.b64encode(f.read()).decode()

# 3. Forward to your VLM of choice (pseudo-code)
vlm_response = your_vlm_client.chat(
    model="gpt-4-vision-preview",
    messages=[{
        "role": "user",
        "content": [
            {"type": "text", "text": result["readable"]["textBlock"]},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
        ],
    }],
)
```

### Hybrid text + vision

The most effective pattern for hard pages (canvas, image-heavy, custom UI): pass **both** the `readable` text AND the screenshot to the VLM. The text gives the model the page structure and selectors; the image gives it visual grounding for things text can't capture (color, layout, icons).

---

## Server Configuration

| Env Variable | Default | Description |
|---|---|---|
| `WEBBRIDGE_HOST` | `127.0.0.1` | Bind address |
| `WEBBRIDGE_PORT` | `9876` | Bind port |
| `WEBBRIDGE_DATA_DIR` | per-OS (see below) | Screenshot/trace storage |
| `WEBBRIDGE_LOG_FILE` | (none) | Log to file |
| `WEBBRIDGE_LOG_LEVEL` | `INFO` | Log level (DEBUG/INFO/WARNING/ERROR/CRITICAL) |
| `WEBBRIDGE_TOKEN` | (none) | **v4.1.** If set, all non-public endpoints require a Bearer token (or `?token=` query param). Defaults to off for backwards-compat. |
| `WEBBRIDGE_URL` | (none) | Client default server URL |
| `WEBBRIDGE_WAIT` | (none) | Client default wait timeout (ms) |
| `WEBBRIDGE_TAB` | (none) | (deprecated — pinned tab is the only target) |

**Default data dir** (cross-platform, no hardcoded paths):

| OS | Path |
|---|---|
| Linux | `~/.local/share/webbridge` (or `$XDG_DATA_HOME/webbridge`) |
| macOS | `~/Library/Application Support/webbridge` |
| Windows | `%LOCALAPPDATA%\webbridge` |

Override with `WEBBRIDGE_DATA_DIR` if you want a custom location.

CLI flags override env vars:

```bash
python server.py --host 127.0.0.1 --port 9876 --log-level DEBUG --log-file bridge.log
```

---

## Anti-Detect Features

The extension includes human-like behavior patterns:

- **Mouse movement** — ease-out trajectory with jitter (not instant jumps). 6–18 steps per move, 6–18ms between moves.
- **Click timing** — hover before click with 50–200ms dwell, 20–60ms press→release gap.
- **Typing** — per-character keyboard events with 45–180ms variable delay, "thinking" pauses every ~12 chars (300–700ms).
- **Scrolling** — burst scroll events (3–6 small wheel events per burst), 20–60ms between wheels, 180–600ms pause between bursts.
- **Actionability checks** — verifies element is visible, in viewport, and not occluded before clicking.
- **Per-tab state** — last mouse position is remembered per tab, so the next move has a real starting coordinate.

These can be disabled per-command (`humanize: false` in args) or tuned in `background.js`.

---

## Security Model

- **The server binds to `127.0.0.1` only.** Not reachable from your network.
- **No authentication** — gated by network reachability only. (If you want auth, contribute a PR — see the issues.)
- **Only the pinned tab can be driven.** Every command in the extension passes through `resolveTargetTabId()`, which:
  - Reads `designatedTabId` from `chrome.storage.local`
  - Rejects commands if no tab is pinned (clear error: "open the WebBridge popup...")
  - Rejects commands that specify a different `tabId` (clear error: "command targets tab X but only the pinned tab Y can be driven")
  - Clears the pin automatically if the pinned tab is closed
- **The extension shows a yellow "being debugged" infobar** on the pinned tab — you always know when the bridge is attached.
- **`chrome.debugger` runs outside the page's CSP** (debug world, not main world) — that's the whole reason CDP is used instead of `chrome.scripting.executeScript`.
- **Don't run on shared / multi-user hosts.**
- **Don't expose port 9876 to LAN or internet.**
- **Don't pin your banking tab.** Pin a dedicated tab for automation.

---

## Testing

```bash
# Run all unit tests (144 tests across server + client)
python -m pytest tests/ -v
# or with unittest:
python -m unittest discover tests -v

# Integration tests require a running server + browser + pinned tab
python -m unittest tests.test_integration -v
```

144 unit tests cover the server Bridge class, HTTP handler (including `/os`, `/version`, auth, and long-poll), client CLI, argument parsing, cross-platform path helpers, and the v4 command types. Integration tests auto-detect server availability and skip gracefully (note: they check the server but NOT whether a tab is pinned — if the server is up but no tab is pinned, integration tests will run and fail with the "no pinned tab" error, which is itself a useful signal).

CI runs the 144 unit tests on every push/PR, across Python 3.9–3.13 on Ubuntu, Windows, and macOS. See `.github/workflows/test.yml`.

---

## Project Structure

```
├── server.py              # Standalone shim — re-exports from webbridge.server
├── client.py              # Standalone shim — re-exports from webbridge.client
├── extension/
│   ├── manifest.json      # MV3 manifest (v2.1.0 — no content_scripts entry)
│   ├── background.js      # Service worker — CDP driver, pinned-tab guard, readable + vision handlers, auth + long-poll
│   ├── popup.html         # Extension popup UI (Pin button + token field)
│   └── popup.js           # Popup logic (pin/unpin, token save, server health, ping)
├── webbridge/             # Installable Python package
│   ├── __init__.py        # Re-exports __version__
│   ├── _version.py        # Single source of truth for version (4.1.0)
│   ├── __main__.py        # python -m webbridge entry
│   ├── server.py          # Server module (Bridge, Handler, /os, /version, auth, long-poll, cross-platform paths)
│   ├── client.py          # Client module (30+ subcommands, OS-level commands, Bearer auth)
│   └── mcp.py             # MCP server (24 tools, stdio JSON-RPC, stdlib-only)
├── tests/
│   ├── test_server.py     # 75+ server tests (incl. auth, long-poll, cross-platform paths)
│   ├── test_client.py     # 70+ client tests (incl. v4 commands, OS-level commands)
│   ├── test_integration.py # 6 integration tests (require browser)
│   └── conftest.py        # Test fixtures
├── .github/workflows/
│   └── test.yml           # CI: Python 3.9–3.13 × Ubuntu/Windows/macOS
├── examples/
│   ├── find_bounties.py   # GitHub bounty finder
│   └── leadgen.py         # Contact extractor (note: example uses hardcoded URLs, not Google search)
├── pyproject.toml         # Package config (v4.1.0, optional [os] and [dev] extras, webbridge-mcp entry point)
├── start.bat              # Windows launcher (uses --port flag)
├── start.sh               # Unix launcher (uses --port flag)
├── LICENSE                # MIT
├── SECURITY.md            # Security policy
└── README.md              # This file
```

---

## Known limitations (honest list)

- **Auth is opt-in, not on by default.** `WEBBRIDGE_TOKEN` must be set explicitly. If you forget, anyone who can reach `127.0.0.1:9876` can drive the pinned tab. The startup banner now prints whether auth is enabled, so it's hard to miss — but it's still your responsibility to set it on shared hosts.
- **No request body size limit.** A 1GB POST to `/trace` is loaded into memory in full.
- **`reload` command** (in the extension) calls `chrome.runtime.reload()`, which resets the extension state. Useful for development; mildly dangerous in production.
- **Screenshots are base64-encoded JSON POSTs** — hits CDP message-size limits on some very large pages. No streaming yet.
- **`/os` is synchronous** — long OS-level operations block the HTTP handler. Fine for clicks/typing; not fine for a 30-second `typewrite` of a 10000-char string.
- **OS-level commands bypass the pinned-tab guard** (they don't touch the browser at all — they're for the desktop). This is by design (you might want to click a native dialog that's blocking the browser), but it means the `/os` endpoint can drive anything on your desktop. Be careful.
- **MCP server doesn't support image content blocks.** `wb_vision` returns the readable text companion as the tool result, but the screenshot stays on disk. Most MCP clients (Claude Desktop as of this writing) don't yet support image content blocks in tool results, so the bridge can't push the screenshot through the MCP layer. To use vision, call `wb_screenshot` to get the file path, then forward the bytes to your VLM separately.
- **MCP server implements a minimal subset of the spec.** It supports `initialize`, `notifications/initialized`, `tools/list`, and `tools/call`. It does NOT support `resources/*`, `prompts/*`, or `sampling/*`. If you need those, swap `webbridge/mcp.py` for a thin wrapper around the official `mcp` package.
- **CI workflow runs unit tests only.** Integration tests (which require a real browser + extension + pinned tab) are not run in CI — they're for local/manual verification only.
- **`/poll` long-poll is capped at 25 seconds** (`POLL_TIMEOUT_MS` in `server.py`). If you want longer, change the constant — but be aware that some network stacks / proxies will close idle connections before 25s.

---

## License

MIT — see [LICENSE](./LICENSE).
