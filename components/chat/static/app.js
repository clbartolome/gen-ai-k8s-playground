const STORAGE_KEY = "genai_slack_channel_v1";

const workspace = document.querySelector(".workspace");
const channelMessagesEl = document.getElementById("channel-messages");
const channelEmptyEl = document.getElementById("channel-empty");
const channelForm = document.getElementById("channel-form");
const channelInput = document.getElementById("channel-input");
const channelSend = document.getElementById("channel-send");

const threadPanel = document.getElementById("thread-panel");
const threadParentEl = document.getElementById("thread-parent");
const threadRepliesEl = document.getElementById("thread-replies");
const threadSubtitle = document.getElementById("thread-subtitle");
const closeThreadBtn = document.getElementById("close-thread");
const clearChannelBtn = document.getElementById("clear-channel");
const threadForm = document.getElementById("thread-form");
const threadInput = document.getElementById("thread-input");
const threadSend = document.getElementById("thread-send");

/** @type {{ messages: ChannelMessage[] }} */
let state = loadState();
/** @type {string | null} */
let openThreadId = null;
/** @type {Set<string>} */
const busyParents = new Set();
const INFO_POLL_MS = 2000;
const INCIDENT_POLL_MS = 2000;

const INCIDENT_AVATAR_ICON =
  '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
  '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>' +
  '<path d="M12 9v4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>' +
  '<circle cx="12" cy="17" r="1" fill="currentColor"/>' +
  "</svg>";

/**
 * @typedef {{
 *   id: string,
 *   text: string,
 *   createdAt: number,
 *   kind?: "message" | "incident",
 *   incidentId?: string,
 *   title?: string,
 *   severity?: string,
 *   agentThreadId: string | null,
 *   lastReadAt?: number,
 *   replies: Reply[],
 * }} ChannelMessage
 *
 * @typedef {{
 *   id: string,
 *   role: "user" | "agent" | "thought" | "error" | "info",
 *   text: string,
 *   createdAt: number,
 * }} Reply
 */

if (typeof marked !== "undefined") {
  marked.setOptions({ gfm: true, breaks: true });
}

function loadState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { messages: [] };
    const parsed = JSON.parse(raw);
    if (!parsed || !Array.isArray(parsed.messages)) return { messages: [] };
    parsed.messages = parsed.messages.map(normalizeMessage);
    return parsed;
  } catch {
    return { messages: [] };
  }
}

function normalizeMessage(msg) {
  if (msg.lastReadAt != null) return msg;
  const replies = visibleReplies(msg.replies || []);
  msg.lastReadAt = replies.length
    ? Math.max(...replies.map((r) => r.createdAt))
    : msg.createdAt;
  return msg;
}

function getLastReadAt(msg) {
  return msg.lastReadAt ?? msg.createdAt;
}

function unreadCount(msg) {
  if (openThreadId === msg.id) return 0;
  const lastRead = getLastReadAt(msg);
  return visibleReplies(msg.replies).filter((r) => r.createdAt > lastRead).length;
}

function hasUnread(msg) {
  return unreadCount(msg) > 0;
}

function markThreadRead(msg) {
  const replies = visibleReplies(msg.replies);
  msg.lastReadAt = replies.length
    ? Math.max(...replies.map((r) => r.createdAt))
    : msg.createdAt;
  saveState();
}

function saveState() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

