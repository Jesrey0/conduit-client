
import asyncio
import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from client.conduit.transport import MCPTransport

class TerminateTests(unittest.TestCase):
    def test_terminate_clears_id(self):
        t = MCPTransport("https://host")
        t._session_id = "s1"
        t._protocol_version = "2025-11-25"
        resp = MagicMock()
        resp.status_code = 200
        with patch.object(t._client, "delete", return_value=resp):
            asyncio.run(t.terminate())
        self.assertIsNone(t.sessionId)
