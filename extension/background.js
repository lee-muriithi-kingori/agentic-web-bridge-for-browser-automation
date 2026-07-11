// WebBridge background service worker (v4) — uses chrome.debugger (CDP).
// Architecture mirrors Kimi WebBridge: the extension attaches to tabs
// via the chrome.debugger API, then sends Chrome DevTools Protocol
// commands. CDP runs at the browser layer, so it:
//   - bypasses page Content Security Policy
//   - can dispatch real mouse / keyboard events
//   - can capture the actual rendered viewport
//   - works with the user's existing login sessions
// Commands are queued by the local server, picked up here, dispatched
// to the active tab, and results posted back.

const SERVER = "http://127.0.0.1:9876";
const EXT_ID = "wb-" + Math.random().toString(36).slice(2, 10);
const POLL_MS = 800;

const knownTabs = new Map();
let activeTabId = null;
const attachedTabs = new Set();   // tabIds where we have a debugger session

// ---------- helpers ----------

function log(...a) { console.log("[webbridge]", ...a); }

async function cdp(tabId, method, params = {}) {
  if (!attachedTabs.has(tabId)) {
    try {
      await chrome.debugger.attach({ tabId }, "1.3");
      attachedTabs.add(tabId);
      log("cdp attach tab=" + tabId);
    } catch (e) {
      throw new Error("debugger attach failed: " + (e.message || e));
    }
  }
  try {
    return await chrome.debugger.sendCommand({ tabId }, method, params);
  } catch (e) {
    // The user might have rejected the debug bar, or another debugger
    // took over. Drop the session and let the next call re-attach.
    attachedTabs.delete(tabId);
    throw e;
  }
}

async function evald(tabId, expression, returnByValue = true, awaitPromise = false) {
  const r = await cdp(tabId, "Runtime.evaluate", {
    expression,
    returnByValue,
    awaitPromise,
  });
  if (r.exceptionDetails) {
    const txt = r.exceptionDetails.exception && r.exceptionDetails.exception.description
      || r.exceptionDetails.text
      || "evaluate failed";
    throw new Error(txt);
  }
  return r.result && r.result.value;
}

async function ensureAttached(tabId) {
  if (!attachedTabs.has(tabId)) {
    try {
      await chrome.debugger.attach({ tabId }, "1.3");
      attachedTabs.add(tabId);
      log("cdp attach tab=" + tabId);
    } catch (e) {
      throw new Error("debugger attach failed: " + (e.message || e) + " — click the extension icon and accept the debug bar");
    }
  }
}

// ---------- tab events ----------

chrome.tabs.onActivated.addListener(({ tabId }) => {
  activeTabId = tabId;
  postState().catch(() => {});
});
chrome.tabs.onUpdated.addListener((tabId, _info, tab) => {
  if (tab && tab.active) activeTabId = tabId;
  knownTabs.set(tabId, { url: tab.url || "", title: tab.title || "", lastSeen: Date.now() });
  postState().catch(() => {});
});
chrome.tabs.onRemoved.addListener((tabId) => {
  knownTabs.delete(tabId);
  attachedTabs.delete(tabId);
  if (activeTabId === tabId) activeTabId = null;
});

chrome.debugger.onDetach.addListener((source, reason) => {
  if (source && source.tabId != null) {
    attachedTabs.delete(source.tabId);
    log("cdp detach tab=" + source.tabId + " reason=" + reason);
  }
});

// ---------- command implementations (CDP-backed) ----------

async function cmdNavigate(tabId, args) {
  if (!args || !args.url) throw new Error("navigate requires url");
  await cdp(tabId, "Page.navigate", { url: args.url });
  // Wait for the page to load.
  await cdp(tabId, "Page.enable").catch(() => {});
  return { navigated: args.url };
}

async function cmdEval(tabId, args) {
  if (args == null || args.code == null) throw new Error("eval requires code");
  const v = await evald(tabId, String(args.code));
  return v;
}

