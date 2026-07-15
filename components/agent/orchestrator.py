import time

from config import Settings
from itsm import ITSMClient
from llm import LLMClient
from mcp import MCPClient
from rag import RAGClient
from state import RunMonitor


class AgentOrchestrator:
    """Coordinates prompt building, tool calls and LLM iterations."""

    def __init__(
        self,
        settings: Settings,
        llm: LLMClient,
        mcp: MCPClient,
        itsm: ITSMClient,
        rag: RAGClient,
        monitor: RunMonitor,
    ) -> None:
        self._settings = settings
        self._llm = llm
        self._mcp = mcp
        self._itsm = itsm
        self._rag = rag
        self._monitor = monitor

    def build_system_prompt(self) -> str:
        return (
            "You are the Gen AI Playground operations assistant.\n"
            "No matter the question, you should always respond with a joke.\n"
        )

    def build_messages(self, user_message: str) -> list[dict]:
        return [
            {"role": "system", "content": self.build_system_prompt()},
            {"role": "user", "content": user_message},
        ]

    def run(self, user_message: str) -> str:
        """Handle one user message. Tool iteration will be added here."""
        start = self._monitor.begin(user_message)

        try:
            if self._settings.delay_seconds > 0:
                self._monitor.set_step("delay")
                time.sleep(self._settings.delay_seconds)

            llm_start = self._monitor.begin_llm()
            try:
                response = self._llm.chat(self.build_messages(user_message))
            except Exception:
                self._monitor.fail_llm()
                raise

            llm_duration_ms = round((time.perf_counter() - llm_start) * 1000)
            duration_ms = round((time.perf_counter() - start) * 1000)

            self._monitor.complete_llm(response, llm_duration_ms)
            self._monitor.complete(response, duration_ms)
            return response
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start) * 1000)
            self._monitor.fail(str(exc), duration_ms)
            raise
