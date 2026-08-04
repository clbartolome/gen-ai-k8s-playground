"""SQLite persistence for agent process traces (read by the monitor)."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("agent.trace_store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    thread_id TEXT PRIMARY KEY,
    last_run_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,
    category TEXT,
    user_message TEXT NOT NULL,
    response TEXT,
    preview TEXT NOT NULL,
    nodes_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_traces_updated ON traces(updated_at DESC);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TraceStore:
    """Process-local SQLite store: one durable timeline per conversation thread."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()
        log.info("Trace store ready path=%s", self._path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                self._migrate_if_needed(conn)
                conn.executescript(_SCHEMA)

    def _migrate_if_needed(self, conn: sqlite3.Connection) -> None:
        """Drop legacy per-run traces so the playground can use thread timelines."""
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='traces'"
        ).fetchone()
        if not row or not row["sql"]:
            return
        ddl = row["sql"]
        if "thread_id TEXT PRIMARY KEY" in ddl:
            return
        log.info("Migrating traces table to thread-scoped schema")
        conn.execute("DROP TABLE traces")

    def upsert(
        self,
        *,
        thread_id: str,
        run_id: str,
        status: str,
        user_message: str,
        preview: str,
        category: str | None = None,
        response: str | None = None,
        nodes: list[dict[str, Any]] | None = None,
    ) -> None:
        now = _utc_now()
        nodes_json = json.dumps(nodes or [], ensure_ascii=False)
        with self._lock:
            with self._connect() as conn:
                existing = conn.execute(
                    "SELECT created_at FROM traces WHERE thread_id = ?",
                    (thread_id,),
                ).fetchone()
                created_at = existing["created_at"] if existing else now
                conn.execute(
                    """
                    INSERT INTO traces (
                        thread_id, last_run_id, created_at, updated_at, status,
                        category, user_message, response, preview, nodes_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(thread_id) DO UPDATE SET
                        last_run_id = excluded.last_run_id,
                        updated_at = excluded.updated_at,
                        status = excluded.status,
                        category = COALESCE(excluded.category, traces.category),
                        user_message = excluded.user_message,
                        response = excluded.response,
                        preview = excluded.preview,
                        nodes_json = excluded.nodes_json
                    """,
                    (
                        thread_id,
                        run_id,
                        created_at,
                        now,
                        status,
                        category,
                        user_message,
                        response,
                        preview,
                        nodes_json,
                    ),
                )

    def list_traces(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT thread_id, last_run_id, created_at, updated_at, status,
                           category, preview, user_message
                    FROM traces
                    ORDER BY updated_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                ).fetchall()
        return [dict(row) for row in rows]

    def get_trace(self, thread_id: str) -> dict[str, Any] | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT thread_id, last_run_id, created_at, updated_at, status,
                           category, user_message, response, preview, nodes_json
                    FROM traces
                    WHERE thread_id = ?
                    """,
                    (thread_id,),
                ).fetchone()
        if row is None:
            return None
        data = dict(row)
        try:
            data["nodes"] = json.loads(data.pop("nodes_json") or "[]")
        except json.JSONDecodeError:
            data["nodes"] = []
        return data
