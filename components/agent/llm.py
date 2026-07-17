from config import Settings
from http_util import request_json
import logging

log = logging.getLogger("agent.llm")


class LLMClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @staticmethod
    def endpoint_for(base_url: str) -> str | None:
        if not base_url:
            return None
        base = base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    @property
    def endpoint(self) -> str | None:
        return self.endpoint_for(self._settings.llm_url)

    def chat(self, messages: list[dict]) -> str:
        if not self._settings.llm_url or not self._settings.llm_api_key:
            raise RuntimeError("LLM_URL and LLM_API_KEY must be set")
        if not self._settings.llm_model:
            raise RuntimeError("LLM_MODEL must be set")

        endpoint = self.endpoint
        log.debug("LLM POST %s model=%s", endpoint, self._settings.llm_model)
        data = request_json(
            "POST",
            endpoint,
            body={"model": self._settings.llm_model, "messages": messages},
            headers={"Authorization": f"Bearer {self._settings.llm_api_key}"},
            timeout=self._settings.llm_timeout,
        )

        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            log.error("Unexpected LLM response keys=%s", list(data) if isinstance(data, dict) else type(data))
            raise RuntimeError(f"Unexpected LLM response: {data}") from exc
