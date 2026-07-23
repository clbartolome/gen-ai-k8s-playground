"""Shared logging setup for the agent service."""

from __future__ import annotations

import logging
import os
import sys


def setup_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    if root.handlers:
        root.setLevel(level)
        return
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )


def mask_secret(value: str, *, keep: int = 4) -> str:
    if not value:
        return "(empty)"
    if len(value) <= keep:
        return "*" * len(value)
    return f"…{value[-keep:]} (len={len(value)})"
