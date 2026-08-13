// WebBridge popup — shows server health, last reported page state, lets
// the user PIN (designate) exactly one tab for the bridge to drive, and
// sets the auth token when the server has WEBBRIDGE_TOKEN enabled.

const SERVER = "http://127.0.0.1:9876";

async function getToken() {
  const { token } = await chrome.storage.local.get("token");
  return token || "";
}

async function authHeaders() {
  const token = await getToken();
  const h = { "Content-Type": "application/json" };
  if (token) h["Authorization"] = "Bearer " + token;
  return h;
}

async function refresh() {
  // Server state (health + version are public; no token needed)
  let authEnabled = false;
  try {
    const r = await fetch(SERVER + "/health");
    const s = await r.json();
    document.getElementById("srv").textContent = "ok";
    document.getElementById("srv").className = "val ok";
    document.getElementById("url").textContent = s.url || "(no active tab)";
    document.getElementById("title").textContent = s.title || "—";
    document.getElementById("ext").textContent = s.extId || "—";
    authEnabled = !!s.auth_enabled;
    document.getElementById("authstatus").textContent = authEnabled ? "ENABLED" : "disabled";
    document.getElementById("authstatus").className = authEnabled ? "val warn" : "val";
  } catch (e) {
    document.getElementById("srv").textContent = "down";
    document.getElementById("srv").className = "val err";
  }

  // Token input — show/hide based on whether auth is enabled
  const tokenRow = document.getElementById("tokenrow");
  if (authEnabled) {
    tokenRow.style.display = "";
  } else {
    tokenRow.style.display = "none";
  }

  // Pinned-tab state (from local storage — survives SW restarts)
  try {
    const { designatedTabId, designatedUrl, designatedTitle, designatedAt } =
      await chrome.storage.local.get([
        "designatedTabId", "designatedUrl", "designatedTitle", "designatedAt"
      ]);
    if (designatedTabId != null) {
      const age = designatedAt ? ` (${Math.round((Date.now() - designatedAt) / 1000)}s ago)` : "";
      document.getElementById("pin").textContent = `#${designatedTabId}${age}`;
      document.getElementById("pin").className = "val ok";
      document.getElementById("pinurl").textContent = designatedTitle
        ? `${designatedTitle} — ${designatedUrl || ""}`
        : (designatedUrl || "—");
    } else {
      document.getElementById("pin").textContent = "none";
      document.getElementById("pin").className = "val warn";
      document.getElementById("pinurl").textContent = "—";
    }
  } catch (e) {
    document.getElementById("pin").textContent = "error";
    document.getElementById("pin").className = "val err";
  }

  // Token field current value
  const token = await getToken();
  document.getElementById("token").value = token;
}

document.getElementById("designate").addEventListener("click", async () => {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) {
      alert("No active tab to pin.");
      return;
    }
    if (!/^https?:/.test(tab.url || "")) {
      const ok = confirm(
        `This tab's URL is "${tab.url}".\n` +
        `WebBridge works best on http(s) pages. Pin it anyway?`
      );
      if (!ok) return;
    }
    await chrome.storage.local.set({
      designatedTabId: tab.id,
      designatedUrl: tab.url,
      designatedTitle: tab.title,
      designatedAt: Date.now(),
    });
    // Tell the SW so it can react immediately (don't wait for next poll).
    try {
      await chrome.runtime.sendMessage({ type: "designated", tabId: tab.id });
    } catch (_) {}
    refresh();
  } catch (e) {
    alert("Failed to pin tab: " + (e.message || e));
  }
});

document.getElementById("unpin").addEventListener("click", async () => {
  await chrome.storage.local.remove([
    "designatedTabId", "designatedUrl", "designatedTitle", "designatedAt"
  ]);
  try {
    await chrome.runtime.sendMessage({ type: "unpinned" });
  } catch (_) {}
  refresh();
});

document.getElementById("savetoken").addEventListener("click", async () => {
  const token = document.getElementById("token").value.trim();
  if (token) {
    await chrome.storage.local.set({ token });
  } else {
    await chrome.storage.local.remove("token");
  }
  // Tell the SW so it picks up the new token immediately.
  try {
    await chrome.runtime.sendMessage({ type: "token-updated" });
  } catch (_) {}
  alert("Token saved.");
});

document.getElementById("ping").addEventListener("click", async () => {
  const id = "popup-" + Date.now();
  await fetch(SERVER + "/cmd", {
    method: "POST",
    headers: await authHeaders(),
    body: JSON.stringify({ id, type: "ping", args: {} }),
  });
  const r = await fetch(SERVER + "/result?id=" + id + "&wait=3000", {
    headers: await authHeaders(),
  });
  const j = await r.json();
  alert(j && j.ok && j.result && j.result.value ? "Pong from " + j.result.value.ext : "no pong");
});

refresh();
setInterval(refresh, 1500);
