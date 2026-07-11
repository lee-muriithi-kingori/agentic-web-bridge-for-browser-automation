# Security

## Scope

The **Agentic Web Bridge for Browser Automation** is a local HTTP-to-CDP bridge. Anyone who can reach `http://127.0.0.1:9876` on the host can issue commands that the browser will execute, including on tabs where the user is logged in.

This is a feature, not a bug — the whole point is to give your local AI agent the same control you have. But it means **the security boundary is the host**, not the bridge.

## Threat model

- **In scope**: the bridge exposing more than the local loopback; the extension auto-attaching without the user seeing the yellow debug bar; the server accidentally binding to a public interface; the queue returning someone else's command results.
- **Out of scope**: anything the user does through the bridge against sites they don't own or have explicit permission to automate. That's on the operator, not on this project.

## Reporting a vulnerability

This repository is **archived** and read-only. If you find a real security issue, you can:

1. Fork the repo and open a pull request against your fork.
2. Email the maintainer account listed on the GitHub profile with a clear description, reproduction steps, and a suggested fix.

Please don't disclose publicly until a fix is available.

## Hardening tips for operators

- Keep the bridge on `127.0.0.1` — that's the default. Don't set `HOST=0.0.0.0`.
- Don't run the bridge on a shared / multi-user host.
- If you attach the extension to a tab, you'll see a yellow "being debugged" bar. If you see that bar on a tab you didn't attach, click **Cancel** and the bridge will detach from that tab.
- Pin the extension's icon so you can quickly check whether the popup shows a green / connected state.
- Treat any agent that can write to your filesystem as trusted. The bridge has no auth — it's gated by network reachability, full stop.
- Audit your agent's outbound traffic separately. The bridge won't phone home, but the agent driving it might.
