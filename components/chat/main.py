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


def call_agent(message: str) -> str:
    payload = json.dumps({"message": message}).encode("utf-8")
    req = urllib.request.Request(
        f"{AGENT_URL.rstrip('/')}/message",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=AGENT_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["response"]


def process_job(job_id: str, message: str) -> None:
    try:
        response = call_agent(message)
        with jobs_lock:
            jobs[job_id] = {"status": "done", "response": response}
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError) as exc:
        with jobs_lock:
            jobs[job_id] = {"status": "error", "error": str(exc)}


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/ask")
def ask():
    data = request.get_json(silent=True) or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {"status": "pending"}

    threading.Thread(target=process_job, args=(job_id, message), daemon=True).start()
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
