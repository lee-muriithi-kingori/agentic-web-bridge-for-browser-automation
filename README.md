# Agentic Web Bridge for Browser Automation

A local-first browser automation bridge. AI agents drive your real Chrome / Edge via the Chrome DevTools Protocol.

**Built by Vex** — your AI build collaborator. Agent-agnostic, 100% local, your logins stay yours. No cloud relay, no third-party tokens, no SaaS lock-in. You drive your own browser, with your own sessions, on your own machine.

```
your agent  ──HTTP──▶  local Python server  ◀──CDP──  browser extension
   (CLI / Python / any tool)   (127.0.0.1:9876)         (your Chrome / Edge)
```

> ## ⚠️ Disclaimer
>
> This software gives the operator full browser-automation capability, including on tabs where the user is already authenticated. **You are responsible for everything done through the bridge.**
>
> - Only run this on machines you control and trust.
> - The bridge binds to `127.0.0.1` only — it is **not** reachable from your LAN or the internet.
> - Any AI agent, script, or human that can talk to `http://127.0.0.1:9876` on your box can drive any tab you have the extension attached to. Don't expose that port.
> - **Respect the terms of service of every site you automate.** Don't use this to scrape content behind a login wall, bypass paywalls, evade rate limits, or do anything that violates the target site's ToS or applicable law.
> - The maintainers provide this code **as-is, with no warranty**, under the MIT license. You bear all responsibility for how you use it.

## What it does

Your AI agent can:

| Command | Description |
|---------|-------------|
| `navigate` | Open pages with real `Page.navigate` calls |
| `eval` | Run arbitrary JS in the page — bypasses page CSP (debug world) |
| `click` | Click any element with a real mouse event at its center |
| `hover` | Hover over elements with human-like mouse trajectory |
| `drag` | Drag from one element to another with mouse movement |
| `type` | Type into any input with real keyboard input events |
| `key` | Press keys (Enter, Tab, Escape, arrows, etc.) |
| `select` | Select options in `<select>` dropdowns |
| `screenshot` | Take screenshots of the actual rendered viewport |
| `upload` | Upload files to `<input type="file">` elements |
| `cookies` | Get, set, and delete browser cookies |
| `console` | Read browser console messages |
| `scroll` | Human-like burst scrolling |
| `back` / `forward` | Navigate browser history |
| `refresh` | Reload the page (with optional hard refresh) |
| `trace` | Capture screenshot + accessibility tree + console log |
| `tabs` | List, focus, and switch between tabs |
| `html` / `url` / `title` / `snippet` | Read page state |
| `query` | Query elements by selector |

All while using **your existing login sessions** — bank dashboards, internal tools, anything that's already authenticated in your browser.

## Architecture

Three pieces:

1. **`server.py`** — stdlib-only HTTP server on `127.0.0.1:9876`. Queues commands, returns results, logs everything. Configurable via env vars.
2. **`extension/`** — Chrome / Edge Manifest V3 extension. The background service worker uses `chrome.debugger` to attach to tabs and send CDP commands. Anti-detect human-like mouse movement, typing, and scrolling.
3. **`client.py`** — CLI with 24+ subcommands, config file support, and pretty output.

```
┌────────────┐    HTTP     ┌──────────────────┐    CDP     ┌────────────┐
│  agent     │ ─────────▶ │ server.py        │ ────────▶ │  Chrome    │
│  (CLI,     │ ◀───────── │ 127.0.0.1:9876   │ ◀──────── │  (with the │
│  Python)   │            │                  │            │ extension) │
└────────────┘            └──────────────────┘            └────────────┘
```

## Install

### Option 1: pip (recommended)

```bash
pip install .
# or
pip install -e .   # editable/development mode
```

This installs the `webbridge` command and the `webbridge` Python package.

### Option 2: standalone (no install)

Just run `server.py` and `client.py` directly — they work without installation.

### 1. Start the server

```bash
# from the repo root
python server.py 9876

# or via the installed command
webbridge-server 9876

# or via module
python -m webbridge 9876

# Windows convenience
start.bat

# Unix convenience
./start.sh
```

### 2. Load the extension

1. Open `chrome://extensions` (or `edge://extensions`)
2. Toggle **Developer mode** (top right)
3. Click **Load unpacked** and pick the `extension/` folder
4. The extension's icon appears in the toolbar

### 3. Verify

```bash
python client.py ping
# or
webbridge ping
```

The popup icon should also turn green (it shows the last-reported page state from the extension).

## CLI Reference

### Global options

```
--server URL    Server URL (default: http://127.0.0.1:9876, or $WEBBRIDGE_URL)
--tab TAB_ID    Target a specific tab
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
webbridge snippet          # visible text (first 800 chars)

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
webbridge cookies get --name session_id   # specific cookie
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
webbridge tabs              # list all open tabs
webbridge tabs-focus <id>   # focus a specific tab

# Scrolling
webbridge scroll down
webbridge scroll up
```

