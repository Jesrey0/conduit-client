
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch
from client.conduit.transport import MCPTransport, _is_retry_safe

def _resp(status, body, session_header=None):
    class Fake:
        def __init__(self):
            self.status_code = status
            self.headers = {"content-type": "application/json"}
            if session_header: self.headers["mcp-session-id"] = session_header
            self.text = body if isinstance(body, str) else json.dumps(body)
        def json(self): return json.loads(self.text)
    return Fake()

class RetryRuleTests(unittest.TestCase):
    def test_is_retry_safe(self):
        self.assertTrue(_is_retry_safe("initialize", None))
        self.assertTrue(_is_retry_safe("tools/call", {"name": "filesystem.read"}))
        self.assertFalse(_is_retry_safe("tools/call", {"name": "git.commit"}))

    def test_retries_on_503(self):
        t = MCPTransport("https://host")
        t._session_id = "s"
        t._protocol_version = "2025-11-25"
        with patch.object(t._client, "post", side_effect=[_resp(503, "ERR"), _resp(200, {"result": {"ok":True}})]):
            res = asyncio.run(t.call("filesystem.read"))
            self.assertTrue(res["ok"])
