"""Session-free machine-readable readiness probe for the optional CLI bridge."""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from typing import Any

from .auth import ConduitAuthState


def _health(url: str) -> dict[str, Any]:
    base = url[:-4] if url.rstrip("/").endswith("/mcp") else url
    health_url = f"{base.rstrip('/')}/health"
    request = urllib.request.Request(health_url, headers={
        "User-Agent": "conduit-doctor/3.0.0",
        "ngrok-skip-browser-warning": "1",
    })
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
        if isinstance(payload, dict):
            return {
                "ok": payload.get("status") == "ok",
                "clientAdmissionEnabled": payload.get("clientAdmissionEnabled"),
            }
        return {"ok": False}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "statusCode": exc.code}
    except Exception as exc:  # pragma: no cover - host network dependent
        return {"ok": False, "error": str(exc)}


async def doctor_result(url: str | None) -> dict[str, Any]:
    """Return compact readiness data without opening an MCP session."""
    try:
        import httpx
        httpx_check: dict[str, Any] = {"ok": True, "version": getattr(httpx, "__version__", None)}
    except Exception as exc:  # pragma: no cover - host dependency dependent
        httpx_check = {"ok": False, "error": str(exc)}

    server = await asyncio.to_thread(_health, url) if url else {"ok": False, "error": "CONDUIT_URL is unset"}
    auth_state = ConduitAuthState()
    has_token = bool(os.getenv("CONDUIT_CLIENT_TOKEN") or os.getenv("CONDUIT_TOKEN") or auth_state.load_token())
    admission = server.get("clientAdmissionEnabled")
    auth = {"ok": admission is not True or has_token, "required": admission is True, "present": has_token}
    checks = {"httpx": httpx_check, "server": server, "auth": auth}
    return {"ready": all(check["ok"] for check in checks.values()), "checks": checks}
