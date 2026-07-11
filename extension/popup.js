// WebBridge popup — shows server health + last reported page state.
// Uses sendMessage (not connect) so it works even when the SW is suspended.
const SERVER = "http://127.0.0.1:9876";

async function refresh() {
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
}

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

