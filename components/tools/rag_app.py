import re
from pathlib import Path

from flask import Flask, jsonify, request

from common import DATA_DIR, install_request_logging

DOCS_DIR = DATA_DIR / "docs"


def load_documents() -> list[dict]:
    documents: list[dict] = []
    if not DOCS_DIR.is_dir():
        return documents

    for path in sorted(DOCS_DIR.glob("*.md")):
        content = path.read_text(encoding="utf-8")
        title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else path.stem.replace("-", " ").title()
        stem = path.stem
        tags = sorted(
            {
                word
                for word in re.findall(r"[a-z]{3,}", f"{stem} {title} {content}".lower())
                if word
                not in {
                    "the",
                    "and",
                    "for",
                    "with",
                    "this",
                    "that",
                    "from",
                    "are",
                    "will",
                    "through",
                }
            }
        )
        documents.append(
            {
                "id": stem,
                "title": title,
                "tags": tags[:12],
                "content": content,
            }
        )
    return documents


def score_document(query: str, doc: dict) -> int:
    words = {w.lower() for w in re.findall(r"\w+", query) if len(w) > 2}
    if not words:
        return 0

    haystack = " ".join(
        [doc["title"], " ".join(doc.get("tags", [])), doc["content"]]
    ).lower()
    return sum(1 for word in words if word in haystack)


def excerpt(text: str, max_len: int = 240) -> str:
    one_line = " ".join(text.split())
    if len(one_line) <= max_len:
        return one_line
    return one_line[: max_len - 1].rstrip() + "…"


def create_app() -> Flask:
    app = Flask(__name__)
    install_request_logging(app, "rag")

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "service": "rag"})

    @app.get("/documents")
    def list_documents():
        docs = load_documents()
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
        for doc in load_documents():
            score = score_document(query, doc)
            if score > 0:
                ranked.append(
                    {
                        "id": doc["id"],
                        "title": doc["title"],
                        "score": score,
                        "excerpt": excerpt(doc["content"]),
                        "content": doc["content"],
                    }
                )

        ranked.sort(key=lambda item: item["score"], reverse=True)
        return jsonify({"query": query, "results": ranked[:3]})

    return app
