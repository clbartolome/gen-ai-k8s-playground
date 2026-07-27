import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from aap_mcp import AapMcpClient
from itsm_mcp import ItsmMcpClient
from llm import LLMClient
from openshift_mcp import OpenShiftMcpClient
from system_prompt import build_system_prompt

log = logging.getLogger("agent.orchestrator")

ThoughtCallback = Callable[[str], None]

# Keep presentation prompts inside the 16k context window of smaller workshop models.
_MAX_OBSERVATION_CHARS = 12_000
_MAX_DIALOGUE_CHARS = 8_000
_MAX_TRACE_CHARS = 4_000


@dataclass
class TurnResult:
    """Outcome of one user turn, including thread side-effects."""

    response: str
    action: str
    pending: dict[str, Any] | None = None
    trace_entry: dict[str, Any] | None = None
    extras: dict[str, Any] = field(default_factory=dict)

PRESENT_RESULT_PROMPT = """
You present OpenShift/Kubernetes, Ansible Automation Platform (AAP), and ITSM/knowledge-base tool results directly to the user.

Rules:

Reply in clear, natural, and friendly prose.
Answer the user's original request directly.
Do not mention tool names, tool calls, arguments, MCP, APIs, or internal execution details unless they are essential to explain the result.
Do not describe your reasoning process or narrate the steps you took.
Use only facts contained in the tool result.
Do not invent, infer, or assume cluster, ticket, or article data that is not present in the result.
When the user asks for a list (job templates, pods, incidents, etc.), list every item present in the tool result.
Do not stop early or claim that only the first few items are available unless the tool result itself says the list is truncated or incomplete.
If the tool result includes a total count higher than the listed items, report both the listed names and the total count.
Be concise, but never omit list items that are present in the data.
Use Markdown only when it improves readability, such as short lists or resource names.
Do not use Markdown code fences unless the result contains code or commands that must be preserved.

Error handling:

If the tool result is an error, explain the problem in simple, user-focused language.
State what could not be completed.
Include the relevant error detail without exposing unnecessary technical internals.
Suggest one practical next step when appropriate.
Do not claim that a resource does not exist unless the tool result explicitly says so.
Do not retry, select another tool, or imply that another action was performed.

Examples of preferred style:

Instead of:
"To determine the number of pods, I called the tool and it returned an error."

Say:
"I couldn't list the pods because the namespace pepe does not exist. Check the namespace name and try again."

Instead of:
"The pods_list_in_namespace tool returned three results."

Say:
"There are 3 pods in the payments namespace."
"""


def _first_json_object(text: str) -> str | None:
    """Return the first balanced `{...}` slice, respecting JSON string quotes."""
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
    """Extract the first JSON object from the LLM reply."""
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


def _item_label(item: Any) -> str | None:
    """Best-effort display name for list entries from AAP/OpenShift/ITSM payloads."""
    if isinstance(item, str):
        text = item.strip()
        return text or None
    if not isinstance(item, dict):
        return None

    for key in ("name", "title", "job_template", "workflow_job_template", "username", "number"):
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
    """
    Collapse bulky list payloads (especially AAP/AWX `results`) to names/ids.

    Full job-template objects are huge; sending them raw often truncates after
    only one or two items and the presenter then claims the list is incomplete.
    """
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


def _observation_for_presentation(result: Any) -> str:
    compact = _compact_list_payload(result)
    if compact is not None:
        observation = json.dumps(compact, ensure_ascii=False, indent=2)
        log.info(
            "Compacted list observation items=%s chars=%s",
            compact.get("item_count"),
            len(observation),
        )
    else:
        observation = _format_tool_result(result)

    if len(observation) > _MAX_OBSERVATION_CHARS:
        log.warning(
            "Truncating observation chars=%s limit=%s",
            len(observation),
            _MAX_OBSERVATION_CHARS,
        )
        observation = (
            observation[:_MAX_OBSERVATION_CHARS]
            + "\n[Tool result truncated to fit model context]"
        )
    return observation


def _trace_summary(result: Any) -> str:
    compact = _compact_list_payload(result)
    if compact is not None:
        names = compact.get("names") or []
        total = compact.get("reported_total_count", compact.get("item_count"))
        preview = ", ".join(str(name) for name in names[:30])
        if len(names) > 30:
            preview += f", … (+{len(names) - 30} more)"
        return f"count={total}; names=[{preview}]"
    text = _format_tool_result(result)
    if len(text) > 1_500:
        return text[:1_499].rstrip() + "…"
    return text


def _fit_dialogue(dialogue: list[dict[str, Any]], *, max_chars: int) -> list[dict[str, str]]:
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


