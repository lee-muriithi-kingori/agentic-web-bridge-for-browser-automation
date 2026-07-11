# Agentic Web Bridge for Browser Automation

A local-first browser automation bridge. AI agents drive your real Chrome / Edge via the Chrome DevTools Protocol.

**Built by Vex** — your AI build collaborator. Agent-agnostic, 100 % local, your logins stay yours. No cloud relay, no third-party tokens, no SaaS lock-in. You drive your own browser, with your own sessions, on your own machine.

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
> - The maintainers (Vex / the GitHub account holding the repo) provide this code **as-is, with no warranty**, under the MIT license. You bear all responsibility for how you use it.
> - This project is not affiliated with, endorsed by, or sponsored by any AI vendor, browser vendor, or SaaS company. The name "Vex" refers to the AI build collaborator that authored the project.

## Responsible use

- Use this on your own browser, with your own accounts, for your own automation.
- If a site's ToS forbids the kind of automation you want — don't do it.
- Don't build spam, credential stuffing, CAPTCHA bypass, or anything that hurts other people.
- If you find a security issue, please open a fork and reach out (see `SECURITY.md`).


## What it does

Your AI agent can:

- **Open pages** with real `Page.navigate` calls
- **Read page state** — title, URL, full HTML, visible text
- **Run arbitrary JS** in the page via `Runtime.evaluate` — bypasses page CSP because it runs in the extension's debug world
- **Click** any element with a real mouse event at its center
- **Type** into any input with real keyboard input
- **Press keys** (Enter, Tab, Escape, arrows, etc.)
- **Take screenshots** of the actual rendered viewport (saved to `webbridge/screenshots/`)
- **List, focus, and switch** between tabs
- **Query** elements by selector

All while using **your existing login sessions** — bank dashboards, internal tools, anything that's already authenticated in your browser.

## Architecture

Three pieces:

1. **`server.py`** — a small stdlib-only HTTP server on `127.0.0.1:9876`. Queues commands, returns results, logs everything.
2. **`extension/`** — a Chrome / Edge Manifest V3 extension. The background service worker is the actual browser driver — it uses `chrome.debugger` to attach to tabs and send CDP commands.
3. **`client.py`** — a tiny CLI to send commands from a terminal. Replace with your own agent.

```
┌────────────┐    HTTP     ┌──────────────────┐    CDP     ┌────────────┐
│  agent     │ ─────────▶ │ server.py        │ ────────▶ │  Chrome    │
│  (CLI,     │ ◀───────── │ 127.0.0.1:9876   │ ◀──────── │  (with the │
│  Python)   │            │                  │            │ extension) │
└────────────┘            └──────────────────┘            └────────────┘
```

## Install

### 1. Start the server

```bash
# from the repo root
python server.py 9876
# or
python start.bat           # Windows convenience
```

It listens on `http://127.0.0.1:9876` and prints every request to stdout.

### 2. Load the extension

1. Open `chrome://extensions` (or `edge://extensions`)
2. Toggle **Developer mode** (top right)
3. Click **Load unpacked** and pick the `extension/` folder
4. The extension's icon appears in the toolbar

### 3. Verify

```bash
python client.py ping
```

The popup icon should also turn green (it shows the last-reported page state from the extension).

## Use

```bash
# list all open tabs
python client.py tabs

# focus the active tab and read its title
python client.py title

# read the URL
python client.py url

# get the full HTML
python client.py html > page.html

# evaluate arbitrary JS in the active tab
python client.py eval "document.querySelectorAll('a').length"

# navigate the active tab to a URL
python client.py navigate https://example.com

# click an element
python client.py click "button.submit"

# type into a focused field
python client.py type "#q" "search query"

# press a key (e.g. Enter, Tab, Escape)
python client.py key Enter

# screenshot the visible viewport (saved to webbridge/screenshots/)
python client.py screenshot
```

You can also hit the server directly with curl / any HTTP client:

```bash
curl -X POST http://127.0.0.1:9876/cmd \
  -H "Content-Type: application/json" \
  -d '{"id":"r1","type":"eval","args":{"code":"document.title"}}'

curl "http://127.0.0.1:9876/result?id=r1&wait=5000"
```

## Targets

By default, every command targets the **active tab**. To target a specific tab, pass `tabId` in the args (the `client.py` exposes this; for raw HTTP, add `"tabId": 435111390` to the command args).

The background service worker will auto-attach `chrome.debugger` to the chosen tab on first use. You'll see a yellow "being debugged" bar at the top of that tab — that's normal and required for CDP to work. **Don't click "Cancel" on that bar** or the bridge will detach.

## Limits

- Content scripts don't run on `chrome://` or `edge://` pages, and the debug bar doesn't attach there either. Always work in real `http(s)` pages.
- Each CDP command opens a fresh debugger session per tab. Multiple tabs are supported; pass `tabId` to disambiguate.
- The bridge is **local-only** — it binds to `127.0.0.1`, not `0.0.0.0`. Not reachable from your network.
- One process at a time per port (`9876` by default).

## Security

- The server binds to `127.0.0.1` only. It is not reachable from your LAN.
- The extension is dev-mode (unpacked). For a real release, package and sign it.
- The bridge has full CDP control of any tab you attach to. **Anyone with shell access to your box while Chrome is running can drive your browser**. Don't run it on an untrusted multi-user system.

## Why CDP, not `chrome.scripting.executeScript`?

`chrome.scripting.executeScript` runs in the page's main world, so it is subject to the page's Content Security Policy. A lot of modern sites (`script-src 'self' 'wasm-unsafe-eval' …`) reject `'unsafe-eval'`, which breaks `new Function(...)`-based eval.

`chrome.debugger` + `Runtime.evaluate` runs in the browser's debug world, which is **outside the page's CSP**. That's how Chrome DevTools itself drives pages, and how you can still drive pages that block injected scripts.

The trade-off: the user sees a yellow "being debugged" bar on attached tabs. For agent use that's a fair price.

## Topics

`browser-automation` `chrome-devtools-protocol` `cdp` `ai-agent` `local-first` `browser-extension` `manifest-v3` `python` `agent-tools` `webbridge`

## License

MIT — see [LICENSE](./LICENSE).
