import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

from config import Settings
from llm import LLMClient


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def summarize(text: str, max_len: int = 100) -> str:
    if not text:
        return ""
    one_line = " ".join(text.split())
    if len(one_line) <= max_len:
        return one_line
    return one_line[: max_len - 1].rstrip() + "…"


class EventReporter:
    """Fire-and-forget event reporter for the external monitor service."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._run_id: str | None = None
        self._seq = 0
        self._llm_start: float | None = None
        self._llm_step_id: str | None = None
        self._llm_phase: str = "react"
        self._tool_step_id: str | None = None

    def _send(self, payload: dict, *, sync: bool = False) -> None:
        if not self._settings.monitor_url:
            return

        url = f"{self._settings.monitor_url.rstrip('/')}/events"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        def deliver() -> None:
            try:
                urllib.request.urlopen(req, timeout=self._settings.monitor_timeout)
            except (urllib.error.URLError, TimeoutError):
                pass

        if sync:
            deliver()
        else:
            threading.Thread(target=deliver, daemon=True).start()

    def _emit(self, event_type: str, data: dict | None = None, *, sync: bool = False) -> None:
        if not self._run_id:
            return

        self._seq += 1
        self._send(
            {
                "run_id": self._run_id,
                "type": event_type,
                "at": utc_now(),
                "seq": self._seq,
                "data": data or {},
            },
            sync=sync,
        )

    def begin(self, message: str) -> float:
        self._run_id = str(uuid.uuid4())
        self._seq = 0
        self._emit(
            "run_started",
            {
                "step_id": f"user-{self._run_id[:8]}",
                "message": message,
                "message_chars": len(message),
            },
            sync=True,
        )
        return time.perf_counter()

    def set_step(self, step: str) -> None:
        self._emit("step_changed", {"step": step})

    def thought(
        self,
        text: str,
        *,
        action: str | None = None,
        action_input: dict | None = None,
        raw_response: str | None = None,
        is_final: bool = False,
        final_answer: str | None = None,
        iteration: int = 0,
    ) -> None:
        self._emit(
            "thought",
            {
                "step_id": str(uuid.uuid4()),
                "thought": text,
                "action": action,
                "action_input": action_input or None,
                "response": raw_response,
                "is_final": is_final,
                "final_answer": final_answer,
                "summary": summarize(final_answer if is_final and final_answer else text),
                "iteration": iteration,
            },
            sync=True,
        )

    def begin_llm(
        self,
        *,
        phase: str = "respond",
        label: str | None = None,
        summary: str = "Calling LLM…",
        detail: dict | None = None,
    ) -> float:
        self._llm_start = time.perf_counter()
        self._llm_step_id = str(uuid.uuid4())
        self._llm_phase = phase
        self._emit(
            "llm_started",
            {
                "step_id": self._llm_step_id,
                "phase": phase,
                "label": label or phase,
                "summary": summary,
                "model": self._settings.llm_model or None,
                "endpoint": LLMClient.endpoint_for(self._settings.llm_url),
                "detail": detail or {},
            },
        )
        return self._llm_start

    def complete_llm(
        self,
        response: str,
        llm_duration_ms: int,
        *,
        summary: str | None = None,
        detail: dict | None = None,
    ) -> None:
        self._emit(
            "llm_done",
            {
                "step_id": self._llm_step_id,
                "phase": self._llm_phase,
                "duration_ms": llm_duration_ms,
                "response_chars": len(response),
                "summary": summary or summarize(response),
                "detail": {
                    **(detail or {}),
                    "response": response,
                    "duration_ms": llm_duration_ms,
                },
            },
            sync=True,
        )
        self._llm_step_id = None

    def fail_llm(self, error: str | None = None) -> int | None:
        if self._llm_start is None:
            return None
        llm_duration_ms = round((time.perf_counter() - self._llm_start) * 1000)
        self._emit(
            "llm_failed",
            {
                "step_id": self._llm_step_id,
                "duration_ms": llm_duration_ms,
                "summary": "LLM call failed",
                "detail": {"error": error, "duration_ms": llm_duration_ms},
            },
            sync=True,
        )
        self._llm_step_id = None
        return llm_duration_ms

    def begin_tool(
        self,
        tool: str,
        action: str,
        *,
        summary: str | None = None,
        detail: dict | None = None,
    ) -> str:
        self._tool_step_id = str(uuid.uuid4())
        self._emit(
            "tool_started",
            {
                "step_id": self._tool_step_id,
                "tool": tool,
                "action": action,
                "summary": summary or f"Calling {tool.upper()}…",
                "detail": detail or {},
            },
        )
        return self._tool_step_id

    def complete_tool(
        self,
        *,
        summary: str,
        detail: dict | None = None,
        duration_ms: int | None = None,
    ) -> None:
        self._emit(
            "tool_done",
            {
                "step_id": self._tool_step_id,
                "summary": summary,
                "duration_ms": duration_ms,
                "detail": detail or {},
            },
        )
        self._tool_step_id = None

    def fail_tool(self, error: str, duration_ms: int | None = None) -> None:
        self._emit(
            "tool_failed",
            {
                "step_id": self._tool_step_id,
                "summary": "Tool call failed",
                "detail": {"error": error, "duration_ms": duration_ms},
            },
        )
        self._tool_step_id = None

    def complete(self, response: str, duration_ms: int) -> None:
        self._emit(
            "run_done",
            {
                "step_id": f"result-{self._run_id[:8] if self._run_id else 'unknown'}",
                "response": response,
                "duration_ms": duration_ms,
                "response_chars": len(response),
                "summary": summarize(response),
            },
            sync=True,
        )

    def fail(self, error: str, duration_ms: int) -> None:
        self.fail_llm(error)
        self._emit(
            "run_failed",
            {
                "step_id": f"error-{self._run_id[:8] if self._run_id else 'unknown'}",
                "error": error,
                "duration_ms": duration_ms,
                "summary": summarize(error),
            },
            sync=True,
        )
