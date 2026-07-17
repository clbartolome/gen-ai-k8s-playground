const flowTrack = document.getElementById("flow-track");
const modalBackdrop = document.getElementById("modal-backdrop");
const detailModal = document.getElementById("detail-modal");
const modalKind = document.getElementById("modal-kind");
const modalTitle = document.getElementById("modal-title");
const modalMeta = document.getElementById("modal-meta");
const modalSummary = document.getElementById("modal-summary");
const modalFields = document.getElementById("modal-fields");
const modalJson = document.getElementById("modal-json");
const modalClose = document.getElementById("modal-close");

let selectedCardId = null;
let lastFlowKey = "";

function formatTime(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString();
}

function statusClass(status) {
  if (status === "active") return "status-active";
  if (status === "error") return "status-error";
  return "status-done";
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatJson(value) {
  if (value == null) return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function fieldBlock(label, value) {
  if (value == null || value === "") return "";
  const content =
    typeof value === "string" ? escapeHtml(value) : escapeHtml(formatJson(value));
  return `
    <div class="detail-field">
      <h4>${escapeHtml(label)}</h4>
      <pre class="detail-field-value">${content}</pre>
    </div>
  `;
}

function renderDetailFields(card) {
  const detail = card.detail || {};
  const parts = [];

  if (card.kind === "thought") {
    if (detail.is_final) {
      parts.push(fieldBlock("Thought", detail.thought));
      parts.push(fieldBlock("Action", detail.action));
      parts.push(fieldBlock("Action Input", detail.action_input));
      parts.push(fieldBlock("Final answer", detail.final_answer));
    } else {
      parts.push(fieldBlock("Response", detail.response));
    }
  } else if (card.kind === "rag") {
    parts.push(fieldBlock("Request", detail.request));
    parts.push(fieldBlock("Response", detail.response));
  } else if (card.kind === "llm") {
    const prompts = detail.prompts || {};
    if (prompts.user || prompts.system) {
      parts.push(fieldBlock("User", prompts.user));
      parts.push(fieldBlock("System", prompts.system));
    } else if (detail.messages) {
      const userMsgs = detail.messages
        .filter((m) => m.role === "user")
        .map((m) => m.content)
        .join("\n\n");
      const systemMsgs = detail.messages
        .filter((m) => m.role === "system")
        .map((m) => m.content)
        .join("\n\n");
      parts.push(fieldBlock("User", userMsgs));
      parts.push(fieldBlock("System", systemMsgs));
    }
  } else if (detail.request || detail.response) {
    parts.push(fieldBlock("Request", detail.request));
    parts.push(fieldBlock("Response", detail.response));
  }

  if (parts.length) {
    modalFields.innerHTML = parts.join("");
    modalJson.hidden = true;
    return;
  }

  modalFields.innerHTML = "";
  modalJson.hidden = false;
  modalJson.textContent = JSON.stringify(detail, null, 2);
}

function renderCard(card) {
  const article = document.createElement("article");
  const isActive = card.status === "active";
  article.className = [
    "flow-card",
    `kind-${card.kind}`,
    selectedCardId === card.id ? "selected" : "",
    isActive ? "active-card" : "",
  ]
    .filter(Boolean)
    .join(" ");
  article.dataset.cardId = card.id;
  if (card.kind === "thought" && card.detail?.is_final) {
    article.dataset.final = "true";
  }
  article.innerHTML = `
    <div class="flow-card-head">
      <span class="flow-card-kind">${escapeHtml(card.kind)}</span>
      <span class="flow-card-status ${statusClass(card.status)}"></span>
    </div>
    <h3 class="flow-card-title">${escapeHtml(card.title)}</h3>
    <p class="flow-card-summary">${escapeHtml(card.summary || "—")}</p>
  `;
  article.addEventListener("click", () => openDetail(card));
  return article;
}

function openDetail(card) {
  selectedCardId = card.id;
  modalKind.textContent = card.kind;
  modalTitle.textContent = card.title;
  modalMeta.textContent = `${formatTime(card.at)} · ${card.status}`;
  modalSummary.textContent = card.summary || "—";
  renderDetailFields(card);
  modalBackdrop.hidden = false;
  detailModal.showModal();
  renderFlow(window.__lastFlow || []);
}

function closeDetail() {
  selectedCardId = null;
  detailModal.close();
  modalBackdrop.hidden = true;
  renderFlow(window.__lastFlow || []);
}

function renderFlow(flow) {
  window.__lastFlow = flow;
  flowTrack.innerHTML = "";

  if (!flow.length) {
    flowTrack.innerHTML = '<p class="flow-empty">Waiting for activity…</p>';
    return;
  }

  flow.forEach((card) => flowTrack.appendChild(renderCard(card)));
  flowTrack.scrollLeft = flowTrack.scrollWidth;
}

function render(run) {
  const flowKey = JSON.stringify(run?.flow || []);
  if (flowKey !== lastFlowKey) {
    lastFlowKey = flowKey;
    renderFlow(run?.flow || []);
  } else if (selectedCardId) {
    flowTrack.querySelectorAll(".flow-card").forEach((el) => {
      el.classList.toggle("selected", el.dataset.cardId === selectedCardId);
    });
  }
}

async function refresh() {
  try {
    const res = await fetch(`/status?t=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) throw new Error(`Status request failed (${res.status})`);
    const data = await res.json();
    render(data.latest_run);
  } catch {
    if (!window.__lastFlow?.length) {
      flowTrack.innerHTML = '<p class="flow-empty">Could not reach monitor API</p>';
    }
  }
}

modalClose.addEventListener("click", closeDetail);
modalBackdrop.addEventListener("click", closeDetail);
detailModal.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeDetail();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && detailModal.open) closeDetail();
});

refresh();
setInterval(refresh, 500);
