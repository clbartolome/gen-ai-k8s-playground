import re

from flask import Flask, jsonify, request

from common import install_request_logging, load_scenario


def score_document(query: str, doc: dict) -> int:
    words = {w.lower() for w in re.findall(r"\w+", query) if len(w) > 2}
    if not words:
        return 0

    haystack = " ".join(
        [doc["title"], " ".join(doc.get("tags", [])), doc["content"]]
    ).lower()
    return sum(1 for word in words if word in haystack)


def create_app() -> Flask:
    app = Flask(__name__)
    install_request_logging(app, "rag")

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "service": "rag"})

    @app.get("/documents")
    def list_documents():
        docs = load_scenario()["documents"]
        return jsonify(
            {
                "documents": [
                    {
                        "id": doc["id"],
                        "title": doc["title"],
                        "tags": doc.get("tags", []),
                    }
                    for doc in docs
                ]
            }
        )

    @app.post("/search")
    def search():
        body = request.get_json(silent=True) or {}
        query = body.get("query", "").strip()
        if not query:
            return jsonify({"error": "query is required"}), 400

        ranked = []
        for doc in load_scenario()["documents"]:
            score = score_document(query, doc)
            if score > 0:
                ranked.append(
                    {
                        "id": doc["id"],
                        "title": doc["title"],
                        "score": score,
                        "excerpt": doc["content"][:240],
                    }
                )

        ranked.sort(key=lambda item: item["score"], reverse=True)
        return jsonify({"query": query, "results": ranked[:3]})

    return app
