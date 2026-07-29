"""Process RAG requests classified as ACTION (create / execute).

Flow:
1. Fetch the KB article and extract parameters, procedure, and follow-up.
2. If required parameters are missing, ask only for those and keep state in pending.
3. When the user provides them, merge into known parameters.
4. Once complete, present information, procedure steps, and follow-up.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from config import RAG_MCP_TOOLS
from itsm_mcp import ItsmMcpClient
from llm import LLMClient
from prompts import (
    build_rag_action_ask_prompt,
    build_rag_action_extract_prompt,
    build_rag_action_fill_prompt,
    build_rag_not_found_prompt,
)

log = logging.getLogger("agent.rag_action")

_MAX_ARTICLE_CHARS = 12_000


@dataclass(frozen=True)
class RagActionResult:
    response: str
    pending: dict[str, Any] | None = None
    action: str = "reply"


def run_rag_action(
    user_message: str,
    *,
    llm: LLMClient,
    itsm_mcp: ItsmMcpClient,
    dialogue: list[dict[str, Any]] | None = None,
    pending: dict[str, Any] | None = None,
) -> RagActionResult:
    """Collect missing params first; then present procedure details."""
    log.info(
        "RAG action started message_chars=%s dialogue_turns=%s continuing=%s",
        len(user_message or ""),
        len(dialogue or []),
        _is_rag_action_pending(pending),
    )
    if _is_rag_action_pending(pending):
        return _continue_collection(
            user_message,
            llm=llm,
            dialogue=dialogue,
            pending=pending or {},
        )
    return _start_action(
        user_message,
        llm=llm,
        itsm_mcp=itsm_mcp,
        dialogue=dialogue,
    )


def _start_action(
    user_message: str,
    *,
    llm: LLMClient,
    itsm_mcp: ItsmMcpClient,
    dialogue: list[dict[str, Any]] | None,
) -> RagActionResult:
    article = _fetch_article(user_message, itsm_mcp=itsm_mcp)
    if article is None:
        return RagActionResult(
            response=_not_found(user_message, llm=llm, dialogue=dialogue),
        )

    article_text = _article_text(article)
    if not article_text.strip():
        log.info("RAG action article empty after formatting")
        return RagActionResult(
            response=_not_found(user_message, llm=llm, dialogue=dialogue),
        )

    if len(article_text) > _MAX_ARTICLE_CHARS:
        article_text = (
            article_text[:_MAX_ARTICLE_CHARS]
            + "\n[Article truncated to fit model context]"
        )

    extracted = _extract_from_article(
        user_message,
        article_text=article_text,
        llm=llm,
        dialogue=dialogue,
    )
    known = _normalize_known(extracted.get("known_parameters"))
    missing = _normalize_missing(extracted.get("missing_parameters"), known)
    procedure = _normalize_procedure(extracted.get("procedure"))
    follow_up = _normalize_follow_up(extracted.get("follow_up"))

    return _next_result(
        user_message,
        llm=llm,
        dialogue=dialogue,
        known=known,
        missing=missing,
        procedure=procedure,
        follow_up=follow_up,
    )


def _continue_collection(
    user_message: str,
    *,
    llm: LLMClient,
    dialogue: list[dict[str, Any]] | None,
    pending: dict[str, Any],
) -> RagActionResult:
    known = _normalize_known(pending.get("known_parameters"))
    missing = _normalize_missing(pending.get("missing_parameters"), known)
    procedure = _normalize_procedure(pending.get("procedure"))
    follow_up = _normalize_follow_up(pending.get("follow_up"))

    if missing:
        provided = _fill_missing_from_reply(
            user_message,
            missing=missing,
            llm=llm,
            dialogue=dialogue,
        )
        known = _merge_known(known, provided)
        missing = _normalize_missing(missing, known)

    return _next_result(
        user_message,
        llm=llm,
        dialogue=dialogue,
        known=known,
        missing=missing,
        procedure=procedure,
        follow_up=follow_up,
    )


def _next_result(
    user_message: str,
    *,
    llm: LLMClient,
    dialogue: list[dict[str, Any]] | None,
    known: list[dict[str, str]],
    missing: list[dict[str, str]],
    procedure: list[dict[str, Any]],
    follow_up: list[str],
) -> RagActionResult:
    state = {
        "category": "RAG",
        "kind": "rag_action",
        "known_parameters": known,
        "missing_parameters": missing,
        "procedure": procedure,
        "follow_up": follow_up,
    }
    if missing:
        log.info(
            "RAG action asking for missing=%s known=%s",
            len(missing),
            len(known),
        )
        return RagActionResult(
            response=_ask_for_missing(
                user_message,
                missing=missing,
                llm=llm,
                dialogue=dialogue,
            ),
            pending=state,
            action="request_information",
        )

    log.info("RAG action presenting procedure known=%s", len(known))
    return RagActionResult(
        response=_format_present(
            known_parameters=known,
            procedure=procedure,
            follow_up=follow_up,
        ),
        pending=None,
        action="reply",
    )


def _extract_from_article(
    user_message: str,
    *,
    article_text: str,
    llm: LLMClient,
    dialogue: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": build_rag_action_extract_prompt()},
    ]
    messages.extend(_dialogue_as_str(dialogue or []))
    messages.append(
        {
            "role": "user",
            "content": (
                f"User request:\n{user_message}\n\n"
                f"Knowledge-base article:\n{article_text}"
            ),
        }
    )
    raw = llm.chat(messages).strip()
    return _parse_json_object(raw)


def _ask_for_missing(
    user_message: str,
    *,
    missing: list[dict[str, str]],
    llm: LLMClient,
    dialogue: list[dict[str, Any]] | None,
) -> str:
    missing_json = json.dumps(missing, ensure_ascii=False, indent=2)
    messages: list[dict[str, str]] = [
        {"role": "system", "content": build_rag_action_ask_prompt()},
    ]
    messages.extend(_dialogue_as_str(dialogue or []))
    messages.append(
        {
            "role": "user",
            "content": (
                f"User request:\n{user_message}\n\n"
                f"Missing parameters:\n{missing_json}"
            ),
        }
    )
    return llm.chat(messages).strip()


def _fill_missing_from_reply(
    user_message: str,
    *,
    missing: list[dict[str, str]],
    llm: LLMClient,
    dialogue: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    missing_json = json.dumps(missing, ensure_ascii=False, indent=2)
    messages: list[dict[str, str]] = [
        {"role": "system", "content": build_rag_action_fill_prompt()},
    ]
    messages.extend(_dialogue_as_str(dialogue or []))
    messages.append(
        {
            "role": "user",
            "content": (
                f"Missing parameters:\n{missing_json}\n\n"
                f"User reply:\n{user_message}"
            ),
        }
    )
    raw = llm.chat(messages).strip()
    data = _parse_json_object(raw)
    allowed = {item["name"].casefold(): item["name"] for item in missing}
    provided: list[dict[str, str]] = []
    for item in _normalize_known(data.get("provided")):
        key = item["name"].casefold()
        if key not in allowed:
            continue
        provided.append({"name": allowed[key], "value": item["value"]})
    return provided


def _format_present(
    *,
    known_parameters: list[dict[str, str]],
    procedure: list[dict[str, Any]],
    follow_up: list[str],
) -> str:
    sections: list[str] = []

    lines = ["## Information"]
    if known_parameters:
        for item in known_parameters:
            lines.append(f"- {item['name']}: {item['value']}")
    else:
        lines.append("- No parameters were required for this procedure.")
    sections.append("\n".join(lines))

    lines = ["## Procedure"]
    if procedure:
        for item in procedure:
            lines.append(f"{item['step']}. {item['detail']}")
    else:
        lines.append("- No procedure steps found in the article.")
    sections.append("\n".join(lines))

    lines = ["## Follow up"]
    if follow_up:
        for item in follow_up:
            lines.append(f"- {item}")
    else:
        lines.append("- No follow-up details found in the article.")
    sections.append("\n".join(lines))

    return "\n\n".join(sections)


def _is_rag_action_pending(pending: dict[str, Any] | None) -> bool:
    return isinstance(pending, dict) and pending.get("kind") == "rag_action"


def _merge_known(
    prior: list[dict[str, str]],
    fresh: Any,
) -> list[dict[str, str]]:
    merged: dict[str, str] = {}
    names: dict[str, str] = {}
    for item in prior:
        key = item["name"].casefold()
        merged[key] = item["value"]
        names[key] = item["name"]
    for item in _normalize_known(fresh):
        key = item["name"].casefold()
        merged[key] = item["value"]
        names.setdefault(key, item["name"])
    return [{"name": names[key], "value": value} for key, value in merged.items()]


def _normalize_known(value: Any) -> list[dict[str, str]]:
    if isinstance(value, dict):
        out: list[dict[str, str]] = []
        for name, raw_value in value.items():
            text_name = str(name).strip()
            text_value = str(raw_value).strip() if raw_value is not None else ""
            if text_name and text_value:
                out.append({"name": text_name, "value": text_value})
        return out
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        raw_value = item.get("value")
        text_value = str(raw_value).strip() if raw_value is not None else ""
        if name and text_value:
            out.append({"name": name, "value": text_value})
    return out


def _normalize_missing(
    value: Any,
    known: list[dict[str, str]],
) -> list[dict[str, str]]:
    known_keys = {item["name"].casefold() for item in known}
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        key = name.casefold()
        if key in known_keys or key in seen:
            continue
        detail = str(item.get("detail") or "").strip()
        entry: dict[str, str] = {"name": name}
        if detail:
            entry["detail"] = detail
        out.append(entry)
        seen.add(key)
    return out


def _normalize_procedure(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if isinstance(item, str) and item.strip():
            out.append({"step": index, "detail": item.strip()})
            continue
        if not isinstance(item, dict):
            continue
        detail = str(item.get("detail") or item.get("text") or "").strip()
        if not detail:
            continue
        step = item.get("step", index)
        try:
            step_num = int(step)
        except (TypeError, ValueError):
            step_num = index
        out.append({"step": step_num, "detail": detail})
    return out


def _normalize_follow_up(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, dict):
            text = str(item.get("detail") or item.get("text") or "").strip()
            if text:
                out.append(text)
    return out


def _parse_json_object(raw: str) -> dict[str, Any]:
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.IGNORECASE)
    cleaned = re.sub(r"```(?:json)?\s*", "", cleaned)
    cleaned = cleaned.replace("```", "").strip()
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        log.warning("RAG action: no JSON in LLM reply")
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        log.warning("RAG action: invalid JSON in LLM reply")
        return {}
    return data if isinstance(data, dict) else {}


def _fetch_article(
    user_message: str,
    *,
    itsm_mcp: ItsmMcpClient,
) -> Any | None:
    tools = [
        t
        for t in itsm_mcp.list_tools()
        if isinstance(t, dict) and t.get("name") in RAG_MCP_TOOLS
    ]
    by_name = {
        t["name"]: t
        for t in tools
        if isinstance(t.get("name"), str)
    }
    if "rag_search_kb" not in by_name or "get_kb_article" not in by_name:
        log.warning("RAG action tools missing names=%s", sorted(by_name))
        return None

    search_args = _query_arguments(by_name["rag_search_kb"], user_message)
    log.info("RAG action search arguments=%s", search_args)
    try:
        search_result = itsm_mcp.call_tool("rag_search_kb", search_args)
    except Exception:
        log.exception("RAG action search failed")
        return None

    article_id = _extract_article_id(search_result)
    if not article_id:
        log.info("RAG action search returned no article id")
        return None

    detail_args = _article_id_arguments(by_name["get_kb_article"], article_id)
    log.info("RAG action get_kb_article arguments=%s", detail_args)
    try:
        return itsm_mcp.call_tool("get_kb_article", detail_args)
    except Exception:
        log.exception("RAG action get_kb_article failed id=%s", article_id)
        return None


def _not_found(
    user_message: str,
    *,
    llm: LLMClient,
    dialogue: list[dict[str, Any]] | None,
) -> str:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": build_rag_not_found_prompt()},
    ]
    messages.extend(_dialogue_as_str(dialogue or []))
    messages.append({"role": "user", "content": user_message})
    return llm.chat(messages).strip()


def _dialogue_as_str(dialogue: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for turn in dialogue:
        role = turn.get("role")
        content = turn.get("content")
        if role in ("user", "assistant", "system") and isinstance(content, str):
            out.append({"role": role, "content": content})
    return out


def _article_text(detail: Any) -> str:
    if isinstance(detail, str):
        return detail
    if not isinstance(detail, dict):
        return json.dumps(detail, ensure_ascii=False, default=str)

    for key in ("body", "content", "text", "markdown", "article"):
        value = detail.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, dict):
            nested = _article_text(value)
            if nested.strip():
                return nested

    return json.dumps(detail, ensure_ascii=False, indent=2, default=str)


def _unwrap_payload(result: Any) -> Any:
    if isinstance(result, str):
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return result
    if not isinstance(result, dict):
        return result
    if "text" in result and isinstance(result["text"], str):
        try:
            return json.loads(result["text"])
        except json.JSONDecodeError:
            pass
    content = result.get("content")
    if isinstance(content, list):
        texts = [
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        if texts:
            merged = "\n".join(texts)
            try:
                return json.loads(merged)
            except json.JSONDecodeError:
                return {"text": merged}
    return result


def _search_hits(result: Any) -> list[dict[str, Any]]:
    payload = _unwrap_payload(result)
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("results", "hits", "articles", "items", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    if any(key in payload for key in ("id", "article_id", "kb_id", "uuid")):
        return [payload]
    return []


def _extract_article_id(result: Any) -> str | None:
    for hit in _search_hits(result):
        for key in ("id", "article_id", "kb_id", "uuid"):
            value = hit.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        article = hit.get("article")
        if isinstance(article, dict):
            for key in ("id", "article_id", "kb_id", "uuid"):
                value = article.get(key)
                if value is not None and str(value).strip():
                    return str(value).strip()
    return None


def _tool_properties(tool: dict[str, Any]) -> dict[str, Any]:
    schema = tool.get("inputSchema") or tool.get("parameters") or {}
    if not isinstance(schema, dict):
        return {}
    props = schema.get("properties")
    return props if isinstance(props, dict) else {}


def _query_arguments(tool: dict[str, Any], user_message: str) -> dict[str, Any]:
    props = _tool_properties(tool)
    for key in ("query", "question", "q", "text", "search", "prompt"):
        if key in props:
            return {key: user_message}
    return {"query": user_message}


def _article_id_arguments(tool: dict[str, Any], article_id: str) -> dict[str, Any]:
    props = _tool_properties(tool)
    for key in ("article_id", "id", "kb_id", "uuid"):
        if key in props:
            return {key: article_id}
    return {"id": article_id}
