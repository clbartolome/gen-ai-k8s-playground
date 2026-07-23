import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    llm_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout: float
    openshift_mcp_url: str
    tools_timeout: float


def _env(name: str, default: str = "") -> str:
    """Read an env var and strip accidental surrounding quotes."""
    return os.environ.get(name, default).strip().strip("\"'")


def load_settings() -> Settings:
    return Settings(
        llm_url=_env("LLM_URL"),
        llm_api_key=_env("LLM_API_KEY"),
        llm_model=_env("LLM_MODEL"),
        llm_timeout=float(_env("LLM_TIMEOUT", "120")),
        openshift_mcp_url=_env("OPENSHIFT_MCP_URL"),
        tools_timeout=float(_env("TOOLS_TIMEOUT", "30")),
    )
