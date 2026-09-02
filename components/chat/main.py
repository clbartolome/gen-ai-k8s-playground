import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

AGENT_URL = os.environ.get("AGENT_URL", "http://localhost:8080")
AGENT_TIMEOUT = float(os.environ.get("AGENT_TIMEOUT", "120"))

jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()

pending_info: dict[str, list[dict]] = {}
pending_info_lock = threading.Lock()

pending_incidents: list[dict] = []
pending_incidents_lock = threading.Lock()


def _agent_json(method: str, path: str, body: dict | None = None) -> dict:
    data = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{AGENT_URL.rstrip('/')}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=AGENT_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def process_job(
    job_id: str,
    message: str,
    thread_id: str | None = None,
    category: str | None = None,
    source: str | None = None,
    incident: dict | None = None,
) -> None:
    try:
        body: dict = {"message": message}
        if thread_id:
            body["thread_id"] = thread_id
        if category:
            body["category"] = category
        if source:
            body["source"] = source
        if incident:
            body["incident"] = incident

        start = _agent_json("POST", "/message", body)
        run_id = start["run_id"]
        resolved_thread_id = start.get("thread_id") or thread_id

        while True:
            run = _agent_json("GET", f"/runs/{run_id}")
            thoughts = run.get("thoughts") or []
            with jobs_lock:
                job = jobs.get(job_id)
                if job is not None:
                    job["thoughts"] = thoughts
                    job["status"] = "pending"
                    if resolved_thread_id:
                        job["thread_id"] = resolved_thread_id

            status = run.get("status")
            if status == "done":
                with jobs_lock:
                    jobs[job_id] = {
                        "status": "done",
                        "response": run.get("response", ""),
                        "thoughts": thoughts,
                        "thread_id": run.get("thread_id") or resolved_thread_id,
                        "pending": run.get("pending"),
                    }
                return
            if status == "error":
                with jobs_lock:
                    jobs[job_id] = {
                        "status": "error",
                        "error": run.get("error", "agent error"),
                        "thoughts": thoughts,
                        "thread_id": run.get("thread_id") or resolved_thread_id,
                    }
                return
            threading.Event().wait(0.4)
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError, RuntimeError) as exc:
        with jobs_lock:
            jobs[job_id] = {"status": "error", "error": str(exc), "thoughts": []}


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/ask")
def ask():
    data = request.get_json(silent=True) or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    thread_id = data.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id.strip():
        thread_id = None
    else:
        thread_id = thread_id.strip()

    category = data.get("category")
    if not isinstance(category, str) or not category.strip():
        category = None
    else:
        category = category.strip().upper()

    source = data.get("source")
    if not isinstance(source, str) or not source.strip():
        source = None
    else:
        source = source.strip().lower()

    incident = data.get("incident")
    if not isinstance(incident, dict):
        incident = None

    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {
            "status": "pending",
            "thoughts": [],
            "thread_id": thread_id,
        }

    threading.Thread(
        target=process_job,
        args=(job_id, message, thread_id, category, source, incident),
        daemon=True,
    ).start()
    return jsonify({"job_id": job_id, "thread_id": thread_id})


@app.get("/jobs/<job_id>")
def job_status(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        return jsonify({"error": "job not found"}), 404
    return jsonify(job)


@app.post("/threads/<thread_id>/info")
def post_thread_info(thread_id: str):
    data = request.get_json(silent=True) or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    thread_id = thread_id.strip()
    if not thread_id:
        return jsonify({"error": "thread_id is required"}), 400

    entry = {
        "id": str(uuid.uuid4()),
        "thread_id": thread_id,
        "message": message,
        "created_at": int(time.time() * 1000),
    }

    with pending_info_lock:
        pending_info.setdefault(thread_id, []).append(entry)

    return jsonify(entry), 201


@app.get("/threads/info")
def poll_thread_info():
    raw_ids = request.args.get("ids", "")
    thread_ids = [item.strip() for item in raw_ids.split(",") if item.strip()]
    if not thread_ids:
        return jsonify({"messages": []})

    messages: list[dict] = []
    with pending_info_lock:
        for thread_id in thread_ids:
            queued = pending_info.pop(thread_id, [])
            messages.extend(queued)

    return jsonify({"messages": messages})


@app.post("/incidents")
def post_incident():
    data = request.get_json(silent=True) or {}
    title = data.get("title", "").strip()
    message = data.get("message", "").strip()
    severity = data.get("severity", "").strip()

    if not message and not title:
        return jsonify({"error": "title or message is required"}), 400

    entry = {
        "id": str(uuid.uuid4()),
        "title": title or "Incident",
        "message": message,
        "severity": severity or "unknown",
        "created_at": int(time.time() * 1000),
    }

    with pending_incidents_lock:
        pending_incidents.append(entry)

    return jsonify(entry), 201


@app.get("/incidents")
def poll_incidents():
    with pending_incidents_lock:
        incidents = list(pending_incidents)
        pending_incidents.clear()
    return jsonify({"incidents": incidents})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, threaded=True)
