import os

from flask import Flask, jsonify, render_template, request

from store import EventStore

app = Flask(__name__, template_folder="templates", static_folder="static")
store = EventStore()


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "monitor"})


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/events")
def ingest_event():
    body = request.get_json(silent=True) or {}
    run_id = body.get("run_id")
    event_type = body.get("type")

    if not run_id or not event_type:
        return jsonify({"error": "run_id and type are required"}), 400

    store.add_event(
        run_id=run_id,
        event_type=event_type,
        data=body.get("data"),
        at=body.get("at"),
    )
    return jsonify({"ok": True}), 202


@app.get("/status")
def status():
    resp = jsonify(store.snapshot())
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@app.get("/runs/<run_id>")
def get_run(run_id: str):
    run = store.get_run(run_id)
    if run is None:
        return jsonify({"error": "run not found"}), 404
    return jsonify(run)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "9010"))
    app.run(host="0.0.0.0", port=port, threaded=True)
