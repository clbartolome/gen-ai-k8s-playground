const statusBadge = document.getElementById("status-badge");
const durationEl = document.getElementById("duration");
const startedAtEl = document.getElementById("started-at");
const messageEl = document.getElementById("message");
const responseEl = document.getElementById("response");
const errorEl = document.getElementById("error");
const hintEl = document.getElementById("hint");

function formatTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString();
}

function formatDuration(ms) {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function setBadge(status) {
  statusBadge.textContent = status;
  statusBadge.className = `badge badge-${status}`;
}

function render(data) {
  setBadge(data.status);
  errorEl.hidden = true;

  if (data.status === "idle") {
    hintEl.textContent = "Send a message from the chat to see activity here.";
    startedAtEl.textContent = "—";
    durationEl.textContent = "—";
    messageEl.textContent = "No requests yet.";
    responseEl.textContent = "—";
    responseEl.classList.add("muted");
    return;
  }

  hintEl.textContent = "Showing the last request handled by this agent.";
  startedAtEl.textContent = formatTime(data.started_at);
  durationEl.textContent = formatDuration(data.duration_ms);
  messageEl.textContent = data.message || "—";

  if (data.status === "processing") {
    responseEl.textContent = "Processing...";
    responseEl.classList.add("muted");
    return;
  }

  if (data.status === "error") {
    responseEl.textContent = "—";
    responseEl.classList.add("muted");
    errorEl.hidden = false;
    errorEl.textContent = data.error || "Unknown error";
    return;
  }

  responseEl.textContent = data.response || "—";
  responseEl.classList.remove("muted");
}

async function refresh() {
  try {
    const res = await fetch(`/debug/status?t=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) throw new Error(`Status request failed (${res.status})`);
    render(await res.json());
  } catch (err) {
    setBadge("error");
    hintEl.textContent = "Could not reach the agent status API.";
    errorEl.hidden = false;
    errorEl.textContent = err.message;
  }
}

refresh();
setInterval(refresh, 500);
