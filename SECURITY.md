# Security

## Scope

The **Agentic Web Bridge for Browser Automation** is a local HTTP-to-CDP bridge. As of v4, it operates on **exactly one pinned tab** — chosen by you in the extension popup. Anyone who can reach `http://127.0.0.1:9876` on the host can issue commands that drive the pinned tab, including on sites where you are logged in.

This is a feature, not a bug — the whole point is to give your local AI agent the same control you have on that one tab. But it means **the security boundary is the host**, not the bridge.

## Threat model

- **In scope**: the bridge exposing more than the local loopback; the extension auto-attaching without the user seeing the yellow debug bar; the server accidentally binding to a public interface; the queue returning someone else's command results; the `/os` endpoint driving anything outside the browser without the operator's awareness.
- **Out of scope**: anything the user does through the bridge against sites they don't own or have explicit permission to automate. That's on the operator, not on this project.

## What v4 changed (security-relevant)

| Before v4 | v4 |
|---|---|
| Extension defaulted to the **active tab** — switching to Gmail handed Gmail to the agent | Extension operates on **only the pinned tab** — switching tabs has no effect |
| Server could supply `cmd.args.tabId` to target **any** open tab | Server-supplied `tabId` is **rejected** if it doesn't match the pinned tab |
| No way to say "this tab only, not that one" | Popup has a **"Pin this tab"** button; the pin is persisted in `chrome.storage.local` |
| Extension ID regenerated on every service-worker restart (server got confused about which extension was connected) | Extension ID is **persisted** across SW restarts |
| `attach` command could attach to any tab | `attach` is **gated** by the pinned-tab rule — it can only attach to the pinned tab |

## Reporting a vulnerability

If you find a real security issue, you can:

1. Open a GitHub issue (the repo is **not** archived when actively being developed).
2. Email the maintainer account listed on the GitHub profile with a clear description, reproduction steps, and a suggested fix.

Please don't disclose publicly until a fix is available.

## Hardening tips for operators

- Keep the bridge on `127.0.0.1` — that's the default. Don't set `WEBBRIDGE_HOST=0.0.0.0`.
- Don't run the bridge on a shared / multi-user host.
- **Don't pin your banking tab.** Pin a dedicated tab for automation. If you need to automate a sensitive site, pin a fresh tab, log in, automate, then unpin.
- If you attach the extension to a tab, you'll see a yellow "being debugged" bar on the pinned tab. If you see that bar on a tab you didn't pin, click **Cancel** and the bridge will detach.
- Pin the extension's icon so you can quickly check whether the popup shows a green / connected state and which tab is pinned.
- Treat any agent that can write to your filesystem as trusted. The bridge has no auth — it's gated by network reachability, full stop.
- **The `/os` endpoint bypasses the pinned-tab guard** — it can drive the whole desktop (mouse, keyboard, screenshots) via `pyautogui`. Only enable the `[os]` extra if you trust your agent. Disable by simply not installing `pyautogui`.
- Audit your agent's outbound traffic separately. The bridge won't phone home, but the agent driving it might.
