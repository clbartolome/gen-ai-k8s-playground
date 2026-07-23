import os
from dataclasses import dataclass

DEFAULT_ITSM_MCP_TOOLS = (
    "rag_search_kb",
    "search_kb",
    "list_kb_articles",
    "get_kb_article",
    "list_incidents",
    "get_incident",
    "create_incident",
    "add_comment",
    "update_severity",
    "close_incident",
)

KB_MCP_TOOLS = frozenset(
    {
        "rag_search_kb",
        "search_kb",
        "list_kb_articles",
        "get_kb_article",
        "create_kb_article",
    }
)


@dataclass(frozen=True)
class Settings:
    delay_seconds: float
    llm_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout: float
    mcp_url: str
    openshift_mcp_url: str
    itsm_mcp_url: str
    itsm_mcp_token: str
    itsm_mcp_tool_allowlist: list[str]
    tools_timeout: float
    max_react_iterations: int


def _csv_list(value: str, fallback: tuple[str, ...]) -> list[str]:
    items = [part.strip() for part in value.split(",") if part.strip()]
    return items or list(fallback)


def _env(name: str, default: str = "") -> str:
    """Read an env var and strip accidental surrounding quotes."""
    return os.environ.get(name, default).strip().strip("\"'")


def load_settings() -> Settings:
    return Settings(
        delay_seconds=float(_env("DELAY_SECONDS", "0")),
        llm_url=_env("LLM_URL"),
        llm_api_key=_env("LLM_API_KEY"),
        llm_model=_env("LLM_MODEL"),
        llm_timeout=float(_env("LLM_TIMEOUT", "120")),
        mcp_url=_env("MCP_URL", "http://localhost:9001"),
        openshift_mcp_url=_env("OPENSHIFT_MCP_URL"),
        itsm_mcp_url=_env("ITSM_MCP_URL", "http://itsm-app:8000/mcp/"),
        itsm_mcp_token=_env("ITSM_MCP_TOKEN", "change-me-mcp-token"),
        itsm_mcp_tool_allowlist=_csv_list(
            _env("ITSM_MCP_TOOLS"),
            DEFAULT_ITSM_MCP_TOOLS,
        ),
        tools_timeout=float(_env("TOOLS_TIMEOUT", "30")),
        max_react_iterations=int(_env("MAX_REACT_ITERATIONS", "5")),
    )
