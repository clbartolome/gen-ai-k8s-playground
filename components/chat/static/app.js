const form = document.getElementById("chat-form");
const messageInput = document.getElementById("message");
const sendBtn = document.getElementById("send-btn");
const messagesEl = document.getElementById("messages");

/** Prior turns sent to the agent (excludes the current user message). */
const conversationHistory = [];

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function clearEmptyState() {
  const empty = messagesEl.querySelector(".thread-empty");
  if (empty) empty.remove();
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

  const body = document.createElement("span");
  body.textContent = text;
  bubble.appendChild(body);

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

  const historyPayload = conversationHistory.map((turn) => ({ ...turn }));

  try {
    const startRes = await fetch("/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, history: historyPayload }),
    });
    const startData = await startRes.json();
    if (!startRes.ok) throw new Error(startData.error || "Could not start request");

    const result = await pollJob(startData.job_id, (newThoughts) => {
      newThoughts.forEach((item) => {
        const text = typeof item === "string" ? item : item.text;
        if (text) addBubble("thought", text);
      });
    });

    if (result.status === "error") {
      addBubble("error", result.error);
    } else {
      addBubble("agent", result.response);
      conversationHistory.push({ role: "user", content: message });
      conversationHistory.push({ role: "assistant", content: result.response });
    }
  } catch (err) {
    addBubble("error", err.message);
  } finally {
    sendBtn.disabled = false;
    messageInput.focus();
  }
});
