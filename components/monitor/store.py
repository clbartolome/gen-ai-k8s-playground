import threading
from collections import deque
from datetime import datetime, timezone

from flow import build_flow


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventStore:
    def __init__(self, max_runs: int = 10, max_events_per_run: int = 100) -> None:
        self._lock = threading.Lock()
        self._max_runs = max_runs
        self._max_events_per_run = max_events_per_run
        self._runs: dict[str, dict] = {}
        self._run_order: deque[str] = deque(maxlen=max_runs)

    def add_event(
        self,
        run_id: str,
        event_type: str,
        data: dict | None = None,
        at: str | None = None,
    ) -> None:
        timestamp = at or utc_now()
        event = {
            "type": event_type,
            "at": timestamp,
            "data": data or {},
        }

        with self._lock:
            if run_id not in self._runs:
                self._runs[run_id] = {
                    "run_id": run_id,
                    "status": "processing",
                    "message": None,
                    "response": None,
                    "error": None,
                    "started_at": timestamp,
                    "finished_at": None,
                    "duration_ms": None,
                    "events": deque(maxlen=self._max_events_per_run),
                }
                self._run_order.appendleft(run_id)

            run = self._runs[run_id]
            run["events"].append(event)
            self._apply_event(run, event_type, data or {}, timestamp)

    def _apply_event(
        self, run: dict, event_type: str, data: dict, timestamp: str
    ) -> None:
        if event_type == "run_started":
            run["status"] = "processing"
            run["message"] = data.get("message")
            run["started_at"] = timestamp
        elif event_type == "run_done":
            run["status"] = "done"
            run["response"] = data.get("response")
            run["finished_at"] = timestamp
            run["duration_ms"] = data.get("duration_ms")
        elif event_type == "run_failed":
            run["status"] = "error"
            run["error"] = data.get("error")
            run["finished_at"] = timestamp
            run["duration_ms"] = data.get("duration_ms")

    def snapshot(self) -> dict:
        with self._lock:
            if not self._run_order:
                return {"latest_run": None, "recent_runs": []}

            latest_id = self._run_order[0]
            latest = self._serialize_run(self._runs[latest_id])
            recent = [
                self._run_summary(self._runs[run_id])
                for run_id in list(self._run_order)
            ]
            return {"latest_run": latest, "recent_runs": recent}

    def get_run(self, run_id: str) -> dict | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            return self._serialize_run(run)

    @staticmethod
    def _run_summary(run: dict) -> dict:
        return {
            "run_id": run["run_id"],
            "status": run["status"],
            "message": run["message"],
            "started_at": run["started_at"],
            "finished_at": run["finished_at"],
            "duration_ms": run["duration_ms"],
        }

    def _serialize_run(self, run: dict) -> dict:
        events = list(run["events"])
        return {
            "run_id": run["run_id"],
            "status": run["status"],
            "message": run["message"],
            "response": run["response"],
            "error": run["error"],
            "started_at": run["started_at"],
            "finished_at": run["finished_at"],
            "duration_ms": run["duration_ms"],
            "events": events,
            "flow": build_flow(events),
        }
