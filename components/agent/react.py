"""Minimal ReAct agent: route intent, then reply with the operation type."""

from __future__ import annotations

import logging

from llm import LLMClient
from prompts import build_router_prompt, build_system_prompt

log = logging.getLogger("agent.react")


class ReactAgent:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self._router_prompt = build_router_prompt()
        self._system_prompt = build_system_prompt()

    def _route(self, user_message: str) -> str:
        messages = [
            {"role": "system", "content": self._router_prompt},
            {"role": "user", "content": user_message},
        ]
        classification = self._llm.chat(messages)
        log.info("Router result=%s", classification.strip())
        return classification

    def run(self, user_message: str) -> str:
        classification = self._route(user_message)
        messages = [
            {"role": "system", "content": self._system_prompt},
            {
                "role": "user",
                "content": (
                    f"User request:\n{user_message}\n\n"
                    f"Router classification:\n{classification.strip()}"
                ),
            },
        ]
        log.info("ReAct run chars=%s", len(user_message))
        return self._llm.chat(messages)
