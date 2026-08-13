// WebBridge background service worker (v5) — uses chrome.debugger (CDP).
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
const POLL_MS = 800;
const POLL_WAIT_MS = 25000;  // server long-poll duration (must match POLL_TIMEOUT_MS in server.py)

// Persist a stable extension id across service-worker restarts.
// (MV3 kills the SW after ~30s idle, which used to regenerate EXT_ID and
// confuse the Python server into thinking a new extension had connected.)
let EXT_ID = "wb-pending";
chrome.storage.local.get(["extId"]).then(({ extId }) => {
  if (extId) {
    EXT_ID = extId;
  } else {
    EXT_ID = "wb-" + Math.random().toString(36).slice(2, 10);
    chrome.storage.local.set({ extId: EXT_ID });
  }
});

// Auth token — read from storage on every fetch (so the user can set it
// from the popup without restarting the SW). When empty, no Authorization
// header is sent (server treats this as 'auth disabled').
async function getToken() {
  const { token } = await chrome.storage.local.get("token");
  return token || "";
}

// Build a URL with optional ?token= query param for endpoints that can't
// easily set headers (none currently, but kept for future use).
async function authHeaders() {
  const token = await getToken();
  const h = { "Content-Type": "application/json" };
  if (token) h["Authorization"] = "Bearer " + token;
  return h;
}

const knownTabs = new Map();
let activeTabId = null;
const attachedTabs = new Set();   // tabIds where we have a debugger session

// ---------- designated-tab guard ----------
//
// SECURITY: only ONE tab can be driven at a time. The user picks it from
// the popup ("Pin this tab"); the choice is persisted in chrome.storage.local
// and survives SW restarts. Any command that doesn't target the pinned tab
// is rejected with a clear error.
//
// Why this matters: previously the bridge defaulted to "the active tab",
// which silently followed the user's focus — switch to Gmail, the agent is
// now driving Gmail. Worse, the server could supply cmd.args.tabId to target
// ANY open tab. Both paths are now closed.

async function getDesignatedTabId() {
  const { designatedTabId } = await chrome.storage.local.get("designatedTabId");
  return designatedTabId != null ? Number(designatedTabId) : null;
}

async function resolveTargetTabId(cmd) {
  // Resolve which tab this command should run against.
  // Returns { tabId } or throws an Error the caller can post back.
  const pinned = await getDesignatedTabId();
  const requested = cmd.args && cmd.args.tabId != null ? Number(cmd.args.tabId) : null;
  if (pinned == null) {
    throw new Error(
      "no pinned tab — open the WebBridge popup and click \"Pin this tab\" on the tab you want the agent to drive"
    );
  }
  if (requested != null && requested !== pinned) {
    throw new Error(
      `command targets tab ${requested} but only the pinned tab (${pinned}) can be driven; ` +
      `re-pin the desired tab from the popup if you want to switch`
    );
  }
  // Sanity-check the pinned tab still exists.
  try {
    await chrome.tabs.get(pinned);
  } catch (_) {
    // Tab was closed — clear the pin so the user is forced to re-pin.
    await chrome.storage.local.remove([
      "designatedTabId", "designatedUrl", "designatedTitle", "designatedAt"
    ]);
    throw new Error(`pinned tab ${pinned} no longer exists; re-pin a tab from the popup`);
  }
  return pinned;
}

// If the pinned tab is closed by the user, clear the pin so the popup shows "none".
chrome.tabs.onRemoved.addListener(async (tabId) => {
  const pinned = await getDesignatedTabId();
  if (pinned === tabId) {
    await chrome.storage.local.remove([
      "designatedTabId", "designatedUrl", "designatedTitle", "designatedAt"
    ]);
    log(`pinned tab ${tabId} was closed; pin cleared`);
  }
  attachedTabs.delete(tabId);
  lastMouse.delete(tabId);
  consoleBuffer.delete(tabId);
  knownTabs.delete(tabId);
});

// Popup → SW channel (so "Pin this tab" reacts immediately instead of
// waiting up to POLL_MS for the next poll cycle).
chrome.runtime.onMessage.addListener((msg, _sender, _sendResponse) => {
  // No async work here — just a notification. The next pollOnce() will pick
  // up the new pinned-tab id from storage.
  if (msg && msg.type === "designated") {
    log(`popup pinned tab ${msg.tabId}`);
  } else if (msg && msg.type === "unpinned") {
    log("popup unpinned tab");
  }
  return false;  // synchronous, no response expected
});

// ---------- helpers ----------

function log(...a) { console.log("[webbridge]", ...a); }

// ---------- anti-detect: random pre-action jitter ----------
async function preActionJitter() { await sleep(jitter(120, 600)); }

// ---------- anti-detect helpers ----------

// Random delay helpers (humans are not metronomes).
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
function jitter(min, max) { return min + Math.random() * (max - min); }
async function humanDelay(min = 80, max = 350) { await sleep(jitter(min, max)); }

// Move the mouse from (fromX,fromY) to (toX,toY) in a few mouseMoved events
// so anti-bot heuristics see a real cursor trajectory, not a teleport.
async function moveMouseHuman(tabId, fromX, fromY, toX, toY) {
  const steps = Math.max(6, Math.round(jitter(8, 18)));
  for (let i = 1; i <= steps; i++) {
    const t = i / steps;
    // ease-out curve
    const ease = 1 - Math.pow(1 - t, 2);
    const x = fromX + (toX - fromX) * ease + jitter(-1.5, 1.5);
    const y = fromY + (toY - fromY) * ease + jitter(-1.5, 1.5);
    await cdp(tabId, "Input.dispatchMouseEvent", { type: "mouseMoved", x, y, button: "none" });
    await sleep(jitter(6, 18));
  }
}

// Track the last mouse position so the next move has a real "from" coordinate.
const lastMouse = new Map(); // tabId -> {x, y}
function setLastMouse(tabId, x, y) { lastMouse.set(tabId, { x, y }); }
function getLastMouse(tabId) {
  return lastMouse.get(tabId) || { x: 100, y: 100 };
}

// Humanized click: hover trajectory, jitter, slight off-center, hover delay.
async function clickHuman(tabId, where) {
  const last = getLastMouse(tabId);
  await moveMouseHuman(tabId, last.x, last.y, where.x, where.y);
  await sleep(jitter(50, 200));
  await cdp(tabId, "Input.dispatchMouseEvent", {
    type: "mousePressed", x: where.x, y: where.y, button: "left", clickCount: 1
  });
  await sleep(jitter(20, 60));
  await cdp(tabId, "Input.dispatchMouseEvent", {
    type: "mouseReleased", x: where.x, y: where.y, button: "left", clickCount: 1
  });
  setLastMouse(tabId, where.x, where.y);
}

