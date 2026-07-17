import json
import logging
import time
from typing import Any

from config import KB_MCP_TOOLS, Settings
from itsm_mcp import ItsmMcpClient
from llm import LLMClient
from mcp import MCPClient
from react import build_system_prompt, extract_prompts, parse_react_response
from reporter import EventReporter

log = logging.getLogger("agent.orchestrator")


class AgentOrchestrator:
    """ReAct agent: Thought / Action / Observation loop."""

    def __init__(
        self,
        settings: Settings,
        llm: LLMClient,
        platform_mcp: MCPClient,
        itsm_mcp: ItsmMcpClient,
        reporter: EventReporter,
    ) -> None:
        self._settings = settings
        self._llm = llm
        self._platform_mcp = platform_mcp
        self._itsm_mcp = itsm_mcp
        self._reporter = reporter
        self._itsm_tools: list[dict[str, Any]] | None = None
        self._itsm_tool_names: set[str] = set()

    def _load_itsm_tools(self) -> list[dict[str, Any]]:
        if self._itsm_tools is None:
            log.info("Loading ITSM MCP tools…")
            try:
                self._itsm_tools = self._itsm_mcp.list_tools()
            except Exception:
                log.exception("Failed to list ITSM MCP tools")
                raise
            self._itsm_tool_names = {
                t.get("name") for t in self._itsm_tools if t.get("name")
            }
            log.info("ITSM MCP tools ready: %s", sorted(self._itsm_tool_names))
        return self._itsm_tools

    def _execute_tool(self, action: str, action_input: dict) -> Any:
        if action == "mcp_invoke":
            tool_name = action_input.get("tool_name", "")
            arguments = action_input.get("arguments") or {}
            return self._platform_mcp.invoke(tool_name, arguments)

        if action in self._itsm_tool_names:
            return self._itsm_mcp.call_tool(action, action_input)

        # Lazy load if first tool call happens before prompt build (should not).
        self._load_itsm_tools()
        if action in self._itsm_tool_names:
            return self._itsm_mcp.call_tool(action, action_input)

        return {"error": f"Unknown tool: {action}"}

    def _tool_monitor_key(self, action: str) -> tuple[str, str]:
        if action in KB_MCP_TOOLS or "kb" in action:
            return "kb", action
        if action == "mcp_invoke":
            return "mcp", "invoke"
        if action in self._itsm_tool_names or any(
            key in action
            for key in (
                "incident",
                "request",
                "change",
                "task",
                "asset",
                "comment",
                "severity",
            )
        ):
            return "itsm", action
        return "tool", action

    def _run_tool(self, action: str, action_input: dict) -> Any:
        tool, monitor_action = self._tool_monitor_key(action)
        request = {"action": action, "input": action_input}
        log.info(
            "Tool start kind=%s action=%s input=%s",
            tool,
            action,
            action_input,
        )

        if tool == "kb":
            tool_summary = "Searching knowledge base articles…"
            tool_title_detail = {"label": "Knowledge Base"}
        elif tool == "itsm":
            tool_summary = f"Calling ITSM · {action}…"
            tool_title_detail = {"label": "ITSM"}
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
            summary = self._summarize_tool_result(action, tool, result)
            log.info(
                "Tool done kind=%s action=%s duration_ms=%s summary=%s",
                tool,
                action,
                duration_ms,
                summary,
            )
            self._reporter.complete_tool(
                summary=summary,
                detail={"request": request, "response": result},
                duration_ms=duration_ms,
            )
            return result
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start) * 1000)
            log.exception(
                "Tool failed kind=%s action=%s duration_ms=%s error=%s",
                tool,
                action,
                duration_ms,
                exc,
            )
            self._reporter.fail_tool(str(exc), duration_ms)
            raise

    @staticmethod
    def _summarize_tool_result(action: str, tool: str, result: Any) -> str:
        if tool == "kb":
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
                    return f"KB articles — {len(hits)} hit(s): {titles}"
                if result.get("title"):
                    return f"KB article — {result.get('title')}"
            return "KB articles consulted"
        if tool == "itsm":
            if action == "create_incident" and isinstance(result, dict):
                ref = result.get("incident_ref") or result.get("id") or ""
                return f"Created incident {ref}".strip()
            if action == "list_incidents" and isinstance(result, dict):
                items = result.get("incidents") or result.get("items") or []
                if isinstance(items, list):
                    return f"Found {len(items)} incident(s)"
            return f"ITSM · {action} completed"
        if action == "mcp_invoke":
            return "MCP tool completed"
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
        log.info(
            "LLM call start iteration=%s phase=%s model=%s messages=%s",
            iteration,
            phase,
            self._settings.llm_model,
            len(messages),
        )
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
            log.exception("LLM call failed iteration=%s phase=%s", iteration, phase)
            raise

        llm_duration_ms = round((time.perf_counter() - llm_start) * 1000)
        log.info(
            "LLM call done iteration=%s duration_ms=%s response_chars=%s preview=%s",
            iteration,
            llm_duration_ms,
            len(response),
            " ".join(response.split())[:200],
        )
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
        log.info("Run start message_chars=%s preview=%s", len(user_message), user_message[:120])
        start = self._reporter.begin(user_message)

        # Refresh MCP tool catalog each run (MLflow-friendly: resolved prompt is per-run).
        self._itsm_tools = None
        tools = self._load_itsm_tools()
        messages: list[dict] = [
            {"role": "system", "content": build_system_prompt(tools=tools)},
            {"role": "user", "content": user_message},
        ]
        log.info(
            "System prompt built tools=%s prompt_chars=%s",
            [t.get("name") for t in tools],
            len(messages[0]["content"]),
        )

        kb_consulted = False
        kb_reminder = (
            "You must NOT give a Final Answer yet. First search Knowledge Base articles "
            "with Action: rag_search_kb (preferred) or Action: search_kb and a suitable "
            'Action Input JSON (e.g. {"query": "delete virtual machine"}). '
            "Wait for the Observation before answering."
        )

        try:
            if self._settings.delay_seconds > 0:
                self._reporter.set_step("delay")
                time.sleep(self._settings.delay_seconds)

            for iteration in range(self._settings.max_react_iterations):
                log.info("ReAct iteration=%s/%s", iteration + 1, self._settings.max_react_iterations)
                raw_response = self._call_llm(messages, iteration=iteration)
                parsed = parse_react_response(raw_response)
                log.info(
                    "Parsed action=%s final=%s thought_chars=%s kb_consulted=%s",
                    parsed.get("action"),
                    bool(parsed.get("final_answer")),
                    len(parsed.get("thought") or ""),
                    kb_consulted,
                )

                # Enforce KB search before any Final Answer (demo policy).
                if parsed.get("final_answer") and not kb_consulted:
                    log.warning(
                        "Rejecting Final Answer before KB search (iteration=%s)",
                        iteration,
                    )
                    messages.append({"role": "assistant", "content": raw_response})
                    messages.append({"role": "user", "content": kb_reminder})
                    continue

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
                    log.info("Run complete via Final Answer duration_ms=%s", duration_ms)
                    self._reporter.complete(answer, duration_ms)
                    return answer

                action = parsed.get("action")
                if not action:
                    if not kb_consulted:
                        log.warning(
                            "Rejecting plain reply before KB search (iteration=%s)",
                            iteration,
                        )
                        messages.append({"role": "assistant", "content": raw_response})
                        messages.append({"role": "user", "content": kb_reminder})
                        continue
                    answer = raw_response.strip()
                    duration_ms = round((time.perf_counter() - start) * 1000)
                    log.info("Run complete via raw text (no Action) duration_ms=%s", duration_ms)
                    self._reporter.complete(answer, duration_ms)
                    return answer

                observation = self._run_tool(action, parsed.get("action_input") or {})
                if self._tool_monitor_key(action)[0] == "kb":
                    kb_consulted = True
                    log.info("KB consulted via action=%s", action)

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
            log.warning("Run exhausted iterations duration_ms=%s", duration_ms)
            self._reporter.complete(fallback, duration_ms)
            return fallback
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start) * 1000)
            log.exception("Run failed duration_ms=%s error=%s", duration_ms, exc)
            self._reporter.fail(str(exc), duration_ms)
            raise
