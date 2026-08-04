"""Serve the process monitor SPA and proxy read-only trace APIs to the agent."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import Flask, Response, jsonify, request, send_from_directory

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("monitor.main")

AGENT_URL = os.environ.get("AGENT_URL", "http://localhost:8080").rstrip("/")
AGENT_TIMEOUT = float(os.environ.get("AGENT_TIMEOUT", "30"))
STATIC_DIR = Path(__file__).resolve().parent / "frontend" / "dist"

app = Flask(__name__, static_folder=None)


def _proxy(path: str) -> Response:
    url = f"{AGENT_URL}{path}"
    if request.query_string:
        url = f"{url}?{request.query_string.decode()}"
    req = Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=AGENT_TIMEOUT) as resp:
            body = resp.read()
            return Response(
                body,
                status=resp.status,
                content_type=resp.headers.get("Content-Type", "application/json"),
            )
    except HTTPError as exc:
        body = exc.read() if exc.fp else b"{}"
        return Response(
            body,
            status=exc.code,
            content_type="application/json",
        )
    except URLError as exc:
        log.warning("Agent proxy failed url=%s err=%s", url, exc)
        return jsonify({"error": f"agent unreachable: {exc.reason}"}), 502


@app.get("/api/traces")
def api_list_traces():
    return _proxy("/traces")


@app.get("/api/traces/<thread_id>")
def api_get_trace(thread_id: str):
    return _proxy(f"/traces/{thread_id}")


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "monitor"})


@app.get("/")
def index():
    index_path = STATIC_DIR / "index.html"
    if not index_path.is_file():
        return (
            jsonify(
                {
                    "error": "frontend not built",
                    "hint": "Run npm run build in components/monitor/frontend",
                }
            ),
            503,
        )
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/<path:asset>")
def spa_assets(asset: str):
    target = STATIC_DIR / asset
    if target.is_file():
        return send_from_directory(STATIC_DIR, asset)
    index_path = STATIC_DIR / "index.html"
    if index_path.is_file():
        return send_from_directory(STATIC_DIR, "index.html")
    return jsonify({"error": "not found"}), 404


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5100"))
    log.info("Starting monitor on :%s agent=%s", port, AGENT_URL)
    app.run(host="0.0.0.0", port=port, threaded=True)