### Python API

```python
from webbridge import __version__
print(__version__)  # "3.2.0"

# Or use the client directly
from webbridge.client import send_command

result = send_command("http://127.0.0.1:9876", "eval", {"code": "document.title"})
print(result)
```

### Config file

Create `~/.webbridge` or `.webbridge` in your project:

```json
{
  "server": "http://127.0.0.1:9876",
  "tab": null,
  "wait": 15000
}
```

Override with `--config path/to/config.json` or `$WEBBRIDGE_URL`.

### Raw HTTP API

```bash
# Queue a command
curl -X POST http://127.0.0.1:9876/cmd \
  -H "Content-Type: application/json" \
  -d '{"id":"r1","type":"eval","args":{"code":"document.title"}}'

# Get the result (blocking)
curl "http://127.0.0.1:9876/result?id=r1&wait=5000"

# List available commands
curl http://127.0.0.1:9876/commands

# Health check
curl http://127.0.0.1:9876/health

# Graceful shutdown
curl -X POST http://127.0.0.1:9876/shutdown
```

## Server Configuration

| Env Variable | Default | Description |
|-------------|---------|-------------|
| `WEBBRIDGE_HOST` | `127.0.0.1` | Bind address |
| `WEBBRIDGE_PORT` | `9876` | Bind port |
| `WEBBRIDGE_DATA_DIR` | `webbridge/` | Screenshot/trace storage |
| `WEBBRIDGE_LOG_FILE` | (none) | Log to file |
| `WEBBRIDGE_URL` | (none) | Client default server URL |

CLI flags override env vars:

```bash
python server.py --host 127.0.0.1 --port 9876 --log-level DEBUG --log-file bridge.log
```

## Anti-Detect Features

The extension includes human-like behavior patterns:

- **Mouse movement** — ease-out trajectory with jitter (not instant jumps)
- **Click timing** — hover before click with random delay
- **Typing** — per-character keyboard events with variable WPM and "thinking" pauses
- **Scrolling** — burst scroll events (3-6 small wheel events per burst)
- **Actionability checks** — verifies element is visible, in viewport, and not occluded before clicking

These can be disabled per-command or tuned in `background.js`.

## Testing

```bash
# Run all unit tests
python -m unittest discover tests -v

# Or with pytest (if installed)
python -m pytest tests/ -v

# Integration tests require a running server + browser
python -m unittest tests.test_integration -v
```

114 unit tests cover the server Bridge class, HTTP handler, client CLI, and argument parsing. Integration tests auto-detect server availability and skip gracefully.

## Security

- The server binds to `127.0.0.1` only. Not reachable from your network.
- No authentication — gated by network reachability only.
- The extension shows a yellow "being debugged" bar on attached tabs.
- `chrome.debugger` runs outside the page's CSP (debug world, not main world).
- Don't run on shared / multi-user hosts.
- Don't expose port 9876 to LAN or internet.

## Why CDP, not `chrome.scripting.executeScript`?

`chrome.scripting.executeScript` runs in the page's main world, subject to CSP. Many modern sites reject `unsafe-eval`, breaking JS injection.

`chrome.debugger` + `Runtime.evaluate` runs in the browser's debug world, **outside the page's CSP**. Same mechanism Chrome DevTools uses. Trade-off: the user sees a yellow debug bar on attached tabs.

## Project Structure

```
├── server.py              # Standalone server (505 lines)
├── client.py              # Standalone CLI (497 lines)
├── extension/
│   ├── manifest.json      # MV3 manifest
│   ├── background.js      # Service worker — CDP driver (1288 lines)
│   ├── content.js         # Content script (legacy/fallback)
│   ├── popup.html         # Extension popup UI
│   └── popup.js           # Popup logic
├── webbridge/             # Installable Python package
│   ├── __init__.py        # Version
│   ├── __main__.py        # python -m webbridge entry
│   ├── server.py          # Server module
│   └── client.py          # Client module
├── tests/
│   ├── test_server.py     # 64 server tests
│   ├── test_client.py     # 44 client tests
│   ├── test_integration.py # 6 integration tests
│   └── conftest.py        # Test fixtures
├── examples/
│   ├── find_bounties.py   # GitHub bounty finder
│   └── leadgen.py         # Facebook lead extractor
├── pyproject.toml         # Package config
├── start.bat              # Windows launcher
├── start.sh               # Unix launcher
├── LICENSE                # MIT
├── SECURITY.md            # Security policy
└── README.md              # This file
```

## License

MIT — see [LICENSE](./LICENSE).