// Humanized type: per-char dispatchKeyEvent with variable WPM and occasional
// pauses. Falls back to insertText only if the page doesn't accept the
// char events.
async function typeHuman(tabId, text) {
  await cdp(tabId, "Input.dispatchKeyEvent", {
    type: "keyDown", text: "", key: "", code: "", windowsVirtualKeyCode: 0
  });
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    const isNewline = ch === "\n";
    await sleep(jitter(45, 180));
    if (isNewline) {
      await cdp(tabId, "Input.dispatchKeyEvent", {
        type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13
      });
      await cdp(tabId, "Input.dispatchKeyEvent", {
        type: "keyUp",   key: "Enter", code: "Enter", windowsVirtualKeyCode: 13
      });
      continue;
    }
    // dispatchKeyEvent with text: mimics a real keypress better than insertText
    await cdp(tabId, "Input.dispatchKeyEvent", {
      type: "keyDown", text: ch, unmodifiedText: ch, key: ch, code: "Key" + ch.toUpperCase(),
      windowsVirtualKeyCode: ch.toUpperCase().charCodeAt(0)
    });
    await cdp(tabId, "Input.dispatchKeyEvent", {
      type: "keyUp",   text: ch, unmodifiedText: ch, key: ch, code: "Key" + ch.toUpperCase(),
      windowsVirtualKeyCode: ch.toUpperCase().charCodeAt(0)
    });
    // Occasional "thinking" pause every ~12 chars
    if (i % 12 === 11) await sleep(jitter(300, 700));
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

// CDP helper: send a Chrome DevTools Protocol command to the attached tab.
async function cdp(tabId, method, params) {
  try {
    return await chrome.debugger.sendCommand({ tabId }, method, params || {});
  } catch (e) {
    const msg = String(e && e.message || e);
    if (msg.includes("detach") || msg.includes("Target closed") || msg.includes("no target") || msg.includes("not found")) {
      attachedTabs.delete(tabId);
      throw new Error("CDP target detached/closed: " + msg);
    }
    throw e;
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
  const v = await evald(tabId, String(args.code), true, !!args.await);
  return v;
}

async function cmdTitle(tabId) { return evald(tabId, "document.title"); }
async function cmdUrl(tabId)   { return evald(tabId, "location.href"); }
async function cmdHtml(tabId, args) {
  // v5: opt-in safe mode strips <script>/<style>/<noscript>/<iframe> and
  // inline on* handlers + javascript: URLs from the returned HTML. This
  // is a light sanitizer, NOT a substitute for DOMPurify when ingesting
  // untrusted HTML — but it stops the common cases (script execution,
  // clickjacking helpers, link-based XSS) without a 100KB dependency.
  const safe = !!(args && args.safe);
  const sel = (args && args.selector) || "document.documentElement";
  const expr = "(()=>{\n" +
    "  const root = " + sel + ";\n" +
    "  if (!root) return '';\n" +
    "  let html = root.outerHTML;\n" +
    (safe ?
    "  if (typeof DOMParser === 'undefined') return html;\n" +
    "  const doc = new DOMParser().parseFromString('<!doctype html><body>' + html + '</body>', 'text/html');\n" +
    "  doc.querySelectorAll('script,style,noscript,iframe,object,embed').forEach(n => n.remove());\n" +
    "  doc.querySelectorAll('*').forEach(n => {\n" +
    "    for (const a of Array.from(n.attributes)) {\n" +
    "      const n2 = a.name.toLowerCase();\n" +
    "      if (n2.startsWith('on')) n.removeAttribute(a.name);\n" +
    "      if ((n2 === 'href' || n2 === 'src' || n2 === 'xlink:href') && /^\\s*javascript:/i.test(a.value)) n.removeAttribute(a.name);\n" +
    "    }\n" +
    "  });\n" +
    "  html = doc.body.innerHTML;\n"
    : "") +
    "  return html;\n" +
    "})()";
  return evald(tabId, expr);
}

async function cmdSnippet(tabId) {
  return evald(tabId,
    "(()=>{const m=document.querySelector('main')||document.body;const t=(m?m.innerText:document.body.innerText)||'';return t.replace(/\\s+/g,' ').trim().slice(0,2000);})()");
}

// ---------- readable: LLM-optimized text dump of the page ----------
//
// Goal: give a text-only LLM (no vision) everything it needs to understand
// and act on the page — URLs, titles, visible text, form structure,
// interactive elements with selectors, and the accessibility tree — in a
// compact, deterministic text format. No base64, no HTML, no images.
//
// `readable` returns a dict with several fields so the caller can pick
// what they need (or just pass the whole thing to the LLM as one block).
//
// Args:
//   maxChars   (default 20000)  — cap per text field
//   includeA11y (default true)  — include the accessibility-tree dump
//   includeForms (default true) — include form/interactive element list
//   includeConsole (default false) — include last N console messages

async function cmdReadable(tabId, args) {
  const maxChars = (args && args.maxChars) || 20000;
  const includeA11y = args && args.includeA11y !== false;
  const includeForms = args && args.includeForms !== false;
  const includeConsole = args && args.includeConsole === true;

  // 1) Page metadata + visible text — collected in ONE Runtime.evaluate
  //    call so we round-trip once.
  const meta = await evald(tabId, `(()=>{
    const main = document.querySelector('main') || document.body;
    const visible = (main ? main.innerText : (document.body && document.body.innerText)) || '';
    const meta = {};
    for (const m of document.querySelectorAll('meta[name],meta[property]')) {
      const k = m.getAttribute('name') || m.getAttribute('property');
      const v = m.getAttribute('content');
      if (k && v) meta[k] = v;
    }
    return {
      url: location.href,
      title: document.title || '',
      description: meta['description'] || meta['og:description'] || '',
      viewport: { w: window.innerWidth, h: window.innerHeight, scrollX: window.scrollX, scrollY: window.scrollY },
      visibleText: visible.replace(/\\s+/g, ' ').trim().slice(0, ${maxChars}),
      headings: Array.from(document.querySelectorAll('h1,h2,h3')).slice(0, 50).map(h => ({
        level: parseInt(h.tagName.slice(1), 10),
        text: (h.innerText || '').trim().slice(0, 200)
      })),
      meta: meta
    };
  })()`);

  // 2) Interactive elements — buttons, links, inputs, selects, textareas.
  //    Each gets a stable selector the agent can use with `click` / `type`.
  let forms = null;
  if (includeForms) {
    forms = await evald(tabId, `(()=>{
      const out = [];
      const seen = new Set();
      const els = document.querySelectorAll('a,button,input,select,textarea,[role="button"],[role="link"],[role="checkbox"],[role="tab"],[onclick]');
      for (const e of els) {
        if (seen.has(e)) continue;
        seen.add(e);
        // Skip hidden / display:none elements
        const r = e.getBoundingClientRect();
        const cs = getComputedStyle(e);
        if (r.width === 0 || r.height === 0 || cs.visibility === 'hidden' || cs.display === 'none') continue;
        // Build a stable selector
        let sel = '';
        if (e.id && /^[A-Za-z][\\w-]*$/.test(e.id)) sel = '#' + e.id;
        else if (e.getAttribute('data-testid')) sel = '[data-testid="' + e.getAttribute('data-testid') + '"]';
        else if (e.name) sel = e.tagName.toLowerCase() + '[name="' + e.name + '"]';
        else if (e.getAttribute('aria-label')) sel = e.tagName.toLowerCase() + '[aria-label="' + e.getAttribute('aria-label').replace(/"/g, '\\\\"') + '"]';
        else {
          // path-based fallback (max 4 levels)
          const parts = [];
          let cur = e;
          for (let i = 0; i < 4 && cur && cur !== document.body; i++) {
            let p = cur.tagName.toLowerCase();
            if (cur.id && /^[A-Za-z][\\w-]*$/.test(cur.id)) { p = '#' + cur.id; parts.unshift(p); break; }
            if (cur.className && typeof cur.className === 'string') {
              const cls = cur.className.trim().split(/\\s+/).slice(0, 2).map(c => '.' + c).join('');
              p += cls;
            }
            parts.unshift(p);
            cur = cur.parentElement;
          }
          sel = parts.join(' > ');
        }
        out.push({
          tag: e.tagName.toLowerCase(),
          type: e.getAttribute('type') || null,
          id: e.id || null,
          name: e.name || null,
          text: (e.innerText || e.value || e.getAttribute('aria-label') || e.getAttribute('placeholder') || e.getAttribute('title') || '').trim().slice(0, 120),
          href: e.href || null,
          selector: sel,
          rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
          disabled: !!e.disabled,
          checked: 'checked' in e ? !!e.checked : null
        });
        if (out.length >= 200) break;
      }
      return out;
    })()`);
  }

  // 3) Accessibility tree — gives the page's semantic structure even when
  //    the visible text is sparse (e.g. icon-only buttons, ARIA-only labels).
  let a11y = null;
  if (includeA11y) {
    try {
      await cdp(tabId, "Accessibility.enable").catch(() => {});
      const r = await cdp(tabId, "Accessibility.getFullAXTree");
      const lines = [];
      for (const n of (r && r.nodes) || []) {
        const role = (n.role && n.role.value) || "";
        const name = (n.name && n.name.value) || "";
        if (!name && !role) continue;
        if (["generic", "none", "InlineTextBox", "LineBreak", "text"].includes(role)) {
          if (name) lines.push(name.trim());
          continue;
        }
        const indent = "  ".repeat(Math.min((n.depth || 0), 6));
        lines.push(`${indent}[${role}] ${name.trim()}`.trimEnd());
      }
      let txt = lines.join("\n").replace(/\n{3,}/g, "\n\n").trim();
      if (txt.length > maxChars) txt = txt.slice(0, maxChars) + "\n... (truncated)";
      a11y = txt;
    } catch (e) {
      a11y = "(a11y tree unavailable: " + (e.message || e) + ")";
    }
  }

  // 4) Console messages (optional — useful for debugging SPA errors)
  let consoleMsgs = null;
  if (includeConsole) {
    const buf = consoleBuffer.get(tabId) || [];
    consoleMsgs = buf.slice(-30);
  }

  return {
    url: meta.url,
    title: meta.title,
    description: meta.description,
    viewport: meta.viewport,
    visibleText: meta.visibleText,
    headings: meta.headings,
    meta: meta.meta,
    interactiveElements: forms,
    a11yTree: a11y,
    console: consoleMsgs,
    // A ready-to-paste text block for LLMs that just want one string:
    textBlock: [
      `URL: ${meta.url}`,
      `Title: ${meta.title}`,
      meta.description ? `Description: ${meta.description}` : null,
      `Viewport: ${meta.viewport.w}x${meta.viewport.h} @ (${meta.viewport.scrollX},${meta.viewport.scrollY})`,
      "",
      "== VISIBLE TEXT ==",
      meta.visibleText,
      "",
      "== HEADINGS ==",
      (meta.headings || []).map(h => `${'  '.repeat(h.level - 1)}H${h.level}: ${h.text}`).join("\n"),
      "",
      "== INTERACTIVE ELEMENTS ==",
      (forms || []).map((e, i) => `${i + 1}. <${e.tag}${e.type ? ' type=' + e.type : ''}> "${e.text}" → ${e.selector}`).join("\n"),
      "",
      includeA11y ? "== ACCESSIBILITY TREE ==" : null,
      a11y || null,
    ].filter(x => x !== null).join("\n")
  };
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

// see: vision loop with Set-of-Marks overlay (WebVoyager / BrowserControl /
// Manus pattern). Returns:
//   - image: a downsized JPEG/PNG screenshot with red numbered boxes
//     drawn over every interactive element (anchor, button, input,
//     textarea, select, contenteditable, [role=button|link|menuitem|...])
//   - som:   array of {id, tag, role, text, selector, x, y, w, h} — one
//     per numbered box. The agent can say "click element 5" and the
//     bridge translates that to a real click on the actual element.
//   - a11y:  optional accessibility tree (text-only, depth-limited)
//   - cursor/focus/viewport: same as perceive
// Overlays are added as DOM elements, the screenshot is captured with
// them in place, then the overlays are removed — no permanent change
// to the page.
async function cmdSee(tabId, args) {
  const maxWidth = Math.max(200, Math.min(2000, args && args.maxWidth || 800));
  const quality = Math.max(10, Math.min(100, args && args.quality || 70));
  const format = (args && args.format) || "jpeg";
  const maxDepth = (args && args.maxDepth) || 4;
  const maxLen = (args && args.maxLen) || 80;
  const includeTree = !(args && args.tree === false);
  const includeSom = !(args && args.som === false);

  // Build the SoM map by walking interactive elements
  const somMap = includeSom ? await evald(tabId, "(function(){\n" +
    "  const sel = 'a, button, input, textarea, select, [role=button], [role=link], [role=menuitem], [role=tab], [role=checkbox], [role=radio], [contenteditable=true]';\n" +
    "  const all = Array.from(document.querySelectorAll(sel));\n" +
    "  const out = [];\n" +
    "  for (const el of all) {\n" +
    "    const r = el.getBoundingClientRect();\n" +
    "    if (r.width === 0 || r.height === 0) continue;\n" +
    "    if (r.bottom < 0 || r.top > window.innerHeight) continue;\n" +
    "    if (r.right < 0 || r.left > window.innerWidth) continue;\n" +
    "    const cs = getComputedStyle(el);\n" +
    "    if (cs.visibility === 'hidden' || cs.display === 'none' || cs.opacity === '0') continue;\n" +
    "    let selector = '';\n" +
    "    if (el.id) selector = '#' + CSS.escape(el.id);\n" +
    "    else if (el.name) selector = el.tagName.toLowerCase() + '[name=\"' + el.name + '\"]';\n" +
    "    else {\n" +
    "      const path = [];\n" +
    "      let cur = el;\n" +
    "      while (cur && cur !== document.body && path.length < 4) {\n" +
    "        let part = cur.tagName.toLowerCase();\n" +
    "        if (cur.id) { part = '#' + CSS.escape(cur.id); path.unshift(part); break; }\n" +
    "        if (cur.className && typeof cur.className === 'string') {\n" +
    "          const cls = cur.className.trim().split(/\\s+/).slice(0, 1).map(c => '.' + CSS.escape(c)).join('');\n" +
    "          if (cls) part += cls;\n" +
    "        }\n" +
    "        const sibs = cur.parentElement ? Array.from(cur.parentElement.children).filter(c => c.tagName === cur.tagName) : [];\n" +
    "        if (sibs.length > 1) part += ':nth-of-type(' + (sibs.indexOf(cur) + 1) + ')';\n" +
    "        path.unshift(part);\n" +
    "        cur = cur.parentElement;\n" +
    "      }\n" +
    "      selector = path.join(' > ');\n" +
    "    }\n" +
    "    const id = out.length + 1;\n" +
    "    el.setAttribute('data-webbridge-som-id', String(id));\n" +
    "    out.push({ id, tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || '',\n" +
    "      text: (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || '').trim().slice(0, 80),\n" +
    "      selector, x: r.left + r.width/2, y: r.top + r.height/2, w: r.width, h: r.height });\n" +
    "  }\n" +
    "  return out;\n" +
    "})()") : [];

  // Inject overlay divs over each element, then re-screenshot
  let image = null;
  if (Array.isArray(somMap) && somMap.length > 0) {
    // Inject overlay
    await evald(tabId, "(function(boxes){\n" +
      "  const prior = document.getElementById('__webbridge_som_overlay__');\n" +
      "  if (prior) prior.remove();\n" +
      "  const wrap = document.createElement('div');\n" +
      "  wrap.id = '__webbridge_som_overlay__';\n" +
      "  wrap.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:2147483647;';\n" +
      "  for (const b of boxes) {\n" +
      "    const r = document.createElement('div');\n" +
      "    r.style.cssText = 'position:absolute;left:' + (b.x - b.w/2) + 'px;top:' + (b.y - b.h/2) + 'px;width:' + b.w + 'px;height:' + b.h + 'px;border:2px solid #ff2d2d;box-sizing:border-box;background:rgba(255,45,45,0.08);';\n" +
      "    const tag = document.createElement('div');\n" +
      "    tag.style.cssText = 'position:absolute;left:-2px;top:-22px;background:#ff2d2d;color:#fff;font:bold 12px monospace;padding:1px 5px;border-radius:2px;white-space:nowrap;';\n" +
      "    tag.textContent = '[' + b.id + ']';\n" +
      "    r.appendChild(tag);\n" +
      "    wrap.appendChild(r);\n" +
      "  }\n" +
      "  document.body.appendChild(wrap);\n" +
      "  return true;\n" +
    "})(" + JSON.stringify(somMap) + ")");
    // Capture with overlay in place
    const cap = await cdp(tabId, "Page.captureScreenshot", { format });
    // Return the raw base64 (downsizing via canvas-in-page would require
    // sending a 200-500KB base64 string through Runtime.evaluate, which
    // is slow and hits the CDP message-size limit on some pages).
    image = { dataUrl: "data:image/" + format + ";base64," + cap.data, width: 0, height: 0, bytes: cap.data.length, raw: true };
    // Clean up overlay
    await evald(tabId, "(()=>{const o=document.getElementById('__webbridge_som_overlay__');if(o)o.remove();return true;})()");
  } else {
    // No SoM elements found; just take a clean screenshot
    const cap = await cdp(tabId, "Page.captureScreenshot", { format });
    image = { dataUrl: "data:image/" + format + ";base64," + cap.data, width: 0, height: 0, bytes: cap.data.length, raw: true };
  }

  // Cursor / focus / viewport
  const last = await evald(tabId, "(window.__webbridgeLastMouse || { x: 0, y: 0 })");
  let underEl = null;
  try {
    underEl = await evald(tabId, "(function(x, y, ml){\n" +
      "  const e = document.elementFromPoint(x, y);\n" +
      "  if (!e) return null;\n" +
      "  const r = e.getBoundingClientRect();\n" +
      "  return { tag: e.tagName, id: e.id || null, cls: (typeof e.className === 'string' ? e.className : '').slice(0, 60),\n" +
      "    text: (e.innerText || e.value || '').trim().slice(0, ml),\n" +
      "    somId: e.getAttribute('data-webbridge-som-id') || null,\n" +
      "    x: r.left + r.width/2, y: r.top + r.height/2 };\n" +
      "})(" + last.x + ", " + last.y + ", " + maxLen + ")");
  } catch (_) {}

  const focus = await evald(tabId, "(function(ml){\n" +
    "  const a = document.activeElement;\n" +
    "  if (!a || a === document.body) return null;\n" +
    "  return { tag: a.tagName, id: a.id || null, role: a.getAttribute('role') || null,\n" +
    "    value: a.value !== undefined ? String(a.value) : null,\n" +
    "    placeholder: a.placeholder || null,\n" +
    "    somId: a.getAttribute('data-webbridge-som-id') || null,\n" +
    "    text: (a.innerText || '').trim().slice(0, ml) };\n" +
    "})(" + maxLen + ")");
  const vp = await evald(tabId, "({ w: window.innerWidth, h: window.innerHeight, scrollX: window.scrollX, scrollY: window.scrollY, docH: document.documentElement.scrollHeight })");

  // a11y tree (optional)
  let tree = null;
  if (includeTree) {
    tree = await evald(tabId, "(function(md, ml){\n" +
      "  function w(el, d) {\n" +
      "    if (!el || d > md) return '';\n" +
      "    const tag = (el.tagName || '').toLowerCase();\n" +
      "    if (['script','style','noscript','svg','path','head','meta','link'].includes(tag)) return '';\n" +
      "    if (el.offsetWidth === 0 && el.offsetHeight === 0 && d > 0) return '';\n" +
      "    const role = el.getAttribute('role') || (['a','button','input','textarea','select'].includes(tag) ? tag : '');\n" +
      "    const aria = el.getAttribute('aria-label') || '';\n" +
      "    const ph = el.getAttribute('placeholder') || '';\n" +
      "    const somId = el.getAttribute('data-webbridge-som-id') || '';\n" +
      "    const text = (el.innerText || '').trim().slice(0, ml);\n" +
      "    let out = '  '.repeat(d) + '<' + tag;\n" +
      "    if (role) out += ' role=' + role;\n" +
      "    if (aria) out += ' aria=\"' + aria + '\"';\n" +
      "    if (ph) out += ' ph=\"' + ph + '\"';\n" +
      "    if (somId) out += ' som=' + somId;\n" +
      "    out += '>' + (text ? ' ' + text : '') + '\\n';\n" +
      "    for (const c of el.children) out += w(c, d + 1);\n" +
      "    return out;\n" +
      "  }\n" +
      "  return w(document.body, 0);\n" +
    "})(" + maxDepth + ", " + maxLen + ")");
  }

  return {
    url: await evald(tabId, "location.href"),
    title: await evald(tabId, "document.title"),
    cursor: { x: last.x, y: last.y, under: underEl },
    focus: focus,
    viewport: vp,
    image: image,
    som: Array.isArray(somMap) ? somMap : null,
    a11y: tree,
  };
}

async function cmdClick(tabId, args) {
  // If elementId given, translate to a real click via the SoM marker
  if (args && (typeof args.elementId === "number" || (typeof args.elementId === "string" && /^\d+$/.test(args.elementId)))) {
    const eid = Number(args.elementId);
    const r = await evald(tabId, "(function(id){\n" +
      "  const e = document.querySelector('[data-webbridge-som-id=\"' + id + '\"]');\n" +
      "  if (!e) return null;\n" +
      "  e.scrollIntoView({block: 'center', behavior: 'instant'});\n" +
      "  const r = e.getBoundingClientRect();\n" +
      "  return { tag: e.tagName, text: (e.innerText || e.value || '').trim().slice(0, 80),\n" +
      "    x: r.left + r.width/2, y: r.top + r.height/2 };\n" +
      "})(" + eid + ")");
    if (!r) throw new Error("no element with SoM id " + eid);
    args = Object.assign({}, args, { x: r.x, y: r.y });
    delete args.elementId;
  }
  const sel = args && args.selector;
  if (!sel && (typeof args.x !== "number" || typeof args.y !== "number")) throw new Error("click requires selector, {x,y}, or {elementId}");
  const humanize = !!(args && args.humanize);
  // Find the element's center via getBoundingClientRect. Slight random
  // offset (only when humanizing) so two consecutive clicks don't land
  // on the exact same pixel — real humans don't pixel-perfect-center.
  const expr =
    "(()=>{const e=document.querySelector(" + JSON.stringify(sel) + ");" +
    "if(!e)throw new Error('no match for '+" + JSON.stringify(sel) + ");" +
    "e.scrollIntoView({block:'center',behavior:'instant'});" +
    "const r=e.getBoundingClientRect();" +
    "return {x:r.left+r.width/2,y:r.top+r.height/2,tag:e.tagName,text:(e.innerText||'').slice(0,80)};})()";
  const where = await evald(tabId, expr);
  // v5: actionability check (opt-in via args.actionable = true). Catches
  // hidden / occluded / out-of-viewport elements BEFORE we click, so the
  // agent gets a clean reason instead of a "clicked but nothing happened"
  // mystery. Skipped when args.actionable is unset/false to preserve
  // existing behavior.
  if (args && args.actionable) {
    const a = await checkActionable(tabId, sel);
    if (!a || !a.ok) {
      const reason = (a && a.reason) || "not-actionable";
      throw new Error("not actionable (" + reason + ") selector=" + (sel || JSON.stringify({x:args.x,y:args.y})));
    }
  }
  const jitter = humanize ? 6 : 0;
  const cx = where.x + (Math.random() * 2 - 1) * jitter;
  const cy = where.y + (Math.random() * 2 - 1) * jitter;

  if (humanize) {
    await clickHuman(tabId, { x: cx, y: cy });
  } else {
    await cdp(tabId, "Input.dispatchMouseEvent", { type: "mousePressed", x: cx, y: cy, button: "left", clickCount: 1 });
    await cdp(tabId, "Input.dispatchMouseEvent", { type: "mouseReleased", x: cx, y: cy, button: "left", clickCount: 1 });
    setLastMouse(tabId, cx, cy);
  }
  return { clicked: where.tag, at: { x: cx, y: cy }, text: where.text, humanize };
}

async function cmdType(tabId, args) {
  const sel = args && args.selector;
  const text = args && args.text;
  if (!sel) throw new Error("type requires selector");
  if (text == null) throw new Error("type requires text");
  const humanize = !!(args && args.humanize);
  // Focus the field by clicking it (humanized if requested).
  await cmdClick(tabId, { selector: sel, humanize });
  if (humanize) {
    await typeHuman(tabId, String(text));
  } else {
    // Fast path: insertText is one call, instant. Use for non-detect
    // flows (CI, scraping, batch ops).
    await cdp(tabId, "Input.insertText", { text: String(text) });
  }
  return { typed: String(text).length, into: sel, humanize };
}

async function cmdScreenshot(tabId) {
  // Full-page or viewport? Default viewport (matches what the user sees).
  const r = await cdp(tabId, "Page.captureScreenshot", { format: "png" });
  // r.data is base64-encoded PNG.
  const r2 = await fetch(SERVER + "/screenshot", {
    method: "POST",
    headers: await authHeaders(),
    body: JSON.stringify({ tabId, png_b64: r.data }),
  });
  const j = await r2.json();
  if (!j.ok) throw new Error(j.error || "screenshot upload failed");
  return j; // { ok, path, url, size }
}

// ---------- vision: screenshot + readable companion, for VLM callers ----------
//
// This is a *hint* command — the bridge does NOT call any vision model.
// It just returns both the screenshot file path (already saved on disk by
// /screenshot) AND a `readable` dump, so the agent can:
//   1) Use the text dump with a text-only LLM (cheaper, faster)
//   2) Use the screenshot with a VLM (GPT-4V, Claude, GLM-4V, etc.)
//   3) Use both — pass the text as context + the image for visual grounding
//
// The caller is responsible for picking the model and forwarding the bytes.

async function cmdVision(tabId, args) {
  const prompt = (args && args.prompt) || "";
  // 1) Screenshot
  const shot = await cmdScreenshot(tabId);
  // 2) Readable companion (reuse the existing handler)
  const text = await cmdReadable(tabId, {
    maxChars: (args && args.maxChars) || 20000,
    includeA11y: args && args.includeA11y !== false,
    includeForms: true,
    includeConsole: false,
  });
  return {
    prompt: prompt,
    screenshot_path: shot.path,
    screenshot_size: shot.size,
    readable: text,
  };
}

async function cmdKey(tabId, args) {
  if (!args || !args.key) throw new Error("key requires key");
  const k = String(args.key);
  const humanize = !!(args && args.humanize);
  if (humanize) {
    await sleep(jitter(60, 220));
  }
  await cdp(tabId, "Input.dispatchKeyEvent", { type: "keyDown", text: k, unmodifiedText: k, key: k, code: k, windowsVirtualKeyCode: k.charCodeAt(0) || 0 });
  if (humanize) await sleep(jitter(15, 50));
  await cdp(tabId, "Input.dispatchKeyEvent", { type: "keyUp", text: k, unmodifiedText: k, key: k, code: k, windowsVirtualKeyCode: k.charCodeAt(0) || 0 });
  return { pressed: k, humanize };
}

// ---------- v5: accessibility-tree (CDP Accessibility domain) ----------
//
// axtree / axquery use the real Chromium accessibility tree (not the
// page DOM). This is the same tree that screen readers and Playwright's
// ARIA snapshot see, so it works for elements that exist only in the
// shadow DOM, in iframes (when scoped), and as accessibility-only nodes
// (e.g. <div role="button">). It's the right way to ask "what can the
// user actually interact with?" instead of guessing from CSS selectors.
//
// Node shape returned by both methods:
//   { nodeId, ignored, role: {value}, name: {value}, description: {value},
//     value: {value}, properties: [...], childIds: [...],
//     parentId, backendDOMNodeId }

// Flatten CDP a11y nodes into a list of agent-friendly rows. We strip
// the {value} wrappers and ignore generic grouping nodes (like "generic"
// or "none" roles) when interestingOnly is set.
function flattenAxNodes(nodes, interestingOnly) {
  if (!Array.isArray(nodes)) return [];
  const out = [];
  for (const n of nodes) {
    const role = (n.role && n.role.value) || "";
    const name = (n.name && n.name.value) || "";
    if (interestingOnly) {
      // Mirror Chromium's "interesting" heuristic: skip role=generic / none
      // and nodes with no name and no value (pure structural noise).
      const interestingRoles = new Set([
        "button","link","textbox","searchbox","combobox","checkbox","radio",
        "switch","slider","spinbutton","menuitem","menuitemcheckbox","menuitemradio",
        "tab","tabpanel","treeitem","option","progressbar","meter",
        "alert","alertdialog","dialog","tooltip","heading","listitem",
        "row","columnheader","rowheader","cell","grid","table","img",
        "navigation","main","banner","contentinfo","form","region","article",
        "list","listbox","menu","menubar","radiogroup","tablist","toolbar",
        "tree","treegrid","status","timer","marquee","log",
      ]);
      if (!interestingRoles.has(role) && !name) continue;
    }
    const props = {};
    if (Array.isArray(n.properties)) {
      for (const p of n.properties) {
        if (!p || !p.name) continue;
        const v = p.value;
        if (v && typeof v === "object" && "value" in v) props[p.name] = v.value;
        else props[p.name] = v;
      }
    }
    out.push({
      nodeId: n.nodeId,
      parentId: n.parentId || null,
      ignored: !!n.ignored,
      role,
      name,
      description: (n.description && n.description.value) || "",
      value: (n.value && n.value.value) || "",
      backendDOMNodeId: n.backendDOMNodeId || null,
      properties: props,
    });
  }
  return out;
}

async function cmdAxtree(tabId, args) {
  // Accessibility.getFullAXTree — full a11y tree of the document.
  // Use Accessibility.enable first; it's required for the domain.
  await cdp(tabId, "Accessibility.enable").catch(() => {});
  const r = await cdp(tabId, "Accessibility.getFullAXTree");
  const interestingOnly = !!(args && args.interestingOnly);
  const maxRows = Math.max(1, Math.min(5000, (args && args.maxRows) || 1500));
  const rows = flattenAxNodes(r && r.nodes, interestingOnly).slice(0, maxRows);
  return {
    url: await evald(tabId, "location.href"),
    title: await evald(tabId, "document.title"),
    interestingOnly,
    count: rows.length,
    nodes: rows,
  };
}

async function cmdAxquery(tabId, args) {
  // axquery: focused a11y filter, like a "semantic querySelector".
  // Calls Accessibility.getFullAXTree once, then filters client-side by
  // role / name / descendantOf / backendDOMNodeId. We don't use
  // Accessibility.queryAXTree because its nodeId is fussy (requires
  // either the AXNodeId from a prior getFullAXTree call OR objectId
  // from Runtime — and the id space is browser-version-dependent).
  // Client-side filtering is faster, easier to debug, and gives the
  // same result.
  if (!args || (!args.role && !args.name && !args.descendantOf && !args.backendNodeId))
    throw new Error("axquery requires at least one of: role, name, descendantOf, backendNodeId");
  await cdp(tabId, "Accessibility.enable").catch(() => {});
  const r = await cdp(tabId, "Accessibility.getFullAXTree");
  const maxRows = Math.max(1, Math.min(2000, (args && args.maxRows) || 200));
  const wantRole = args.role ? String(args.role).toLowerCase() : null;
  const wantName = args.name ? String(args.name).toLowerCase() : null;
  const wantNameExact = !!args.nameExact;
  // Build parent map for descendantOf filter
  const parentOf = new Map();
  for (const n of (r && r.nodes) || []) {
    if (n.parentId) parentOf.set(String(n.nodeId), String(n.parentId));
  }
  function isDescendantOf(nodeId, targetId) {
    let cur = parentOf.get(String(nodeId));
    while (cur) {
      if (cur === String(targetId)) return true;
      cur = parentOf.get(cur);
    }
    return false;
  }
  // First pass: flatten
  const flat = flattenAxNodes(r && r.nodes, true);
  // Second pass: filter
  const matched = [];
  for (const n of flat) {
    if (wantRole && (n.role || "").toLowerCase() !== wantRole) continue;
    if (wantName) {
      const nm = (n.name || "").toLowerCase();
      if (wantNameExact ? nm !== wantName : !nm.includes(wantName)) continue;
    }
    if (args.descendantOf != null) {
      if (!isDescendantOf(n.nodeId, args.descendantOf)) continue;
    }
    if (args.backendNodeId != null) {
      if (Number(n.backendDOMNodeId) !== Number(args.backendNodeId)) continue;
    }
    matched.push(n);
    if (matched.length >= maxRows) break;
  }
  return { count: matched.length, nodes: matched };
}

// ---------- v5: web-first expect (auto-retry assertion) ----------
//
// expect polls a JS expression in the page until it returns truthy
// (or non-null when asNotNull is set), with a soft timeout. Returns
// the final value, attempt count, and elapsed ms. Use it instead of
// racy "sleep then check" loops. Per the knowledge base: this is the
// Playwright `expect().toBeVisible()` pattern, implemented as a
// single CDP call.
async function cmdExpect(tabId, args) {
  if (!args || !args.code) throw new Error("expect requires code");
  const timeoutMs = Math.max(100, Math.min(60000, args.timeoutMs || 10000));
  const intervalMs = Math.max(50, Math.min(2000, args.intervalMs || 250));
  const asNotNull = !!args.asNotNull;
  const negate = !!args.negate;            // wait for the expression to be FALSY
  const start = Date.now();
  let attempts = 0;
  let lastValue = undefined;
  let lastError = null;
  while (Date.now() - start < timeoutMs) {
    attempts++;
    try {
      const v = await evald(tabId, String(args.code), true, false);
      lastValue = v;
      const truthy = asNotNull ? (v != null) : !!v;
      const ok = negate ? !truthy : truthy;
      if (ok) {
        return { ok: true, attempts, elapsedMs: Date.now() - start, value: v };
      }
    } catch (e) {
      lastError = String(e && e.message || e);
    }
    await sleep(intervalMs);
  }
  return {
    ok: false,
    attempts,
    elapsedMs: Date.now() - start,
    value: lastValue,
    lastError,
    timeoutMs,
    hint: "expression never became " + (negate ? "falsy" : (asNotNull ? "non-null" : "truthy")),
  };
}

// ---------- v5: human-like scroll ----------
//
// Real users don't teleport-scroll. They do a few small wheel events
// (50-150px each), pause to read, then maybe another burst. This
// command mimics that pattern: dispatches N small mouseWheel events
// separated by jittered pauses, with a long pause after the burst.
//
// Modes:
//   - { y: -400 }             scroll up 400px (small delta) in human bursts
//   - { y: 1000 }             scroll down 1000px in human bursts
//   - { to: 1500 }            scroll to absolute Y position (smooth-ish)
//   - { bottom: true }        scroll to the bottom of the document
//   - { selector: "h2.reviews" } scroll element into view (uses scrollIntoView)
async function cmdScroll(tabId, args) {
  args = args || {};
  await ensureAttached(tabId);
  if (args.selector) {
    return evald(tabId, "(function(sel){" +
      "const e=document.querySelector(sel);" +
      "if(!e)return{ok:false,reason:'not-found'};" +
      "e.scrollIntoView({block:'center',behavior:'smooth'});" +
      "return{ok:true,scrolledTo:sel,y:window.scrollY};})(" + JSON.stringify(args.selector) + ")");
  }
  if (args.bottom) {
    await evald(tabId, "window.scrollTo({top:document.documentElement.scrollHeight,behavior:'smooth'})");
    return { scrolledTo: "bottom", finalY: await evald(tabId, "window.scrollY") };
  }
  if (args.top) {
    await evald(tabId, "window.scrollTo({top:0,behavior:'smooth'})");
    return { scrolledTo: "top", finalY: await evald(tabId, "window.scrollY") };
  }
  let targetY = null;
  let delta = 0;
  if (typeof args.to === "number") {
    const cur = await evald(tabId, "window.scrollY");
    targetY = args.to;
    delta = targetY - (cur || 0);
  } else if (typeof args.y === "number") {
    delta = args.y;
  } else {
    throw new Error("scroll requires {y}, {to}, {bottom}, {top}, or {selector}");
  }
  // Dispatch the scroll in small human-like bursts.
  // Each burst: 3-7 small wheel events of 40-110px each, then a 200-700ms pause.
  // Total delta: ~delta
  const direction = delta >= 0 ? 1 : -1;
  const absTotal = Math.abs(delta);
  const burstCount = Math.max(1, Math.ceil(absTotal / 400)); // a burst per ~400px
  let remaining = absTotal;
  let totalWaited = 0;
  const perBurstPauses = [];
  for (let b = 0; b < burstCount && remaining > 0; b++) {
    const burstSize = Math.min(remaining, 200 + Math.random() * 250); // 200-450 per burst
    const wheelCount = 3 + Math.floor(Math.random() * 4); // 3-6 events
    const perWheel = burstSize / wheelCount;
    for (let w = 0; w < wheelCount; w++) {
      const d = perWheel * direction;
      // Get viewport dims from the page (not the SW's global `window`)
      const dims = await evald(tabId, "({w:window.innerWidth,h:window.innerHeight})");
      await cdp(tabId, "Input.dispatchMouseEvent", {
        type: "mouseWheel",
        x: Math.floor((dims && dims.w) / 2) || 640,
        y: Math.floor((dims && dims.h) / 2) || 400,
        deltaX: 0,
        deltaY: d,
        wheelDeltaX: 0,
        wheelDeltaY: d,
        modifiers: 0,
        pointerType: "mouse",
        bubbles: true,
        cancelable: true,
      });
      await sleep(jitter(20, 60));
      remaining -= perWheel;
    }
    const pause = jitter(180, 600);
    perBurstPauses.push(Math.round(pause));
    await sleep(pause);
    totalWaited += pause;
  }
  // Ensure we actually moved (some pages have their own scroll handlers)
  const finalY = await evald(tabId, "window.scrollY");
  return {
    requestedDelta: delta,
    burstCount,
    pauses: perBurstPauses,
    finalY,
    finalDocH: await evald(tabId, "document.documentElement.scrollHeight"),
  };
}
//
// Computer Use / desktop automation tools think in coordinates and
// move. The bridge has had implicit moves inside click/type, but
// agents sometimes want to say "hover at (x,y) for 2s and report
// what shows up under the cursor." This is the bridge command for that.
async function cmdMove(tabId, args) {
  if (!args || typeof args.x !== "number" || typeof args.y !== "number")
    throw new Error("move requires {x, y}");
  const humanize = args.humanize !== false; // default ON
  if (humanize) {
    const last = getLastMouse(tabId);
    await moveMouseHuman(tabId, last.x, last.y, args.x, args.y);
  } else {
    await cdp(tabId, "Input.dispatchMouseEvent", {
      type: "mouseMoved", x: args.x, y: args.y, button: "none",
    });
  }
  setLastMouse(tabId, args.x, args.y);
  return { x: args.x, y: args.y, humanize };
}

// ---------- v5: actionability check ----------
//
// Before clicking, optionally verify the target is actually clickable:
//   - has a non-zero bounding box
//   - not display:none / visibility:hidden / opacity:0
//   - inside the viewport (with optional tolerance)
//   - elementFromPoint at the center matches the target (or is a
//     descendant of it) — catches overlap by sticky headers, modals,
//     dev-tool overlays, etc.
// Returns { ok, reason? }. Used by cmdClick when args.actionable = true.
async function checkActionable(tabId, selector) {
  if (!selector) return { ok: true, reason: "no-selector-skipped" };
  const expr =
    "(()=>{const e=document.querySelector(" + JSON.stringify(selector) + ");" +
    "if(!e)return{ok:false,reason:'not-found',tag:null};" +
    "const r=e.getBoundingClientRect();" +
    "if(r.width===0||r.height===0)return{ok:false,reason:'zero-size',rect:r};" +
    "const cs=getComputedStyle(e);" +
    "if(cs.display==='none')return{ok:false,reason:'display-none',tag:e.tagName};" +
    "if(cs.visibility==='hidden')return{ok:false,reason:'visibility-hidden',tag:e.tagName};" +
    "if(parseFloat(cs.opacity||'1')===0)return{ok:false,reason:'opacity-0',tag:e.tagName};" +
    "if(cs.pointerEvents==='none')return{ok:false,reason:'pointer-events-none',tag:e.tagName};" +
    "if(r.bottom<0||r.right<0||r.top>innerHeight||r.left>innerWidth)" +
    "  return{ok:false,reason:'out-of-viewport',rect:r,vp:{w:innerWidth,h:innerHeight}};" +
    "const cx=r.left+r.width/2,cy=r.top+r.height/2;" +
    "const top=document.elementFromPoint(cx,cy);" +
    "if(!top||top===document.body||top===document.documentElement)" +
    "  return{ok:false,reason:'covered-by-body',tag:e.tagName,topTag:top&&top.tagName};" +
    "if(top!==e&&!e.contains(top))" +
    "  return{ok:false,reason:'occluded',tag:e.tagName,topTag:top.tagName,topId:top.id||null};" +
    "return{ok:true,tag:e.tagName,rect:r,center:{x:cx,y:cy}};})()";
  return evald(tabId, expr);
}

// ---------- v5: trace bundle (auto-save for offline debugging) ----------
//
// When something goes wrong, save a self-contained bundle to disk:
//   - screenshot (full viewport PNG)
//   - URL + title + viewport + active element summary
//   - focused a11y tree (interesting nodes only)
//   - the last 20 console messages captured via Runtime.consoleAPICalled
// Saves to the path returned by the server's /trace endpoint. The agent
// can then attach the trace to a bug report or replay steps.
const consoleBuffer = new Map(); // tabId -> [{type, text, ts}, ...]
function recordConsole(tabId, type, text) {
  if (!consoleBuffer.has(tabId)) consoleBuffer.set(tabId, []);
  const buf = consoleBuffer.get(tabId);
  buf.push({ type, text, ts: Date.now() });
  if (buf.length > 200) buf.splice(0, buf.length - 200);
}

async function ensureRuntimeListener(tabId) {
  if (ensureRuntimeListener._cache && ensureRuntimeListener._cache.has(tabId)) return;
  if (!ensureRuntimeListener._cache) ensureRuntimeListener._cache = new Set();
  try {
    await cdp(tabId, "Runtime.enable").catch(() => {});
    await cdp(tabId, "Log.enable").catch(() => {});
    ensureRuntimeListener._cache.add(tabId);
  } catch (_) {}
}

async function cmdTrace(tabId, args) {
  await ensureRuntimeListener(tabId);
  // 1) screenshot
  const cap = await cdp(tabId, "Page.captureScreenshot", { format: "png" });
  const screenshotB64 = cap.data;
  // 2) page meta
  const meta = await evald(tabId, "({url:location.href,title:document.title,vp:{w:innerWidth,h:innerHeight,scrollY:scrollY,docH:document.documentElement.scrollHeight}})");
  const focus = await evald(tabId, "(function(){const a=document.activeElement;if(!a||a===document.body)return null;return{tag:a.tagName,id:a.id||null,role:a.getAttribute&&a.getAttribute('role')||null,value:a.value!==undefined?String(a.value).slice(0,200):null,placeholder:a.placeholder||null,text:(a.innerText||'').trim().slice(0,200)};})()");
  // 3) a11y tree (interesting only)
  let ax = null;
  try {
    await cdp(tabId, "Accessibility.enable").catch(() => {});
    const r = await cdp(tabId, "Accessibility.getFullAXTree");
    ax = flattenAxNodes(r && r.nodes, true).slice(0, 400);
  } catch (e) { ax = { error: String(e && e.message || e) }; }
  // 4) recent console
  const console = (consoleBuffer.get(tabId) || []).slice(-20);
  // 5) post the bundle to the server
  const r2 = await fetch(SERVER + "/trace", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      tabId,
      meta,
      focus,
      ax,
      console,
      png_b64: screenshotB64,
      note: (args && args.note) || "",
    }),
  });
  const j = await r2.json();
  if (!j.ok) throw new Error(j.error || "trace upload failed");
  return j; // { ok, path, dir, files: [...] }
}

