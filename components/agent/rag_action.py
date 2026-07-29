"""Process RAG requests classified as ACTION (create / execute).

This module is the entry point for procedure execution derived from the
knowledge base. Information lookups stay in the RAG path in react.py.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("agent.rag_action")

_PLACEHOLDER_RESPONSE = (
    "I understood you want me to perform an action, but executing procedures "
    "from the knowledge base is not available yet. Ask for information about "
    "the procedure, or try again later."
)


def run_rag_action(
    user_message: str,
    *,
    dialogue: list[dict[str, Any]] | None = None,
) -> str:
    """Handle an ACTION intent under the RAG category.

    For now returns a placeholder reply. Future work will resolve a procedure
    from the knowledge base and execute it.
    """
    log.info(
        "RAG action started message_chars=%s dialogue_turns=%s",
        len(user_message or ""),
        len(dialogue or []),
    )
    return _PLACEHOLDER_RESPONSE
