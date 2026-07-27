const form = document.getElementById("chat-form");
const messageInput = document.getElementById("message");
const sendBtn = document.getElementById("send-btn");
const messagesEl = document.getElementById("messages");

const THREAD_KEY = "genai_chat_thread_id";

function getThreadId() {
  return sessionStorage.getItem(THREAD_KEY) || null;
}

function setThreadId(threadId) {
  if (threadId) sessionStorage.setItem(THREAD_KEY, threadId);
}

if (typeof marked !== "undefined") {
  marked.setOptions({
    gfm: true,
    breaks: true,
  });
}

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function clearEmptyState() {
  const empty = messagesEl.querySelector(".thread-empty");
  if (empty) empty.remove();
}

function renderBody(role, text) {
  const body = document.createElement("div");
  body.className = "bubble-body";

  // Only agent replies are treated as Markdown; keep other roles as plain text.
  if (role === "agent" && typeof marked !== "undefined") {
    const html = marked.parse(text || "");
    body.classList.add("bubble-md");
    body.innerHTML =
      typeof DOMPurify !== "undefined"
        ? DOMPurify.sanitize(html)
        : html;
  } else {
    body.textContent = text;
  }
  return body;
}

function addBubble(role, text, extraClass = "") {
  clearEmptyState();

  const bubble = document.createElement("div");
  bubble.className = `bubble bubble-${role} ${extraClass}`.trim();

  const meta = document.createElement("span");
  meta.className = "bubble-meta";
  if (role === "user") meta.textContent = "You";
  else if (role === "agent") meta.textContent = "Agent";
  else if (role === "thought") meta.textContent = "Thinking";
  else meta.textContent = "Error";
  bubble.appendChild(meta);
  bubble.appendChild(renderBody(role, text));

  messagesEl.appendChild(bubble);
  scrollToBottom();
  return bubble;
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
    if (data.thread_id) setThreadId(data.thread_id);
    if (data.status === "pending") {
      await new Promise((r) => setTimeout(r, 400));
      continue;
    }
    return data;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = messageInput.value.trim();
  if (!message) return;

  sendBtn.disabled = true;
  addBubble("user", message);
  messageInput.value = "";

  const payload = { message };
  const threadId = getThreadId();
  if (threadId) payload.thread_id = threadId;

  try {
    const startRes = await fetch("/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const startData = await startRes.json();
    if (!startRes.ok) throw new Error(startData.error || "Could not start request");
    if (startData.thread_id) setThreadId(startData.thread_id);

    const result = await pollJob(startData.job_id, (newThoughts) => {
      newThoughts.forEach((item) => {
        const text = typeof item === "string" ? item : item.text;
        if (text) addBubble("thought", text);
      });
    });

    if (result.thread_id) setThreadId(result.thread_id);

    if (result.status === "error") {
      addBubble("error", result.error);
    } else {
      addBubble("agent", result.response);
    }
  } catch (err) {
    addBubble("error", err.message);
  } finally {
    sendBtn.disabled = false;
    messageInput.focus();
  }
});
