import logging
import os

from flask import Flask, jsonify, request

from config import load_settings
from itsm_mcp import ItsmMcpClient
from llm import LLMClient
from logutil import mask_secret, setup_logging
from mcp import MCPClient
from orchestrator import AgentOrchestrator
from reporter import EventReporter

setup_logging()
log = logging.getLogger("agent.main")

settings = load_settings()
log.info(
    "Agent settings llm_model=%s mcp_url=%s itsm_mcp_url=%s itsm_token=%s monitor_url=%s",
    settings.llm_model or "(empty)",
    settings.mcp_url,
    settings.itsm_mcp_url,
    mask_secret(settings.itsm_mcp_token),
    settings.monitor_url or "(empty)",
)

orchestrator = AgentOrchestrator(
    settings=settings,
    llm=LLMClient(settings),
    platform_mcp=MCPClient(settings),
    itsm_mcp=ItsmMcpClient(settings),
    reporter=EventReporter(settings),
)

app = Flask(__name__)


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "agent"})


@app.post("/message")
def message():
    data = request.get_json(silent=True) or {}
    user_message = data.get("message", "")
    log.info("POST /message chars=%s", len(user_message))

    try:
        response = orchestrator.run(user_message)
        log.info("POST /message ok response_chars=%s", len(response))
        return jsonify({"response": response})
    except Exception as exc:
        log.exception("POST /message failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    log.info("Starting agent on :%s", port)
    app.run(host="0.0.0.0", port=port, threaded=True)
