import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    delay_seconds: float
    llm_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout: float
    mcp_url: str
    itsm_url: str
    rag_url: str
    tools_timeout: float
    monitor_url: str
    monitor_timeout: float
    max_react_iterations: int


def load_settings() -> Settings:
    return Settings(
        delay_seconds=float(os.environ.get("DELAY_SECONDS", "0")),
        llm_url=os.environ.get("LLM_URL", ""),
        llm_api_key=os.environ.get("LLM_API_KEY", ""),
        llm_model=os.environ.get("LLM_MODEL", ""),
        llm_timeout=float(os.environ.get("LLM_TIMEOUT", "120")),
        mcp_url=os.environ.get("MCP_URL", "http://localhost:9001"),
        itsm_url=os.environ.get("ITSM_URL", "http://localhost:9002"),
        rag_url=os.environ.get("RAG_URL", "http://localhost:9003"),
        tools_timeout=float(os.environ.get("TOOLS_TIMEOUT", "30")),
        monitor_url=os.environ.get("MONITOR_URL", "http://localhost:9010"),
        monitor_timeout=float(os.environ.get("MONITOR_TIMEOUT", "5")),
        max_react_iterations=int(os.environ.get("MAX_REACT_ITERATIONS", "5")),
    )
