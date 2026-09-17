"""MCP Streamable HTTP client for the Ansible Automation Platform (AAP) MCP server."""

from __future__ import annotations

import contextvars
import copy
import json
import logging
import urllib.error
import urllib.request
from typing import Any

from config import Settings
from http_util import ssl_context_for
from logutil import mask_secret
from search_match import extract_item_id_by_exact_name, quoted_name_from_step

log = logging.getLogger("agent.aap_mcp")

LAUNCH_TOOLS_WITH_EXTRA_VARS = frozenset(
    {
        "workflow_job_templates_launch_create",
        "job_templates_launch_create",
    }
)
LIST_TEMPLATE_TOOLS = frozenset(
    {
        "workflow_job_templates_list",
        "job_templates_list",
    }
)

REQUEST_BODY_KEY = "requestBody"
_REQUEST_BODY_KEYS = (REQUEST_BODY_KEY, "request_body")
_TEMPLATE_ID_KEYS = (
    "template_id",
    "workflow_job_template_id",
    "workflow_template_id",
    "job_template_id",
)

_thread_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "aap_thread_id",
    default=None,
)


def set_aap_thread_context(thread_id: str | None) -> None:
    """Bind the current agent thread id for downstream AAP tool calls."""
    _thread_id_ctx.set(thread_id)


def inject_thread_id_into_arguments(
    arguments: dict[str, Any] | None,
    *,
    tool_name: str,
    thread_id: str | None,
) -> dict[str, Any]:
    """Fold launch extra_vars, stringify them, and merge thread_id."""
    args = copy.deepcopy(arguments or {})
    if tool_name in LAUNCH_TOOLS_WITH_EXTRA_VARS:
        args = _fold_launch_extra_vars(args)

    located = _find_request_body(args)
    serialize_extra_vars = tool_name in LAUNCH_TOOLS_WITH_EXTRA_VARS
    if located is not None:
        _, body = located
        body["extra_vars"] = _prepare_extra_vars(
            body.get("extra_vars"),
            thread_id,
            serialize=serialize_extra_vars,
        )
        if serialize_extra_vars:
            _ensure_request_body_key(args)
        return args

    if "extra_vars" in args:
        args["extra_vars"] = _prepare_extra_vars(
            args.get("extra_vars"),
            thread_id,
            serialize=serialize_extra_vars,
        )
        return args

    if thread_id and serialize_extra_vars:
        args[REQUEST_BODY_KEY] = {
            "extra_vars": _prepare_extra_vars(None, thread_id, serialize=True),
        }

    if serialize_extra_vars:
        _ensure_request_body_key(args)
    return args