// ---------- v5: hover ----------
//
// Move mouse to an element's center with a human trajectory, then
// dispatch mouseenter / mouseover / mousemove DOM events so hover-
// dependent UI (tooltips, dropdowns) appears.
async function cmdHover(tabId, args) {
  const sel = args && args.selector;
  if (!sel) throw new Error("hover requires selector");
  const where = await evald(tabId,
    "(()=>{const e=document.querySelector(" + JSON.stringify(sel) + ");" +
    "if(!e)throw new Error('no match for '+" + JSON.stringify(sel) + ");" +
    "e.scrollIntoView({block:'center',behavior:'instant'});" +
    "const r=e.getBoundingClientRect();" +
    "return {x:r.left+r.width/2,y:r.top+r.height/2,tag:e.tagName};})()");
  const last = getLastMouse(tabId);
  await moveMouseHuman(tabId, last.x, last.y, where.x, where.y);
  await sleep(jitter(100, 300));
  await evald(tabId, "(function(sel){" +
    "const e=document.querySelector(sel);" +
    "if(!e)return false;" +
    "const r=e.getBoundingClientRect();" +
    "const cx=r.left+r.width/2,cy=r.top+r.height/2;" +
    "const opts={bubbles:true,cancelable:true,clientX:cx,clientY:cy,button:0};" +
    "e.dispatchEvent(new MouseEvent('mouseenter',opts));" +
    "e.dispatchEvent(new MouseEvent('mouseover',opts));" +
    "e.dispatchEvent(new MouseEvent('mousemove',opts));" +
    "return true;})(" + JSON.stringify(sel) + ")");
  setLastMouse(tabId, where.x, where.y);
  return { hovered: where.tag, at: { x: where.x, y: where.y } };
}

