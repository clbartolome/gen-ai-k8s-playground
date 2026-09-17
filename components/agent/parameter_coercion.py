"""Coerce user-provided procedure parameter values using article hints."""

from __future__ import annotations

import re
from typing import Any

_INTEGER_HINTS = (
    "integer",
    "number of",
    "amount of memory",
    "in gib",
    "gib",
    "cpus",
    "cpu",
    "memory",
)

_INTEGER_PARAM_NAMES = frozenset(
    {
        "cpus",
        "cpu",
        "mem",
        "memory",
        "vcpu",
        "vcpus",
        "ram",
    }
)

_FIRST_INTEGER_RE = re.compile(r"\d+")
_LAUNCH_FIELD_RE = re.compile(
    r"(?m)^[ \t]*([a-z][a-z0-9_]*)\s*:[ \t]*([^\n,;]+)\s*$",
    re.IGNORECASE,
)

_SKIP_LAUNCH_ALIAS_KEYS = frozenset({"extra_vars", "http", "https"})

def extract_launch_aliases_from_procedure(
    procedure: list[dict[str, Any]] | None,
) -> dict[str, str]:
    """Map human labels from article launch steps to canonical extra_var keys."""
    aliases: dict[str, str] = {}
    for step in procedure or []:
        if not isinstance(step, dict):
            continue
        detail = str(step.get("detail") or "")
        for match in _LAUNCH_FIELD_RE.finditer(detail):
            canonical = str(match.group(1) or "").strip()
            label = str(match.group(2) or "").strip().casefold()
            if not canonical or not label:
                continue
            if canonical in {"http", "https"}:
                continue
            if canonical in _SKIP_LAUNCH_ALIAS_KEYS:
                continue
            aliases[label] = canonical
    return aliases


def build_launch_field_aliases(
    parameter_specs: list[dict[str, str]] | None = None,
    procedure: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Build label→key aliases from the current article only."""
    aliases = extract_launch_aliases_from_procedure(procedure)
    for spec in parameter_specs or []:
        if not isinstance(spec, dict):
            continue
        name = str(spec.get("name") or "").strip()
        detail = str(spec.get("detail") or "").strip()
        if not name or not detail:
            continue
        if _looks_like_canonical_key(name) and not _looks_like_canonical_key(detail):
            aliases[detail.casefold()] = name
            continue
        if not _looks_like_canonical_key(name) and _looks_like_canonical_key(detail):
            aliases[name.casefold()] = detail
    return aliases


def _looks_like_canonical_key(name: str) -> bool:
    return bool(re.fullmatch(r"[a-z][a-z0-9_]*", name.strip()))


def canonicalize_launch_parameters(
    known_parameters: dict[str, str],
    *,
    parameter_specs: list[dict[str, str]] | None = None,
    procedure: list[dict[str, Any]] | None = None,
    polluted_extra_vars: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Normalize parameter names to launch extra_var keys using article metadata."""
    aliases = build_launch_field_aliases(parameter_specs, procedure)
    canonical: dict[str, str] = {}

    def add(name: str, value: Any, *, from_polluted: bool = False) -> None:
        text_name = str(name or "").strip()
        text_value = str(value or "").strip()
        if not text_name or not text_value:
            return
        if _looks_like_canonical_key(text_name):
            canonical[text_name] = text_value
            return
        label = text_name.casefold()
        mapped = aliases.get(label)
        if mapped:
            canonical[mapped] = text_value
            return
        for alias_label, alias_key in aliases.items():
            if label == alias_label or label in alias_label or alias_label in label:
                canonical[alias_key] = text_value
                return
        if not from_polluted:
            canonical[text_name] = text_value

    for name, value in known_parameters.items():
        add(name, value)

    if isinstance(polluted_extra_vars, dict):
        for name, value in polluted_extra_vars.items():
            if name == "thread_id":
                continue
            add(name, value, from_polluted=True)

    return canonical


def build_parameter_specs(
    missing: list[dict[str, str]],
    known: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Build name/detail specs for coercion from missing list and known names."""
    specs: dict[str, dict[str, str]] = {}
    for item in missing:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        key = name.casefold()
        specs[key] = {
            "name": name,
            "detail": str(item.get("detail") or "").strip(),
        }
    for item in known:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        key = name.casefold()
        specs.setdefault(key, {"name": name, "detail": ""})
    return list(specs.values())


def _should_coerce_to_integer(name: str, detail: str) -> bool:
    lowered_name = name.casefold()
    if lowered_name in _INTEGER_PARAM_NAMES:
        return True
    hint = f"{name} {detail}".casefold()
    return any(token in hint for token in _INTEGER_HINTS)


def coerce_parameter_value(name: str, value: str, *, detail: str = "") -> str:
    """Normalize a single parameter value using article/name hints."""
    text = str(value or "").strip()
    if not text:
        return text
    if not _should_coerce_to_integer(name, detail):
        return text
    match = _FIRST_INTEGER_RE.search(text)
    if match:
        return match.group(0)
    return text


def coerce_known_parameters(
    known: list[dict[str, str]],
    parameter_specs: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Return known parameters with coerced values where applicable."""
    detail_by_name = {
        str(item.get("name") or "").casefold(): str(item.get("detail") or "")
        for item in (parameter_specs or [])
        if isinstance(item, dict) and item.get("name")
    }
    coerced: list[dict[str, str]] = []
    for item in known:
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        if not name:
            continue
        detail = detail_by_name.get(name.casefold(), "")
        new_value = coerce_parameter_value(name, value, detail=detail)
        coerced.append({"name": name, "value": new_value})
    return coerced


def known_parameters_as_map(known: list[dict[str, str]]) -> dict[str, str]:
    return {
        str(item.get("name") or "").strip(): str(item.get("value") or "").strip()
        for item in known
        if item.get("name") and item.get("value") is not None
    }
