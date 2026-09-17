"""Exact-name matching for list/search tool results."""

from __future__ import annotations

import re
from typing import Any

_NAME_KEYS = (
    "name",
    "title",
    "job_template",
    "workflow_job_template",
    "username",
    "number",
)
_ID_KEYS = ("id", "pk", "uuid", "uid")


def quoted_name_from_step(step_detail: str | None) -> str:
    if not step_detail:
        return ""
    match = re.search(r'"([^"]+)"|“([^”]+)”', step_detail)
    if not match:
        return ""
    return (match.group(1) or match.group(2) or "").strip()


def step_requires_exact_search_match(step_detail: str, tool_name: str) -> bool:
    """True when a quoted target name must be matched exactly in list/search results."""
    if not quoted_name_from_step(step_detail):
        return False
    lowered = step_detail.casefold()
    if any(
        token in lowered
        for token in ("search", "find", "list", "look for", "lookup")
    ):
        return True
    tool_lower = tool_name.casefold()
    return "list" in tool_lower or "search" in tool_lower


def extract_item_id_by_exact_name(result: Any, wanted_name: str | None) -> str:
    """Return the id of the item whose display name matches wanted_name exactly."""
    item = extract_item_by_exact_name(result, wanted_name)
    if item is None:
        return ""
    for key in _ID_KEYS:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    metadata = item.get("metadata")
    if isinstance(metadata, dict):
        for key in _ID_KEYS:
            value = metadata.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    label = _item_label(item)
    return label


def extract_item_by_exact_name(
    result: Any,
    wanted_name: str | None,
) -> dict[str, Any] | None:
    wanted = (wanted_name or "").strip()
    if not wanted:
        return None

    matches: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            label = _item_label(node)
            if label == wanted and _item_id(node):
                matches.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(result)
    return matches[0] if matches else None


def _item_label(node: dict[str, Any]) -> str:
    for key in _NAME_KEYS:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = value.get("name")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
    metadata = node.get("metadata")
    if isinstance(metadata, dict):
        name = metadata.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return ""


def _item_id(node: dict[str, Any]) -> bool:
    for key in _ID_KEYS:
        value = node.get(key)
        if value is not None and str(value).strip():
            return True
    metadata = node.get("metadata")
    if isinstance(metadata, dict):
        for key in _ID_KEYS:
            value = metadata.get(key)
            if value is not None and str(value).strip():
                return True
    return bool(_item_label(node))