// ---------- v5: drag ----------
//
// Drag from one element to another (or from coords to coords).
// Simulates mousedown → mousemove with jitter → mouseup, plus
// the full set of DOM drag events (dragstart, drag, dragenter,
// dragover, drop, dragend).
async function cmdDrag(tabId, args) {
  if (!args) throw new Error("drag requires arguments");
  let fromX, fromY, toX, toY;
  const fromSel = args.fromSelector || null;
  const toSel = args.toSelector || null;
  if (fromSel && toSel) {
    const from = await evald(tabId, "(function(sel){" +
      "const e=document.querySelector(sel);" +
      "if(!e)throw new Error('from not found: '+sel);" +
      "e.scrollIntoView({block:'center',behavior:'instant'});" +
      "const r=e.getBoundingClientRect();" +
      "return {x:r.left+r.width/2,y:r.top+r.height/2};})(" + JSON.stringify(fromSel) + ")");
    const to = await evald(tabId, "(function(sel){" +
      "const e=document.querySelector(sel);" +
      "if(!e)throw new Error('to not found: '+sel);" +
      "e.scrollIntoView({block:'center',behavior:'instant'});" +
      "const r=e.getBoundingClientRect();" +
      "return {x:r.left+r.width/2,y:r.top+r.height/2};})(" + JSON.stringify(toSel) + ")");
    fromX = from.x; fromY = from.y; toX = to.x; toY = to.y;
  } else if (typeof args.fromX === "number" && typeof args.fromY === "number" &&
    typeof args.toX === "number" && typeof args.toY === "number") {
    fromX = args.fromX; fromY = args.fromY; toX = args.toX; toY = args.toY;
  } else {
    throw new Error("drag requires {fromSelector,toSelector} or {fromX,fromY,toX,toY}");
  }
  // mousedown at source
  await cdp(tabId, "Input.dispatchMouseEvent", {
    type: "mousePressed", x: fromX, y: fromY, button: "left", clickCount: 1,
  });
  await sleep(jitter(50, 150));
  // dragstart on source element
  if (fromSel) {
    await evald(tabId, "(function(sel){" +
      "const e=document.querySelector(sel);" +
      "if(!e)return;" +
      "e.dispatchEvent(new DragEvent('dragstart',{bubbles:true,dataTransfer:new DataTransfer()}));" +
      "})(" + JSON.stringify(fromSel) + ")");
  }
  // human-like trajectory from source to destination
  await moveMouseHuman(tabId, fromX, fromY, toX, toY);
  // dragover / dragenter on destination
  if (toSel) {
    await evald(tabId, "(function(sel){" +
      "const e=document.querySelector(sel);" +
      "if(!e)return;" +
      "const r=e.getBoundingClientRect();" +
      "const cx=r.left+r.width/2,cy=r.top+r.height/2;" +
      "const dt=new DataTransfer();" +
      "e.dispatchEvent(new DragEvent('dragover',{bubbles:true,dataTransfer:dt,clientX:cx,clientY:cy}));" +
      "e.dispatchEvent(new DragEvent('dragenter',{bubbles:true,dataTransfer:dt,clientX:cx,clientY:cy}));" +
      "})(" + JSON.stringify(toSel) + ")");
  }
  await sleep(jitter(50, 150));
  // mouseup at destination
  await cdp(tabId, "Input.dispatchMouseEvent", {
    type: "mouseReleased", x: toX, y: toY, button: "left", clickCount: 1,
  });
  // drop on destination, dragend on source
  await evald(tabId, "(function(fromSel,toSel){" +
    "if(toSel){" +
    "  const e=document.querySelector(toSel);" +
    "  if(e){" +
    "    const r=e.getBoundingClientRect();" +
    "    const cx=r.left+r.width/2,cy=r.top+r.height/2;" +
    "    const dt=new DataTransfer();" +
    "    e.dispatchEvent(new DragEvent('drop',{bubbles:true,dataTransfer:dt,clientX:cx,clientY:cy}));" +
    "  }" +
    "}" +
    "if(fromSel){" +
    "  const e=document.querySelector(fromSel);" +
    "  if(e)e.dispatchEvent(new DragEvent('dragend',{bubbles:true,dataTransfer:new DataTransfer()}));" +
    "}" +
    "})(" + JSON.stringify(fromSel) + "," + JSON.stringify(toSel) + ")");
  setLastMouse(tabId, toX, toY);
  return { from: { x: fromX, y: fromY }, to: { x: toX, y: toY } };
}

