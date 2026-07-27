"""Agent orchestrator: classify intent, then atomic or guided procedure."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from aap_mcp import AapMcpClient
from intent import classify_intent, classify_procedure_reply
from itsm_mcp import ItsmMcpClient
from llm import LLMClient
from openshift_mcp import OpenShiftMcpClient
from procedure_session import ProcedureSession, ProcedureStore
from system_prompt import build_procedure_plan_prompt, build_system_prompt

log = logging.getLogger("agent.orchestrator")

ThoughtCallback = Callable[[str], None]

MAX_PROCEDURE_STEPS = 8
MAX_ATOMIC_TOOL_CALLS = 5
RAG_TOOL = "rag_search_kb"

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


def _ask_next_step(session: ProcedureSession) -> str:
    idx = session.current_index
    if idx >= len(session.steps):
        return "All steps are done."
    step = session.steps[idx]
    title = step.get("title") or f"step {idx + 1}"
    remaining = len(session.steps) - idx
    return (
        f"Step {idx + 1}/{len(session.steps)} ready: **{title}**.\n\n"
        f"Confirm to run this step, ask me to run all remaining "
        f"({remaining}) steps, or tell me to cancel."
    )


class AgentOrchestrator:
    """Classify intent, then dispatch atomic tools or guided procedures."""

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
        self._procedures = ProcedureStore()
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

    def _dispatch(
        self,
        action: str,
        arguments: dict[str, Any],
        user_message: str,
        *,
        on_thought: ThoughtCallback | None = None,
        present: bool = True,
    ) -> str:
        match action:
            case "unsupported" | "out_of_scope" | "request_information" | "done":
                return str(arguments.get("message") or action)

            case _ if action.startswith("openshift."):
                tool_name = action.removeprefix("openshift.")
                if on_thought:
                    on_thought(f"Calling OpenShift tool “{tool_name}”…")
                result = self._openshift_mcp.invoke(tool_name, arguments)
                if not present:
                    return _format_tool_result(result)
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
                if not present:
                    return _format_tool_result(result)
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
                if not present:
                    return _format_tool_result(result)
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

    def _run_atomic(
        self,
        user_message: str,
        *,
        on_thought: ThoughtCallback | None = None,
    ) -> str:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_message},
        ]
        last_tool = ""
        last_args: dict[str, Any] = {}
        last_result = ""

        for step in range(MAX_ATOMIC_TOOL_CALLS):
            raw = self._llm.chat(messages)
            decision = _parse_decision(raw)
            action = str(decision.get("action", "")).strip()
            arguments = decision.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {}

            thought = str(decision.get("thought") or "").strip()
            if thought and on_thought:
                on_thought(thought)

            log.info(
                "Atomic step=%s action=%s arguments=%s",
                step + 1,
                action,
                arguments,
            )

            if action in {
                "unsupported",
                "out_of_scope",
                "request_information",
                "done",
            }:
                return str(arguments.get("message") or action)

            if not (
                action.startswith("openshift.")
                or action.startswith("aap.")
                or action.startswith("itsm.")
            ):
                return self._dispatch(
                    action,
                    arguments,
                    user_message,
                    on_thought=on_thought,
                )

            observation = self._dispatch(
                action,
                arguments,
                user_message,
                on_thought=on_thought,
                present=False,
            )
            last_tool = action
            last_args = arguments
            last_result = observation
            # Keep follow-up context bounded.
            obs_for_llm = observation[:8000]
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Tool result for `{action}`:\n{obs_for_llm}\n\n"
                        "Continue. If you can finish the user request, return "
                        'action "done" with the final message. If you still '
                        "need a tool (for example resolve an id then act), "
                        "return the next tool action."
                    ),
                }
            )

        if last_tool:
            tool_name = last_tool.split(".", 1)[-1]
            return self._present_result(
                user_message,
                tool_name,
                last_args,
                last_result,
                on_thought=on_thought,
            )
        return "I could not complete the request within the tool-call limit."

    def _start_procedure(
        self,
        user_message: str,
        session_id: str,
        rag_query: str | None,
        *,
        on_thought: ThoughtCallback | None = None,
    ) -> str:
        query = rag_query or user_message
        if on_thought:
            on_thought("Looking up the procedure in the knowledge base…")
        sop_raw = self._itsm_mcp.call_tool(RAG_TOOL, {"query": query})
        sop = _format_tool_result(sop_raw)

        if on_thought:
            on_thought("Building a step-by-step plan…")
        plan_prompt = build_procedure_plan_prompt(
            self.ocp_tools,
            self.aap_tools,
            self.itsm_tools,
            sop,
        )
        raw = self._llm.chat(
            [
                {"role": "system", "content": plan_prompt},
                {
                    "role": "user",
                    "content": (
                        f"User request:\n{user_message}\n\n"
                        "Build the confirmation plan from the SOP."
                    ),
                },
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

        if action == "request_information":
            return str(arguments.get("message") or "Need more information.")

        if action != "propose_plan":
            log.warning("Expected propose_plan, got %s", action)
            return str(
                arguments.get("message")
                or "I could not build a procedure plan from the knowledge base."
            )

        steps = arguments.get("steps") or []
        if not isinstance(steps, list):
            steps = []
        steps = [s for s in steps if isinstance(s, dict)][:MAX_PROCEDURE_STEPS]
        if not steps:
            return (
                "I found documentation, but could not derive executable steps. "
                "Try rephrasing the request."
            )

        session = ProcedureSession(
            original_request=user_message,
            sop_excerpt=sop[:4000],
            steps=steps,
            current_index=0,
            status="awaiting_confirm",
        )
        self._procedures.set(session_id, session)

        message = str(arguments.get("message") or "").strip()
        if message:
            return message
        lines = [str(arguments.get("summary") or "Proposed plan:"), ""]
        for i, step in enumerate(steps, start=1):
            lines.append(f"{i}. {step.get('title') or step.get('action')}")
        lines.append("")
        lines.append(_ask_next_step(session))
        return "\n".join(lines)

    def _execute_steps(
        self,
        session_id: str,
        session: ProcedureSession,
        count: int,
        *,
        on_thought: ThoughtCallback | None = None,
    ) -> str:
        parts: list[str] = []
        executed = 0
        while (
            executed < count
            and session.current_index < len(session.steps)
        ):
            step = session.steps[session.current_index]
            action = str(step.get("action", "")).strip()
            arguments = step.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {}
            title = step.get("title") or action
            if on_thought:
                on_thought(f"Running step {session.current_index + 1}: {title}…")

            reply = self._dispatch(
                action,
                arguments,
                session.original_request,
                on_thought=on_thought,
            )
            parts.append(f"### {title}\n\n{reply}")
            session.current_index += 1
            executed += 1
            self._procedures.set(session_id, session)

        if session.current_index >= len(session.steps):
            session.status = "done"
            self._procedures.clear(session_id)
            parts.append("\nProcedure completed.")
        else:
            session.status = "awaiting_confirm"
            self._procedures.set(session_id, session)
            parts.append("\n" + _ask_next_step(session))
        return "\n\n".join(parts)

    def _handle_active_procedure(
        self,
        user_message: str,
        session_id: str,
        session: ProcedureSession,
        *,
        on_thought: ThoughtCallback | None = None,
    ) -> str:
        """Interpret the user's reply with the LLM, then act on the plan."""
        idx = session.current_index
        step = session.steps[idx] if idx < len(session.steps) else {}
        title = str(step.get("title") or f"step {idx + 1}")
        reply = classify_procedure_reply(
            self._llm,
            user_message,
            step_title=title,
            step_index=idx + 1,
            total_steps=len(session.steps),
        )
        if reply["thought"] and on_thought:
            on_thought(reply["thought"])

        decision = reply["decision"]
        if decision == "cancel":
            self._procedures.clear(session_id)
            return "Procedure cancelled."

        if decision == "run_all":
            remaining = len(session.steps) - session.current_index
            return self._execute_steps(
                session_id,
                session,
                remaining,
                on_thought=on_thought,
            )

        if decision == "confirm":
            return self._execute_steps(
                session_id,
                session,
                1,
                on_thought=on_thought,
            )

        # other — keep plan; ask again in natural language
        return (
            "Todavía hay un procedimiento pendiente.\n\n"
            + _ask_next_step(session)
        )

    def run(
        self,
        user_message: str,
        *,
        session_id: str | None = None,
        on_thought: ThoughtCallback | None = None,
    ) -> str:
        log.info(
            "Message received=%s session_id=%s",
            user_message[:120],
            session_id or "-",
        )
        if on_thought:
            on_thought("Analyzing your message…")

        active = self._procedures.get(session_id)
        if active and active.status == "awaiting_confirm":
            return self._handle_active_procedure(
                user_message,
                session_id or "",
                active,
                on_thought=on_thought,
            )

        classified = classify_intent(self._llm, user_message)
        if classified["thought"] and on_thought:
            on_thought(classified["thought"])

        intent = classified["intent"]
        if intent == "OUT_OF_SCOPE":
            return str(
                classified.get("message")
                or "I can only help with OpenShift, Kubernetes, AAP, and ITSM."
            )
        if intent == "AMBIGUOUS":
            return str(
                classified.get("message")
                or "Could you clarify whether you want a status check, "
                "a single action, or a full procedure?"
            )

        if intent == "C":
            if not session_id:
                return (
                    "I need a session id to run guided procedures. "
                    "Please retry from the chat UI."
                )
            return self._start_procedure(
                user_message,
                session_id,
                classified.get("rag_query"),
                on_thought=on_thought,
            )

        # A / B — existing atomic path
        return self._run_atomic(user_message, on_thought=on_thought)
