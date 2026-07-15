const activityEl = document.getElementById("activity");

const SERVICE_LABELS = {
  mcp: "MCP",
  itsm: "ITSM",
  rag: "RAG",
};

function renderEntry(entry) {
  return `<li><span>${entry.at}</span> ${entry.method} ${entry.path} · ${entry.status} · ${entry.duration_ms}ms</li>`;
}

function renderColumn(name, entries) {
  const label = SERVICE_LABELS[name] || name;
  const items = entries.length
    ? entries.map(renderEntry).join("")
    : '<li class="muted">No calls yet</li>';

  return `
    <div class="activity-col">
      <h3>${label}</h3>
      <ul>${items}</ul>
    </div>
  `;
}

function render(data) {
  const activity = data.activity || {};
  activityEl.innerHTML = ["mcp", "itsm", "rag"]
    .map((name) => renderColumn(name, activity[name] || []))
    .join("");
}

async function refresh() {
  try {
    const res = await fetch(`/status?t=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) throw new Error(`Status request failed (${res.status})`);
    render(await res.json());
  } catch (err) {
    activityEl.innerHTML = `<p class="muted">${err.message}</p>`;
  }
}

refresh();
setInterval(refresh, 500);
