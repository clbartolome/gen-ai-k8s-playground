import json
import os
import threading
import time
from collections import deque
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"

state_lock = threading.Lock()
activity: dict[str, deque] = {
    "mcp": deque(maxlen=8),
    "itsm": deque(maxlen=8),
    "rag": deque(maxlen=8),
}

_tickets: list[dict] | None = None
_request_starts: dict[int, float] = {}


def load_scenario() -> dict:
    with open(DATA_DIR / "scenario.json", encoding="utf-8") as fh:
        return json.load(fh)


def get_tickets() -> list[dict]:
    global _tickets
    if _tickets is None:
        _tickets = [dict(ticket) for ticket in load_scenario()["tickets"]]
    return _tickets


def install_request_logging(app, service: str) -> None:
    @app.before_request
    def _start_timer():
        _request_starts[id(app)] = time.perf_counter()

    @app.after_request
    def _log_request(response):
        start = _request_starts.pop(id(app), time.perf_counter())
        duration_ms = round((time.perf_counter() - start) * 1000)
        from flask import request

        record_activity(
            service,
            request.method,
            request.path,
            response.status_code,
            duration_ms,
        )
        return response


def record_activity(
    service: str, method: str, path: str, status: int, duration_ms: int
) -> None:
    entry = {
        "method": method,
        "path": path,
        "status": status,
        "duration_ms": duration_ms,
        "at": time.strftime("%H:%M:%S", time.gmtime()),
    }
    with state_lock:
        activity[service].appendleft(entry)


def activity_snapshot() -> dict:
    with state_lock:
        return {name: list(entries) for name, entries in activity.items()}


def port(name: str, default: int) -> int:
    return int(os.environ.get(f"{name.upper()}_PORT", str(default)))