async function cmdTitle(tabId) { return evald(tabId, "document.title"); }
async function cmdUrl(tabId)   { return evald(tabId, "location.href"); }
async function cmdHtml(tabId)  { return evald(tabId, "document.documentElement.outerHTML"); }

async function cmdSnippet(tabId) {
  return evald(tabId,
    "(()=>{const m=document.querySelector('main')||document.body;const t=(m?m.innerText:document.body.innerText)||'';return t.replace(/\\s+/g,' ').trim().slice(0,2000);})()");
}

async function cmdQuery(tabId, args) {
  const sel = args && args.selector;
  if (!sel) throw new Error("query requires selector");
  // Use DOM.querySelectorAll for efficiency; fall back to Runtime.evaluate
  // for complex selectors.
  const expr =
    "(()=>{const r=document.querySelectorAll(" + JSON.stringify(sel) +
    ");return Array.from(r).slice(0,50).map(e=>({tag:e.tagName,text:(e.innerText||'').slice(0,200),href:e.href||null,id:e.id||null,cls:e.className||null}));})()";
  return evald(tabId, expr);
}

async function cmdClick(tabId, args) {
  const sel = args && args.selector;
  if (!sel) throw new Error("click requires selector");
  // Find the element's center via getBoxModel, then dispatch a real mouse
  // press/release at that point.
  const expr =
    "(()=>{const e=document.querySelector(" + JSON.stringify(sel) + ");" +
    "if(!e)throw new Error('no match for '+" + JSON.stringify(sel) + ");" +
    "e.scrollIntoView({block:'center'});" +
    "const r=e.getBoundingClientRect();" +
    "return {x:r.left+r.width/2,y:r.top+r.height/2,tag:e.tagName,text:(e.innerText||'').slice(0,80)};})()";
  const where = await evald(tabId, expr);
  await cdp(tabId, "Input.dispatchMouseEvent", { type: "mousePressed", x: where.x, y: where.y, button: "left", clickCount: 1 });
  await cdp(tabId, "Input.dispatchMouseEvent", { type: "mouseReleased", x: where.x, y: where.y, button: "left", clickCount: 1 });
  return { clicked: where.tag, at: { x: where.x, y: where.y }, text: where.text };
}

async function cmdType(tabId, args) {
  const sel = args && args.selector;
  const text = args && args.text;
  if (!sel) throw new Error("type requires selector");
  if (text == null) throw new Error("type requires text");
  // Focus the field by clicking it, then type.
  await cmdClick(tabId, { selector: sel });
  // InsertText is the right way — bypasses keyboard layout issues.
  await cdp(tabId, "Input.insertText", { text: String(text) });
  return { typed: String(text).length, into: sel };
}

async function cmdScreenshot(tabId) {
  // Full-page or viewport? Default viewport (matches what the user sees).
  const r = await cdp(tabId, "Page.captureScreenshot", { format: "png" });
  // r.data is base64-encoded PNG.
  const r2 = await fetch(SERVER + "/screenshot", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tabId, png_b64: r.data }),
  });
  const j = await r2.json();
  if (!j.ok) throw new Error(j.error || "screenshot upload failed");
  return j; // { ok, path, url, size }
}

async function cmdKey(tabId, args) {
  if (!args || !args.key) throw new Error("key requires key");
  // args.key is e.g. "Enter", "Tab", "Escape", "ArrowDown", or a printable char
  // CDP expects windowsVirtualKeyCode for some events and text for others.
  // We use the high-level dispatchKeyEvent with text.
  const k = String(args.key);
  await cdp(tabId, "Input.dispatchKeyEvent", { type: "keyDown", text: k, unmodifiedText: k, key: k, code: k, windowsVirtualKeyCode: k.charCodeAt(0) || 0 });
  await cdp(tabId, "Input.dispatchKeyEvent", { type: "keyUp", text: k, unmodifiedText: k, key: k, code: k, windowsVirtualKeyCode: k.charCodeAt(0) || 0 });
  return { pressed: k };
}

