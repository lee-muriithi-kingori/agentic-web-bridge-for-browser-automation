# Changelog

All notable changes to the Agentic Web Bridge are documented in this file.

## v1.1.0 — 2026-07-11

### Added
- `args.await: true` parameter on `eval` to await Promise-returning expressions.
  Previously, `eval` set `awaitPromise: false` on the underlying CDP call, so any
  expression returning a Promise resolved to `{}` in the result. With `await: true`,
  the bridge waits for the Promise to resolve and returns the resolved value.
- `cdp()` helper inside `extension/background.js` — small wrapper around
  `chrome.debugger.sendCommand` so the rest of the SW reads cleanly. The earlier
  release was missing this helper, so the SW errored on every command.
- `tabId` support for `attach` / `detach` and all page commands. Default behavior
  is the active tab; opt in with `cmd.args.tabId` to target a specific tab.
- `reload` command (`{type: "reload"}`) — forces the service worker to reload
  itself, so an agent can pick up file changes without asking the user to click
  the chrome://extensions refresh button.
- `humanize: true` flag on `click`, `type`, and `key` to add anti-detect
  behaviour: mouse trajectories with ease-out curves, per-character typing
  delays, "thinking" pauses, and key-down/key-up jitter. Off by default; the
  non-humanized paths still work for CI, scraping, and batch operations.

### Changed
- `server.py` (v3.0) — added `/screenshot` endpoint and improved result polling.
- `extension/background.js` (v1.1) — now exports a stable `cdp()` wrapper, accepts
  `tabId` in command args, and honors `args.await` on `eval`.
- `client.py` — minor improvements.

### Fixed
- `eval` returning `{}` for Promise-returning code (the `awaitPromise` flag fix).
- `cdp is not defined` ReferenceError that affected every command after a
  particular sequence of edits (the missing `cdp()` helper).
- `click` failing silently on Reddit-style pages where the visible "leave" /
  "join" link is an `<A>` with a jQuery click handler — `el.click()` via `eval`
  now triggers the handler reliably.

## v1.0.0 — 2026-07-10

### Added
- Initial public release.
- Manifest V3 Chrome extension (`extension/`).
- Python HTTP server (`server.py`) on `127.0.0.1:9876` — stdlib only.
- CLI client (`client.py`) for the agent to send commands.
- One-click launcher (`start_server.bat`).
- `examples/` directory with `leadgen.py` and `find_bounties.py`.
- `README.md`, `SECURITY.md`, `LICENSE`, `FORUM_POSTS.md`.
