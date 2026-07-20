import logging
import os
import threading
import uuid

from flask import Flask, jsonify, request

from config import load_settings
from itsm_mcp import ItsmMcpClient
from llm import LLMClient
from logutil import mask_secret, setup_logging
from mcp import MCPClient
from orchestrator import AgentOrchestrator

setup_logging()
log = logging.getLogger("agent.main")

settings = load_settings()
log.info(
    "Agent settings llm_model=%s mcp_url=%s itsm_mcp_url=%s itsm_token=%s",
    settings.llm_model or "(empty)",
    settings.mcp_url,
    settings.itsm_mcp_url,
    mask_secret(settings.itsm_mcp_token),
)

orchestrator = AgentOrchestrator(
    settings=settings,
    llm=LLMClient(settings),
    platform_mcp=MCPClient(settings),
    itsm_mcp=ItsmMcpClient(settings),
)

app = Flask(__name__)

runs: dict[str, dict] = {}
runs_lock = threading.Lock()


def _update_run(run_id: str, **fields) -> None:
    with runs_lock:
        run = runs.get(run_id)
        if not run:
            return
        run.update(fields)


def _append_thought(run_id: str, text: str) -> None:
    with runs_lock:
        run = runs.get(run_id)
        if not run:
            return
        run.setdefault("thoughts", []).append({"text": text})


def _process_run(run_id: str, user_message: str, history: list) -> None:
    try:
        response = orchestrator.run(
            user_message,
            history=history,
            on_thought=lambda text: _append_thought(run_id, text),
        )
        _update_run(run_id, status="done", response=response)
    except Exception as exc:
        log.exception("Run %s failed: %s", run_id, exc)
        _update_run(run_id, status="error", error=str(exc))


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "agent"})


@app.post("/message")
def message():
    data = request.get_json(silent=True) or {}
    user_message = data.get("message", "")
    history = data.get("history") or []
    if not isinstance(history, list):
        history = []

    run_id = str(uuid.uuid4())
    with runs_lock:
        runs[run_id] = {"status": "running", "thoughts": [], "response": None}

    log.info("POST /message run_id=%s chars=%s history=%s", run_id, len(user_message), len(history))
    threading.Thread(
        target=_process_run,
        args=(run_id, user_message, history),
        daemon=True,
    ).start()
    return jsonify({"run_id": run_id})


@app.get("/runs/<run_id>")
def get_run(run_id: str):
    with runs_lock:
        run = runs.get(run_id)
        if run is None:
            return jsonify({"error": "run not found"}), 404
        return jsonify(run)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    log.info("Starting agent on :%s", port)
    app.run(host="0.0.0.0", port=port, threaded=True)
