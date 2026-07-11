# Forum posts — Agentic Web Bridge for Browser Automation

Repo: https://github.com/lee-muriithi-kingori/agentic-web-bridge-for-browser-automation

---

## Option A — Reddit r/LocalLLaMA (best fit — local-AI audience)

**Title:** [Open Source] Agentic Web Bridge for Browser Automation — local AI agents driving your real Chrome via CDP, agent-agnostic, 100% local

**Body:**

Built an open-source bridge so any local AI agent (Ollama, llama.cpp, LM Studio, vLLM, exo — or any HTTP-speaking agent) can drive your real Chrome/Edge via the Chrome DevTools Protocol.

Repo: https://github.com/lee-muriithi-kingori/agentic-web-bridge-for-browser-automation

What it does:
- `navigate` / `eval` / `click` / `type` / `screenshot` / `query` over plain HTTP
- Uses `chrome.debugger` (CDP) so it bypasses page Content-Security-Policy
- Works with your existing logged-in sessions — bank dashboards, internal tools, anything
- 100% local. Binds to `127.0.0.1:9876`. No cloud, no telemetry, no third-party token.
- Agent-agnostic by design. The AI doesn't matter; only the HTTP/JSON does.
- MIT licensed, no warranty, by Vex.

Why CDP and not `chrome.scripting.executeScript`? Modern sites reject `'unsafe-eval'` in their CSP and break `new Function(...)`-based eval. CDP runs in the browser's debug world, outside the page's CSP — same as DevTools.

Two example scripts in `examples/` show it driving real workflows:
- `leadgen.py` — Google search → click Facebook posts → extract contact info
- `find_bounties.py` — open GitHub bounty issues → read bodies

Anyone can fork and use. The main repo is archived/read-only to prevent drift — the canonical version is locked at the version you see now. If you want to extend, fork and PR against your fork.

Disclaimer + SECURITY.md are in the repo. Use it on machines you control, respect site ToS, don't scrape behind paywalls.

Curious what people build with this. What local agents are you all driving with what?

---

## Option B — Hacker News (Show HN)

**Title:** Show HN: Agentic Web Bridge – agent-agnostic local CDP bridge for AI browser automation

**URL:** https://github.com/lee-muriithi-kingori/agentic-web-bridge-for-browser-automation

**Text (optional first comment):**

I built a small local HTTP-to-CDP bridge so any AI agent — local LLM, cloud agent CLI, or your own script — can drive a real Chrome/Edge tab without sending data to anyone.

Two pieces:
- `server.py` — stdlib HTTP on 127.0.0.1:9876
- `extension/` — Manifest V3 Chrome extension that uses `chrome.debugger` + `Runtime.evaluate` + `Input.dispatchMouseEvent` to drive the tab

The interesting part is that `chrome.debugger` runs in the browser's debug world, which is outside the page's CSP. That's why `eval` works on sites that have `script-src 'self' 'wasm-unsafe-eval'` — they block injected scripts but the browser debugger gets through.

The protocol is intentionally tiny: `POST /cmd {id, type, args}` then `GET /result?id=...`. Reference `client.py` is 100 lines.

MIT. Archived as a public read-only release so it doesn't drift. Forks welcome.

---

## Option C — Dev.to (longer form, friendlier tone)

**Title:** I open-sourced my local AI browser-automation bridge (and here's why I made it agent-agnostic)

**Body (opening):**

I've been building AI agents for the last few months and kept hitting the same wall: every tool that lets an agent drive a browser either (a) lives in the cloud, or (b) is locked to one specific AI vendor. I wanted a local, vendor-neutral option that works with Ollama, llama.cpp, or any HTTP-speaking script.

So I built **Agentic Web Bridge for Browser Automation** — a 350-line Python server + 300-line Chrome extension that exposes a tiny HTTP API (`POST /cmd`, `GET /result`) and translates each command into a Chrome DevTools Protocol call against your real browser.

Link: https://github.com/lee-muriithi-kingori/agentic-web-bridge-for-browser-automation

What makes it interesting (to me at least):

- **Local-first.** Server binds to 127.0.0.1, no cloud, no third-party tokens, no telemetry. Your logins stay yours.
- **Agent-agnostic.** The AI is just an HTTP client. Write 20 lines of glue in any language.
- **Bypasses page CSP** because it uses the browser's debug world, not injected scripts.
- **MIT-licensed, public, archived** so it stays the canonical version forever.

Two example scripts in the repo (`examples/leadgen.py`, `examples/find_bounties.py`) drive real workflows through a real Chrome. They use my actual logged-in Facebook, GitHub, and Google sessions.

The whole project is about 650 lines of code (server + extension + reference client). If you want to bolt a local LLM onto it, the protocol is documented in the README and the `client.py` is the reference. The README also has a disclaimer + SECURITY.md about responsible use.

Happy to answer questions. Repo: https://github.com/lee-muriithi-kingori/agentic-web-bridge-for-browser-automation

---

## Short version (for Twitter/X, single tweet)

Open-sourced Agentic Web Bridge for Browser Automation — 650 lines of code that let any local AI agent drive your real Chrome via CDP. 100% local, agent-agnostic, MIT. https://github.com/lee-muriithi-kingori/agentic-web-bridge-for-browser-automation
