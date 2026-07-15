from config import Settings
from http_util import request_json


class RAGClient:
    def __init__(self, settings: Settings) -> None:
        self._base = settings.rag_url.rstrip("/")
        self._timeout = settings.tools_timeout

    def list_documents(self) -> list[dict]:
        data = request_json("GET", f"{self._base}/documents", timeout=self._timeout)
        return data.get("documents", [])

    def search(self, query: str) -> dict:
        return request_json(
            "POST",
            f"{self._base}/search",
            body={"query": query},
            timeout=self._timeout,
        )
