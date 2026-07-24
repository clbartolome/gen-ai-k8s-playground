"""MCP Streamable HTTP client for the Ansible Automation Platform (AAP) MCP server."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from config import Settings
from http_util import ssl_context_for
from logutil import mask_secret

log = logging.getLogger("agent.aap_mcp")


class AapMcpClient:
    """Talks JSON-RPC to the AAP MCP server (Streamable HTTP)."""

    def __init__(self, settings: Settings) -> None:
        url = settings.aap_mcp_url.rstrip("/")
        self._url = url if url.endswith("/mcp") else f"{url}/mcp"
        if not self._url.endswith("/"):
            self._url += "/"
        self._timeout = settings.tools_timeout
        self._token = settings.aap_mcp_token
        self._request_id = 0
        self._session_id: str | None = None
        self._initialized = False
        log.info(
            "AapMcpClient ready url=%s timeout=%ss token=%s",
            self._url,
            self._timeout,
            mask_secret(self._token),
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

    def list_tools(self) -> list[dict]:
        """List tools from the AAP MCP server (tools/list)."""
        result = self._rpc("tools/list")
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            log.warning("AAP MCP tools/list unexpected result type=%s", type(result))
            return []
        log.info(
            "AAP MCP tools count=%s names=%s",
            len(tools),
            [t.get("name") for t in tools if isinstance(t, dict)],
        )
        return tools

    def call_tool(self, name: str, arguments: dict | None = None) -> Any:
        """Call a tool on the AAP MCP server (tools/call)."""
        log.info("AAP MCP tools/call name=%s arguments=%s", name, arguments or {})
        result = self._rpc(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
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
