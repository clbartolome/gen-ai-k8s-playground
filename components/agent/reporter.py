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
        self._llm_start: float | None = None
        self._llm_step_id: str | None = None
        self._tool_step_id: str | None = None

    def _emit(self, event_type: str, data: dict | None = None) -> None:
        if not self._settings.monitor_url or not self._run_id:
            return

        payload = json.dumps(
            {
                "run_id": self._run_id,
                "type": event_type,
                "at": utc_now(),
                "data": data or {},
            }
        ).encode("utf-8")

        url = f"{self._settings.monitor_url.rstrip('/')}/events"
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        def send() -> None:
            try:
                urllib.request.urlopen(req, timeout=self._settings.monitor_timeout)
            except (urllib.error.URLError, TimeoutError):
                pass

        threading.Thread(target=send, daemon=True).start()

    def begin(self, message: str) -> float:
        self._run_id = str(uuid.uuid4())
        self._emit(
            "run_started",
            {
                "step_id": f"user-{self._run_id[:8]}",
                "message": message,
                "message_chars": len(message),
            },
        )
        return time.perf_counter()

    def set_step(self, step: str) -> None:
        self._emit("step_changed", {"step": step})

    def begin_llm(
        self,
        *,
        phase: str = "respond",
        summary: str = "Calling LLM…",
        detail: dict | None = None,
    ) -> float:
        self._llm_start = time.perf_counter()
        self._llm_step_id = str(uuid.uuid4())
        self._emit(
            "llm_started",
            {
                "step_id": self._llm_step_id,
                "phase": phase,
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
                "phase": "respond",
                "duration_ms": llm_duration_ms,
                "response_chars": len(response),
                "summary": summary or summarize(response),
                "detail": {
                    **(detail or {}),
                    "response": response,
                    "duration_ms": llm_duration_ms,
                },
            },
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
        )
