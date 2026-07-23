"""MCP Streamable HTTP client for the OpenShift MCP server."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from config import Settings
from http_util import ssl_context_for

log = logging.getLogger("agent.openshift_mcp")


class OpenShiftMcpClient:
    """Minimal OpenShift MCP client: get_tools + invoke."""

    def __init__(self, settings: Settings) -> None:
        self._url = settings.openshift_mcp_url.rstrip("/")
        self._timeout = settings.tools_timeout
        self._request_id = 0
        log.info("OpenShiftMcpClient ready url=%s timeout=%ss", self._url, self._timeout)

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

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
        log.debug("OpenShift MCP POST method=%s bytes=%s", method, len(data))
        try:
            with urllib.request.urlopen(
                req, timeout=self._timeout, context=ssl_context_for(self._url)
            ) as resp:
                content_type = resp.headers.get("Content-Type", "")
                body = resp.read().decode("utf-8")
                return self._parse_body(body, content_type)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"OpenShift MCP failed ({exc.code}) POST {self._url}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"OpenShift MCP connection failed POST {self._url}: {exc.reason}"
            ) from exc

    def _rpc(self, method: str, params: dict | None = None) -> Any:
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": method,
                "params": params or {},
            }
        )
        if "error" in response:
            raise RuntimeError(f"OpenShift MCP error on {method}: {response['error']}")
        return response.get("result")

    def get_tools(self) -> list[dict]:
        """List tools from the OpenShift MCP server (tools/list)."""
        result = self._rpc("tools/list")
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            log.warning("tools/list unexpected result type=%s", type(result))
            return []
        log.info(
            "OpenShift MCP tools count=%s names=%s",
            len(tools),
            [t.get("name") for t in tools if isinstance(t, dict)],
        )
        return tools

    def invoke(self, name: str, arguments: dict | None = None) -> Any:
        """Call a tool on the OpenShift MCP server (tools/call)."""
        log.info("OpenShift MCP invoke name=%s arguments=%s", name, arguments or {})
        return self._rpc(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )
