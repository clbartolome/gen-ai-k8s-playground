"""Intent classifier (A/B/C) and procedure-reply reasoning."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from llm import LLMClient

log = logging.getLogger("agent.intent")

CLASSIFY_PROMPT = """
You classify IT operations chat requests before any tool runs.

Domains: OpenShift/Kubernetes, AAP (Ansible), ITSM/KB.

## Intents

- A: read-only / status / listing (how many jobs, pods in X, get incident).
- B: one atomic action with a clear target (run job template Y, close ticket Z).
- C: multi-step procedure / service request (provision app, onboard env,
  follow an SOP, create environment). Needs KB/SOP first.
- out_of_scope: unrelated to those domains.
- ambiguous: cannot decide; need a clarifying question.

## Output

Return exactly one JSON object, no Markdown or fences:

{
  "intent": "A|B|C|out_of_scope|ambiguous",
  "rag_query": "short KB search query if intent is C, else null",
  "message": "user-facing text if out_of_scope or ambiguous, else null",
  "thought": "brief reason, max 30 words"
}

Rules:
- Prefer A or B when a single tool call would finish the request.
- Prefer C for onboarding, provisioning, or multi-system workflows.
- Do not invent tool names.
""".strip()

PROCEDURE_REPLY_PROMPT = """
A guided IT procedure is waiting for the user's reply.

Interpret the user's natural language. Do NOT require exact keywords.
Understand intent even with typos, slang, or varied phrasing.

## Decision values

- confirm: agrees to run the current / next step only
  (sí, ok, afirmativo, procede, de acuerdo, me parece bien, continúa,
   dale, adelante, vale, go ahead, sure, typos like "jecuta el paso", etc.)
- run_all: wants every remaining step run now without further confirms
  (ejecuta todo, lanza el resto, hazlos todos, run all, etc.)
- cancel: wants to stop / abort the procedure
  (cancela, no, para, stop, abort, olvídalo, etc.)
- other: a new request, a question, or unclear — not a procedure decision

## Output

Return exactly one JSON object, no Markdown or fences:

{
  "decision": "confirm|run_all|cancel|other",
  "thought": "brief reason, max 30 words"
}

Reason about meaning, not string equality.
""".strip()


def _first_json_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _parse_json(raw: str) -> dict[str, Any]:
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.I)
    cleaned = re.sub(r"```(?:json)?\s*", "", cleaned)
    cleaned = cleaned.replace("```", "").strip()
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    snippet = _first_json_object(cleaned)
    if not snippet:
        raise ValueError(f"No JSON in classifier reply: {raw[:200]}")
    data = json.loads(snippet)
    if not isinstance(data, dict):
        raise ValueError("Classifier JSON was not an object")
    return data


def classify_intent(llm: LLMClient, message: str) -> dict[str, Any]:
    """Classify user message into A/B/C/out_of_scope/ambiguous."""
    raw = llm.chat(
        [
            {"role": "system", "content": CLASSIFY_PROMPT},
            {"role": "user", "content": message},
        ]
    )
    data = _parse_json(raw)
    lowered = str(data.get("intent", "")).strip().lower()
    if lowered in {"a", "b", "c"}:
        intent = lowered.upper()
    elif lowered in {"out_of_scope", "out-of-scope", "oos"}:
        intent = "OUT_OF_SCOPE"
    elif lowered == "ambiguous":
        intent = "AMBIGUOUS"
    else:
        intent = "AMBIGUOUS"

    rag_query = data.get("rag_query")
    if rag_query is not None:
        rag_query = str(rag_query).strip() or None

    result = {
        "intent": intent,
        "rag_query": rag_query,
        "message": data.get("message"),
        "thought": str(data.get("thought") or "").strip(),
    }
    log.info("Classified intent=%s rag_query=%s", result["intent"], rag_query)
    return result


def classify_procedure_reply(
    llm: LLMClient,
    message: str,
    *,
    step_title: str,
    step_index: int,
    total_steps: int,
) -> dict[str, Any]:
    """Reason about confirm / run_all / cancel / other for an open plan."""
    raw = llm.chat(
        [
            {"role": "system", "content": PROCEDURE_REPLY_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Pending step {step_index}/{total_steps}: {step_title}\n"
                    f"User reply:\n{message}"
                ),
            },
        ]
    )
    data = _parse_json(raw)
    decision = str(data.get("decision", "other")).strip().lower()
    if decision not in {"confirm", "run_all", "cancel", "other"}:
        decision = "other"
    result = {
        "decision": decision,
        "thought": str(data.get("thought") or "").strip(),
    }
    log.info("Procedure reply decision=%s", decision)
    return result
