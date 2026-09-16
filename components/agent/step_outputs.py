"""Deterministic extraction of procedure state from MCP tool results."""

from __future__ import annotations

import re
from typing import Any

from parameter_coercion import canonicalize_launch_parameters

_REQ_REF_RE = re.compile(r"^REQ[-_]\d{4}-\d+$", re.IGNORECASE)
_RITM_REF_RE = re.compile(r"^RITM[-_]\d+$", re.IGNORECASE)
_CHG_REF_RE = re.compile(r"^CHG[-_]\d+$", re.IGNORECASE)

_CREATE_REQUEST_KEYS = {
    "itsm_service_request_ref": (
        "number",
        "request_ref",
        "request_number",
        "service_request_ref",
        "request_id",
    ),
    "itsm_change_ref": (
        "change_ref",
        "change_id",
        "change_number",
        "change",
    ),
    "itsm_ritm_ref": (
        "ritm_ref",
        "ritm_id",
        "ritm",
    ),
}

_LAUNCH_KEYS = {
    "workflow_job_templates_launch_create": {
        "workflow_job_id": ("workflow_job", "id", "job_id"),
    },
    "job_templates_launch_create": {
        "job_id": ("job", "id", "job_id"),
    },
}

_LIST_TEMPLATE_KEYS = {
    "workflow_job_templates_list": {
        "workflow_template_id": ("id",),
    },
    "job_templates_list": {
        "job_template_id": ("id",),
    },
}


def merge_derived(existing: dict[str, Any], fresh: dict[str, str]) -> dict[str, Any]:
    merged = dict(existing) if isinstance(existing, dict) else {}
    for key, value in fresh.items():
        text_key = str(key).strip()
        text_value = str(value).strip()
        if text_key and text_value:
            merged[text_key] = text_value
    return merged


def extract_derived_from_tool_result(tool_name: str, result: Any) -> dict[str, str]:
    """Extract cross-step identifiers from a tool result without using an LLM."""
    extracted: dict[str, str] = {}
    payload = _unwrap_payload(result)
    if not payload:
        return extracted

    if tool_name == "create_request":
        extracted.update(_extract_mapped_fields(payload, _CREATE_REQUEST_KEYS))
        extracted.update(_extract_ticket_patterns(payload))
        return extracted

    if tool_name in _LAUNCH_KEYS:
        extracted.update(_extract_mapped_fields(payload, _LAUNCH_KEYS[tool_name]))
        return extracted

    if tool_name in _LIST_TEMPLATE_KEYS:
        extracted.update(_extract_first_list_item_id(payload, tool_name))
        return extracted

    return extracted


def _launch_template_ref(arguments: dict[str, Any] | None) -> str:
    if not isinstance(arguments, dict):
        return ""
    for key in ("id", "name", "job_template_id", "workflow_job_template_id"):
        value = arguments.get(key)
        if value is not None and str(value).strip():
            return str(value).strip().casefold()
    return ""


def launch_result_is_failure(tool_name: str, result: Any) -> bool:
    """Return True when an AAP launch response did not create a job."""
    if tool_name not in _LAUNCH_KEYS:
        return False

    payload = _unwrap_payload(result)
    if not isinstance(payload, dict):
        return True

    variables_needed = payload.get("variables_needed_to_start")
    if isinstance(variables_needed, list) and variables_needed:
        return True

    detail = str(payload.get("detail") or "").strip()
    if detail:
        return True

    if _extract_mapped_fields(payload, _LAUNCH_KEYS[tool_name]):
        return False

    return True


def format_launch_failure(result: Any) -> str:
    """Build a concise launch failure message from an AAP response."""
    payload = _unwrap_payload(result)
    if not isinstance(payload, dict):
        return str(result)

    variables_needed = payload.get("variables_needed_to_start")
    if isinstance(variables_needed, list) and variables_needed:
        missing = "; ".join(str(item).strip() for item in variables_needed if str(item).strip())
        if missing:
            return f"AAP launch did not start because required variables are missing: {missing}"

    detail = str(payload.get("detail") or "").strip()
    if detail:
        return detail

    return "AAP launch did not create a job."


def should_skip_duplicate_launch(
    action: str,
    accumulated: dict[str, Any],
    *,
    arguments: dict[str, Any] | None = None,
) -> bool:
    """Return True when a launch of this type already succeeded in this run."""
    del arguments
    if action not in _LAUNCH_KEYS:
        return False

    derived = accumulated.get("derived")
    if isinstance(derived, dict):
        job_field = next(iter(_LAUNCH_KEYS[action]))
        if str(derived.get(job_field) or "").strip():
            return True

    steps_log = accumulated.get("steps_log")
    if not isinstance(steps_log, list):
        return False

    for item in steps_log:
        if not isinstance(item, dict):
            continue
        if item.get("skipped"):
            continue
        if item.get("tool") != action or not item.get("ok"):
            continue
        stored_result = item.get("result")
        if stored_result is not None and not launch_result_is_failure(action, stored_result):
            return True
    return False


