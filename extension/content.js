// WebBridge content script. Uses sendMessage (not ports) so it can wake
// the MV3 service worker up on first contact. Every tick is a chance to
// pull a command; every result is a one-way send.

(function () {
  "use strict";

  const TICK_MS = 800;

  async function run(cmd) {
    const args = (cmd && cmd.args) || {};
    switch (cmd.type) {
      case "eval": {
        // eslint-disable-next-line no-new-func
        const fn = new Function("with(this){return (" + String(args.code) + ")}");
        return { value: await fn.call(window) };
      }
      case "html":
        return { value: document.documentElement.outerHTML };
      case "url":
        return { value: location.href };
      case "title":
        return { value: document.title };
      case "click": {
        const el = document.querySelector(args.selector);
        if (!el) throw new Error("no element matches " + args.selector);
        el.click();
        return { value: { clicked: true, tag: el.tagName } };
      }
      case "type": {
        const el = document.querySelector(args.selector);
        if (!el) throw new Error("no element matches " + args.selector);
        el.focus();
        el.value = args.text || "";
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
        return { value: { typed: true, len: (args.text || "").length } };
      }
      case "screenshot":
        return { value: {
          w: window.innerWidth, h: window.innerHeight,
          x: window.scrollX, y: window.scrollY,
        } };
      case "snippet": {
        const main = document.querySelector("main") || document.body;
        const text = (main ? main.innerText : document.body.innerText) || "";
        return { value: text.replace(/\s+/g, " ").trim().slice(0, 800) };
      }
      default:
        throw new Error("unknown command type: " + cmd.type);
    }
  }

  async function sendTick() {
    // A sendMessage to the SW wakes it up if it isn't running, and carries
    // our current page state. We swallow any errors silently.
    try {
      const r = await chrome.runtime.sendMessage({
        type: "tick",
        url: location.href,
        title: document.title,
        snippet: ((document.querySelector("main") || document.body).innerText || "")
          .replace(/\s+/g, " ").trim().slice(0, 800),
      });
      if (r && r.command && r.command.id) {
        const id = r.command.id;
        try {
          const out = await run(r.command);
          await chrome.runtime.sendMessage({
            type: "result",
            id,
            ok: true,
            value: out.value,
          });
        } catch (e) {
          await chrome.runtime.sendMessage({
            type: "result",
            id,
            ok: false,
            error: String(e && e.message || e),
          });
        }
      }
    } catch (_) {
      // SW not running yet — back off and try again.
    }
  }

  // Tick on a steady cadence.
  (async function loop() {
    while (true) {
      await sendTick();
      await new Promise(r => setTimeout(r, TICK_MS));
    }
  })();
})();
