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

# Used to classify MCP tools from the same itsm-app server.
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
    llm_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout: float
    openshift_mcp_url: str
    aap_mcp_url: str
    aap_mcp_token: str
    itsm_mcp_url: str
    itsm_mcp_token: str
    itsm_mcp_tool_allowlist: list[str]
    tools_timeout: float


def _env(name: str, default: str = "") -> str:
    """Read an env var and strip accidental surrounding quotes."""
    return os.environ.get(name, default).strip().strip("\"'")


def _csv_list(value: str, fallback: tuple[str, ...]) -> list[str]:
    items = [part.strip() for part in value.split(",") if part.strip()]
    return items or list(fallback)


def load_settings() -> Settings:
    return Settings(
        llm_url=_env("LLM_URL"),
        llm_api_key=_env("LLM_API_KEY"),
        llm_model=_env("LLM_MODEL"),
        llm_timeout=float(_env("LLM_TIMEOUT", "120")),
        openshift_mcp_url=_env("OPENSHIFT_MCP_URL"),
        aap_mcp_url=_env("AAP_MCP_URL"),
        aap_mcp_token=_env("AAP_MCP_TOKEN"),
        itsm_mcp_url=_env("ITSM_MCP_URL", "http://itsm-app:8000/mcp/"),
        itsm_mcp_token=_env("ITSM_MCP_TOKEN", "change-me-mcp-token"),
        itsm_mcp_tool_allowlist=_csv_list(
            _env("ITSM_MCP_TOOLS"),
            DEFAULT_ITSM_MCP_TOOLS,
        ),
        tools_timeout=float(_env("TOOLS_TIMEOUT", "30")),
    )