// ---------- v5: select ----------
//
// Select an option in a <select> element by value or index.
async function cmdSelect(tabId, args) {
  if (!args || !args.selector) throw new Error("select requires selector");
  const sel = args.selector;
  let expr;
  if (args.value !== undefined) {
    expr = "(function(sel,val){" +
      "const e=document.querySelector(sel);" +
      "if(!e)throw new Error('select not found: '+sel);" +
      "if(e.tagName!=='SELECT')throw new Error('not a <select>: '+e.tagName);" +
      "e.value=String(val);" +
      "e.dispatchEvent(new Event('input',{bubbles:true}));" +
      "e.dispatchEvent(new Event('change',{bubbles:true}));" +
      "return {selected:e.value,selectedIndex:e.selectedIndex};})(" +
      JSON.stringify(sel) + "," + JSON.stringify(String(args.value)) + ")";
  } else if (typeof args.index === "number") {
    expr = "(function(sel,idx){" +
      "const e=document.querySelector(sel);" +
      "if(!e)throw new Error('select not found: '+sel);" +
      "if(e.tagName!=='SELECT')throw new Error('not a <select>: '+e.tagName);" +
      "if(idx<0||idx>=e.options.length)throw new Error('index out of range: '+idx);" +
      "e.selectedIndex=idx;" +
      "e.dispatchEvent(new Event('input',{bubbles:true}));" +
      "e.dispatchEvent(new Event('change',{bubbles:true}));" +
      "return {selected:e.value,selectedIndex:e.selectedIndex};})(" +
      JSON.stringify(sel) + "," + args.index + ")";
  } else {
    throw new Error("select requires value or index");
  }
  return evald(tabId, expr);
}

