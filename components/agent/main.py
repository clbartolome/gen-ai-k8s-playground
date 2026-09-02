import logging
import os
import threading
import uuid

from flask import Flask, jsonify, request

from aap_mcp import AapMcpClient, set_aap_thread_context
from config import load_settings
from itsm_mcp import ItsmMcpClient
from llm import LLMClient
from logutil import setup_logging
from openshift_mcp import OpenShiftMcpClient
from react import ReactAgent
from threads import ThreadStore
from trace import TraceBuilder
from trace_store import TraceStore

setup_logging()
log = logging.getLogger("agent.main")

settings = load_settings()
agent = ReactAgent(
    llm=LLMClient(settings),
    openshift_mcp=OpenShiftMcpClient(settings),
    aap_mcp=AapMcpClient(settings),
    itsm_mcp=ItsmMcpClient(settings),
)
thread_store = ThreadStore()
trace_store = TraceStore(os.environ.get("TRACE_DB_PATH", "/tmp/agent-traces.db"))

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


def _incident_trace_label(incident: dict | None, fallback_message: str) -> str:
    if isinstance(incident, dict):
        title = str(incident.get("title") or "").strip()
        if title:
            return title
    return "Incident alert"


def _process_run(
    run_id: str,
    thread_id: str,
    user_message: str,
    *,
    forced_category: str | None = None,
    source: str | None = None,
    incident: dict | None = None,
) -> None:
    set_aap_thread_context(thread_id)
    try:
        _execute_run(
            run_id,
            thread_id,
            user_message,
            forced_category=forced_category,
            source=source,
            incident=incident,
        )
    finally:
        set_aap_thread_context(None)


def _execute_run(
    run_id: str,
    thread_id: str,
    user_message: str,
    *,
    forced_category: str | None = None,
    source: str | None = None,
    incident: dict | None = None,
) -> None:
    existing = trace_store.get_trace(thread_id)
    _, thread = thread_store.get_or_create(thread_id)
    pending = thread.get("pending")
    continuing = isinstance(pending, dict) and (
        pending.get("kind") == "rag_action" or bool(pending.get("question"))
    )

    trace = TraceBuilder.for_thread(
        thread_id=thread_id,
        run_id=run_id,
        existing=existing,
    )
    # First ask of a thread (or a new ask after the previous turn finished).
    # Clarifying replies are recorded as user_input inside ReactAgent.
    if not continuing:
        if source == "incident":
            incident_detail: dict = {"message": user_message}
            if isinstance(incident, dict):
                incident_detail = {
                    "title": incident.get("title"),
                    "message": incident.get("message") or user_message,
                    "severity": incident.get("severity"),
                }
            trace.add(
                "incident",
                _incident_trace_label(incident, user_message),
                detail=incident_detail,
            )
        else:
            trace.add(
                "user_message",
                "User message",
                detail={"message": user_message},
            )

    root_message = trace.root_message or user_message
    trace_store.upsert(
        thread_id=thread_id,
        run_id=run_id,
        status="running",
        user_message=root_message,
        preview=trace.preview(),
        category=(existing or {}).get("category") if existing else None,
        nodes=trace.nodes,
    )
    try:
        turn = agent.run(
            user_message,
            dialogue=thread.get("dialogue") or [],
            pending=pending,
            last_category=thread.get("last_category"),
            forced_category=forced_category,
            on_thought=lambda text: _append_thought(run_id, text),
            trace=trace,
        )
        thread_store.commit_turn(
            thread_id,
            user_message=user_message,
            assistant_message=turn.response,
            action=turn.action,
            pending=turn.pending,
            last_category=turn.category,
        )
        status = (
            "pending"
            if turn.action == "request_information"
            or (
                isinstance(turn.pending, dict)
                and turn.pending.get("kind") == "rag_action"
            )
            else "done"
        )
        trace_store.upsert(
            thread_id=thread_id,
            run_id=run_id,
            status=status,
            user_message=trace.root_message or user_message,
            preview=trace.preview(turn.category),
            category=turn.category,
            response=turn.response,
            nodes=trace.nodes,
        )
        _update_run(
            run_id,
            status="done",
            response=turn.response,
            thread_id=thread_id,
            pending=turn.pending,
            category=turn.category,
        )
    except Exception as exc:
        log.exception("Run %s failed: %s", run_id, exc)
        trace.add(
            "error",
            "Run failed",
            status="error",
            detail={"error": str(exc)},
        )
        trace_store.upsert(
            thread_id=thread_id,
            run_id=run_id,
            status="error",
            user_message=trace.root_message or user_message,
            preview=trace.preview(),
            response=str(exc),
            nodes=trace.nodes,
        )
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

    forced_category = data.get("category")
    if not isinstance(forced_category, str) or not forced_category.strip():
        forced_category = None
    else:
        forced_category = forced_category.strip().upper()

    source = data.get("source")
    if not isinstance(source, str) or not source.strip():
        source = None
    else:
        source = source.strip().lower()

    incident = data.get("incident")
    if not isinstance(incident, dict):
        incident = None

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
        kwargs={
            "forced_category": forced_category,
            "source": source,
            "incident": incident,
        },
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


@app.get("/traces")
def list_traces():
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        limit = 50
    try:
        offset = int(request.args.get("offset", 0))
    except ValueError:
        offset = 0
    return jsonify({"traces": trace_store.list_traces(limit=limit, offset=offset)})


@app.get("/traces/<thread_id>")
def get_trace(thread_id: str):
    trace = trace_store.get_trace(thread_id)
    if trace is None:
        return jsonify({"error": "trace not found"}), 404
    return jsonify(trace)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    log.info("Starting agent on :%s", port)
    app.run(host="0.0.0.0", port=port, threaded=True)
