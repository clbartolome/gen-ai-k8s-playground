const form = document.getElementById("chat-form");
const messageInput = document.getElementById("message");
const sendBtn = document.getElementById("send-btn");
const messagesEl = document.getElementById("messages");

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function clearEmptyState() {
  const empty = messagesEl.querySelector(".messages-empty");
  if (empty) empty.remove();
}

function addBubble(role, text, extraClass = "") {
  clearEmptyState();

  const bubble = document.createElement("div");
  bubble.className = `bubble bubble-${role} ${extraClass}`.trim();

  const meta = document.createElement("span");
  meta.className = "bubble-meta";
  meta.textContent = role === "user" ? "You" : role === "agent" ? "Agent" : "Error";
  bubble.appendChild(meta);

  const body = document.createElement("span");
  body.textContent = text;
  bubble.appendChild(body);

  messagesEl.appendChild(bubble);
  scrollToBottom();
  return bubble;
}

function addWaitingBubble() {
  clearEmptyState();

  const bubble = document.createElement("div");
  bubble.className = "bubble-waiting";
  bubble.innerHTML = `
    Agent is thinking
    <span class="dots" aria-hidden="true">
      <span></span><span></span><span></span>
    </span>
  `;
  messagesEl.appendChild(bubble);
  scrollToBottom();
  return bubble;
}

async function pollJob(jobId) {
  while (true) {
    const res = await fetch(`/jobs/${jobId}`);
    const data = await res.json();
    if (data.status === "pending") {
      await new Promise((r) => setTimeout(r, 500));
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

  const waitingBubble = addWaitingBubble();

  try {
    const startRes = await fetch("/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    const startData = await startRes.json();
    if (!startRes.ok) throw new Error(startData.error || "Could not start request");

    const result = await pollJob(startData.job_id);
    waitingBubble.remove();

    if (result.status === "error") {
      addBubble("error", result.error);
    } else {
      addBubble("agent", result.response);
    }
  } catch (err) {
    waitingBubble.remove();
    addBubble("error", err.message);
  } finally {
    sendBtn.disabled = false;
    messageInput.focus();
  }
});
