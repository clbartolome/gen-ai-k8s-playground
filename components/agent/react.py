"""Route by intent, then run the matching domain specialist with MCP tools."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from aap_mcp import AapMcpClient
from config import DEFAULT_ITSM_MCP_TOOLS, RAG_MCP_TOOLS
from itsm_mcp import ItsmMcpClient
from llm import LLMClient
from openshift_mcp import OpenShiftMcpClient
from prompts import (
    build_aap_prompt,
    build_itsm_prompt,
    build_openshift_prompt,
    build_out_context_prompt,
    build_present_result_prompt,
    build_rag_prompt,
    build_router_prompt,
)

log = logging.getLogger("agent.react")

_CATEGORY_RE = re.compile(
    r"Category:\s*(OPENSHIFT|AAP|ITSM|RAG|OUT_CONTEXT)",
    re.IGNORECASE,
)
_MAX_OBSERVATION_CHARS = 12_000

InvokeFn = Callable[[str, dict[str, Any]], Any]


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

    def run(self, user_message: str) -> str:
        category = self._route(user_message)
        log.info("Routed category=%s", category)

        if category == "OUT_CONTEXT":
            return self._out_context(user_message)
        if category == "OPENSHIFT":
            return self._run_specialist(
                user_message,
                tools=self._openshift_mcp.get_tools(),
                build_prompt=build_openshift_prompt,
                invoke=self._openshift_mcp.invoke,
            )
        if category == "AAP":
            return self._run_specialist(
                user_message,
                tools=self._aap_mcp.list_tools(),
                build_prompt=build_aap_prompt,
                invoke=self._aap_mcp.call_tool,
            )
        if category == "ITSM":
            return self._run_specialist(
                user_message,
                tools=self._itsm_mcp.list_tools(),
                build_prompt=build_itsm_prompt,
                invoke=self._itsm_mcp.call_tool,
            )
        if category == "RAG":
            tools = [
                t
                for t in self._itsm_mcp.list_tools()
                if isinstance(t, dict) and t.get("name") in RAG_MCP_TOOLS
            ]
            return self._run_specialist(
                user_message,
                tools=tools,
                build_prompt=build_rag_prompt,
                invoke=self._itsm_mcp.call_tool,
            )

        return (
            "I could not classify that request. Please rephrase it in terms of "
            "OpenShift, Ansible, ITSM, or IT knowledge."
        )

    def _route(self, user_message: str) -> str:
        raw = self._llm.chat(
            [
                {"role": "system", "content": self._router_prompt},
                {"role": "user", "content": user_message},
            ]
        )
        match = _CATEGORY_RE.search(raw or "")
        if match:
            return match.group(1).upper()
        upper = (raw or "").upper()
        for name in ("OPENSHIFT", "AAP", "ITSM", "RAG", "OUT_CONTEXT"):
            if name in upper:
                return name
        log.warning("Router parse failed raw=%s", (raw or "")[:200])
        return "OUT_CONTEXT"

    def _out_context(self, user_message: str) -> str:
        return self._llm.chat(
            [
                {"role": "system", "content": self._out_context_prompt},
                {"role": "user", "content": user_message},
            ]
        ).strip()

    def _run_specialist(
        self,
        user_message: str,
        *,
        tools: list[dict[str, Any]],
        build_prompt: Callable[[list[dict[str, Any]]], str],
        invoke: InvokeFn,
    ) -> str:
        if not tools:
            return (
                "I could not reach the tools needed for this request right now. "
                "Please try again in a moment."
            )

        allowed = {
            t.get("name")
            for t in tools
            if isinstance(t, dict) and isinstance(t.get("name"), str)
        }
        decision = self._decide(user_message, build_prompt(tools))
        action = str(decision.get("action") or "").strip()
        arguments = decision.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}

        log.info("Specialist action=%s arguments=%s", action, arguments)

        if action == "reply" or action in {"unsupported", "out_of_scope"}:
            message = arguments.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
            return (
                "I do not have enough information or capabilities to complete "
                "that request right now."
            )

        if action not in allowed:
            log.warning("Unknown or disallowed tool action=%s", action)
            return (
                "I could not map that request to an available operation. "
                "Please rephrase or provide more detail."
            )

        try:
            result = invoke(action, arguments)
        except Exception as exc:
            log.exception("Tool invoke failed action=%s", action)
            return self._present(
                user_message,
                tool_name=action,
                arguments=arguments,
                result={"error": str(exc)},
            )

        return self._present(
            user_message,
            tool_name=action,
            arguments=arguments,
            result=result,
        )

    def _decide(self, user_message: str, system_prompt: str) -> dict[str, Any]:
        raw = self._llm.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]
        )
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
    ) -> str:
        observation = _observation_for_presentation(result)
        return self._llm.chat(
            [
                {"role": "system", "content": self._present_prompt},
                {
                    "role": "user",
                    "content": (
                        f"User request:\n{user_message}\n\n"
                        f"Tool called: {tool_name}\n"
                        f"Arguments: {json.dumps(arguments, ensure_ascii=False)}\n\n"
                        f"Tool result:\n{observation}"
                    ),
                },
            ]
        ).strip()


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


def _observation_for_presentation(result: Any) -> str:
    compact = _compact_list_payload(result)
    if compact is not None:
        observation = json.dumps(compact, ensure_ascii=False, indent=2)
    else:
        observation = _format_tool_result(result)
    if len(observation) > _MAX_OBSERVATION_CHARS:
        observation = (
            observation[:_MAX_OBSERVATION_CHARS]
            + "\n[Tool result truncated to fit model context]"
        )
    return observation