def apply_launch_template_id(
    arguments: dict[str, Any] | None,
    *,
    derived: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Set launch `id` from the list step when the article does not include it."""
    args = copy.deepcopy(arguments or {})
    source = derived if isinstance(derived, dict) else {}
    for key in _TEMPLATE_ID_KEYS:
        value = source.get(key)
        if value is None or str(value).strip() == "":
            continue
        args["id"] = str(value).strip()
        return args
    current = _launch_id(args)
    if current:
        return args
    for key in ("id", "pk"):
        value = args.get(key)
        if value is not None and not _looks_like_template_id(str(value).strip()):
            args.pop(key, None)
    return args


def launch_template_id(arguments: dict[str, Any] | None) -> str:
    return _launch_id(dict(arguments or {}))


def extract_template_id(result: Any, template_name: str | None = None) -> str:
    """Pick a template id from a list/search result by exact name match."""
    return extract_item_id_by_exact_name(result, template_name)


def _fold_launch_extra_vars(args: dict[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if "extra_vars" in args:
        extra.update(_stringify_extra_vars(args.pop("extra_vars")))
    located = _find_request_body(args)
    body: dict[str, Any] = {}
    if located is not None:
        _, found = located
        extra.update(_stringify_extra_vars(found.get("extra_vars")))
        body = {key: value for key, value in found.items() if key != "extra_vars"}
    kept: dict[str, Any] = {}
    for key, value in args.items():
        if key in _REQUEST_BODY_KEYS:
            continue
        if key in {"id", "pk"}:
            kept[key] = value
            continue
        if value is None:
            continue
        extra.setdefault(str(key), value)
    body["extra_vars"] = extra
    kept[REQUEST_BODY_KEY] = body
    return kept


def _launch_id(args: dict[str, Any]) -> str:
    for key in ("id", "pk"):
        value = args.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text and _looks_like_template_id(text):
            return text
    return ""


def _looks_like_template_id(value: str) -> bool:
    if value.isdigit():
        return True
    lowered = value.casefold()
    if " " in value or "deploy" in lowered or "template" in lowered:
        return False
    return True


def _find_request_body(args: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    for key in _REQUEST_BODY_KEYS:
        body = args.get(key)
        if isinstance(body, dict):
            return key, body
    return None


def _ensure_request_body_key(args: dict[str, Any]) -> None:
    """Normalize launch payloads to the camelCase key expected by AAP MCP."""
    snake = args.pop("request_body", None)
    if not isinstance(snake, dict):
        return
    camel = args.get(REQUEST_BODY_KEY)
    if isinstance(camel, dict):
        args[REQUEST_BODY_KEY] = {**snake, **camel}
    else:
        args[REQUEST_BODY_KEY] = snake


def _prepare_extra_vars(
    extra_vars: Any,
    thread_id: str | None,
    *,
    serialize: bool = False,
) -> dict[str, str] | str:
    merged = _stringify_extra_vars(extra_vars)
    if thread_id:
        merged.setdefault("thread_id", thread_id)
    if serialize:
        return json.dumps(merged, ensure_ascii=False, separators=(",", ":"))
    return merged


def _stringify_extra_vars(extra_vars: Any) -> dict[str, str]:
    if isinstance(extra_vars, str):
        try:
            parsed = json.loads(extra_vars)
        except json.JSONDecodeError:
            parsed = {}
        source = parsed if isinstance(parsed, dict) else {}
    elif isinstance(extra_vars, dict):
        source = extra_vars
    else:
        source = {}
    out: dict[str, str] = {}
    for key, value in source.items():
        name = str(key)
        if not name or value is None:
            continue
        out[name] = value if isinstance(value, str) else str(value)
    return out


class AapMcpClient:
    """Talks JSON-RPC to the AAP MCP server (Streamable HTTP)."""

    def __init__(self, settings: Settings) -> None:
        url = settings.aap_mcp_url.rstrip("/")
        self._url = url if url.endswith("/mcp") else f"{url}/mcp"
        if not self._url.endswith("/"):
            self._url += "/"
        self._timeout = settings.tools_timeout
        self._token = settings.aap_mcp_token
        self._allowlist = settings.aap_mcp_tool_allowlist
        self._request_id = 0
        self._session_id: str | None = None
        self._initialized = False
        log.info(
            "AapMcpClient ready url=%s timeout=%ss token=%s allowlist=%s",
            self._url,
            self._timeout,
            mask_secret(self._token),
            ",".join(self._allowlist) or "(all)",
        )

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        return headers

    @staticmethod
    def _parse_body(raw: str, content_type: str) -> dict:
        raw = raw.strip()
        if not raw:
            return {}
        if "text/event-stream" in content_type:
            last: dict = {}
            for line in raw.splitlines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                last = json.loads(payload)
            return last
        return json.loads(raw)

    def _post(self, payload: dict) -> dict:
        method = payload.get("method", "?")
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._url,
            data=data,
            headers=self._headers(),
            method="POST",
        )
        log.debug(
            "AAP MCP POST %s method=%s session=%s bytes=%s",
            self._url,
            method,
            self._session_id or "-",
            len(data),
        )
        try:
            with urllib.request.urlopen(
                req, timeout=self._timeout, context=ssl_context_for(self._url)
            ) as resp:
                session = resp.headers.get("mcp-session-id")
                if session:
                    self._session_id = session
                content_type = resp.headers.get("Content-Type", "")
                status = getattr(resp, "status", None) or resp.getcode()
                body = resp.read().decode("utf-8")
                log.debug(
                    "AAP MCP response status=%s content_type=%s body_len=%s session=%s",
                    status,
                    content_type,
                    len(body),
                    self._session_id or "-",
                )
                return self._parse_body(body, content_type)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            log.error(
                "AAP MCP HTTP error status=%s method=%s url=%s detail=%s",
                exc.code,
                method,
                self._url,
                detail[:500],
            )
            raise RuntimeError(
                f"AAP MCP failed ({exc.code}) POST {self._url}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            log.error("AAP MCP connection error url=%s reason=%s", self._url, exc.reason)
            raise RuntimeError(
                f"AAP MCP connection failed POST {self._url}: {exc.reason}"
            ) from exc

    def _rpc(self, method: str, params: dict | None = None) -> Any:
        self._ensure_session()
        log.info("AAP MCP rpc method=%s", method)
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": method,
                "params": params or {},
            }
        )
        if "error" in response:
            log.error("AAP MCP rpc error method=%s error=%s", method, response["error"])
            raise RuntimeError(f"AAP MCP error on {method}: {response['error']}")
        return response.get("result")

    def _ensure_session(self) -> None:
        if self._initialized:
            return
        log.info("AAP MCP initialize → %s", self._url)
        init = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "gen-ai-playground-agent",
                        "version": "0.1.0",
                    },
                },
            }
        )
        if "error" in init:
            log.error("AAP MCP initialize failed: %s", init["error"])
            raise RuntimeError(f"AAP MCP initialize failed: {init['error']}")
        server = (init.get("result") or {}).get("serverInfo") if isinstance(init, dict) else None
        log.info(
            "AAP MCP initialized session=%s server=%s",
            self._session_id or "-",
            server or init.get("result"),
        )
        self._post(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
        )
        self._initialized = True

    def list_tools(self, *, allowlist: list[str] | None = None) -> list[dict]:
        """List tools from the AAP MCP server (tools/list), filtered by allowlist."""
        result = self._rpc("tools/list")
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            log.warning("AAP MCP tools/list unexpected result type=%s", type(result))
            return []
        all_names = [t.get("name") for t in tools if isinstance(t, dict)]
        log.info("AAP MCP tools/list raw_count=%s names=%s", len(tools), all_names)
        names = allowlist if allowlist is not None else self._allowlist
        if names:
            allowed = set(names)
            tools = [t for t in tools if t.get("name") in allowed]
            log.info(
                "AAP MCP tools after allowlist count=%s names=%s",
                len(tools),
                [t.get("name") for t in tools],
            )
        return tools

    def call_tool(self, name: str, arguments: dict | None = None) -> Any:
        """Call a tool on the AAP MCP server (tools/call)."""
        thread_id = _thread_id_ctx.get()
        prepared = inject_thread_id_into_arguments(
            arguments,
            tool_name=name,
            thread_id=thread_id,
        )
        if prepared != (arguments or {}):
            log.info(
                "AAP MCP tools/call name=%s injected thread_id=%s",
                name,
                thread_id,
            )
        log.info("AAP MCP tools/call name=%s arguments=%s", name, prepared)
        result = self._rpc(
            "tools/call",
            {"name": name, "arguments": prepared},
        )
        normalized = self._normalize_tool_result(result)
        preview = json.dumps(normalized, ensure_ascii=False, default=str)
        log.info(
            "AAP MCP tools/call done name=%s result_preview=%s",
            name,
            preview[:400],
        )
        return normalized

    @staticmethod
    def _normalize_tool_result(result: Any) -> Any:
        if not isinstance(result, dict):
            return result
        content = result.get("content")
        if not isinstance(content, list):
            return result
        texts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(str(item.get("text", "")))
        if not texts:
            return result
        merged = "\n".join(texts)
        try:
            return json.loads(merged)
        except json.JSONDecodeError:
            return {"text": merged, "raw": result}