// ---------- v5: cookies ----------
//
// Get / set / delete cookies via the CDP Network domain.
async function cmdCookies(tabId, args) {
  if (!args || !args.action) throw new Error("cookies requires action");
  await cdp(tabId, "Network.enable").catch(() => {});
  const url = await evald(tabId, "location.href");
  switch (args.action) {
    case "get": {
      const r = await cdp(tabId, "Network.getCookies", { urls: [url] });
      const all = r.cookies || [];
      if (args.name) {
        const match = all.find(c => c.name === args.name);
        return match || null;
      }
      return all;
    }
    case "set": {
      if (!args.name || args.value === undefined) throw new Error("cookies set requires name and value");
      const params = { name: args.name, value: String(args.value), url: url };
      if (args.domain) params.domain = args.domain;
      if (args.path) params.path = args.path;
      if (args.secure !== undefined) params.secure = args.secure;
      if (args.httpOnly !== undefined) params.httpOnly = args.httpOnly;
      if (args.sameSite) params.sameSite = args.sameSite;
      if (args.expires !== undefined) params.expires = args.expires;
      const r = await cdp(tabId, "Network.setCookie", params);
      return { success: r.success };
    }
    case "delete": {
      if (!args.name) throw new Error("cookies delete requires name");
      const params = { name: args.name, url: url };
      if (args.domain) params.domain = args.domain;
      if (args.path) params.path = args.path;
      await cdp(tabId, "Network.deleteCookies", params);
      return { deleted: args.name };
    }
    default:
      throw new Error("cookies action must be get, set, or delete");
  }
}

