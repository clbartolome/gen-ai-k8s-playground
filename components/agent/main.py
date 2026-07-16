import os

from flask import Flask, jsonify, request

from config import load_settings
from itsm import ITSMClient
from llm import LLMClient
from mcp import MCPClient
from orchestrator import AgentOrchestrator
from rag import RAGClient
from reporter import EventReporter

settings = load_settings()
orchestrator = AgentOrchestrator(
    settings=settings,
    llm=LLMClient(settings),
    mcp=MCPClient(settings),
    itsm=ITSMClient(settings),
    rag=RAGClient(settings),
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

    try:
        response = orchestrator.run(user_message)
        return jsonify({"response": response})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, threaded=True)
