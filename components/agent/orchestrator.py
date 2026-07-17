import json
import time

from config import Settings
from itsm import ITSMClient
from llm import LLMClient
from mcp import MCPClient
from rag import RAGClient
from react import build_system_prompt, extract_prompts, parse_react_response
from reporter import EventReporter, summarize


class AgentOrchestrator:
    """ReAct agent: Thought / Action / Observation loop."""

    def __init__(
        self,
        settings: Settings,
        llm: LLMClient,
        mcp: MCPClient,
        itsm: ITSMClient,
        rag: RAGClient,
        reporter: EventReporter,
    ) -> None:
        self._settings = settings
        self._llm = llm
        self._mcp = mcp
        self._itsm = itsm
        self._rag = rag
        self._reporter = reporter

    def _execute_tool(self, action: str, action_input: dict) -> dict:
        if action == "rag_search":
            query = action_input.get("query", "")
            return self._rag.search(query)

        if action == "itsm_list_tickets":
            return {
                "tickets": self._itsm.list_tickets(
                    component=action_input.get("component"),
                    status=action_input.get("status"),
                    severity=action_input.get("severity"),
                )
            }

        if action == "itsm_create_ticket":
            return self._itsm.create_ticket(action_input)

        if action == "mcp_invoke":
            tool_name = action_input.get("tool_name", "")
            arguments = action_input.get("arguments") or {}
            return self._mcp.invoke(tool_name, arguments)

        return {"error": f"Unknown tool: {action}"}

    def _tool_monitor_key(self, action: str) -> tuple[str, str]:
        if action == "rag_search":
            return "rag", "search"
        if action.startswith("itsm_"):
            return "itsm", action.removeprefix("itsm_")
        if action == "mcp_invoke":
            return "mcp", "invoke"
        return "tool", action

    def _run_tool(self, action: str, action_input: dict) -> dict:
        tool, monitor_action = self._tool_monitor_key(action)
        request = {"action": action, "input": action_input}

        if action == "rag_search":
            tool_summary = "Consulting knowledge base…"
            tool_title_detail = {"label": "RAG"}
        else:
            tool_summary = f"Calling {action}…"
            tool_title_detail = {}

        start = time.perf_counter()
        self._reporter.begin_tool(
            tool,
            monitor_action,
            summary=tool_summary,
            detail={"request": request, **tool_title_detail},
        )
        try:
            result = self._execute_tool(action, action_input)
            duration_ms = round((time.perf_counter() - start) * 1000)
            summary = self._summarize_tool_result(action, result)
            self._reporter.complete_tool(
                summary=summary,
                detail={"request": request, "response": result},
                duration_ms=duration_ms,
            )
            return result
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start) * 1000)
            self._reporter.fail_tool(str(exc), duration_ms)
            raise

    @staticmethod
    def _summarize_tool_result(action: str, result: dict) -> str:
        if action == "rag_search":
            hits = result.get("results") or []
            if not hits:
                return "RAG consulted — no runbooks found"
            titles = ", ".join(item.get("title", "Untitled") for item in hits)
            return f"RAG consulted — {len(hits)} runbook(s): {titles}"
        if action == "itsm_list_tickets":
            tickets = result.get("tickets", []) if isinstance(result, dict) else []
            return f"Found {len(tickets)} ticket(s)"
        if action == "itsm_create_ticket":
            return f"Created ticket {result.get('id', '')}"
        if action == "mcp_invoke":
            return f"MCP tool completed"
        return "Tool completed"

    @staticmethod
    def _llm_step_labels(iteration: int) -> tuple[str, str, str]:
        if iteration == 0:
            return (
                "analyze",
                "Analyzing question",
                "Analyzing your question…",
            )
        return (
            "plan",
            "Planning next step",
            "Planning the next step…",
        )

    def _call_llm(self, messages: list[dict], *, iteration: int) -> str:
        phase, label, summary = self._llm_step_labels(iteration)
        llm_start = self._reporter.begin_llm(
            phase=phase,
            label=label,
            summary=summary,
            detail={
                "messages": messages,
                "prompts": extract_prompts(messages),
                "model": self._settings.llm_model,
            },
        )
        try:
            response = self._llm.chat(messages)
        except Exception:
            raise

        llm_duration_ms = round((time.perf_counter() - llm_start) * 1000)
        parsed = parse_react_response(response)
        self._reporter.complete_llm(
            response,
            llm_duration_ms,
            summary=f"{label} — done",
            detail={
                "messages": messages,
                "prompts": extract_prompts(messages),
                "model": self._settings.llm_model,
            },
        )
        return response

    def run(self, user_message: str) -> str:
        start = self._reporter.begin(user_message)

        messages: list[dict] = [
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": user_message},
        ]

        try:
            if self._settings.delay_seconds > 0:
                self._reporter.set_step("delay")
                time.sleep(self._settings.delay_seconds)

            for iteration in range(self._settings.max_react_iterations):
                raw_response = self._call_llm(messages, iteration=iteration)
                parsed = parse_react_response(raw_response)

                if (
                    parsed.get("thought")
                    or parsed.get("action")
                    or parsed.get("final_answer")
                ):
                    self._reporter.thought(
                        parsed.get("thought") or "",
                        action=parsed.get("action"),
                        action_input=parsed.get("action_input"),
                        raw_response=raw_response,
                        is_final=bool(parsed.get("final_answer")),
                        final_answer=parsed.get("final_answer"),
                        iteration=iteration,
                    )

                if parsed.get("final_answer"):
                    answer = parsed["final_answer"]
                    duration_ms = round((time.perf_counter() - start) * 1000)
                    self._reporter.complete(answer, duration_ms)
                    return answer

                action = parsed.get("action")
                if not action:
                    answer = raw_response.strip()
                    duration_ms = round((time.perf_counter() - start) * 1000)
                    self._reporter.complete(answer, duration_ms)
                    return answer

                observation = self._run_tool(action, parsed.get("action_input") or {})
                messages.append({"role": "assistant", "content": raw_response})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Observation: {json.dumps(observation, ensure_ascii=False)}\n\n"
                            "If you can answer the user now, use Final Answer with friendly "
                            "prose only — no JSON, tool names, or copy-pasted runbook text."
                        ),
                    }
                )

            fallback = (
                "I could not complete the request within the allowed number of steps."
            )
            duration_ms = round((time.perf_counter() - start) * 1000)
            self._reporter.complete(fallback, duration_ms)
            return fallback
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start) * 1000)
            self._reporter.fail(str(exc), duration_ms)
            raise
