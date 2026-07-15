from flask import Flask, jsonify, request

from common import get_tickets, install_request_logging

VALID_STATUSES = {"open", "in_progress", "resolved", "scheduled"}


def create_app() -> Flask:
    app = Flask(__name__)
    install_request_logging(app, "itsm")

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "service": "itsm"})

    @app.get("/tickets")
    def list_tickets():
        tickets = get_tickets()
        component = request.args.get("component")
        status = request.args.get("status")
        severity = request.args.get("severity")

        if component:
            tickets = [t for t in tickets if t["component"] == component]
        if status:
            tickets = [t for t in tickets if t["status"] == status]
        if severity:
            tickets = [t for t in tickets if t["severity"] == severity]

        return jsonify({"tickets": tickets})

    @app.get("/tickets/<ticket_id>")
    def get_ticket(ticket_id: str):
        for ticket in get_tickets():
            if ticket["id"] == ticket_id:
                return jsonify(ticket)
        return jsonify({"error": f"Ticket not found: {ticket_id}"}), 404

    @app.post("/tickets")
    def create_ticket():
        body = request.get_json(silent=True) or {}
        title = body.get("title", "").strip()
        if not title:
            return jsonify({"error": "title is required"}), 400

        tickets = get_tickets()
        ticket_id = f"INC-{1000 + len(tickets)}"
        ticket = {
            "id": ticket_id,
            "title": title,
            "component": body.get("component", "chat"),
            "severity": body.get("severity", "medium"),
            "status": "open",
            "description": body.get("description", ""),
        }
        tickets.append(ticket)
        return jsonify(ticket), 201

    @app.patch("/tickets/<ticket_id>")
    def update_ticket(ticket_id: str):
        body = request.get_json(silent=True) or {}
        status = body.get("status")
        if status and status not in VALID_STATUSES:
            return jsonify({"error": f"Invalid status: {status}"}), 400

        for ticket in get_tickets():
            if ticket["id"] == ticket_id:
                if status:
                    ticket["status"] = status
                if "description" in body:
                    ticket["description"] = body["description"]
                return jsonify(ticket)

        return jsonify({"error": f"Ticket not found: {ticket_id}"}), 404

    return app
