"""
client/conduit/errors.py — Conduit Error Hierarchy
"""

from typing import Any, Optional


class ConduitError(Exception):
    """Base class for all Conduit errors."""
    pass


class TransportError(ConduitError):
    """Errors related to the transport layer (e.g., connection failures)."""
    def __init__(self, message: str, cause: Optional[Exception] = None):
        super().__init__(message)
        self.cause = cause


class ProtocolError(ConduitError):
    """Errors related to the MCP protocol or JSON-RPC envelope."""
    def __init__(self, message: str, status_code: Optional[int] = None, body: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class SessionError(ConduitError):
    """Errors related to session management (e.g., invalid or missing session)."""
    pass


class AuthError(ConduitError):
    """Server rejected the request for authentication reasons (JSON-RPC -32001).

    Carries the server's machine-readable ``reason`` plus an actionable
    remediation hint, so operators immediately know which knob to turn
    instead of staring at a bare 'Unauthorized' string.
    """

    def __init__(
        self,
        message: str,
        *,
        reason: Optional[str] = None,
        status_code: Optional[int] = None,
        body: Optional[str] = None,
    ):
        super().__init__(message)
        self.reason = reason
        self.status_code = status_code
        self.body = body

    @classmethod
    def from_rpc(
        cls,
        server_message: str,
        *,
        reason: Optional[str],
        status_code: Optional[int] = None,
        body: Optional[str] = None,
    ) -> "AuthError":
        if reason == "AUTH_REQUIRED":
            hint = (
                "The server requires an approved client token, but this client sent none. "
                "Run the bootstrap to enroll, or export CONDUIT_CLIENT_TOKEN "
                "(token file default: ~/.conduit_auth.json, override with CONDUIT_AUTH_PATH)."
            )
        elif reason == "SESSION_CLIENT_MISMATCH":
            hint = "This session belongs to a different approved client. Start a new Conduit instance without CONDUIT_SESSION_ID."
        elif reason in ("INVALID_CLIENT_TOKEN", None):
            hint = (
                "The client token was rejected. Rotate/re-enroll via the admin CLI, "
                "or export a valid CONDUIT_CLIENT_TOKEN."
            )
        else:
            hint = f"Authentication failed ({reason}). Check the client token and enrollment status."
        return cls(f"{server_message} — {hint}", reason=reason, status_code=status_code, body=body)


class ToolError(ConduitError):
    """Errors returned by tools during execution."""
    def __init__(self, message: str, code: int, data: Optional[Any] = None):
        super().__init__(message)
        self.code = code
        self.data = data
