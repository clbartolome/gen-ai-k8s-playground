"""Human-readable thoughts shown in chat while the agent works."""

from __future__ import annotations

from typing import Any, Callable


ThoughtCallback = Callable[[str], None]


def summarize_tool(action: str, kind: str, result: Any) -> str:
    if kind == "kb":
        if isinstance(result, dict):
            hits = (
                result.get("results")
                or result.get("articles")
                or result.get("items")
                or []
            )
            if isinstance(hits, list) and hits:
                titles = ", ".join(
                    str(item.get("title", item.get("id", "Untitled")))
                    for item in hits
                    if isinstance(item, dict)
                )
                return f"Found knowledge base articles: {titles}"
            if result.get("title"):
                return f"Read article: {result.get('title')}"
        return "Checked the knowledge base"
    if action == "create_incident" and isinstance(result, dict):
        ref = result.get("incident_ref") or result.get("id") or ""
        return f"Opened ITSM ticket {ref}".strip()
    if action == "add_comment":
        return "Added a comment on the ITSM ticket"
    if action == "close_incident":
        return "Closed the ITSM ticket"
    if action == "mcp_invoke" and isinstance(result, dict):
        workflow = result.get("workflow") or (result.get("arguments") or {}).get("tool_name")
        if result.get("hostname") and result.get("ip"):
            return (
                f"AAP {workflow or 'workflow'} succeeded — "
                f"{result.get('hostname')} at {result.get('ip')}"
            )
        if result.get("hostname"):
            return f"AAP {workflow or 'workflow'} succeeded for {result.get('hostname')}"
        if result.get("message"):
            return str(result["message"])
        return f"AAP workflow finished ({workflow or 'ok'})"
    if action == "list_incidents":
        return "Listed ITSM incidents"
    return f"Finished {action}"


def tool_start_message(action: str, kind: str, action_input: dict | None = None) -> str:
    action_input = action_input or {}
    if kind == "kb":
        query = action_input.get("query") or action_input.get("q") or ""
        if query:
            return f"Searching knowledge base for “{query}”…"
        return "Searching the knowledge base…"
    if action == "create_incident":
        title = action_input.get("title") or ""
        return f"Opening an ITSM ticket{f': {title}' if title else ''}…"
    if action == "mcp_invoke":
        tool_name = action_input.get("tool_name") or "workflow"
        return f"Running AAP workflow “{tool_name}”…"
    if action == "add_comment":
        return "Updating the ITSM ticket with a comment…"
    if action == "close_incident":
        return "Closing the ITSM ticket…"
    return f"Calling {action}…"


def clean_model_thought(text: str | None, *, max_len: int = 280) -> str | None:
    if not text:
        return None
    one = " ".join(text.split())
    if not one:
        return None
    if len(one) > max_len:
        return one[: max_len - 1].rstrip() + "…"
    return one
