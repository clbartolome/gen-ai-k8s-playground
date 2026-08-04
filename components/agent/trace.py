"""Structured process-trace nodes for the monitor UI."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Node types the monitor knows how to render.
NODE_TYPES = frozenset(
    {
        "user_message",
        "classified",
        "rag_intent",
        "article",
        "procedure",
        "missing_info",
        "user_input",
        "tool_call",
        "step",
        "final",
        "error",
    }
)

_NODE_ID_RE = re.compile(r"^n(\d+)$")


@dataclass
class TraceBuilder:
    """Collects curated timeline nodes for one conversation thread."""

    thread_id: str
    run_id: str
    _nodes: list[dict[str, Any]] = field(default_factory=list)
    _seq: int = 0
    _root_message: str = ""

    @classmethod
    def for_thread(
        cls,
        *,
        thread_id: str,
        run_id: str,
        existing: dict[str, Any] | None = None,
    ) -> TraceBuilder:
        """Resume an existing thread trace or start a new one."""
        nodes: list[dict[str, Any]] = []
        root_message = ""
        if existing:
            nodes = list(existing.get("nodes") or [])
            root_message = str(existing.get("user_message") or "")
        seq = 0
        for node in nodes:
            match = _NODE_ID_RE.match(str(node.get("id") or ""))
            if match:
                seq = max(seq, int(match.group(1)))
        return cls(
            thread_id=thread_id,
            run_id=run_id,
            _nodes=nodes,
            _seq=seq,
            _root_message=root_message,
        )

    def add(
        self,
        node_type: str,
        label: str,
        *,
        status: str = "ok",
        detail: dict[str, Any] | None = None,
        parallel_group: str | None = None,
    ) -> str:
        """Append a node and return its id."""
        self._seq += 1
        node_id = f"n{self._seq}"
        kind = node_type if node_type in NODE_TYPES else "step"
        payload = detail or {}
        if kind == "user_message" and not self._root_message:
            message = payload.get("message")
            if isinstance(message, str) and message.strip():
                self._root_message = message.strip()
        self._nodes.append(
            {
                "id": node_id,
                "type": kind,
                "label": (label or kind).strip() or kind,
                "status": status,
                "detail": payload,
                "parallel_group": parallel_group,
                "run_id": self.run_id,
            }
        )
        return node_id

    @property
    def nodes(self) -> list[dict[str, Any]]:
        return list(self._nodes)

    @property
    def root_message(self) -> str:
        return self._root_message

    def preview(self, category: str | None = None) -> str:
        """Short list label for the monitor sidebar (first user ask)."""
        text = " ".join((self._root_message or "").split())
        if not text:
            text = "Conversation"
        if len(text) > 72:
            text = text[:71].rstrip() + "…"
        if category:
            return f"[{category}] {text}"
        return text
