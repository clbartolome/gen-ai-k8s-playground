"""Builds UI flow cards from raw agent events."""


def truncate(text: str, max_len: int = 100) -> str:
    if not text:
        return ""
    one_line = " ".join(text.split())
    if len(one_line) <= max_len:
        return one_line
    return one_line[: max_len - 1].rstrip() + "…"


def _tool_title(tool: str, action: str | None = None) -> str:
    labels = {
        "mcp": "MCP",
        "itsm": "ITSM",
        "kb": "Knowledge Base",
        "rag": "Knowledge Base",
    }
    name = labels.get(tool, tool.upper())
    if tool in {"kb", "rag"}:
        return name
    return f"{name} · {action}" if action else name


def build_flow(events: list[dict]) -> list[dict]:
    cards: list[dict] = []
    open_steps: dict[str, dict] = {}

    for index, event in enumerate(events):
        event_type = event["type"]
        data = event.get("data") or {}
        at = event.get("at")
        step_id = data.get("step_id") or f"step-{index}"

        if event_type == "run_started":
            message = data.get("message", "")
            cards.append(
                {
                    "id": step_id,
                    "kind": "user",
                    "status": "done",
                    "title": "User",
                    "summary": truncate(message, 80),
                    "detail": data,
                    "at": at,
                }
            )
            continue

        if event_type == "thought":
            is_final = bool(data.get("is_final"))
            thought_text = data.get("thought") or data.get("summary", "")
            if is_final and data.get("final_answer"):
                summary = truncate(data.get("final_answer"), 120)
            else:
                summary = truncate(thought_text, 120)
            detail = {
                "response": data.get("response"),
                "thought": data.get("thought"),
                "action": data.get("action"),
                "action_input": data.get("action_input"),
                "final_answer": data.get("final_answer"),
                "is_final": is_final,
            }
            cards.append(
                {
                    "id": step_id,
                    "kind": "thought",
                    "status": "done",
                    "title": "Final" if is_final else "Thought",
                    "summary": summary,
                    "detail": detail,
                    "at": at,
                }
            )
            continue

        if event_type == "llm_started":
            phase = data.get("phase", "llm")
            label = data.get("label") or phase
            card = {
                "id": step_id,
                "kind": "llm",
                "status": "active",
                "title": label,
                "summary": data.get("summary") or "Calling LLM…",
                "detail": data.get("detail") or data,
                "at": at,
            }
            cards.append(card)
            open_steps[step_id] = card
            continue

        if event_type in {"llm_done", "llm_failed"}:
            card = open_steps.get(step_id)
            if card is None:
                card = {
                    "id": step_id,
                    "kind": "llm",
                    "title": "LLM",
                    "summary": "",
                    "detail": {},
                    "at": at,
                }
                cards.append(card)
            card["status"] = "error" if event_type == "llm_failed" else "done"
            card["summary"] = data.get("summary") or card.get("summary", "")
            card["detail"] = {
                **(card.get("detail") or {}),
                **(data.get("detail") or data),
            }
            if event_type == "llm_failed":
                card["summary"] = data.get("summary") or "LLM call failed"
            open_steps.pop(step_id, None)
            continue

        if event_type == "tool_started":
            tool = data.get("tool", "tool")
            title = _tool_title(tool, data.get("action"))
            if tool in {"kb", "rag"}:
                summary = data.get("summary") or "Searching knowledge base articles…"
            elif tool == "itsm":
                summary = data.get("summary") or f"Calling ITSM…"
            else:
                summary = data.get("summary") or f"Calling {tool.upper()}…"
            card = {
                "id": step_id,
                "kind": "kb" if tool == "rag" else tool,
                "status": "active",
                "title": title,
                "summary": summary,
                "detail": data.get("detail") or data,
                "at": at,
            }
            cards.append(card)
            open_steps[step_id] = card
            continue

        if event_type in {"tool_done", "tool_failed"}:
            card = open_steps.get(step_id)
            if card is None:
                tool = data.get("tool", "tool")
                title = _tool_title(tool, data.get("action"))
                card = {
                    "id": step_id,
                    "kind": "kb" if tool == "rag" else tool,
                    "title": title,
                    "summary": "",
                    "detail": {},
                    "at": at,
                }
                cards.append(card)
            card["status"] = "error" if event_type == "tool_failed" else "done"
            card["summary"] = data.get("summary") or card.get("summary", "")
            card["detail"] = {
                **(card.get("detail") or {}),
                **(data.get("detail") or data),
            }
            open_steps.pop(step_id, None)
            continue

        if event_type == "run_done":
            response = data.get("response", "")
            cards.append(
                {
                    "id": step_id,
                    "kind": "result",
                    "status": "done",
                    "title": "Response",
                    "summary": truncate(response, 100),
                    "detail": data,
                    "at": at,
                }
            )
            continue

        if event_type == "run_failed":
            cards.append(
                {
                    "id": step_id,
                    "kind": "error",
                    "status": "error",
                    "title": "Error",
                    "summary": truncate(data.get("error", "Request failed"), 100),
                    "detail": data,
                    "at": at,
                }
            )

    return cards
