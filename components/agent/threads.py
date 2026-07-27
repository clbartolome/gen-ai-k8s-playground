"""In-memory conversation threads: dialogue + compact tool trace + pending ask."""

from __future__ import annotations

import threading
import uuid
from copy import deepcopy
from typing import Any


# Keep thread context small enough for ~16k-token workshop models.
_MAX_DIALOGUE_TURNS = 20
_MAX_TRACE_ENTRIES = 8
_MAX_SUMMARY_CHARS = 2_000


def new_thread_id() -> str:
    return str(uuid.uuid4())


def empty_thread() -> dict[str, Any]:
    return {
        "dialogue": [],
        "trace": [],
        "pending": None,
    }


def _clip_summary(text: str, limit: int = _MAX_SUMMARY_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


class ThreadStore:
    """Process-local thread memory (lost on pod restart; fine for the playground)."""

    def __init__(self) -> None:
        self._threads: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def get_or_create(self, thread_id: str | None) -> tuple[str, dict[str, Any]]:
        with self._lock:
            if thread_id and thread_id in self._threads:
                return thread_id, deepcopy(self._threads[thread_id])
            created = thread_id or new_thread_id()
            if created not in self._threads:
                self._threads[created] = empty_thread()
            return created, deepcopy(self._threads[created])

    def snapshot(self, thread_id: str) -> dict[str, Any] | None:
        with self._lock:
            thread = self._threads.get(thread_id)
            return deepcopy(thread) if thread is not None else None

    def commit_turn(
        self,
        thread_id: str,
        *,
        user_message: str,
        assistant_message: str,
        action: str,
        pending: dict[str, Any] | None = None,
        trace_entry: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            thread = self._threads.setdefault(thread_id, empty_thread())
            dialogue: list[dict[str, str]] = thread["dialogue"]
            dialogue.append({"role": "user", "content": user_message})
            dialogue.append({"role": "assistant", "content": assistant_message})
            if len(dialogue) > _MAX_DIALOGUE_TURNS:
                thread["dialogue"] = dialogue[-_MAX_DIALOGUE_TURNS:]

            if trace_entry:
                entry = dict(trace_entry)
                summary = entry.get("summary")
                if isinstance(summary, str):
                    entry["summary"] = _clip_summary(summary)
                trace: list[dict[str, Any]] = thread["trace"]
                trace.append(entry)
                if len(trace) > _MAX_TRACE_ENTRIES:
                    thread["trace"] = trace[-_MAX_TRACE_ENTRIES:]

            if action == "request_information" and pending:
                thread["pending"] = pending
            else:
                thread["pending"] = None

            return deepcopy(thread)