const COMMANDS = {
  navigate: cmdNavigate,
  eval: cmdEval,
  title: cmdTitle,
  url: cmdUrl,
  html: cmdHtml,
  snippet: cmdSnippet,
  query: cmdQuery,
  click: cmdClick,
  type: cmdType,
  screenshot: cmdScreenshot,
  key: cmdKey,
};

// ---------- network + state ----------

async function postState() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.url) return;
    activeTabId = tab.id;
    const known = knownTabs.get(tab.id);
    await fetch(SERVER + "/state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ext: EXT_ID,
        url: tab.url,
        title: tab.title || "",
        tabId: tab.id,
        attached: attachedTabs.has(tab.id),
        snippet: known ? "" : "",
      }),
    });
  } catch (_) {}
}

async function pollOnce() {
  try {
    const r = await fetch(SERVER + "/poll?ext=" + encodeURIComponent(EXT_ID));
    if (!r.ok) return;
    const cmd = await r.json();
    if (!cmd || !cmd.id) return;
    let payload;
    try {
      if (cmd.type === "ping") {
        payload = { id: cmd.id, ok: true, value: { pong: true, ext: EXT_ID, attached: Array.from(attachedTabs) } };
      } else if (cmd.type === "tabs") {
        const tabs = await chrome.tabs.query({});
        payload = { id: cmd.id, ok: true, value: tabs.map(t => ({ id: t.id, url: t.url, title: t.title, active: t.active, attached: attachedTabs.has(t.id) })) };
      } else if (cmd.type === "active_tab") {
        const [t] = await chrome.tabs.query({ active: true, currentWindow: true });
        payload = { id: cmd.id, ok: true, value: t ? { id: t.id, url: t.url, title: t.title, attached: attachedTabs.has(t.id) } : null };
      } else if (cmd.type === "attach") {
        let t;
        if (cmd.args && cmd.args.tabId != null) {
          t = await chrome.tabs.get(cmd.args.tabId);
        } else {
          [t] = await chrome.tabs.query({ active: true, currentWindow: true });
        }
        if (!t) throw new Error("no tab to attach to");
        await ensureAttached(t.id);
        payload = { id: cmd.id, ok: true, value: { attached: true, tabId: t.id } };
      } else if (cmd.type === "detach") {
        const targetId = cmd.args && cmd.args.tabId != null
          ? cmd.args.tabId
          : (await chrome.tabs.query({ active: true, currentWindow: true }))[0]?.id;
        if (targetId != null && attachedTabs.has(targetId)) {
          await chrome.debugger.detach({ tabId: targetId });
          attachedTabs.delete(targetId);
        }
        payload = { id: cmd.id, ok: true, value: { detached: true, tabId: targetId } };
      } else if (cmd.type === "reload") {
        // Force the service worker to reload so the new background.js
        // takes effect without the user having to click the ↻ button.
        chrome.runtime.reload();
        payload = { id: cmd.id, ok: true, value: { reloading: true } };
      } else {
        const handler = COMMANDS[cmd.type];
        if (!handler) throw new Error("unknown command: " + cmd.type);
        // Use cmd.args.tabId if supplied, else the active tab.
        let targetId = cmd.args && cmd.args.tabId;
        if (targetId == null) {
          const [t] = await chrome.tabs.query({ active: true, currentWindow: true });
          if (!t) throw new Error("no active tab");
          targetId = t.id;
        }
        const value = await handler(targetId, cmd.args);
        payload = { id: cmd.id, ok: true, value };
      }
    } catch (e) {
      payload = { id: cmd.id, ok: false, error: String(e && e.message || e) };
    }
    await fetch(SERVER + "/result", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (_) {}
}

async function tick() {
  await postState();
  await pollOnce();
}

chrome.runtime.onInstalled.addListener(() => { tick(); });
chrome.runtime.onStartup.addListener(() => { tick(); });

(async function loop() {
  while (true) {
    await tick();
    await new Promise(r => setTimeout(r, POLL_MS));
  }
})();
