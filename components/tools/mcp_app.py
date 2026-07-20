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
    {
        "name": "create_vm",
        "description": (
            "Run the AAP create_vm workflow to provision a virtual machine. "
            "Requires an ITSM ticket id and VM parameters."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "description": "ITSM ticket / incident ref"},
                "name": {"type": "string", "description": "Requested VM hostname"},
                "size": {
                    "type": "string",
                    "enum": ["small", "medium", "large"],
                    "description": "VM size profile",
                },
                "network": {"type": "string", "description": "Network / zone"},
                "environment": {
                    "type": "string",
                    "enum": ["dev", "test", "prod"],
                },
                "owner": {"type": "string", "description": "Owner contact"},
            },
            "required": ["ticket_id", "name", "size", "network", "environment", "owner"],
        },
    },
    {
        "name": "delete_vm",
        "description": (
            "Run the AAP delete_vm workflow to decommission a virtual machine. "
            "Requires an ITSM ticket id and the VM hostname."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "description": "ITSM ticket / incident ref"},
                "hostname": {"type": "string", "description": "VM hostname to delete"},
                "environment": {
                    "type": "string",
                    "enum": ["dev", "test", "prod"],
                },
                "reason": {"type": "string"},
            },
            "required": ["ticket_id", "hostname"],
        },
    },
]


def _fake_ip(name: str) -> str:
    seed = sum(ord(c) for c in name) % 200 + 20
    return f"10.42.{seed % 50}.{seed}"


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

        if name == "create_vm":
            missing = [
                key
                for key in ("ticket_id", "name", "size", "network", "environment", "owner")
                if not body.get(key)
            ]
            if missing:
                return jsonify({"error": "missing_fields", "fields": missing}), 400
            hostname = str(body["name"]).strip().lower().replace(" ", "-")
            return jsonify(
                {
                    "status": "succeeded",
                    "workflow": "create_vm",
                    "ticket_id": body["ticket_id"],
                    "hostname": hostname,
                    "ip": _fake_ip(hostname),
                    "size": body["size"],
                    "network": body["network"],
                    "environment": body["environment"],
                    "owner": body["owner"],
                    "message": f"VM {hostname} provisioned successfully",
                }
            )

        if name == "delete_vm":
            missing = [key for key in ("ticket_id", "hostname") if not body.get(key)]
            if missing:
                return jsonify({"error": "missing_fields", "fields": missing}), 400
            hostname = str(body["hostname"]).strip()
            return jsonify(
                {
                    "status": "succeeded",
                    "workflow": "delete_vm",
                    "ticket_id": body["ticket_id"],
                    "hostname": hostname,
                    "environment": body.get("environment"),
                    "reason": body.get("reason"),
                    "message": f"VM {hostname} deleted successfully",
                }
            )

        return jsonify({"error": f"Unknown tool: {name}"}), 404

    return app
