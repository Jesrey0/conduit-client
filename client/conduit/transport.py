#!/usr/bin/env python3
"""Streamable HTTP MCP transport used by Conduit Local Python clients."""

from __future__ import annotations

import asyncio
import json
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

import httpx

from .errors import AuthError, ProtocolError, SessionError, TransportError

RECOVERABLE_SESSION_STATUSES = {400, 404, 409, 410}
SUPPORTED_PROTOCOL_VERSION = "2025-11-25"

def _load_tool_retry_policy() -> dict[str, str]:
    path = Path(__file__).with_name("tool-policy.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schemaVersion") != 1 or not isinstance(data.get("tools"), dict):
        raise RuntimeError(f"Invalid Conduit client tool policy: {path}")
    return {name: entry["retry"] for name, entry in data["tools"].items()}


TOOL_RETRY_POLICY = _load_tool_retry_policy()
RETRY_SAFE_TOOLS = frozenset(name for name, retry in TOOL_RETRY_POLICY.items() if retry == "read-safe")
IDEMPOTENCY_AWARE_TOOLS = frozenset(name for name, retry in TOOL_RETRY_POLICY.items() if retry == "idempotency-key")


def _is_retry_safe(method: str, params: dict | None) -> bool:
    if method != "tools/call":
        return True
    params = params or {}
    name = params.get("name")
    retry = TOOL_RETRY_POLICY.get(name)
    if retry == "read-safe":
        return True
    arguments = params.get("arguments") or {}
    return retry == "idempotency-key" and bool(arguments.get("idempotencyKey"))


def _looks_like_session_rejection(exc: ProtocolError) -> bool:
    body = exc.body or ""
    try:
        parsed = json.loads(body)
        data = parsed.get("data") if isinstance(parsed, dict) else None
        if isinstance(data, dict):
            if data.get("reason") == "SESSION_NOT_FOUND":
                return True
            if data.get("action") == "reinitialize":
                return True
    except (ValueError, TypeError):
        pass

    return False


def normalize_base_url(base_url: str) -> str:
    parsed = urllib.parse.urlsplit(base_url.strip())
    path = parsed.path.rstrip("/")
    if path.endswith("/mcp"):
        path = path[:-4]
    return urllib.parse.urlunsplit(parsed._replace(path=path, query="", fragment=""))


def mcp_endpoint(base_url: str) -> str:
    parsed = urllib.parse.urlsplit(base_url.strip())
    raw_path = parsed.path.rstrip("/")
    if raw_path.endswith("/mcp"):
        path = raw_path
    else:
        path = f"{raw_path}/mcp" if raw_path else "/mcp"
    return urllib.parse.urlunsplit(parsed._replace(path=path, query="", fragment=""))