// ---------- v5: upload ----------
//
// Set files on a <input type="file"> element via CDP
// DOM.setFileInputFiles. Paths must be absolute on the host
// filesystem.
async function cmdUpload(tabId, args) {
  if (!args || !args.selector) throw new Error("upload requires selector");
  if (!args.files || !Array.isArray(args.files) || args.files.length === 0) {
    throw new Error("upload requires files array");
  }
  // Get a remote object reference for the file input element
  const r = await cdp(tabId, "Runtime.evaluate", {
    expression: "(function(sel){" +
      "const e=document.querySelector(sel);" +
      "if(!e)throw new Error('input not found: '+sel);" +
      "if(e.tagName!=='INPUT'||e.type!=='file')throw new Error('element is not a file input');" +
      "return e;})(" + JSON.stringify(args.selector) + ")",
  });
  if (!r.result || !r.result.objectId) {
    throw new Error("could not get file input element");
  }
  await cdp(tabId, "DOM.setFileInputFiles", {
    files: args.files,
    objectId: r.result.objectId,
  });
  // Dispatch change event so page listeners fire
  await evald(tabId, "(function(sel){" +
    "const e=document.querySelector(sel);" +
    "if(e)e.dispatchEvent(new Event('change',{bubbles:true}));" +
    "})(" + JSON.stringify(args.selector) + ")");
  return { uploaded: args.files };
}

