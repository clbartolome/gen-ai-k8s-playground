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
from orchestrator import AgentOrchestrator
from threads import ThreadStore

setup_logging()
log = logging.getLogger("agent.main")

settings = load_settings()
log.info(
    "Agent settings llm_model=%s openshift_mcp_url=%s aap_mcp_url=%s itsm_mcp_url=%s",
    settings.llm_model or "(empty)",
    settings.openshift_mcp_url or "(empty)",
    settings.aap_mcp_url or "(empty)",
    settings.itsm_mcp_url or "(empty)",
)

orchestrator = AgentOrchestrator(
    llm=LLMClient(settings),
    openshift_mcp=OpenShiftMcpClient(settings),
    aap_mcp=AapMcpClient(settings),
    itsm_mcp=ItsmMcpClient(settings),
)
thread_store = ThreadStore()

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


def _process_run(run_id: str, thread_id: str, user_message: str) -> None:
    try:
        _, thread = thread_store.get_or_create(thread_id)
        turn = orchestrator.run(
            user_message,
            dialogue=thread.get("dialogue") or [],
            trace=thread.get("trace") or [],
            pending=thread.get("pending"),
            on_thought=lambda text: _append_thought(run_id, text),
        )
        updated = thread_store.commit_turn(
            thread_id,
            user_message=user_message,
            assistant_message=turn.response,
            action=turn.action,
            pending=turn.pending,
            trace_entry=turn.trace_entry,
        )
        _update_run(
            run_id,
            status="done",
            response=turn.response,
            thread_id=thread_id,
            pending=updated.get("pending"),
        )
    except Exception as exc:
        log.exception("Run %s failed: %s", run_id, exc)
        _update_run(run_id, status="error", error=str(exc), thread_id=thread_id)


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "agent"})


@app.post("/message")
def message():
    data = request.get_json(silent=True) or {}
    user_message = str(data.get("message") or "").strip()
    if not user_message:
        return jsonify({"error": "message is required"}), 400

    requested_thread_id = data.get("thread_id")
    if requested_thread_id is not None and not isinstance(requested_thread_id, str):
        requested_thread_id = None
    requested_thread_id = (requested_thread_id or "").strip() or None

    thread_id, _ = thread_store.get_or_create(requested_thread_id)

    run_id = str(uuid.uuid4())
    with runs_lock:
        runs[run_id] = {
            "status": "running",
            "thoughts": [],
            "response": None,
            "thread_id": thread_id,
        }

    log.info(
        "POST /message run_id=%s thread_id=%s chars=%s",
        run_id,
        thread_id,
        len(user_message),
    )
    threading.Thread(
        target=_process_run,
        args=(run_id, thread_id, user_message),
        daemon=True,
    ).start()
    return jsonify({"run_id": run_id, "thread_id": thread_id})


@app.get("/runs/<run_id>")
def get_run(run_id: str):
    with runs_lock:
        run = runs.get(run_id)
        if run is None:
            return jsonify({"error": "run not found"}), 404
        return jsonify(run)


@app.get("/threads/<thread_id>")
def get_thread(thread_id: str):
    thread = thread_store.snapshot(thread_id)
    if thread is None:
        return jsonify({"error": "thread not found"}), 404
    return jsonify({"thread_id": thread_id, **thread})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    log.info("Starting agent on :%s", port)
    app.run(host="0.0.0.0", port=port, threaded=True)
