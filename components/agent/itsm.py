from urllib.parse import urlencode

from config import Settings
from http_util import request_json


class ITSMClient:
    def __init__(self, settings: Settings) -> None:
        self._base = settings.itsm_url.rstrip("/")
        self._timeout = settings.tools_timeout

    def list_tickets(
        self,
        *,
        component: str | None = None,
        status: str | None = None,
        severity: str | None = None,
    ) -> list[dict]:
        params: dict[str, str] = {}
        if component:
            params["component"] = component
        if status:
            params["status"] = status
        if severity:
            params["severity"] = severity

        query = f"?{urlencode(params)}" if params else ""
        data = request_json("GET", f"{self._base}/tickets{query}", timeout=self._timeout)
        return data.get("tickets", [])

    def get_ticket(self, ticket_id: str) -> dict:
        return request_json(
            "GET", f"{self._base}/tickets/{ticket_id}", timeout=self._timeout
        )

    def create_ticket(self, payload: dict) -> dict:
        return request_json(
            "POST",
            f"{self._base}/tickets",
            body=payload,
            timeout=self._timeout,
        )

    def update_ticket(self, ticket_id: str, payload: dict) -> dict:
        return request_json(
            "PATCH",
            f"{self._base}/tickets/{ticket_id}",
            body=payload,
            timeout=self._timeout,
        )
