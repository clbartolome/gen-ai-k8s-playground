import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from flask import Flask, jsonify, redirect, render_template, request

app = Flask(__name__)

DELAY_SECONDS = float(os.environ.get("DELAY_SECONDS", "0"))
LLM_URL = os.environ.get("LLM_URL", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "120"))

state_lock = threading.Lock()
last_run: dict = {
    "status": "idle",
    "step": None,
    "message": None,
    "response": None,
    "error": None,
    "started_at": None,
    "finished_at": None,
    "duration_ms": None,
    "llm_status": "idle",
    "llm_started_at": None,
    "llm_finished_at": None,
    "llm_duration_ms": None,
    "message_chars": None,
    "response_chars": None,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def snapshot() -> dict:
    with state_lock:
        data = dict(last_run)
    data["llm_model"] = LLM_MODEL or None
    data["llm_endpoint"] = chat_completions_url(LLM_URL) if LLM_URL else None
    data["llm_timeout_s"] = LLM_TIMEOUT
    return data


def chat_completions_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def call_llm(message: str) -> str:
    if not LLM_URL or not LLM_API_KEY or not LLM_MODEL:
        raise RuntimeError("LLM_URL, LLM_API_KEY and LLM_MODEL must be set")

    payload = json.dumps(
        {
            "model": LLM_MODEL,
            "messages": [{"role": "user", "content": message}],
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        chat_completions_url(LLM_URL),
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LLM_API_KEY}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM request failed ({exc.code}): {body}") from exc

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected LLM response: {data}") from exc


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
                "step": "received",
                "message": user_message,
                "response": None,
                "error": None,
                "started_at": started_at,
                "finished_at": None,
                "duration_ms": None,
                "llm_status": "idle",
                "llm_started_at": None,
                "llm_finished_at": None,
                "llm_duration_ms": None,
                "message_chars": len(user_message),
                "response_chars": None,
            }
        )

    try:
        if DELAY_SECONDS > 0:
            with state_lock:
                last_run["step"] = "delay"
            time.sleep(DELAY_SECONDS)

        llm_started_at = utc_now()
        llm_start = time.perf_counter()
        with state_lock:
            last_run.update(
                {
                    "step": "calling_llm",
                    "llm_status": "calling",
                    "llm_started_at": llm_started_at,
                    "llm_finished_at": None,
                    "llm_duration_ms": None,
                }
            )

        response = call_llm(user_message)
        llm_duration_ms = round((time.perf_counter() - llm_start) * 1000)
        duration_ms = round((time.perf_counter() - start) * 1000)

        with state_lock:
            last_run.update(
                {
                    "status": "done",
                    "step": None,
                    "response": response,
                    "finished_at": utc_now(),
                    "duration_ms": duration_ms,
                    "llm_status": "success",
                    "llm_finished_at": utc_now(),
                    "llm_duration_ms": llm_duration_ms,
                    "response_chars": len(response),
                }
            )

        return jsonify({"response": response})
    except Exception as exc:
        duration_ms = round((time.perf_counter() - start) * 1000)
        with state_lock:
            updates = {
                "status": "error",
                "error": str(exc),
                "finished_at": utc_now(),
                "duration_ms": duration_ms,
            }
            if last_run.get("llm_status") == "calling":
                updates.update(
                    {
                        "llm_status": "failed",
                        "llm_finished_at": utc_now(),
                        "llm_duration_ms": round(
                            (time.perf_counter() - llm_start) * 1000
                        )
                        if "llm_start" in locals()
                        else None,
                    }
                )
            last_run.update(updates)
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, threaded=True)
