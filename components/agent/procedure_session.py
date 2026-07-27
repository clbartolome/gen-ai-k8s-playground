"""In-memory procedure sessions for guided Type-C flows."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class ProcedureSession:
    original_request: str
    sop_excerpt: str
    steps: list[dict] = field(default_factory=list)
    current_index: int = 0
    status: str = "awaiting_confirm"


class ProcedureStore:
    """Thread-safe session store keyed by session_id."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, ProcedureSession] = {}

    def get(self, session_id: str | None) -> ProcedureSession | None:
        if not session_id:
            return None
        with self._lock:
            return self._sessions.get(session_id)

    def set(self, session_id: str, session: ProcedureSession) -> None:
        with self._lock:
            self._sessions[session_id] = session

    def clear(self, session_id: str | None) -> None:
        if not session_id:
            return
        with self._lock:
            self._sessions.pop(session_id, None)
