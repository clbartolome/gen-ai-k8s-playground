import json
import logging
import re
from typing import Any, Callable

from aap_mcp import AapMcpClient
from itsm_mcp import ItsmMcpClient
from llm import LLMClient
from openshift_mcp import OpenShiftMcpClient
from system_prompt import build_system_prompt

log = logging.getLogger("agent.orchestrator")

ThoughtCallback = Callable[[str], None]

PRESENT_RESULT_PROMPT = """
You present OpenShift/Kubernetes, Ansible Automation Platform (AAP), and ITSM/knowledge-base tool results directly to the user.

Rules:

Reply in clear, natural, and friendly prose.
Answer the user's original request directly.
Do not mention tool names, tool calls, arguments, MCP, APIs, or internal execution details unless they are essential to explain the result.
Do not describe your reasoning process or narrate the steps you took.
Use only facts contained in the tool result.
Do not invent, infer, or assume cluster, ticket, or article data that is not present in the result.
Be concise and prioritize the information that directly answers the user's request.
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
            "Loaded ocp_tools count=%s aap_tools count=%s itsm_tools count=%s",
            len(self.ocp_tools),
            len(self.aap_tools),
            len(self.itsm_tools),
        )

    def _present_result(
        self,
        user_message: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
        *,
        on_thought: ThoughtCallback | None = None,
    ) -> str:
        observation = _format_tool_result(result)
        if on_thought:
            on_thought("Formatting the result for you…")
        return self._llm.chat(
            [
                {"role": "system", "content": PRESENT_RESULT_PROMPT},
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

    def run(
        self,
        user_message: str,
        *,
        on_thought: ThoughtCallback | None = None,
    ) -> str:
        log.info("Message received=%s", user_message[:120])
        if on_thought:
            on_thought("Analyzing your message…")

        raw = self._llm.chat(
            [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": user_message},
            ]
        )
        decision = _parse_decision(raw)
        action = str(decision.get("action", "")).strip()
        arguments = decision.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}

        thought = str(decision.get("thought") or "").strip()
        if thought and on_thought:
            on_thought(thought)

        log.info("Decision action=%s arguments=%s", action, arguments)

        match action:
            case "unsupported" | "out_of_scope" | "request_information":
                return str(arguments.get("message") or action)

            case _ if action.startswith("openshift."):
                tool_name = action.removeprefix("openshift.")
                if on_thought:
                    on_thought(f"Calling OpenShift tool “{tool_name}”…")
                result = self._openshift_mcp.invoke(tool_name, arguments)
                return self._present_result(
                    user_message,
                    tool_name,
                    arguments,
                    result,
                    on_thought=on_thought,
                )

            case _ if action.startswith("aap."):
                tool_name = action.removeprefix("aap.")
                if on_thought:
                    on_thought(f"Calling AAP tool “{tool_name}”…")
                result = self._aap_mcp.call_tool(tool_name, arguments)
                return self._present_result(
                    user_message,
                    tool_name,
                    arguments,
                    result,
                    on_thought=on_thought,
                )

            case _ if action.startswith("itsm."):
                tool_name = action.removeprefix("itsm.")
                if on_thought:
                    on_thought(f"Calling ITSM tool “{tool_name}”…")
                result = self._itsm_mcp.call_tool(tool_name, arguments)
                return self._present_result(
                    user_message,
                    tool_name,
                    arguments,
                    result,
                    on_thought=on_thought,
                )

            case _:
                log.warning("Unknown action from LLM: %s", action)
                return f"I could not handle that action ({action or 'empty'})."
