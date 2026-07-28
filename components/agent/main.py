import logging
import os
import threading
import uuid

from flask import Flask, jsonify, request

from aap_mcp import AapMcpClient
from config import load_settings
from itsm_mcp import ItsmMcpClient
from llm import LLMClient
from logutil import setup_logging
from openshift_mcp import OpenShiftMcpClient
from react import ReactAgent

setup_logging()
log = logging.getLogger("agent.main")

settings = load_settings()
agent = ReactAgent(
    llm=LLMClient(settings),
    openshift_mcp=OpenShiftMcpClient(settings),
    aap_mcp=AapMcpClient(settings),
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


def _process_run(run_id: str, user_message: str) -> None:
    try:
        response = agent.run(user_message)
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
    user_message = str(data.get("message") or "").strip()
    if not user_message:
        return jsonify({"error": "message is required"}), 400

    run_id = str(uuid.uuid4())
    with runs_lock:
        runs[run_id] = {"status": "running", "thoughts": [], "response": None}

    log.info("POST /message run_id=%s chars=%s", run_id, len(user_message))
    threading.Thread(
        target=_process_run,
        args=(run_id, user_message),
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
