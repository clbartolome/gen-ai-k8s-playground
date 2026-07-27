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


def process_job(job_id: str, message: str, thread_id: str | None = None) -> None:
    try:
        body: dict = {"message": message}
        if thread_id:
            body["thread_id"] = thread_id

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

    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {
            "status": "pending",
            "thoughts": [],
            "thread_id": thread_id,
        }

    threading.Thread(
        target=process_job, args=(job_id, message, thread_id), daemon=True
    ).start()
    return jsonify({"job_id": job_id, "thread_id": thread_id})


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