def inject_trusted_derived_into_launch_args(
    arguments: dict[str, Any],
    derived: dict[str, Any],
    *,
    known_parameters: dict[str, str] | None = None,
    parameter_specs: list[dict[str, str]] | None = None,
    procedure: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Fill launch extra_vars from derived state and known parameters."""
    args = _deep_copy_dict(arguments)
    located = _find_request_body(args)
    if located is None:
        body_key = "requestBody"
        body: dict[str, Any] = {}
        args[body_key] = body
    else:
        body_key, body = located

    prior_extra_vars = body.get("extra_vars")
    thread_id = None
    polluted: dict[str, Any] = {}
    if isinstance(prior_extra_vars, dict):
        polluted = prior_extra_vars
        thread_value = prior_extra_vars.get("thread_id")
        if thread_value is not None and str(thread_value).strip():
            thread_id = str(thread_value).strip()

    canonical_known = canonicalize_launch_parameters(
        known_parameters or {},
        parameter_specs=parameter_specs,
        procedure=procedure,
        polluted_extra_vars=polluted,
    )

    body["extra_vars"] = sanitize_launch_extra_vars(
        derived,
        canonical_known,
        thread_id=thread_id,
    )
    args[body_key] = body
    from aap_mcp import canonicalize_request_body_key

    return canonicalize_request_body_key(args)


def sanitize_launch_extra_vars(
    derived: dict[str, Any],
    known_parameters: dict[str, str],
    *,
    thread_id: str | None = None,
) -> dict[str, str]:
    """Rebuild workflow extra_vars using only canonical parameter names and trusted refs."""
    cleaned: dict[str, str] = {}

    for name, value in known_parameters.items():
        text_name = str(name or "").strip()
        text_value = str(value or "").strip()
        if text_name and text_value:
            cleaned[text_name] = text_value

    for key, pick_keys in (
        ("itsm_service_request_ref", ("itsm_service_request_ref", "request_ref", "service_request_ref")),
        ("itsm_change_ref", ("itsm_change_ref", "change_ref", "change_id")),
        ("itsm_ritm_ref", ("itsm_ritm_ref", "ritm_ref", "ritm_id")),
    ):
        value = _pick_derived(derived, *pick_keys)
        if value:
            cleaned[key] = value

    if thread_id:
        cleaned["thread_id"] = thread_id

    return cleaned


def validate_launch_references(
    arguments: dict[str, Any],
    derived: dict[str, Any],
) -> str | None:
    """Return an error message when launch args contain untrusted ITSM references."""
    body = _find_request_body(arguments)
    if body is None:
        return None
    _, payload = body
    extra_vars = payload.get("extra_vars")
    if not isinstance(extra_vars, dict):
        return None

    trusted = {
        str(value).strip()
        for value in derived.values()
        if value is not None and str(value).strip()
    }
    for key in ("itsm_service_request_ref", "itsm_change_ref", "itsm_ritm_ref"):
        value = extra_vars.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        if text in trusted:
            continue
        return (
            f"Refusing to launch with unverified {key}={text!r}. "
            "It was not produced by a previous procedure step."
        )
    return None


def extract_template_name_from_step(detail: str) -> str | None:
    """Extract a quoted workflow/job template name from a human procedure step."""
    text = detail.strip()
    if not text:
        return None
    quoted = re.search(r'"([^"]+)"', text)
    if quoted:
        return quoted.group(1).strip()
    quoted = re.search(r"'([^']+)'", text)
    if quoted:
        return quoted.group(1).strip()
    match = re.search(
        r"(?:workflow job template|job template|workflow template)"
        r"(?:\s+called|\s+named)?\s+([A-Za-z0-9][A-Za-z0-9 _-]{2,})",
        text,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()
    return None


def recover_aap_launch_decision(
    step: dict[str, Any],
    *,
    accumulated: dict[str, Any],
    known_parameters: dict[str, str],
    parameter_specs: list[dict[str, str]] | None = None,
    procedure: list[dict[str, Any]] | None = None,
    prefer_workflow: bool | None = None,
) -> dict[str, Any] | None:
    """Build a launch decision from step text and accumulated state."""
    detail = str(step.get("detail") or "").strip()
    template_name = extract_template_name_from_step(detail)
    if not template_name:
        return None

    if prefer_workflow is None:
        prefer_workflow = "workflow" in detail.casefold()

    tool_name = (
        "workflow_job_templates_launch_create"
        if prefer_workflow
        else "job_templates_launch_create"
    )
    derived = accumulated.get("derived") if isinstance(accumulated.get("derived"), dict) else {}
    template_id = _pick_derived(derived, "workflow_template_id", "job_template_id")
    arguments: dict[str, Any] = {}
    if template_id and str(template_id).isdigit():
        arguments["id"] = str(template_id)
    else:
        arguments["id"] = template_name

    arguments = inject_trusted_derived_into_launch_args(
        arguments,
        derived,
        known_parameters=known_parameters,
        parameter_specs=parameter_specs,
        procedure=procedure,
    )
    return {
        "action": tool_name,
        "arguments": arguments,
        "thought": f"Launch {template_name} using accumulated procedure state.",
        "domain": "AAP",
    }


def _extract_mapped_fields(
    payload: Any,
    mapping: dict[str, tuple[str, ...]],
) -> dict[str, str]:
    found: dict[str, str] = {}
    for target, candidates in mapping.items():
        value = _find_first(payload, candidates)
        if value is not None:
            text = str(value).strip()
            if text:
                found[target] = text
    return found


def _extract_ticket_patterns(payload: Any) -> dict[str, str]:
    found: dict[str, str] = {}
    for value in _iter_string_values(payload):
        if _REQ_REF_RE.match(value) and "itsm_service_request_ref" not in found:
            found["itsm_service_request_ref"] = value
        elif _RITM_REF_RE.match(value) and "itsm_ritm_ref" not in found:
            found["itsm_ritm_ref"] = value
        elif _CHG_REF_RE.match(value) and "itsm_change_ref" not in found:
            found["itsm_change_ref"] = value
    return found


def _extract_first_list_item_id(payload: Any, tool_name: str) -> dict[str, str]:
    items = _extract_list(payload)
    if not items:
        return {}
    first = items[0]
    if not isinstance(first, dict):
        return {}
    template_id = first.get("id")
    if template_id is None:
        return {}
    if tool_name == "workflow_job_templates_list":
        return {"workflow_template_id": str(template_id)}
    return {"job_template_id": str(template_id)}


def _unwrap_payload(result: Any) -> Any:
    if isinstance(result, dict):
        for key in ("text", "data", "result", "payload"):
            nested = result.get(key)
            if isinstance(nested, (dict, list)):
                return nested
        return result
    return result


def _extract_list(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("results", "items", "data"):
            items = payload.get(key)
            if isinstance(items, list):
                return items
    return []


def _find_first(payload: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(payload, dict):
        for key in keys:
            if key in payload and payload[key] not in (None, ""):
                return payload[key]
        for value in payload.values():
            found = _find_first(value, keys)
            if found not in (None, ""):
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_first(item, keys)
            if found not in (None, ""):
                return found
    return None


def _iter_string_values(payload: Any):
    if isinstance(payload, dict):
        for value in payload.values():
            yield from _iter_string_values(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _iter_string_values(item)
    elif isinstance(payload, str):
        yield payload.strip()


def render_procedure_summary(
    accumulated: dict[str, Any],
    follow_up: list[str],
) -> str:
    """Build a user-facing summary using only verified execution state."""
    parameters = accumulated.get("parameters") or []
    derived = accumulated.get("derived") or {}
    steps_log = accumulated.get("steps_log") or []

    lines = ["### What was done", ""]
    if not steps_log:
        lines.append("- No executable steps were recorded.")
    for item in steps_log:
        step_num = item.get("step")
        tool = item.get("tool")
        ok = item.get("ok")
        skipped = item.get("skipped")
        summary = str(item.get("result_summary") or "").strip()
        if skipped:
            lines.append(f"- Step {step_num}: skipped.")
        elif ok:
            label = f"Step {step_num}"
            if tool:
                label += f" ({tool})"
            lines.append(f"- {label}: completed.")
        else:
            label = f"Step {step_num}"
            if tool:
                label += f" ({tool})"
            lines.append(f"- {label}: failed.")
            if summary:
                lines.append(f"  - {summary}")

    if parameters or derived:
        lines.extend(["", "### Key values", ""])
    for item in parameters:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        if name and value:
            lines.append(f"- {name}: `{value}`")
    for key, value in derived.items():
        text = str(value).strip()
        if text:
            lines.append(f"- {key}: `{text}`")

    if follow_up:
        lines.extend(["", "### Follow-up", ""])
        for item in follow_up:
            text = str(item).strip()
            if text:
                lines.append(f"- {text}")

    return "\n".join(lines).strip()


def render_procedure_error(
    *,
    step_num: int,
    detail: str,
    failure: str,
    accumulated: dict[str, Any],
) -> str:
    """Build an abort message using verified state only."""
    derived = accumulated.get("derived") or {}
    lines = [
        "The procedure was stopped because a step failed.",
        "",
        f"- Failed step: {step_num}",
        f"- Detail: {detail}",
        f"- Reason: {failure.strip()}",
    ]
    if derived:
        lines.extend(["", "Verified values collected before the failure:", ""])
        for key, value in derived.items():
            text = str(value).strip()
            if text:
                lines.append(f"- {key}: `{text}`")
    return "\n".join(lines).strip()


def _looks_like_ticket_ref(value: str) -> bool:
    return bool(
        _REQ_REF_RE.match(value)
        or _RITM_REF_RE.match(value)
        or _CHG_REF_RE.match(value)
        or value.isdigit()
    )


def _pick_derived(derived: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = derived.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _find_request_body(args: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    for key in ("request_body", "requestBody"):
        body = args.get(key)
        if isinstance(body, dict):
            return key, body
    return None


def _deep_copy_dict(value: dict[str, Any]) -> dict[str, Any]:
    return json_copy(value)


def json_copy(value: Any) -> Any:
    import json

    return json.loads(json.dumps(value, default=str))
