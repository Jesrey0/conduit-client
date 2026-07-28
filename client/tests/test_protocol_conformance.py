import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from client.conduit.transport import MCPTransport


class FakeResponse:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self._body = body
        self.headers = {"content-type": "application/json", **(headers or {})}
        self.text = "" if body is None else json.dumps(body)

    def json(self):
        return self._body


class ProtocolConformanceTests(unittest.TestCase):
    def test_initialization_negotiates_2025_revision_and_sends_notification(self):
        async def scenario():
            transport = MCPTransport("https://host")
            initialize = FakeResponse(
                200,
                {"jsonrpc": "2.0", "id": "init", "result": {"protocolVersion": "2025-11-25", "capabilities": {}, "serverInfo": {"name": "test", "version": "1"}}},
                {"mcp-session-id": "session-1"},
            )
            initialized = FakeResponse(202)
            with patch.object(transport._client, "post", AsyncMock(side_effect=[initialize, initialized])) as post:
                result = await transport.initialize()
            self.assertEqual(result["protocolVersion"], "2025-11-25")
            self.assertEqual(transport.sessionId, "session-1")
            self.assertEqual(post.await_count, 2)
            init_call, notification_call = post.await_args_list
            self.assertEqual(init_call.kwargs["json"]["params"]["protocolVersion"], "2025-11-25")
            self.assertEqual(notification_call.kwargs["json"]["method"], "notifications/initialized")
            self.assertNotIn("id", notification_call.kwargs["json"])
            self.assertEqual(notification_call.kwargs["headers"]["mcp-session-id"], "session-1")
            self.assertEqual(notification_call.kwargs["headers"]["MCP-Protocol-Version"], "2025-11-25")
            await transport.close()

        asyncio.run(scenario())

    def test_subsequent_requests_send_negotiated_version_header(self):
        async def scenario():
            transport = MCPTransport("https://host")
            transport._session_id = "session-1"
            transport._protocol_version = "2025-11-25"
            response = FakeResponse(200, {"jsonrpc": "2.0", "id": "call", "result": {"ok": True}})
            with patch.object(transport._client, "post", AsyncMock(return_value=response)) as post:
                await transport.call("workspace.current")
            headers = post.await_args.kwargs["headers"]
            self.assertEqual(headers["mcp-session-id"], "session-1")
            self.assertEqual(headers["MCP-Protocol-Version"], "2025-11-25")
            await transport.close()

        asyncio.run(scenario())
