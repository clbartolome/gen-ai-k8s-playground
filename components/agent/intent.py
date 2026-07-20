"""LLM-based intent / reply classification (prompt templates, MLflow-ready)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from llm import LLMClient

log = logging.getLogger("agent.intent")

# Versionable templates — later: load from MLflow Prompt Registry.
USER_INTENT_SYSTEM_PROMPT = """You classify the user's latest message for an operations assistant.

Given the conversation history (if any) and the latest user message, decide:
1) intent — what the latest message is doing
2) task_mode — the overall goal of this conversation thread

intent values:
- execute: user wants the assistant to perform work now (create/delete VM, open tickets, run automation)
- inform: user wants explanation, how-to, procedure, or documentation only
- clarify: user is answering a previous question, providing missing fields, or asking a short clarification about an ongoing task

task_mode values:
- execute: the thread is about performing work (including follow-up replies with VM details)
- inform: the thread is about learning / how-to only

Rules:
- "how do I create a VM" / "explain the procedure" → intent=inform, task_mode=inform
- "create a virtual machine" / "please delete vm web-01" → intent=execute, task_mode=execute
- After the assistant asked for missing fields, a user reply with name/size/network/… → intent=clarify, task_mode=execute
- Prefer task_mode=execute if history shows an in-progress execution (assistant asked for params or started ITSM/AAP steps)

Respond with ONLY a single JSON object, no markdown, no extra text:
{"intent":"execute|inform|clarify","task_mode":"execute|inform","reason":"short explanation"}
"""

AGENT_REPLY_SYSTEM_PROMPT = """You classify an assistant Final Answer during an EXECUTE workflow.

The user wants work performed (ITSM ticket + AAP create_vm/delete_vm), not only a how-to.

Classify the assistant text as exactly one of:
- clarifying: asks the user for missing parameters / confirmation before acting
- done: reports that work was completed (ticket created, workflow run, outcome)
- premature: only describes the procedure or gives generic steps without asking for missing fields and without having executed tools

Respond with ONLY a single JSON object, no markdown, no extra text:
{"kind":"clarifying|done|premature","reason":"short explanation"}
"""


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    # Drop common model wrappers (markdown fences / think blocks).
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"```(?:json)?\s*", "", cleaned)
    cleaned = cleaned.replace("```", "").strip()
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        raise ValueError(f"No JSON object in classifier reply: {text[:200]}")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("Classifier JSON was not an object")
    return data


def _format_history(history: list[dict] | None) -> str:
    if not history:
        return "(no prior turns)"
    lines: list[str] = []
    for turn in history[-12:]:
        role = turn.get("role", "?")
        content = str(turn.get("content", "")).strip()
        if not content:
            continue
        lines.append(f"{role}: {content[:500]}")
    return "\n".join(lines) if lines else "(no prior turns)"


def classify_user_intent(
    llm: LLMClient,
    user_message: str,
    *,
    history: list[dict] | None = None,
) -> dict[str, str]:
    """Return intent + task_mode from the LLM classifier."""
    user_prompt = (
        f"Conversation history:\n{_format_history(history)}\n\n"
        f"Latest user message:\n{user_message}\n"
    )
    raw = llm.chat(
        [
            {"role": "system", "content": USER_INTENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
    )
    try:
        data = _extract_json_object(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        log.warning("Intent classify parse failed (%s); raw=%s", exc, raw[:300])
        return {
            "intent": "inform",
            "task_mode": "inform",
            "reason": f"parse_fallback: {exc}",
        }

    intent = str(data.get("intent", "inform")).strip().lower()
    task_mode = str(data.get("task_mode", "inform")).strip().lower()
    if intent not in {"execute", "inform", "clarify"}:
        intent = "inform"
    if task_mode not in {"execute", "inform"}:
        task_mode = "execute" if intent in {"execute", "clarify"} else "inform"
    reason = str(data.get("reason", "")).strip()
    result = {"intent": intent, "task_mode": task_mode, "reason": reason}
    log.info("User intent classified: %s", result)
    return result


def classify_agent_reply(
    llm: LLMClient,
    final_answer: str,
) -> str:
    """Return clarifying | done | premature for an execute-mode Final Answer."""
    raw = llm.chat(
        [
            {"role": "system", "content": AGENT_REPLY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Assistant Final Answer:\n{final_answer}\n",
            },
        ]
    )
    try:
        data = _extract_json_object(raw)
        kind = str(data.get("kind", "premature")).strip().lower()
        reason = str(data.get("reason", "")).strip()
    except (ValueError, json.JSONDecodeError) as exc:
        log.warning("Reply classify parse failed (%s); raw=%s", exc, raw[:300])
        return "premature"

    if kind not in {"clarifying", "done", "premature"}:
        kind = "premature"
    log.info("Agent reply classified kind=%s reason=%s", kind, reason)
    return kind
