"""MCP Streamable HTTP client for the Ansible Automation Platform (AAP) MCP server."""

from __future__ import annotations

import contextvars
import copy
import json
import logging
import urllib.error
import urllib.request
from typing import Any, Callable

from config import Settings
from http_util import ssl_context_for
from logutil import mask_secret

log = logging.getLogger("agent.aap_mcp")

LAUNCH_TOOLS_WITH_EXTRA_VARS = frozenset(
    {
        "workflow_job_templates_launch_create",
        "job_templates_launch_create",
    }
)

LAUNCH_TO_LIST_TOOL = {
    "job_templates_launch_create": "job_templates_list",
    "workflow_job_templates_launch_create": "workflow_job_templates_list",
}

_TEMPLATE_NAME_KEYS = (
    "name",
    "job_template_name",
    "template_name",
    "workflow_job_template_name",
    "job_template",
    "workflow_job_template",
)

_REQUEST_BODY_KEYS = ("request_body", "requestBody")


class TemplateResolveError(RuntimeError):
    """Failed to map a template name to an AAP id."""


class TemplateNotFoundError(TemplateResolveError):
    """No template matched the provided name."""


class TemplateAmbiguousError(TemplateResolveError):
    """Multiple templates matched the provided name."""

    def __init__(self, name: str, matches: list[dict[str, Any]]) -> None:
        self.name = name
        self.matches = matches
        options = ", ".join(
            f"{item.get('name')} (id={item.get('id')})"
            for item in matches[:5]
        )
        super().__init__(
            f"Multiple job templates match {name!r}: {options}. "
            "Please specify the exact template name or provide the numeric id."
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
    """Merge thread_id into extra_vars without removing existing values."""
    if not thread_id:
        return dict(arguments or {})

    args = copy.deepcopy(arguments or {})

    located = _find_request_body(args)
    if located is not None:
        _, body = located
        body["extra_vars"] = _merge_thread_id(body.get("extra_vars"), thread_id)
        return args

    if "extra_vars" in args:
        args["extra_vars"] = _merge_thread_id(args.get("extra_vars"), thread_id)
        return args

    if tool_name in LAUNCH_TOOLS_WITH_EXTRA_VARS:
        args["request_body"] = {
            "extra_vars": _merge_thread_id(None, thread_id),
        }

    return args


def _find_request_body(args: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    for key in _REQUEST_BODY_KEYS:
        body = args.get(key)
        if isinstance(body, dict):
            return key, body
    return None


def _merge_thread_id(extra_vars: Any, thread_id: str) -> dict[str, Any]:
    merged = copy.deepcopy(extra_vars) if isinstance(extra_vars, dict) else {}
    merged.setdefault("thread_id", thread_id)
    return merged


def normalize_extra_var_value(value: Any) -> Any:
    """Coerce numeric extra_vars to strings for AAP survey/job template launches."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): normalize_extra_var_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [normalize_extra_var_value(item) for item in value]
    return value


def normalize_launch_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Normalize extra_vars in launch tool arguments before calling AAP."""
    args = copy.deepcopy(arguments)
    located = _find_request_body(args)
    if located is not None:
        _, body = located
        if "extra_vars" in body:
            body["extra_vars"] = normalize_extra_var_value(body.get("extra_vars"))
        return args
    if "extra_vars" in args:
        args["extra_vars"] = normalize_extra_var_value(args.get("extra_vars"))
    return args


def is_numeric_template_id(value: Any) -> bool:
    """Return True when value is a positive integer id (not a template name)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value > 0
    if isinstance(value, str):
        stripped = value.strip()
        return bool(stripped) and stripped.isdigit()
    return False


def normalize_template_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


def extract_template_list(result: Any) -> list[dict[str, Any]]:
    """Parse job/workflow template list payloads from MCP tool results."""
    payload = result
    if isinstance(result, str):
        try:
            payload = json.loads(result)
        except json.JSONDecodeError:
            return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("results", "items", "data"):
            items = payload.get(key)
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
    return []


def find_templates_by_name(
    items: list[dict[str, Any]],
    name: str,
) -> list[dict[str, Any]]:
    """Match templates by exact name; fall back to a unique partial match."""
    target = normalize_template_name(name)
    if not target:
        return []

    exact = [
        item
        for item in items
        if normalize_template_name(str(item.get("name") or "")) == target
    ]
    if exact:
        return exact

    partial = [
        item
        for item in items
        if target in normalize_template_name(str(item.get("name") or ""))
    ]
    if partial:
        return partial
    return []


def resolve_template_id_from_items(
    items: list[dict[str, Any]],
    name: str,
) -> int:
    matches = find_templates_by_name(items, name)
    if not matches:
        raise TemplateNotFoundError(
            f"No job template found with name {name!r}. "
            "Check the template name in Ansible Automation Platform."
        )
    if len(matches) > 1:
        raise TemplateAmbiguousError(name, matches)
    template_id = matches[0].get("id")
    if template_id is None:
        raise TemplateNotFoundError(
            f"Template {name!r} was matched but has no id in AAP."
        )
    return int(template_id)


def extract_template_reference(args: dict[str, Any]) -> tuple[str, str] | None:
    """Return (field_name, value) for the template id or name in launch args."""
    id_val = args.get("id")
    if id_val is not None and str(id_val).strip():
        return "id", str(id_val).strip()
    for key in _TEMPLATE_NAME_KEYS:
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return key, val.strip()
    return None


def resolve_launch_template_arguments(
    arguments: dict[str, Any],
    *,
    tool_name: str,
    list_items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Replace a template name with its numeric id before launch."""
    if tool_name not in LAUNCH_TO_LIST_TOOL:
        return arguments

    ref = extract_template_reference(arguments)
    if ref is None:
        return arguments

    field, value = ref
    if is_numeric_template_id(value):
        resolved = copy.deepcopy(arguments)
        if field == "id":
            resolved["id"] = str(int(str(value).strip()))
        return resolved

    resolved_id = resolve_template_id_from_items(list_items, value)
    resolved = copy.deepcopy(arguments)
    resolved["id"] = str(resolved_id)
    if field != "id":
        resolved.pop(field, None)
    log.info(
        "AAP resolved template name=%s id=%s tool=%s",
        value,
        resolved_id,
        tool_name,
    )
    return resolved


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
        self._template_list_cache: dict[str, list[dict[str, Any]]] = {}
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

    def _load_template_list(self, list_tool: str) -> list[dict[str, Any]]:
        cached = self._template_list_cache.get(list_tool)
        if cached is not None:
            return cached
        if list_tool not in self._allowlist:
            raise TemplateNotFoundError(
                f"Cannot resolve template name: list tool {list_tool!r} is not enabled."
            )
        log.info("AAP MCP loading template list tool=%s", list_tool)
        result = self._rpc("tools/call", {"name": list_tool, "arguments": {}})
        items = extract_template_list(self._normalize_tool_result(result))
        self._template_list_cache[list_tool] = items
        log.info(
            "AAP MCP cached template list tool=%s count=%s",
            list_tool,
            len(items),
        )
        return items

    def _resolve_launch_template_id(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        list_loader: Callable[[str], list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        list_tool = LAUNCH_TO_LIST_TOOL.get(tool_name)
        if not list_tool:
            return arguments

        ref = extract_template_reference(arguments)
        if ref is None:
            return arguments

        _, value = ref
        if is_numeric_template_id(value):
            return arguments

        loader = list_loader or self._load_template_list
        items = loader(list_tool)
        return resolve_launch_template_arguments(
            arguments,
            tool_name=tool_name,
            list_items=items,
        )

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
        if name in LAUNCH_TOOLS_WITH_EXTRA_VARS:
            prepared = self._resolve_launch_template_id(name, prepared)
            prepared = normalize_launch_arguments(prepared)
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