def _build_decide_messages(
    *,
    system_prompt: str,
    user_message: str,
    dialogue: list[dict[str, Any]] | None,
    trace: list[dict[str, Any]] | None,
    pending: dict[str, Any] | None,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]

    prior = _fit_dialogue(dialogue or [], max_chars=_MAX_DIALOGUE_CHARS)
    messages.extend(prior)

    context_blocks: list[str] = []
    if trace:
        slim_trace = []
        for entry in trace[-8:]:
            if not isinstance(entry, dict):
                continue
            slim_trace.append(
                {
                    "tool": entry.get("tool"),
                    "arguments": entry.get("arguments") or {},
                    "summary": entry.get("summary") or "",
                }
            )
        encoded = json.dumps(slim_trace, ensure_ascii=False)
        if len(encoded) > _MAX_TRACE_CHARS:
            encoded = encoded[: _MAX_TRACE_CHARS - 20] + "…[trace truncated]"
        context_blocks.append(
            "OPERATIONAL_TRACE (compact evidence from earlier tool calls in this thread):\n"
            + encoded
            + "\nUse these facts when relevant. Do not repeat identical tool calls "
            "unless the user asks for a refresh or state may have changed."
        )

    if pending:
        context_blocks.append(
            "PENDING_REQUEST: You previously asked the user for missing information.\n"
            + json.dumps(pending, ensure_ascii=False)
            + "\nThe latest user message should be treated as a reply to that question. "
            "Merge the new information with known_arguments and continue the intended "
            "operation when possible. Clear the pending ask by acting or by asking only "
            "for whatever is still missing."
        )

    if context_blocks:
        messages.append({"role": "system", "content": "\n\n".join(context_blocks)})

    messages.append({"role": "user", "content": user_message})
    return messages


class AgentOrchestrator:
    """Decide next action via LLM, then dispatch (OpenShift / AAP / ITSM MCP or reply)."""

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
        self.ocp_tools = self._openshift_mcp.get_tools()
        self.aap_tools = self._aap_mcp.list_tools()
        self.itsm_tools = self._itsm_mcp.list_tools()
        self._system_prompt = build_system_prompt(
            self.ocp_tools,
            self.aap_tools,
            self.itsm_tools,
        )
        log.info(
            "Loaded ocp_tools count=%s aap_tools count=%s itsm_tools count=%s "
            "system_prompt_chars=%s",
            len(self.ocp_tools),
            len(self.aap_tools),
            len(self.itsm_tools),
            len(self._system_prompt),
        )

    def _present_result(
        self,
        user_message: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
        *,
        on_thought: ThoughtCallback | None = None,
        dialogue: list[dict[str, Any]] | None = None,
    ) -> str:
        observation = _observation_for_presentation(result)
        if on_thought:
            on_thought("Formatting the result for you…")

        # Include a short dialogue tail so follow-up asks stay grounded.
        prior = _fit_dialogue(dialogue or [], max_chars=2_000)
        history_blob = ""
        if prior:
            history_blob = (
                "Recent conversation:\n"
                + "\n".join(f"{m['role']}: {m['content']}" for m in prior[-4:])
                + "\n\n"
            )

        return self._llm.chat(
            [
                {"role": "system", "content": PRESENT_RESULT_PROMPT},
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

    def _invoke_tool(self, action: str, arguments: dict[str, Any]) -> Any:
        if action.startswith("openshift."):
            return self._openshift_mcp.invoke(action.removeprefix("openshift."), arguments)
        if action.startswith("aap."):
            return self._aap_mcp.call_tool(action.removeprefix("aap."), arguments)
        if action.startswith("itsm."):
            return self._itsm_mcp.call_tool(action.removeprefix("itsm."), arguments)
        raise ValueError(f"Unsupported action prefix: {action}")

    def run(
        self,
        user_message: str,
        *,
        dialogue: list[dict[str, Any]] | None = None,
        trace: list[dict[str, Any]] | None = None,
        pending: dict[str, Any] | None = None,
        on_thought: ThoughtCallback | None = None,
    ) -> TurnResult:
        log.info(
            "Message received chars=%s dialogue_turns=%s trace=%s pending=%s",
            len(user_message),
            len(dialogue or []),
            len(trace or []),
            bool(pending),
        )
        if on_thought:
            on_thought("Analyzing your message…")

        decide_messages = _build_decide_messages(
            system_prompt=self._system_prompt,
            user_message=user_message,
            dialogue=dialogue,
            trace=trace,
            pending=pending,
        )
        raw = self._llm.chat(decide_messages)
        decision = _parse_decision(raw)
        action = str(decision.get("action", "")).strip()
        arguments = decision.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}

        thought = str(decision.get("thought") or "").strip()
        if thought and on_thought:
            on_thought(thought)

        log.info("Decision action=%s arguments=%s", action, arguments)

        if action in {"unsupported", "out_of_scope"}:
            return TurnResult(
                response=str(arguments.get("message") or action),
                action=action,
            )

        if action == "request_information":
            message = str(arguments.get("message") or "Need more information.")
            pending_state = {
                "question": message,
                "intended_action": arguments.get("intended_action"),
                "known_arguments": arguments.get("known_arguments")
                if isinstance(arguments.get("known_arguments"), dict)
                else {},
            }
            return TurnResult(
                response=message,
                action=action,
                pending=pending_state,
            )

        if action.startswith(("openshift.", "aap.", "itsm.")):
            tool_name = action.split(".", 1)[1]
            domain = action.split(".", 1)[0]
            if on_thought:
                label = {
                    "openshift": "OpenShift",
                    "aap": "AAP",
                    "itsm": "ITSM",
                }.get(domain, domain)
                on_thought(f"Calling {label} tool “{tool_name}”…")

            result = self._invoke_tool(action, arguments)
            response = self._present_result(
                user_message,
                tool_name,
                arguments,
                result,
                on_thought=on_thought,
                dialogue=dialogue,
            )
            return TurnResult(
                response=response,
                action=action,
                trace_entry={
                    "tool": action,
                    "arguments": arguments,
                    "summary": _trace_summary(result),
                },
            )

        log.warning("Unknown action from LLM: %s", action)
        return TurnResult(
            response=f"I could not handle that action ({action or 'empty'}).",
            action=action or "unknown",
        )