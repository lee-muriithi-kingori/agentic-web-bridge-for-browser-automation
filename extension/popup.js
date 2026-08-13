// WebBridge popup — shows server health, last reported page state, and
// lets the user PIN (designate) exactly one tab for the bridge to drive.
// The pinned-tab id is persisted in chrome.storage.local and read by the
// background service worker on every command.

const SERVER = "http://127.0.0.1:9876";

async function refresh() {
  // Server state
  try {
    const r = await fetch(SERVER + "/state");
    const s = await r.json();
    document.getElementById("srv").textContent = "ok";
    document.getElementById("srv").className = "val ok";
    document.getElementById("url").textContent = s.url || "(no active tab)";
    document.getElementById("title").textContent = s.title || "—";
    document.getElementById("ext").textContent = s.extId || "—";
  } catch (e) {
    document.getElementById("srv").textContent = "down";
    document.getElementById("srv").className = "val err";
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

document.getElementById("ping").addEventListener("click", async () => {
  const id = "popup-" + Date.now();
  await fetch(SERVER + "/cmd", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, type: "ping", args: {} }),
  });
  const r = await fetch(SERVER + "/result?id=" + id + "&wait=3000");
  const j = await r.json();
  alert(j && j.ok && j.result && j.result.value ? "Pong from " + j.result.value.ext : "no pong");
});

refresh();
setInterval(refresh, 1500);
