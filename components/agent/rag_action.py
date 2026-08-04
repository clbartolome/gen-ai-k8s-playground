"""Process RAG requests classified as ACTION (create / execute).

Flow:
1. Fetch the KB article and extract parameters, procedure, and follow-up.
2. If required parameters are missing, ask only for those and keep state in pending.
3. When the user provides them, merge into known parameters.
4. Once complete, execute each procedure step via domain specialist prompts + MCP tools.
5. On tool error, stop and explain politely; on success, merge results into state.
6. After all steps, compose a summary using accumulated state and follow-up.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable

from aap_mcp import AapMcpClient
from config import RAG_MCP_TOOLS
from itsm_mcp import ItsmMcpClient
from llm import LLMClient
from openshift_mcp import OpenShiftMcpClient
from prompts import (
    build_aap_prompt,
    build_itsm_prompt,
    build_openshift_prompt,
    build_rag_action_ask_prompt,
    build_rag_action_error_prompt,
    build_rag_action_extract_prompt,
    build_rag_action_fill_prompt,
    build_rag_action_merge_prompt,
    build_rag_action_step_domain_prompt,
    build_rag_action_summary_prompt,
    build_rag_not_found_prompt,
)
from trace import TraceBuilder

log = logging.getLogger("agent.rag_action")

_MAX_ARTICLE_CHARS = 12_000
_MAX_RESULT_CHARS = 8_000

InvokeFn = Callable[[str, dict[str, Any]], Any]
ThoughtCallback = Callable[[str], None]
PromptBuilder = Callable[[list[dict[str, Any]]], str]

_DOMAIN_RE = re.compile(
    r"Domain:\s*(OPENSHIFT|AAP|ITSM|NONE)",
    re.IGNORECASE,
)
_VALID_DOMAINS = frozenset({"OPENSHIFT", "AAP", "ITSM", "NONE"})


@dataclass(frozen=True)
class RagActionResult:
    response: str
    pending: dict[str, Any] | None = None
    action: str = "reply"


@dataclass(frozen=True)
class _DomainBundle:
    tools: list[dict[str, Any]]
    invoke: InvokeFn
    build_prompt: PromptBuilder


@dataclass(frozen=True)
class _ToolRegistry:
    domains: dict[str, _DomainBundle]
    invokers: dict[str, InvokeFn]


def run_rag_action(
    user_message: str,
    *,
    llm: LLMClient,
    itsm_mcp: ItsmMcpClient,
    openshift_mcp: OpenShiftMcpClient,
    aap_mcp: AapMcpClient,
    dialogue: list[dict[str, Any]] | None = None,
    pending: dict[str, Any] | None = None,
    on_thought: ThoughtCallback | None = None,
    trace: TraceBuilder | None = None,
) -> RagActionResult:
    """Collect missing params, then execute the procedure with MCP tools."""
    log.info(
        "RAG action started message_chars=%s dialogue_turns=%s continuing=%s",
        len(user_message or ""),
        len(dialogue or []),
        _is_rag_action_pending(pending),
    )
    registry = _build_tool_registry(
        openshift_mcp=openshift_mcp,
        aap_mcp=aap_mcp,
        itsm_mcp=itsm_mcp,
    )
    if _is_rag_action_pending(pending):
        return _continue_collection(
            user_message,
            llm=llm,
            dialogue=dialogue,
            pending=pending or {},
            registry=registry,
            on_thought=on_thought,
            trace=trace,
        )
    return _start_action(
        user_message,
        llm=llm,
        itsm_mcp=itsm_mcp,
        dialogue=dialogue,
        registry=registry,
        on_thought=on_thought,
        trace=trace,
    )


def _start_action(
    user_message: str,
    *,
    llm: LLMClient,
    itsm_mcp: ItsmMcpClient,
    dialogue: list[dict[str, Any]] | None,
    registry: _ToolRegistry,
    on_thought: ThoughtCallback | None,
    trace: TraceBuilder | None,
) -> RagActionResult:
    if on_thought:
        on_thought("Searching the knowledge base for the procedure…")
    article = _fetch_article(user_message, itsm_mcp=itsm_mcp)
    if article is None:
        if trace:
            trace.add(
                "article",
                "No procedure article found",
                status="error",
            )
        return RagActionResult(
            response=_not_found(user_message, llm=llm, dialogue=dialogue),
        )

    article_text = _article_text(article)
    if not article_text.strip():
        log.info("RAG action article empty after formatting")
        if trace:
            trace.add(
                "article",
                "No procedure article found",
                status="error",
            )
        return RagActionResult(
            response=_not_found(user_message, llm=llm, dialogue=dialogue),
        )

    article_id = _article_id_from_payload(article)
    if trace:
        label = (
            f"Article found · {article_id}" if article_id else "Article found"
        )
        trace.add(
            "article",
            label,
            detail={"article_id": article_id} if article_id else {},
        )

    if len(article_text) > _MAX_ARTICLE_CHARS:
        article_text = (
            article_text[:_MAX_ARTICLE_CHARS]
            + "\n[Article truncated to fit model context]"
        )

    if on_thought:
        on_thought("Extracting procedure details…")
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

    if trace:
        trace.add(
            "procedure",
            f"Procedure analyzed · {len(procedure)} steps",
            detail={
                "steps": len(procedure),
                "known_parameters": known,
                "missing_parameters": missing,
                "procedure": procedure,
            },
        )

    return _next_result(
        user_message,
        llm=llm,
        dialogue=dialogue,
        known=known,
        missing=missing,
        procedure=procedure,
        follow_up=follow_up,
        registry=registry,
        on_thought=on_thought,
        trace=trace,
    )


def _continue_collection(
    user_message: str,
    *,
    llm: LLMClient,
    dialogue: list[dict[str, Any]] | None,
    pending: dict[str, Any],
    registry: _ToolRegistry,
    on_thought: ThoughtCallback | None,
    trace: TraceBuilder | None,
) -> RagActionResult:
    known = _normalize_known(pending.get("known_parameters"))
    missing = _normalize_missing(pending.get("missing_parameters"), known)
    procedure = _normalize_procedure(pending.get("procedure"))
    follow_up = _normalize_follow_up(pending.get("follow_up"))

    if missing:
        if on_thought:
            on_thought("Checking the details you provided…")
        provided = _fill_missing_from_reply(
            user_message,
            missing=missing,
            llm=llm,
            dialogue=dialogue,
        )
        known = _merge_known(known, provided)
        missing = _normalize_missing(missing, known)

    if trace and procedure:
        trace.add(
            "procedure",
            f"Continuing procedure · {len(procedure)} steps",
            detail={
                "steps": len(procedure),
                "known_parameters": known,
                "missing_parameters": missing,
            },
        )

    return _next_result(
        user_message,
        llm=llm,
        dialogue=dialogue,
        known=known,
        missing=missing,
        procedure=procedure,
        follow_up=follow_up,
        registry=registry,
        on_thought=on_thought,
        trace=trace,
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
    registry: _ToolRegistry,
    on_thought: ThoughtCallback | None,
    trace: TraceBuilder | None,
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
        if on_thought:
            on_thought("Asking for the remaining required information…")
        question = _ask_for_missing(
            user_message,
            missing=missing,
            llm=llm,
            dialogue=dialogue,
        )
        if trace:
            trace.add(
                "missing_info",
                "Missing information",
                status="pending",
                detail={
                    "question": question,
                    "missing_parameters": missing,
                    "known_parameters": known,
                },
            )
        return RagActionResult(
            response=question,
            pending=state,
            action="request_information",
        )

    return _execute_procedure(
        user_message,
        llm=llm,
        dialogue=dialogue,
        known=known,
        procedure=procedure,
        follow_up=follow_up,
        registry=registry,
        on_thought=on_thought,
        trace=trace,
    )


def _execute_procedure(
    user_message: str,
    *,
    llm: LLMClient,
    dialogue: list[dict[str, Any]] | None,
    known: list[dict[str, str]],
    procedure: list[dict[str, Any]],
    follow_up: list[str],
    registry: _ToolRegistry,
    on_thought: ThoughtCallback | None,
    trace: TraceBuilder | None,
) -> RagActionResult:
    accumulated: dict[str, Any] = {
        "parameters": known,
        "derived": {},
        "steps_log": [],
    }
    total = len(procedure) or 1

    if not procedure:
        log.info("RAG action has no procedure steps; summarizing only")
        if on_thought:
            on_thought("No executable steps found; preparing the summary…")
        return RagActionResult(
            response=_summarize(
                user_message,
                llm=llm,
                dialogue=dialogue,
                accumulated=accumulated,
                follow_up=follow_up,
            ),
            pending=None,
            action="reply",
        )

    if not registry.invokers:
        log.warning("RAG action execution has no MCP tools available")
        return RagActionResult(
            response=_explain_error(
                user_message,
                llm=llm,
                dialogue=dialogue,
                step={"step": 1, "detail": "Procedure execution"},
                failure=(
                    "No operations tools are available right now to run this procedure."
                ),
            ),
            pending=None,
            action="reply",
        )

    for index, step in enumerate(procedure, start=1):
        step_num = int(step.get("step") or index)
        detail = str(step.get("detail") or "").strip()
        if on_thought:
            on_thought(f"Working on step {index}/{total}…")

        decision = _decide_step(
            user_message,
            step=step,
            accumulated=accumulated,
            llm=llm,
            registry=registry,
        )
        action = str(decision.get("action") or "").strip()
        arguments = decision.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}
        domain = str(decision.get("domain") or "NONE").upper()
        thought = str(decision.get("thought") or "").strip()
        if thought and on_thought:
            on_thought(thought)

        parallel_group = domain if domain in {"OPENSHIFT", "AAP", "ITSM"} else None

        if action in {"", "skip", "reply", "unsupported", "out_of_scope"}:
            accumulated["steps_log"].append(
                {
                    "step": step_num,
                    "detail": detail,
                    "tool": None,
                    "ok": True,
                    "skipped": True,
                    "domain": domain,
                    "result_summary": thought or "No tool needed for this step.",
                }
            )
            if trace:
                trace.add(
                    "step",
                    f"Step {step_num} · skipped",
                    status="skipped",
                    detail={
                        "step": step_num,
                        "detail": detail,
                        "domain": domain,
                        "summary": thought or "No tool needed",
                    },
                    parallel_group=parallel_group,
                )
            continue

        if action == "request_information":
            message = arguments.get("message")
            if not isinstance(message, str) or not message.strip():
                message = "Required information was still missing for this step."
            return _abort_procedure(
                user_message,
                llm=llm,
                dialogue=dialogue,
                step=step,
                failure=message.strip(),
                accumulated=accumulated,
                step_num=step_num,
                detail=detail,
                tool=None,
                on_thought=on_thought,
                trace=trace,
                domain=domain,
            )

        if action not in registry.invokers:
            log.warning("RAG action unknown tool action=%s", action)
            failure = (
                f"Could not map step {step_num} to an available operation ({action})."
            )
            return _abort_procedure(
                user_message,
                llm=llm,
                dialogue=dialogue,
                step=step,
                failure=failure,
                accumulated=accumulated,
                step_num=step_num,
                detail=detail,
                tool=action,
                on_thought=on_thought,
                trace=trace,
                domain=domain,
            )

        if on_thought:
            on_thought(f"Calling tool “{action}”…")
        try:
            result = registry.invokers[action](action, arguments)
        except Exception as exc:
            log.exception("RAG action tool failed action=%s", action)
            return _abort_procedure(
                user_message,
                llm=llm,
                dialogue=dialogue,
                step=step,
                failure=str(exc),
                accumulated=accumulated,
                step_num=step_num,
                detail=detail,
                tool=action,
                on_thought=on_thought,
                trace=trace,
                domain=domain,
            )

        if _result_is_error(result):
            failure = _format_result(result)
            log.warning(
                "RAG action tool returned error action=%s step=%s",
                action,
                step_num,
            )
            return _abort_procedure(
                user_message,
                llm=llm,
                dialogue=dialogue,
                step=step,
                failure=failure,
                accumulated=accumulated,
                step_num=step_num,
                detail=detail,
                tool=action,
                on_thought=on_thought,
                trace=trace,
                domain=domain,
            )

        derived = _merge_derived_from_result(
            result,
            llm=llm,
            existing=accumulated["derived"],
        )
        accumulated["derived"] = derived
        result_summary = _format_result(result)[:500]
        accumulated["steps_log"].append(
            {
                "step": step_num,
                "detail": detail,
                "tool": action,
                "ok": True,
                "domain": domain,
                "arguments": arguments,
                "result_summary": result_summary,
            }
        )
        if trace:
            lane = domain if domain in {"OPENSHIFT", "AAP", "ITSM"} else "TOOL"
            trace.add(
                "step",
                f"Step {step_num} · {lane} · {action}",
                detail={
                    "step": step_num,
                    "detail": detail,
                    "domain": domain,
                    "tool": action,
                    "arguments": arguments,
                    "result_summary": result_summary,
                },
                parallel_group=parallel_group,
            )
        if on_thought:
            on_thought(f"Step {index}/{total} completed.")

    if on_thought:
        on_thought("Preparing the final summary…")
    return RagActionResult(
        response=_summarize(
            user_message,
            llm=llm,
            dialogue=dialogue,
            accumulated=accumulated,
            follow_up=follow_up,
        ),
        pending=None,
        action="reply",
    )


def _build_tool_registry(
    *,
    openshift_mcp: OpenShiftMcpClient,
    aap_mcp: AapMcpClient,
    itsm_mcp: ItsmMcpClient,
) -> _ToolRegistry:
    domains: dict[str, _DomainBundle] = {}
    invokers: dict[str, InvokeFn] = {}

    sources: list[
        tuple[str, Callable[[], list[dict[str, Any]]], InvokeFn, PromptBuilder]
    ] = [
        (
            "OPENSHIFT",
            openshift_mcp.get_tools,
            openshift_mcp.invoke,
            build_openshift_prompt,
        ),
        (
            "AAP",
            aap_mcp.list_tools,
            aap_mcp.call_tool,
            build_aap_prompt,
        ),
        (
            "ITSM",
            itsm_mcp.list_tools,
            itsm_mcp.call_tool,
            build_itsm_prompt,
        ),
    ]
    for domain, list_fn, invoke, build_prompt in sources:
        try:
            listed = list_fn()
        except Exception:
            log.exception("RAG action failed listing tools domain=%s", domain)
            continue
        if not isinstance(listed, list):
            continue
        tools: list[dict[str, Any]] = []
        for tool in listed:
            if not isinstance(tool, dict):
                continue
            name = tool.get("name")
            if not isinstance(name, str) or not name:
                continue
            if domain == "ITSM" and name in RAG_MCP_TOOLS:
                continue
            if name in invokers:
                log.warning(
                    "RAG action tool name collision name=%s domain=%s keeping first",
                    name,
                    domain,
                )
                continue
            tools.append(tool)
            invokers[name] = invoke
        if tools:
            domains[domain] = _DomainBundle(
                tools=tools,
                invoke=invoke,
                build_prompt=build_prompt,
            )

    log.info(
        "RAG action tool registry domains=%s tools=%s",
        sorted(domains),
        sorted(invokers),
    )
    return _ToolRegistry(domains=domains, invokers=invokers)


def _decide_step(
    user_message: str,
    *,
    step: dict[str, Any],
    accumulated: dict[str, Any],
    llm: LLMClient,
    registry: _ToolRegistry,
) -> dict[str, Any]:
    domain = _classify_step_domain(
        user_message,
        step=step,
        accumulated=accumulated,
        llm=llm,
    )
    if domain == "NONE":
        return {
            "action": "skip",
            "arguments": {},
            "thought": "Step does not require an operations tool.",
            "domain": domain,
        }

    bundle = registry.domains.get(domain)
    if bundle is None or not bundle.tools:
        return {
            "action": "request_information",
            "arguments": {
                "message": (
                    f"No {domain} operations tools are available to run this step."
                ),
            },
            "thought": f"Domain {domain} has no tools.",
            "domain": domain,
        }

    payload = {
        "user_request": user_message,
        "current_step": step,
        "accumulated_state": {
            "parameters": accumulated.get("parameters") or [],
            "derived": accumulated.get("derived") or {},
            "completed_steps": [
                {
                    "step": item.get("step"),
                    "tool": item.get("tool"),
                    "ok": item.get("ok"),
                    "result_summary": item.get("result_summary"),
                }
                for item in (accumulated.get("steps_log") or [])
            ],
        },
        "instruction": (
            "Execute this procedure step now using exactly one available tool when "
            "needed. Prefer values from accumulated_state. Do not invent identifiers. "
            "If no tool is required, return action reply. If a required argument is "
            "still missing from the state, return request_information."
        ),
    }
    messages = [
        {"role": "system", "content": bundle.build_prompt(bundle.tools)},
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, default=str),
        },
    ]
    raw = llm.chat(messages).strip()
    data = _parse_json_object(raw)
    if not data:
        return {
            "action": "skip",
            "arguments": {},
            "thought": "No decision from specialist.",
            "domain": domain,
        }
    data["domain"] = domain
    return data


def _classify_step_domain(
    user_message: str,
    *,
    step: dict[str, Any],
    accumulated: dict[str, Any],
    llm: LLMClient,
) -> str:
    payload = {
        "user_request": user_message,
        "current_step": step,
        "parameters": accumulated.get("parameters") or [],
        "derived": accumulated.get("derived") or {},
    }
    messages = [
        {"role": "system", "content": build_rag_action_step_domain_prompt()},
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, default=str),
        },
    ]
    raw = llm.chat(messages)
    match = _DOMAIN_RE.search(raw or "")
    if match:
        domain = match.group(1).upper()
        if domain in _VALID_DOMAINS:
            return domain
    upper = (raw or "").strip().upper()
    for name in ("OPENSHIFT", "AAP", "ITSM", "NONE"):
        if name in upper:
            return name
    log.warning("RAG action step domain parse failed raw=%s", (raw or "")[:200])
    return "NONE"


def _merge_derived_from_result(
    result: Any,
    *,
    llm: LLMClient,
    existing: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(existing) if isinstance(existing, dict) else {}
    observation = _format_result(result)
    if len(observation) > _MAX_RESULT_CHARS:
        observation = observation[:_MAX_RESULT_CHARS] + "\n[truncated]"
    messages = [
        {"role": "system", "content": build_rag_action_merge_prompt()},
        {
            "role": "user",
            "content": (
                f"Existing derived state:\n"
                f"{json.dumps(merged, ensure_ascii=False, default=str)}\n\n"
                f"Tool result:\n{observation}"
            ),
        },
    ]
    raw = llm.chat(messages).strip()
    data = _parse_json_object(raw)
    derived = data.get("derived") if isinstance(data.get("derived"), dict) else {}
    for key, value in derived.items():
        text_key = str(key).strip()
        if not text_key or value is None:
            continue
        text_value = str(value).strip()
        if text_value:
            merged[text_key] = text_value
    return merged


def _summarize(
    user_message: str,
    *,
    llm: LLMClient,
    dialogue: list[dict[str, Any]] | None,
    accumulated: dict[str, Any],
    follow_up: list[str],
) -> str:
    payload = {
        "user_request": user_message,
        "parameters": accumulated.get("parameters") or [],
        "derived": accumulated.get("derived") or {},
        "steps_log": accumulated.get("steps_log") or [],
        "follow_up": follow_up,
    }
    messages: list[dict[str, str]] = [
        {"role": "system", "content": build_rag_action_summary_prompt()},
    ]
    messages.extend(_dialogue_as_str(dialogue or []))
    messages.append(
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, default=str),
        }
    )
    return llm.chat(messages).strip()


def _abort_procedure(
    user_message: str,
    *,
    llm: LLMClient,
    dialogue: list[dict[str, Any]] | None,
    step: dict[str, Any],
    failure: str,
    accumulated: dict[str, Any],
    step_num: int,
    detail: str,
    tool: str | None,
    on_thought: ThoughtCallback | None,
    trace: TraceBuilder | None = None,
    domain: str | None = None,
) -> RagActionResult:
    """Stop the whole procedure on the first failed step; never continue."""
    accumulated["steps_log"].append(
        {
            "step": step_num,
            "detail": detail,
            "tool": tool,
            "ok": False,
            "domain": domain,
            "result_summary": failure[:500],
        }
    )
    if trace:
        lane = domain if domain in {"OPENSHIFT", "AAP", "ITSM"} else None
        label = f"Step {step_num} failed"
        if tool:
            label = f"Step {step_num} · {tool} failed"
        trace.add(
            "step",
            label,
            status="error",
            detail={
                "step": step_num,
                "detail": detail,
                "domain": domain,
                "tool": tool,
                "error": failure[:500],
            },
            parallel_group=lane,
        )
    if on_thought:
        on_thought("A step failed; stopping the procedure…")
    return RagActionResult(
        response=_explain_error(
            user_message,
            llm=llm,
            dialogue=dialogue,
            step=step,
            failure=failure,
        ),
        pending=None,
        action="reply",
    )


def _explain_error(
    user_message: str,
    *,
    llm: LLMClient,
    dialogue: list[dict[str, Any]] | None,
    step: dict[str, Any],
    failure: str,
) -> str:
    payload = {
        "user_request": user_message,
        "failed_step": step,
        "failure_detail": failure[:2_000],
        "procedure_aborted": True,
    }
    messages: list[dict[str, str]] = [
        {"role": "system", "content": build_rag_action_error_prompt()},
    ]
    messages.extend(_dialogue_as_str(dialogue or []))
    messages.append(
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, default=str),
        }
    )
    return llm.chat(messages).strip()


def _result_is_error(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("isError") is True or result.get("is_error") is True:
        return True
    if result.get("success") is False or result.get("ok") is False:
        return True
    if result.get("error"):
        return True
    raw = result.get("raw")
    if isinstance(raw, dict) and (
        raw.get("isError") is True or raw.get("is_error") is True
    ):
        return True
    status = result.get("status")
    if isinstance(status, str) and status.strip().lower() in {
        "error",
        "failed",
        "failure",
        "rejected",
    }:
        return True
    return False


def _format_result(result: Any) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    except TypeError:
        return str(result)


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


def _article_id_from_payload(detail: Any) -> str | None:
    """Best-effort id from a get_kb_article payload."""
    found = _extract_article_id(detail)
    if found:
        return found
    if isinstance(detail, dict):
        for key in ("id", "article_id", "kb_id", "uuid"):
            value = detail.get(key)
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
