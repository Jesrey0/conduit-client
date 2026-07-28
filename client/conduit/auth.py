#!/usr/bin/env python3
"""Canonical, permission-checked bearer-token state for Conduit clients."""
from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path
from typing import Any

from .atomic_file import write_json_atomic

DEFAULT_AUTH_FILENAME = ".conduit_auth.json"
AUTH_SCHEMA_VERSION = 1


def default_auth_path() -> Path:
    configured = os.getenv("CONDUIT_AUTH_PATH") or str(Path.home() / DEFAULT_AUTH_FILENAME)
    return Path(configured)


class ConduitAuthState:
    def __init__(self, auth_path: Path | str | None = None):
        self.auth_path = Path(auth_path) if auth_path else default_auth_path()

    def _read(self) -> dict[str, Any] | None:
        if not self.auth_path.exists():
            return None
        info = self.auth_path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise PermissionError(f"Conduit auth path is not a regular file: {self.auth_path}")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise PermissionError(f"Conduit auth file is not owned by the current user: {self.auth_path}")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise PermissionError(f"Conduit auth file must have mode 0600: {self.auth_path}")
        data = json.loads(self.auth_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schemaVersion") != AUTH_SCHEMA_VERSION:
            raise ValueError("Unsupported Conduit auth schema; re-enroll this client")
        return data

    def load_token(self) -> str | None:
        env_token = os.getenv("CONDUIT_CLIENT_TOKEN")
        if env_token and env_token.strip():
            return env_token.strip()
        data = self._read()
        token = data.get("clientToken") if data else None
        return token if isinstance(token, str) and token else None

    def load_server_url(self) -> str | None:
        data = self._read()
        value = data.get("serverUrl") if data else None
        return value if isinstance(value, str) and value else None

    def load_saved_at(self) -> float | None:
        data = self._read()
        value = data.get("savedAt") if data else None
        return float(value) if isinstance(value, (int, float)) else None

    def token_age_days(self) -> float | None:
        saved_at = self.load_saved_at()
        return None if saved_at is None else max(0.0, (time.time() - saved_at) / 86400.0)

    def save_token(self, token: str, *, server_url: str | None = None) -> bool:
        payload: dict[str, Any] = {
            "schemaVersion": AUTH_SCHEMA_VERSION,
            "clientToken": token,
            "savedAt": time.time(),
        }
        if server_url:
            payload["serverUrl"] = server_url
        write_json_atomic(self.auth_path, payload, mode=0o600)
        return True

    def clear(self) -> None:
        self.auth_path.unlink(missing_ok=True)
