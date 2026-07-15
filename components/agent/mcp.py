from typing import Any

from config import Settings
from http_util import request_json


class MCPClient:
    def __init__(self, settings: Settings) -> None:
        self._base = settings.mcp_url.rstrip("/")
        self._timeout = settings.tools_timeout

    def list_tools(self) -> list[dict]:
        data = request_json("GET", f"{self._base}/tools", timeout=self._timeout)
        return data.get("tools", [])

    def invoke(self, name: str, arguments: dict | None = None) -> Any:
        return request_json(
            "POST",
            f"{self._base}/tools/{name}/invoke",
            body=arguments or {},
            timeout=self._timeout,
        )

    def get_service_health(self, service: str) -> dict:
        return self.invoke("get_service_health", {"service": service})

    def get_recent_events(self, component: str | None = None) -> dict:
        body = {"component": component} if component else {}
        return self.invoke("get_recent_events", body)

    def list_active_alerts(self, component: str | None = None) -> dict:
        body = {"component": component} if component else {}
        return self.invoke("list_active_alerts", body)
