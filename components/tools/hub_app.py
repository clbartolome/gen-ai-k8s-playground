from flask import Flask, jsonify, render_template

from common import activity_snapshot, port

HUB_PORT = port("hub", 9000)
MCP_PORT = port("mcp", 9001)


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "service": "tools-hub"})

    @app.get("/")
    def hub_page():
        return render_template(
            "hub.html",
            mcp_port=MCP_PORT,
        )

    @app.get("/status")
    def hub_status():
        resp = jsonify(
            {
                "ports": {
                    "hub": HUB_PORT,
                    "mcp": MCP_PORT,
                },
                "activity": activity_snapshot(),
            }
        )
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return resp

    return app
