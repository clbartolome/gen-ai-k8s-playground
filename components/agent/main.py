import os
import threading
import time
from datetime import datetime, timezone

from flask import Flask, jsonify, redirect, render_template, request

app = Flask(__name__)

DELAY_SECONDS = float(os.environ.get("DELAY_SECONDS", "0"))

state_lock = threading.Lock()
last_run: dict = {
    "status": "idle",
    "message": None,
    "response": None,
    "error": None,
    "started_at": None,
    "finished_at": None,
    "duration_ms": None,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def snapshot() -> dict:
    with state_lock:
        return dict(last_run)


@app.get("/")
def root():
    return redirect("/debug")


@app.get("/debug")
def debug_page():
    return render_template("debug.html")


@app.get("/debug/status")
def debug_status():
    resp = jsonify(snapshot())
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@app.post("/message")
def message():
    started_at = utc_now()
    start = time.perf_counter()

    data = request.get_json(silent=True) or {}
    user_message = data.get("message", "")

    with state_lock:
        last_run.update(
            {
                "status": "processing",
                "message": user_message,
                "response": None,
                "error": None,
                "started_at": started_at,
                "finished_at": None,
                "duration_ms": None,
            }
        )

    try:
        if DELAY_SECONDS > 0:
            time.sleep(DELAY_SECONDS)

        response = f"You said: {user_message}"
        duration_ms = round((time.perf_counter() - start) * 1000)

        with state_lock:
            last_run.update(
                {
                    "status": "done",
                    "response": response,
                    "finished_at": utc_now(),
                    "duration_ms": duration_ms,
                }
            )

        return jsonify({"response": response})
    except Exception as exc:
        duration_ms = round((time.perf_counter() - start) * 1000)
        with state_lock:
            last_run.update(
                {
                    "status": "error",
                    "error": str(exc),
                    "finished_at": utc_now(),
                    "duration_ms": duration_ms,
                }
            )
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, threaded=True)
