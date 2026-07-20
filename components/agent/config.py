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
    itsm_mcp_url: str
    itsm_mcp_token: str
    itsm_mcp_tool_allowlist: list[str]
    tools_timeout: float
    max_react_iterations: int


def _csv_list(value: str, fallback: tuple[str, ...]) -> list[str]:
    items = [part.strip() for part in value.split(",") if part.strip()]
    return items or list(fallback)


def load_settings() -> Settings:
    return Settings(
        delay_seconds=float(os.environ.get("DELAY_SECONDS", "0")),
        llm_url=os.environ.get("LLM_URL", ""),
        llm_api_key=os.environ.get("LLM_API_KEY", ""),
        llm_model=os.environ.get("LLM_MODEL", ""),
        llm_timeout=float(os.environ.get("LLM_TIMEOUT", "120")),
        mcp_url=os.environ.get("MCP_URL", "http://localhost:9001"),
        itsm_mcp_url=os.environ.get("ITSM_MCP_URL", "http://itsm-app:8000/mcp/"),
        itsm_mcp_token=os.environ.get("ITSM_MCP_TOKEN", "change-me-mcp-token"),
        itsm_mcp_tool_allowlist=_csv_list(
            os.environ.get("ITSM_MCP_TOOLS", ""),
            DEFAULT_ITSM_MCP_TOOLS,
        ),
        tools_timeout=float(os.environ.get("TOOLS_TIMEOUT", "30")),
        max_react_iterations=int(os.environ.get("MAX_REACT_ITERATIONS", "5")),
    )
