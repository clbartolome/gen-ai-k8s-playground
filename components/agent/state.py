import threading
import time
from datetime import datetime, timezone

from config import Settings
from llm import LLMClient


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunMonitor:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_run: dict = self._idle_state()
        self._llm_start: float | None = None

    @staticmethod
    def _idle_state() -> dict:
        return {
            "status": "idle",
            "step": None,
            "message": None,
            "response": None,
            "error": None,
            "started_at": None,
            "finished_at": None,
            "duration_ms": None,
            "llm_status": "idle",
            "llm_started_at": None,
            "llm_finished_at": None,
            "llm_duration_ms": None,
            "message_chars": None,
            "response_chars": None,
        }

    def begin(self, message: str) -> float:
        started_at = utc_now()
        with self._lock:
            self._last_run = {
                **self._idle_state(),
                "status": "processing",
                "step": "received",
                "message": message,
                "started_at": started_at,
                "message_chars": len(message),
            }
        return time.perf_counter()

    def set_step(self, step: str) -> None:
        with self._lock:
            self._last_run["step"] = step

    def begin_llm(self) -> float:
        self._llm_start = time.perf_counter()
        with self._lock:
            self._last_run.update(
                {
                    "step": "calling_llm",
                    "llm_status": "calling",
                    "llm_started_at": utc_now(),
                    "llm_finished_at": None,
                    "llm_duration_ms": None,
                }
            )
        return self._llm_start

    def complete_llm(self, response: str, llm_duration_ms: int) -> None:
        with self._lock:
            self._last_run.update(
                {
                    "llm_status": "success",
                    "llm_finished_at": utc_now(),
                    "llm_duration_ms": llm_duration_ms,
                    "response_chars": len(response),
                }
            )

    def fail_llm(self) -> int | None:
        if self._llm_start is None:
            return None
        llm_duration_ms = round((time.perf_counter() - self._llm_start) * 1000)
        with self._lock:
            if self._last_run.get("llm_status") == "calling":
                self._last_run.update(
                    {
                        "llm_status": "failed",
                        "llm_finished_at": utc_now(),
                        "llm_duration_ms": llm_duration_ms,
                    }
                )
        return llm_duration_ms

    def complete(self, response: str, duration_ms: int) -> None:
        with self._lock:
            self._last_run.update(
                {
                    "status": "done",
                    "step": None,
                    "response": response,
                    "finished_at": utc_now(),
                    "duration_ms": duration_ms,
                }
            )

    def fail(self, error: str, duration_ms: int) -> None:
        self.fail_llm()
        with self._lock:
            self._last_run.update(
                {
                    "status": "error",
                    "error": error,
                    "finished_at": utc_now(),
                    "duration_ms": duration_ms,
                }
            )

    def snapshot(self, settings: Settings) -> dict:
        with self._lock:
            data = dict(self._last_run)
        data["llm_model"] = settings.llm_model or None
        data["llm_endpoint"] = LLMClient.endpoint_for(settings.llm_url)
        data["llm_timeout_s"] = settings.llm_timeout
        return data
