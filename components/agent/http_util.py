import json
import os
import ssl
import urllib.error
import urllib.request
from typing import Any

_UNVERIFIED_SSL: ssl.SSLContext | None = None


def ssl_context_for(url: str) -> ssl.SSLContext | None:
    """Return an SSL context for HTTPS. Verification off unless SSL_VERIFY=true."""
    if not url.lower().startswith("https://"):
        return None
    verify = os.environ.get("SSL_VERIFY", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if verify:
        return None  # urllib default verification
    global _UNVERIFIED_SSL
    if _UNVERIFIED_SSL is None:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        _UNVERIFIED_SSL = ctx
    return _UNVERIFIED_SSL


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
    context = ssl_context_for(url)

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Request failed ({exc.code}) {method} {url}: {detail}") from exc
