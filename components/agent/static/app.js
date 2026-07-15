const statusBadge = document.getElementById("status-badge");
const durationEl = document.getElementById("duration");
const startedAtEl = document.getElementById("started-at");
const messageEl = document.getElementById("message");
const responseEl = document.getElementById("response");
const errorEl = document.getElementById("error");
const hintEl = document.getElementById("hint");

const llmStatusBadge = document.getElementById("llm-status-badge");
const llmStartedAtEl = document.getElementById("llm-started-at");
const llmDurationEl = document.getElementById("llm-duration");
const llmFinishedAtEl = document.getElementById("llm-finished-at");
const llmCharsEl = document.getElementById("llm-chars");
const llmModelEl = document.getElementById("llm-model");
const llmEndpointEl = document.getElementById("llm-endpoint");
const llmTimeoutEl = document.getElementById("llm-timeout");

let lastData = null;

function formatTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString();
}

function formatDuration(ms) {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function elapsedMs(iso) {
  if (!iso) return null;
  return Math.max(0, Date.now() - new Date(iso).getTime());
}

function setBadge(el, status, prefix = "badge") {
  el.textContent = status;
  el.className = `${prefix} ${prefix}-${status}`;
}

function formatChars(inChars, outChars) {
  const input = inChars == null ? "—" : `${inChars} chars`;
  const output = outChars == null ? "—" : `${outChars} chars`;
  return `${input} → ${output}`;
}

function renderLlmConfig(data) {
  llmModelEl.textContent = data.llm_model || "not configured";
  llmEndpointEl.textContent = data.llm_endpoint || "not configured";
  llmTimeoutEl.textContent = data.llm_timeout_s != null ? `${data.llm_timeout_s} s` : "—";
}

function renderLlmCall(data) {
  const llmStatus = data.llm_status || "idle";
  setBadge(llmStatusBadge, `llm-${llmStatus}`, "badge");

  llmStartedAtEl.textContent = formatTime(data.llm_started_at);

  if (llmStatus === "calling" && data.llm_started_at) {
    llmDurationEl.textContent = `${formatDuration(elapsedMs(data.llm_started_at))} (in progress)`;
    llmDurationEl.classList.add("live");
    llmFinishedAtEl.textContent = "—";
  } else {
    llmDurationEl.classList.remove("live");
    llmDurationEl.textContent = formatDuration(data.llm_duration_ms);
    llmFinishedAtEl.textContent = formatTime(data.llm_finished_at);
  }

  llmCharsEl.textContent = formatChars(data.message_chars, data.response_chars);
  renderLlmConfig(data);
}

function renderRequestDuration(data) {
  if (data.status === "processing" && data.started_at) {
    durationEl.textContent = `${formatDuration(elapsedMs(data.started_at))} (in progress)`;
    durationEl.classList.add("live");
    return;
  }
  durationEl.classList.remove("live");
  durationEl.textContent = formatDuration(data.duration_ms);
}

function render(data) {
  lastData = data;
  setBadge(statusBadge, data.status);
  errorEl.hidden = true;
  renderLlmCall(data);

  if (data.status === "idle") {
    hintEl.textContent = "Send a message from the chat to see activity here.";
    startedAtEl.textContent = "—";
    durationEl.textContent = "—";
    durationEl.classList.remove("live");
    messageEl.textContent = "No requests yet.";
    responseEl.textContent = "—";
    responseEl.classList.add("muted");
    return;
  }

  hintEl.textContent = "Showing the last request handled by this agent.";
  startedAtEl.textContent = formatTime(data.started_at);
  renderRequestDuration(data);
  messageEl.textContent = data.message || "—";

  if (data.status === "processing") {
    responseEl.textContent = data.step === "calling_llm"
      ? "Waiting for LLM response..."
      : "Preparing request...";
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
    setBadge(statusBadge, "error");
    hintEl.textContent = "Could not reach the agent status API.";
    errorEl.hidden = false;
    errorEl.textContent = err.message;
  }
}

function tickLiveTimers() {
  if (!lastData) return;
  if (lastData.status === "processing") {
    renderRequestDuration(lastData);
  }
  if (lastData.llm_status === "calling") {
    renderLlmCall(lastData);
  }
}

refresh();
setInterval(refresh, 500);
setInterval(tickLiveTimers, 250);
