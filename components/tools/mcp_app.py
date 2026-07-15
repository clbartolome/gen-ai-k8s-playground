from flask import Flask, jsonify, request

from common import install_request_logging, load_scenario

TOOLS = [
    {
        "name": "get_service_health",
        "description": "Current health and metrics for a playground component (chat, agent, tools).",
        "parameters": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "enum": ["chat", "agent", "tools"],
                }
            },
            "required": ["service"],
        },
    },
    {
        "name": "get_recent_events",
        "description": "Recent platform events, optionally filtered by component.",
        "parameters": {
            "type": "object",
            "properties": {
                "component": {
                    "type": "string",
                    "enum": ["chat", "agent", "tools"],
                }
            },
        },
    },
    {
        "name": "list_active_alerts",
        "description": "Active alerts for playground components.",
        "parameters": {
            "type": "object",
            "properties": {
                "component": {
                    "type": "string",
                    "enum": ["chat", "agent", "tools"],
                }
            },
        },
    },
]


def create_app() -> Flask:
    app = Flask(__name__)
    install_request_logging(app, "mcp")

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "service": "mcp"})

    @app.get("/tools")
    def list_tools():
        return jsonify({"tools": TOOLS})

    @app.post("/tools/<name>/invoke")
    def invoke_tool(name: str):
        body = request.get_json(silent=True) or {}
        scenario = load_scenario()

        if name == "get_service_health":
            service = body.get("service")
            if service not in scenario["services"]:
                return jsonify({"error": f"Unknown service: {service}"}), 400
            return jsonify({"service": service, **scenario["services"][service]})

        if name == "get_recent_events":
            events = scenario["events"]
            component = body.get("component")
            if component:
                events = [e for e in events if e["component"] == component]
            return jsonify({"events": events})

        if name == "list_active_alerts":
            alerts = scenario["alerts"]
            component = body.get("component")
            if component:
                alerts = [a for a in alerts if a["component"] == component]
            return jsonify({"alerts": alerts})

        return jsonify({"error": f"Unknown tool: {name}"}), 404

    return app
