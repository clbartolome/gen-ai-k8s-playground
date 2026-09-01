"""Route by intent, then run the matching domain specialist with MCP tools."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable

from aap_mcp import AapMcpClient
from itsm_mcp import ItsmMcpClient
from llm import LLMClient
from openshift_mcp import OpenShiftMcpClient
from prompts import (
    build_aap_prompt,
    build_itsm_prompt,
    build_openshift_prompt,
    build_out_context_prompt,
    build_present_result_prompt,
    build_router_prompt,
)
from rag_action import run_rag_action
from trace import TraceBuilder, clip_label

log = logging.getLogger("agent.react")

_CATEGORY_RE = re.compile(
    r"Category:\s*(OPENSHIFT|AAP|ITSM|RAG|OUT_CONTEXT)",
    re.IGNORECASE,
)
_VALID_CATEGORIES = frozenset(
    {"OPENSHIFT", "AAP", "ITSM", "RAG", "OUT_CONTEXT"}
)
_MAX_OBSERVATION_CHARS = 12_000
_MAX_DIALOGUE_CHARS = 6_000

InvokeFn = Callable[[str, dict[str, Any]], Any]
ThoughtCallback = Callable[[str], None]


@dataclass
class TurnOutcome:
    response: str
    category: str
    action: str = "reply"
    pending: dict[str, Any] | None = None


class ReactAgent:
    def __init__(
        self,
        llm: LLMClient,
        openshift_mcp: OpenShiftMcpClient,
        aap_mcp: AapMcpClient,
        itsm_mcp: ItsmMcpClient,
    ) -> None:
        self._llm = llm
        self._openshift_mcp = openshift_mcp
        self._aap_mcp = aap_mcp
        self._itsm_mcp = itsm_mcp
        self._router_prompt = build_router_prompt()
        self._present_prompt = build_present_result_prompt()
        self._out_context_prompt = build_out_context_prompt()

    def run(
        self,
        user_message: str,
        *,
        dialogue: list[dict[str, Any]] | None = None,
        pending: dict[str, Any] | None = None,
        last_category: str | None = None,
        on_thought: ThoughtCallback | None = None,
        trace: TraceBuilder | None = None,
    ) -> TurnOutcome:
        prior = _fit_dialogue(dialogue or [], max_chars=_MAX_DIALOGUE_CHARS)
        if on_thought:
            on_thought("Classifying your request…")
        category = self._resolve_category(
            user_message,
            dialogue=prior,
            pending=pending,
            last_category=last_category,
        )
        log.info(
            "Routed category=%s dialogue_turns=%s pending=%s last_category=%s",
            category,
            len(prior),
            bool(pending),
            last_category,
        )
        if on_thought:
            on_thought(f"Classified as {category}")
        if trace:
            continuing = isinstance(pending, dict) and (
                pending.get("kind") == "rag_action" or bool(pending.get("question"))
            )
            if continuing:
                trace.add(
                    "user_input",
                    "User provided details",
                    detail={"message": user_message},
                )
            trace.add(
                "classified",
                f"Classified as {category}",
                detail={"category": category},
            )

        if category == "OUT_CONTEXT":
            response = self._out_context(user_message, dialogue=prior)
            return _finalize_turn(
                TurnOutcome(response=response, category=category),
                trace=trace,
            )
        if category == "OPENSHIFT":
            return _finalize_turn(
                self._run_specialist(
                    user_message,
                    category=category,
                    tools=self._openshift_mcp.get_tools(),
                    build_prompt=build_openshift_prompt,
                    invoke=self._openshift_mcp.invoke,
                    dialogue=prior,
                    on_thought=on_thought,
                    trace=trace,
                ),
                trace=trace,
            )
        if category == "AAP":
            return _finalize_turn(
                self._run_specialist(
                    user_message,
                    category=category,
                    tools=self._aap_mcp.list_tools(),
                    build_prompt=build_aap_prompt,
                    invoke=self._aap_mcp.call_tool,
                    dialogue=prior,
                    on_thought=on_thought,
                    trace=trace,
                ),
                trace=trace,
            )
        if category == "ITSM":
            return _finalize_turn(
                self._run_specialist(
                    user_message,
                    category=category,
                    tools=self._itsm_mcp.list_tools(),
                    build_prompt=build_itsm_prompt,
                    invoke=self._itsm_mcp.call_tool,
                    dialogue=prior,
                    on_thought=on_thought,
                    trace=trace,
                ),
                trace=trace,
            )
        if category == "RAG":
            if on_thought and not (
                isinstance(pending, dict) and pending.get("kind") == "rag_action"
            ):
                on_thought("Searching the knowledge base for the procedure…")
            elif on_thought:
                on_thought("Continuing the procedure…")
            result = run_rag_action(
                user_message,
                llm=self._llm,
                itsm_mcp=self._itsm_mcp,
                openshift_mcp=self._openshift_mcp,
                aap_mcp=self._aap_mcp,
                dialogue=prior,
                pending=pending,
                on_thought=on_thought,
                trace=trace,
            )
            return _finalize_turn(
                TurnOutcome(
                    response=result.response,
                    category=category,
                    action=result.action,
                    pending=result.pending,
                ),
                trace=trace,
            )

        return _finalize_turn(
            TurnOutcome(
                response=(
                    "I could not classify that request. Please rephrase it in terms of "
                    "OpenShift, Ansible, ITSM, or IT knowledge."
                ),
                category="OUT_CONTEXT",
            ),
            trace=trace,
        )

    def _resolve_category(
        self,
        user_message: str,
        *,
        dialogue: list[dict[str, str]],
        pending: dict[str, Any] | None,
        last_category: str | None,
    ) -> str:
        pending_category = None
        if isinstance(pending, dict):
            pending_category = str(pending.get("category") or "").upper() or None
            if pending_category not in _VALID_CATEGORIES:
                pending_category = None

        # If we previously asked the user for information, stay in that domain
        # until the pending ask is cleared — answer length does not matter.
        if pending_category and pending_category != "OUT_CONTEXT":
            log.info("Continuing pending category=%s", pending_category)
            return pending_category

        return self._route(
            user_message,
            dialogue=dialogue,
            previous_category=last_category,
        )

    def _route(
        self,
        user_message: str,
        *,
        dialogue: list[dict[str, str]] | None = None,
        previous_category: str | None = None,
    ) -> str:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self._router_prompt},
        ]
        if previous_category:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        f"Previous category for this thread: {previous_category}. "
                        "If the latest user message is a follow-up answer or continuation, "
                        "keep that category. If it is a new request, reclassify it."
                    ),
                }
            )
        messages.extend(dialogue or [])
        messages.append({"role": "user", "content": user_message})
        raw = self._llm.chat(messages)
        log.info("Router raw=%s", (raw or "").strip()[:300])
        match = _CATEGORY_RE.search(raw or "")
        if match:
            return match.group(1).upper()
        upper = (raw or "").upper()
        for name in ("OPENSHIFT", "AAP", "ITSM", "RAG", "OUT_CONTEXT"):
            if re.search(rf"\b{name}\b", upper):
                return name
        if previous_category in _VALID_CATEGORIES:
            log.warning(
                "Router parse failed; falling back to previous_category=%s raw=%s",
                previous_category,
                (raw or "")[:200],
            )
            return previous_category
        log.warning("Router parse failed raw=%s", (raw or "")[:200])
        return "OUT_CONTEXT"

    def _out_context(
        self,
        user_message: str,
        *,
        dialogue: list[dict[str, str]] | None = None,
    ) -> str:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self._out_context_prompt},
        ]
        messages.extend(dialogue or [])
        messages.append({"role": "user", "content": user_message})
        return self._llm.chat(messages).strip()

    def _run_specialist(
        self,
        user_message: str,
        *,
        category: str,
        tools: list[dict[str, Any]],
        build_prompt: Callable[[list[dict[str, Any]]], str],
        invoke: InvokeFn,
        dialogue: list[dict[str, str]] | None = None,
        on_thought: ThoughtCallback | None = None,
        trace: TraceBuilder | None = None,
    ) -> TurnOutcome:
        if not tools:
            return TurnOutcome(
                response=(
                    "I could not reach the tools needed for this request right now. "
                    "Please try again in a moment."
                ),
                category=category,
            )

        allowed = {
            t.get("name")
            for t in tools
            if isinstance(t, dict) and isinstance(t.get("name"), str)
        }
        if on_thought:
            on_thought("Deciding the next operation…")
        decision = self._decide(
            user_message,
            build_prompt(tools),
            dialogue=dialogue,
        )
        action = str(decision.get("action") or "").strip()
        arguments = decision.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}

        log.info("Specialist action=%s arguments=%s", action, arguments)

        if action == "request_information":
            message = arguments.get("message")
            if not isinstance(message, str) or not message.strip():
                message = "Could you provide the missing details to continue?"
            if trace:
                trace.add(
                    "missing_info",
                    clip_label(message.strip()) or "Missing information",
                    status="pending",
                    detail={"question": message.strip(), "category": category},
                )
            return TurnOutcome(
                response=message.strip(),
                category=category,
                action="request_information",
                pending={
                    "category": category,
                    "question": message.strip(),
                },
            )

        if action == "reply" or action in {"unsupported", "out_of_scope"}:
            message = arguments.get("message")
            if isinstance(message, str) and message.strip():
                text = message.strip()
                if action == "reply" and _looks_like_clarifying_question(text):
                    if trace:
                        trace.add(
                            "missing_info",
                            clip_label(text) or "Missing information",
                            status="pending",
                            detail={"question": text, "category": category},
                        )
                    return TurnOutcome(
                        response=text,
                        category=category,
                        action="request_information",
                        pending={
                            "category": category,
                            "question": text,
                        },
                    )
                return TurnOutcome(response=text, category=category)
            return TurnOutcome(
                response=(
                    "I do not have enough information or capabilities to complete "
                    "that request right now."
                ),
                category=category,
            )

        if action not in allowed:
            log.warning("Unknown or disallowed tool action=%s", action)
            if trace:
                trace.add(
                    "error",
                    f"Unknown tool · {action}",
                    status="error",
                    detail={"tool": action, "arguments": arguments},
                )
            return TurnOutcome(
                response=(
                    "I could not map that request to an available operation. "
                    "Please rephrase or provide more detail."
                ),
                category=category,
            )

        if on_thought:
            on_thought(f"Calling tool “{action}”…")
        try:
            result = invoke(action, arguments)
        except Exception as exc:
            log.exception("Tool invoke failed action=%s", action)
            if trace:
                trace.add(
                    "tool_call",
                    f"{category} · {action}",
                    status="error",
                    detail={
                        "tool": action,
                        "arguments": arguments,
                        "error": str(exc),
                        "category": category,
                    },
                    parallel_group=category,
                )
            return TurnOutcome(
                response=self._present(
                    user_message,
                    tool_name=action,
                    arguments=arguments,
                    result={"error": str(exc)},
                    dialogue=dialogue,
                ),
                category=category,
                action=action,
            )

        if trace:
            trace.add(
                "tool_call",
                f"{category} · {action}",
                detail={
                    "tool": action,
                    "arguments": arguments,
                    "category": category,
                    "result_summary": _clip_for_trace(result),
                },
                parallel_group=category,
            )
        return TurnOutcome(
            response=self._present(
                user_message,
                tool_name=action,
                arguments=arguments,
                result=result,
                dialogue=dialogue,
            ),
            category=category,
            action=action,
        )

    def _decide(
        self,
        user_message: str,
        system_prompt: str,
        *,
        dialogue: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
        ]
        messages.extend(dialogue or [])
        messages.append({"role": "user", "content": user_message})
        raw = self._llm.chat(messages)
        try:
            return _parse_decision(raw)
        except ValueError:
            log.warning("Decision parse failed raw=%s", (raw or "")[:300])
            return {
                "action": "reply",
                "arguments": {
                    "message": (
                        "I had trouble deciding how to handle that request. "
                        "Please try rephrasing it."
                    )
                },
            }

    def _present(
        self,
        user_message: str,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
        present_prompt: str | None = None,
        compact: bool = True,
        dialogue: list[dict[str, str]] | None = None,
    ) -> str:
        observation = _observation_for_presentation(result, compact=compact)
        history_blob = ""
        if dialogue:
            history_blob = (
                "Recent conversation:\n"
                + "\n".join(f"{m['role']}: {m['content']}" for m in dialogue[-4:])
                + "\n\n"
            )
        return self._llm.chat(
            [
                {"role": "system", "content": present_prompt or self._present_prompt},
                {
                    "role": "user",
                    "content": (
                        f"{history_blob}"
                        f"User request:\n{user_message}\n\n"
                        f"Tool called: {tool_name}\n"
                        f"Arguments: {json.dumps(arguments, ensure_ascii=False)}\n\n"
                        f"Tool result:\n{observation}"
                    ),
                },
            ]
        ).strip()


def _finalize_turn(
    turn: TurnOutcome,
    *,
    trace: TraceBuilder | None,
) -> TurnOutcome:
    """Append a final node when the turn produced a completed user-facing response."""
    if not trace:
        return turn
    # Waiting for user input is represented by missing_info / user_input nodes.
    if turn.action == "request_information":
        return turn
    if isinstance(turn.pending, dict) and turn.pending.get("kind") == "rag_action":
        return turn
    label = clip_label(turn.response) or "Final response"
    trace.add(
        "final",
        label,
        status="ok",
        detail={
            "response": turn.response,
            "action": turn.action,
            "category": turn.category,
            "pending": turn.pending,
        },
    )
    return turn


def _clip_for_trace(value: Any, limit: int = 400) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _looks_like_clarifying_question(text: str) -> bool:
    lowered = text.lower()
    if "?" in text:
        return True
    markers = (
        "please provide",
        "could you provide",
        "which namespace",
        "what namespace",
        "need the",
        "need more",
        "missing",
        "specify",
        "tell me the",
    )
    return any(marker in lowered for marker in markers)


def _fit_dialogue(
    dialogue: list[dict[str, Any]],
    *,
    max_chars: int,
) -> list[dict[str, str]]:
    fitted: list[dict[str, str]] = []
    budget = max_chars
    for message in reversed(dialogue):
        role = message.get("role")
        content = message.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        if len(content) > budget:
            if not fitted:
                fitted.append(
                    {
                        "role": role,
                        "content": content[: max(0, budget - 40)]
                        + "\n[Earlier message truncated]",
                    }
                )
            break
        fitted.append({"role": role, "content": content})
        budget -= len(content)
        if budget <= 0:
            break
    fitted.reverse()
    return fitted


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


def _parse_decision(raw: str) -> dict[str, Any]:
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.IGNORECASE)
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
        raise ValueError(f"No JSON decision in LLM reply: {raw[:200]}")
    data = json.loads(snippet)
    if not isinstance(data, dict):
        raise ValueError("Decision JSON was not an object")
    return data


def _item_label(item: Any) -> str | None:
    if isinstance(item, str):
        text = item.strip()
        return text or None
    if not isinstance(item, dict):
        return None
    for key in (
        "name",
        "title",
        "job_template",
        "workflow_job_template",
        "username",
        "number",
    ):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = value.get("name")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
    metadata = item.get("metadata")
    if isinstance(metadata, dict):
        name = metadata.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def _compact_list_payload(result: Any) -> dict[str, Any] | None:
    payload = result
    if isinstance(result, str):
        try:
            payload = json.loads(result)
        except json.JSONDecodeError:
            return None

    items: list[Any] | None = None
    total: int | None = None
    source = "list"

    if isinstance(payload, dict):
        if isinstance(payload.get("results"), list):
            items = payload["results"]
            source = "results"
            count = payload.get("count")
            if isinstance(count, int):
                total = count
        elif isinstance(payload.get("items"), list):
            items = payload["items"]
            source = "items"
        elif isinstance(payload.get("data"), list):
            items = payload["data"]
            source = "data"
    elif isinstance(payload, list):
        items = payload

    if not items:
        return None

    labels: list[str] = []
    entries: list[dict[str, Any]] = []
    for item in items:
        label = _item_label(item)
        entry: dict[str, Any] = {}
        if isinstance(item, dict) and item.get("id") is not None:
            entry["id"] = item.get("id")
        if label:
            entry["name"] = label
            labels.append(label)
        elif entry:
            labels.append(str(entry.get("id")))
        if entry:
            entries.append(entry)

    if not labels:
        return None

    compact: dict[str, Any] = {
        "item_count": len(labels),
        "names": labels,
        "items": entries,
        "source_field": source,
    }
    if total is not None:
        compact["reported_total_count"] = total
        if total > len(labels):
            compact["note"] = (
                f"Tool returned {len(labels)} items but reported total count={total}."
            )
    return compact


def _format_tool_result(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            texts = [
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            if texts:
                return "\n".join(texts)
        return json.dumps(result, ensure_ascii=False, indent=2)
    return json.dumps(result, ensure_ascii=False, default=str)


def _observation_for_presentation(result: Any, *, compact: bool = True) -> str:
    if compact:
        compacted = _compact_list_payload(result)
        if compacted is not None:
            observation = json.dumps(compacted, ensure_ascii=False, indent=2)
        else:
            observation = _format_tool_result(result)
    else:
        observation = _format_tool_result(result)
    if len(observation) > _MAX_OBSERVATION_CHARS:
        observation = (
            observation[:_MAX_OBSERVATION_CHARS]
            + "\n[Tool result truncated to fit model context]"
        )
    return observation