class MCPTransport:
    """Small, explicit Streamable HTTP JSON-RPC transport."""

    def __init__(
        self,
        base_url: str,
        timeout: float = 120.0,
        client_token: str | None = None,
        workspace: str | None = None,
    ):
        self.base_url = normalize_base_url(base_url)
        self.endpoint = mcp_endpoint(base_url)
        self._session_id = None
        self._protocol_version: str | None = None
        self._initialize_lock = asyncio.Lock()
        self.last_initialize_result: dict[str, Any] | None = None
        self._workspace = workspace
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "conduit-agent-client/2.0.0",
            "ngrok-skip-browser-warning": "1",
        }
        if client_token:
            headers["Authorization"] = f"Bearer {client_token}"
        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
        )

    @property
    def sessionId(self) -> str | None:
        return self._session_id

    async def terminate(self) -> None:
        if not self._session_id:
            return
        headers: dict[str, str] = {"mcp-session-id": self._session_id}
        if self._protocol_version:
            headers["MCP-Protocol-Version"] = self._protocol_version
        try:
            await self._client.delete(self.endpoint, headers=headers)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError):
            pass
        finally:
            self._session_id = None
            self._protocol_version = None

    async def close(self) -> None:
        await self._client.aclose()

    async def ensure_initialized(self) -> None:
        if self._session_id:
            return
        async with self._initialize_lock:
            if not self._session_id:
                await self._initialize_unlocked()

    async def initialize(self) -> dict[str, Any]:
        async with self._initialize_lock:
            return await self._initialize_unlocked()

    async def _initialize_unlocked(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "protocolVersion": SUPPORTED_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "conduit-agent-client", "version": "2.0.0"},
        }
        if self._workspace:
            params["workspace"] = self._workspace

        result = await self._rpc(
            "initialize",
            params,
            is_initialize=True,
        )
        negotiated = result.get("protocolVersion")
        if negotiated != SUPPORTED_PROTOCOL_VERSION:
            self._session_id = None
            raise ProtocolError(
                f"Unsupported negotiated MCP protocol version: {negotiated!r}; expected {SUPPORTED_PROTOCOL_VERSION}"
            )
        self._protocol_version = negotiated
        await self._send_initialized_notification()
        self.last_initialize_result = result
        return result

    async def _send_initialized_notification(self) -> None:
        if not self._session_id or not self._protocol_version:
            raise SessionError("Cannot send initialized notification before session negotiation")
        response = await self._client.post(
            self.endpoint,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={
                "mcp-session-id": self._session_id,
                "MCP-Protocol-Version": self._protocol_version,
            },
        )
        if response.status_code not in {200, 202, 204}:
            raise ProtocolError(
                f"Initialized notification failed with HTTP {response.status_code}",
                status_code=response.status_code,
                body=response.text,
            )

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        await self.ensure_initialized()
        return await self._rpc("tools/call", {"name": name, "arguments": arguments or {}})

    async def tools_list(self) -> dict[str, Any]:
        await self.ensure_initialized()
        return await self._rpc("tools/list")

    async def resources_list(self) -> dict[str, Any]:
        await self.ensure_initialized()
        return await self._rpc("resources/list")

    async def prompts_list(self) -> dict[str, Any]:
        await self.ensure_initialized()
        return await self._rpc("prompts/list")

    async def _rpc(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        is_initialize: bool = False,
        is_retry: bool = False,
    ) -> dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
            "params": params or {},
        }

        headers: dict[str, str] = {}
        if self._session_id and not is_initialize:
            headers["mcp-session-id"] = self._session_id
            if not self._protocol_version:
                raise SessionError("Session protocol version is unavailable; reinitialize")
            headers["MCP-Protocol-Version"] = self._protocol_version
        elif not self._session_id and not is_initialize:
            raise SessionError("Session not initialized. Call initialize() first.")

        response: httpx.Response | None = None
        retry_safe = _is_retry_safe(method, params)
        retry_delay = 1.0
        for attempt in range(3):
            try:
                response = await self._client.post(self.endpoint, json=payload, headers=headers)
                if response.status_code in {502, 503, 504} and attempt < 2 and retry_safe:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                break
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if not retry_safe:
                    raise TransportError(
                        "Conduit transport failed after a call that may have been applied; the client did not retry it without server-backed idempotency. Re-run only after checking the result, or supply an idempotency key.",
                        cause=exc,
                    )
                if attempt == 2:
                    raise TransportError(
                        "Conduit endpoint unreachable after 3 attempts — the tunnel may be offline. Check the server and CONDUIT_URL.",
                        cause=exc,
                    )
                await asyncio.sleep(retry_delay)
                retry_delay *= 2

        if response is None:
            raise TransportError("Failed to get a response from Conduit")

        try:
            result = self._decode_response(response)
            session_id = response.headers.get("mcp-session-id")
            if session_id:
                self._session_id = session_id
            return result
        except SessionError:
            if not is_initialize and not is_retry and _is_retry_safe(method, params):
                return await self._reinit_and_retry(method, params)
            raise
        except ProtocolError as exc:
            if (
                not is_initialize
                and not is_retry
                and self._session_id is not None
                and exc.status_code in RECOVERABLE_SESSION_STATUSES
                and _looks_like_session_rejection(exc)
            ):
                return await self._reinit_and_retry(method, params)
            raise

    async def _reinit_and_retry(self, method: str, params: dict[str, Any] | None) -> dict[str, Any]:
        self._session_id = None
        self._protocol_version = None
        try:
            await self.initialize()
        except (TransportError, ProtocolError, SessionError) as exc:
            raise TransportError(
                "The supplied session was rejected and re-initialization failed (the server may have restarted with a new route prefix). Start a new Conduit instance or update CONDUIT_URL.",
                cause=exc if isinstance(exc, Exception) else None,
            )
        return await self._rpc(method, params, is_retry=True)

    def _decode_response(self, response: httpx.Response) -> dict[str, Any]:
        content_type = response.headers.get("content-type", "")
        if content_type.startswith("text/event-stream"):
            return self._parse_sse(response.text)

        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            if response.status_code != 200:
                raise ProtocolError(f"HTTP Error {response.status_code}", status_code=response.status_code, body=response.text)
            raise ProtocolError(f"Failed to parse JSON response: {exc}", body=response.text)
        return self._handle_rpc_result(data, response.status_code)

    def _parse_sse(self, text: str) -> dict[str, Any]:
        last_data: dict[str, Any] | None = None
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                last_data = json.loads(payload)
            except json.JSONDecodeError:
                continue
        if last_data is None:
            raise ProtocolError("No valid data found in SSE stream", body=text)
        return self._handle_rpc_result(last_data)

    def _handle_rpc_result(self, data: dict[str, Any], status_code: int = 200) -> dict[str, Any]:
        if "error" in data:
            error = data["error"]
            code = error.get("code", -32603)
            message = error.get("message", "Internal error")
            err_data = error.get("data") if isinstance(error.get("data"), dict) else {}
            reason = err_data.get("reason")
            if code == -32001:
                raise AuthError.from_rpc(message, reason=reason, status_code=status_code, body=json.dumps(error))
            if code == -32000 and reason == "RATE_LIMITED":
                raise ProtocolError(f"Rate limited: {message}", status_code=status_code, body=json.dumps(error))
            if code == -32000:
                raise SessionError(message)
            raise ProtocolError(f"JSON-RPC Error {code}: {message}", status_code=status_code, body=json.dumps(error))
        return data.get("result", data)