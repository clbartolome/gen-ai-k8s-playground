import json
import logging
import time
from typing import Any

from config import KB_MCP_TOOLS, Settings
from intent import classify_agent_reply, classify_user_intent
from itsm_mcp import ItsmMcpClient
from llm import LLMClient
from mcp import MCPClient
from react import build_system_prompt, format_single_step, parse_react_response
from thoughts import (
    ThoughtCallback,
    clean_model_thought,
    summarize_tool,
    tool_start_message,
)

log = logging.getLogger("agent.orchestrator")


class AgentOrchestrator:
    """ReAct agent: Thought / Action / Observation loop."""

    def __init__(
        self,
        settings: Settings,
        llm: LLMClient,
        platform_mcp: MCPClient,
        itsm_mcp: ItsmMcpClient,
    ) -> None:
        self._settings = settings
        self._llm = llm
        self._platform_mcp = platform_mcp
        self._itsm_mcp = itsm_mcp
        self._itsm_tools: list[dict[str, Any]] | None = None
        self._itsm_tool_names: set[str] = set()

    def _emit(self, on_thought: ThoughtCallback | None, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        log.info("Thought: %s", text)
        if on_thought:
            on_thought(text)

    def _load_itsm_tools(self) -> list[dict[str, Any]]:
        if self._itsm_tools is None:
            log.info("Loading ITSM MCP tools…")
            self._itsm_tools = self._itsm_mcp.list_tools()
            self._itsm_tool_names = {
                t.get("name") for t in self._itsm_tools if t.get("name")
            }
            log.info("ITSM MCP tools ready: %s", sorted(self._itsm_tool_names))
        return self._itsm_tools

    def _tool_kind(self, action: str) -> str:
        if action in KB_MCP_TOOLS or "kb" in action:
            return "kb"
        if action == "mcp_invoke":
            return "mcp"
        if action in self._itsm_tool_names or any(
            key in action
            for key in ("incident", "request", "change", "task", "asset", "comment")
        ):
            return "itsm"
        return "tool"

    def _execute_tool(self, action: str, action_input: dict) -> Any:
        if action == "mcp_invoke":
            return self._platform_mcp.invoke(
                action_input.get("tool_name", ""),
                action_input.get("arguments") or {},
            )
        if action in self._itsm_tool_names:
            return self._itsm_mcp.call_tool(action, action_input)
        self._load_itsm_tools()
        if action in self._itsm_tool_names:
            return self._itsm_mcp.call_tool(action, action_input)
        return {"error": f"Unknown tool: {action}"}

    def _run_tool(
        self,
        action: str,
        action_input: dict,
        *,
        on_thought: ThoughtCallback | None,
    ) -> Any:
        kind = self._tool_kind(action)
        self._emit(on_thought, tool_start_message(action, kind, action_input))
        start = time.perf_counter()
        try:
            result = self._execute_tool(action, action_input)
        except Exception as exc:
            self._emit(on_thought, f"Tool failed ({action}): {exc}")
            raise
        duration_ms = round((time.perf_counter() - start) * 1000)
        summary = summarize_tool(action, kind, result)
        log.info("Tool done action=%s duration_ms=%s summary=%s", action, duration_ms, summary)
        self._emit(on_thought, summary)
        return result

    def _classify_execute_reply(self, final_answer: str) -> str:
        try:
            return classify_agent_reply(self._llm, final_answer)
        except Exception:
            log.exception("Reply classification failed; treating as premature")
            return "premature"

    def _call_llm(self, messages: list[dict], *, iteration: int) -> str:
        log.info(
            "LLM call start iteration=%s model=%s messages=%s",
            iteration,
            self._settings.llm_model,
            len(messages),
        )
        start = time.perf_counter()
        response = self._llm.chat(messages)
        log.info(
            "LLM call done iteration=%s duration_ms=%s preview=%s",
            iteration,
            round((time.perf_counter() - start) * 1000),
            " ".join(response.split())[:200],
        )
        return response

    def run(
        self,
        user_message: str,
        *,
        history: list[dict] | None = None,
        on_thought: ThoughtCallback | None = None,
    ) -> str:
        log.info("Run start preview=%s", user_message[:120])
        self._itsm_tools = None
        tools = self._load_itsm_tools()

        messages: list[dict] = [
            {"role": "system", "content": build_system_prompt(tools=tools)},
        ]
        for turn in history or []:
            role = turn.get("role")
            content = turn.get("content")
            if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_message})

        self._emit(on_thought, "Understanding what you need…")
        try:
            intent_info = classify_user_intent(
                self._llm, user_message, history=history
            )
        except Exception:
            log.exception("Intent classification failed; defaulting to inform")
            intent_info = {
                "intent": "inform",
                "task_mode": "inform",
                "reason": "classifier_error",
            }

        execute = intent_info.get("task_mode") == "execute"
        intent_label = intent_info.get("intent", "inform")
        if execute and intent_label == "clarify":
            self._emit(
                on_thought,
                "Got your details — continuing the workflow.",
            )
        elif execute:
            self._emit(
                on_thought,
                "You asked me to do this, not only explain — I'll run the full workflow.",
            )
        else:
            self._emit(
                on_thought,
                "I'll look up guidance and explain — not running automation unless you ask.",
            )

        kb_consulted = False
        automation_done = False
        kb_reminder = (
            "You must NOT give a Final Answer yet. First search Knowledge Base articles "
            "with Action: rag_search_kb (preferred) or Action: search_kb. "
            "Wait for the Observation before continuing."
        )
        execute_reminder = (
            "This is an EXECUTE request. Reply with EXACTLY ONE Action, then stop.\n"
            "Either: Final Answer asking only for missing VM fields, OR\n"
            "create_incident, then (next turn) mcp_invoke create_vm/delete_vm, then Final Answer.\n"
            "Do not stack multiple Actions in one reply."
        )

        if self._settings.delay_seconds > 0:
            time.sleep(self._settings.delay_seconds)

        for iteration in range(self._settings.max_react_iterations):
            log.info("ReAct iteration=%s/%s", iteration + 1, self._settings.max_react_iterations)
            raw_response = self._call_llm(messages, iteration=iteration)
            parsed = parse_react_response(raw_response)
            log.info(
                "Parsed action=%s final=%s kb=%s auto=%s",
                parsed.get("action"),
                bool(parsed.get("final_answer")),
                kb_consulted,
                automation_done,
            )

            thought = clean_model_thought(parsed.get("thought"))
            if thought and (parsed.get("action") or parsed.get("final_answer")):
                self._emit(on_thought, thought)

            if parsed.get("final_answer") and not kb_consulted:
                self._emit(on_thought, "I need to check the knowledge base before answering…")
                messages.append({"role": "assistant", "content": raw_response})
                messages.append({"role": "user", "content": kb_reminder})
                continue

            if (
                execute
                and parsed.get("final_answer")
                and kb_consulted
                and not automation_done
            ):
                reply_kind = self._classify_execute_reply(parsed["final_answer"])
                if reply_kind == "premature":
                    self._emit(
                        on_thought,
                        "That was only an explanation — continuing with the real workflow…",
                    )
                    messages.append({"role": "assistant", "content": raw_response})
                    messages.append({"role": "user", "content": execute_reminder})
                    continue

            if parsed.get("final_answer"):
                return parsed["final_answer"]

            action = parsed.get("action")
            if not action:
                if not kb_consulted:
                    messages.append({"role": "assistant", "content": raw_response})
                    messages.append({"role": "user", "content": kb_reminder})
                    continue
                if execute and not automation_done:
                    reply_kind = self._classify_execute_reply(raw_response)
                    if reply_kind == "premature":
                        messages.append({"role": "assistant", "content": raw_response})
                        messages.append({"role": "user", "content": execute_reminder})
                        continue
                return raw_response.strip()

            observation = self._run_tool(
                action,
                parsed.get("action_input") or {},
                on_thought=on_thought,
            )
            kind = self._tool_kind(action)
            if kind == "kb":
                kb_consulted = True
            if action == "mcp_invoke":
                automation_done = True

            follow_up = (
                f"Observation: {json.dumps(observation, ensure_ascii=False)}\n\n"
                "Continue with EXACTLY ONE next Action (or Final Answer). "
                "Do not invent later steps or Observations."
            )
            if execute and not automation_done:
                follow_up += (
                    " EXECUTE: next create_incident or mcp_invoke create_vm/delete_vm, "
                    "or Final Answer if fields are still missing."
                )

            messages.append(
                {
                    "role": "assistant",
                    "content": format_single_step(
                        thought=parsed.get("thought"),
                        action=action,
                        action_input=parsed.get("action_input") or {},
                    ),
                }
            )
            messages.append({"role": "user", "content": follow_up})

        return "I could not complete the request within the allowed number of steps."
