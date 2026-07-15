import json
import urllib.error
import urllib.request
from typing import Any


def request_json(
    method: str,
    url: str,
    *,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30,
) -> Any:
    payload = None
    req_headers = dict(headers or {})
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=payload, headers=req_headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Request failed ({exc.code}) {method} {url}: {detail}") from exc
