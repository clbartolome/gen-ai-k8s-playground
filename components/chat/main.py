import json
import os
import threading
import urllib.error
import urllib.request
import uuid

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

AGENT_URL = os.environ.get("AGENT_URL", "http://localhost:8080")
AGENT_TIMEOUT = float(os.environ.get("AGENT_TIMEOUT", "120"))

jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


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


def process_job(job_id: str, message: str, history: list | None = None) -> None:
    try:
        start = _agent_json(
            "POST",
            "/message",
            {"message": message, "history": history or []},
        )
        run_id = start["run_id"]
        while True:
            run = _agent_json("GET", f"/runs/{run_id}")
            thoughts = run.get("thoughts") or []
            with jobs_lock:
                job = jobs.get(job_id)
                if job is not None:
                    job["thoughts"] = thoughts
                    job["status"] = "pending"

            status = run.get("status")
            if status == "done":
                with jobs_lock:
                    jobs[job_id] = {
                        "status": "done",
                        "response": run.get("response", ""),
                        "thoughts": thoughts,
                    }
                return
            if status == "error":
                with jobs_lock:
                    jobs[job_id] = {
                        "status": "error",
                        "error": run.get("error", "agent error"),
                        "thoughts": thoughts,
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

    history = data.get("history") or []
    if not isinstance(history, list):
        history = []

    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {"status": "pending", "thoughts": []}

    threading.Thread(
        target=process_job, args=(job_id, message, history), daemon=True
    ).start()
    return jsonify({"job_id": job_id})


@app.get("/jobs/<job_id>")
def job_status(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        return jsonify({"error": "job not found"}), 404
    return jsonify(job)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, threaded=True)