function uid() {
  return crypto.randomUUID
    ? crypto.randomUUID()
    : `id-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function formatTime(ts) {
  try {
    return new Intl.DateTimeFormat(undefined, {
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(ts));
  } catch {
    return "";
  }
}

function authorLabel(role) {
  if (role === "user") return "Tú";
  if (role === "agent") return "Agent";
  if (role === "info") return "Info";
  if (role === "incident") return "Incident";
  if (role === "thought") return "Thinking";
  return "Error";
}

function avatarInitial(role, authorName = null) {
  if (authorName) {
    const words = authorName.trim().split(/\s+/).filter(Boolean);
    if (words.length >= 2) {
      return (words[0][0] + words[1][0]).toUpperCase();
    }
    return authorName.slice(0, 2).toUpperCase();
  }
  if (role === "user") return "TÚ";
  if (role === "agent") return "AI";
  if (role === "info") return "i";
  if (role === "thought") return "…";
  return "!";
}

function renderMarkdown(text) {
  if (typeof marked === "undefined") return escapeHtml(text || "");
  const html = marked.parse(text || "");
  return typeof DOMPurify !== "undefined" ? DOMPurify.sanitize(html) : html;
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function autosize(el) {
  el.style.height = "auto";
  el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
}

function scrollChannelBottom() {
  channelMessagesEl.scrollTop = channelMessagesEl.scrollHeight;
}

function scrollThreadBottom() {
  threadRepliesEl.scrollTop = threadRepliesEl.scrollHeight;
}

function findMessage(parentId) {
  return state.messages.find((m) => m.id === parentId) || null;
}

function findMessageByAgentThreadId(agentThreadId) {
  return (
    state.messages.find((m) => m.agentThreadId === agentThreadId) || null
  );
}

function visibleReplies(replies) {
  return (replies || []).filter((r) => r.role !== "thought");
}

/** How many recent thoughts to show in the live stack (current + previous). */
const THOUGHT_STACK_VISIBLE = 4;

function trailingThoughts(replies) {
  const list = replies || [];
  let start = list.length;
  while (start > 0 && list[start - 1].role === "thought") start -= 1;
  return list.slice(start);
}

function refreshThoughtStack(stackEl, thoughts, { animateNew = false } = {}) {
  const linesEl = stackEl.querySelector(".thought-stack-lines");
  if (!linesEl) return;

  const visible = thoughts.slice(-THOUGHT_STACK_VISIBLE);
  const prevNewestId = linesEl.dataset.newestId || "";
  const newest = visible[visible.length - 1];
  const newestId = newest ? newest.id : "";
  const shouldAnimate = animateNew && newestId && newestId !== prevNewestId;

  const existing = new Map(
    [...linesEl.querySelectorAll(".thought-line")].map((el) => [
      el.dataset.id,
      el,
    ])
  );
  const nextIds = new Set(visible.map((t) => t.id));

  for (const [id, el] of existing) {
    if (!nextIds.has(id)) el.remove();
  }

  visible.forEach((thought, index) => {
    const depth = visible.length - 1 - index;
    let line = existing.get(thought.id);
    if (!line) {
      line = document.createElement("div");
      line.className = "thought-line";
      line.dataset.id = thought.id;
      line.textContent = thought.text;
      if (shouldAnimate && thought.id === newestId) {
        line.classList.add("is-new");
        line.addEventListener(
          "animationend",
          () => line.classList.remove("is-new"),
          { once: true }
        );
      }
    }
    line.dataset.depth = String(depth);
    linesEl.appendChild(line);
  });

  linesEl.dataset.newestId = newestId;

  const countEl = stackEl.querySelector(".thought-stack-count");
  if (countEl) {
    countEl.textContent =
      thoughts.length > THOUGHT_STACK_VISIBLE
        ? `${thoughts.length} pasos`
        : "";
  }
}

function buildThoughtStackEl(thoughts, { animateNew = false } = {}) {
  const row = document.createElement("article");
  row.className = "thought-stack";
  row.setAttribute("aria-live", "polite");
  row.setAttribute("aria-label", "Pensando");

  const avatar = document.createElement("div");
  avatar.className = "msg-avatar thought";
  avatar.innerHTML =
    '<span class="thought-avatar-pulse" aria-hidden="true"></span>';
  row.appendChild(avatar);

  const card = document.createElement("div");
  card.className = "thought-stack-card";

  const header = document.createElement("div");
  header.className = "thought-stack-header";

  const label = document.createElement("span");
  label.className = "thought-stack-label";
  label.textContent = "Thinking";
  header.appendChild(label);

  const dots = document.createElement("span");
  dots.className = "typing-dots";
  dots.setAttribute("aria-hidden", "true");
  dots.innerHTML = "<span></span><span></span><span></span>";
  header.appendChild(dots);

  const count = document.createElement("span");
  count.className = "thought-stack-count";
  header.appendChild(count);

  card.appendChild(header);

  const lines = document.createElement("div");
  lines.className = "thought-stack-lines";
  card.appendChild(lines);

  row.appendChild(card);
  refreshThoughtStack(row, thoughts, { animateNew });
  return row;
}

function ensureThreadDivider(replies) {
  const count = visibleReplies(replies).length;
  if (count === 0 && !replies.some((r) => r.role === "thought")) return;

  let divider = threadRepliesEl.querySelector(".thread-divider");
  if (!divider) {
    divider = document.createElement("div");
    divider.className = "thread-divider";
    threadRepliesEl.prepend(divider);
  }
  divider.textContent =
    count === 0
      ? "Respuestas"
      : count === 1
        ? "1 respuesta"
        : `${count} respuestas`;
}

function upsertThoughtStack(parentId) {
  const msg = findMessage(parentId);
  if (!msg || openThreadId !== parentId) return;

  const thoughts = trailingThoughts(msg.replies);
  if (!thoughts.length) return;

  ensureThreadDivider(msg.replies);

  let stack = threadRepliesEl.querySelector(".thought-stack");
  if (!stack) {
    stack = buildThoughtStackEl(thoughts, { animateNew: true });
    threadRepliesEl.appendChild(stack);
  } else {
    refreshThoughtStack(stack, thoughts, { animateNew: true });
  }

  const count = visibleReplies(msg.replies).length;
  threadSubtitle.textContent =
    count === 0
      ? "Esperando respuesta del agente"
      : count === 1
        ? "1 respuesta"
        : `${count} respuestas`;

  scrollThreadBottom();
}

function buildMsgEl(opts) {
  const {
    role,
    text,
    createdAt,
    markdown = false,
    pending = false,
    onClick = null,
    active = false,
    replyCount = 0,
    unread = 0,
    severity = null,
    authorName = null,
  } = opts;

  const row = document.createElement("article");
  row.className = `msg${pending ? " pending" : ""}${active ? " active-thread" : ""}${unread > 0 ? " has-unread" : ""}`;
  if (onClick) {
    row.style.cursor = "pointer";
    row.addEventListener("click", (event) => {
      if (event.target.closest("button, a")) return;
      onClick();
    });
  }

  const avatar = document.createElement("div");
  if (role === "incident") {
    avatar.className = "msg-avatar incident";
    avatar.innerHTML = INCIDENT_AVATAR_ICON;
  } else {
    avatar.className = `msg-avatar ${role}`;
    avatar.textContent = avatarInitial(role, authorName);
  }
  avatar.setAttribute("aria-hidden", "true");
  row.appendChild(avatar);

  const body = document.createElement("div");
  body.className = "msg-body";

  const meta = document.createElement("div");
  meta.className = "msg-meta";

  const author = document.createElement("span");
  author.className = "msg-author";
  author.textContent = authorName || authorLabel(role);
  if (unread > 0) {
    const dot = document.createElement("span");
    dot.className = "unread-dot";
    dot.setAttribute("aria-label", `${unread} sin leer`);
    author.appendChild(dot);
  }
  if (role === "agent") {
    const tag = document.createElement("span");
    tag.className = "ai-tag";
    tag.textContent = "AI";
    author.appendChild(tag);
  }
  if (role === "info") {
    const tag = document.createElement("span");
    tag.className = "info-tag";
    tag.textContent = "Info";
    author.appendChild(tag);
  }
  if (role === "incident") {
    const tag = document.createElement("span");
    tag.className = "incident-tag";
    tag.textContent = "INCIDENT";
    author.appendChild(tag);
    if (severity && severity.toLowerCase() !== "unknown") {
      const sev = document.createElement("span");
      sev.className = `incident-severity severity-${severity.toLowerCase()}`;
      sev.textContent = severity.toUpperCase();
      author.appendChild(sev);
    }
  }
  meta.appendChild(author);

  const time = document.createElement("span");
  time.className = "msg-time";
  time.textContent = formatTime(createdAt);
  meta.appendChild(time);
  body.appendChild(meta);

  const textEl = document.createElement("div");
  textEl.className = `msg-text${markdown ? " md" : ""}${role === "thought" ? " thought" : ""}${role === "error" ? " error" : ""}${role === "info" ? " info" : ""}`;
  if (markdown && role === "agent") {
    textEl.innerHTML = renderMarkdown(text);
  } else {
    textEl.textContent = text;
  }
  if (pending) {
    const dots = document.createElement("span");
    dots.className = "typing-dots";
    dots.innerHTML = "<span></span><span></span><span></span>";
    textEl.appendChild(dots);
  }
  body.appendChild(textEl);

  if (replyCount > 0 || unread > 0) {
    const bar = document.createElement("button");
    bar.type = "button";
    bar.className = `reply-bar${unread > 0 ? " has-unread" : ""}`;
    bar.addEventListener("click", (event) => {
      event.stopPropagation();
      onClick && onClick();
    });

    const faces = document.createElement("span");
    faces.className = "reply-faces";
    faces.innerHTML = "<span>AI</span>";
    bar.appendChild(faces);

    const label = document.createElement("span");
    if (unread > 0) {
      label.textContent =
        unread === 1 ? "1 mensaje sin leer" : `${unread} mensajes sin leer`;
    } else {
      label.textContent =
        replyCount === 1 ? "1 respuesta" : `${replyCount} respuestas`;
    }
    bar.appendChild(label);
    body.appendChild(bar);
  }

  row.appendChild(body);
  return row;
}

function renderChannel() {
  const nearBottom =
    channelMessagesEl.scrollHeight -
      channelMessagesEl.scrollTop -
      channelMessagesEl.clientHeight <
    80;

  [...channelMessagesEl.querySelectorAll(".msg")].forEach((el) => el.remove());

  if (!state.messages.length) {
    channelEmptyEl.hidden = false;
    if (!channelEmptyEl.isConnected) {
      channelMessagesEl.appendChild(channelEmptyEl);
    }
    return;
  }

  channelEmptyEl.hidden = true;

  for (const msg of state.messages) {
    const count = visibleReplies(msg.replies).length;
    const unread = unreadCount(msg);
    const isIncident = msg.kind === "incident";
    const row = buildMsgEl({
      role: isIncident ? "incident" : "user",
      text: msg.text,
      createdAt: msg.createdAt,
      active: openThreadId === msg.id,
      replyCount: count,
      unread,
      severity: isIncident ? msg.severity : null,
      authorName: isIncident ? msg.title : null,
      onClick: () => openThread(msg.id),
    });
    row.dataset.parentId = msg.id;
    channelMessagesEl.appendChild(row);
  }

  if (nearBottom) scrollChannelBottom();
}

function renderThread(parentId) {
  const msg = findMessage(parentId);
  if (!msg) {
    closeThread();
    return;
  }

  threadParentEl.innerHTML = "";
  const isIncident = msg.kind === "incident";
  threadParentEl.appendChild(
    buildMsgEl({
      role: isIncident ? "incident" : "user",
      text: msg.text,
      createdAt: msg.createdAt,
      severity: isIncident ? msg.severity : null,
      authorName: isIncident ? msg.title : null,
    })
  );

  threadRepliesEl.innerHTML = "";
  const replies = msg.replies || [];
  const count = visibleReplies(replies).length;

  if (count > 0 || replies.some((r) => r.role === "thought")) {
    const divider = document.createElement("div");
    divider.className = "thread-divider";
    divider.textContent =
      count === 0
        ? "Respuestas"
        : count === 1
          ? "1 respuesta"
          : `${count} respuestas`;
    threadRepliesEl.appendChild(divider);
  }

  let i = 0;
  while (i < replies.length) {
    if (replies[i].role === "thought") {
      const batch = [];
      while (i < replies.length && replies[i].role === "thought") {
        batch.push(replies[i]);
        i += 1;
      }
      threadRepliesEl.appendChild(buildThoughtStackEl(batch));
      continue;
    }

    threadRepliesEl.appendChild(
      buildMsgEl({
        role: replies[i].role,
        text: replies[i].text,
        createdAt: replies[i].createdAt,
        markdown: replies[i].role === "agent",
      })
    );
    i += 1;
  }

  threadSubtitle.textContent =
    count === 0
      ? "Esperando respuesta del agente"
      : count === 1
        ? "1 respuesta"
        : `${count} respuestas`;

  scrollThreadBottom();
}

function openThread(parentId) {
  const msg = findMessage(parentId);
  if (!msg) return;

  openThreadId = parentId;
  markThreadRead(msg);
  threadPanel.hidden = false;
  workspace.classList.add("thread-open");
  renderChannel();
  renderThread(parentId);
  threadInput.focus();
}

function closeThread() {
  openThreadId = null;
  threadPanel.hidden = true;
  workspace.classList.remove("thread-open");
  renderChannel();
  channelInput.focus();
}

async function pollJob(jobId, onThoughts) {
  let seen = 0;
  while (true) {
    const res = await fetch(`/jobs/${jobId}`);
    const data = await res.json();
    const thoughts = data.thoughts || [];
    if (thoughts.length > seen) {
      onThoughts(thoughts.slice(seen));
      seen = thoughts.length;
    }
    if (data.status === "pending") {
      await new Promise((r) => setTimeout(r, 400));
      continue;
    }
    return data;
  }
}

function appendReply(parentId, reply) {
  const msg = findMessage(parentId);
  if (!msg) return;
  msg.replies.push(reply);
  if (openThreadId === parentId && reply.role !== "thought") {
    markThreadRead(msg);
  } else {
    saveState();
  }
  if (openThreadId === parentId) {
    if (reply.role === "thought") {
      upsertThoughtStack(parentId);
    } else {
      renderThread(parentId);
    }
  }
  renderChannel();
}

function setAgentThreadId(parentId, agentThreadId) {
  const msg = findMessage(parentId);
  if (!msg || !agentThreadId) return;
  msg.agentThreadId = agentThreadId;
  saveState();
}

function knownAgentThreadIds() {
  return [
    ...new Set(
      state.messages
        .map((m) => m.agentThreadId)
        .filter((id) => typeof id === "string" && id.length > 0)
    ),
  ];
}

async function pollInfoMessages() {
  const threadIds = knownAgentThreadIds();
  if (!threadIds.length) return;

  try {
    const res = await fetch(
      `/threads/info?ids=${encodeURIComponent(threadIds.join(","))}`
    );
    if (!res.ok) return;
    const data = await res.json();
    const messages = data.messages || [];
    if (!messages.length) return;

    for (const item of messages) {
      const parent = findMessageByAgentThreadId(item.thread_id);
      if (!parent) continue;

      const already = (parent.replies || []).some((r) => r.id === item.id);
      if (already) continue;

      appendReply(parent.id, {
        id: item.id,
        role: "info",
        text: item.message,
        createdAt: item.created_at || Date.now(),
      });
    }
  } catch {
    // Ignore transient poll errors.
  }
}

function startInfoPolling() {
  pollInfoMessages();
  setInterval(pollInfoMessages, INFO_POLL_MS);
}

function incidentDisplayText(incident) {
  return (incident.message || "").trim() || incident.title || "Incident";
}

function formatIncidentForAgent(incident) {
  const parts = ["[INCIDENT ALERT]"];
  if (incident.title) parts.push(`Title: ${incident.title}`);
  if (incident.message) parts.push(`Description: ${incident.message}`);
  if (incident.severity && incident.severity !== "unknown") {
    parts.push(`Severity: ${incident.severity}`);
  }
  parts.push(
    "Find a knowledge-base remediation procedure for this incident and offer autoremediation."
  );
  return parts.join("\n");
}

async function ingestIncident(incident) {
  if (state.messages.some((m) => m.incidentId === incident.id)) return;

  const parent = {
    id: uid(),
    kind: "incident",
    incidentId: incident.id,
    title: incident.title,
    severity: incident.severity,
    text: incidentDisplayText(incident),
    createdAt: incident.created_at || Date.now(),
    agentThreadId: null,
    replies: [],
  };
  state.messages.push(parent);
  saveState();
  renderChannel();
  scrollChannelBottom();

  void runAgentTurn(parent.id, formatIncidentForAgent(incident), {
    isNewThread: true,
    forceCategory: "RAG",
    source: "incident",
    incident: {
      title: incident.title,
      message: incident.message,
      severity: incident.severity,
    },
  });
}

async function pollIncidents() {
  try {
    const res = await fetch("/incidents");
    if (!res.ok) return;
    const data = await res.json();
    for (const incident of data.incidents || []) {
      ingestIncident(incident);
    }
  } catch {
    // Ignore transient poll errors.
  }
}

function startIncidentPolling() {
  pollIncidents();
  setInterval(pollIncidents, INCIDENT_POLL_MS);
}

async function runAgentTurn(parentId, message, { isNewThread, forceCategory, source, incident }) {
  const msg = findMessage(parentId);
  if (!msg) return;

  busyParents.add(parentId);
  updateSendButtons();

  const payload = { message };
  if (!isNewThread && msg.agentThreadId) {
    payload.thread_id = msg.agentThreadId;
  }
  if (forceCategory) {
    payload.category = forceCategory;
  }
  if (source) {
    payload.source = source;
  }
  if (incident) {
    payload.incident = incident;
  }

  try {
    const startRes = await fetch("/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const startData = await startRes.json();
    if (!startRes.ok) throw new Error(startData.error || "No se pudo iniciar la solicitud");
    if (startData.thread_id) setAgentThreadId(parentId, startData.thread_id);

    const result = await pollJob(startData.job_id, (newThoughts) => {
      newThoughts.forEach((item) => {
        const text = typeof item === "string" ? item : item.text;
        if (!text) return;
        appendReply(parentId, {
          id: uid(),
          role: "thought",
          text,
          createdAt: Date.now(),
        });
      });
    });

    if (result.thread_id) setAgentThreadId(parentId, result.thread_id);

    // Drop ephemeral thinking bubbles once the turn settles.
    const parent = findMessage(parentId);
    if (parent) {
      parent.replies = parent.replies.filter((r) => r.role !== "thought");
      saveState();
    }

    if (result.status === "error") {
      appendReply(parentId, {
        id: uid(),
        role: "error",
        text: result.error || "Error del agente",
        createdAt: Date.now(),
      });
    } else {
      appendReply(parentId, {
        id: uid(),
        role: "agent",
        text: result.response || "",
        createdAt: Date.now(),
      });
    }
  } catch (err) {
    appendReply(parentId, {
      id: uid(),
      role: "error",
      text: err.message || String(err),
      createdAt: Date.now(),
    });
  } finally {
    busyParents.delete(parentId);
    updateSendButtons();
  }
}

function updateSendButtons() {
  channelSend.disabled = channelForm.dataset.sending === "1";
  const threadBusy =
    openThreadId &&
    (busyParents.has(openThreadId) || threadForm.dataset.sending === "1");
  threadSend.disabled = Boolean(threadBusy);
}

channelInput.addEventListener("input", () => autosize(channelInput));
threadInput.addEventListener("input", () => autosize(threadInput));

channelInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    channelForm.requestSubmit();
  }
});

threadInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    threadForm.requestSubmit();
  }
});

closeThreadBtn.addEventListener("click", () => closeThread());

clearChannelBtn.addEventListener("click", () => {
  if (state.messages.length && !confirm("¿Limpiar todos los mensajes del canal?")) {
    return;
  }
  state = { messages: [] };
  busyParents.clear();
  localStorage.removeItem(STORAGE_KEY);
  closeThread();
  channelInput.value = "";
  threadInput.value = "";
  autosize(channelInput);
  autosize(threadInput);
  updateSendButtons();
  renderChannel();
  channelInput.focus();
});

channelForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = channelInput.value.trim();
  if (!text) return;

  channelForm.dataset.sending = "1";
  updateSendButtons();

  const parent = {
    id: uid(),
    kind: "message",
    text,
    createdAt: Date.now(),
    agentThreadId: null,
    lastReadAt: Date.now(),
    replies: [],
  };
  state.messages.push(parent);
  saveState();

  channelInput.value = "";
  autosize(channelInput);
  renderChannel();
  scrollChannelBottom();
  openThread(parent.id);

  channelForm.dataset.sending = "0";
  updateSendButtons();

  await runAgentTurn(parent.id, text, { isNewThread: true });
});

threadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!openThreadId) return;

  const text = threadInput.value.trim();
  if (!text) return;

  const parent = findMessage(openThreadId);
  if (!parent) return;
  if (busyParents.has(parent.id)) return;

  threadForm.dataset.sending = "1";
  updateSendButtons();

  appendReply(parent.id, {
    id: uid(),
    role: "user",
    text,
    createdAt: Date.now(),
  });

  threadInput.value = "";
  autosize(threadInput);
  threadForm.dataset.sending = "0";
  updateSendButtons();

  await runAgentTurn(parent.id, text, { isNewThread: false });
});

renderChannel();
channelInput.focus();
startInfoPolling();
startIncidentPolling();