// ---------- v5: back / forward ----------
async function cmdBack(tabId) {
  await evald(tabId, "history.back()");
  return { navigated: "back" };
}
async function cmdForward(tabId) {
  await evald(tabId, "history.forward()");
  return { navigated: "forward" };
}

// ---------- v5: refresh ----------
async function cmdRefresh(tabId, args) {
  const hard = !!(args && args.hard);
  await cdp(tabId, "Page.reload", { ignoreCache: hard });
  return { refreshed: true, hard };
}

// ---------- v5: console ----------
async function cmdConsole(tabId, args) {
  await ensureRuntimeListener(tabId);
  const count = (args && args.count) || 50;
  const buf = consoleBuffer.get(tabId) || [];
  return { messages: buf.slice(-count), total: buf.length };
}

// Capture console + log messages from the page (Runtime domain).
// This listener is registered the first time a trace/expect runs.
chrome.debugger.onEvent.addListener((source, method, params) => {
  if (!source || source.tabId == null) return;
  const tabId = source.tabId;
  if (method === "Runtime.consoleAPICalled" && params && params.args) {
    const text = (params.args || []).map(a => {
      if (a.value !== undefined) return String(a.value);
      if (a.description) return a.description;
      return JSON.stringify(a);
    }).join(" ");
    recordConsole(tabId, params.type || "log", text);
  } else if (method === "Log.entryAdded" && params && params.entry) {
    recordConsole(tabId, params.entry.level || "log", params.entry.text || "");
  } else if (method === "Runtime.exceptionThrown" && params && params.exceptionDetails) {
    const d = params.exceptionDetails;
    recordConsole(tabId, "exception", (d.exception && d.exception.description) || d.text || "exception");
  }
});

const COMMANDS = {
  navigate: cmdNavigate,
  eval: cmdEval,
  title: cmdTitle,
  url: cmdUrl,
  html: cmdHtml,
  snippet: cmdSnippet,
  readable: cmdReadable,
  query: cmdQuery,
  click: cmdClick,
  type: cmdType,
  screenshot: cmdScreenshot,
  vision: cmdVision,
  key: cmdKey,
  see: cmdSee,
  // v5 additions
  axtree: cmdAxtree,
  axquery: cmdAxquery,
  expect: cmdExpect,
  move: cmdMove,
  scroll: cmdScroll,
  trace: cmdTrace,
  // new commands
  hover: cmdHover,
  drag: cmdDrag,
  select: cmdSelect,
  cookies: cmdCookies,
  upload: cmdUpload,
  back: cmdBack,
  forward: cmdForward,
  refresh: cmdRefresh,
  console: cmdConsole,
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
      headers: await authHeaders(),
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
    // Long-poll: server blocks up to POLL_WAIT_MS waiting for a command.
    // We add a small client-side margin so the HTTP timeout fires after
    // the server's, not before.
    const token = await getToken();
    const url = SERVER + "/poll?ext=" + encodeURIComponent(EXT_ID) + "&wait=" + POLL_WAIT_MS
      + (token ? "&token=" + encodeURIComponent(token) : "");
    const r = await fetch(url, { signal: AbortSignal.timeout(POLL_WAIT_MS + 5000) });
    if (!r.ok) return;
    const cmd = await r.json();
    if (!cmd || !cmd.id) return;
    let payload;
    try {
      if (cmd.type === "ping") {
        const pinned = await getDesignatedTabId();
        payload = { id: cmd.id, ok: true, value: { pong: true, ext: EXT_ID, attached: Array.from(attachedTabs), pinnedTabId: pinned } };
      } else if (cmd.type === "tabs") {
        // List is OK to return without pinning (read-only metadata).
        const pinned = await getDesignatedTabId();
        const tabs = await chrome.tabs.query({});
        payload = { id: cmd.id, ok: true, value: tabs.map(t => ({ id: t.id, url: t.url, title: t.title, active: t.active, attached: attachedTabs.has(t.id), pinned: pinned === t.id })) };
      } else if (cmd.type === "active_tab") {
        const [t] = await chrome.tabs.query({ active: true, currentWindow: true });
        const pinned = await getDesignatedTabId();
        payload = { id: cmd.id, ok: true, value: t ? { id: t.id, url: t.url, title: t.title, attached: attachedTabs.has(t.id), pinned: pinned === t.id } : null };
      } else if (cmd.type === "attach") {
        // Even `attach` is now gated by the pinned-tab rule — you can only
        // attach (open a CDP session on) the pinned tab.
        const targetId = await resolveTargetTabId(cmd);
        await ensureAttached(targetId);
        payload = { id: cmd.id, ok: true, value: { attached: true, tabId: targetId, pinned: true } };
      } else if (cmd.type === "detach") {
        const targetId = await resolveTargetTabId(cmd);
        if (attachedTabs.has(targetId)) {
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
        // SECURITY: every command resolves its target through resolveTargetTabId,
        // which enforces the pinned-tab rule. No more "follow the active tab"
        // and no more "server can override tabId".
        const targetId = await resolveTargetTabId(cmd);
        const timeoutMs = (cmd.args && cmd.args.timeout) || 30000;
        const value = await new Promise((resolve, reject) => {
          const timer = setTimeout(() => reject(new Error("command timed out after " + timeoutMs + "ms")), timeoutMs);
          handler(targetId, cmd.args).then(
            v => { clearTimeout(timer); resolve(v); },
            e => { clearTimeout(timer); reject(e); }
          );
        });
        payload = { id: cmd.id, ok: true, value };
      }
    } catch (e) {
      payload = { id: cmd.id, ok: false, error: String(e && e.message || e) };
    }
    await fetch(SERVER + "/result", {
      method: "POST",
      headers: await authHeaders(),
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
